# -*- coding: utf-8 -*-
"""回答阶段的结构契约（防缩进类事故）。

背景：2026-10-12 一次脚本化改注解时，answer_messages 的赋值被多缩进一层、落进
`if alias_notes:` 块内 -> alias_notes 为空时回答阶段报 UnboundLocalError，
而 py_compile 查不出缩进错位。这里用 AST 做结构断言，把该类事故挡在提交前。
"""
import ast
from pathlib import Path

NODES = Path(__file__).resolve().parents[1] / "app" / "agent" / "nodes.py"

# 这些名字在 answer_agent 里被多处使用；其赋值必须位于函数体顶层，
# 否则会在某些分支下未绑定（UnboundLocalError）。
CRITICAL_BINDINGS = ("answer_messages", "content_parts", "stream_error")


def _walk_statements(body):
    """展开控制流语句体（if/for/while/try/with），用于判断赋值是否在"嵌套分支"里。"""
    for stmt in body:
        yield stmt, False
        for attr in ("body", "orelse", "finalbody"):
            inner = getattr(stmt, attr, None)
            if inner:
                for sub, _ in _walk_statements(inner):
                    yield sub, True
        for handler in getattr(stmt, "handlers", []) or []:
            for sub, _ in _walk_statements(handler.body):
                yield sub, True


def _assigned_names(body):
    top, nested = set(), set()
    for stmt, in_nested in _walk_statements(body):
        names = []
        if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
            names = [stmt.target.id]
        elif isinstance(stmt, ast.Assign):
            names = [x.id for x in stmt.targets if isinstance(x, ast.Name)]
        for n in names:
            (nested if in_nested else top).add(n)
    return top, nested


def test_critical_bindings_are_top_level():
    tree = ast.parse(NODES.read_text(encoding="utf-8"))
    fn = next((n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "answer_agent"), None)
    assert fn is not None, "未找到 answer_agent"
    top, nested = _assigned_names(fn.body)
    for name in CRITICAL_BINDINGS:
        assert name in top, f"{name} 未在 answer_agent 函数体顶层赋值（疑似被缩进进条件分支）"
        assert name not in nested or name in top, f"{name} 只在嵌套分支内赋值"


def test_answer_messages_assignment_not_guarded_by_alias_notes():
    """直接锁死本次事故形态：answer_messages 的赋值不得出现在 if alias_notes 块内。"""
    src = NODES.read_text(encoding="utf-8")
    lines = src.splitlines()
    for i, line in enumerate(lines):
        if "answer_messages: List[BaseMessage] = [" in line:
            indent = len(line) - len(line.lstrip())
            # 往上找最近的 if/for/try 行，检查缩进层级是否更浅
            for j in range(i - 1, max(-1, i - 6), -1):
                prev = lines[j]
                if not prev.strip():
                    continue
                prev_indent = len(prev) - len(prev.lstrip())
                if prev_indent < indent and prev.lstrip().startswith(("if ", "elif ", "for ", "while ")):
                    raise AssertionError(f"answer_messages 赋值疑似落在条件块内：L{j+1} {prev.strip()[:60]}")
                break
            return
    raise AssertionError("未找到 answer_messages 的赋值行")

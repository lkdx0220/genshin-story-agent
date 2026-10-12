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


# ====== 名单类结论的确定性直出 ======
_CAST_OUTPUT = """===== 实体查询「人前人后」（某任务有哪些出场角色，共 1 条命中）=====

【结论区｜强证据（出场人物表或正文台词），可作结论】
1. [强·出场人物表] 人前人后（活动剧情，正文提及 8 次，出场人物表 8 人）
   证据：出场人物表（8 人）：温迪、胡桃、菲尔戈黛特、行秋、重云、钟离、香菱、魈

覆盖度：本次在已收录的 1 个任务条目中比对…
"""


def _tool_msg(text):
    from langchain_core.messages import ToolMessage
    return ToolMessage(content=text, tool_call_id="t1")


def test_list_block_injected_when_answer_incomplete():
    from app.agent.nodes import _deterministic_list_block
    incomplete = "《人前人后》出场的有：胡桃、钟离、行秋、重云、魈、温迪、香菱和旅行者。"
    block = _deterministic_list_block([_tool_msg(_CAST_OUTPUT)], incomplete)
    assert "菲尔戈黛特" in block, "模型漏项时应直出完整名单"


def test_list_block_skipped_when_answer_complete():
    from app.agent.nodes import _deterministic_list_block
    complete = "温迪、胡桃、菲尔戈黛特、行秋、重云、钟离、香菱、魈均在《人前人后》出场。"
    assert _deterministic_list_block([_tool_msg(_CAST_OUTPUT)], complete) == ""


def test_list_block_ignores_non_list_tools():
    from app.agent.nodes import _deterministic_list_block
    other = "===== 混合检索「胡桃」=====\n1. 内容"
    assert _deterministic_list_block([_tool_msg(other)], "任意回答") == ""

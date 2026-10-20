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


def _module_functions(tree):
    """模块级函数名 -> 节点。"""
    return {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}


def _direct_callees(fn, module_funcs):
    """fn 直接调用的模块级函数名（只看 Call 的目标是 Name 的情况）。"""
    out = set()
    for n in ast.walk(fn):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in module_funcs:
            out.add(n.func.id)
    return out


def _is_pure_delegation(fn, module_funcs):
    """wrapper 判定：函数体顶层只允许 赋值/文档字符串表达式/return 调用模块级函数。

    出现 if/for/while/try/with 等控制流 -> 不是纯委托，需路径完整性另行证明。
    """
    for stmt in fn.body:
        if isinstance(stmt, ast.Return):
            v = stmt.value
            if not (isinstance(v, ast.Call) and isinstance(v.func, ast.Name) and v.func.id in module_funcs):
                return False
        elif isinstance(stmt, (ast.Assign, ast.AnnAssign)):
            continue
        elif isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant) and isinstance(stmt.value.value, str):
            continue  # 文档字符串
        else:
            return False
    return True


def test_critical_bindings_are_reachable_top_level():
    """结构契约（2026-10-20 修订 v2）：承载层顶层 + 所有 return 路径经过承载层。

    背景：R08 把 answer_agent 改成**编排层**（enabled 分支、gather、validate_answer、trace），
    实际承载 answer_messages 的是它调用的 _answer_agent_original（其函数体顶层）。
    因此：① 不再要求 answer_agent 自己赋值；② 改为在"可达链"里定位承载层，
    并要求该层**顶层**赋值（不得只在嵌套分支）；③ answer_agent 函数体内必须出现承载层调用（**弱化版**：不证明支配关系，已记为已知局限）。
    """
    tree = ast.parse(NODES.read_text(encoding="utf-8"))
    funcs = _module_functions(tree)
    fn = funcs.get("answer_agent")
    assert fn is not None, "未找到 answer_agent"

    callees = _direct_callees(fn, funcs)
    chain = [fn] + [funcs[n] for n in sorted(callees)]

    for name in CRITICAL_BINDINGS:
        carriers = []
        nested_only = []
        for f in chain:
            top, nested = _assigned_names(f.body)
            if name in top:
                carriers.append(f.name)
            elif name in nested:
                nested_only.append(f.name)
        assert carriers, (
            f"{name} 不在可达链任何一层的顶层：疑似被缩进进条件分支。"
            f"chain={[f.name for f in chain]} nested_only={nested_only}"
        )
        assert not (nested_only and not carriers), f"{name} 仅在嵌套分支内赋值：{nested_only}"

        # 弱化版路径检查（已知局限，见 docstring）：只要求 answer_agent 函数体内
        # 出现承载层调用，不证明"每条 return 都被它支配"。支配关系检查待后续版本。
        for carrier in carriers:
            if carrier == "answer_agent":
                continue
            called_anywhere = {
                c.func.id for c in ast.walk(fn)
                if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)
            }
            assert carrier in called_anywhere, (
                f"{name} 的承载函数 {carrier} 从未在 answer_agent 中被调用"
            )


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
    from app.agent.nodes import _list_block_from_text
    incomplete = "《人前人后》出场的有：胡桃、钟离、行秋、重云、魈、温迪、香菱和旅行者。"
    block = _list_block_from_text(_CAST_OUTPUT, incomplete)
    assert "菲尔戈黛特" in block, "模型漏项时应直出完整名单"


def test_list_block_skipped_when_answer_complete():
    from app.agent.nodes import _list_block_from_text
    complete = "温迪、胡桃、菲尔戈黛特、行秋、重云、钟离、香菱、魈均在《人前人后》出场。"
    assert _list_block_from_text(_CAST_OUTPUT, complete) == ""


def test_list_block_ignores_non_list_tools():
    from app.agent.nodes import _list_block_from_text
    other = "===== 混合检索「胡桃」=====\n1. 内容"
    assert _list_block_from_text(other, "任意回答") == ""


def test_list_tool_short_circuits_answer():
    """名单类问题必须走短路分支：输出带短路专属标记，且包含权威名单里的每一位。

    判别方式：不依赖"哪个 LLM 函数被调用"（模型选择在函数开头就会发生，patch 它区分不出短路），
    改用只有短路分支才会写出的来源标记；短路一旦失效，标记就不存在，测试立刻红。
    """
    from app.agent import nodes

    state = {"messages": [_tool_msg(_CAST_OUTPUT)], "execution_facts": _CAST_OUTPUT,
             "user_query": "《人前人后》里有哪些角色出场？"}
    out = nodes.answer_agent(state)
    text = out["final_response"]
    assert "结构化数据直接给出" in text, "未走短路（该标记只由短路分支产生）"
    assert "菲尔戈黛特" in text, "短路输出必须包含权威名单里的每一位"

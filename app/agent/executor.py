# -*- coding: utf-8 -*-
"""工具执行器：LangGraph 的 tools 节点实现。

负责执行 LLM 输出的 tool_calls，并实现熔断截断（防止规划阶段过度搜索）。
"""
import json

from langchain_core.messages import AIMessage, ToolMessage
from langgraph.prebuilt import ToolNode

from app.progress import _emit_progress, _cancel_events
from app.tools import tools, MELTDOWN_TRIGGER_TOOLS


# 预构建 ToolNode 实例（启动时一次）
_tool_node = ToolNode(tools)


def tool_executor(state):
    """执行工具调用并记录输入/输出日志。
    代码加固：熔断截断——同轮内已有 load_*/find_first_mention 成功返回后，
    后续非触发类工具调用被截断，强制 Plan Agent 进入回答阶段。"""
    # ---- 取消信号检查 ----
    run_id = state.get("run_id")
    cancel_event = _cancel_events.get(run_id) if run_id else None
    if cancel_event and cancel_event.is_set():
        print("  -> [取消] 工具执行前收到中断信号")
        # 设置 final_response，route_after_tools 会路由到 answer_agent
        return {"messages": [], "final_response": "[回答已中断] 当前任务已被用户终止。"}

    messages = state.get("messages", [])
    if not messages:
        return {}

    last_msg = messages[-1]
    if not isinstance(last_msg, AIMessage) or not hasattr(last_msg, 'tool_calls'):
        return {}

    tool_calls = last_msg.tool_calls
    tool_messages = []

    meltdown_triggered = False  # 本轮是否已有熔断触发工具成功返回

    for tc in tool_calls:
        tool_name = tc.get("name", "?")
        tool_args = tc.get("args", {})
        tc_id = tc.get("id", "")

        # 日志：输入（精简 args 中过长的值）
        args_brief = {}
        for k, v in tool_args.items():
            s = str(v)
            args_brief[k] = s[:100] + "..." if len(s) > 100 else s
        print(f"  [工具] {tool_name}({json.dumps(args_brief, ensure_ascii=False)})")

        # 向 Web 前端推送进度
        _emit_progress("tool_start", {"tool": tool_name, "args": args_brief})

        # ---- 代码加固 1：熔断截断 ----
        # 同轮内允许多个 load_/find_first_mention 并行执行（如对比分析需加载两个任务）
        # 只拦截非触发类工具（如 hybrid_search、query_character 等）
        if meltdown_triggered and tool_name not in MELTDOWN_TRIGGER_TOOLS:
            result_str = (
                f"[系统拦截] 全文/溯源熔断已触发：本轮已有 load_ 或 find_first_mention 成功返回内容，"
                f"当前工具 {tool_name} 被截断。请停止搜索，结束规划阶段，让回答阶段基于已加载的文本生成答案。"
            )
            print(f"    -> [熔断截断] {tool_name} 被拦截")
            tool_messages.append(ToolMessage(content=result_str, tool_call_id=tc_id))
            continue

        # 执行工具
        try:
            result = _tool_node.tools_by_name[tool_name].invoke(tool_args)
        except Exception as e:
            result = f"工具执行出错: {e}"
            print(f"    -> 错误: {e}")

        # 日志：结果摘要
        result_str = str(result)
        result_len = len(result_str)
        if result_len > 300:
            print(f"    -> 返回: {result_len}字 | {result_str[:300]}...")
        else:
            print(f"    -> 返回: {result_len}字 | {result_str}")

        # 检查是否触发熔断（成功返回内容，非"未找到"）
        if tool_name in MELTDOWN_TRIGGER_TOOLS:
            is_success = not any(kw in result_str for kw in ("未找到", "未收录", "不存在", "无匹配"))
            if is_success:
                meltdown_triggered = True
                print(f"    -> [熔断] {tool_name} 成功返回，本轮后续非加载类工具将被截断")

        tool_messages.append(ToolMessage(content=result_str, tool_call_id=tc_id))

        # 向 Web 前端推送工具完成
        _emit_progress("tool_end", {"tool": tool_name, "result_len": len(result_str)})

    return {"messages": tool_messages}

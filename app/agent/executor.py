# -*- coding: utf-8 -*-
"""工具执行器：LangGraph 的 tools 节点实现。

负责执行 LLM 输出的 tool_calls，并实现熔断截断（防止规划阶段过度搜索）。
"""
import json

from langchain_core.messages import AIMessage, ToolMessage
from langgraph.prebuilt import ToolNode

from app.progress import _emit_progress, _cancel_events
from app.tools import tools, MELTDOWN_TRIGGER_TOOLS
from app.trace_recorder import emit as trace_emit


# 预构建 ToolNode 实例（启动时一次）
_tool_node = ToolNode(tools)


def _tool_call_signature(tool_name: str, tool_args) -> str:
    """(工具名, 参数) 的稳定签名，用于识别重复调用；参数排序后序列化。"""
    try:
        args_json = json.dumps(tool_args, ensure_ascii=False, sort_keys=True, default=str)
    except Exception:
        args_json = str(tool_args)
    return f"{tool_name}|{args_json}"


def _previous_tool_results(messages) -> dict:
    """历史里已执行过的「签名 → 上次返回文本」。

    只认真正返回过内容的调用：系统拦截、执行出错、空串都不缓存——否则重复调用守卫会把
    拦截文案当结果回灌，让模型误以为工具真的执行过。
    """
    result_by_id = {}
    for msg in messages:
        if isinstance(msg, ToolMessage):
            result_by_id[getattr(msg, "tool_call_id", "") or ""] = str(msg.content or "")
    out = {}
    for msg in messages:
        if not isinstance(msg, AIMessage):
            continue
        for tc in (getattr(msg, "tool_calls", None) or []):
            sig = _tool_call_signature(tc.get("name", ""), tc.get("args", {}) or {})
            if sig in out:
                continue
            text = result_by_id.get(tc.get("id", "") or "", "")
            if not text or text.startswith("[系统") or text.startswith("工具执行出错"):
                continue
            out[sig] = text
    return out


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

    # ---- 代码加固 0：重复调用守卫 ----
    # 依据（2026-09-20 实测）：HP2「崩坏星穹铁道」把 hybrid_search 用完全相同参数连调两次；
    # F5「外号大娘」调查时同样观察到重复调用。重复执行既浪费迭代预算，又会让熔断计数虚高。
    prev_results = _previous_tool_results(messages[:-1])
    seen_signatures = set(prev_results.keys())

    for tc in tool_calls:
        tool_name = tc.get("name", "?")
        tool_args = tc.get("args", {})
        tc_id = tc.get("id", "")

        signature = _tool_call_signature(tool_name, tool_args)
        if signature in seen_signatures:
            cached = prev_results.get(signature, "")
            digest = f"上次返回摘要：{cached[:200]}" if cached else "上次返回见上文工具结果。"
            result_str = (
                f"[系统提示] 与上一次完全相同的调用（{tool_name}，参数一致）已执行过，本次不重复执行。"
                f"{digest} 请基于已有结果继续，不要重复调用同一工具与相同参数。"
            )
            print(f"    -> [重复调用] {tool_name} 同参数已调用过，跳过执行")
            tool_messages.append(ToolMessage(content=result_str, tool_call_id=tc_id))
            trace_emit("tool_end", {
                "tool": tool_name,
                "tool_call_id": tc_id,
                "run_id": run_id,
                "status": "duplicate_skipped",
                "result_preview": result_str[:500],
                "result_length": len(result_str),
                "meltdown_trigger": False,
            })
            _emit_progress("tool_end", {"tool": tool_name, "result_len": len(result_str)})
            continue
        seen_signatures.add(signature)

        # 日志：输入（精简 args 中过长的值）
        args_brief = {}
        for k, v in tool_args.items():
            s = str(v)
            args_brief[k] = s[:100] + "..." if len(s) > 100 else s
        print(f"  [工具] {tool_name}({json.dumps(args_brief, ensure_ascii=False)})")

        # 向 Web 前端推送进度
        _emit_progress("tool_start", {"tool": tool_name, "args": args_brief})

        # 结构化 Trace 事件（默认关闭）
        trace_emit("tool_start", {
            "tool": tool_name,
            "args": tool_args,
            "tool_call_id": tc_id,
            "run_id": run_id,
        })

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
            trace_emit("tool_end", {
                "tool": tool_name,
                "tool_call_id": tc_id,
                "run_id": run_id,
                "status": "intercepted",
                "result_preview": result_str[:500],
                "result_length": len(result_str),
                "meltdown_trigger": False,
            })
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
            is_success = not any(kw in result_str for kw in ("未找到", "未收录", "无匹配"))
            if is_success:
                meltdown_triggered = True
                print(f"    -> [熔断] {tool_name} 成功返回，本轮后续非加载类工具将被截断")

        tool_messages.append(ToolMessage(content=result_str, tool_call_id=tc_id))

        # 结构化 Trace 事件（默认关闭）
        stripped = result_str.lstrip()
        if stripped.startswith("工具执行出错"):
            trace_status = "error"
        elif any(stripped.startswith(kw) for kw in ("未找到", "未收录", "不存在", "无匹配", "No match", "not found")):
            trace_status = "not_found"
        else:
            trace_status = "success"
        trace_emit("tool_end", {
            "tool": tool_name,
            "tool_call_id": tc_id,
            "run_id": run_id,
            "status": trace_status,
            "result_preview": result_str[:500],
            "result_length": len(result_str),
            "meltdown_trigger": (
                tool_name in MELTDOWN_TRIGGER_TOOLS and trace_status == "success"
            ),
        })

        # 向 Web 前端推送工具完成
        _emit_progress("tool_end", {"tool": tool_name, "result_len": len(result_str)})

    return {"messages": tool_messages}

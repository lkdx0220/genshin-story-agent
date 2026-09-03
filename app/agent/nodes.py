# -*- coding: utf-8 -*-
"""LangGraph 节点函数：rewrite/assess/fast/plan/answer + 路由函数。

节点共享 GenshinAdvisorState，通过 state 字段传递数据。
路由函数返回字符串，由 StateGraph 的 add_conditional_edges 映射到下一节点。
"""
import re
import json
from typing import Dict, Any, List, Tuple

from langchain_core.messages import HumanMessage, AIMessage, SystemMessage, ToolMessage

from app.config import (
    MAX_AGENT_ITERATIONS, MAX_PLAN_RETRIES, MAX_FAST_ITERATIONS, RECENT_TURNS,
)
from app.llm import (
    llm, plan_llm, plan_llm_l2, assess_llm, llm_invoke_with_retry, _select_answer_llm,
)
from app.schema import (
    GenshinAdvisorState,
    AGENT_SYSTEM_PROMPT_PLAN, AGENT_SYSTEM_PROMPT_ANSWER, AGENT_SYSTEM_PROMPT_FAST,
)
from app.progress import _emit_progress, _cancel_events
from app.trace_recorder import emit as trace_emit
from app.tools import tools_by_name as _tools_by_name
from app.tools.query import find_similar_quest_names
from app.retrieval import _sanitize_query, _is_compound_hit, _judge_alias_sandbox
from character_aliases import ALIAS_MAP, ALIASES_SORTED
from intent_router import route_intent, get_tools_for_intent, get_pseudo_legendary_note


# ====== 多轮对话摘要 ======

def _summarize_conversation(existing_summary: str, turns: List[Dict[str, str]]) -> str:
    """用 LLM 将多轮对话压缩为一段摘要"""
    turns_text = ""
    for i, t in enumerate(turns, 1):
        turns_text += f"第{i}轮:\n  用户: {t['user']}\n  助手: {t['assistant'][:300]}\n\n"

    prompt = f"""将以下多轮对话压缩为一段简短摘要（100-200字），只保留用户关注的核心话题和关键信息点。

已有摘要：{existing_summary if existing_summary else '（无）'}

对话内容：
{turns_text}

===== 摘要规则（重要）=====
- 必须保留每轮提到的具体实体名称（角色名、任务名、书名、物品名）
- 跨轮指代（"她"→"小九九"、"那个"→"层岩巨渊"）必须写出解析后的名称
- 例如："用户问胡桃传说任务中的幽灵小九九她唱的歌原文是什么"
  而非："用户问了胡桃相关的内容，然后追问了角色歌曲"
- 如果长度受限，优先保留最新 2 轮的完整实体信息

请输出一段摘要文本，不要加前缀和解释。"""
    try:
        response = llm.invoke([HumanMessage(content=prompt)])
        return response.content.strip()
    except Exception:
        # 摘要失败就简单拼接话题关键词
        topics = set()
        for t in turns:
            topics.add(t['user'][:30])
        return existing_summary + "；" + "；".join(list(topics)[-3:])


# ====== 别名检测节点 ======

def rewrite_query(state: GenshinAdvisorState) -> Dict[str, Any]:
    user_query = state.get("user_query", "")
    print("\n" + "=" * 50)
    print("【别名检测】检测查询中的角色别名...")
    print("=" * 50)
    print(f"  原始: {user_query}")

    # Step 1: 安全层 —— 消毒，剥离指令性内容
    sanitized = _sanitize_query(user_query)
    if sanitized != user_query:
        print(f"  消毒: {sanitized}")

    # Step 2: 检测潜在别名命中，收集映射信息（不替换原文）
    alias_notes_parts = []  # 收集别名说明，最终拼接为系统消息补充
    alias_pairs = []        # 结构化别名映射 [(别名, 规范名)]，供下游确定性身份直答使用

    # ·/- 归一化：用户常用 "-" 代替 "·"（如 "芙宁娜-德-枫丹"），统一转为 "·" 后再匹配
    match_text = sanitized.replace('-', '·')

    for alias in ALIASES_SORTED:
        pos = match_text.find(alias)
        if pos < 0:
            continue

        if _is_compound_hit(sanitized, alias, pos):
            # 复合词命中，需要 AI 沙箱判断
            canonical = ALIAS_MAP[alias]
            ctx_start = max(0, pos - 8)
            ctx_end = min(len(sanitized), pos + len(alias) + 8)
            context = sanitized[ctx_start:ctx_end]

            if _judge_alias_sandbox(alias, canonical, context):
                print(f"  [AI判定] '{alias}' → '{canonical}' (上下文: \"{context}\")")
                alias_notes_parts.append(f'"{alias}" 指 {canonical}')
                alias_pairs.append((alias, canonical))
            else:
                print(f"  [AI判定] '{alias}' 在上下文中不是角色别名，保留原样 (上下文: \"{context}\")")
        else:
            # 独立词命中，直接记录映射
            canonical = ALIAS_MAP[alias]
            print(f"  [检测到] '{alias}' → '{canonical}'")
            alias_notes_parts.append(f'"{alias}" 指 {canonical}')
            alias_pairs.append((alias, canonical))

    if alias_notes_parts:
        # 代码加固：多实体强制注入 —— 如果检测到多个别名/实体，明确列出并强制要求全部回答
        multi_entity_note = ""
        if len(alias_notes_parts) >= 2:
            entity_list = "、".join(f"「{p}」" for p in alias_notes_parts)
            multi_entity_note = (
                f"\n[强制要求] 用户问题包含 {len(alias_notes_parts)} 个实体：{entity_list}。"
                f"你的回答必须覆盖以上全部 {len(alias_notes_parts)} 个实体，严禁只回答其中一部分。"
                f"如果某个实体的信息在工具返回中暂缺，也必须先说明已知部分，再对缺失部分说明\"当前知识库未收录\"。\n"
            )

        # 注意：alias_notes 的展示格式为 `"X" 指 Y`，是 alias_pairs 的文本化；
        # 下游 `_parse_alias_mappings` 也依赖此格式作为回退解析，修改时必须同步两者。
        alias_notes = f"""
[别名标注]
以下词汇在用户问题中被检测为角色别名，映射关系如下：
{chr(10).join(f"- {p}" for p in alias_notes_parts)}

这些映射用于帮助你理解用户意图和规范名。
- 纯身份查询（「XX是谁/指谁」）：代码会根据别名标注直接回答映射关系，不需要由你决定是否调工具。
- 行为/故事/属性/对比查询：必须调用工具检索剧情内容，不得仅凭别名标注回答。
- 行为提问必须从 load_quest_content 或 hybrid_search 提取具体动作，不得仅凭人物传记概括。
- 涉及别名但不是纯身份查询时（例如「岩王帝君的故事」），可调用 query_character(规范名) 获取更丰富信息。
{multi_entity_note}"""
        print(f"  -> 已标注 {len(alias_notes_parts)} 个别名映射，原文保持不变")
    else:
        alias_notes = ""
        print(f"  -> 未检测到别名")

    # rewritten_query 保持原样，不再做文本替换
    trace_emit("rewrite", {
        "user_query": user_query,
        "rewritten_query": sanitized,
        "alias_notes": alias_notes,
        "alias_pairs": alias_pairs or [],
        "alias_count": len(alias_notes_parts),
        "run_id": state.get("run_id"),
    })
    return {"rewritten_query": sanitized, "alias_notes": alias_notes, "alias_pairs": alias_pairs or None}


# ====== 查询分类器（L1 / L2 判断）======

ASSESS_PROMPT = """你是原神剧情助手的查询分类器。你的唯一任务是判断用户问题属于哪种类型。

用户问题：「{user_query}」

分类标准：
- L1（简单事实）：单一属性查询（XX的武器/地区/元素）、基本定义（XX是什么意思）。这类问题通常只需 0-1 次工具调用就能回答。
- 注意：身份查询（XX是谁）虽然看似简单，但需要知识库数据支撑，归为 L2。
- L2（复杂推理）：对比分析（XX和YY的区别）、多步推理（XX的成长经历/做了什么）、原因解释（为什么XX）、原话引用（XX说了什么）、溯源追踪（XX最早出现在哪里）、跨源拼装（列出所有提到XX的文案）。

核心原则：**犹豫就L2**。如果你不确定该分到哪类，输出L2。

只输出一个词：L1 或 L2。不要输出任何其他内容。"""


def assess_query(state: GenshinAdvisorState) -> Dict[str, Any]:
    """分类节点：判断用户问题走 L1（快速）还是 L2（完整）路径。"""
    user_query = state.get("user_query", "")
    print("\n" + "=" * 50)
    print("【路径分类】判断 L1（快速）或 L2（完整）...")
    print("=" * 50)

    prompt = ASSESS_PROMPT.replace("{user_query}", user_query)
    try:
        response = assess_llm.invoke([HumanMessage(content=prompt)])
        result = response.content.strip().upper()
    except Exception as e:
        print(f"  -> 分类器调用失败: {e}，默认走 L2")
        result = "L2"

    execution_mode = "L1" if "L1" in result else "L2"

    # L1/L2 硬规则：问题长度>30字 或 别名标注含≥2个实体 或 结构/多步清单类问题 → 强制 L2
    # 防止 assess_query 误判导致 L1 越权处理复杂问题
    alias_notes = state.get("alias_notes", "") or ""
    entity_count = alias_notes.count("指") if alias_notes else 0
    structure_pattern = re.compile(r'(几幕|子任务|包含哪些|有哪些子任务|章节结构|幕数|任务结构|完整剧情|讲了什么|清单|列举)')
    structure_hit = bool(structure_pattern.search(user_query))
    if len(user_query) > 30 or entity_count >= 2 or structure_hit:
        execution_mode = "L2"
        print(f"  -> 硬规则触发（长度={len(user_query)}、实体={entity_count}、结构类={structure_hit}），强制 L2")

    print(f"  -> 判定: {execution_mode}")

    trace_emit("assess", {
        "execution_mode": execution_mode,
        "user_query": user_query,
        "query_length": len(user_query),
        "entity_count": entity_count,
        "hard_rule": len(user_query) > 30 or entity_count >= 2,
        "run_id": state.get("run_id"),
    })
    return {"execution_mode": execution_mode}


def route_after_assess(state: GenshinAdvisorState) -> str:
    """分类后的路由：L1 → fast_agent，L2 → plan_agent"""
    mode = state.get("execution_mode", "L2")
    if mode == "L1":
        return "fast_agent"
    return "plan_agent"


# ====== 快速回答节点（L1 路径）======

def fast_agent(state: GenshinAdvisorState) -> Dict[str, Any]:
    """快速路径：合并 Plan + Answer，使用全部工具，最多 2 轮工具调用。"""
    iteration = state.get("fast_iteration", 0) or 0
    messages = list(state.get("messages", []))
    original_query = state.get("user_query", "")
    alias_notes = state.get("alias_notes", "")

    print("\n" + "=" * 50)
    print(f"【快速路径】第 {iteration + 1}/{MAX_FAST_ITERATIONS + 1} 轮")
    print("=" * 50)

    # ---- 取消信号检查 ----
    run_id = state.get("run_id")
    cancel_event = _cancel_events.get(run_id) if run_id else None
    if cancel_event and cancel_event.is_set():
        print("  -> [取消] 收到中断信号")
        return {
            "final_response": "[回答已中断] 当前任务已被用户终止。",
            "messages": [],
            "intent_labels": ["ALL"],
        }

    # 首轮：构建消息
    if not messages:
        # 使用全部工具
        all_tools = list(_tools_by_name.values())
        current_llm_with_tools = plan_llm.bind_tools(all_tools)

        system_content = AGENT_SYSTEM_PROMPT_FAST

        if alias_notes:
            system_content += alias_notes

        # 注入对话摘要
        conv_summary = state.get("conversation_summary", "")
        if conv_summary:
            system_content += f"\n\n## 之前的对话摘要\n{conv_summary}"

        messages = [SystemMessage(content=system_content)]

        # 注入最近 N 轮对话历史
        conv_history = state.get("conversation_history") or []
        for turn in conv_history[-RECENT_TURNS:]:
            messages.append(HumanMessage(content=turn["user"]))
            messages.append(AIMessage(content=turn["assistant"]))

        messages.append(HumanMessage(content=original_query))
    else:
        # 后续轮次：延用全部工具
        all_tools = list(_tools_by_name.values())
        current_llm_with_tools = plan_llm.bind_tools(all_tools)

    # ---- 代码短路：元数据工具确定性直答 ----
    # 如果本轮只调用了结构化元数据工具，直接复述工具结果，不经过回答 LLM。
    direct_answer = _get_direct_metadata_answer(messages)
    if direct_answer:
        print("  -> [代码短路] 元数据直答路径，跳过 LLM")
        return {
            "messages": messages,
            "final_response": direct_answer,
            "fast_iteration": iteration + 1,
            "intent_labels": ["ALL"],
        }

    # ---- 代码短路：快速路径全部工具均未找到 ----
    tool_messages = [m for m in messages if isinstance(m, ToolMessage)]
    if tool_messages and all(_is_not_found(m) for m in tool_messages):
        print("  -> [代码短路] 快速路径所有工具均未找到，直接返回'未收录'")
        return {
            "messages": messages,
            "final_response": "当前知识库未收录。",
            "fast_iteration": iteration + 1,
            "intent_labels": ["ALL"],
        }

    # 已达最大轮次，强制生成回答（不带工具）
    if iteration >= MAX_FAST_ITERATIONS:
        print(f"  -> 已达最大快速轮次，强制生成回答")
        # 剥离最后一条 AIMessage 的 tool_calls（工具未执行，防止 LLM 困惑）
        for i in range(len(messages) - 1, -1, -1):
            msg = messages[i]
            if isinstance(msg, AIMessage) and hasattr(msg, 'tool_calls') and msg.tool_calls:
                msg.tool_calls = []
                msg.additional_kwargs = {}
                break
        current_llm = llm  # 不带工具绑定的 llm
        try:
            response = current_llm.invoke(messages)
        except Exception as e:
            print(f"  -> LLM 调用失败: {e}")
            try:
                response = llm_invoke_with_retry(messages)
            except Exception:
                response = AIMessage(content=f"抱歉，处理出错：{e}")
        content = response.content if hasattr(response, 'content') else ''
        return {
            "messages": messages,
            "final_response": content,
            "fast_iteration": iteration + 1,
            "intent_labels": ["ALL"],
        }

    trace_emit("llm_start", {"role": "fast", "iteration": iteration + 1, "run_id": state.get("run_id"), "model": getattr(current_llm_with_tools, "model_name", None)})
    try:
        response = current_llm_with_tools.invoke(messages)
    except Exception as e:
        print(f"  -> LLM 调用失败: {e}")
        try:
            response = llm_invoke_with_retry(messages)
        except Exception:
            return {
                "final_response": f"抱歉，处理出错：{e}",
                "messages": messages,
                "intent_labels": ["ALL"],
            }
    trace_emit("llm_end", {"role": "fast", "iteration": iteration + 1, "run_id": state.get("run_id"), "tool_call_count": len(getattr(response, "tool_calls", None) or [])})

    # 有工具调用 → 继续循环
    if hasattr(response, 'tool_calls') and response.tool_calls:
        print(f"  -> 调用 {len(response.tool_calls)} 个工具: {[tc['name'] for tc in response.tool_calls]}")
        return {
            "messages": [response],
            "fast_iteration": iteration + 1,
        }

    # 无工具调用 → 直接返回回答
    content = response.content if hasattr(response, 'content') else ''
    # 空内容兜底：工具已返回"未找到"等结果，但 LLM 仍可能返回空串，此时明确告知未收录
    if not content or not content.strip():
        content = "当前知识库未收录。"
    print(f"  -> 快速路径完成（无工具调用），直接返回回答")
    return {
        "messages": [response],
        "final_response": content,
        "fast_iteration": iteration + 1,
        "intent_labels": ["ALL"],
    }


def route_after_fast(state: GenshinAdvisorState) -> str:
    """快速路径工具执行后路由：有 tool_calls 且未达上限 → tools，否则 → END"""
    messages = state.get("messages", [])
    iteration = state.get("fast_iteration", 0) or 0

    # 已产生最终回答
    if state.get("final_response"):
        return "end"

    if not messages:
        return "end"

    last_msg = messages[-1]

    if hasattr(last_msg, "tool_calls") and last_msg.tool_calls:
        if iteration >= MAX_FAST_ITERATIONS:
            print(f"  -> 快速路径达上限，强制生成回答")
            return "fast_agent"
        return "tools"

    return "end"


# ====== Plan 结构化工具调用提取 ======
# 设计说明：
# - 原生 tool_calls 仍是第一通道；如果模型只在正文写了“调用 XXX”，
#   而没生成原生 tool_calls，则解析正文中强制要求的【工具调用】JSON 块，
#   由代码构造真实的 tool_calls 并交给工具执行器。
# - 这样即使用户问的是偶发性的“正文写调用但 tool_calls 为空”，也不会被当作零工具处理。


def _extract_json_tool_calls(content: str) -> List[Dict[str, Any]]:
    """从 Plan 正文中提取【工具调用】JSON 块，返回原始 list（未校验）。"""
    if not content or not isinstance(content, str):
        return []

    candidates = []
    marker_pos = content.find('【工具调用】')
    if marker_pos >= 0:
        segment = content[marker_pos:]
        segment = re.sub(r'^```(?:json)?\s*', '', segment)
        candidates.append(segment)

    m = re.search(r'```json\s*(.*?)```', content, re.S)
    if m:
        candidates.append(m.group(1))

    for segment in candidates:
        start = segment.find('[')
        end = segment.rfind(']')
        if start < 0 or end <= start:
            continue
        raw = segment[start:end + 1]
        try:
            data = json.loads(raw)
        except Exception:
            continue
        if isinstance(data, list):
            return data
    return []


def _normalize_json_tool_calls(raw_calls: List[Any], allowed_tool_names: set) -> List[Dict[str, Any]]:
    """把 JSON 块中的工具调用规范成 LangChain tool_calls 格式。

    只接收：
    - {"tool": "hybrid_search", "args": {"query": "..."}}
    - {"name": "hybrid_search", "args": {"query": "..."}}
    工具名必须在本轮注入白名单内；args 必须是对象。
    """
    result: List[Dict[str, Any]] = []
    for i, item in enumerate(raw_calls):
        if not isinstance(item, dict):
            continue
        name = item.get("tool") or item.get("name")
        args = item.get("args")
        if not isinstance(name, str) or not isinstance(args, dict):
            continue
        if name not in allowed_tool_names:
            continue
        result.append({
            "name": name,
            "args": args,
            "id": f"call_json_{i + 1}",
            "type": "tool_call",
        })
    return result


def _parse_plan_json_tool_calls(content: str, allowed_tool_names: set) -> List[Dict[str, Any]]:
    """Plan 正文 → 规范化 LangChain tool_calls 列表（无有效块则返回空）。"""
    return _normalize_json_tool_calls(_extract_json_tool_calls(content), allowed_tool_names)


# ====== 统一 Agent 循环（L2 路径）======

def plan_agent(state: GenshinAdvisorState) -> Dict[str, Any]:
    """规划阶段：分析用户问题，输出【执行报告】和工具调用。不生成最终回答。"""
    iteration = state.get("iteration", 0) or 0
    messages = list(state.get("messages", []))
    original_query = state.get("user_query", "")
    alias_notes = state.get("alias_notes", "")
    intent_labels = state.get("intent_labels", [])

    # ---- 前置拦截：P16 超短/无意义输入 ----
    cleaned = re.sub(r'[^\u4e00-\u9fff\w]', '', original_query)
    if len(cleaned) < 3:
        print(f"  -> [前置拦截] 无效输入（清洗后长度={len(cleaned)}），直接返回引导语")
        return {"final_response": "请提出具体的原神剧情相关问题，我会尽力解答。", "messages": []}

    print("\n" + "=" * 50)
    print(f"【规划阶段】第 {iteration + 1}/{MAX_AGENT_ITERATIONS} 轮")
    print("=" * 50)

    # ---- 取消信号检查 ----
    run_id = state.get("run_id")
    cancel_event = _cancel_events.get(run_id) if run_id else None
    if cancel_event and cancel_event.is_set():
        print("  -> [取消] 收到中断信号")
        return {
            "final_response": "[回答已中断] 当前任务已被用户终止。",
            "messages": [],
        }

    # 首轮：路由 + 构建消息
    if not messages:
        # ---- 步骤1：意图路由 ----
        conv_summary = state.get("conversation_summary", "")
        turn_number = len(state.get("conversation_history") or [])
        if not intent_labels:
            intent_labels = route_intent(
                user_query=original_query,
                conversation_summary=conv_summary,
                turn_number=turn_number,
            )
            print(f"  [路由] 意图标签: {intent_labels}")

        # ---- 步骤2：根据意图动态绑定工具 ----
        routed_tools = get_tools_for_intent(intent_labels, _tools_by_name)
        tool_names = [t.name for t in routed_tools]
        print(f"  [路由] 注入工具({len(routed_tools)}个): {tool_names}")
        trace_emit("route", {
            "intent_labels": intent_labels,
            "injected_tools": tool_names,
            "turn_number": turn_number,
            "run_id": state.get("run_id"),
        })

        # ---- 步骤3：构建本轮 llm_with_tools ----
        current_llm_with_tools = plan_llm_l2.bind_tools(routed_tools)

        system_content = AGENT_SYSTEM_PROMPT_PLAN

        # 注入别名参考（如有）
        if alias_notes:
            system_content += alias_notes

        # 注入伪传说任务提示（如有）
        pseudo_note = get_pseudo_legendary_note(original_query)
        if pseudo_note:
            system_content += "\n\n" + pseudo_note

        # 注入对话摘要（旧对话的压缩）
        if conv_summary:
            system_content += f"\n\n## 之前的对话摘要\n{conv_summary}\n（以上是之前对话的摘要，供你理解上下文。请注意：如果用户当前问题与摘要中的话题相关，请结合上下文回答；如果不相关，请忽略。）"

        # 构建消息：SystemPrompt + 最近N轮历史 + 当前问题
        messages = [SystemMessage(content=system_content)]

        # 注入最近 N 轮对话历史
        conv_history = state.get("conversation_history") or []
        for turn in conv_history[-RECENT_TURNS:]:
            messages.append(HumanMessage(content=turn["user"]))
            messages.append(AIMessage(content=turn["assistant"]))

        # 当前问题：使用原始问题，不做替换
        messages.append(HumanMessage(content=original_query))
    else:
        # 后续轮次：延用已有的 intent 和工具集
        intent_labels = state.get("intent_labels", [])
        routed_tools = list(_tools_by_name.values())
        if intent_labels:
            routed_tools = get_tools_for_intent(intent_labels, _tools_by_name)
        current_llm_with_tools = plan_llm_l2.bind_tools(routed_tools)

    # 任务未命中后的确定性恢复：在让 LLM 自由选择下一步之前，先走 search_all → 相似名纠错。
    auto_recovery = _maybe_auto_task_recovery(state, messages, routed_tools, iteration)
    if auto_recovery is not None:
        return auto_recovery

    trace_emit("llm_start", {"role": "plan", "iteration": iteration + 1, "run_id": state.get("run_id"), "model": getattr(current_llm_with_tools, "model_name", None)})
    try:
        response = current_llm_with_tools.invoke(messages)
    except Exception as e:
        print(f"  -> LLM 调用失败: {e}")
        try:
            response = llm_invoke_with_retry(messages)
        except Exception:
            return {
                "execution_plan": f"【执行报告】\n用户问题回显：{original_query}\n用户意图：未知\n工具决策：LLM调用失败\n",
                "final_response": f"抱歉，处理出错：{e}",
                "messages": messages,
                "intent_labels": intent_labels,
            }
    trace_emit("llm_end", {"role": "plan", "iteration": iteration + 1, "status": "success", "run_id": state.get("run_id"), "tool_call_count": len(getattr(response, "tool_calls", None) or [])})

    # 保存执行计划（response.content 即模型输出的【执行报告】文本）
    plan_content = response.content if hasattr(response, 'content') else ''

    trace_emit("plan", {
        "iteration": iteration + 1,
        "execution_plan": plan_content[:500],
        "tool_call_names": [tc.get("name") for tc in (getattr(response, "tool_calls", None) or [])],
        "run_id": state.get("run_id"),
    })

    # 有工具调用 → 继续循环，不生成回答
    if hasattr(response, 'tool_calls') and response.tool_calls:
        print(f"  -> 调用 {len(response.tool_calls)} 个工具: {[tc['name'] for tc in response.tool_calls]}")
        if iteration + 1 >= MAX_AGENT_ITERATIONS:
            print(f"  -> 已达到最大迭代次数({MAX_AGENT_ITERATIONS})，本轮后进入回答阶段")
        return {
            "messages": [response],
            "execution_plan": plan_content,
            "iteration": iteration + 1,
            "intent_labels": intent_labels,
        }

    # 原生 tool_calls 缺失但正文含【工具调用】JSON → 代码构造真实 tool_calls
    # 这是 R7 类偶发问题的兜底：模型可能在正文写了"调用 hybrid_search"，
    # 却没有生成原生 tool_calls；只要它按提示词输出了机器可读 JSON 块，
    # 代码就把它转换成真正的工具调用，不再误判为零工具。
    allowed_tool_names = {t.name for t in routed_tools}
    json_tool_calls = _parse_plan_json_tool_calls(plan_content, allowed_tool_names)
    if json_tool_calls:
        print(f"  -> [JSON工具块] 从执行报告提取 {len(json_tool_calls)} 个工具: {[tc['name'] for tc in json_tool_calls]}")
        structured_response = AIMessage(content=plan_content, tool_calls=json_tool_calls)
        trace_emit("plan", {
            "iteration": iteration + 1,
            "execution_plan": plan_content[:500],
            "tool_call_names": [tc["name"] for tc in json_tool_calls],
            "tool_call_source": "json_block",
            "run_id": state.get("run_id"),
        })
        if iteration + 1 >= MAX_AGENT_ITERATIONS:
            print(f"  -> 已达到最大迭代次数({MAX_AGENT_ITERATIONS})，本轮后进入回答阶段")
        return {
            "messages": [structured_response],
            "execution_plan": plan_content,
            "iteration": iteration + 1,
            "intent_labels": intent_labels,
        }

    # 无工具调用 → 检查是否需要强制重试
    plan_retry_count = state.get("plan_retry", 0) or 0

    # 无工具进入回答阶段的原因：优先基于用户问题 + 结构化别名映射判定，
    # 不再解析 LLM 是否写出了"0轮工具/别名标注已给出答案"等固定措辞。
    alias_matches = _get_pure_alias_identity_matches(state)
    plan_exit_reason = None

    if alias_matches:
        plan_exit_reason = "alias_identity"
    else:
        stripped = original_query.strip()
        MATH_GREETING_PATTERN = re.compile(
            r'^[\d\s\+\-\*/\.=\(\)\？\?]+$'         # 纯数学表达式（无中文）
            r'|^(你好|您好|hi|hello|在吗|谢谢|多谢|再见|拜拜|早上好|晚上好|中午好)[\s！!。.]*$'  # 问候语
        )
        if MATH_GREETING_PATTERN.match(stripped):
            plan_exit_reason = "math_greeting"
            print("  -> P10 豁免：纯数学/问候语，跳过强制重试")
        # 含中文的数学问题（如"1+1等于几"），用更严格的模式：必须有数字-运算符-数字结构
        elif re.search(r'\d\s*[\+\-\*/]\s*\d', stripped) and len(stripped) <= 20:
            plan_exit_reason = "math_greeting"
            print("  -> P10 豁免：含中文数学表达式，跳过强制重试")
        else:
            # 前一轮已执行工具并返回结果 → Plan Agent 已看过结果，信任其"不需要再搜"的判断
            # 注意：用 hasattr/type 按实际类型检测，而非 isinstance(ToolMessage)，避免 import 依赖
            has_prior_tool_result = any(
                type(msg).__name__ == 'ToolMessage' for msg in messages
            )
            if has_prior_tool_result:
                plan_exit_reason = "prior_tool_result"

    is_exempt = plan_exit_reason in ("alias_identity", "math_greeting", "prior_tool_result")

    if is_exempt:
        if plan_exit_reason == "alias_identity":
            print(f"  -> 纯身份查询（别名豁免，{len(alias_matches)}个映射），无工具调用，进入回答阶段")
        else:
            print(f"  -> 无工具调用但符合豁免({plan_exit_reason})，进入回答阶段")
        trace_emit("plan_exit", {
            "iteration": iteration + 1,
            "plan_exit_reason": plan_exit_reason,
            "alias_count": len(alias_matches),
            "run_id": state.get("run_id"),
        })
        return {
            "messages": [response],
            "execution_plan": plan_content,
            "intent_labels": intent_labels,
            "plan_retry": plan_retry_count,
            "plan_exit_reason": plan_exit_reason,
        }

    if plan_retry_count < MAX_PLAN_RETRIES:
        print(f"  -> [拦截] 无工具调用且非身份查询，强制重试 ({plan_retry_count + 1}/{MAX_PLAN_RETRIES})")
        # 动态构建当前可用工具提示（避免硬编码与实际注入工具不匹配）
        tool_hints = []
        for _t in routed_tools:
            _name = _t.name if hasattr(_t, 'name') else _t.get('name', '?')
            _desc = _t.description if hasattr(_t, 'description') else _t.get('description', '')
            _short = _desc.split('\n')[0][:50] if _desc else ''
            tool_hints.append(f"{_name}（{_short}）" if _short else _name)
        _tool_str = "、".join(tool_hints)
        messages.append(response)
        messages.append(SystemMessage(content=(
            "错误：检测到你的规划中没有包含任何工具调用，也没有输出【工具调用】JSON 块。"
            "对于非身份查询类问题，你必须至少调用一个工具来检索信息。"
            f"请重新规划，并在【执行报告】后输出【工具调用】JSON 块，格式为："
            f'[{{"tool": "工具名", "args": {{"参数名": "参数值"}}}}]'
            f"。当前可调用的工具包括：{_tool_str}。"
            "你的职责是调用工具获取信息。如果确实搜不到，正常输出执行报告即可，后续阶段会处理未收录情况。"
        )))
        trace_emit("llm_start", {"role": "plan_retry", "iteration": iteration + 1, "run_id": state.get("run_id"), "model": getattr(current_llm_with_tools, "model_name", None)})
        try:
            response = current_llm_with_tools.invoke(messages)
        except Exception as e:
            print(f"  -> [拦截] LLM 重试调用失败: {e}")
            trace_emit("llm_end", {"role": "plan_retry", "status": "error", "run_id": state.get("run_id")})
        else:
            trace_emit("llm_end", {"role": "plan_retry", "status": "success", "run_id": state.get("run_id"), "tool_call_count": len(getattr(response, "tool_calls", None) or [])})
            plan_content = response.content if hasattr(response, 'content') else ''
            if hasattr(response, 'tool_calls') and response.tool_calls:
                print(f"  -> [拦截] 重试成功，调用 {len(response.tool_calls)} 个工具: {[tc['name'] for tc in response.tool_calls]}")
                if iteration + 1 >= MAX_AGENT_ITERATIONS:
                    print(f"  -> 已达到最大迭代次数({MAX_AGENT_ITERATIONS})，本轮后进入回答阶段")
                return {
                    "messages": [response],
                    "execution_plan": plan_content,
                    "iteration": iteration + 1,
                    "plan_retry": plan_retry_count + 1,
                    "intent_labels": intent_labels,
                }
            # 重试后仍无原生 tool_calls，但正文含【工具调用】JSON → 同样构造真实调用
            retry_json_calls = _parse_plan_json_tool_calls(plan_content, allowed_tool_names)
            if retry_json_calls:
                print(f"  -> [拦截] 重试 JSON工具块，调用 {len(retry_json_calls)} 个工具: {[tc['name'] for tc in retry_json_calls]}")
                structured_retry = AIMessage(content=plan_content, tool_calls=retry_json_calls)
                trace_emit("plan", {
                    "iteration": iteration + 1,
                    "execution_plan": plan_content[:500],
                    "tool_call_names": [tc["name"] for tc in retry_json_calls],
                    "tool_call_source": "json_block_retry",
                    "run_id": state.get("run_id"),
                })
                if iteration + 1 >= MAX_AGENT_ITERATIONS:
                    print(f"  -> 已达到最大迭代次数({MAX_AGENT_ITERATIONS})，本轮后进入回答阶段")
                return {
                    "messages": [structured_retry],
                    "execution_plan": plan_content,
                    "iteration": iteration + 1,
                    "plan_retry": plan_retry_count + 1,
                    "intent_labels": intent_labels,
                }
            print(f"  -> [拦截] 重试后仍无工具调用，放弃")
            plan_exit_reason = "retry_exhausted"
    else:
        print(f"  -> [拦截] 重试次数已耗尽，放弃工具调用")
        plan_exit_reason = "retry_exhausted"

    # 重试耗尽 → 进入回答阶段
    print("  -> 无工具调用，进入回答阶段")
    trace_emit("plan_exit", {
        "iteration": iteration + 1,
        "plan_exit_reason": plan_exit_reason,
        "alias_count": len(alias_matches),
        "run_id": state.get("run_id"),
    })
    return {
        "messages": [response],
        "execution_plan": plan_content,
        "plan_retry": plan_retry_count,
        "intent_labels": intent_labels,
        "plan_exit_reason": plan_exit_reason,
    }


def route_after_plan(state: GenshinAdvisorState) -> str:
    """规划节点后的路由：有 tool_calls → tools，无 tool_calls → answer_agent"""
    messages = state.get("messages", [])
    iteration = state.get("iteration", 0) or 0

    # 已产生最终回答（含中断消息），直接路由到 answer_agent
    if state.get("final_response"):
        return "answer_agent"

    if not messages:
        return "answer_agent"

    last_msg = messages[-1]

    # 有工具调用 → 执行工具（除非已达最大轮次）
    if hasattr(last_msg, "tool_calls") and last_msg.tool_calls:
        if iteration >= MAX_AGENT_ITERATIONS:
            print(f"  -> 已达上限，进入回答阶段")
            return "answer_agent"
        return "tools"

    # 无工具调用 → 进入回答阶段
    return "answer_agent"


def _is_not_found(tool_message) -> bool:
    """检查工具返回是否表示未找到/未匹配"""
    content = tool_message.content if hasattr(tool_message, 'content') else str(tool_message)
    return any(kw in content for kw in ("未找到", "未收录", "无匹配", "No match", "not found"))



# ====== 任务未命中后的确定性恢复链 ======
# 设计说明：
# - query_quest/load_quest_content 未命中时，不允许 LLM 自由跳到其他任务/角色；
#   代码层先强制 search_all 全局检索，全局也无结果时才进入疑似错别字纠正。
# - 非法跳转（如“影蝶之章”未命中后直接搜“丝柯克”）在代码层被阻断。

def _build_tool_name_by_call_id(messages):
    """从 AIMessage.tool_calls 反查 tool_call_id -> 工具名。"""
    mapping = {}
    for msg in messages:
        if isinstance(msg, AIMessage) and hasattr(msg, "tool_calls") and msg.tool_calls:
            for tc in msg.tool_calls:
                tid = tc.get("id")
                if tid:
                    mapping[tid] = tc.get("name", "")
    return mapping


def _tool_name_of(msg, name_by_call_id):
    direct = getattr(msg, "name", "") or ""
    if direct:
        return direct
    return name_by_call_id.get(getattr(msg, "tool_call_id", ""), "")


def _tool_call_arg_value(messages, tool_call_id, keys):
    """按 tool_call_id 反查该次调用的参数值。"""
    for msg in reversed(messages):
        if isinstance(msg, AIMessage) and hasattr(msg, "tool_calls"):
            for tc in msg.tool_calls:
                if tc.get("id") == tool_call_id:
                    args = tc.get("args", {}) or {}
                    for key in keys:
                        value = args.get(key)
                        if value:
                            return str(value)
    return ""


def _last_tool_message(messages):
    for msg in reversed(messages):
        if isinstance(msg, ToolMessage):
            return msg
    return None


def _attempted_task_names(messages):
    """已经用 query_quest/load_quest_content 尝试过的任务名集合。"""
    names = set()
    for msg in messages:
        if isinstance(msg, AIMessage) and hasattr(msg, "tool_calls"):
            for tc in msg.tool_calls:
                if tc.get("name") in ("query_quest", "load_quest_content"):
                    args = tc.get("args", {}) or {}
                    value = args.get("name") or args.get("quest_name")
                    if value:
                        names.add(str(value))
    return names


def _attempted_search_all_queries(messages):
    """已经用 search_all 全局检索过的查询词集合。"""
    queries = set()
    for msg in messages:
        if isinstance(msg, AIMessage) and hasattr(msg, "tool_calls"):
            for tc in msg.tool_calls:
                if tc.get("name") == "search_all":
                    args = tc.get("args", {}) or {}
                    value = args.get("query")
                    if value:
                        queries.add(str(value))
    return queries


def _maybe_auto_task_recovery(state, messages, routed_tools, iteration):
    """任务未命中后的确定性恢复：先全局检索，再无结果则相似名纠错。

    返回 None 表示不需要代码介入，按原流程继续交给 LLM。
    """
    if not messages:
        return None

    name_by_call_id = _build_tool_name_by_call_id(messages)
    latest = _last_tool_message(messages)
    if latest is None:
        return None

    latest_name = _tool_name_of(latest, name_by_call_id)
    latest_content = latest.content if hasattr(latest, "content") else str(latest)

    # 阶段1：任务查询未命中 → 强制先 search_all 全局检索
    if latest_name in ("query_quest", "load_quest_content") and _is_not_found(latest):
        failed_name = _tool_call_arg_value(
            messages, getattr(latest, "tool_call_id", ""),
            ("name", "quest_name", "query"),
        )
        if not failed_name:
            failed_name = state.get("user_query", "")
        if not failed_name:
            return None
        if failed_name in _attempted_search_all_queries(messages):
            return None
        content = (
            "【执行报告】\n"
            f"用户问题回显：{state.get('user_query', '')}\n"
            "用户意图：任务名全局检索\n"
            f"工具决策：任务查询对「{failed_name}」未命中，按硬规则先调用 search_all 全局检索，"
            "确认知识库中是否真的不存在，避免直接跳到其他任务/角色。\n"
            "【工具调用】\n"
            f'[{{"tool": "search_all", "args": {{"query": "{failed_name}"}}}}]'
        )
        tool_call = {
            "name": "search_all",
            "args": {"query": failed_name},
            "id": f"call_recovery_search_{iteration + 1}",
            "type": "tool_call",
        }
        trace_emit("plan", {
            "iteration": iteration + 1,
            "execution_plan": content[:500],
            "tool_call_names": ["search_all"],
            "tool_call_source": "auto_recovery_search",
            "run_id": state.get("run_id"),
        })
        return {
            "messages": [AIMessage(content=content, tool_calls=[tool_call])],
            "execution_plan": content,
            "iteration": iteration + 1,
            "intent_labels": state.get("intent_labels", []),
        }

    # 阶段2：search_all 也无结果 → 进入疑似错别字纠正（相似名候选，代码层执行）
    if latest_name == "search_all" and _is_not_found(latest):
        # 找到这次全局检索之前最近的一次任务未命中
        latest_index = None
        for i in range(len(messages) - 1, -1, -1):
            if messages[i] is latest:
                latest_index = i
                break
        if latest_index is None:
            return None
        prev_failure = None
        for i in range(latest_index - 1, -1, -1):
            msg = messages[i]
            if not isinstance(msg, ToolMessage):
                continue
            name = _tool_name_of(msg, name_by_call_id)
            if name in ("query_quest", "load_quest_content") and _is_not_found(msg):
                prev_failure = msg
                break
            # 只认最近的任务查询失败；若中间已有其他成功结果，说明 LLM 已转向其他路径，不强制纠正
            if _is_not_found(msg) or name in ("search_all",):
                continue
            break
        if prev_failure is not None:
            original_name = _tool_call_arg_value(
                messages, getattr(prev_failure, "tool_call_id", ""),
                ("name", "quest_name", "query"),
            )
        else:
            # LLM 可能先直接 search_all 而不是先 query_quest；
            # 此时以本次 search_all 的查询词作为原任务名，仍走同一套相似名纠正。
            original_name = _tool_call_arg_value(
                messages, getattr(latest, "tool_call_id", ""),
                ("query",),
            )
        if not original_name:
            return None
        attempted = _attempted_task_names(messages)
        candidates = find_similar_quest_names(original_name, top_n=1)
        if not candidates:
            return None
        corrected = candidates[0]
        if corrected == original_name or corrected in attempted:
            return None
        retry_tool = _tool_name_of(prev_failure, name_by_call_id) or "query_quest"
        if retry_tool not in ("query_quest", "load_quest_content"):
            retry_tool = "query_quest"
        if retry_tool == "load_quest_content":
            retry_args = {"quest_name": corrected}
        else:
            retry_args = {"name": corrected}
        content = (
            "【执行报告】\n"
            f"用户问题回显：{state.get('user_query', '')}\n"
            "用户意图：任务名疑似错别字纠正\n"
            f"工具决策：search_all 对「{original_name}」也无结果，按相似度识别疑似正确名称「{corrected}」，"
            "用同一任务查询工具重试一次。\n"
            "【工具调用】\n"
            f'[{{"tool": "{retry_tool}", "args": {json.dumps(retry_args, ensure_ascii=False)}}}]'
        )
        tool_call = {
            "name": retry_tool,
            "args": retry_args,
            "id": f"call_recovery_typo_{iteration + 1}",
            "type": "tool_call",
        }
        trace_emit("plan", {
            "iteration": iteration + 1,
            "execution_plan": content[:500],
            "tool_call_names": [retry_tool],
            "tool_call_source": "auto_recovery_typo",
            "run_id": state.get("run_id"),
        })
        return {
            "messages": [AIMessage(content=content, tool_calls=[tool_call])],
            "execution_plan": content,
            "iteration": iteration + 1,
            "intent_labels": state.get("intent_labels", []),
        }

    return None

# ====== 纯别名身份查询确定性识别 ======
# 设计说明：
# - 豁免判定固定解析"用户问题 + 结构化别名映射"，不再解析 LLM 写出的措辞。
# - 只放行"纯身份查询"（以是谁/指谁等身份短语结尾）。
# - 复合句（身份 + 追问，例如"岩王帝君是谁，是璃月的吗"）不属于纯身份查询，
#   不移交、不豁免，走正常工具流程；这是有意保守，不是正则遗漏。
_IDENTITY_TAIL_RE = re.compile(
    r"(?:是谁|指谁|是什么人|是哪位|是什么角色)[？?吗嘛呢啊呀。！!、，\s]*$"
)


def _parse_alias_mappings(alias_notes: str) -> List[Tuple[str, str]]:
    """从 alias_notes 文本解析 [(别名, 规范名)]，排除自映射。

    这是 alias_pairs 结构化字段的兼容回退路径；当前 alias_notes 由 rewrite_query
    生成，格式为 `"X" 指 Y`，修改该格式时必须同步本函数。
    """
    pairs: List[Tuple[str, str]] = []
    for line in (alias_notes or "").splitlines():
        m = re.search(r'"([^"]+)" 指 (.+)', line)
        if not m:
            continue
        alias = re.sub(r"[「」]", "", m.group(1)).strip()
        canonical = re.sub(r"[「」]", "", m.group(2)).strip()
        if alias and canonical and alias != canonical:
            pairs.append((alias, canonical))
    return pairs


def _get_alias_pairs(state: GenshinAdvisorState) -> List[Tuple[str, str]]:
    """优先使用 rewrite_query 产出的结构化 alias_pairs，旧数据回退到文本解析。"""
    pairs = state.get("alias_pairs") or []
    if pairs:
        return [(a, c) for a, c in pairs if a and c and a != c]
    return _parse_alias_mappings(state.get("alias_notes") or "")


def _get_pure_alias_identity_matches(state: GenshinAdvisorState) -> List[Tuple[str, str]]:
    """判断当前问题是否为纯别名身份查询，返回应回答的 (别名, 规范名) 列表。

    判定完全基于用户问题与别名映射，不依赖 Plan LLM 输出文字。
    只命中"XX是谁/指谁/是什么人/是哪位/是什么角色"类尾部问题。
    """
    text = (state.get("rewritten_query") or state.get("user_query") or "").strip()
    if not text or not _IDENTITY_TAIL_RE.search(text):
        return []

    hits = [
        (alias, canonical)
        for alias, canonical in _get_alias_pairs(state)
        if alias in text
    ]
    if not hits:
        return []

    # 去重：优先保留最长别名（"岩王帝君"与"帝君"同时命中时只保留前者）
    hits.sort(key=lambda x: -len(x[0]))
    kept: List[Tuple[str, str]] = []
    for alias, canonical in hits:
        if any(alias != k[0] and alias in k[0] for k in kept):
            continue
        kept.append((alias, canonical))
    if not kept:
        return []

    # 覆盖度护栏：纯身份直答要求所有“身份询问主体”都有别名映射覆盖。
    # 例如“六星火神和现任火神分别是谁”只映射了“六星火神”，“现任火神”未被覆盖，
    # 不能按纯别名身份直答，否则会漏答并跳过必要的检索。
    # 这是保守策略：宁可多走一次工具，也不允许直答只覆盖一半。
    story = re.sub(_IDENTITY_TAIL_RE, "", text)
    chunks = re.split(r"[和与、及跟同还有以及]|分别|都|同时|也|还", story)
    for chunk in chunks:
        chunk = chunk.strip(" 　「」『』“”‘’'\"，,。！!？?：:、")
        if not chunk:
            continue
        # 跳过纯连接词/身份填充残余（例如“分别是”“是谁”被切出的残留）
        if re.fullmatch(r"(?:是|分别|和|与|、|及|跟|同|还有|以及|都|同时|也|还|谁|哪一位|哪个人|什么角色|指).*", chunk):
            continue
        if not any(alias in chunk for alias, _ in kept):
            return []
    return kept


# ====== 元数据工具确定性直答 ======
# 这些工具返回的是结构化元数据，本身已足够回答用户问题。
# 如果本轮只调用了这些工具，就不再让回答 LLM 自由生成，直接从工具结果拼接答案，
# 避免 LLM 在元数据之外联想起不存在的书名/地区/角色/剧情。
DIRECT_ANSWER_TOOLS = {
    "get_book_metadata",
    "query_character",
    "query_region",
    "query_weapon",
    "query_artifact",
    "query_material",
    "query_collectible",
    "query_recipe",
    "query_food",
    "query_monster",
}


def _get_direct_metadata_answer(messages: list) -> str | None:
    """如果当前只发生了元数据类工具调用，直接返回其原始结果。

    返回 None 表示不适用（存在非元数据工具、或没有任何工具消息）。
    返回字符串时，调用方应跳过回答 LLM，直接将字符串作为 final_response。
    """
    tool_messages = [m for m in messages if isinstance(m, ToolMessage)]
    if not tool_messages:
        return None

    # 从最近的 AIMessage.tool_calls 中还原工具名（ToolMessage 可能没有 name 字段）
    name_by_call_id = {}
    for msg in messages:
        if isinstance(msg, AIMessage) and hasattr(msg, "tool_calls") and msg.tool_calls:
            for tc in msg.tool_calls:
                tid = tc.get("id")
                if tid:
                    name_by_call_id[tid] = tc.get("name", "")

    def _tool_name(m: ToolMessage) -> str:
        direct_name = getattr(m, "name", "") or ""
        if direct_name:
            return direct_name
        return name_by_call_id.get(getattr(m, "tool_call_id", ""), "")

    # 只要本轮出现过非元数据工具，就交给原有回答 LLM 处理
    if any(_tool_name(m) not in DIRECT_ANSWER_TOOLS for m in tool_messages):
        return None

    parts = []
    for m in tool_messages:
        content = m.content if hasattr(m, "content") else str(m)
        content = str(content).strip()
        if content:
            parts.append(content)
    if not parts:
        return None
    return "\n\n".join(parts)


def route_after_tools(state: GenshinAdvisorState) -> str:
    """工具执行后路由：L1 路径回 fast_agent，L2 路径回 plan_agent（含熔断逻辑）"""
    # L1 路径：工具执行后回 fast_agent
    execution_mode = state.get("execution_mode", "L2")
    if execution_mode == "L1":
        return "fast_agent"

    # L2 路径：原有的熔断和循环逻辑
    messages = state.get("messages", [])
    iteration = state.get("iteration", 0) or 0

    # 已产生最终回答（含中断消息），直接路由到 answer_agent
    if state.get("final_response"):
        return "answer_agent"

    # 从后往前，找到最近一轮 AIMessage（含 tool_calls）之后的所有 ToolMessage
    failures = 0
    for msg in reversed(messages):
        if isinstance(msg, AIMessage) and hasattr(msg, 'tool_calls') and msg.tool_calls:
            break
        if isinstance(msg, ToolMessage):
            if _is_not_found(msg):
                failures += 1
            else:
                failures = 0
                break
        if not isinstance(msg, ToolMessage) and not isinstance(msg, AIMessage):
            break

    if failures >= 2:
        print(f"  -> 连续失败熔断({failures}次)，进入回答阶段")
        # 修改最新 AIMessage 的 tool_calls 为空，阻止执行
        for msg in reversed(messages):
            if isinstance(msg, AIMessage) and hasattr(msg, 'tool_calls') and msg.tool_calls:
                msg.tool_calls = []
                msg.additional_kwargs = {}
                new_content = (msg.content or '') + (
                    '\n\n[系统提示] 连续 2 轮搜索均未找到结果，已触发连续失败熔断。'
                    '请基于已有信息直接回答用户，如无可用信息则告知"当前知识库未收录"。'
                )
                msg.content = new_content
                break
        return "answer_agent"

    return "plan_agent"


def _build_fallback_answer(messages: list, original_query: str) -> str:
    """LLM 返回空时的兜底：从工具返回结果中提取与查询相关的片段，拼凑回答。"""
    parts = []
    for msg in messages:
        if isinstance(msg, ToolMessage):
            content = msg.content if hasattr(msg, 'content') else str(msg)
            if content and len(content) > 20:
                # 截取前 2000 字符
                truncated = content[:2000]
                parts.append(truncated)
    if not parts:
        return f"关于「{original_query}」，当前知识库中未找到相关信息，请尝试更具体的查询或联系开发者补充数据。"
    # 简单拼接所有工具返回
    combined = "\n\n---\n\n".join(parts)
    return f"关于「{original_query}」，以下是知识库中检索到的相关内容：\n\n{combined}\n\n（注：以上为机器提取的原始数据，未经过 AI 整理。）"


def answer_agent(state: GenshinAdvisorState) -> Dict[str, Any]:
    """回答阶段：基于规划阶段的【执行报告】和工具返回结果，生成最终回答。不调用任何工具。"""
    messages = list(state.get("messages", []))
    execution_plan = state.get("execution_plan", "")
    original_query = state.get("user_query", "")
    alias_notes = state.get("alias_notes", "")
    intent_labels = state.get("intent_labels", [])
    answer_llm = _select_answer_llm(intent_labels)
    print(f"  [AnswerLLM] 意图={intent_labels} → {answer_llm.model_name}, thinking={answer_llm.model_kwargs.get('reasoning_effort', 'none')}, max_tokens={answer_llm.max_tokens}")

    # 前置拦截：plan_agent / tool_executor 已产生中断消息，跳过 LLM 调用
    existing_final = state.get("final_response")
    if existing_final and existing_final.startswith("[回答已中断]"):
        print("  -> [前置拦截] 中断消息，直接返回")
        # 清理 _cancel_events 中的事件（plan_agent 路径不会走 generate() 的清理）
        run_id = state.get("run_id")
        if run_id:
            _cancel_events.pop(run_id, None)
        return {"final_response": existing_final, "messages": messages}

    # 前置拦截：plan_agent 已产生最终回答（如 P16 超短输入），跳过 LLM 调用
    if existing_final and not messages:
        print("  -> [前置拦截] plan_agent 已产生最终回答，直接返回")
        return {"final_response": existing_final, "messages": messages}

    print("\n" + "=" * 50)
    print("【回答阶段】生成最终回答")
    print("=" * 50)
    _emit_progress("answer_start", {})
    trace_emit("answer_start", {
        "run_id": state.get("run_id"),
        "intent_labels": intent_labels,
    })

    # ---- 构建【执行事实】区块（注入到 prompt 中，防止 LLM 伪造熔断）----
    # 先从 AIMessage.tool_calls 还原工具名，因为自定义 tool_executor 创建 ToolMessage 时没有填 name
    name_by_call_id = {}
    for msg in messages:
        if isinstance(msg, AIMessage) and hasattr(msg, 'tool_calls') and msg.tool_calls:
            for tc in msg.tool_calls:
                tid = tc.get("id")
                if tid:
                    name_by_call_id[tid] = tc.get("name", "")

    tool_call_count = 0
    tool_results_summary = []
    full_text_loaded = False
    consecutive_failures = 0
    max_consecutive_failures = 0

    for i, msg in enumerate(messages):
        if isinstance(msg, AIMessage) and hasattr(msg, 'tool_calls') and msg.tool_calls:
            tool_call_count += len(msg.tool_calls)
        elif isinstance(msg, ToolMessage):
            content = msg.content if hasattr(msg, 'content') else str(msg)
            # 检查是成功还是失败
            is_failure = ("未找到" in content or "未收录" in content or
                         "无匹配" in content or "没有找到" in content
                         or content.strip().startswith("当前知识库未收录"))
            is_intercepted = "系统拦截" in content

            if is_failure:
                consecutive_failures += 1
                max_consecutive_failures = max(max_consecutive_failures, consecutive_failures)
            else:
                consecutive_failures = 0

            # 提取工具名（优先 ToolMessage.name，缺失时用 tool_call_id 反查）
            tool_name = (getattr(msg, "name", "") or "") or name_by_call_id.get(getattr(msg, "tool_call_id", ""), "")
            # 从最近的 AIMessage 中提取该 tool 的参数
            call_args = ""
            for prev_msg in reversed(messages[:i]):
                if isinstance(prev_msg, AIMessage) and hasattr(prev_msg, 'tool_calls') and prev_msg.tool_calls:
                    for tc in prev_msg.tool_calls:
                        if tc.get("name") == tool_name:
                            arg_vals = list(tc.get("args", {}).values())
                            call_args = f"({', '.join(str(v) for v in arg_vals if v)})"
                            break
                    break
            label = f"{tool_name}{call_args}"
            if is_intercepted:
                tool_results_summary.append(f"{label}: [被系统截断]")
            elif is_failure:
                tool_results_summary.append(f"{label}: 未找到")
            else:
                length = len(content)
                tool_results_summary.append(f"{label}: 成功返回({length}字)")
                # 对于成功返回的数据，提取一行内容预览
                content_preview = content.strip().split('\n')[0][:60]
                if content_preview:
                    tool_results_summary[-1] += f" | {content_preview}"
                if tool_name in ("load_quest_content", "load_book_content", "find_first_mention"):
                    full_text_loaded = True

    # 判断熔断状态
    if full_text_loaded:
        meltdown_status = "全文熔断"
    elif consecutive_failures >= 2:
        meltdown_status = f"失败熔断（连续{max_consecutive_failures}次未找到）"
    elif tool_call_count > 0:
        meltdown_status = "有搜索结果"
    else:
        meltdown_status = "无"

    # 判断 response_mode：失败熔断 或 工具调用次数=0 且无全文加载 → not_found
    # response_mode 是二元值，由代码层确定性注入，Answer 据此决定是否输出"未收录"
    if consecutive_failures >= 2 or (tool_call_count == 0 and not full_text_loaded):
        response_mode = "not_found"
    else:
        response_mode = "found"

    # ---- 代码短路：纯别名身份查询直答 ----
    # F2 类问题：零工具 + 问题为"XX是谁/指谁" + 别名标注给出映射时，直接回答映射关系。
    # 必须放在 not_found 短路之前，否则会被"零工具→未收录"误伤。
    if tool_call_count == 0 and not full_text_loaded:
        alias_matches = _get_pure_alias_identity_matches(state)
        if alias_matches:
            parts = [f"「{alias}」指「{canonical}」" for alias, canonical in alias_matches]
            alias_answer = "；".join(parts) + "。"
            print(f"  -> [代码短路] 纯别名身份直答：{alias_answer}")
            trace_emit("answer_end", {
                "response_mode": "found",
                "final_response": alias_answer,
                "short_circuit": True,
                "answer_source": "alias_direct",
                "run_id": state.get("run_id"),
            })
            return {"final_response": alias_answer, "messages": messages}


    # ---- 代码短路：元数据工具确定性直答 ----
    # 如果本轮只调用了结构化元数据工具，直接复述工具结果，不经过回答 LLM，
    # 防止模型在“作者未提及”之外联想稻妻/雷神/白狐等不存在的信息。
    direct_answer = _get_direct_metadata_answer(messages)
    if direct_answer:
        print("  -> [代码短路] 元数据直答路径，跳过 LLM")
        return {"final_response": direct_answer, "messages": messages}

    execution_facts = (
        f"===== 【执行事实】（铁证，不可篡改）=====\n"
        f"工具调用次数：{tool_call_count}\n"
        f"熔断状态：{meltdown_status}\n"
        f"response_mode：{response_mode}\n"
        f"工具返回摘要：{'; '.join(tool_results_summary) if tool_results_summary else '无'}\n"
        f"======================================\n\n"
        f"你的'熔断检查'必须如实填写以上信息。如果'工具调用次数'为0，不得写'已加载全文'或'连续N次未找到'。"
    )

    system_content = execution_facts + "\n" + AGENT_SYSTEM_PROMPT_ANSWER

    # not_found 代码短路：response_mode=not_found 时直接返回固定字符串，不调用 LLM
    # 这从根本上消除了 Answer LLM 忽略 not_found 标记、强行编造内容的可能性
    if response_mode == "not_found":
        print("  -> [代码短路] response_mode=not_found，跳过 LLM 调用，直接返回'未收录'")
        trace_emit("answer_end", {
            "response_mode": "not_found",
            "final_response": "当前知识库未收录。",
            "short_circuit": True,
            "run_id": state.get("run_id"),
        })
        return {"final_response": "当前知识库未收录。", "messages": messages}

    if alias_notes:
        system_content += alias_notes

    # 构建回答阶段的上下文：系统提示 + 执行报告 + 原始问题 + 所有工具返回
    answer_messages = [SystemMessage(content=system_content)]

    # 注入规划阶段的执行报告作为上下文
    if execution_plan:
        answer_messages.append(SystemMessage(content=(
            f"以下是规划阶段的分析结果，请基于此结果和工具返回的内容生成回答：\n\n{execution_plan}"
        )))

    # 注入用户原始问题
    answer_messages.append(HumanMessage(content=f"请基于上述规划结果和工具搜索内容，回答用户问题：{original_query}"))

    # 注入所有对话消息（工具调用和返回结果）
    for msg in messages:
        if isinstance(msg, (ToolMessage, AIMessage)):
            answer_messages.append(msg)

    trace_emit("llm_start", {"role": "answer", "run_id": state.get("run_id"), "model": getattr(answer_llm, "model_name", None)})
    try:
        response = llm_invoke_with_retry(answer_messages, llm_instance=answer_llm)
    except Exception as e:
        response = AIMessage(content=f"抱歉，处理出错：{e}")
    trace_emit("llm_end", {"role": "answer", "run_id": state.get("run_id"), "status": "success", "final_response_len": len(response.content or '')})

    content = response.content if hasattr(response, 'content') else ''

    # 兜底：如果 LLM 返回空内容（reasoning 耗尽 token 预算），根据工具返回结果拼凑回答
    if not content or not content.strip():
        content = _build_fallback_answer(messages, original_query)
        print("  -> [空回复兜底] LLM 返回空，使用兜底方案")

    trace_emit("answer_end", {
        "response_mode": response_mode,
        "final_response": content,
        "short_circuit": False,
        "run_id": state.get("run_id"),
    })
    return {
        "messages": answer_messages,
        "final_response": content,
    }

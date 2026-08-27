# -*- coding: utf-8 -*-
"""LangGraph 节点函数：rewrite/assess/fast/plan/answer + 路由函数。

节点共享 GenshinAdvisorState，通过 state 字段传递数据。
路由函数返回字符串，由 StateGraph 的 add_conditional_edges 映射到下一节点。
"""
import re
from typing import Dict, Any, List

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
from app.tools import tools_by_name as _tools_by_name
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
            else:
                print(f"  [AI判定] '{alias}' 在上下文中不是角色别名，保留原样 (上下文: \"{context}\")")
        else:
            # 独立词命中，直接记录映射
            canonical = ALIAS_MAP[alias]
            print(f"  [检测到] '{alias}' → '{canonical}'")
            alias_notes_parts.append(f'"{alias}" 指 {canonical}')

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

        alias_notes = ("\n\n[别名标注]\n以下词汇在用户问题中被检测为角色别名，映射关系如下：\n"
                       + "\n".join(f"- {p}" for p in alias_notes_parts)
                       + "\n\n这些映射仅用于帮助你理解用户意图和规范名。你仍然应当调用工具获取角色的详细信息。\n"
                       "- 如果用户在问「这个别名指谁/是谁」，可以在回答中引用别名标注，但仍应调用工具获取更丰富的信息。\n"
                       "- 如果用户在问角色的行为/故事，必须使用工具检索剧情内容。\n"
                       "- 行为提问必须从 load_quest_content 或 hybrid_search 提取具体动作，不得仅凭人物传记概括。\n"
                       + multi_entity_note)
        print(f"  -> 已标注 {len(alias_notes_parts)} 个别名映射，原文保持不变")
    else:
        alias_notes = ""
        print(f"  -> 未检测到别名")

    # rewritten_query 保持原样，不再做文本替换
    return {"rewritten_query": sanitized, "alias_notes": alias_notes}


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

    # L1/L2 硬规则：问题长度>30字 或 别名标注含≥2个实体 → 强制 L2
    # 防止 assess_query 误判导致 L1 越权处理复杂问题
    alias_notes = state.get("alias_notes", "") or ""
    entity_count = alias_notes.count("指") if alias_notes else 0
    if len(user_query) > 30 or entity_count >= 2:
        execution_mode = "L2"
        print(f"  -> 硬规则触发（长度={len(user_query)}或实体={entity_count}），强制 L2")

    print(f"  -> 判定: {execution_mode}")

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

    # 保存执行计划（response.content 即模型输出的【执行报告】文本）
    plan_content = response.content if hasattr(response, 'content') else ''

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

    # 无工具调用 → 检查是否需要强制重试
    plan_retry_count = state.get("plan_retry", 0) or 0

    # 豁免条件：身份查询 + 别名标注已给出（注意：自映射无信息量，不算）
    alias_notes = state.get("alias_notes", "") or ""
    has_useful_alias = False
    if alias_notes:
        for line in alias_notes.split("\n"):
            m = re.search(r'"([^"]+)" 指 (.+)', line)
            if m:
                alias_word = re.sub(r'[「」]', '', m.group(1)).strip()
                canonical = re.sub(r'[「」]', '', m.group(2)).strip()
                if alias_word != canonical:
                    has_useful_alias = True
                    break
    is_exempt = (
        "身份查询" in plan_content and
        ("0轮工具" in plan_content or "别名标注已给出答案" in plan_content)
    ) and has_useful_alias

    # P10：数学/问候白名单 —— 对纯数字运算、问候语跳过强制重试
    if not is_exempt:
        stripped = original_query.strip()
        MATH_GREETING_PATTERN = re.compile(
            r'^[\d\s\+\-\*/\.=\(\)\？\?]+$'         # 纯数学表达式（无中文）
            r'|^(你好|您好|hi|hello|在吗|谢谢|多谢|再见|拜拜|早上好|晚上好|中午好)[\s！!。.]*$'  # 问候语
        )
        if MATH_GREETING_PATTERN.match(stripped):
            is_exempt = True
            print("  -> P10 豁免：纯数学/问候语，跳过强制重试")
        # 含中文的数学问题（如"1+1等于几"），用更严格的模式：必须有数字-运算符-数字结构
        elif re.search(r'\d\s*[\+\-\*/]\s*\d', stripped) and len(stripped) <= 20:
            is_exempt = True
            print("  -> P10 豁免：含中文数学表达式，跳过强制重试")

    # 前一轮已执行工具并返回结果 → Plan Agent 已看过结果，信任其"不需要再搜"的判断
    # 注意：用 hasattr/type 按实际类型检测，而非 isinstance(ToolMessage)，避免 import 依赖
    has_prior_tool_result = any(
        type(msg).__name__ == 'ToolMessage' for msg in messages
    )
    if has_prior_tool_result:
        is_exempt = True

    if is_exempt:
        print("  -> 身份查询（别名豁免），无工具调用，进入回答阶段")
        return {
            "messages": [response],
            "execution_plan": plan_content,
            "intent_labels": intent_labels,
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
            "错误：检测到你的规划中没有包含任何工具调用。"
            "根据规则，对于非身份查询类问题，你必须至少调用一个工具来检索信息。"
            f"请重新规划。当前可调用的工具包括：{_tool_str}。"
            "你的职责是调用工具获取信息。如果确实搜不到，正常输出执行报告即可，后续阶段会处理未收录情况。"
        )))
        try:
            response = current_llm_with_tools.invoke(messages)
        except Exception as e:
            print(f"  -> [拦截] LLM 重试调用失败: {e}")
        else:
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
            print(f"  -> [拦截] 重试后仍无工具调用，放弃")
    else:
        print(f"  -> [拦截] 重试次数已耗尽，放弃工具调用")

    # 重试耗尽或豁免 → 进入回答阶段
    print("  -> 无工具调用，进入回答阶段")
    return {
        "messages": [response],
        "execution_plan": plan_content,
        "plan_retry": plan_retry_count,
        "intent_labels": intent_labels,
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

    try:
        response = llm_invoke_with_retry(answer_messages, llm_instance=answer_llm)
    except Exception as e:
        response = AIMessage(content=f"抱歉，处理出错：{e}")

    content = response.content if hasattr(response, 'content') else ''

    # 兜底：如果 LLM 返回空内容（reasoning 耗尽 token 预算），根据工具返回结果拼凑回答
    if not content or not content.strip():
        content = _build_fallback_answer(messages, original_query)
        print("  -> [空回复兜底] LLM 返回空，使用兜底方案")

    return {
        "messages": answer_messages,
        "final_response": content,
    }

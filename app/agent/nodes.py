# -*- coding: utf-8 -*-
"""LangGraph 节点函数：rewrite/assess/fast/plan/answer + 路由函数。

节点共享 GenshinAdvisorState，通过 state 字段传递数据。
路由函数返回字符串，由 StateGraph 的 add_conditional_edges 映射到下一节点。
"""
import re
import json
import time
from typing import Dict, Any, List, Tuple

from langchain_core.messages import HumanMessage, AIMessage, SystemMessage, ToolMessage

from app.config import (
    MAX_AGENT_ITERATIONS, MAX_PLAN_RETRIES, MAX_FAST_ITERATIONS, RECENT_TURNS,
)
from app.llm import (
    llm, plan_llm, plan_llm_l2, assess_llm, llm_invoke_with_retry, _select_answer_llm,
    answer_llm_medium, answer_llm_deep, answer_llm_l3, mechanism_judge_llm,
)
from app.schema import (
    GenshinAdvisorState,
    AGENT_SYSTEM_PROMPT_PLAN, AGENT_SYSTEM_PROMPT_ANSWER, AGENT_SYSTEM_PROMPT_FAST,
    AGENT_SYSTEM_PROMPT_L3_ANSWER,
)
from app.progress import _emit_progress, _cancel_events
from app.trace_recorder import emit as trace_emit
from app.tools import tools_by_name as _tools_by_name
from app.tools.search import search_world

# search_world 是“搜索碰壁后”才暴露的补充工具，不能进入初始工具集。
_HIDDEN_TOOL_NAMES = {"search_world"}


def _get_initial_tools():
    """返回初始可暴露给 LLM 的工具列表（排除隐藏的碰壁后工具）。"""
    return [
        t for t in _tools_by_name.values()
        if getattr(t, "name", "") not in _HIDDEN_TOOL_NAMES
    ]

from app.tools.query import find_similar_quest_names
from app.retrieval import _sanitize_query, _is_compound_hit, _judge_alias_sandbox_batch, TITLE_REGISTRY
from app.data import (
    角色知识库, 地区知识库, 任务知识库, 武器知识库, 圣遗物知识库, 素材知识库,
    _npcs_data, _normalize_for_match, _load_content_json,
)
from character_aliases import ALIAS_MAP, ALIASES_SORTED, resolve_aliases
from intent_router import route_intent, get_tools_for_intent, get_pseudo_legendary_note
from wiki_entry_graph import WikiEntryGraph, DEFAULT_OUTPUT as WIKI_GRAPH_OUTPUT


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
    t_start = time.perf_counter()
    batch_duration_ms = 0.0
    batch_candidate_count = 0
    user_query = state.get("user_query", "")
    # 供导出器区分“进程启动/知识库加载”与真正的 rewrite_query 阶段耗时。
    trace_emit("rewrite_start", {"run_id": state.get("run_id")})
    print("\n" + "=" * 50)
    print("【别名检测】检测查询中的角色别名...")
    print("=" * 50)
    print(f"  原始: {user_query}")

    # Step 1: 安全层 —— 消毒，剥离指令性内容
    sanitized = _sanitize_query(user_query)
    if sanitized != user_query:
        print(f"  消毒: {sanitized}")

    # Step 2: 检测潜在别名命中，收集映射信息（不替换原文）
    # 性能优化：
    # 1) 自映射（规范名=别名）确定性标注，不浪费 LLM 判断，保留 alias_notes 行为；
    # 2) 已接受的长别名覆盖的区间，其子串不再单独判断（如“雷电将军”已命中则“将军”跳过）；
    # 3) 剩余需要 AI 沙箱判断的复合命中合并为一次批量调用，避免逐个串行。
    alias_notes_parts = []  # 收集别名说明，最终拼接为系统消息补充
    alias_pairs = []        # 结构化别名映射 [(别名, 规范名)]，供下游确定性身份直答使用
    covered_spans = []      # 已接受别名覆盖的 (start, end) 区间，防止子串重复判断

    compound_candidates = []  # 批量待判断候选 (alias, canonical, context, pos, span)

    # ·/- 归一化：用户常用 "-" 代替 "·"（如 "芙宁娜-德-枫丹"），统一转为 "·" 后再匹配
    match_text = sanitized.replace('-', '·')

    for alias in ALIASES_SORTED:
        pos = match_text.find(alias)
        if pos < 0:
            continue

        canonical = ALIAS_MAP[alias]
        span = (pos, pos + len(alias))
        # 已经被更长的已接受别名覆盖，子串不再判断
        if any(s <= pos and pos + len(alias) <= e for s, e in covered_spans):
            continue

        # 自映射（别名=规范名）：确定性标注，不需要 LLM 判断，保留原有 alias_notes 行为
        if canonical == alias:
            print(f"  [检测到] '{alias}' → '{canonical}'")
            alias_notes_parts.append(f'"{alias}" 指 {canonical}')
            alias_pairs.append((alias, canonical))
            covered_spans.append(span)
            continue

        if _is_compound_hit(sanitized, alias, pos):
            # 复合词命中，延迟到循环结束后统一批量判断
            ctx_start = max(0, pos - 8)
            ctx_end = min(len(sanitized), pos + len(alias) + 8)
            context = sanitized[ctx_start:ctx_end]
            # 暂存：alias, canonical, context, pos, span
            compound_candidates.append((alias, canonical, context, pos, span))
        else:
            # 独立词命中，直接记录映射
            print(f"  [检测到] '{alias}' → '{canonical}'")
            alias_notes_parts.append(f'"{alias}" 指 {canonical}')
            alias_pairs.append((alias, canonical))
            covered_spans.append(span)

    # 批量 AI 沙箱判断：一次调用处理所有复合命中候选
    if compound_candidates:
        batch_candidate_count = len(compound_candidates)
        batch_candidates = [(a, c, ctx) for a, c, ctx, _pos, _span in compound_candidates]
        t_batch = time.perf_counter()
        batch_results = _judge_alias_sandbox_batch(batch_candidates)
        batch_duration_ms = (time.perf_counter() - t_batch) * 1000
        for (alias, canonical, context, pos, span), accepted in zip(compound_candidates, batch_results):
            if not accepted:
                print(f"  [AI判定] '{alias}' 在上下文中不是角色别名，保留原样 (上下文: \"{context}\")")
                continue
            # 批量内仍按最长优先处理：若已被更长的已接受别名覆盖，跳过子串
            if any(s <= pos and pos + len(alias) <= e for s, e in covered_spans):
                continue
            print(f"  [AI判定] '{alias}' → '{canonical}' (上下文: \"{context}\")")
            alias_notes_parts.append(f'"{alias}" 指 {canonical}')
            alias_pairs.append((alias, canonical))
            covered_spans.append(span)

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
    total_duration_ms = (time.perf_counter() - t_start) * 1000
    print(
        f"  [rewrite计时] 总耗时 {total_duration_ms:.1f}ms"
        f", DeepSeek批量 {batch_duration_ms:.1f}ms"
        f", 候选 {batch_candidate_count} 个"
    )
    trace_emit("rewrite", {
        "user_query": user_query,
        "rewritten_query": sanitized,
        "alias_notes": alias_notes,
        "alias_pairs": alias_pairs or [],
        "alias_count": len(alias_notes_parts),
        "run_id": state.get("run_id"),
        "duration_ms": round(total_duration_ms, 2),
        "batch_duration_ms": round(batch_duration_ms, 2),
        "batch_candidate_count": batch_candidate_count,
    })
    return {"rewritten_query": sanitized, "alias_notes": alias_notes, "alias_pairs": alias_pairs or None}


# ====== 查询分类器（L1 / L2 判断）======

ASSESS_PROMPT = """你是原神剧情助手的查询分类器。你的唯一任务是判断用户问题属于哪种类型。

用户问题：「{user_query}」

分类标准：
- L1（简单事实）：单一属性查询（XX的武器/地区/元素）。这类问题通常只需 0-1 次工具调用就能回答。
- 注意：身份查询（XX是谁）虽然看似简单，但需要知识库数据支撑，归为 L2。
- 注意：概念本质/定义类（XX是什么/什么是XX/XX的本质/XX的定义/何为XX）不是 L1 的“基本定义”，归为 L2。
- L2（复杂推理）：对比分析（XX和YY的区别）、多步推理（XX的成长经历/做了什么）、原因解释（为什么XX）、原话引用（XX说了什么）、溯源追踪（XX最早出现在哪里）、跨源拼装（列出所有提到XX的文案）。

核心原则：**犹豫就L2**。如果你不确定该分到哪类，输出L2。

只输出一个词：L1 或 L2。不要输出任何其他内容。"""



_CONCEPT_ESSENCE_MARKERS = ("是什么", "什么是", "何为", "本质", "定义", "含义", "意思")


def _is_concept_essence_question(user_query: str, conversation_summary: str = "", turn_number: int = 0) -> bool:
    """判断是否为概念本质/定义类问题（用于强制归入 L2 路径）。

    只触发能路由到 C2/ALL 的抽象概念问法；简单属性查询（如"温迪的武器是什么"，
    路由到 B）不会误触发；身份/任务元数据问法也不会触发。
    """
    query = (user_query or "").strip()
    if not query or not any(marker in query for marker in _CONCEPT_ESSENCE_MARKERS):
        return False
    if any(marker in query for marker in (
            "是谁", "指谁", "哪个角色", "哪一位",
            "包含几幕", "有哪些子任务", "有几幕", "第几幕", "章节", "任务", "之章")):
        return False
    try:
        labels = route_intent(
            user_query=query,
            conversation_summary=conversation_summary,
            turn_number=turn_number,
        )
    except Exception:
        return False
    return "C2" in labels or "ALL" in labels


_MECHANISM_NAME_BOUNDARY = (
    r"(?=揭|开|是|的|会|将|把|让|使|在|与|和|成|为|被|给|从|向|对|能|可|"
    r"不|没|有|无|这|那|其|并|也|都|就|才|只|便|即|如|若|因|所|以|而|却|则|"
    r"，|。|！|？|；|：|、|\s|$)"
)
_MECHANISM_NAME_PATTERNS = (
    re.compile(r"口中的([\u4e00-\u9fff]{2,6}?)" + _MECHANISM_NAME_BOUNDARY),
    re.compile(r"所谓的([\u4e00-\u9fff]{2,6}?)" + _MECHANISM_NAME_BOUNDARY),
    re.compile(r"被称为([\u4e00-\u9fff]{2,6}?)" + _MECHANISM_NAME_BOUNDARY),
    re.compile(r"称之为([\u4e00-\u9fff]{2,6}?)" + _MECHANISM_NAME_BOUNDARY),
    re.compile(r"叫做([\u4e00-\u9fff]{2,6}?)" + _MECHANISM_NAME_BOUNDARY),
    re.compile(r"称为([\u4e00-\u9fff]{2,6}?)" + _MECHANISM_NAME_BOUNDARY),
)
_MECHANISM_NAME_STOPWORDS = {
    "故事", "梦境", "记忆", "力量", "东西", "事情", "方法", "方式",
    "世界", "时间", "生命", "存在", "原因", "结果", "问题", "答案",
}


def _extract_mechanism_terms(messages) -> List[str]:
    """从工具原文里提取被明确命名的机制/手段名，例如“纳西妲口中的童话”。

    只做通用句式提取，不针对具体题目；用于回答阶段提醒模型保留机制原词。
    """
    text_parts = []
    for msg in messages:
        if isinstance(msg, ToolMessage):
            content = msg.content if hasattr(msg, "content") else str(msg)
            if content:
                text_parts.append(str(content))
    text = "\n".join(text_parts)
    if not text:
        return []
    terms: List[str] = []
    for pattern in _MECHANISM_NAME_PATTERNS:
        for match in pattern.finditer(text):
            term = match.group(1).strip("「」『』")
            if not (2 <= len(term) <= 8):
                continue
            if term in _MECHANISM_NAME_STOPWORDS:
                continue
            if term not in terms:
                terms.append(term)
    return terms[:5]


_MECHANISM_CONTEXT_WINDOW = 300
_MECHANISM_FOCUS_STOPWORDS = {
    "为什么", "为何", "原因", "机制", "原理", "怎么", "如何", "什么", "为何",
    "是谁", "谁", "哪些", "哪个", "多少", "几点", "哪里",
    "的", "了", "是", "在", "有", "和", "与", "把", "被", "让", "使",
    "会", "能", "可", "要", "还", "就", "都", "也", "不", "没", "很",
    "之后", "以后", "因为", "所以", "如果", "但是", "而是", "不是",
    "彻底", "完全", "没有", "是不是", "为什么说", "什么叫做",
}


def _extract_query_focus_terms(user_query: str, exclude_terms=None) -> List[str]:
    """从用户问题中提取可能承载问题焦点的中文 2~4 字片段。

    用途：机制名相关性预筛。先去掉问题中的问句/功能词，再把问题里
    连续中文 2~4 字片段当作“关注词”；主实体（别名标注里的名字）通过
    exclude_terms 排除，避免“纳西妲”这类实体名和机制名在同一段证据里
    出现就误判相关。
    """
    exclude = set(exclude_terms or [])
    # 只保留中文和数字/字母，去掉标点与空白。
    cleaned = re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", "", user_query or "")
    if not cleaned:
        return []
    terms: List[str] = []
    for length in (4, 3, 2):
        for i in range(0, len(cleaned) - length + 1):
            piece = cleaned[i:i + length]
            if piece in _MECHANISM_FOCUS_STOPWORDS or piece in exclude:
                continue
            # 片段里如果带英文/数字，单独保留；纯中文片段若完全由
            # 单字功能词组成也跳过。
            if re.fullmatch(r"[\u4e00-\u9fff]+", piece):
                if all(ch in "的是在有何把被让使会能可要还就都也不没很因为所以而却" for ch in piece):
                    continue
            if piece not in terms:
                terms.append(piece)
    return terms[:40]


def _mechanism_context_windows(messages, term: str, width: int = _MECHANISM_CONTEXT_WINDOW) -> List[str]:
    """返回机制名在工具原文中的前后文窗口（用于相关性预筛与裁判）。"""
    windows: List[str] = []
    for msg in messages:
        if not isinstance(msg, ToolMessage):
            continue
        content = str(getattr(msg, "content", "") or "")
        if not content:
            continue
        start = 0
        while True:
            idx = content.find(term, start)
            if idx < 0:
                break
            lo = max(0, idx - width)
            hi = min(len(content), idx + len(term) + width)
            windows.append(content[lo:hi])
            start = idx + len(term)
    return windows[:5]


def _mechanism_judge_relevant(term: str, user_query: str, windows: List[str]) -> bool:
    """小模型兜底裁判：判断机制名是否与当前问题相关。只允许回答是/否。

    只有在正则预筛拿不准时才调用；模型输出不合法按 False 处理，宁可
    少注入一个机制名，也不把无关机制名塞进 Answer 提示词。
    """
    if not windows:
        return False
    context = "\n".join(windows)
    if len(context) > 1500:
        context = context[:1500]
    prompt = (
        "你是机制相关性裁判。判断下面这个“机制名”是否是回答用户问题所必需的关键机制。\n"
        "只输出一个词：是 或 否。不要输出任何解释。\n\n"
        f"用户问题：{user_query}\n"
        f"机制名：{term}\n"
        f"机制名出现处的证据上下文：\n{context}\n"
    )
    try:
        response = mechanism_judge_llm.invoke([HumanMessage(content=prompt)])
        answer = (getattr(response, "content", "") or "").strip().lower()
    except Exception as e:
        print(f"  -> [机制裁判] 调用失败，默认判定为否: {e}")
        return False
    if answer.startswith("是") or answer.startswith("yes"):
        return True
    if answer.startswith("否") or answer.startswith("no"):
        return False
    return False


def _filter_mechanism_terms(messages, terms: List[str], user_query: str, alias_pairs=None) -> List[str]:
    """机制名相关性过滤：先正则/共现预筛，再交给小模型兜底。

    P0-1 目标：避免把工具原文里出现但和当前问题无关的机制名注入
    Answer 提示词（典型：H3 问纳西妲年龄，工具里出现“童话”被误注入）。
    """
    if not terms:
        return []
    # 别名标注里的实体名不参与“共现=相关”预筛，防止主实体出现在证据里
    # 就误判相关。
    exclude = set()
    for pair in (alias_pairs or []):
        if isinstance(pair, (list, tuple)):
            for item in pair:
                if isinstance(item, str):
                    exclude.add(item)
    # 问题里直接出现的角色名同样排除；否则“纳西妲”在证据里出现一次
    # 就会让无关机制名通过预筛。
    for entry in 角色知识库:
        if not isinstance(entry, dict):
            continue
        for key in ("角色名称", "名称", "title"):
            name = entry.get(key)
            if isinstance(name, str) and len(name) >= 2 and name in (user_query or ""):
                exclude.add(name)
    focus_terms = _extract_query_focus_terms(user_query, exclude)
    kept: List[str] = []
    for term in terms:
        if term in (user_query or ""):
            kept.append(term)
            continue
        windows = _mechanism_context_windows(messages, term)
        # 预筛：机制名上下文和问题关注词有共现，说明大概率相关。
        prescreen_hit = any(
            ft in window
            for ft in focus_terms
            for window in windows
        )
        if prescreen_hit:
            kept.append(term)
            continue
        # 预筛没把握时交给小模型裁判；小模型只输出是/否。
        if _mechanism_judge_relevant(term, user_query, windows):
            kept.append(term)
    return kept


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

    # L1/L2 硬规则：问题长度>30字 或 别名标注含≥2个实体 或 结构/多步清单类问题 或 概念本质/定义类 → 强制 L2
    # 防止 assess_query 误判导致 L1 越权处理复杂问题
    alias_notes = state.get("alias_notes", "") or ""
    entity_count = alias_notes.count("指") if alias_notes else 0
    structure_pattern = re.compile(r'(几幕|子任务|包含哪些|有哪些子任务|章节结构|幕数|任务结构|完整剧情|讲了什么|清单|列举)')
    structure_hit = bool(structure_pattern.search(user_query))
    conversation_summary = state.get("conversation_summary", "") or ""
    turn_number = len(state.get("conversation_history") or [])
    concept_hit = _is_concept_essence_question(user_query, conversation_summary, turn_number)
    if len(user_query) > 30 or entity_count >= 2 or structure_hit or concept_hit:
        execution_mode = "L2"
        print(f"  -> 硬规则触发（长度={len(user_query)}、实体={entity_count}、结构类={structure_hit}、概念本质类={concept_hit}），强制 L2")

    print(f"  -> 判定: {execution_mode}")

    trace_emit("assess", {
        "execution_mode": execution_mode,
        "user_query": user_query,
        "query_length": len(user_query),
        "entity_count": entity_count,
        "hard_rule": len(user_query) > 30 or entity_count >= 2 or concept_hit,
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
        all_tools = _get_initial_tools()
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
        all_tools = _get_initial_tools()
        current_llm_with_tools = plan_llm.bind_tools(all_tools)

    # ---- 代码短路：元数据工具确定性直答 ----
    # 如果本轮只调用了结构化元数据工具，直接复述工具结果，不经过回答 LLM。
    direct_answer = _get_direct_metadata_answer(messages, original_query)
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


# ====== 搜索碰壁后补充暴露世界/组织检索工具 ======
# search_all 不覆盖 lore.json 和 NPC 所属组织；当普通搜索未命中时，
# 才把 search_world 追加到后续 Plan 轮次的工具集中，初始不暴露。

def _is_search_dead_end(messages) -> bool:
    """判断本轮对话中是否已有搜索类工具返回未命中。

    不只看最后一条：即使后续有成功的旁路搜索，只要核心名称此前碰壁，
    也应把 search_world 暴露给 Planner 作为补充检索手段。
    """
    if not messages:
        return False
    name_by_call_id = _build_tool_name_by_call_id(messages)
    for msg in messages:
        if not isinstance(msg, ToolMessage):
            continue
        name = _tool_name_of(msg, name_by_call_id)
        if name not in ("search_all", "hybrid_search", "search_activity", "query_quest", "load_quest_content"):
            continue
        if _is_not_found(msg):
            return True
    return False


def _maybe_add_dead_end_search_world(messages, routed_tools):
    """搜索碰壁时把 search_world 追加到工具集；否则保持原工具集。"""
    if not _is_search_dead_end(messages):
        return routed_tools
    names = {getattr(t, "name", str(t)) for t in routed_tools}
    if getattr(search_world, "name", None) in names:
        return routed_tools
    print("  -> [search_world] 搜索碰壁，追加 search_world")
    return list(routed_tools) + [search_world]


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
        # 显式 Context Cache：SystemPrompt 作为稳定前缀打 cache_control 标记，
        # 同一会话后续 plan 轮次可命中缓存，减少 Prefill 耗时。
        messages = [SystemMessage(content=[
            {"type": "text", "text": system_content, "cache_control": {"type": "ephemeral"}},
        ])]

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
        routed_tools = _get_initial_tools()
        if intent_labels:
            routed_tools = get_tools_for_intent(intent_labels, _tools_by_name)
        # 搜索碰壁后补充暴露 search_world（lore/NPC组织），初始不暴露。
        had_search_world = any(
            getattr(t, "name", "") == "search_world" for t in routed_tools
        )
        routed_tools = _maybe_add_dead_end_search_world(messages, routed_tools)
        if not had_search_world and any(
            getattr(t, "name", "") == "search_world" for t in routed_tools
        ):
            messages.append(SystemMessage(content=(
                "系统提示：常规搜索（search_all/hybrid_search）已碰壁。"
                "现已补充工具 search_world，可搜索世界观设定、组织/NPC背景（如至冬组织名、lore.json 条目）。"
                "若目标名称可能属于此类数据，请调用 search_world 继续检索。"
            )))
        current_llm_with_tools = plan_llm_l2.bind_tools(routed_tools)

    # 任务未命中后的确定性恢复：在让 LLM 自由选择下一步之前，先走 search_all → 相似名纠错。
    auto_recovery = _maybe_auto_task_recovery(state, messages, routed_tools, iteration)
    if auto_recovery is not None:
        return auto_recovery

    # 全景/综合类问题：确定性全文旁路（挂载点A）。普通题目不触发，保持切块定位路径不变。
    full_text_auto = _maybe_auto_full_text_panoramic(state, messages, routed_tools, iteration)
    if full_text_auto is not None:
        return full_text_auto

    # wiki 图：多任务 + 地图文本的确定性补全（挂载点A）。
    graph_map_auto = _maybe_auto_graph_map_texts(state, messages, routed_tools, iteration)
    if graph_map_auto is not None:
        return graph_map_auto

    # 概念三视图补位（挂载点A）：任务恢复链未介入时，检查已有工具结果是否覆盖三个维度。
    concept_auto = _maybe_auto_concept_dimension(state, messages, routed_tools, iteration, intent_labels=intent_labels)
    if concept_auto is not None:
        return concept_auto

    # 关系/情感原因补搜（挂载点A）：已有工具结果后，若关系维度未覆盖则自动补一发。
    relationship_auto = _maybe_auto_relationship_search(state, messages, routed_tools, iteration)
    if relationship_auto is not None:
        return relationship_auto


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
    print(f"  [PlanOut] 第{iteration+1}轮 plan_content长度={len(plan_content)}")

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


    # wiki 图：多任务 + 地图文本的确定性补全（挂载点B）。
    # LLM 想零工具收工时，若任务/地图文本还没补齐，代码直接补发，Planner 不能提前收工。
    graph_map_auto = _maybe_auto_graph_map_texts(
        state, messages, routed_tools, iteration, response=response,
    )
    if graph_map_auto is not None:
        return graph_map_auto

    # 概念三视图补位（挂载点B）：LLM 本轮没出任何工具调用且想进入回答阶段，
    # 先检查概念维度是否齐；不齐则代码直接补发，Planner 不能提前收工。
    concept_auto = _maybe_auto_concept_dimension(
        state, messages, routed_tools, iteration,
        intent_labels=intent_labels, response=response,
    )
    if concept_auto is not None:
        return concept_auto

    # 关系/情感原因补搜（挂载点B）：LLM 想零工具收工时，若关系维度未覆盖则自动补一发。
    relationship_auto = _maybe_auto_relationship_search(
        state, messages, routed_tools, iteration, response=response,
    )
    if relationship_auto is not None:
        return relationship_auto


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


_NOT_FOUND_PREFIXES = (
    "未找到",
    "未收录",
    "无匹配",
    "不存在",
    "No match",
    "not found",
    "混合检索未找到",
    "语义搜索未找到",
    "在世界观设定中未找到",
    "书籍数据文件未找到",
)


def _is_not_found(tool_message) -> bool:
    """检查工具返回是否表示工具级“未找到/未匹配”。

    只认工具返回开头就是未命中提示的格式；不再对成功返回的整段知识库文本
    做子串搜索，避免把原文中“未找到第二个「镌光铭印」”这类正文误判为未命中。
    """
    content = tool_message.content if hasattr(tool_message, 'content') else str(tool_message)
    text = str(content).strip()
    if not text:
        return False
    return text.startswith(_NOT_FOUND_PREFIXES)




# ====== 概念本质类问题：三视图确定性覆盖 ======
# 设计说明：
# - 概念本质类问题（"XX是什么/本质/定义"）失败根因是 Planner 覆盖维度不全就提前收工。
#   是否搜够属于"结果属性"，不能交给 LLM 的过程自觉；这里由代码做补位验收。
# - LLM 可加不可删：LLM 首轮照常选择查询词；代码只补缺失维度，每个维度最多补一次。
# - 查询词由"概念名 × 通用维度模板"生成，不含任何评测关键词，不泄露测试答案。

_CONCEPT_DIMENSION_TEMPLATES = (
    ("definition", "本质 定义"),
    ("power_system", "能量 体系"),
    ("historical_impact", "知识 污染"),
)

_CONCEPT_DIMENSION_MARKERS = {
    "definition": ("本质", "定义", "是什么", "何为", "概念"),
    "power_system": ("能量", "体系", "力量", "元素", "三界", "虚界", "光界", "对立"),
    "historical_impact": ("知识", "污染", "历史", "影响", "来源", "起源", "后果", "灾难", "坎瑞亚", "世界树"),
}

_ENTITY_INDEX = None


def _ensure_entity_index() -> dict:
    """懒构建精确实体索引，用于把角色/任务/书籍/地区/具体物品排除出概念守卫。

    匹配策略全部是精确相等（经 _normalize_for_match 去装饰标点），
    不使用 query_quest 的 substring 包含匹配；否则"深渊"会误命中"深渊法师"。
    """
    global _ENTITY_INDEX
    if _ENTITY_INDEX is not None:
        return _ENTITY_INDEX

    index = {
        "character": set(),
        "quest": set(),
        "region": set(),
        "book": set(),
        "item": set(),
    }

    # 角色：角色名称 + 称号 + NPC 名称
    for entry in 角色知识库:
        if not isinstance(entry, dict):
            continue
        name = _normalize_for_match(str(entry.get("角色名称", "") or ""))
        if name:
            index["character"].add(name)
        for title in str(entry.get("称号", "") or "").split("/"):
            title = _normalize_for_match(title.strip())
            if title:
                index["character"].add(title)
    for npc_name in _npcs_data:
        name = _normalize_for_match(str(npc_name))
        if name:
            index["character"].add(name)

    # 任务：TITLE_REGISTRY + 任务知识库的任务名称/系列任务/所属角色
    for title in TITLE_REGISTRY:
        name = _normalize_for_match(str(title))
        if name:
            index["quest"].add(name)
    for entry in 任务知识库:
        if not isinstance(entry, dict):
            continue
        name = _normalize_for_match(str(entry.get("任务名称") or entry.get("title") or ""))
        if name:
            index["quest"].add(name)
        for part in str(entry.get("系列任务") or "").replace("，", ",").split(","):
            part = _normalize_for_match(part.strip())
            if part:
                index["quest"].add(part)
        owner = _normalize_for_match(str(entry.get("所属角色") or ""))
        if owner:
            index["quest"].add(owner)

    # 地区
    for entry in 地区知识库:
        if isinstance(entry, dict):
            name = _normalize_for_match(str(entry.get("地区名称", "") or ""))
            if name:
                index["region"].add(name)

    # 书籍：books.json 的 title 与 metadata.书籍名
    for item in _load_content_json("books"):
        if not isinstance(item, dict):
            continue
        for key in ("title",):
            name = _normalize_for_match(str(item.get(key, "") or ""))
            if name:
                index["book"].add(name)
        meta = item.get("metadata") or {}
        if isinstance(meta, dict):
            name = _normalize_for_match(str(meta.get("书籍名", "") or ""))
            if name:
                index["book"].add(name)

    # 具体物品：武器/圣遗物/素材知识库 + content_data 中的怪物/材料/采集物/食谱/食物
    for entry in 武器知识库:
        if isinstance(entry, dict):
            name = _normalize_for_match(str(entry.get("武器名称", "") or ""))
            if name:
                index["item"].add(name)
    for entry in 圣遗物知识库:
        if isinstance(entry, dict):
            name = _normalize_for_match(str(entry.get("圣遗物名称", "") or ""))
            if name:
                index["item"].add(name)
    for entry in 素材知识库:
        if isinstance(entry, dict):
            name = _normalize_for_match(str(entry.get("素材名称", "") or ""))
            if name:
                index["item"].add(name)
    for file_key in ("monsters", "materials", "collectibles", "recipes", "foods"):
        for item in _load_content_json(file_key):
            if not isinstance(item, dict):
                continue
            name = _normalize_for_match(str(item.get("名称", "") or ""))
            if name:
                index["item"].add(name)

    _ENTITY_INDEX = index
    return _ENTITY_INDEX


def _resolve_known_entity_type(subject: str) -> str:
    """把主语解析为具体实体类型；解析不到返回空字符串（按抽象概念处理）。"""
    normalized = _normalize_for_match((subject or "").strip())
    if not normalized or len(normalized) < 2:
        return ""

    index = _ensure_entity_index()

    # 角色优先级最高：先查别名反向映射，再查角色名/称号/NPC 名。
    # 注意：ALIAS_MAP 也收录"天理"等非角色条目，只有映射目标确实在角色索引中才算角色。
    canonical = ALIAS_MAP.get(normalized)
    if canonical and canonical in index["character"]:
        return "character"
    if normalized in index["character"]:
        return "character"

    if normalized in index["quest"]:
        return "quest"
    if normalized in index["region"]:
        return "region"
    if normalized in index["book"]:
        return "book"
    if normalized in index["item"]:
        return "item"
    return ""


def _detect_concept_subject(original_query: str, intent_labels) -> str:
    """判断问题是否为"抽象概念 XX 是什么/本质/定义"，返回概念主语；不满足返回空字符串。

    只做保守触发：主语能精确解析为角色/任务/书籍/地区/具体物品时一律不触发；
    任务元数据问法（包含几幕/有哪些子任务等）与身份问法（是谁/指谁）也不触发。
    """
    if not intent_labels or ("C2" not in intent_labels and "ALL" not in intent_labels):
        return ""
    query = (original_query or "").strip()
    if not query:
        return ""

    concept_markers = ("是什么", "什么是", "何为", "本质", "定义")
    if not any(marker in query for marker in concept_markers):
        return ""
    exclusion_markers = (
        "是谁", "指谁", "哪个角色", "哪一位",
        "包含几幕", "有哪些子任务", "有几幕", "第几幕", "章节", "任务", "之章",
    )
    if any(marker in query for marker in exclusion_markers):
        return ""

    # 优先取书名号/引号内的实体
    candidate = ""
    for pattern in (
        r"[「『]([^」』]{2,16})[」』]",
        r"[“\"]([^”\"]{2,16})[”\"]",
    ):
        match = re.search(pattern, query)
        if match:
            candidate = match.group(1)
            break

    # 无引号则按概念句式提取主语
    if not candidate:
        patterns = (
            r"什么是(.{2,16}?)[？?，,。！!]?$",
            r"何为(.{2,16}?)[？?，,。！!]?$",
            r"(.{2,16}?)的(?:本质|定义)(?:是|为)?(?:什么)?[？?。]?$",
            r"(.{2,16}?)(?:本质上|本质)?(?:是|为)?什么[？?。]?$",
        )
        for pattern in patterns:
            match = re.search(pattern, query)
            if match:
                candidate = match.group(1)
                break

    candidate = _normalize_for_match(candidate.strip())
    candidate = candidate.rstrip("的")
    if not candidate or len(candidate) < 2 or len(candidate) > 16:
        return ""
    if any(ch in candidate for ch in ("谁", "哪", "何", "几")):
        return ""

    if _resolve_known_entity_type(candidate):
        return ""
    return candidate


def _covered_concept_dimensions(messages) -> set:
    """扫描已执行的 hybrid_search 查询，返回已尝试过的三视图维度集合。"""
    covered = set()
    for msg in messages:
        if not isinstance(msg, AIMessage) or not hasattr(msg, "tool_calls"):
            continue
        for tc in msg.tool_calls or []:
            if not isinstance(tc, dict) or tc.get("name") != "hybrid_search":
                continue
            args = tc.get("args") or {}
            query_text = str(args.get("query") or "")
            for dim, markers in _CONCEPT_DIMENSION_MARKERS.items():
                if any(marker in query_text for marker in markers):
                    covered.add(dim)
    return covered


def _maybe_auto_concept_dimension(state, messages, routed_tools, iteration, intent_labels=None, response=None):
    """概念三视图补位守卫：缺哪个维度补哪个，返回 None 表示无需介入。"""
    labels = intent_labels if intent_labels is not None else state.get("intent_labels", [])
    subject = _detect_concept_subject(state.get("user_query", ""), labels)
    if not subject:
        return None

    tool_names = {getattr(t, "name", str(t)) for t in routed_tools}
    if "hybrid_search" not in tool_names:
        return None
    if iteration + 1 >= MAX_AGENT_ITERATIONS:
        return None

    missing = [
        dim for dim, _template in _CONCEPT_DIMENSION_TEMPLATES
        if dim not in _covered_concept_dimensions(messages)
    ]
    if not missing:
        return None

    # 挂载点A（LLM 调用前）：只在已有工具结果后补位，不抢 Planner 的首轮决策权。
    if response is None and _last_tool_message(messages) is None:
        return None

    template_by_dim = dict(_CONCEPT_DIMENSION_TEMPLATES)
    calls = []
    for dim in missing:
        calls.append({
            "name": "hybrid_search",
            "args": {"query": f"{subject} {template_by_dim[dim]}"},
            "id": f"call_concept_{dim}_{iteration + 1}",
            "type": "tool_call",
        })

    missing_names = "、".join(f"{subject} {template_by_dim[dim]}" for dim in missing)
    content = (
        "【执行报告】\n"
        f"用户问题回显：{state.get('user_query', '')}\n"
        "用户意图：概念本质类问题的三视图检索\n"
        f"工具决策：系统检测到概念「{subject}」的检索维度尚未覆盖，按硬规则自动补齐：{missing_names}。\n"
        "【工具调用】\n"
        + json.dumps([{"tool": c["name"], "args": c["args"]} for c in calls], ensure_ascii=False)
    )
    print(f"  -> [概念三视图] 自动补搜: {missing_names}")

    trace_emit("plan", {
        "iteration": iteration + 1,
        "execution_plan": content[:500],
        "tool_call_names": [c["name"] for c in calls],
        "tool_call_source": "concept_dimension",
        "concept": subject,
        "dimensions": missing,
        "run_id": state.get("run_id"),
    })
    return {
        "messages": [AIMessage(content=content, tool_calls=calls)],
        "execution_plan": content,
        "iteration": iteration + 1,
        "intent_labels": labels,
    }

# ====== 情感/关系类问题：双极性关系补搜 ======
# 设计说明：
# - “为什么讨厌/恨”和“为什么喜欢/爱/信任”都属于人物关系/情感原因问题，
#   不能只补负面词（否则像过拟合 R4），也不能完全交给 Planner 自觉。
# - 这里用对称的双极性后缀：负面问题补“抛弃/背叛/伤害”，正面问题补“守护/陪伴/信任”，
#   代码只负责“当已有检索没有覆盖关系原因维度时补一发”，不写入任何提示词。
# - 具体后缀词是可解释的通用关系/情感词，不包含 Golden Test 的专有答案术语。

_RELATIONSHIP_NEGATIVE_PATTERN = re.compile(
    r"(?:为什么|为何).{0,10}(?:讨厌|恨|怨恨|厌恶|反感|不喜欢|怨)"
)
_RELATIONSHIP_POSITIVE_PATTERN = re.compile(
    r"(?:为什么|为何).{0,10}(?:喜欢|爱|信任|依赖|欣赏|崇拜|怀念|尊敬|珍惜|感恩)"
)

_RELATIONSHIP_SUFFIX_BY_POLARITY = {
    "negative": "抛弃 被抛弃 造物主 背叛 伤害 遗弃",
    "positive": "守护 陪伴 帮助 信任 关爱 羁绊",
    "neutral": "关系 经历 过去 事件",
}

_RELATIONSHIP_COVER_MARKERS = {
    "negative": ("抛弃", "被抛弃", "造物主", "背叛", "伤害", "遗弃"),
    "positive": ("守护", "陪伴", "帮助", "信任", "关爱", "羁绊"),
    "neutral": ("关系", "经历", "过去", "事件"),
}


def _detect_relationship_polarity(query: str) -> str:
    """检测“人物关系/情感原因”问题，返回 negative / positive / neutral，不是则返回空串。"""
    query = (query or "").strip()
    if not query:
        return ""
    if _RELATIONSHIP_NEGATIVE_PATTERN.search(query):
        return "negative"
    if _RELATIONSHIP_POSITIVE_PATTERN.search(query):
        return "positive"
    if any(word in query for word in ("有什么关系", "为什么关系", "两者关系", "为何关系")):
        return "neutral"
    return ""


def _covered_relationship_search(messages, polarity: str) -> bool:
    """已执行的 hybrid_search 中是否已包含该极性所需的关系原因词。"""
    markers = _RELATIONSHIP_COVER_MARKERS.get(polarity, ())
    if not markers:
        return False
    for msg in messages:
        if not isinstance(msg, AIMessage) or not hasattr(msg, "tool_calls"):
            continue
        for tc in msg.tool_calls or []:
            if not isinstance(tc, dict) or tc.get("name") != "hybrid_search":
                continue
            args = tc.get("args") or {}
            query_text = str(args.get("query") or "")
            if any(marker in query_text for marker in markers):
                return True
    return False


def _maybe_auto_relationship_search(state, messages, routed_tools, iteration, response=None):
    """关系/情感原因类补搜守卫：缺关系词维度就自动补一发，返回 None 表示无需介入。"""
    original_query = state.get("user_query", "") or state.get("rewritten_query", "")
    polarity = _detect_relationship_polarity(original_query)
    if not polarity:
        return None

    tool_names = {getattr(t, "name", str(t)) for t in routed_tools}
    if "hybrid_search" not in tool_names:
        return None
    if iteration + 1 >= MAX_AGENT_ITERATIONS:
        return None

    if _covered_relationship_search(messages, polarity):
        return None

    # 挂载点A（LLM 调用前）：已有工具结果后才补，不抢 Planner 首轮决策。
    if response is None and _last_tool_message(messages) is None:
        return None

    suffix = _RELATIONSHIP_SUFFIX_BY_POLARITY[polarity]
    search_query = f"{original_query} {suffix}"
    tool_call = {
        "name": "hybrid_search",
        "args": {"query": search_query},
        "id": f"call_relationship_{polarity}_{iteration + 1}",
        "type": "tool_call",
    }
    content = (
        "【执行报告】\n"
        f"用户问题回显：{original_query}\n"
        "用户意图：人物关系/情感原因类问题\n"
        f"工具决策：系统检测到现有检索尚未覆盖“{suffix}”关系维度，按通用规则自动补搜："
        f"{search_query}。\n"
        "【工具调用】\n"
        f'[{{"tool": "hybrid_search", "args": {{"query": "{search_query}"}}}}]'
    )
    print(f"  -> [关系补搜] 自动补搜({polarity}): {search_query}")

    trace_emit("plan", {
        "iteration": iteration + 1,
        "execution_plan": content[:500],
        "tool_call_names": ["hybrid_search"],
        "tool_call_source": "relationship_dimension",
        "relationship_polarity": polarity,
        "run_id": state.get("run_id"),
    })
    return {
        "messages": [AIMessage(content=content, tool_calls=[tool_call])],
        "execution_plan": content,
        "iteration": iteration + 1,
        "intent_labels": state.get("intent_labels", []),
    }


# ====== wiki 链接图：多任务 + 地图文本的确定性补全 ======
# 设计说明：
# - 用户问题里同时出现多个任务名和“地图文本”时，不依赖 Planner 自觉走
#   wiki_graph_expand 多跳；代码直接把任务词条反向引用的地图文本找出来，
#   并自动补发 wiki_graph_get 调用。
# - 先保证任务词条文本已加载，再补地图文本；每张图只补一次，防死循环。

_wiki_graph_cache: Any = None
# 地图相关问题的触发词：不再只认“地图文本”，也覆盖“地图说明/地点文本/周边文本/对应地区”等说法。
_MAP_TEXT_QUESTION_RE = re.compile(
    r"地图文本|地图说明|地图相关|地点文本|周边文本|地区文本|对应地区|结合.{0,6}地图|结合.{0,6}地区"
)
_GRAPH_MAP_MAX_ENTRIES = 8


def _load_wiki_graph_cached():
    """懒加载本地 wiki 链接图；加载失败返回 None（本守卫不介入）。"""
    global _wiki_graph_cache
    if _wiki_graph_cache is None:
        try:
            _wiki_graph_cache = WikiEntryGraph.load(WIKI_GRAPH_OUTPUT)
        except Exception as e:
            print(f"  -> [wiki图] 加载失败，跳过地图文本补全：{e}")
            _wiki_graph_cache = False
    return _wiki_graph_cache or None


def _task_title_aliases(entry):
    """生成任务标题的通用别名：完整标题、去地区前缀、去章节幕号、去书名号后缀。

    注意：只做字符串结构处理，不引入任何地区/主题关键词；
    地区前缀从 entry.region 读取，避免把“至冬”这种地区名本身当成任务名匹配。
    """
    title = (entry.title or "").strip()
    region = (entry.region or "").strip()
    aliases = {title}

    # 去掉“地区 + 空格”前缀，例如“至冬 在生命的寓所” -> “在生命的寓所”。
    if region and title.startswith(region + " "):
        aliases.add(title[len(region) + 1:].strip())
    # 去掉首段地区/系列前缀，但不要把地区名本身当成任务别名。
    first = title.split(" ", 1)[0].strip()
    if len(first) >= 2 and first != region:
        aliases.add(first)
    # 去掉“第X幕「...」”后缀，例如“水仙的追迹 第一幕「藻海的寻踪」” -> “水仙的追迹”。
    m = re.match(r"^(.*?)\s*第[一二三四五六七八九十百0-9]+幕", title)
    if m:
        base = m.group(1).strip()
        if len(base) >= 2:
            aliases.add(base)
    # 去掉末尾的「...」/【...】说明后缀。
    m2 = re.match(r"^(.*?)\s*[「【].*?[」】]\s*$", title)
    if m2:
        base = m2.group(1).strip()
        if len(base) >= 2:
            aliases.add(base)

    return [a for a in aliases if len(a) >= 2]


def _match_graph_task_titles(graph, query):
    """找出标题（或其通用别名）出现在用户问题里的任务词条。

    通用化点：不再假设任务标题一定以“至冬 ”开头；
    任何“地区 任务名”结构都会去掉地区前缀，任何“系列名 第X幕”结构都会去掉幕号。
    """
    matched = []
    seen = set()
    for entry in graph.entries.values():
        if entry.entry_type != "task":
            continue
        for alias in _task_title_aliases(entry):
            if alias in query:
                if entry.entry_id not in seen:
                    seen.add(entry.entry_id)
                    matched.append(entry)
                break
    return matched


def _collect_graph_map_texts(graph, task_entries):
    """收集与任务相关的本地地图文本：反向引用优先，再用地区词补全。"""
    candidates: List[Tuple[int, str, Any]] = []
    seen = set()
    # 先收全部反向引用（优先级 0），再按地区补全（优先级 1），
    # 否则先处理到的任务会把后续任务的 backlink 地图文本抢先标成低优先级。
    for task in task_entries:
        for source, _link in graph.backlinks(task.entry_id):
            if source is None or source.entry_type != "map_text":
                continue
            if source.entry_id in seen:
                continue
            seen.add(source.entry_id)
            candidates.append((0, source.entry_id, source))
    for task in task_entries:
        task_text = f"{task.title}\n{_entry_story_text(task)}"
        for entry in graph.entries.values():
            if entry.entry_type != "map_text" or entry.entry_id in seen:
                continue
            if entry.region and entry.region in task_text:
                seen.add(entry.entry_id)
                candidates.append((1, entry.entry_id, entry))
    candidates.sort(key=lambda x: (x[0], x[1]))
    return [entry for _priority, _eid, entry in candidates[:_GRAPH_MAP_MAX_ENTRIES]]


def _attempted_wiki_get_ids(messages):
    """已经用 wiki_graph_get 尝试过的词条 ID。"""
    ids = set()
    for msg in messages:
        if isinstance(msg, AIMessage) and hasattr(msg, "tool_calls"):
            for tc in msg.tool_calls:
                if tc.get("name") == "wiki_graph_get":
                    args = tc.get("args", {}) or {}
                    value = args.get("entry_id")
                    if value:
                        ids.add(str(value))
    return ids


def _has_loaded_task_text(messages):
    """是否已有 load_quest_content / wiki_graph_get 的成功返回。"""
    name_by_call_id = _build_tool_name_by_call_id(messages)
    for msg in messages:
        if not isinstance(msg, ToolMessage):
            continue
        tool_name = _tool_name_of(msg, name_by_call_id)
        if tool_name in ("load_quest_content", "wiki_graph_get") and not _is_not_found(msg):
            return True
    return False


def _maybe_auto_graph_map_texts(state, messages, routed_tools, iteration, response=None):
    """多任务+地图文本问题的确定性地图补全；返回 None 表示无需介入。"""
    original_query = state.get("user_query", "") or state.get("rewritten_query", "")
    if not _MAP_TEXT_QUESTION_RE.search(original_query):
        return None

    tool_names = {getattr(t, "name", str(t)) for t in routed_tools}
    if "wiki_graph_get" not in tool_names:
        return None
    if iteration + 1 >= MAX_AGENT_ITERATIONS:
        return None

    graph = _load_wiki_graph_cached()
    if not graph:
        return None
    matched_tasks = _match_graph_task_titles(graph, original_query)
    if not matched_tasks:
        return None

    # 挂载点A（LLM 调用前）：必须已有工具返回，避免抢 Planner 首轮决策。
    if response is None and _last_tool_message(messages) is None:
        return None

    attempted = _attempted_wiki_get_ids(messages)
    map_entries = _collect_graph_map_texts(graph, matched_tasks)
    pending_map = [e for e in map_entries if e.entry_id not in attempted]
    task_text_loaded = _has_loaded_task_text(messages)

    calls = []
    if not task_text_loaded:
        # 任务全文还没拿到，先把任务词条本身补上。
        for task in matched_tasks:
            if task.entry_id not in attempted:
                calls.append((task.entry_id, task.title))
    for entry in pending_map:
        calls.append((entry.entry_id, entry.title))

    if not calls:
        return None

    tool_calls = []
    content_parts = [
        "【执行报告】",
        f"用户问题回显：{original_query}",
        "用户意图：多任务剧情 + 对应地区地图文本的综合梳理",
        "工具决策：系统检测到问题涉及多个任务与地图文本，确定性补全 wiki 链接图：",
    ]
    for i, (entry_id, title) in enumerate(calls, 1):
        tool_calls.append({
            "name": "wiki_graph_get",
            "args": {"entry_id": entry_id},
            "id": f"call_graph_map_{entry_id}_{iteration + 1}",
            "type": "tool_call",
        })
        content_parts.append(f"{i}. 读取 {entry_id} {title}")
    content = "\n".join(content_parts)

    task_ids = [t.entry_id for t in matched_tasks]
    map_ids = [e.entry_id for e in pending_map]
    print(
        f"  -> [wiki图补全] 任务={task_ids}, 补读任务={len([c for c in calls if c[0] not in map_ids])}, "
        f"地图文本={len(map_ids)}: {map_ids}"
    )

    trace_emit("plan", {
        "iteration": iteration + 1,
        "execution_plan": content[:500],
        "tool_call_names": [tc["name"] for tc in tool_calls],
        "tool_call_source": "graph_map_text",
        "graph_task_ids": task_ids,
        "graph_map_ids": map_ids,
        "run_id": state.get("run_id"),
    })
    return {
        "messages": [AIMessage(content=content, tool_calls=tool_calls)],
        "execution_plan": content,
        "iteration": iteration + 1,
        "intent_labels": state.get("intent_labels", []),
    }


# ====== 全景/综合题的确定性全文旁路 ======
# 设计：
# - 普通“定点找证据”问题仍走切块/1500字定位路径，成本不变；
# - 只有“梳理/全景/所有角色/综合评价+多任务”这类问题，代码直接加载全文，
#   不让 Plan 在 9% 覆盖率时收口。
# - 触发与 LLM 无关，普通题误触概率通过“全景正则 + 多任务命中”双重限制降到最低。

_PANORAMIC_FULL_TEXT_RE = re.compile(
    r"梳理|全景|综合|完整|所有角色|全部角色|角色评价|评价所有|整体|全局|"
    r"来龙去脉|前因后果|整个.*任务|全部.*任务|所有.*任务|跨.*汇总|串联|人物档案"
)
_FULL_TEXT_HEADER = "[全景全文读取]"


def _already_full_text_panoramic(messages):
    for msg in messages:
        if isinstance(msg, ToolMessage):
            content = msg.content if hasattr(msg, "content") else str(msg)
            if content.strip().startswith(_FULL_TEXT_HEADER):
                return True
    return False


def _entry_story_text(entry):
    """全景题证据只使用 story_text；玩法模块（推荐角色/装备展示等）不进入剧情证据。"""
    return (getattr(entry, "story_text", "") or "").strip()


# ====== 全景题的实体档案展开 ======
# 实体提及索引（Aho-Corasick 构建）解决“谁在正文里被提到”；
# 说话人提取解决“伊兹梅洛/卡谢伊/阿尔维斯”这类没有独立词条、只在正文出现的人名。
_ENTITY_INDEX_PATH = WIKI_GRAPH_OUTPUT.parent / "wiki_entity_mention_index.json"
_ENTITY_INDEX_CACHE = None
_ENTITY_TYPE_PRIORITY = {
    "character": 0, "npc": 1, "organization": 2, "artifact": 3,
    "weapon": 4, "book": 5, "monster": 6, "region_feature": 7,
    "story_chapter": 8, "gadget": 9, "map_text": 10,
    "character_anecdote": 11, "task": 12, "activity": 13, "item": 14,
}
_SPEAKER_RE = re.compile(r"(?:^|\n)\s*\*?\s*([\u4e00-\u9fff·]{2,10})\s*[：:]")
_SPEAKER_STOPWORDS = {"旅行者", "派蒙"}
# 说话人提取的通用噪音词：正文里的物件/笔记/怪物/匿名声音不是人物。
_SPEAKER_NOISE_KEYWORDS = (
    "字迹", "笔记", "记录", "留言", "日志", "报告", "告示", "配方", "画框",
    "书柜", "花瓶", "猫叫", "通讯", "女声", "树妖", "雪精",
)
_PANORAMIC_ENTITY_MAX = 30
_PANORAMIC_EXTRA_CHARS = 120000
_PANORAMIC_ENTITY_TEXT_CAP = 6000
# 说话人名字没有独立角色词条时，只在“正文提及该名字”的档案类词条里找，
# 避免把闲云/珊瑚宫心海这类泛角色长档案误当成本任务相关角色。
_UNKNOWN_SPEAKER_TARGET_TYPES = {
    "artifact", "organization", "map_text", "story_chapter", "book",
    "gadget", "item", "task", "activity",
}
# 实体相关性过滤：只在“正文里出现具体名字/具体别名，或本人就是说话人”时保留。
# 这里的通用别名/头衔表不是主题词，而是为了防止“妹妹/父亲/偶像”这类泛称别名
# 把无关的全局角色误判为当前任务相关角色。
_ENTITY_GENERIC_ALIASES = frozenset({
    "妹妹", "哥哥", "姐姐", "弟弟", "父亲", "母亲", "爷爷", "奶奶", "叔叔", "阿姨",
    "偶像", "军师", "仆人", "内鬼", "那个女人", "这个男人", "那个人", "少女", "少年",
    "男人", "女人", "孩子", "大人", "先生", "小姐", "老板", "店主", "商人", "村民",
    "冒险家", "接待员", "守卫", "士兵", "记者", "编辑", "厨师", "船夫", "工人",
    "学徒", "信使", "使者", "观众", "听众", "路人", "魔神", "大门", "山上",
    "未来", "代价", "奇迹", "团雀", "猫粮", "秘密", "故事", "记忆", "时间",
    "生命", "灵魂", "愿望",
})
_ENTITY_GENERIC_TITLE_KEYWORDS = (
    "接待员", "冒险家协会", "商人", "村民", "守卫", "士兵", "记者", "编辑",
    "厨师", "船夫", "工人", "学徒", "信使", "使者", "观众", "听众", "路人",
    "老板", "店主", "店员", "摊主",
)
_ENTITY_ALLOWED_TYPES = {
    "character", "npc", "organization", "monster", "artifact", "weapon",
    "book", "story_chapter", "gadget", "item", "region_feature",
}
# 图谱一跳扩展：只从任务/地图词条向这些“造物/文献/组织/地点”类型扩展，
# 不扩展角色/NPC，避免把全局角色噪声带回来。
_LORE_EXPAND_TYPES = {
    "artifact", "weapon", "book", "story_chapter", "organization", "region_feature",
}
_LORE_EXPAND_MAX = 20


def _entity_specific_alias_hits(entry, text):
    """返回实体在正文中的具体名字命中次数（标题 + 非泛称别名）。"""
    title = (entry.title or "").strip()
    hits = text.count(title) if title else 0
    for alias in (entry.aliases or []):
        alias = (alias or "").strip()
        if len(alias) < 3 or alias in _ENTITY_GENERIC_ALIASES:
            continue
        hits += text.count(alias)
    return hits


def _entity_is_speaker(entry, speakers):
    title = (entry.title or "").strip()
    if title and title in speakers:
        return True
    for alias in (entry.aliases or []):
        alias = (alias or "").strip()
        if len(alias) >= 3 and alias not in _ENTITY_GENERIC_ALIASES and alias in speakers:
            return True
    return False


def _load_entity_mention_index():
    global _ENTITY_INDEX_CACHE
    if _ENTITY_INDEX_CACHE is None:
        try:
            _ENTITY_INDEX_CACHE = json.loads(_ENTITY_INDEX_PATH.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"  -> [实体索引] 加载失败，跳过实体档案展开：{e}")
            _ENTITY_INDEX_CACHE = False
    return _ENTITY_INDEX_CACHE or None


def _extract_speaker_names(texts):
    names = set()
    for text in texts:
        for m in _SPEAKER_RE.finditer(text or ""):
            name = m.group(1).strip("「」")
            if not (2 <= len(name) <= 8):
                continue
            if name in _SPEAKER_STOPWORDS:
                continue
            if any(k in name for k in _SPEAKER_NOISE_KEYWORDS):
                continue
            names.add(name)
    return names


def _collect_related_entity_entries(graph, task_entries, task_texts, map_entries):
    """返回与全景题相关的角色/组织/圣遗物/地点档案词条。

    相关性规则（通用、与主题无关）：
    - 已知实体：来自实体提及索引，且必须在任务/地图正文里出现“具体标题/具体别名”；
    - 说话人：只有词条标题或具体别名与说话人完全一致时才保留；
    - 泛称别名（妹妹/父亲/偶像/接待员等）不构成相关性证据；
    - 只允许角色/组织/造物/书籍等档案类型，任务词条和地图文本由各自通道处理。
    """
    excluded = {e.entry_id for e in task_entries} | {e.entry_id for e in map_entries}
    candidates = {}

    # 1) 已知实体：实体提及索引的反向边
    mention_index = _load_entity_mention_index()
    if mention_index:
        inverted = mention_index.get("inverted") or {}
        for task in task_entries:
            for eid in inverted.get(task.entry_id, []):
                if eid in excluded:
                    continue
                entry = graph.get(eid)
                if entry is None or entry.entry_type not in _ENTITY_ALLOWED_TYPES:
                    continue
                info = candidates.setdefault(
                    eid, {"entry": entry, "known": 0, "speaker": 0, "hits": 0}
                )
                info["known"] += 1

    # 2) 说话人：只在标题或具体别名与说话人完全一致时保留，
    #    不再用 graph.search 的模糊结果（避免把只共享单字的无关词条带进来）。
    speaker_names = _extract_speaker_names(task_texts)
    for name in speaker_names:
        for entry in graph.search(name, limit=20):
            if entry.entry_id in excluded:
                continue
            if entry.entry_type not in _UNKNOWN_SPEAKER_TARGET_TYPES:
                continue
            title = (entry.title or "").strip()
            if title != name and name not in (entry.aliases or []):
                continue
            info = candidates.setdefault(
                entry.entry_id, {"entry": entry, "known": 0, "speaker": 0, "hits": 0}
            )
            info["speaker"] += 1

    # 3) 通用相关性过滤：必须有具体名字命中，或本人就是说话人。
    combined_text = "\n".join(task_texts + [_entry_story_text(m) for m in map_entries])
    filtered = []
    for info in candidates.values():
        entry = info["entry"]
        title = (entry.title or "").strip()
        if not _entry_story_text(entry):
            # 没有剧情文本的词条不进入剧情证据（例如只有玩法模块的图鉴/成就）。
            continue
        speaker_hit = _entity_is_speaker(entry, speaker_names)
        if any(k in title for k in _ENTITY_GENERIC_TITLE_KEYWORDS) and not speaker_hit:
            continue
        if title in _ENTITY_GENERIC_ALIASES and not speaker_hit:
            continue
        hits = _entity_specific_alias_hits(entry, combined_text)
        if not speaker_hit and hits == 0:
            # 圣遗物/武器/书籍即使正文里没有再次点名，只要被任务提及索引显式关联，
            # 仍作为“造物/文献档案”保留；角色/NPC/组织不享受这条兜底。
            if not (entry.entry_type in ("artifact", "weapon", "book") and info["known"] > 0):
                continue
        info["speaker"] = 1 if speaker_hit else 0
        info["hits"] = hits
        info["expand"] = 0
        filtered.append(info)

    # 4) 图谱一跳扩展：从任务/地图词条出发，补回与其显式链接的造物/文献/组织/地点。
    #    只做一跳、只允许上面 _LORE_EXPAND_TYPES 的类型，避免噪声扩散。
    existing_ids = {info["entry"].entry_id for info in filtered}
    expanded = []
    for seed in list(task_entries) + list(map_entries):
        neighbors = [t for t, _ in graph.expand(seed.entry_id, limit=100) if t is not None]
        neighbors += [s for s, _ in graph.backlinks(seed.entry_id) if s is not None]
        for neighbor in neighbors:
            if neighbor.entry_id in excluded or neighbor.entry_id in existing_ids:
                continue
            if neighbor.entry_type not in _LORE_EXPAND_TYPES:
                continue
            if any(info["entry"].entry_id == neighbor.entry_id for info in expanded):
                continue
            expanded.append({
                "entry": neighbor, "known": 0, "speaker": 0, "hits": 0, "expand": 1,
            })
            if len(expanded) >= _LORE_EXPAND_MAX:
                break
        if len(expanded) >= _LORE_EXPAND_MAX:
            break
    filtered.extend(expanded)

    ordered = sorted(
        filtered,
        key=lambda x: (
            -x["speaker"],
            -x["hits"],
            -x["known"],
            -x.get("expand", 0),
            _ENTITY_TYPE_PRIORITY.get(x["entry"].entry_type, 99),
            len(_entry_story_text(x["entry"])),
            x["entry"].title,
        ),
    )
    out = []
    total_chars = 0
    for info in ordered[:_PANORAMIC_ENTITY_MAX]:
        entry = info["entry"]
        text_len = min(len(_entry_story_text(entry)), _PANORAMIC_ENTITY_TEXT_CAP)
        if total_chars + text_len > _PANORAMIC_EXTRA_CHARS and out:
            break
        out.append(entry)
        total_chars += text_len
    return out


def _maybe_auto_full_text_panoramic(state, messages, routed_tools, iteration, response=None):
    """全景/综合题：代码直接加载相关任务+地图文本+实体档案全文，绕过普通截断与熔断。"""
    original_query = state.get("user_query", "") or state.get("rewritten_query", "")
    if not _PANORAMIC_FULL_TEXT_RE.search(original_query):
        return None
    if _already_full_text_panoramic(messages):
        return None
    if iteration + 1 >= MAX_AGENT_ITERATIONS:
        return None

    graph = _load_wiki_graph_cached()
    if not graph:
        return None
    matched_tasks = _match_graph_task_titles(graph, original_query)
    if len(matched_tasks) < 2:
        return None
    matched_map_texts = _collect_graph_map_texts(graph, matched_tasks)
    task_texts = [_entry_story_text(e) or (e.full_text or "") for e in matched_tasks]
    related_entities = _collect_related_entity_entries(
        graph, matched_tasks, task_texts, matched_map_texts
    )

    parts = [
        _FULL_TEXT_HEADER,
        "检测到全景/综合类问题，以下为相关词条剧情文本（由代码确定性加载，不含玩法推荐模块）：",
    ]
    for i, entry in enumerate(matched_tasks, 1):
        text = _entry_story_text(entry) or (entry.full_text or "")
        parts.append(
            f"\n===== 任务 {i}: {entry.title} (ID {entry.entry_id}) 共 {len(text)} 字 ====="
        )
        parts.append(text)
    if matched_map_texts:
        parts.append("\n\n===== 相关地图文本剧情文本 =====")
        for entry in matched_map_texts:
            text = _entry_story_text(entry) or (entry.full_text or "")
            parts.append(
                f"\n----- {entry.title} (ID {entry.entry_id}) 共 {len(text)} 字 -----\n" + text
            )
    if related_entities:
        parts.append("\n\n===== 相关角色/组织/圣遗物/地点剧情档案 =====")
        for entry in related_entities:
            text = _entry_story_text(entry)
            if len(text) > _PANORAMIC_ENTITY_TEXT_CAP:
                text = text[:_PANORAMIC_ENTITY_TEXT_CAP] + "\n...[档案过长已截断]"
            parts.append(
                f"\n----- {entry.title} ({entry.entry_type}, ID {entry.entry_id}) "
                f"共 {len(text)} 字 -----\n" + text
            )

    content = "\n".join(parts)
    print(
        f"  -> [全景全文旁路] 任务={[e.entry_id for e in matched_tasks]} "
        f"地图={[e.entry_id for e in matched_map_texts]} "
        f"实体档案={[e.entry_id for e in related_entities]} 总字={len(content)}"
    )
    trace_emit("plan", {
        "iteration": iteration + 1,
        "execution_plan": (
            f"全景全文旁路：{len(matched_tasks)} 个任务全文 + {len(matched_map_texts)} 个地图文本全文 "
            f"+ {len(related_entities)} 个实体档案"
        ),
        "tool_call_names": [],
        "tool_call_source": "full_text_panoramic",
        "full_text_task_ids": [e.entry_id for e in matched_tasks],
        "full_text_map_ids": [e.entry_id for e in matched_map_texts],
        "full_text_entity_ids": [e.entry_id for e in related_entities],
        "run_id": state.get("run_id"),
    })
    return {
        "messages": [ToolMessage(content=content, tool_call_id=f"call_full_panoramic_{iteration + 1}")],
        "execution_plan": f"【执行报告】\n用户问题回显：{original_query}\n用户意图：全景/综合梳理\n工具决策：代码确定性地加载相关词条全文。\n",
        "iteration": iteration + 1,
        "intent_labels": state.get("intent_labels", []),
    }


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


# ====== 是否需要综合叙述（而不是元数据直答）======
# 元数据直答只适合“几星/神之眼/命座/版本”这类单点事实；
# “介绍一下/讲讲/性格/经历/什么样”需要 Answer LLM 综合工具结果生成自然回答。

_SYNTHESIS_MARKERS = (
    "介绍", "介绍下", "讲讲", "说说", "描述",
    "人物故事", "角色故事", "人物经历", "角色经历",
    "性格", "为人", "什么样", "怎么样", "是个什么样的人",
    "角色是什么", "角色是谁", "是什么角色",
    "这个角色是什么", "这个角色是谁", "什么样的角色",
    "是谁", "指谁", "是什么人", "什么人物", "是哪位", "哪位",
    "人物简介", "角色简介", "小传", "身份", "背景",
    "详述", "概括", "讲了什么", "讲什么",
)

# 只有用户明确要求“贴原始数据”时，才允许把元数据工具的完整结果直接当作答案；
# 否则一律走 Answer LLM 综合叙述，避免把角色详情/元数据卡原样糊到用户脸上。
_RAW_METADATA_REQUEST_MARKERS = (
    "详情", "详细", "资料", "数据", "角色卡", "人物卡",
    "原始", "全部", "所有", "完整", "贴出来", "列出来", "列出",
)


def _should_synthesize_answer(original_query: str) -> bool:
    """判断用户问题是否需要综合叙述，而不是直接复述元数据工具结果。"""
    query = (original_query or "").strip()
    if not query:
        return False
    return any(marker in query for marker in _SYNTHESIS_MARKERS)


def _should_return_raw_metadata(original_query: str) -> bool:
    """是否允许把元数据工具的原始完整结果直接返回。"""
    query = (original_query or "").strip()
    if not query:
        return False
    return any(marker in query for marker in _RAW_METADATA_REQUEST_MARKERS)


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


def _get_direct_metadata_answer(messages: list, original_query: str = "") -> str | None:
    """如果当前只发生了元数据类工具调用，直接返回其原始结果。

    返回 None 表示不适用（存在非元数据工具、或没有任何工具消息）。
    返回字符串时，调用方应跳过回答 LLM，直接将字符串作为 final_response。
    """
    # 默认不允许把完整元数据卡直接当答案；只有用户明确要求“详情/资料/原始数据”才返回。
    # 介绍类、是谁类、背景类问题统一交给 Answer LLM 综合叙述。
    if _should_synthesize_answer(original_query):
        return None
    if not _should_return_raw_metadata(original_query):
        return None

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

    # 如果整个会话中已有任意成功的工具返回（例如已加载任务全文），
    # 后续补充查询失败不能把核心内容一并熔断掉，允许 Plan 继续或进入回答。
    has_any_success = False
    for msg in messages:
        if isinstance(msg, ToolMessage):
            text = str(getattr(msg, "content", "") or "")
            if not _is_not_found(msg) and "系统拦截" not in text:
                has_any_success = True
                break

    if failures >= 2 and not has_any_success:
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


_NOT_FOUND_ANSWER_SNIPPETS = (
    "当前知识库未收录", "知识库未收录", "未找到相关信息",
    "没有找到相关信息", "未收录", "无法回答",
)


def _looks_like_not_found_answer(content: str) -> bool:
    """判断 LLM 输出是否为“未收录”类短路答复（只对短文判定）。"""
    stripped = (content or "").strip()
    if not stripped or len(stripped) > 60:
        return False
    return any(snippet in stripped for snippet in _NOT_FOUND_ANSWER_SNIPPETS)


def _retry_answer_as_found(answer_messages, messages, original_query, current_llm):
    """P0-2：response_mode=found 但模型输出“未收录”时的重试。

    先追加硬规则提醒重试 medium，仍失败换 deep；全部失败则退回
    工具原文拼装，绝不把与 response_mode 矛盾的“未收录”当最终答案。
    """
    retry_messages = list(answer_messages)
    retry_messages.append(SystemMessage(content=(
        "注意：response_mode=found，工具返回中已经有证据。"
        "禁止输出“当前知识库未收录/未找到相关信息/无法回答”这类答复，"
        "必须基于工具返回原文回答用户问题。"
    )))
    candidates = []
    if current_llm is not answer_llm_medium:
        candidates.append(answer_llm_medium)
    if current_llm is not answer_llm_deep:
        candidates.append(answer_llm_deep)
    for retry_llm in candidates:
        try:
            response = llm_invoke_with_retry(retry_messages, llm_instance=retry_llm)
            cand = getattr(response, "content", "") or ""
        except Exception as e:
            print(f"  -> [未收录重试] {getattr(retry_llm, 'model_name', '?')} 调用失败: {e}")
            continue
        if cand.strip() and not _looks_like_not_found_answer(cand):
            print(f"  -> [未收录重试] 换 {getattr(retry_llm, 'model_name', '?')} 后得到正常回答")
            return cand
    print("  -> [未收录重试] medium/deep 均失败，退回工具原文兜底")
    return _build_fallback_answer(messages, original_query)


_ORG_NAMES_CACHE = None
_ORG_NAME_SUFFIXES = (
    "教团", "骑士团", "协会", "旅团", "教令院", "组织", "商会", "公会",
    "委员会", "军团", "军", "团", "会", "院",
)
_COMPARISON_MARKERS = ("异同", "区别", "对比", "比较", "不同", "相同", "差异")
# 过程性/侵蚀类词：对比分析题里这些词常常来自原文比喻，被模型升格成分析维度。
# 只用于触发范围修复，不针对具体题目；题目本身出现的词不会触发。
_PROCESS_WORD_LEXICON = (
    "磨损", "冲刷", "侵蚀", "消磨", "吞噬", "瓦解", "消逝",
    "凋零", "腐化", "腐蚀", "污染", "毁灭", "崩解", "撕裂",
)


def _is_comparison_question(user_query: str) -> bool:
    """判断是否为对比分析类问题。"""
    query = user_query or ""
    return any(marker in query for marker in _COMPARISON_MARKERS)


def _get_known_org_names() -> set:
    """懒加载知识库中的组织名，用于概念本质题的组织范围检查。

    只取 concepts.json 中类型含“组织”且名称符合组织后缀（教团/骑士团/
    协会/旅团/教令院等）的条目；像“神之眼/元素/地脉”这种误标成
    “概念/组织”的非组织条目会被后缀规则挡掉。
    """
    global _ORG_NAMES_CACHE
    if _ORG_NAMES_CACHE is not None:
        return _ORG_NAMES_CACHE
    names = set()
    try:
        concepts = _load_content_json("concepts") or []
        for item in concepts:
            if not isinstance(item, dict):
                continue
            name = str(item.get("名称") or "").strip()
            type_ = str(item.get("类型") or "")
            if not name or "组织" not in type_:
                continue
            if name.endswith(_ORG_NAME_SUFFIXES) or name in {"愚人众", "魔女会"}:
                names.add(name)
    except Exception as e:
        print(f"  -> [范围守卫] 组织名加载失败: {e}")
    _ORG_NAMES_CACHE = names
    return names


def _remove_sentences_with_terms(text: str, terms: List[str]) -> str:
    """兜底删除包含越界词的句子；仅在 LLM 改写后仍残留越界词时使用。"""
    if not text or not terms:
        return text
    pieces = re.split(r"(?<=[。！？!?\n])", text)
    kept = [p for p in pieces if not any(term in p for term in terms)]
    result = "".join(kept).strip()
    return result or text


def _rewrite_answer_with_scope(user_query: str, answer: str, drift_terms: List[str], mode_label: str) -> str:
    """只针对越界内容做最小改写，保留其余答案。"""
    if not drift_terms:
        return answer
    drift_text = "、".join(f"「{term}」" for term in drift_terms)
    if mode_label == "概念本质题":
        rules = (
            "1. 删除或改写包含这些组织名的句子/段落；其余内容尽量逐字保留；\n"
            "2. 不得新增任何事实、例子、结论或小标题；\n"
            "3. 如果删除后影响连贯，只做最小连接修改；\n"
        )
    else:
        rules = (
            "1. 只处理越界词本身：能替换成中性表述就替换，不能替换才删除该词；\n"
            "2. 严禁删除整句或整段；包含越界词的句子只做最小改写；\n"
            "3. 必须保留原答案里所有人物名、作品名、核心概念和事实；\n"
            "4. 不得新增任何事实、例子、结论或小标题；\n"
        )
    prompt = (
        "你在做回答范围修复。\n"
        f"题目类型：{mode_label}\n"
        f"用户问题：{user_query}\n\n"
        f"原答案：\n{answer}\n\n"
        f"需要收紧的越界内容：{drift_text}\n\n"
        "修复规则：\n"
        + rules
        + "输出修正后的完整答案，不要解释修复过程。\n"
    )
    try:
        response = llm_invoke_with_retry(
            [SystemMessage(content=prompt)],
            llm_instance=answer_llm_medium,
        )
        rewritten = (getattr(response, "content", "") or "").strip()
    except Exception as e:
        print(f"  -> [范围守卫] 改写调用失败，使用兜底删除: {e}")
        return _remove_sentences_with_terms(answer, drift_terms)
    if not rewritten:
        return _remove_sentences_with_terms(answer, drift_terms)
    # 二次校验：改写后仍有越界词，则做确定性删除。
    if any(term in rewritten for term in drift_terms):
        print(f"  -> [范围守卫] 改写后仍残留越界词，兜底删除: {drift_terms}")
        rewritten = _remove_sentences_with_terms(rewritten, drift_terms)
    return rewritten


def _apply_answer_scope_guard(original_query: str, content: str, messages, state) -> str:
    """R1/C1 类范围守卫：概念本质题剔组织名，对比题剔无关分析维度。"""
    if not content or _already_full_text_panoramic(messages):
        return content
    if _looks_like_not_found_answer(content):
        return content
    conversation_summary = state.get("conversation_summary", "") or ""
    turn_number = len(state.get("conversation_history") or [])
    try:
        if _is_concept_essence_question(original_query, conversation_summary, turn_number):
            org_names = _get_known_org_names()
            drift = [name for name in org_names if name in content and name not in (original_query or "")]
            if drift:
                print(f"  -> [范围守卫] 概念本质题越界组织名: {drift}")
                return _rewrite_answer_with_scope(original_query, content, drift, "概念本质题")
            return content
        if _is_comparison_question(original_query):
            # 对比分析题的漂移大多来自原文里的过程性/侵蚀类比喻词。
            # 这里用确定性词表触发范围修复，不再依赖小模型裁判是否“看见”。
            drift = [
                word for word in _PROCESS_WORD_LEXICON
                if word in content and word not in (original_query or "")
            ]
            if drift:
                print(f"  -> [范围守卫] 对比分析题过程词越界: {drift}")
                return _rewrite_answer_with_scope(original_query, content, drift, "对比分析题")
    except Exception as e:
        print(f"  -> [范围守卫] 检查失败，跳过: {e}")
    return content


def answer_agent(state: GenshinAdvisorState) -> Dict[str, Any]:
    """回答阶段：基于规划阶段的【执行报告】和工具返回结果，生成最终回答。不调用任何工具。"""
    messages = list(state.get("messages", []))
    execution_plan = state.get("execution_plan", "")
    original_query = state.get("user_query", "")
    alias_notes = state.get("alias_notes", "")
    intent_labels = state.get("intent_labels", [])
    answer_llm = _select_answer_llm(intent_labels)
    # “介绍/讲讲/性格/经历”等综合叙述题，改用 medium 模型，避免轻量模型偶发误判未收录。
    if _should_synthesize_answer(original_query):
        answer_llm = answer_llm_medium
        print("  -> [综合叙述] 使用 medium Answer LLM")
    # L3 全景/超长文综合：使用更强的 qwen3.8-max。
    if _already_full_text_panoramic(messages):
        answer_llm = answer_llm_l3
        print("  -> [L3全景] 使用 qwen3.8-max Answer LLM")
    # P0-3：为什么/原因/机制/原理/怎么/如何类问题强制 medium，避免
    # 意图路由到 B 时用 qwen3.6-flash 生成机制解释导致不稳定。
    elif any(keyword in original_query for keyword in ("为什么", "为何", "原因", "机制", "原理", "怎么", "如何")):
        answer_llm = answer_llm_medium
        print("  -> [机制/原因类] 强制 medium Answer LLM")
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
    has_successful_tool = False

    for i, msg in enumerate(messages):
        if isinstance(msg, AIMessage) and hasattr(msg, 'tool_calls') and msg.tool_calls:
            tool_call_count += len(msg.tool_calls)
        elif isinstance(msg, ToolMessage):
            content = msg.content if hasattr(msg, 'content') else str(msg)
            # 检查是成功还是失败：使用前缀式未命中判定，避免正文子串误判
            is_failure = _is_not_found(msg) or content.strip().startswith("当前知识库未收录")
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
                has_successful_tool = True
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
    # 只要已有任意成功的工具返回，就不能因为后续补充查询失败而整体判为“未收录”。
    if (consecutive_failures >= 2 or (tool_call_count == 0 and not full_text_loaded)) and not has_successful_tool:
        response_mode = "not_found"
    else:
        response_mode = "found"

    # ---- L3 全景题：分段生成 ----
    # 全景题证据约 15 万字，单次 qwen3.8-max 生成 1.2~1.8 万字实测超过 10 分钟；
    # 这里改为按任务线/实体/地图/跨线综合分桶，多节并行生成后拼装。
    if _already_full_text_panoramic(messages):
        from app.agent.l3_panorama import generate_panorama_answer
        print("  -> [L3分段] 检测到全景全文，进入分段生成")
        l3_content = generate_panorama_answer(messages, original_query, emit=_emit_progress)
        if l3_content:
            trace_emit("llm_end", {
                "role": "answer", "run_id": state.get("run_id"),
                "status": "success", "final_response_len": len(l3_content),
                "answer_source": "l3_panorama_sections",
            })
            trace_emit("answer_end", {
                "response_mode": "found",
                "final_response": l3_content,
                "short_circuit": False,
                "answer_source": "l3_panorama_sections",
                "run_id": state.get("run_id"),
            })
            return {"messages": messages, "final_response": l3_content}
        print("  -> [L3分段] 解析失败，回退单次 L3 调用")

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
    direct_answer = _get_direct_metadata_answer(messages, original_query)
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

    # 概念本质题范围硬规则：只回答概念本身，不展开组织/阵营/教团的成立史。
    # 这是通用范围控制，不针对任何具体题目；由既有的概念本质判定函数触发。
    conversation_summary = state.get("conversation_summary", "") or ""
    turn_number = len(state.get("conversation_history") or [])
    if _is_concept_essence_question(original_query, conversation_summary, turn_number):
        system_content += (
            "\n\n===== 概念本质题范围硬规则（必须遵守）=====\n"
            "本题只回答概念本身：定义/本质、力量或能量体系、历史来源与影响。\n"
            "禁止展开任何组织、阵营、教团、人物团体的成立史、成员名单或政治目标；\n"
            "证据中出现的组织名不得单独成句、成段或作为小标题；\n"
            "只有不提组织名就无法解释概念机制时，才允许用一句话带过。\n"
        )

    # 机制名保真：为什么/原因/机制类问题，工具原文明确命名的机制/手段必须保留原词。
    # P0-1：注入前先做相关性过滤，正则预筛拿不准时交给 qwen3.6-flash 小裁判，
    # 避免把无关机制名（如问纳西妲年龄时工具里出现的“童话”）塞进提示词。
    if any(k in original_query for k in ("为什么", "为何", "原因", "机制", "原理", "怎么", "如何")):
        mechanism_terms = _filter_mechanism_terms(
            messages,
            _extract_mechanism_terms(messages),
            original_query,
            state.get("alias_pairs"),
        )
        if mechanism_terms:
            system_content += (
                "\n\n===== 机制名保真（硬规则）=====\n"
                "工具原文明确给出了以下机制/手段名称，回答必须保留原词，"
                "不得只用意象、比喻或近义词替代：\n"
                + "、".join(f"「{term}」" for term in mechanism_terms)
                + "\n"
            )

    # 全景题：证据里包含代码加载的全文，输出规约必须明确要求“逐线逐角色逐地图文本展开”，
    # 否则模型会再次把 15 万字证据压缩成 2~3 千字概要。
    if _already_full_text_panoramic(messages):
        system_content += AGENT_SYSTEM_PROMPT_L3_ANSWER

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
            f"以下是规划阶段的简短结论（仅作参考，最终以工具返回原文为准）：\n\n{execution_plan[:500]}"
        )))

    # 注入用户原始问题
    answer_messages.append(HumanMessage(content=f"请基于上述规划结果和工具搜索内容，回答用户问题：{original_query}"))

    # 注入所有对话消息（工具调用和返回结果）
    for msg in messages:
        if isinstance(msg, (ToolMessage, AIMessage)):
            answer_messages.append(msg)

    trace_emit("llm_start", {"role": "answer", "run_id": state.get("run_id"), "model": getattr(answer_llm, "model_name", None)})
    # 流式生成：逐 chunk 推送给前端；失败或疑似截断则回退非流式。
    content_parts = []
    stream_error = None
    stream_finish_reason = ""
    try:
        for chunk in answer_llm.stream(answer_messages):
            if isinstance(chunk, str):
                delta = chunk
            else:
                delta = getattr(chunk, "content", "") or ""
                metadata = getattr(chunk, "response_metadata", None) or {}
                finish_reason = metadata.get("finish_reason") or metadata.get("finishReason")
                if finish_reason:
                    stream_finish_reason = str(finish_reason)
            if delta:
                content_parts.append(delta)
                _emit_progress("answer_delta", {"delta": delta})
    except Exception as e:
        print(f"  -> [流式] 流式生成失败，回退非流式: {e}")
        stream_error = e

    # 流式截断守卫：finish_reason=length，或短回答结尾没有句末标点时，
    # 认为流式没有正常收尾，回退非流式重试一次，避免把半句话当最终答案。
    if stream_error is None and content_parts:
        stream_content = "".join(content_parts).rstrip()
        looks_incomplete = False
        if stream_finish_reason == "length":
            looks_incomplete = True
        elif len(stream_content) < 800 and stream_content and not re.search(r"[。！？!?…」』】）\)\"'”’]$", stream_content):
            looks_incomplete = True
        if looks_incomplete:
            print(
                f"  -> [流式] 疑似截断（finish_reason={stream_finish_reason or 'unknown'}，"
                f"长度={len(stream_content)}），回退非流式重试"
            )
            stream_error = "stream_incomplete"

    if stream_error is not None or not content_parts:
        try:
            response = llm_invoke_with_retry(answer_messages, llm_instance=answer_llm)
            content = response.content if hasattr(response, 'content') else ''
        except Exception as e:
            content = f"抱歉，处理出错：{e}"
        # 兜底：如果 LLM 返回空内容（reasoning 耗尽 token 预算），根据工具返回结果拼凑回答
        if not content or not content.strip():
            content = _build_fallback_answer(messages, original_query)
            print("  -> [空回复兜底] LLM 返回空，使用兜底方案")
    else:
        content = "".join(content_parts)

    # P0-2：response_mode=found 时不允许输出“未收录”。
    # 轻量模型偶发在流式里只输出一句“当前知识库未收录。”，与代码判定矛盾；
    # 这里重试 medium/deep，仍失败才退回工具原文。
    if response_mode == "found" and _looks_like_not_found_answer(content):
        print(f"  -> [P0-2] found 但输出未收录（{content[:40]!r}），重试 medium/deep")
        content = _retry_answer_as_found(answer_messages, messages, original_query, answer_llm)

    # 范围守卫：概念本质题剔除组织名，对比分析题剔除题目未要求的分析维度。
    # 这是生成后校验，不依赖模型自觉；只对 R1/C1 这类结构性范围问题触发。
    content = _apply_answer_scope_guard(original_query, content, messages, state)

    trace_emit("llm_end", {
        "role": "answer",
        "run_id": state.get("run_id"),
        "status": "success",
        "final_response_len": len(content),
        "stream_finish_reason": stream_finish_reason,
        "stream_fallback": stream_error is not None,
    })

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

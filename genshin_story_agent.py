#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
原神剧情助手 - LangGraph Agent（分层执行架构 v3）

架构：别名归一化 → 路径分类(L1/L2) → [L1] 快速回答 / [L2] Agent 循环（LLM + 工具，最多 10 轮迭代）→ 回答
L1（简单事实）：合并 Plan/Answer，最多 2 轮工具调用
L2（复杂推理）：保留完整 Plan → Tool → Answer 流程

LLM: 通义千问 qwen3.7-max
"""

import os
import sys
import re
import json
import math
import time
import threading
import warnings
import requests
from collections import Counter, OrderedDict
from typing import Dict, List, Any, Literal, TypedDict, Optional, Annotated
from datetime import datetime

warnings.filterwarnings("ignore")

from dotenv import load_dotenv
if getattr(sys, 'frozen', False):
    # exe 模式：从 PyInstaller 解压目录读取 .env
    load_dotenv(os.path.join(sys._MEIPASS, '.env'))
else:
    load_dotenv()

os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
os.environ.setdefault("HF_HUB_OFFLINE", "1")

# 禁用 LangSmith tracing（项目暂不需要，避免浪费 API 限额）
os.environ["LANGCHAIN_TRACING_V2"] = "false"
os.environ["LANGCHAIN_ENDPOINT"] = ""
os.environ["LANGCHAIN_API_KEY"] = ""
os.environ["LANGCHAIN_PROJECT"] = ""

if sys.platform == 'win32':
    try:
        if sys.stdout is not None:
            sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

from langchain_core.prompts import ChatPromptTemplate
from langchain_core.messages import HumanMessage, AIMessage, BaseMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool
from langchain_core.output_parsers import StrOutputParser
from langchain_openai import ChatOpenAI
from langgraph.graph import StateGraph, END
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode

# ====== 导入知识库 ======
try:
    from genshin_knowledge_base import (
        角色知识库, 地区知识库, 主线剧情知识库, 武器知识库,
        任务知识库, 素材知识库, 圣遗物知识库
    )
except ImportError:
    角色知识库, 地区知识库, 主线剧情知识库 = [], [], []
    武器知识库, 任务知识库, 素材知识库, 圣遗物知识库 = [], [], [], []

CONTENT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "content_data")

# 加载 NPC 数据（供 query_character 兜底）
_npcs_data = {}
_npcs_path = os.path.join(CONTENT_DIR, "npcs_processed.json")
if os.path.exists(_npcs_path):
    try:
        with open(_npcs_path, "r", encoding="utf-8") as f:
            _npcs_data = json.load(f)
        print(f"[初始化] NPC 数据已加载: {len(_npcs_data)} 条")
    except Exception as e:
        print(f"[警告] NPC 数据加载失败: {e}")

from character_aliases import CHARACTER_ALIASES, resolve_aliases, ALIAS_MAP, ALIASES_SORTED
from intent_router import route_intent, get_tools_for_intent, get_pseudo_legendary_note

# ====== RAG 记忆 ======
try:
    from memory_manager import GenshinRAGMemory
    RAG_AVAILABLE = True
    print("[初始化] RAG记忆系统已加载")
except ImportError:
    RAG_AVAILABLE = False
    print("[警告] RAG记忆系统不可用")

# ====== 向量知识库 ======
try:
    from kb_vector_store import KBVectorStore
    _vector_store = KBVectorStore()
    _vector_stats = _vector_store.get_stats()
    _vector_total = sum(_vector_stats.values())
    print(f"[初始化] 向量知识库已加载: {_vector_stats}, 总计 {_vector_total} 条")
except Exception as e:
    _vector_store = None
    print(f"[警告] 向量知识库不可用: {e}")

# ====== LLM 配置 ======
QWEN_API_KEY = os.getenv("DASHSCOPE_API_KEY")
QWEN_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"

llm = ChatOpenAI(
    model="qwen3.7-max",
    api_key=QWEN_API_KEY,
    base_url=QWEN_BASE_URL,
    temperature=0.2,
    max_tokens=65536,
    request_timeout=180,
    model_kwargs={
        "reasoning_effort": "medium",
    },
)

# Plan L1 专用 LLM（简单题，fast_agent）：不需要深度思考，快速决策
plan_llm = ChatOpenAI(
    model="qwen-plus",
    api_key=QWEN_API_KEY,
    base_url=QWEN_BASE_URL,
    temperature=0.1,
    max_tokens=4096,
    request_timeout=60,
)

# Plan L2 专用 LLM（复杂题，plan_agent）：轻度思考，确保多子问题拆解和工具选择准确性
plan_llm_l2 = ChatOpenAI(
    model="qwen3.7-max",
    api_key=QWEN_API_KEY,
    base_url=QWEN_BASE_URL,
    temperature=0.1,
    max_tokens=32768,
    request_timeout=120,
    model_kwargs={
        "reasoning_effort": "low",
    },
)

# 按意图分级配置 Answer LLM
# 轻量（简单事实/角色查询）：不开思考，快速响应
answer_llm_light = ChatOpenAI(
    model="qwen-plus",
    api_key=QWEN_API_KEY,
    base_url=QWEN_BASE_URL,
    temperature=0.2,
    max_tokens=4096,
    request_timeout=60,
)
# 中等（搜索/世界观/书籍）：轻度思考
answer_llm_medium = ChatOpenAI(
    model="qwen3.7-max",
    api_key=QWEN_API_KEY,
    base_url=QWEN_BASE_URL,
    temperature=0.2,
    max_tokens=32768,
    request_timeout=120,
    model_kwargs={
        "reasoning_effort": "low",
    },
)
# 深度（剧情任务/溯源追踪）：充分思考
answer_llm_deep = ChatOpenAI(
    model="qwen3.7-max",
    api_key=QWEN_API_KEY,
    base_url=QWEN_BASE_URL,
    temperature=0.2,
    max_tokens=65536,
    request_timeout=180,
    model_kwargs={
        "reasoning_effort": "medium",
    },
)

# 意图 → Answer LLM 映射：取最高优先级的意图
INTENT_LLM_MAP = {
    "D": answer_llm_deep,     # 剧情任务
    "F": answer_llm_deep,     # 溯源追踪
    "C2": answer_llm_deep,    # 世界观设定
    "E": answer_llm_medium,   # 书籍文献
    "A": answer_llm_medium,   # 搜索检索
    "B": answer_llm_light,    # 角色查询
}

def _select_answer_llm(intent_labels: list) -> ChatOpenAI:
    """根据意图标签选择最合适的 Answer LLM。
    多个意图取最高优先级（Deep > Medium > Light）。"""
    if not intent_labels:
        return answer_llm_deep  # 未知意图用深度
    # 按优先级查找：先找 D/F/C2，再 E/A，最后 B
    for priority_key in ("D", "F", "C2", "E", "A", "B"):
        if priority_key in intent_labels:
            return INTENT_LLM_MAP[priority_key]
    return answer_llm_deep

# ====== DeepSeek 别名判断（安全沙箱，不计入 agent 轮次） ======

alias_judge_llm = ChatOpenAI(
    model="deepseek-v4-flash",
    api_key=DEEPSEEK_API_KEY,
    base_url=DEEPSEEK_BASE_URL,
    temperature=0,
    max_tokens=20,
    request_timeout=10,
)

# ====== 查询分类器（L1/L2 判断，轻量模型）======
assess_llm = ChatOpenAI(
    model="deepseek-v4-flash",
    api_key=DEEPSEEK_API_KEY,
    base_url=DEEPSEEK_BASE_URL,
    temperature=0,
    max_tokens=50,
    request_timeout=10,
)


def _sanitize_query(query: str) -> str:
    """安全层：移除用户输入中的指令性内容，提取核心查询文本。
    剥离括号内容、末尾标点等非实体部分。"""
    # 移除中英文括号及其内容（典型 prompt 注入路径）
    cleaned = re.sub(r'[（(][^）)]*[）)]', '', query)
    # 移除末尾纯标点
    cleaned = re.sub(r'[？！。，、；：\s]+$', '', cleaned)
    return cleaned.strip()


def _is_compound_hit(query: str, alias: str, pos: int) -> bool:
    """判断别名是否作为复合词的一部分出现（如 '风龙' 在 '风龙废墟' 中）。
    如果有相邻 CJK 字符，视为复合词的一部分。"""
    before = query[pos - 1] if pos > 0 else ''
    after = query[pos + len(alias)] if pos + len(alias) < len(query) else ''
    cjk = re.compile(r'[\u4e00-\u9fff]')
    return bool(cjk.match(before) or cjk.match(after))


def _judge_alias_sandbox(alias: str, canonical: str, context: str) -> bool:
    """安全沙箱：用 deepseek-v4-flash 判断别名是否应替换。
    固定 prompt 模板，模型只能输出 KEEP 或 REPLACE，无法被注入。
    返回 True 表示替换，False 表示保留原样。"""
    prompt = (
        f"你是原神知识库的别名查询工具。你的唯一任务是判断一个词在上下文中是否指代特定角色。\n\n"
        f"已知：「{alias}」在某些语境下是角色「{canonical}」的别名。\n\n"
        f"上下文片段：「{context}」\n\n"
        f"判断规则：\n"
        f"- 如果「{alias}」在上下文中是地名、建筑名、书名、物品名、技能名等非角色名词的组成部分（如\"风龙废墟\"中的\"风龙\"是地名的一部分），输出 KEEP\n"
        f"- 如果「{alias}」在上下文中确实指代角色本身（如\"风龙的力量\"中的\"风龙\"指角色特瓦林），输出 REPLACE\n\n"
        f"只输出一个词：KEEP 或 REPLACE。不要输出任何其他内容。"
    )
    try:
        response = alias_judge_llm.invoke([HumanMessage(content=prompt)])
        result = response.content.strip().upper()
        return "REPLACE" in result
    except Exception as e:
        print(f"  [AliasJudge] 调用失败: {e}，默认保留原样")
        return False


# ====== Reranker（qwen3-rerank，DashScope 原生接口）======
def _rerank(query: str, documents: List[str], top_n: int = 10) -> Optional[List[int]]:
    """对候选文档列表重排序，返回按相关性降序排列的原始索引列表。失败时返回 None。"""
    if len(documents) <= top_n:
        return None  # 候选太少，无需重排
    try:
        resp = requests.post(
            "https://dashscope.aliyuncs.com/api/v1/services/rerank/text-rerank/text-rerank",
            headers={
                "Authorization": f"Bearer {QWEN_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": "qwen3-rerank",
                "input": {
                    "query": query,
                    "documents": documents,
                },
                "parameters": {
                    "top_n": min(top_n, len(documents)),
                },
            },
            timeout=15,
        )
        if resp.status_code == 200:
            data = resp.json()
            results = data.get("output", {}).get("results", [])
            if results:
                return [r["index"] for r in results]
    except Exception as e:
        print(f"  [Reranker] 调用失败: {e}")
    return None


# ====== 混合检索（Hybrid Search + RRF）======

TITLE_REGISTRY: Dict[str, dict] = {}
SECTION_TO_TITLE: Dict[str, str] = {}

def _build_title_registry(content_dir: str):
    """启动时构建任务标题注册表和章节→标题反向索引。
    章节索引用于 kb_quests_vec 的 ID 解析——该集合的文档以"===章节标题==="开头，
    通过此索引反查所属任务标题，使 RRF 融合时能与关键词路正确合并。"""
    registry = {}
    section_map = {}
    for fname in os.listdir(content_dir):
        if not fname.startswith("quests_") or not fname.endswith(".json"):
            continue
        if fname == "quests_processed.json":
            continue
        try:
            with open(os.path.join(content_dir, fname), "r", encoding="utf-8") as f:
                quests = json.load(f)
        except Exception:
            print(f"[警告] 加载任务文件 {fname} 失败，跳过")
            continue
        if not isinstance(quests, list):
            continue
        for q in quests:
            title = q.get("title", "")
            if not title:
                continue
            registry[title] = {
                "category": q.get("category", ""),
                "version": q.get("metadata", {}).get("实装版本", ""),
                "region": q.get("metadata", {}).get("任务地区", ""),
            }
            # 构建章节→标题反向索引
            text = q.get("text", "")
            for m in re.finditer(r'={2,}([^=\n]+?)={2,}', text):
                sec = m.group(1).strip()
                if len(sec) >= 2:
                    section_map[sec] = title
    return registry, section_map


# 构建任务标题注册表和章节索引
try:
    TITLE_REGISTRY, SECTION_TO_TITLE = _build_title_registry(CONTENT_DIR)
    print(f"[初始化] 任务标题注册表已构建: {len(TITLE_REGISTRY)} 个任务, {len(SECTION_TO_TITLE)} 个章节索引")
except Exception as e:
    print(f"[警告] 任务标题注册表构建失败: {e}")

# 向量 ID 前缀映射（前缀名 → 前缀长度）
_VEC_PREFIX_MAP = {
    "kb_quests_bm25": len("quest:"),
    "kb_lore": len("lore:"),
    "kb_books": len("book:"),
    "kb_characters": len("character:"),
    "kb_regions": len("region:"),
}


def _get_doc_key(item: dict) -> str:
    """从检索结果中提取文档标识 key，用于 RRF 融合。"""
    doc_id = item.get("id", "")
    collection = item.get("collection", "")

    # kb_quests_vec 先尝试 ID 解析（新格式 quest:{title}:chunk:{N}），
    # 再兜底文档文本解析（旧格式，章节标题开头）
    if collection == "kb_quests_vec":
        # 方式1：ID 解析（重建后全量使用此格式）
        if doc_id.startswith("quest:"):
            rest = doc_id[6:]  # 去掉 "quest:" 前缀
            title = rest.split(":", 1)[0]
            if title in TITLE_REGISTRY:
                return title
        # 方式2：文档文本解析（旧格式兼容，文档以"===章节标题==="开头）
        doc_text = item.get("document", "")
        m = re.search(r'={2,}([^=\n]+?)={2,}', doc_text)
        if m:
            section_name = m.group(1).strip()
            title = SECTION_TO_TITLE.get(section_name)
            if title and title in TITLE_REGISTRY:
                return title
        return doc_id  # 无法解析，保留原始 ID

    if collection in _VEC_PREFIX_MAP:
        prefix_len = _VEC_PREFIX_MAP[collection]
        rest = doc_id[prefix_len:]
        if collection in ("kb_quests", "kb_quests_bm25"):
            # "quest:{title}:chunk:{N}" → 取第一段为标题
            title = rest.split(":", 1)[0]
        else:
            title = rest
    else:
        title = doc_id

    # 校验：quests 类型的 title 必须在注册表中
    if collection == "kb_quests_bm25" and title not in TITLE_REGISTRY:
        return doc_id  # 兜底：保留原始 ID，防止错误合并
    return title


def _rrf_fusion(kw_items: list, vec_items: list, k: int = 60) -> list:
    """RRF（倒数排名融合）两路检索结果，返回按融合分降序排列的 (doc_key, score) 列表。"""
    scores: Dict[str, float] = {}
    for rank, item in enumerate(kw_items):
        key = _get_doc_key(item)
        scores[key] = scores.get(key, 0) + 1.0 / (k + rank + 1)
    for rank, item in enumerate(vec_items):
        key = _get_doc_key(item)
        scores[key] = scores.get(key, 0) + 1.0 / (k + rank + 1)
    return sorted(scores.items(), key=lambda x: x[1], reverse=True)


def _keyword_search_docs(query: str, top_k: int = 15) -> list:
    """关键词路：搜索任务内容、世界观设定、书籍正文。
    返回格式与向量结果统一：[{id, collection, document, category}]。"""
    results = []
    search_terms = _expand_query_with_aliases(query)

    # --- 任务内容关键词搜索 ---
    quest_candidates = []
    seen_candidates = set()
    for filename in os.listdir(CONTENT_DIR):
        if not filename.startswith("quests_") or not filename.endswith(".json"):
            continue
        if filename == "quests_processed.json":
            continue
        if len(quest_candidates) >= 200:
            break
        try:
            with open(os.path.join(CONTENT_DIR, filename), "r", encoding="utf-8") as f:
                quests = json.load(f)
        except Exception:
            continue
        if not isinstance(quests, list):
            continue
        for q in quests:
            if len(quest_candidates) >= 200:
                break
            text = q.get("text", "")
            matched_term = None
            for term in search_terms:
                if _match_all_in(term, text):
                    matched_term = term
                    break
            if not matched_term:
                continue
            first_word = matched_term.split()[0]
            words = matched_term.split()
            if len(words) > 1 and len(text) > 600:
                # 长文档多搜索词：找到覆盖最多不同搜索词的窗口位置，避免只截取第一个词的首次出现
                best_pos = -1
                best_count = 0
                for w in words:
                    pos = 0
                    while True:
                        idx = text.find(w, pos)
                        if idx == -1:
                            break
                        window_text = text[max(0, idx-120):min(len(text), idx+120)]
                        count = sum(1 for w2 in words if w2 in window_text)
                        if count > best_count:
                            best_count = count
                            best_pos = idx
                        pos = idx + len(w)
                if best_pos >= 0:
                    start = max(0, best_pos - 120)
                    end = min(len(text), best_pos + 120)
                else:
                    start = max(0, text.find(first_word))
                    end = min(len(text), start + 240)
            else:
                idx = text.find(first_word)
                start = max(0, idx - 120)
                end = min(len(text), idx + len(matched_term) + 120)
            snippet = text[start:end].replace('\n', ' ').strip()
            dedup_key = (q["title"], snippet[:60])
            if dedup_key not in seen_candidates:
                seen_candidates.add(dedup_key)
                quest_candidates.append((q["title"], q.get("category", ""), snippet))

    # Reranker 重排序
    if quest_candidates:
        rerank_docs = [f"【{c[0]}】{c[2]}" for c in quest_candidates]
        reranked = _rerank(query, rerank_docs, top_n=min(top_k, len(quest_candidates)))
        if reranked:
            for idx in reranked:
                c = quest_candidates[idx]
                results.append({
                    "id": f"quest:{c[0]}:chunk:0",
                    "collection": "kb_quests_bm25",
                    "document": c[2],
                    "category": c[1],
                })
        else:
            for c in quest_candidates[:top_k]:
                results.append({
                    "id": f"quest:{c[0]}:chunk:0",
                    "collection": "kb_quests_bm25",
                    "document": c[2],
                    "category": c[1],
                })

    # --- 世界观设定搜索 ---
    lore_path = os.path.join(CONTENT_DIR, "lore.json")
    if os.path.exists(lore_path):
        try:
            with open(lore_path, "r", encoding="utf-8") as f:
                lore = json.load(f)
        except Exception:
            lore = []
        if lore:
            lore_candidates = []
            seen_lore = set()
            lore_terms = [query]
            if len(query) >= 3:
                lore_terms.append(query[:-1])
            if len(query) >= 4:
                lore_terms.append(query[:-2])
            for term in lore_terms:
                for entry in lore:
                    if term in entry["text"]:
                        eid = entry["title"] + entry["text"][:40]
                        if eid not in seen_lore:
                            seen_lore.add(eid)
                            lore_candidates.append(entry)
            if lore_candidates:
                rerank_docs = [f"【{c['title']}】{c['text']}" for c in lore_candidates]
                reranked = _rerank(query, rerank_docs, top_n=min(5, len(lore_candidates)))
                if reranked:
                    ordered = [lore_candidates[i] for i in reranked]
                else:
                    ordered = lore_candidates[:5]
                for c in ordered:
                    text = c["text"]
                    # 长文档 snippet 定位：找到搜索词出现位置，从该位置截取窗口
                    # 避免至冬(81871字)等长文本只返回开头的目录结构，关键原文被截掉
                    best_pos = -1
                    for term in lore_terms:
                        idx = text.find(term)
                        if idx >= 0:
                            best_pos = idx
                            break
                    if best_pos >= 0:
                        start = max(0, best_pos - 120)
                        end = min(len(text), best_pos + 480)
                        snippet = text[start:end].replace('\n', ' ').strip()
                    else:
                        snippet = text[:480].replace('\n', ' ').strip()
                    results.append({
                        "id": f"lore:{c['title']}",
                        "collection": "kb_lore",
                        "document": snippet,
                    })

    # --- 书籍正文搜索 ---
    books = _load_content_json("books")
    book_candidates = []
    for b in books:
        text = b.get("text", "")
        title = b.get("title", "")
        for term in search_terms:
            if _match_all_in(term, title) or _match_all_in(term, text):
                # 优先从正文匹配位置提取片段，标题匹配则取正文开头
                if _match_all_in(term, text):
                    idx = text.find(term.split()[0])
                    start = max(0, idx - 120)
                    end = min(len(text), idx + len(term.split()[0]) + 120)
                    snippet = text[start:end].replace('\n', ' ').strip()
                else:
                    snippet = text[:240].replace('\n', ' ').strip()
                book_candidates.append((b["title"], snippet))
                break
    for i, (title, snippet) in enumerate(book_candidates[:5]):
        results.append({
            "id": f"book:{title}",
            "collection": "kb_books",
            "document": snippet,
        })

    # --- 概念/组织/设定搜索 ---
    # concepts.json 包含 64 个具体概念（教令院、七星、愚人众、神之眼等）
    # 删除 query_concept 工具后，这些数据通过 hybrid_search 的关键词路检索
    concepts = _load_content_json("concepts")
    if concepts:
        concept_candidates = []
        seen_concepts = set()
        for term in search_terms:
            for c in concepts:
                name = c.get("名称", "")
                body = c.get("正文", "")
                sections = c.get("章节", {})
                section_titles = " ".join(sections.keys()) if sections else ""
                full_text = name + " " + body + " " + section_titles
                if _match_all_in(term, full_text):
                    cid = name + body[:40]
                    if cid not in seen_concepts:
                        seen_concepts.add(cid)
                        # 构建摘要：名称 + 正文 / 章节预览
                        info = f"【{name}】（{c.get('类型', '?')}）"
                        if body:
                            info += f"\n{body[:400]}"
                        elif sections:
                            for sec_name in list(sections.keys())[:3]:
                                sec_text = sections[sec_name][:200]
                                if sec_text.strip():
                                    info += f"\n[{sec_name}]: {sec_text}"
                        concept_candidates.append((name, info))
        for name, info in concept_candidates[:3]:
            results.append({
                "id": f"concept:{name}",
                "collection": "kb_concepts",
                "document": info,
            })

    return results


@tool
def hybrid_search(query: str, top_k: int = 10) -> str:
    """混合检索：同时执行关键词匹配和语义搜索，自动融合排序。大多数内容搜索场景的默认工具。
    query: 搜索内容（自然语言描述即可）
    top_k: 返回结果数，默认10"""
    # 关键词路
    kw_docs = _keyword_search_docs(query, top_k=max(top_k, 15))
    # 向量路：排除 kb_quests_bm25（9000字大切片不适合语义搜索）
    vec_docs = []
    if _vector_store is not None:
        vec_raw = _vector_store.search(query, top_k=max(top_k, 15),
                                       exclude=["kb_quests_bm25"], collection=None)
        vec_docs = vec_raw

    # RRF 融合
    merged = _rrf_fusion(kw_docs, vec_docs, k=60)

    # 取 top_k 结果
    top_keys = [key for key, _ in merged[:top_k]]

    # 构建输出：合并关键词和向量两路中命中的结果
    lines = [f"\n===== 混合检索「{query}」({len(top_keys)}条结果) ====="]
    for key in top_keys:
        # 从关键词结果中找
        kw_match = [d for d in kw_docs if _get_doc_key(d) == key]
        vec_match = [d for d in vec_docs if _get_doc_key(d) == key] if vec_docs else []
        tags = []
        if kw_match:
            tags.append("关键词")
        if vec_match:
            tags.append("向量")
        tag_str = "+".join(tags)

        # 优先用关键词结果的完整 snipet
        region = TITLE_REGISTRY.get(key, {}).get("region", "")
        if kw_match:
            doc = kw_match[0]
            doc_id = doc.get("id", key)
            collection = doc.get("collection", "")
            category = doc.get("category", "")
            attrs = []
            if category:
                attrs.append(category)
            if region:
                attrs.append(f"任务地区：{region}")
            attr_str = f"（{'，'.join(attrs)}）" if attrs else ""
            lines.append(f"\n【{key}】{attr_str}({collection}) [{tag_str}]")
            lines.append(f"  {doc['document'][:600]}")
        elif vec_match:
            doc = vec_match[0]
            doc_id = doc.get("id", key)
            collection = doc.get("collection", "")
            score = doc.get("score", 0)
            region_str = f"（任务地区：{region}）" if region else ""
            lines.append(f"\n【{key}】{region_str}({collection}) [{tag_str}] 相似度:{score:.4f}")
            lines.append(f"  {doc['document'][:600]}")

    if not top_keys:
        return f"混合检索未找到与「{query}」相关的内容。"
    return "\n".join(lines)


def llm_invoke_with_retry(messages, max_retries=3, llm_instance=None):
    """带重试的 LLM 调用，处理 API 限流。
    llm_instance: 指定 LLM 实例，不传则用默认 llm。"""
    target_llm = llm_instance if llm_instance is not None else llm
    for attempt in range(max_retries):
        try:
            return target_llm.invoke(messages)
        except Exception as e:
            if "rate" in str(e).lower() or "throttle" in str(e).lower() or "429" in str(e):
                wait = (attempt + 1) * 5
                print(f"  [限流] 等待 {wait}s 后重试...")
                time.sleep(wait)
            elif attempt < max_retries - 1:
                time.sleep(2)
            else:
                raise


rag_memory = None
if RAG_AVAILABLE:
    try:
        rag_memory = GenshinRAGMemory(persist_dir="./conversation_memory")
        print("[初始化] RAG记忆管理器已就绪")
    except Exception as e:
        print(f"[错误] RAG记忆初始化失败: {e}")

# ====== 格式化工具函数 ======

def _format_role_info(role: Dict) -> str:
    """格式化角色信息。剧情数据始终显示，游戏机制数据（稀有度/武器类型）按需显示。"""
    lines = [f"\n{'='*50}"]
    lines.append(f"【{role['角色名称']}】{role.get('称号', '')}")
    lines.append(f"所属: {role.get('所属', '未知')}")
    if role.get('性别'):
        lines.append(f"性别: {role['性别']}")
    if role.get('稀有度'):
        lines.append(f"稀有度: {role['稀有度']}星")
    if role.get('武器类型'):
        lines.append(f"武器类型: {role['武器类型']}")
    if role.get('神之眼'):
        lines.append(f"神之眼: {role['神之眼']}")
    if role.get('命之座'):
        lines.append(f"命之座: {role['命之座']}")
    if role.get('实装版本'):
        lines.append(f"实装版本: {role['实装版本']}")
    if role.get('生日'):
        lines.append(f"生日: {role['生日']}")
    if role.get('身份'):
        lines.append(f"身份: {', '.join(role['身份']) if isinstance(role['身份'], list) else role['身份']}")
    if role.get('TAG'):
        tags = role['TAG'] if isinstance(role['TAG'], list) else role['TAG'].split('、')
        lines.append(f"特性: {'、'.join(tags[:6])}{'...' if len(tags) > 6 else ''}")
    lines.append(f"\n简介: {role.get('简介', '暂无')}")
    stories = role.get('角色故事', {})
    if stories:
        lines.append(f"\n角色故事（共{len(stories)}段）:")
        for sk, sv in stories.items():
            preview = sv[:200].replace('\n', ' ')
            lines.append(f"  [{sk}]: {preview}...")
    if role.get('相关剧情'):
        lines.append(f"\n相关剧情:")
        for p in role['相关剧情']:
            lines.append(f"  · {p}")
    lines.append(f"{'='*50}")
    return "\n".join(lines)


def _format_npc_info(name: str, npc: Dict) -> str:
    """格式化 NPC 信息"""
    lines = [f"\n{'='*50}"]
    lines.append(f"【{name}】（NPC）")
    if npc.get('occupation'):
        lines.append(f"职业: {npc['occupation']}")
    if npc.get('sex'):
        lines.append(f"性别: {npc['sex']}")
    if npc.get('org'):
        lines.append(f"所属组织: {npc['org']}")
    if npc.get('race'):
        lines.append(f"种族: {npc['race']}")
    if npc.get('origin'):
        lines.append(f"出身: {npc['origin']}")
    if npc.get('region'):
        lines.append(f"地区: {npc['region']}")
    if npc.get('version'):
        lines.append(f"实装版本: {npc['version']}")
    if npc.get('system'):
        lines.append(f"系统: {npc['system']}")
    if npc.get('appear_time'):
        lines.append(f"出现时间: {npc['appear_time']}")

    dialogue = npc.get('dialogue', '')
    if dialogue:
        lines.append(f"\n对话与语音（前600字预览）:")
        lines.append(dialogue[:600])

    related = npc.get('related_quests', [])
    if related:
        lines.append(f"\n相关任务:")
        for q in related:
            lines.append(f"  · {q}")

    lines.append(f"\n[系统标记] 此条目为 NPC（非可玩角色）。若用户询问其成长/履历/身份转变/出场经历，请使用 NPC 离散成长检索协议：仅以 NPC 全名检索 hybrid_search，禁止添加「成长/变化/脉络」等抽象词，拿到出场列表后按版本顺序逐篇加载全文。")
    lines.append(f"{'='*50}")
    return "\n".join(lines)


def _format_region_info(region: Dict) -> str:
    lines = [f"\n{'='*50}"]
    lines.append(f"【{region['地区名称']}】")
    lines.append(f"元素: {region.get('元素')}  |  神明: {region.get('神明')}")
    lines.append(f"理念: {region.get('理念')}  |  剧情章节: {region.get('剧情章节')}")
    lines.append(f"主要地点: {', '.join(region.get('主要地点', []))}")
    lines.append(f"\n简介: {region.get('简介', '暂无')}")
    lines.append(f"{'='*50}")
    return "\n".join(lines)


def _format_story_info(arc: Dict) -> str:
    lines = [f"\n{'='*50}"]
    lines.append(f"【{arc['章节名称']}】")
    lines.append(f"所属地区: {arc.get('所属地区')}")
    lines.append(f"主要角色: {', '.join(arc.get('主要角色', []))}")
    lines.append(f"\n剧情概要: {arc.get('剧情概要', '暂无')}")
    if arc.get('关键事件'):
        lines.append(f"\n关键事件:")
        for i, event in enumerate(arc['关键事件'], 1):
            lines.append(f"  {i}. {event}")
    lines.append(f"{'='*50}")
    return "\n".join(lines)


# ====== 内容数据 ======
CONTENT_FILES = {
    "monsters": os.path.join(CONTENT_DIR, "monsters.json"),
    "materials": os.path.join(CONTENT_DIR, "materials.json"),
    "collectibles": os.path.join(CONTENT_DIR, "collectibles.json"),
    "recipes": os.path.join(CONTENT_DIR, "recipes.json"),
    "concepts": os.path.join(CONTENT_DIR, "concepts.json"),
    "foods": os.path.join(CONTENT_DIR, "foods.json"),
    "books": os.path.join(CONTENT_DIR, "books.json"),
}


def _load_content_json(file_key: str) -> List[Dict]:
    path = CONTENT_FILES.get(file_key)
    if not path or not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


# ====== 工具函数（保持不变） ======

@tool
def query_character(name: str) -> str:
    """查询原神角色详细信息。name: 角色名称（如\"胡桃\"、\"钟离\"、\"雷电将军\"）"""
    for role in 角色知识库:
        if name in role.get("角色名称", "") or name in role.get("称号", ""):
            print(f"[工具] 查询角色: {name}")
            return _format_role_info(role)

    # 兜底：查 NPC 数据
    npc = _npcs_data.get(name)
    if npc:
        print(f"[工具] 查询NPC: {name}")
        return _format_npc_info(name, npc)

    return f"未找到角色「{name}」的信息。知识库还在完善中，建议前往 Bilibili Wiki 查看。"


@tool
def query_region(name: str) -> str:
    """查询原神地区（国家）信息。name: 地区名称（如\"蒙德\"、\"璃月\"、\"稻妻\"）"""
    for region in 地区知识库:
        if name in region.get("地区名称", ""):
            print(f"[工具] 查询地区: {name}")
            return _format_region_info(region)
    return f"未找到地区「{name}」的信息。"


@tool
def query_story(arc_name: str) -> str:
    """查询版本主线剧情信息。arc_name: 章节名称关键词（如\"辞行久远之躯\"）"""
    results = []
    for arc in 主线剧情知识库:
        if arc_name in arc.get("章节名称", "") or arc_name in arc.get("所属地区", ""):
            results.append(arc)
    if len(results) == 1:
        print(f"[工具] 查询剧情: {arc_name}")
        return _format_story_info(results[0])
    elif results:
        print(f"[工具] 查询剧情(多条): {arc_name}")
        return "\n\n".join(_format_story_info(a) for a in results)
    return f"未找到与「{arc_name}」相关的剧情。"


@tool
def query_weapon(name: str) -> str:
    """查询武器信息。name: 武器名称（如\"护摩之杖\"、\"雾切之回光\"）"""
    for wpn in 武器知识库:
        if name in wpn.get("武器名称", ""):
            print(f"[工具] 查询武器: {name}")
            star_count = wpn.get('稀有度', 0)
            star_str = '★' * star_count if isinstance(star_count, int) and star_count > 0 else str(star_count)
            lines = [f"\n{'='*50}", f"【{wpn['武器名称']}】{star_str}",
                     f"类型: {wpn.get('武器类型', '未知')}"]
            if wpn.get('副属性'):
                lines.append(f"副属性: {wpn['副属性']}")
            if wpn.get('技能名称'):
                lines.append(f"技能: {wpn['技能名称']}")
            if wpn.get('实装版本'):
                lines.append(f"实装版本: {wpn['实装版本']}")
            lines.append(f"\n简介: {wpn.get('简介', '暂无')}")
            if wpn.get('武器故事'):
                story = wpn['武器故事']
                lines.append(f"\n武器故事:\n{story}")
            lines.append(f"{'='*50}")
            return "\n".join(lines)
    return f"未找到武器「{name}」的信息。"


# 戏称映射：角色名 → 被戏称为该角色传说任务的版本活动
FAKE_LEGEND_QUESTS = {
    "兹白": "奔霄颂玉轮",
    "布伦妮": "幻友绮旅",
    "嘉明": "彩鹞栉春风",
    "菲米尼": "特尔克西的奇幻历险",
    "卡维": "盛典与慧业",
    "夏沃蕾": "蔷薇与铳枪",
    "胡桃": "春曦画桃符",
}

# 部族纪闻映射：角色名 → 对应的部族纪闻章节名
TRIBAL_CHRONICLES = {
    "玛拉妮": "流泉所归之处",
    "基尼奇": "尤潘基的回火",
    "希诺宁": "祈祝福愿，倾告嵴锋",
    "恰斯卡": "花之归尘，羽之将坠",
    "瓦雷莎": "蘑境菌奇",
    "茜特菈莉": "流淌着色彩的回忆",
}


@tool
def query_quest(name: str) -> str:
    """查询传说任务/世界任务信息。name: 任务名称或角色名。
    返回时自动按系列任务（章节名）分组。显示所属角色，区分主角视角与客串出场。"""
    # 获取名字的所有变体（瓦雷莎/瓦蕾莎等异体字问题）
    name_variants = {name}
    if name in ALIAS_MAP:
        name_variants.add(ALIAS_MAP[name])  # 规范名
    for alias, canon in ALIAS_MAP.items():
        if canon in name_variants or alias in name_variants:
            name_variants.add(alias)
            name_variants.add(canon)

    # 戏称映射处理：用户用角色名问"传说任务"，实际是版本活动
    matched_fake = None
    for v in name_variants:
        if v in FAKE_LEGEND_QUESTS:
            matched_fake = FAKE_LEGEND_QUESTS[v]
            break
    if matched_fake:
        activity_results = [q for q in 任务知识库 if matched_fake in q.get("任务名称", "")]
        if activity_results:
            lines = [f"注意：「{name}」没有传说任务，以下是被戏称为「{name}传说任务」的版本活动："]
            for q in activity_results:
                lines.append(f"\n【{q['任务名称']}】版本活动")
                lines.append(f"简介: {q.get('简介', '暂无')}")
            print(f"[工具] 查询任务(戏称映射): {name} → {matched_fake}")
            return "\n".join(lines)

    # 部族纪闻映射：该角色的部族纪闻可能未全部关联角色名，需按章节名补全
    is_tribal = False
    results = []
    seen = set()
    matched_tribal = None
    for v in name_variants:
        if v in TRIBAL_CHRONICLES:
            matched_tribal = TRIBAL_CHRONICLES[v]
            break
    if matched_tribal:
        for q in 任务知识库:
            if q.get("系列任务", "").startswith(matched_tribal):
                results.append(q)
                seen.add(q.get("任务名称", ""))
        is_tribal = True

    for q in 任务知识库:
        if q.get("任务名称") in seen:
            continue
        matched = False
        for v in name_variants:
            if v in q.get("任务名称", "") or v in q.get("关联角色", "") or v in q.get("系列任务", ""):
                matched = True
                break
        if matched:
            results.append(q)
            seen.add(q.get("任务名称", ""))
    if results:
        # 按系列任务（完整"章节,幕名"）分组，区分同一章节下的多幕
        series_groups = {}  # {"章节,幕名": [任务列表]}
        standalone = []
        for q in results:
            series = q.get("系列任务", "")
            if series:
                if series not in series_groups:
                    series_groups[series] = []
                series_groups[series].append(q)
            else:
                standalone.append(q)

        lines = []
        if is_tribal:
            lines.append(f"注意：「{name}」没有专属传说任务，以下为其所在的部族纪闻：")
            lines.append("-" * 40)

        # 补全：同一系列任务下，纳入所有子任务（包括元数据缺失的孤立条目）
        all_series = set(series_groups.keys())
        if all_series:
            for q in 任务知识库:
                s = q.get("系列任务", "")
                if s and s in all_series and q.get("任务名称") not in seen:
                    series_groups[s].append(q)
                    seen.add(q.get("任务名称", ""))

        for series, quests in series_groups.items():
            owner = quests[0].get("所属角色", "")
            is_main = (owner == name)  # 搜索角色等于所属角色=主角视角

            parts = series.split(",")
            chapter = parts[0].strip()
            act_name = parts[1].strip() if len(parts) > 1 else ""
            qtype = quests[0].get("任务类型", "")
            tag = " 【主角】" if is_main else " 【客串出场】"
            lines.append(f"\n【{chapter}】{qtype}{tag}")
            if owner:
                lines.append(f"所属角色: {owner}")
            if act_name:
                lines.append(f"幕: {act_name}")
            lines.append(f"子任务({len(quests)}个): " + " | ".join(q["任务名称"] for q in quests))
            lines.append("-" * 40)

        for q in standalone:
            lines.append(f"\n【{q['任务名称']}】")
            lines.append(f"类型: {q.get('任务类型')}  |  关联角色: {q.get('关联角色')}")
            owner = q.get("所属角色", "")
            if owner:
                lines.append(f"所属角色: {owner}")
            lines.append(f"简介: {q.get('简介', '暂无')}")
            lines.append("-" * 40)

        print(f"[工具] 查询任务: {name}")
        return "\n".join(lines)
    return f"未找到任务「{name}」的信息。"


@tool
def list_characters_by_element(element: str) -> str:
    """按元素类型列出所有角色。element: 元素类型（火/水/风/雷/冰/岩/草）"""
    element_map = {"火": "火", "水": "水", "风": "风", "雷": "雷", "冰": "冰", "岩": "岩", "草": "草"}
    target = element_map.get(element, element)
    matched = [r for r in 角色知识库 if r.get("神之眼") == target]
    if matched:
        print(f"[工具] 按元素列出: {element}")
        lines = [f"\n【{target}元素角色列表】({len(matched)}位)", "-" * 40]
        for r in matched:
            lines.append(f"  {r['角色名称']}（{r.get('称号','')}）| {r.get('武器类型','')} | {r.get('所属','')}")
        return "\n".join(lines)
    return f"知识库中暂无{target}元素角色数据。"


@tool
def list_characters_by_region(region: str) -> str:
    """按所属地区列出所有角色。region: 地区名称（蒙德/璃月/稻妻/须弥/枫丹/纳塔/至冬/其他）"""
    matched = [r for r in 角色知识库 if r.get("所属") == region]
    if matched:
        print(f"[工具] 按地区列出: {region}")
        lines = [f"\n【{region}角色列表】({len(matched)}位)", "-" * 40]
        for r in matched:
            lines.append(f"  {r['角色名称']}（{r.get('称号','')}）| {r.get('神之眼','')} | {r.get('武器类型','')} | {r.get('稀有度','')}星")
        return "\n".join(lines)
    return f"知识库中暂无{region}角色数据。"


@tool
def list_characters_by_weapon(weapon_type: str) -> str:
    """按武器类型列出所有角色。weapon_type: 武器类型（单手剑/双手剑/长柄武器/法器/弓）"""
    matched = [r for r in 角色知识库 if r.get("武器类型") == weapon_type]
    if matched:
        print(f"[工具] 按武器列出: {weapon_type}")
        lines = [f"\n【{weapon_type}角色列表】({len(matched)}位)", "-" * 40]
        for r in matched:
            lines.append(f"  {r['角色名称']}（{r.get('称号','')}）| {r.get('神之眼','')} | {r.get('所属','')} | {r.get('稀有度','')}星")
        return "\n".join(lines)
    return f"知识库中暂无{weapon_type}角色数据。"


@tool
def list_characters_by_rarity(rarity: str) -> str:
    """按稀有度列出所有角色。rarity: 稀有度（4或5）"""
    try:
        target = int(rarity)
    except ValueError:
        return f"稀有度参数无效：{rarity}，请传入 4 或 5。"
    matched = [r for r in 角色知识库 if r.get("稀有度") == target]
    if matched:
        print(f"[工具] 按稀有度列出: {rarity}星")
        lines = [f"\n【{rarity}星角色列表】({len(matched)}位)", "-" * 40]
        for r in matched:
            lines.append(f"  {r['角色名称']}（{r.get('称号','')}）| {r.get('神之眼','')} | {r.get('武器类型','')} | {r.get('所属','')}")
        return "\n".join(lines)
    return f"知识库中暂无{rarity}星角色数据。"


# ====== 全角色任务目录缓存 ======
_quest_series_cache: Optional[str] = None
_quest_series_cache_time: float = 0.0
_QUEST_SERIES_CACHE_TTL = 86400  # 24 小时


@tool
def list_all_quest_series() -> str:
    """列出知识库中所有有"系列任务"的剧情条目，按角色分组，显示章节/幕/子任务结构。
    仅返回层级骨架（不含剧情文本、任务描述、奖励等详情），token 消耗极低。
    使用场景：用户要求"列出全部传说任务"、"每个角色有几章任务"、"全角色任务目录"等总览类查询。
    详情查询请使用 query_quest(角色名) 获取单个角色的子任务列表。"""
    global _quest_series_cache, _quest_series_cache_time
    import time as _time
    now = _time.time()
    if _quest_series_cache and (now - _quest_series_cache_time) < _QUEST_SERIES_CACHE_TTL:
        print("[工具] 全角色任务目录（缓存命中）")
        return _quest_series_cache

    _emit_progress("tool_start", {"tool": "list_all_quest_series", "message": "正在生成全角色任务目录..."})

    # 按所属角色分组：角色名 → {系列任务: [子任务列表]}
    char_map: dict = {}
    standalone_series = []  # 无所属角色的系列任务

    for q in 任务知识库:
        series = q.get("系列任务", "")
        if not series:
            continue
        # 只返回传说任务，活动剧情/其他类型不在此列出
        if q.get("任务类型") != "传说任务":
            continue
        owner = q.get("所属角色", "")
        task_name = q.get("任务名称", "")
        task_type = q.get("任务类型", "")

        if owner:
            if owner not in char_map:
                char_map[owner] = {}
            if series not in char_map[owner]:
                char_map[owner][series] = []
            char_map[owner][series].append(task_name)
        else:
            # 无所属角色的系列，单独收集
            found = False
            for item in standalone_series:
                if item["series"] == series:
                    item["tasks"].append(task_name)
                    found = True
                    break
            if not found:
                standalone_series.append({
                    "series": series,
                    "type": task_type,
                    "tasks": [task_name],
                })

    lines = []
    lines.append(f"===== 全角色任务目录 =====\n")

    # 有角色的传说任务（按角色名排序）
    for owner in sorted(char_map.keys()):
        series_map = char_map[owner]
        total_tasks = sum(len(v) for v in series_map.values())
        lines.append(f"【{owner}】（{len(series_map)}章，{total_tasks}个子任务）")
        for series, tasks in series_map.items():
            parts = series.split(",")
            chapter = parts[0].strip()
            act = parts[1].strip() if len(parts) > 1 else "—"
            lines.append(f"  {chapter} · {act}（{len(tasks)}个子任务）")
            for t in tasks:
                lines.append(f"    - {t}")
        lines.append("")

    # 无角色的活动/魔神任务
    if standalone_series:
        lines.append(f"--- 无专属角色的系列任务（{len(standalone_series)}个） ---\n")
        for item in standalone_series:
            parts = item["series"].split(",")
            chapter = parts[0].strip()
            act = parts[1].strip() if len(parts) > 1 else "—"
            lines.append(f"【{chapter}】{item['type']} · {act}（{len(item['tasks'])}个子任务）")
            for t in item["tasks"]:
                lines.append(f"  - {t}")
            lines.append("")

    lines.append(f"共 {len(char_map)} 位角色，{sum(len(v) for v in char_map.values())} 章。")
    lines.append("如需查看任意角色的子任务详情，请使用 query_quest(角色名)。")

    result = "\n".join(lines)
    _quest_series_cache = result
    _quest_series_cache_time = now

    print("[工具] 全角色任务目录（已生成）")
    return result


# ====== 各类目录缓存（统一 TTL 24h） ======
_books_cache: Optional[str] = None
_books_cache_time: float = 0.0
_lore_cache: Optional[str] = None
_lore_cache_time: float = 0.0
_weapon_artifact_cache: Optional[str] = None
_weapon_artifact_cache_time: float = 0.0
_items_cache: Optional[str] = None
_items_cache_time: float = 0.0
_DIR_CACHE_TTL = 86400


def _cache_get(cache_var: Optional[str], cache_time: float) -> Optional[str]:
    import time as _time
    if cache_var and (_time.time() - cache_time) < _DIR_CACHE_TTL:
        return cache_var
    return None


@tool
def list_all_books() -> str:
    """列出知识库中收录的所有游戏内书籍。
    返回书名、作者、体裁、卷数，不含书籍正文。
    使用场景：用户要求"有哪些书"、"收录了哪些书籍"、"书籍目录"等浏览类查询。
    查看具体书籍内容请使用 load_book_content(书名)。"""
    global _books_cache, _books_cache_time
    cached = _cache_get(_books_cache, _books_cache_time)
    if cached:
        print("[工具] 书籍目录（缓存命中）")
        return cached

    _emit_progress("tool_start", {"tool": "list_all_books", "message": "正在生成书籍目录..."})

    books = _load_content_json("books")
    if not books:
        return "知识库中暂无书籍数据。"

    lines = [f"===== 收录书籍目录（{len(books)} 本）=====\n"]
    for b in books:
        meta = b.get("metadata", {})
        title = b.get("title", meta.get("书籍名", "?"))
        author = meta.get("作者", "?")
        genre = meta.get("体裁", "?")
        vols = meta.get("卷数", "?")
        lines.append(f"  《{title}》 | 作者:{author} | 体裁:{genre} | {vols}")

    lines.append(f"\n共 {len(books)} 本书。查看内容: load_book_content(书名)。")

    import time as _time
    result = "\n".join(lines)
    _books_cache = result
    _books_cache_time = _time.time()
    print("[工具] 书籍目录（已生成）")
    return result


@tool
def list_all_lore_entries() -> str:
    """列出知识库中所有世界观条目：地区（8个）和概念（约64个）。
    返回名称和类型，不含正文。
    使用场景：用户要求"有哪些世界观概念"、"收录了哪些地区"、"概念目录"等浏览类查询。
    查看具体内容请使用 query_region() 或 query_concept()。"""
    global _lore_cache, _lore_cache_time
    cached = _cache_get(_lore_cache, _lore_cache_time)
    if cached:
        print("[工具] 世界观目录（缓存命中）")
        return cached

    _emit_progress("tool_start", {"tool": "list_all_lore_entries", "message": "正在生成世界观目录..."})

    lines = [f"===== 世界观条目目录 =====\n"]

    # 地区
    lines.append(f"【地区】（{len(地区知识库)} 个）")
    for r in 地区知识库:
        lines.append(f"  {r['地区名称']} | 元素:{r.get('元素','?')} | 神明:{r.get('神明','?')} | 理念:{r.get('理念','?')}")

    # 概念
    concepts = _load_content_json("concepts")
    lines.append(f"\n【概念】（{len(concepts)} 个）")
    for c in concepts:
        name = c.get("名称", "?")
        ctype = c.get("类型", "?")
        lines.append(f"  {name}（{ctype}）")

    lines.append(f"\n共 {len(地区知识库)} 个地区 + {len(concepts)} 个概念。")
    lines.append("详情查询: query_region() / query_concept()。")

    import time as _time
    result = "\n".join(lines)
    _lore_cache = result
    _lore_cache_time = _time.time()
    print("[工具] 世界观目录（已生成）")
    return result


@tool
def list_all_weapons_and_artifacts() -> str:
    """列出知识库中所有武器（约236把）和圣遗物套装（约64套）。
    返回名称、类型和稀有度，不含武器故事/圣遗物文本。
    使用场景：用户要求"有哪些武器"、"列出所有五星武器"、"有哪些圣遗物"等浏览类查询。
    查看详情请使用 query_weapon() 或 query_artifact()。"""
    global _weapon_artifact_cache, _weapon_artifact_cache_time
    cached = _cache_get(_weapon_artifact_cache, _weapon_artifact_cache_time)
    if cached:
        print("[工具] 武器圣遗物目录（缓存命中）")
        return cached

    _emit_progress("tool_start", {"tool": "list_all_weapons_and_artifacts", "message": "正在生成武器/圣遗物目录..."})

    lines = [f"===== 武器 & 圣遗物目录 =====\n"]

    lines.append(f"【武器】（{len(武器知识库)} 把）")
    by_type = {}
    for w in 武器知识库:
        wt = w.get("武器类型", "其他")
        if wt not in by_type:
            by_type[wt] = []
        by_type[wt].append(f"{w['武器名称']}（{w.get('稀有度','?')}星）")
    for wt in sorted(by_type.keys()):
        lines.append(f"  [{wt}] " + " | ".join(by_type[wt]))

    lines.append(f"\n【圣遗物】（{len(圣遗物知识库)} 套）")
    for a in 圣遗物知识库:
        lines.append(f"  {a['圣遗物名称']}（{a.get('稀有度','?')}星）")

    lines.append(f"\n共 {len(武器知识库)} 把武器 + {len(圣遗物知识库)} 套圣遗物。")
    lines.append("详情查询: query_weapon() / query_artifact()。")

    import time as _time
    result = "\n".join(lines)
    _weapon_artifact_cache = result
    _weapon_artifact_cache_time = _time.time()
    print("[工具] 武器圣遗物目录（已生成）")
    return result


@tool
def list_all_game_items() -> str:
    """列出知识库中所有游戏物品：怪物、食谱、食物、材料、采集物。
    按类别分组，返回名称和简要属性，不含正文。
    使用场景：用户要求"有哪些怪物"、"有哪些食谱"、"列出了哪些材料"等浏览类查询。
    查看详情请使用对应的 query_ 工具。"""
    global _items_cache, _items_cache_time
    cached = _cache_get(_items_cache, _items_cache_time)
    if cached:
        print("[工具] 游戏物品目录（缓存命中）")
        return cached

    _emit_progress("tool_start", {"tool": "list_all_game_items", "message": "正在生成游戏物品目录..."})

    lines = [f"===== 游戏物品目录 =====\n"]
    total = 0

    # 怪物
    monsters = _load_content_json("monsters")
    lines.append(f"【怪物】（{len(monsters)} 个）")
    by_class = {}
    for m in monsters:
        mc = m.get("怪物分类", "其他")
        if mc not in by_class:
            by_class[mc] = []
        by_class[mc].append(m.get("名称", "?"))
    for mc in sorted(by_class.keys()):
        items = by_class[mc]
        lines.append(f"  [{mc}] {', '.join(items[:20])}{' ...' if len(items) > 20 else ''}")
    total += len(monsters)

    # 食谱
    recipes = _load_content_json("recipes")
    lines.append(f"\n【食谱】（{len(recipes)} 个）")
    for r in recipes:
        lines.append(f"  {r['名称']} | {r.get('类型','?')} | {r.get('稀有度','?')}星")
    total += len(recipes)

    # 食物
    foods = _load_content_json("foods")
    lines.append(f"\n【食物】（{len(foods)} 个）")
    by_cat = {}
    for f in foods:
        cat = f.get("类别", "其他")
        if cat not in by_cat:
            by_cat[cat] = []
        by_cat[cat].append(f"{f.get('名称','?')}({f.get('稀有度','?')}星)")
    for cat in sorted(by_cat.keys()):
        items = by_cat[cat]
        lines.append(f"  [{cat}] {', '.join(items[:15])}{' ...' if len(items) > 15 else ''}")
    total += len(foods)

    # 材料
    materials = _load_content_json("materials")
    lines.append(f"\n【材料】（{len(materials)} 个）")
    by_mat_type = {}
    for mat in materials:
        mt = mat.get("类型", "其他")
        if mt not in by_mat_type:
            by_mat_type[mt] = []
        by_mat_type[mt].append(f"{mat.get('名称','?')}({mat.get('稀有度','?')}星)")
    for mt in sorted(by_mat_type.keys()):
        items = by_mat_type[mt]
        lines.append(f"  [{mt}] {', '.join(items[:20])}{' ...' if len(items) > 20 else ''}")
    total += len(materials)

    # 采集物
    collectibles = _load_content_json("collectibles")
    lines.append(f"\n【采集物】（{len(collectibles)} 个）")
    for c in collectibles:
        lines.append(f"  {c['名称']} | {c.get('类型','?')} | {c.get('分布地区','?')}")
    total += len(collectibles)

    lines.append(f"\n共 {total} 个游戏物品。详情查询: query_monster() / query_recipe() / query_food() / query_material() / query_collectible()。")

    import time as _time
    result = "\n".join(lines)
    _items_cache = result
    _items_cache_time = _time.time()
    print("[工具] 游戏物品目录（已生成）")
    return result


def _normalize_for_match(s: str) -> str:
    """去除装饰性标点，用于文本匹配。
    中英文书名号、引号、括号等纯装饰性标点被移除，保留语义内容。
    避免"天理的维系者"无法匹配"「天理」的维系者"这类问题。"""
    for ch in "「」『』""''（）()【】《》":
        s = s.replace(ch, "")
    return s


def _match_all_in(query: str, text: str) -> bool:
    """检查 text 中是否包含 query 的至少一半词（空格分词）。
    宽松 AND 逻辑：单关键词退化为普通子串匹配；多关键词要求至少 ceil(N/2) 个词在 text 中。
    避免 Plan Agent 构造搜索词时加入多余词导致漏召回。
    匹配前对 query 和 text 做标点归一化。"""
    words = query.split()
    if not words:
        return False
    normalized_text = _normalize_for_match(text)
    matched = sum(1 for w in words if _normalize_for_match(w) in normalized_text)
    threshold = max(1, (len(words) + 1) // 2)  # 至少一半词命中（向上取整）
    return matched >= threshold


def search_all(query: str) -> str:
    """全局搜索原神知识库，在角色、地区、剧情、武器、任务、概念、怪物、材料、书籍、食谱、食物、采集物、任务内容中模糊匹配。query: 搜索关键词"""
    all_results = []
    # 角色
    for role in 角色知识库:
        if _match_all_in(query, role.get("角色名称", "")) or _match_all_in(query, role.get("称号", "")) or _match_all_in(query, str(role.get("身份", []))):
            all_results.append(("角色", _format_role_info(role)))
    # 地区
    for region in 地区知识库:
        if _match_all_in(query, region.get("地区名称", "")) or _match_all_in(query, region.get("神明", "")):
            all_results.append(("地区", _format_region_info(region)))
    # 主线剧情
    for arc in 主线剧情知识库:
        if _match_all_in(query, arc.get("章节名称", "")) or _match_all_in(query, arc.get("所属地区", "")):
            all_results.append(("剧情", _format_story_info(arc)))
    # 武器
    for wpn in 武器知识库:
        if _match_all_in(query, wpn.get("武器名称", "")) or _match_all_in(query, wpn.get("关联角色", "")):
            all_results.append(("武器", f"\n【{wpn['武器名称']}】{'★'*wpn['稀有度']} {wpn.get('武器类型')}"))
    # 圣遗物
    for art in 圣遗物知识库:
        if _match_all_in(query, art.get("圣遗物名称", "")):
            all_results.append(("圣遗物", f"\n【{art['圣遗物名称']}】{art.get('稀有度','')}星 | 两件套: {art.get('两件套效果','?')[:60]}"))
    # 任务元数据
    for q in 任务知识库:
        if _match_all_in(query, q.get("任务名称", "")) or _match_all_in(query, q.get("关联角色", "")):
            all_results.append(("任务", f"\n【{q['任务名称']}】{q.get('任务类型','')} | 关联: {q.get('关联角色','')} | {q.get('简介','')[:100]}"))
    # 概念
    concepts = _load_content_json("concepts")
    for c in concepts:
        name = c.get("名称", "")
        text_body = c.get("正文", "") + str(c.get("章节", {}))
        if _match_all_in(query, name) or _match_all_in(query, text_body):
            preview = text_body[:500].replace('\n', ' ')
            all_results.append(("概念", f"\n【{name}】（{c.get('类型','')}）\n  {preview}..."))
    # 怪物
    monsters = _load_content_json("monsters")
    for m in monsters:
        if _match_all_in(query, m.get("名称", "")) or _match_all_in(query, m.get("别称", "")):
            all_results.append(("怪物", f"\n【{m.get('名称','')}】{m.get('怪物类型','')} | {m.get('元素属性','')}"))
    # 材料
    materials = _load_content_json("materials")
    for mat in materials:
        if _match_all_in(query, mat.get("名称", "")):
            all_results.append(("材料", f"\n【{mat.get('名称','')}】{mat.get('类型','')} | {mat.get('用途','')[:100]}"))
    # 食谱
    recipes = _load_content_json("recipes")
    for r in recipes:
        if _match_all_in(query, r.get("名称", "")):
            all_results.append(("食谱", f"\n【{r.get('名称','')}】{r.get('类型','')} | {r.get('效果','')[:80]}"))
    # 食物
    foods = _load_content_json("foods")
    for fd in foods:
        if _match_all_in(query, fd.get("名称", "")):
            all_results.append(("食物", f"\n【{fd.get('名称','')}】{fd.get('类型','')} | {fd.get('效果','')[:80]}"))
    # 采集物
    collectibles = _load_content_json("collectibles")
    for col in collectibles:
        if _match_all_in(query, col.get("名称", "")):
            all_results.append(("采集物", f"\n【{col.get('名称','')}】"))
    # 书籍
    books = _load_content_json("books")
    for b in books:
        if _match_all_in(query, b.get("title", "")) or _match_all_in(query, b.get("text", "")):
            vol_count = b.get("metadata", {}).get("卷数", "")
            all_results.append(("书籍", f"\n【{b['title']}】（{vol_count}）| 来源: {b.get('source','')}"))

    # 任务内容全文搜索 - 收集候选后用 Reranker 重排序
    # 术语别名扩展：同一实体可能有多种称呼，都作为搜索词尝试
    search_terms = _expand_query_with_aliases(query)
    quest_candidates = []  # (title, category, expanded_snippet, result_string)
    seen_candidates = set()  # 去重: (title, category)
    for filename in os.listdir(CONTENT_DIR):
        if not filename.startswith("quests_") or not filename.endswith(".json"):
            continue
        if filename == "quests_processed.json":
            continue
        if len(quest_candidates) >= 200:  # 激进上限，防止极端情况
            break
        try:
            with open(os.path.join(CONTENT_DIR, filename), "r", encoding="utf-8") as f:
                quests = json.load(f)
        except Exception:
            continue
        if not isinstance(quests, list):
            continue
        file_count = 0
        for q in quests:
            if file_count >= 5 or len(quest_candidates) >= 200:
                break
            text = q.get("text", "")
            # 用所有搜索词（含别名）尝试匹配
            matched_term = None
            for term in search_terms:
                if _match_all_in(term, text):
                    matched_term = term
                    break
            if matched_term:
                # 用匹配到的词定位片段
                first_word = matched_term.split()[0]
                idx = text.find(first_word)
                start = max(0, idx - 120)
                end = min(len(text), idx + len(matched_term) + 120)
                snippet = text[start:end].replace('\n', ' ').strip()
                category = q.get('category', '')
                # 去重
                dedup_key = (q['title'], category, snippet[:60])
                if dedup_key not in seen_candidates:
                    seen_candidates.add(dedup_key)
                    result_str = f"\n【{q['title']}】（{category}）\n  匹配片段: ...{snippet}..."
                    quest_candidates.append((q['title'], category, snippet, result_str))
                    file_count += 1

    # Reranker 重排序：带标题上下文，帮助区分不同意图
    if quest_candidates:
        rerank_docs = [f"【{c[0]}】{c[2]}" for c in quest_candidates]
        # 用原始 query 做 Rerank（保持语义精度）
        reranked = _rerank(query, rerank_docs, top_n=10)
        if reranked:
            for idx in reranked:
                all_results.append(("任务内容", quest_candidates[idx][3]))
        else:
            for c in quest_candidates[:10]:
                all_results.append(("任务内容", c[3]))

    if not all_results:
        return f"未找到与「{query}」相关的任何内容。"

    print(f"[工具] 全局搜索: {query} -> {len(all_results)}条结果")
    lines = [f"\n===== 搜索「{query}」({len(all_results)}条结果) ====="]
    for cat, text in all_results:
        lines.append(text)
    return "\n".join(lines)


@tool
def search_activity(keyword: str, activity_name: str = "") -> str:
    """搜索活动剧情中的对话内容。当用户提到具体活动名（如\"风花的呼吸\"、\"海灯节\"）时，优先用此工具代替 search_all。
    keyword: 要在活动剧情中搜索的关键词（如角色名、概念、台词片段）
    activity_name: 活动名称（可选）。提供后只搜索该活动相关页面；不提供则搜索所有活动类内容。"""
    results = []
    # 收集所有 quests 文件
    quest_files = [f for f in os.listdir(CONTENT_DIR) if f.startswith("quests_") and f.endswith(".json")]
    # 活动文件优先排在前面
    quest_files.sort(key=lambda f: (0 if "活动" in f else 1, f))

    search_terms = _expand_query_with_aliases(keyword)

    for filename in quest_files:
        try:
            with open(os.path.join(CONTENT_DIR, filename), "r", encoding="utf-8") as f:
                quests = json.load(f)
        except Exception:
            continue
        if not isinstance(quests, list):
            continue

        for q in quests:
            text = q.get("text", "")
            # 用所有搜索词（含别名）尝试匹配
            matched_term = None
            for term in search_terms:
                if _match_all_in(term, text):
                    matched_term = term
                    break
            if not matched_term:
                continue

            # 如果指定了活动名，检查该页面是否属于此活动
            if activity_name:
                meta = q.get("metadata", {})
                title = q.get("title", "")
                category = q.get("category", "")
                # 在标题、元数据各字段中查找活动名
                meta_text = json.dumps(meta, ensure_ascii=False)
                if activity_name not in title and activity_name not in meta_text and activity_name not in category:
                    continue

            # 用匹配到的词定位片段
            first_kw = matched_term.split()[0]
            idx = text.find(first_kw)
            start = max(0, idx - 80)
            end = min(len(text), idx + len(matched_term) + 120)
            snippet = text[start:end].replace('\n', ' ').strip()
            results.append({
                "title": q["title"],
                "category": q.get("category", ""),
                "snippet": snippet,
            })

    if not results:
        hint = f"（限定活动「{activity_name}」）" if activity_name else ""
        return f"未在活动剧情中找到与「{keyword}」相关的内容。{hint}"

    print(f"[工具] 活动搜索: {keyword}" + (f" @ {activity_name}" if activity_name else "") + f" -> {len(results)}条")

    # Reranker 重排序
    rerank_docs = [f"【{r['title']}】{r['snippet']}" for r in results]
    reranked = _rerank(keyword, rerank_docs, top_n=10)
    if reranked:
        ordered = [results[i] for i in reranked]
    else:
        ordered = results[:10]

    lines = [f"\n===== 活动剧情搜索「{keyword}」" + (f"（{activity_name}）" if activity_name else "") + f" ({len(results)}条候选，取前{len(ordered)}条) ====="]
    for r in ordered:
        lines.append(f"\n【{r['title']}】（{r['category']}）\n  片段: ...{r['snippet']}...")
    return "\n".join(lines)



def search_lore(keyword: str) -> str:
    """专门搜索世界观设定（深渊本质、天理、降临者、世界树等抽象概念）。
    当用户问及\"XX的本质\"、\"XX的定义\"、\"OO是什么概念\"等宏观世界观问题时优先使用此工具。
    keyword: 要搜索的关键词（如\"深渊\"、\"天理\"、\"降临者\"）
    """
    lore_path = os.path.join(CONTENT_DIR, "lore.json")
    if not os.path.exists(lore_path):
        return "世界观设定数据(lore.json)尚未构建，请先用 search_all。"
    try:
        with open(lore_path, "r", encoding="utf-8") as f:
            lore = json.load(f)
    except Exception:
        return "世界观设定数据读取失败。"

    if not lore:
        return "世界观设定数据为空。"

    # 直接搜索匹配 + 自动词根退化
    # 对于 3 字及以上的关键词，同时搜原始词和去掉末字的词根
    # 例如"降临者" → 也搜"降临"（雷内原文用的是"降临"而非"降临者"）
    search_terms = [keyword]
    if len(keyword) >= 3:
        search_terms.append(keyword[:-1])
    if len(keyword) >= 4:
        search_terms.append(keyword[:-2])  # "原初之人" → 也搜"原初之"、"原初"

    candidates = []
    seen_ids = set()
    for term in search_terms:
        for entry in lore:
            if term in entry["text"]:
                eid = entry["title"] + entry["text"][:40]
                if eid not in seen_ids:
                    seen_ids.add(eid)
                    candidates.append(entry)

    if not candidates:
        return f"在世界观设定中未找到与「{keyword}」相关的内容。"

    fallback_info = "" if len(search_terms) == 1 else f"（含词根退化「{'、'.join(search_terms[1:])}」）"
    print(f"[工具] 世界观搜索: {keyword} -> {len(candidates)}条候选{fallback_info}")

    # Reranker 重排序
    rerank_docs = [f"【{c['title']}】{c['text']}" for c in candidates]
    reranked = _rerank(keyword, rerank_docs, top_n=8)
    if reranked:
        ordered = [candidates[i] for i in reranked]
    else:
        ordered = candidates[:8]

    lines = [f"\n===== 世界观设定「{keyword}」({len(candidates)}条候选，取前{len(ordered)}条) ====="]
    for c in ordered:
        lines.append(f"\n【{c['title']}】（{c['source']}）\n  {c['text']}")
    return "\n".join(lines)


@tool
def load_book_content(book_name: str, query: str = "") -> str:
    """加载指定书籍的完整文本。book_name: 书籍名称。query: 可选，传入后按关键词定位返回上下文片段（最多3个，各500字）。"""
    content_file = os.path.join(CONTENT_DIR, "books.json")
    try:
        with open(content_file, "r", encoding="utf-8") as f:
            books = json.load(f)
    except Exception:
        return "书籍数据文件未找到。"
    normalized_book = _normalize_for_match(book_name)
    # 双向匹配：支持 "提瓦特游览指南·蒙德篇" 匹配到 "提瓦特游览指南"（书名作为查询的子串）
    matches = [b for b in books if normalized_book in _normalize_for_match(b["title"])
               or _normalize_for_match(b["title"]) in normalized_book]
    if not matches:
        return f"未找到书籍「{book_name}」。"
    best = max(matches, key=lambda b: len(b["text"]))
    text = best["text"]
    print(f"[工具] 加载书籍: {best['title']} ({len(text)}字)")

    if query and query.strip():
        # 关键词定位：找到 query 在书中的位置，返回上下文片段
        keyword = query.strip()
        words = keyword.split()
        first_word = words[0]
        snippets = []
        idx = 0
        while True:
            pos = text.find(first_word, idx)
            if pos == -1:
                break
            ctx_start = max(0, pos - 500)
            ctx_end = min(len(text), pos + 500)
            ctx = text[ctx_start:ctx_end]
            if all(_normalize_for_match(w) in _normalize_for_match(ctx) for w in words):
                snippets.append(ctx.replace('\n', ' '))
            idx = pos + 1
            if len(snippets) >= 3:  # 最多3个片段
                break
        if snippets:
            print(f"  [关键词定位] \"{query}\" -> {len(snippets)} 个片段")
            result = f"\n【{best['title']}】（共{len(text)}字）\n[关键词定位: \"{query}\", {len(snippets)}个片段]\n"
            result += "\n---\n".join(snippets)
            return result
        else:
            return f"\n【{best['title']}】\n[关键词\"{query}\"未在书中找到，返回开头]\n{text[:3000]}"

    # 附加元数据摘要
    meta = best.get("metadata", {})
    meta_parts = []
    if meta.get("体裁"):
        meta_parts.append(f"体裁: {meta['体裁']}")
    if meta.get("卷数"):
        meta_parts.append(f"卷数: {meta['卷数']}")
    if meta.get("实装版本"):
        meta_parts.append(f"版本: {meta['实装版本']}")
    if meta.get("作者"):
        meta_parts.append(f"作者: {meta['作者']}")
    else:
        meta_parts.append("作者: 游戏内未提及")
    meta_line = f"\n[元数据] {', '.join(meta_parts)}" if meta_parts else ""

    preview = text[:3000]
    more = f"\n...（共{len(text)}字）" if len(text) > 3000 else ""
    return f"\n【{best['title']}】\n{preview}{more}{meta_line}"


# ====== BM25 检索引擎 ======

class SimpleBM25:
    """简易 BM25 实现，用于在长任务切片中检索相关原文片段。
    使用二字词（bigram）分词，适合中文文本。"""

    def __init__(self, chunks: List[str]):
        self.chunks = chunks
        self.N = len(chunks)
        self.avgdl = sum(len(c) for c in chunks) / self.N if self.N > 0 else 1
        # 构建 DF（文档频率）
        df = Counter()
        for chunk in chunks:
            for t in set(self._tokenize(chunk)):
                df[t] += 1
        self.df = df

    def _tokenize(self, text: str) -> List[str]:
        """二字词分词"""
        tokens = []
        for i in range(len(text) - 1):
            tokens.append(text[i:i + 2])
        return tokens

    def search(self, query: str, top_k: int = 3) -> List[str]:
        """检索最相关的 top_k 个切片"""
        q_tokens = self._tokenize(query)
        scores = []
        for chunk in self.chunks:
            d_tokens = self._tokenize(chunk)
            tf = Counter(d_tokens)
            score = 0.0
            doc_len = len(d_tokens)
            for t in set(q_tokens):
                if t not in self.df:
                    continue
                idf = math.log(1 + (self.N - self.df[t] + 0.5) / (self.df[t] + 0.5))
                tf_t = tf.get(t, 0)
                k1, b = 1.2, 0.75
                score += idf * (tf_t * (k1 + 1)) / (tf_t + k1 * (1 - b + b * doc_len / self.avgdl))
            scores.append((chunk, score))
        scores.sort(key=lambda x: x[1], reverse=True)
        return [c for c, s in scores[:top_k] if s > 0]


# ====== 幕名 → 子任务名映射 ======
# 从 Bilibili Wiki 爬取的魔神任务 / 间章 / 空月之歌 幕→子任务映射
# 格式：幕名 → [子任务标题列表]，子任务标题与 content_data/quests_*.json 中的 title 字段一一对应
ACT_TO_QUESTS: Dict[str, List[str]] = {
    # 序章 捕风的异乡人
    "捕风的异乡人": ["鸟瞰风物", "异常的权柄", "林间相会", "随风而来的骑士", "与轻风同行", "自由之都", "龙灾", "西风骑士团", "昔日的风", "骑士的现场教习", "书页里的电火花"],
    "为了没有眼泪的明天": ["阴影下的蒙德", "不期而遇", "那个绿色的家伙", "听凭风引", "温迪的计划", "温迪的新计划", "逃亡", "幕后谈话", "追逐暗影", "至宝的现状", "遗落之泪", "藏匿之泪", "被夺之泪", "澄净之泪", "与巨龙重逢"],
    "巨龙与自由之歌": ["深渊法师", "障壁", "无人之家", "导光仪式", "在终曲之前", "为了青色的身影", "尾声，风停之后", "尾声的尾声"],
    # 第一章 辞行久远之躯
    "浮世浮生千岩间": ["请仙", "惊变", "望舒", "叠山", "留云", "返尘"],
    "辞行久远之躯": ["往生", "指月", "传香", "壶天", "市井", "归终（任务）", "邀约"],
    "迫近的客星": ["浮城", "玑衡", "孤芳", "离心", "回天", "送仙"],
    "我们终将重逢": ["非自愿的祭献", "无信者的使徒", "不荣誉的试炼", "有隔阂的魂灵"],
    # 第二章 千手百眼，天下人间
    "振袖秋风问红叶": ["冲破雷暴之法", "南十字武斗会", "一路随风"],
    "不动鸣神，恒常乐土": ["起航之日", "异乡人的忏悔录", "离岛逃离计划", "三个心愿", "无意义的等待的意义", "愿仁义之人被仁义以待", "剑道家的道路铺满剑道梦的碎片", "于狱中绽放之花"],
    "无念无想，泡影断灭": ["在审判的雷鸣声中", "以反抗之人的名义"],
    "千手百眼，天下人间": ["剑与鱼与反抗者", "渴求神明注视之人", "邪眼", "眷属的践行", "定罪公文", "愚忠与愚勇", "御前决斗", "千手百眼", "愿望"],
    "回响渊底的安魂曲": ["渊底不期的再会", "被守护者的灵柩", "因提瓦特的记忆", "黑蛇骑士的荣光"],
    # 第三章 虚空劫灰往世书
    "穿越烟帷与暗林": ["林中遇变", "疗养观察", "痼疾", "缄默的求知者", "智慧之神的踪影", "失物匿于繁华", "近在咫尺的目标"],
    "千朵玫瑰带来的黎明": ["终将到来的花神诞祭", "已然来临的花神诞祭", "流转存续的花神诞祭", "轮回意志的花神诞祭", "因果命运的花神诞祭", "空幻回响的花神诞祭", "终将结束的花神诞祭"],
    "迷梦与空幻与欺骗": ["如凯旋的英雄一般", "来自某位「神明」的凝视", "剑拔弩张四人众"],
    "赤土之王与三朝圣者": ["失踪的守村人", "魔鳞病医院的哭声", "热沙中的秘密"],
    "虚空鼓动，劫火高扬": ["行于黎明前夜幕里", "如临神之畔", "识藏日", "意识之舟所至之处", "请饮下祝胜之酒"],
    "卡利贝尔": ["如命运般的相逢", "嘲弄命运的资格", "命运尽头的垂泪者", "既已写下的命运"],
    # 第四章 罪人舞步旋
    "白露与黑潮的序诗": ["独舞者的序幕", "细雨眷恋之城", "聚光灯下谎言成影"],
    "仿若无因飘落的轻雨": ["如同昔日的微茫月明", "真相流逝于雨后", "当一切回归于水"],
    "向深水中的晨星": ["锋芒难掩的茶会", "梅洛彼得堡", "匿于日常之禁忌", "深海的迷路者"],
    "谕示胎动的终焉之刻": ["探入水底迷雾", "真相隐于影中", "守秘者与禁区", "灾厄的脚步", "片刻安宁"],
    "罪人舞步旋": ["怒涛之灾", "相见亦是离别", "狩猎者，预见者", "审判日", "黑潮与白露的歌剧", "终幕礼"],
    "睡前故事": ["积存已久的委托", "不应存在的记忆", "以世界之格的诉说"],
    # 第五章 炽烈的还魂诗
    "荣花与炎日之途": ["纳塔！新的旅程", "归火圣夜巡礼", "温泉之乡"],
    "黑石湮落白石下": ["抉择", "古名寻回之旅", "生命的回响", "坠入永夜", "过去与未来"],
    "镜与谜烟的彼方": ["皆为崇高之名", "向着迷烟飘往之处", "摇曳灯火一分为二"],
    "命定将焚的虹光": ["秘源之下", "共睹那日之将落", "席卷而来的暗潮", "绝望高悬天之上", "我们不会孤军奋战", "名为「命运」的燃料"],
    "万火归一": ["为同一片土地"],
    "炽烈的还魂诗": ["地中残景", "正如日出日落", "星与火的征途", "众望所归", "当一切镌刻成碑"],
    "你存在的时空": ["命运始动的钥匙", "「救世主」", "你所不在的时空"],
    # 空月之歌
    "归途": ["墟火", "阴燃", "逆焰"],
    "雪浪与苍林之舞": ["月亮升起的地方", "于月光下重逢", "在夜的阴影中"],
    "尘与灯的挽歌": ["轰鸣与暗涌", "窥见记忆的暗面", "曾有人追猎月亮", "灰白的秩序熊熊燃烧", "遥不可及的安息"],
    "不存在的国土": ["匿于阴影", "特别行动", "如月长存"],
    "回望湮灭的月光": ["皆为预言", "蛇与蝎的亡命舞", "命运的回声"],
    "终北的夜行诗": ["月影轮番登台", "无法传达的涟漪"],
    "散于晨雾的月芒": ["名于何处？", "月亮回家的夜晚", "回到月亮上去"],
    "如果在冬夜，一个旅人": ["无月之夜", "你我交错的时空", "循着过往的足迹"],
    "真实之月": ["最初的那抹月光", "月之将坠", "空月归乡"],
    "身土坏空，五蕴识转": ["花于何处醒来", "通向自我的歧途", "旧影重现"],
    "虚空劫灰往世书（系列任务）": ["幽暗时分", "妄念与真知的通天塔", "虚空劫灰往世书", "你的往事如一座花园"],
    # 间章
    "风起鹤归": ["琼台玉阁", "鸣海栖霞", "往事如尘", "此心安处"],
    "危途疑踪": ["意外之客", "岩下迷境", "危机四伏", "穷途末路", "绝处逢生"],
    "倾落伽蓝": ["夜中飞鸟坠于三段", "乱世轮舞", "幕切——倾奇之末", "如朝露一般"],
    "悖理": ["第一及第二罪行", "众生的渴求", "园丁"],
}

# ====== 活动剧情→传说任务别名映射 ======
# 以下活动剧情常被玩家戏称为某角色的"传说任务"。
# 单向映射：用户查询时可按别名定位到活动剧情，但回答中禁止称其为传说任务。
ACTIVITY_LEGENDARY_ALIAS: Dict[str, List[str]] = {
    "兹白传说任务":    ["在云间", "在岩间", "在人间", "白马闲游记"],
    "兹白的传说任务":  ["在云间", "在岩间", "在人间", "白马闲游记"],
    "胡桃传说任务2":   ["璃月港佳节兴，八奇现瘴疠隐", "往生堂三日无主，玉京台遣将调兵", "奇门术息灾平昏寿，护摩法净世定幽冥", "终回：八奇炼桃都"],
    "胡桃传2":        ["璃月港佳节兴，八奇现瘴疠隐", "往生堂三日无主，玉京台遣将调兵", "奇门术息灾平昏寿，护摩法净世定幽冥", "终回：八奇炼桃都"],
    "胡桃的第二个传说任务": ["璃月港佳节兴，八奇现瘴疠隐", "往生堂三日无主，玉京台遣将调兵", "奇门术息灾平昏寿，护摩法净世定幽冥", "终回：八奇炼桃都"],
    "卡维传说任务":    ["蝶去蝶来", "沙起沙落", "人聚人散", "落幕时分"],
    "卡维的传说任务":  ["蝶去蝶来", "沙起沙落", "人聚人散", "落幕时分"],
    "菲米尼传说任务":  ["水妖的猜想", "王子的国度", "奇迹的冠冕"],
    "菲米尼的传说任务":["水妖的猜想", "王子的国度", "奇迹的冠冕"],
    "夏沃蕾传说任务":  ["划破宁静的枪响", "景框内外的虚实", "雾中隐现的孤岛", "何处盛放的蔷薇", "两个铳枪手的凯旋"],
    "夏沃蕾的传说任务":["划破宁静的枪响", "景框内外的虚实", "雾中隐现的孤岛", "何处盛放的蔷薇", "两个铳枪手的凯旋"],
    "布伦妮传说任务":  ["野林猪与小魔女", "若你遗忘梦的入口", "永不失效的魔法"],
    "布伦妮的传说任务":["野林猪与小魔女", "若你遗忘梦的入口", "永不失效的魔法"],
    "嘉明传说任务":    ["风莺梳春，开天呈祥", "描露摹晖，诉愿云海", "故鸢吹归，再宿堂楼", "人来人往"],
    "嘉明的传说任务":  ["风莺梳春，开天呈祥", "描露摹晖，诉愿云海", "故鸢吹归，再宿堂楼", "人来人往"],
}

# 以下子任务标题在知识库中的实际分类是活动剧情（非传说任务）
# 回答时禁止称其为传说任务，必须标注为活动剧情
FORCE_ACTIVITY_TITLES: set = {
    "在云间", "在岩间", "在人间", "白马闲游记",
    "璃月港佳节兴，八奇现瘴疠隐", "往生堂三日无主，玉京台遣将调兵",
    "奇门术息灾平昏寿，护摩法净世定幽冥", "终回：八奇炼桃都",
    "蝶去蝶来", "沙起沙落", "人聚人散", "落幕时分",
    "水妖的猜想", "王子的国度", "奇迹的冠冕",
    "划破宁静的枪响", "景框内外的虚实", "雾中隐现的孤岛",
    "何处盛放的蔷薇", "两个铳枪手的凯旋",
    "野林猪与小魔女", "若你遗忘梦的入口", "永不失效的魔法",
    "风莺梳春，开天呈祥", "描露摹晖，诉愿云海", "故鸢吹归，再宿堂楼", "人来人往",
}

# ====== 术语别名表 ======
# 同一实体在游戏文本中可能有多种称呼，搜A时自动也搜B。
# 维护：遇到新的同实体异名时追加。
TERM_ALIASES: Dict[str, List[str]] = {
    "天理的维系者": ["陌生的神灵", "阿斯莫代"],
}


def _expand_query_with_aliases(query: str) -> List[str]:
    """扩展查询词：术语别名 + 角色别名反向展开。
    角色别名反向展开解决"用户用规范名搜索，但文本中角色以别名出现"的问题
    （如搜"散兵"但活动文本中写"流浪者"）。"""
    results = [query]

    # 1. 术语别名（正向映射）
    term_aliases = TERM_ALIASES.get(query.strip(), [])
    if term_aliases:
        results.extend(term_aliases)

    # 2. 角色别名反向展开：查询中每个词若为规范角色名，生成别名变体
    words = query.split()
    for word in words:
        if word in CHARACTER_ALIASES:
            all_aliases = CHARACTER_ALIASES[word]
            for alias in all_aliases:
                if alias == word:
                    continue
                variant = query.replace(word, alias)
                if variant not in results:
                    results.append(variant)
                    if len(results) >= 12:  # 防止别名太多的角色爆炸
                        break
        if len(results) >= 12:
            break

    return results


# ====== 长任务预处理数据 ======

_quests_processed: Dict[str, dict] = {}

def _load_processed_data():
    global _quests_processed
    if _quests_processed:
        return
    path = os.path.join(CONTENT_DIR, "quests_processed.json")
    if not os.path.exists(path):
        return
    try:
        with open(path, "r", encoding="utf-8") as f:
            _quests_processed = json.load(f)
        print(f"[预处理] 加载 quests_processed.json ({len(_quests_processed)} 条长任务)")
    except Exception as e:
        print(f"[警告] 加载 quests_processed.json 失败: {e}")


# 角色名黑名单（非角色的 *前缀 行）
_NON_CHARACTER = {'旁白', '系统', '画外音', '系统提示', '任务提示', '说明'}


def _extract_scenes(text: str) -> list:
    """提取场景流转列表（按出场顺序）。兼容两种文本格式。"""
    # 格式1: ====标题====（世界任务/活动任务）
    scenes = []
    for m in re.finditer(r'^====(.+?)====$', text, re.MULTILINE):
        title = m.group(1).strip()
        if title and not re.match(r'^\d{1,2}:\d{2}', title):
            scenes.append(title[:30] + ('...' if len(title) > 30 else ''))
    if scenes:
        return scenes

    # 格式2: [场景描述]（传说任务/魔神任务）
    for m in re.finditer(r'^\[([^\]]{10,80})\]$', text, re.MULTILINE):
        desc = m.group(1).strip()
        if not desc.startswith('{{'):
            scenes.append(desc[:30] + ('...' if len(desc) > 30 else ''))
    return scenes


def _extract_characters(text: str) -> list:
    """提取出场角色（去重，按首次出场顺序排列，含字符偏移量）。"""
    pattern = re.compile(r'^\*([^*\s:：]{1,8})[：:]', re.MULTILINE)
    seen = OrderedDict()
    for match in pattern.finditer(text):
        name = match.group(1).strip()
        if not name or len(name) < 1:
            continue
        if re.search(r'[{}|【】]', name):
            continue
        if name in _NON_CHARACTER:
            continue
        if name not in seen:
            seen[name] = match.start()
    return list(seen.items())


def _build_quest_header(text: str, char_count: int) -> str:
    """为长任务文本（>3000字）生成结构索引头，帮助 Answer Agent 定位关键段落。"""
    if char_count <= 3000:
        return text

    scenes = _extract_scenes(text)
    characters = _extract_characters(text)

    header_parts = [f"[结构概览] 全文长度: {char_count} 字符"]

    if scenes:
        scene_flow = " → ".join(scenes[:8])
        if len(scenes) > 8:
            scene_flow += f" ... (共{len(scenes)}个场景)"
        header_parts.append(f"场景流转: {scene_flow}")

    if characters:
        char_list = ", ".join(
            f"{name}(L{pos // 1000 * 1000})" if pos >= 1000 else f"{name}(L0)"
            for name, pos in characters[:12]
        )
        if len(characters) > 12:
            char_list += f" ... (共{len(characters)}位)"
        header_parts.append(f"出场角色: {char_list}")

    header = "\n".join(header_parts)
    return header + "\n\n--- 以下为任务完整剧情 ---\n\n" + text


@tool
def load_quest_content(quest_name: str, query: str = "") -> str:
    """加载指定任务的剧情内容。长任务会自动返回大纲或按关键词检索原文片段。
    quest_name: 任务名称或幕名。支持玩家熟知的幕名（如\"虚空鼓动，劫火高扬\"），会自动匹配其下属子任务。
    query: 用户的具体问题或关键词（可选）。传入后会在长任务中精准检索相关原文片段，不走大纲。"""
    results = []
    # 检查是否为已知幕名（魔神任务章节）
    act_quests = ACT_TO_QUESTS.get(quest_name)
    # 也检查活动剧情→传说任务别名映射
    if not act_quests:
        act_quests = ACTIVITY_LEGENDARY_ALIAS.get(quest_name)
    # 如果都不是，尝试从任务知识库中查找该章节名对应的所有子任务
    if not act_quests:
        kb_tasks = set()
        for q in 任务知识库:
            series = q.get("系列任务", "")
            if series and quest_name in series:
                kb_tasks.add(q.get("任务名称", ""))
        if kb_tasks:
            act_quests = list(kb_tasks)
            print(f"[工具] 从任务知识库匹配到章节「{quest_name}」-> {len(act_quests)} 个子任务")
    for filename in os.listdir(CONTENT_DIR):
        if not filename.startswith("quests_") or not filename.endswith(".json"):
            continue
        if filename == "quests_processed.json":
            continue
        try:
            with open(os.path.join(CONTENT_DIR, filename), "r", encoding="utf-8") as f:
                quests = json.load(f)
        except Exception:
            continue
        if not isinstance(quests, list):
            continue
        for q in quests:
            if act_quests:
                # 幕名匹配：检查子任务标题是否在映射列表中
                if q["title"] in act_quests:
                    results.append((q["title"], q.get("category", ""), q["text"]))
            else:
                # 标题匹配（原有逻辑）
                if quest_name in q["title"] or quest_name in q.get("系列任务", ""):
                    results.append((q["title"], q.get("category", ""), q["text"]))
    if not results:
        return f"未找到包含「{quest_name}」的任务。"

    _load_processed_data()
    print(f"[工具] 加载任务: {quest_name} -> {len(results)} 条匹配")

    output = []
    for title, cat, text in results[:4]:
        # 类型名规范化：活动活动→活动剧情；指定标题强制标注为活动剧情
        if title in FORCE_ACTIVITY_TITLES or cat == "活动活动":
            cat = "活动剧情"
        char_count = len(text)

        if char_count <= 9000:
            # 短文本：>3000字注入结构索引头，<=3000字直接返回全文
            if char_count > 3000:
                text_with_header = _build_quest_header(text, char_count)
                output.append(f"\n【{title}】（{cat}）\n{text_with_header}")
            else:
                output.append(f"\n【{title}】（{cat}）\n{text}")

        elif title in _quests_processed:
            p = _quests_processed[title]
            if query and query.strip():
                # 轨道 B：BM25 粗召回 + Reranker 精排
                chunks = p.get("chunks", [])
                if chunks:
                    bm25 = SimpleBM25(chunks)
                    # 多拿一些候选交给 Reranker 筛选
                    top_chunks = bm25.search(query, top_k=8)
                    if top_chunks:
                        # Reranker 语义重排序
                        reranked_idx = _rerank(query, top_chunks, top_n=3)
                        if reranked_idx:
                            top_chunks = [top_chunks[i] for i in reranked_idx[:3]]
                        else:
                            top_chunks = top_chunks[:3]
                        print(f"  -> BM25+Reranker: \"{query}\" -> {len(top_chunks)} 个切片")
                        chunk_texts = "\n\n---\n\n".join(top_chunks)
                        output.append(
                            f"\n【{title}】（{cat}）\n"
                            f"[BM25+Reranker检索: \"{query}\", 共{char_count}字, 命中{len(top_chunks)}个切片]\n"
                            f"{chunk_texts}"
                        )
                    else:
                        # BM25 无命中，回退到大纲
                        output.append(
                            f"\n【{title}】（{cat}）\n[剧情大纲]\n{p['summary']}"
                        )
                else:
                    # 无切片（异常情况），用前 9000 字
                    output.append(f"\n【{title}】（{cat}）\n{text[:9000]}")
            else:
                # 轨道 A：返回离线摘要
                output.append(
                    f"\n【{title}】（{cat}）\n[剧情大纲]\n{p['summary']}"
                )
        else:
            # 未预处理的长任务（不应出现），返回前 9000 字
            output.append(
                f"\n【{title}】（{cat}）\n{text[:9000]}"
                f"\n...（共{char_count}字，仅显示前9000字）"
            )

    return "\n".join(output)


@tool
def count_character_lines(character_name: str, quest_name: str = "") -> str:
    """统计指定角色在剧情任务中说了多少句话。character_name: 角色名称；quest_name: 任务名关键词（可选）"""
    aliases = resolve_aliases(character_name)
    print(f"[工具] 统计台词: {character_name} -> 别名={aliases}")
    results = []
    for filename in os.listdir(CONTENT_DIR):
        if not filename.startswith("quests_") or not filename.endswith(".json"):
            continue
        if filename == "quests_processed.json":
            continue
        try:
            with open(os.path.join(CONTENT_DIR, filename), "r", encoding="utf-8") as f:
                quests = json.load(f)
        except Exception:
            continue
        if not isinstance(quests, list):
            continue
        for q in quests:
            if quest_name and quest_name not in q["title"]:
                continue
            text = q["text"]
            total_lines = 0
            for alias in aliases:
                lines = re.findall(rf'^\*{re.escape(alias)}[：:(]', text, re.MULTILINE)
                total_lines += len(lines)
            if total_lines > 0:
                sample_lines = []
                for alias in aliases:
                    found = re.findall(rf'^\*{re.escape(alias)}[：:(].+', text, re.MULTILINE)
                    sample_lines.extend(found[:3])
                results.append((q["title"], q.get("category", ""), total_lines, sample_lines[:3]))
    if not results:
        return f"未找到「{character_name}」的台词。"
    output_lines = [f"\n【{character_name} 台词统计】（别名：{' / '.join(aliases)}）"]
    grand_total = 0
    for title, cat, count, samples in results:
        grand_total += count
        output_lines.append(f"\n「{title}」（{cat}）: {count} 句")
        for s in samples:
            output_lines.append(f"  · {s.strip()[:100]}")
    output_lines.append(f"\n{'─'*40}")
    output_lines.append(f"总计: {grand_total} 句台词（{len(results)} 个任务）")
    return "\n".join(output_lines)


@tool
def query_monster(name: str) -> str:
    """查询怪物图鉴：属性、抗性、掉落、技能。name: 怪物名称（模糊匹配）"""
    monsters = _load_content_json("monsters")
    if not monsters:
        return "怪物数据库为空。"
    matches = [m for m in monsters if name in m.get("名称", "")]
    if not matches:
        matches = [m for m in monsters if name in m.get("别称", "")]
    if not matches:
        names = [m["名称"] for m in monsters[:30]]
        return f"未找到「{name}」。部分怪物: {', '.join(names)}"
    results = []
    for m in matches[:5]:
        info = f"\n【{m.get('名称', '?')}】"
        if m.get("别称"):
            info += f" ({m['别称']})"
        info += f"\n  类型: {m.get('怪物类型', '?')} | 元素: {m.get('元素属性', '?')}"
        info += f"\n  攻击属性: {m.get('攻击属性', '?')}"
        if m.get("抗性"):
            info += f"\n  抗性: {', '.join(f'{k}={v}' for k, v in m['抗性'].items() if v)}"
        if m.get("掉落"):
            for dk, dv in m["掉落"].items():
                info += f"\n  {dk}: {dv[:150]}"
        if m.get("出现地点"):
            info += f"\n  出现地点: {m['出现地点'][:200]}"
        if m.get("技能名"):
            info += f"\n  技能: {m['技能名']}"
        results.append(info)
    return "\n".join(results)


@tool
def query_artifact(name: str) -> str:
    """查询圣遗物套装：两件套/四件套效果、部位故事。name: 套装名称（模糊匹配）"""
    if not 圣遗物知识库:
        return "圣遗物知识库为空。"
    matches = [a for a in 圣遗物知识库 if name in a.get("圣遗物名称", "")]
    if not matches:
        names = [a["圣遗物名称"] for a in 圣遗物知识库[:20]]
        return f"未找到「{name}」。部分圣遗物: {', '.join(names)}"
    results = []
    for a in matches[:5]:
        info = f"\n【{a.get('圣遗物名称', '?')}】({a.get('稀有度', '?')}星)"
        info += f"\n  两件套: {a.get('两件套效果', '?')}"
        info += f"\n  四件套: {a.get('四件套效果', '?')}"
        stories = a.get("部位故事", {})
        if stories:
            info += f"\n  部位故事 ({len(stories)}篇):"
            for k, v in stories.items():
                info += f"\n    [{k}]: {v[:200]}..."
        results.append(info)
    return "\n".join(results)


@tool
def query_material(name: str) -> str:
    """查询材料：用途、来源、类型。name: 材料名称（模糊匹配）"""
    materials = _load_content_json("materials")
    if not materials:
        return "材料数据库为空。"
    matches = [m for m in materials if name in m.get("名称", "")]
    if not matches:
        names = [m["名称"] for m in materials if m.get("名称")][:30]
        return f"未找到「{name}」。部分材料: {', '.join(names)}"
    results = []
    for m in matches[:8]:
        info = f"\n【{m.get('名称', '?')}】"
        if m.get("类型"):
            info += f" | {m['类型']}"
        if m.get("稀有度"):
            info += f" | {m['稀有度']}星"
        if m.get("用途"):
            info += f"\n  用途: {m['用途'][:200]}"
        if m.get("来源"):
            info += f"\n  来源: {m['来源'][:200]}"
        if m.get("简介"):
            info += f"\n  简介: {m['简介'][:200]}"
        results.append(info)
    return "\n".join(results)


@tool
def query_collectible(name: str) -> str:
    """查询采集物/地区特产：分布地区、刷新时间、用途。name: 采集物名称（模糊匹配）"""
    collectibles = _load_content_json("collectibles")
    if not collectibles:
        return "采集物数据库为空。"
    matches = [c for c in collectibles if name in c.get("名称", "")]
    if not matches:
        names = [c["名称"] for c in collectibles if c.get("名称")][:30]
        return f"未找到「{name}」。部分采集物: {', '.join(names)}"
    results = []
    for c in matches[:8]:
        info = f"\n【{c.get('名称', '?')}】"
        if c.get("类型"):
            info += f" | {c['类型']}"
        if c.get("分布地区"):
            info += f"\n  分布: {c['分布地区'][:300]}"
        if c.get("刷新时间"):
            info += f"\n  刷新: {c['刷新时间']}"
        if c.get("用途"):
            info += f"\n  用途: {c['用途'][:200]}"
        results.append(info)
    return "\n".join(results)


@tool
def list_collectibles_by_region(region: str) -> str:
    """按地区列出所有区域特产。region: 地区名称（蒙德/璃月/稻妻/须弥/枫丹/纳塔/挪德卡莱）"""
    collectibles = _load_content_json("collectibles")
    if not collectibles:
        return "采集物数据库为空。"
    keyword = f"{region}区域特产"
    matched = [c for c in collectibles if keyword in c.get("类型", "")]
    if not matched:
        return f"未找到{region}区域特产数据。"
    lines = [f"\n【{region}区域特产】({len(matched)}种)", "-" * 40]
    for c in matched:
        lines.append(f"  {c['名称']}")
    return "\n".join(lines)


@tool
def query_recipe(name: str) -> str:
    """查询食谱：效果、材料、获取方式。name: 食谱名称（模糊匹配）"""
    recipes = _load_content_json("recipes")
    if not recipes:
        return "食谱数据库为空。"
    matches = [r for r in recipes if name in r.get("名称", "")]
    if not matches:
        names = [r["名称"] for r in recipes if r.get("名称")][:30]
        return f"未找到「{name}」。部分食谱: {', '.join(names)}"
    results = []
    for r in matches[:8]:
        info = f"\n【{r.get('名称', '?')}】"
        if r.get("稀有度"):
            info += f" | {r['稀有度']}星"
        if r.get("效果"):
            info += f"\n  效果: {r['效果'][:200]}"
        if r.get("材料"):
            info += f"\n  材料: {r['材料'][:200]}"
        if r.get("获取方式"):
            info += f"\n  获取: {r['获取方式'][:200]}"
        results.append(info)
    return "\n".join(results)


@tool
def query_food(name: str) -> str:
    """查询食物/料理信息。name: 食物名称（模糊匹配）"""
    foods = _load_content_json("foods")
    if not foods:
        return "食物数据库为空。"
    matches = [f for f in foods if name in f.get("名称", "")]
    if not matches:
        names = [f["名称"] for f in foods[:20]]
        return f"未找到「{name}」。部分食物: {', '.join(names)}"
    results = []
    for f in matches[:10]:
        info = f"\n【{f.get('名称', '?')}】"
        info += f"\n  稀有度: {f.get('稀有度', '?')}星"
        info += f"\n  类型: {f.get('类型', '?')}"
        info += f"\n  效果: {f.get('效果说明', '')[:200]}"
        desc = f.get("介绍", "").replace("<br>", " ")
        info += f"\n  介绍: {desc[:300]}"
        info += f"\n  食材: {f.get('所需食材', '')}"
        info += f"\n  获取: {f.get('获取方式', '')[:200]}"
        if f.get("特殊料理角色"):
            info += f"\n  特殊料理: {f.get('特殊料理角色', '')}"
        results.append(info)
    return "\n".join(results)


@tool
def find_first_mention(keyword: str) -> str:
    """
    查找某个关键词在剧情文本中首次出现的位置。按游戏版本号排序，优先返回版本最早的任务。
    keyword: 要查找的关键词（如\"降临者\"、\"深渊\"）
    """
    print(f"[工具] 查找首次提及: {keyword}")
    type_priority = {"魔神任务": 1, "传说任务": 2, "世界任务": 3, "部族纪闻": 4, "邀约事件": 5}
    all_matches = []
    for filename in os.listdir(CONTENT_DIR):
        if not filename.startswith("quests_") or not filename.endswith(".json"):
            continue
        if filename == "quests_processed.json":
            continue
        try:
            with open(os.path.join(CONTENT_DIR, filename), "r", encoding="utf-8") as f:
                quests = json.load(f)
        except Exception:
            continue
        if not isinstance(quests, list):
            continue
        for q in quests:
            text = q.get("text", "")
            if not _match_all_in(keyword, text):
                continue
            category = q.get("category", "")
            priority = type_priority.get(category, 99)
            metadata = q.get("metadata", {})
            version_str = metadata.get("所属版本", "")
            try:
                version = float(version_str) if version_str else 999.0
            except ValueError:
                version = 999.0
            chapter_name = metadata.get("chapter_name", "")
            act_name = metadata.get("act_name", "")
            # 多词时用第一个词定位
            first_kw = keyword.split()[0]
            idx = text.find(first_kw)
            start = max(0, idx - 100)
            end = min(len(text), idx + len(keyword) + 100)
            snippet = text[start:end].replace('\n', ' ').strip()
            speaker_match = re.search(r'\*「?([^」\n：]{1,10})」?：', text[max(0, idx-200):idx+50])
            speaker = speaker_match.group(1) if speaker_match else "未知"
            all_matches.append({
                "title": q["title"], "category": category,
                "version": version, "version_str": version_str,
                "priority": priority, "speaker": speaker, "snippet": snippet,
                "chapter_name": chapter_name, "act_name": act_name,
            })
    if not all_matches:
        return f"未在任务剧情中找到「{keyword}」的提及。"
    all_matches.sort(key=lambda x: (x["version"], x["priority"]))
    first = all_matches[0]
    result = f"\n【首次提及位置】\n"
    # 构建层级信息（章/幕）
    hierarchy_parts = []
    if first.get("chapter_name") and first["chapter_name"] != "开场动画":
        hierarchy_parts.append(first["chapter_name"])
    if first.get("act_name"):
        hierarchy_parts.append(first["act_name"])
    if first.get("chapter_name") == "开场动画":
        hierarchy_parts.append("开场动画")
    hierarchy_str = "，".join(hierarchy_parts) + "，" if hierarchy_parts else ""
    result += f"任务: {first['title']}（{first['category']}，{hierarchy_str}版本 {first['version_str']}）\n" if first['version_str'] else f"任务: {first['title']}（{first['category']}）\n"
    result += f"说话角色: {first['speaker']}\n"
    result += f"原文片段: 「...{first['snippet']}...」\n"
    if len(all_matches) > 1:
        result += f"\n其他提及位置（共{len(all_matches)}处）:\n"
        for m in all_matches[1:6]:
            v = f"（版本 {m['version_str']}）" if m['version_str'] else ""
            result += f"  - {m['title']}（{m['category']}）{v}\n"
    return result


def kb_vector_search(query: str, collection: str = "", top_k: int = 5) -> str:
    """语义搜索知识库（向量检索）。适合模糊/概念性问题。
query: 搜索内容（自然语言描述即可）
collection: 指定集合（quests/lore/books/characters/regions），为空则搜全部
top_k: 返回结果数，默认5"""
    if _vector_store is None:
        return "向量知识库未初始化。请先运行 kb_build_index.py 构建索引。"
    col_name = f"kb_{collection}" if collection else None
    results = _vector_store.search(query, collection=col_name, top_k=top_k)
    if not results:
        return f"语义搜索未找到与「{query}」相关的内容。"
    lines = [f"===== 语义搜索「{query}」({len(results)}条结果) ====="]
    for i, r in enumerate(results):
        # 从 id 中提取类型和标题信息
        doc_id = r.get("id", "")
        collection = r.get("collection", "")
        doc_preview = r.get("document", "")[:500].replace('\n', ' ')
        score = r.get("score", 0)
        # 格式化标题
        header = f"【{doc_id}】({collection})"
        lines.append(header)
        lines.append(f"  相似度: {score:.4f} | 内容: {doc_preview}...")
    return "\n".join(lines)


@tool
def get_book_metadata(book_name: str) -> str:
    """获取书籍的元数据（作者、版本、体裁、卷数），不返回正文。
book_name: 书籍名称。当用户问"作者是谁"、"哪个版本"、"什么体裁"等涉及书籍元数据的问题时，优先用此工具而非 load_book_content。"""
    content_file = os.path.join(CONTENT_DIR, "books.json")
    try:
        with open(content_file, "r", encoding="utf-8") as f:
            books = json.load(f)
    except Exception:
        return "书籍数据文件未找到。"
    normalized_book = _normalize_for_match(book_name)
    # 双向匹配：支持 "提瓦特游览指南·蒙德篇" 匹配到 "提瓦特游览指南"（书名作为查询的子串）
    matches = [b for b in books if normalized_book in _normalize_for_match(b["title"])
               or _normalize_for_match(b["title"]) in normalized_book]
    if not matches:
        return f"未找到书籍「{book_name}」。"
    best = max(matches, key=lambda b: len(b["text"]))
    meta = best.get("metadata", {})
    lines = [f"\n【{best['title']}】元数据"]
    if meta.get("作者"):
        lines.append(f"作者: {meta['作者']}")
    else:
        lines.append(f"作者: 游戏内未提及")
    if meta.get("体裁"):
        lines.append(f"体裁: {meta['体裁']}")
    if meta.get("卷数"):
        lines.append(f"卷数: {meta['卷数']}")
    if meta.get("实装版本"):
        lines.append(f"版本: {meta['实装版本']}")
    return "\n".join(lines)


# ====== 工具列表 ======
tools = [
    query_character, query_region, query_story, query_weapon, query_quest,
    list_characters_by_element, list_characters_by_region, list_characters_by_weapon, list_characters_by_rarity,
    list_all_quest_series,
    list_all_books, list_all_lore_entries, list_all_weapons_and_artifacts, list_all_game_items,
    search_activity,
    load_book_content, load_quest_content, get_book_metadata,
    count_character_lines, find_first_mention,
    query_monster, query_artifact, query_material, query_collectible, list_collectibles_by_region, query_recipe,
    query_food,
    hybrid_search,
]

llm_with_tools = llm.bind_tools(tools)
tool_node = ToolNode(tools)

# ====== 工具名 → 函数映射（供意图路由器动态注入） ======
_tools_by_name = {t.name: t for t in tools}

# ====== 代码加固：熔断触发工具 ======

# 这些工具一旦在本轮中成功返回内容，后续所有工具调用将被系统截断
MELTDOWN_TRIGGER_TOOLS = {"load_book_content", "load_quest_content", "find_first_mention"}

# ====== 进度事件（供 Web API 流式推给前端） ======
_progress_hook = None
# 取消信号字典：run_id → threading.Event（线程安全，避免模块级变量竞态）
_cancel_events: Dict[str, "threading.Event"] = {}


def _emit_progress(event_type: str, data: dict):
    """向 Web 前端推送进度事件。_progress_hook 由 Web API 注入。"""
    if _progress_hook:
        try:
            _progress_hook({"type": event_type, **data})
        except Exception:
            pass


# ====== 自定义工具执行节点（带日志） ======
def tool_executor(state):
    """执行工具调用并记录输入/输出日志。
    代码加固：熔断截断。"""
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
            result = tool_node.tools_by_name[tool_name].invoke(tool_args)
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

# ====== 配置 ======
MAX_AGENT_ITERATIONS = 10
MAX_PLAN_RETRIES = 2       # Plan Agent 无工具调用时的最大强制重试次数
MAX_FAST_ITERATIONS = 2    # 快速路径的最大工具调用轮次

PROMPTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "prompts")

def _load_prompt(filename: str) -> str:
    filepath = os.path.join(PROMPTS_DIR, filename)
    if os.path.exists(filepath):
        with open(filepath, "r", encoding="utf-8") as f:
            return f.read()
    return ""

AGENT_SYSTEM_PROMPT_PLAN = _load_prompt("system/agent_system_v4_plan.txt")
AGENT_SYSTEM_PROMPT_ANSWER = _load_prompt("system/agent_system_v4_answer.txt")
AGENT_SYSTEM_PROMPT_FAST = _load_prompt("system/agent_fast_answer.txt")
HELP_TEXT = _load_prompt("help.txt")

# ====== Agent 状态 ======
class GenshinAdvisorState(TypedDict):
    user_query: str
    rewritten_query: Optional[str]
    alias_notes: Optional[str]
    conversation_history: Optional[List[Dict[str, str]]]
    conversation_summary: Optional[str]
    messages: Annotated[List[BaseMessage], add_messages]
    final_response: Optional[str]
    iteration: Optional[int]
    plan_retry: Optional[int]             # 规划阶段无工具调用时的强制重试计数
    execution_plan: Optional[str]          # 规划阶段生成的执行报告，供回答阶段使用
    intent_labels: Optional[List[str]]    # 路由器输出的意图标签，如 ["B", "D"]
    run_id: Optional[str]                 # 运行标识，用于取消信号查找
    execution_mode: Optional[str]         # L1（快速路径）或 L2（完整路径）
    fast_iteration: Optional[int]         # 快速路径的迭代计数


# ====== 多轮记忆配置 ======
RECENT_TURNS = 3       # 最近 N 轮完整保留
SUMMARY_TRIGGER = 5    # 总轮数超过此值时触发摘要


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


# ====== 统一 Agent 循环 ======
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
    return any(kw in content for kw in ("未找到", "未收录", "不存在", "无匹配", "No match", "not found"))


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
    from langchain_core.messages import ToolMessage
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
            is_failure = ("未找到" in content or "未在" in content or
                         "不存在" in content or "没有找到" in content)
            is_intercepted = "系统拦截" in content

            if is_failure:
                consecutive_failures += 1
                max_consecutive_failures = max(max_consecutive_failures, consecutive_failures)
            else:
                consecutive_failures = 0

            # 提取工具名
            tool_name = msg.name if hasattr(msg, 'name') else ""
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


# ====== 构建工作流 ======
def create_agent_workflow() -> StateGraph:
    workflow = StateGraph(GenshinAdvisorState)

    workflow.add_node("rewrite_query", rewrite_query)
    workflow.add_node("assess_query", assess_query)
    workflow.add_node("fast_agent", fast_agent)
    workflow.add_node("plan_agent", plan_agent)
    workflow.add_node("tools", tool_executor)
    workflow.add_node("answer_agent", answer_agent)

    workflow.set_entry_point("rewrite_query")
    workflow.add_edge("rewrite_query", "assess_query")

    # 分类后路由：L1 → fast_agent，L2 → plan_agent
    workflow.add_conditional_edges(
        "assess_query",
        route_after_assess,
        {"fast_agent": "fast_agent", "plan_agent": "plan_agent"},
    )

    # L2 路径：plan_agent → tools 循环 → answer_agent
    workflow.add_conditional_edges(
        "plan_agent",
        route_after_plan,
        {"tools": "tools", "answer_agent": "answer_agent"},
    )
    workflow.add_conditional_edges(
        "tools",
        route_after_tools,
        {"plan_agent": "plan_agent", "fast_agent": "fast_agent", "answer_agent": "answer_agent", "end": END},
    )
    workflow.add_edge("answer_agent", END)

    # L1 路径：fast_agent → tools 循环 → END
    workflow.add_conditional_edges(
        "fast_agent",
        route_after_fast,
        {"tools": "tools", "end": END, "fast_agent": "fast_agent"},
    )

    return workflow.compile()


# ====== 主函数 ======
def main():
    print("\n" + "=" * 55)
    print("       原神剧情检索助手 Agent")
    print("=" * 55)
    print(f"  LLM: 通义千问 qwen3.7-max")
    print(f"  架构: 别名归一化 → 路径分类(L1/L2) → 快速/完整 双路径")
    print(f"  L1 快速: 合并 Plan/Answer，最多 {MAX_FAST_ITERATIONS} 轮工具调用")
    print(f"  L2 完整: Plan → Tool → Answer，最多 {MAX_AGENT_ITERATIONS} 轮迭代")
    print(f"  知识库: 角色{len(角色知识库)}位 | 武器{len(武器知识库)}把 | 圣遗物{len(圣遗物知识库)}套")
    print(f"  剧情: {len(主线剧情知识库)}章 | 地区{len(地区知识库)}个 | 任务{len(任务知识库)}个")
    print(f"  内容数据: 怪物/材料/采集物/食谱/书籍/任务剧情")
    print(f"  RAG记忆: {'已启用' if rag_memory else '未启用'}")
    print("=" * 55)
    print("\n输入 '帮助' 查看用法，输入 '退出' 结束对话\n")

    agent = create_agent_workflow()
    conversation_history = []
    conversation_summary = ""

    while True:
        try:
            user_input = input("\n【旅行者】").strip()
            if not user_input:
                continue

            if user_input.lower() in ["退出", "exit", "q"]:
                print("\n祝你在提瓦特的旅途愉快！")
                break

            if user_input.lower() in ["帮助", "help"]:
                print(HELP_TEXT)
                continue

            if user_input.lower() in ["记忆", "memory"]:
                if rag_memory:
                    stats = rag_memory.get_stats()
                    print(f"\n  记忆总数: {stats.get('count', 0)} 条")
                else:
                    print("\n  RAG 记忆系统未启用（需安装 chromadb + sentence-transformers）")
                continue

            print("\n  ... 正在查询...")

            start = datetime.now()
            result = agent.invoke({
                "user_query": user_input,
                "rewritten_query": None,
                "alias_notes": None,
                "conversation_history": conversation_history,
                "conversation_summary": conversation_summary,
                "messages": [],
                "final_response": None,
                "iteration": 0,
            })
            elapsed = (datetime.now() - start).total_seconds()

            response = result.get("final_response", "未生成回答")

            print(f"\n{'─' * 50}")
            print(f"  [用时 {elapsed:.1f}s]")
            print(f"{'─' * 50}")
            print(response)
            print(f"{'─' * 50}")

            conversation_history.append({"user": user_input, "assistant": response})

            # 多轮记忆管理：超过阈值时触发摘要
            unsummarized = len(conversation_history)
            if unsummarized > SUMMARY_TRIGGER:
                # 保留最近 RECENT_TURNS 轮，其余做摘要
                turns_to_summarize = conversation_history[:-RECENT_TURNS]
                if turns_to_summarize:
                    print(f"\n  [记忆] 压缩 {len(turns_to_summarize)} 轮对话...")
                    conversation_summary = _summarize_conversation(conversation_summary, turns_to_summarize)
                    conversation_history = conversation_history[-RECENT_TURNS:]
                    print(f"  [记忆] 摘要完成，当前保留最近 {len(conversation_history)} 轮")

            # 保存到 RAG 长期记忆
            if rag_memory:
                try:
                    rag_memory.save_conversation(user_input, response)
                except Exception as e:
                    print(f"\n[记忆] 保存失败: {e}")

        except KeyboardInterrupt:
            print("\n\n祝你在提瓦特的旅途愉快！")
            break
        except Exception as e:
            print(f"\n[系统错误] {e}")


if __name__ == "__main__":
    main()

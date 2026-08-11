# -*- coding: utf-8 -*-
"""检索层：别名处理、BM25、混合检索、RRF 融合、向量检索。

依赖关系：
- app.config: API key、路径
- app.data: 知识库、_match_all_in、_load_content_json、TITLE_REGISTRY
- app.llm: alias_judge_llm
- character_aliases: CHARACTER_ALIASES（外部模块）
"""
import os
import re
import math
import json
import requests
from collections import Counter
from typing import Dict, List, Optional

from langchain_core.messages import HumanMessage
from langchain_core.tools import tool

from app.config import CONTENT_DIR, QWEN_API_KEY
from app.data import (
    _match_all_in, _load_content_json, _normalize_for_match,
)
from app.llm import alias_judge_llm

# 字符别名表（外部独立模块）
from character_aliases import CHARACTER_ALIASES, resolve_aliases, ALIAS_MAP, ALIASES_SORTED


# ====== 查询消毒与别名检测 ======

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


def _expand_query_with_aliases(query: str) -> List[str]:
    """扩展查询词：术语别名 + 角色别名反向展开。
    角色别名反向展开解决"用户用规范名搜索，但文本中角色以别名出现"的问题
    （如搜"散兵"但活动文本中写"流浪者"）。"""
    # 延迟导入避免循环依赖
    from app.data import TERM_ALIASES
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


# ====== 任务标题注册表（启动时构建一次）======

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


# 启动时构建任务标题注册表和章节索引
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
    # 延迟导入避免循环依赖（_vector_store 在 data.py 顶层初始化）
    from app.data import _vector_store

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


@tool
def kb_vector_search(query: str, collection: str = "", top_k: int = 5) -> str:
    """语义搜索知识库（向量检索）。适合模糊/概念性问题。
    query: 搜索内容（自然语言描述即可）
    collection: 指定集合（quests/lore/books/characters/regions），为空则搜全部
    top_k: 返回结果数，默认5"""
    # 延迟导入避免循环依赖
    from app.data import _vector_store

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

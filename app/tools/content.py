# -*- coding: utf-8 -*-
"""Content 类工具：加载完整内容（书籍/任务剧情）。

长任务加载使用双轨道：
- 轨道 A（无 query）：返回离线摘要大纲
- 轨道 B（有 query）：BM25 粗召回 + Reranker 精排，定位最相关原文片段
"""
import os
import json

from langchain_core.tools import tool

from app.config import CONTENT_DIR
from app.data import (
    任务知识库, _normalize_for_match, _load_processed_data, _quests_processed,
    _build_quest_header, ACT_TO_QUESTS, ACTIVITY_LEGENDARY_ALIAS, FORCE_ACTIVITY_TITLES,
    _aggregate_map_text,
)
from app.retrieval import SimpleBM25, _rerank


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
        map_text = _aggregate_map_text(book_name)
        if map_text:
            return map_text
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
    meta_line = f"\n[书籍信息] {', '.join(meta_parts)}" if meta_parts else ""

    preview = text[:3000]
    more = f"\n...（共{len(text)}字）" if len(text) > 3000 else ""
    return f"\n【{best['title']}】\n{preview}{more}{meta_line}"


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
        # 指定标题强制标注为活动剧情（活动剧情类数据本身已使用该分类名）
        if title in FORCE_ACTIVITY_TITLES:
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
                chunks = p.get("chunks_bm25") or p.get("chunks", [])
                if chunks:
                    bm25 = SimpleBM25(chunks)
                    # 多拿一些候选交给 Reranker 筛选
                    top_chunks = bm25.search(query, top_k=8)
                    if top_chunks:
                        # Reranker 语义重排序
                        reranked_idx = _rerank(query, top_chunks, top_n=3)
                        if reranked_idx is None:
                            top_chunks = top_chunks[:3]
                        elif reranked_idx:
                            top_chunks = [top_chunks[i] for i in reranked_idx[:3]]
                        else:
                            top_chunks = []
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

# -*- coding: utf-8 -*-
"""Content 类工具：加载完整内容（书籍/任务剧情）。

长任务加载使用双轨道：
- 轨道 A（无 query）：返回离线摘要大纲
- 轨道 B（有 query）：BM25 粗召回 + Reranker 精排，定位最相关原文片段
"""

import os
import json
import re

from langchain_core.tools import tool

from app.config import CONTENT_DIR
from app.data import (
    任务知识库,
    _normalize_for_match,
    _load_processed_data,
    _quests_processed,
    _build_quest_header,
    ACT_TO_QUESTS,
    ACTIVITY_LEGENDARY_ALIAS,
    FORCE_ACTIVITY_TITLES,
    _aggregate_map_text,
)
from app.retrieval import SimpleBM25, _rerank


_PAGE_CHARS = 13000  # 每页正文预算：13000 字以内整本一次给全（104/105 本书走这条路），超长书才分页
_VOLUME_RE = re.compile(r"【卷(\d+)内容】\s*([^\n]{0,24})")
# 卷号请求与卷数解析（支持「第五卷」/「卷5」），用于卷号守卫。
_CN_NUM = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
_VOLUME_REQUEST_RE = re.compile(r"(?:第)?([0-9一二三四五六七八九十]+)\s*卷")


def _volume_number(text: str):
    """取文本里的第一个卷号，取不到返回 None。"""
    match = _VOLUME_REQUEST_RE.search(text or "")
    if not match:
        return None
    raw = match.group(1)
    if raw.isdigit():
        return int(raw)
    if len(raw) == 1:
        return _CN_NUM.get(raw)
    return None


def _volume_total(text: str, meta: dict) -> int:
    """书内卷标记的最大卷号（优先），否则取元数据「共N卷」。"""
    numbers = [int(num) for num, _ in _VOLUME_RE.findall(text or "")]
    if numbers:
        return max(numbers)
    return _volume_number(str((meta or {}).get("卷数") or "")) or 0


def _book_pages(text: str) -> list:
    """把书籍正文切成页：带【卷N内容】标记的按整卷打包，其余按 _PAGE_CHARS 定长切。

    返回 [(页标签, 页正文)]。单卷超过预算时整卷返回，不把一卷劈成两页——
    分页的意义是让模型能读完长书，而不是把卷切碎。
    """
    marks = list(_VOLUME_RE.finditer(text))
    pages = []
    if marks:
        segs = []
        if marks[0].start() > 0:
            segs.append((None, "", text[: marks[0].start()]))  # 卷前导言
        for i, m in enumerate(marks):
            end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
            segs.append((m.group(1), m.group(2).strip(), text[m.start() : end]))
        cur, labels, cur_len = [], [], 0
        for num, title, seg in segs:
            label = f"卷{num} {title}".strip() if num else "卷前导言"
            if cur and cur_len + len(seg) > _PAGE_CHARS:
                pages.append(("、".join(labels), "".join(cur)))
                cur, labels, cur_len = [], [], 0
            cur.append(seg)
            labels.append(label)
            cur_len += len(seg)
        if cur:
            pages.append(("、".join(labels), "".join(cur)))
    else:
        for i in range(0, len(text), _PAGE_CHARS):
            pages.append((f"第{i + 1}-{min(i + _PAGE_CHARS, len(text))}字", text[i : i + _PAGE_CHARS]))
    return pages


def _page_hint(title: str, pages: list, page_no: int, total_chars: int) -> str:
    """返回续读指引；只有一页时返回空串。"""
    if len(pages) <= 1:
        return ""
    head = f"\n...（本书共{total_chars}字，共 {len(pages)} 页；本页为第 {page_no} 页 = {pages[page_no - 1][0]}）"
    if page_no < len(pages):
        nxt = page_no + 1
        return f'{head}\n【续读】load_book_content(book_name="{title}", part={nxt}) → {pages[nxt - 1][0]}'
    return f"{head}\n【续读】已是最后一页。"


def _load_book_matches(book_name: str):
    """读取书籍数据并做双向书名匹配。

    返回 (matches, None)；未命中时返回 (None, 提前返回文本)——
    提前返回文本可能是地图文本聚合结果或「未找到书籍」提示。
    """
    content_file = os.path.join(CONTENT_DIR, "books.json")
    try:
        with open(content_file, "r", encoding="utf-8") as f:
            books = json.load(f)
    except Exception:
        return None, "书籍数据文件未找到。"
    normalized_book = _normalize_for_match(book_name)
    # 双向匹配：支持 "提瓦特游览指南·蒙德篇" 匹配到 "提瓦特游览指南"（书名作为查询的子串）
    matches = []
    for b in books:
        if not isinstance(b, dict):
            continue
        title = b.get("title") or ""
        if not title:
            # 畸形记录：必须跳过——空标题会让 "" in normalized_book 恒真，匹配到所有书。
            continue
        norm_title = _normalize_for_match(title)
        if normalized_book in norm_title or norm_title in normalized_book:
            matches.append(b)
    if not matches:
        map_text = _aggregate_map_text(book_name)
        if map_text:
            return None, map_text
        return None, f"未找到书籍「{book_name}」。"
    return matches, None


def _book_meta_line(meta: dict) -> str:
    """把书籍元数据拼成返回头部的 [书籍信息] 行。"""
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
    return f"[书籍信息] {', '.join(meta_parts)}"


def _select_book_page(pages: list, part) -> int:
    """解析页码：非法或越界时夹到 [1, len(pages)]。"""
    try:
        return max(1, min(int(part), len(pages)))
    except (TypeError, ValueError):
        return 1


def _format_page_line(page_no: int, pages: list, page_label: str) -> str:
    """只有多页时才显示 [本页: 第 x/y 页] 行。"""
    return f"\n[本页: 第 {page_no}/{len(pages)} 页 = {page_label}]" if len(pages) > 1 else ""


def _apply_volume_guard(book_name: str, title: str, text: str, meta: dict, meta_line: str, pages: list, page_no: int):
    """卷号守卫：书名点名了「第N卷」时校验并定位到对应页。

    返回 (提前返回文本, 页号)，两者互斥：
    - 请求卷号超出总卷数：返回 (卷号校验文本, 原页号)；
    - 命中卷标记：返回 (None, 该卷所在页号)；
    - 无卷号请求/无卷数据/未命中：返回 (None, 原页号)。
    """
    requested_volume = _volume_number(book_name)
    if not requested_volume:
        return None, page_no
    total_volumes = _volume_total(text, meta)
    if not total_volumes:
        return None, page_no
    if requested_volume > total_volumes:
        catalog = "、".join(f"卷{num} {vol_title.strip()}".strip() for num, vol_title in _VOLUME_RE.findall(text))
        return (
            f"\n【{title}】{meta_line}\n"
            f"[卷号校验] 本书共 {total_volumes} 卷，不存在「第{requested_volume}卷」。"
            + (f"现有卷目：{catalog}。" if catalog else "")
            + "\n请按上述卷目提问；不指定卷号可直接读取全书。"
        ), page_no
    for index, (label, _body) in enumerate(pages, 1):
        if f"卷{requested_volume}" in label:
            return None, index
    return None, page_no


def _book_keyword_snippets(text: str, query: str) -> list:
    """关键词定位：返回最多 3 个命中上下文片段（各 ±500 字）。"""
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
            snippets.append(ctx.replace("\n", " "))
        idx = pos + 1
        if len(snippets) >= 3:  # 最多3个片段
            break
    return snippets


@tool
def load_book_content(book_name: str, query: str = "", part: int = 1) -> str:
    """加载指定书籍的完整文本。
    book_name: 书籍名称。
    query: 可选，传入后按关键词定位返回上下文片段（最多3个，各500字）。
    part: 可选，页码（默认1）。正文超过 13000 字会自动分页（按卷打包），返回内容尾部会给出下一页码与对应的卷，长书需逐页读完。"""
    matches, early = _load_book_matches(book_name)
    if early is not None:
        return early
    best = max(matches, key=lambda b: len(b.get("text") or ""))
    text = best.get("text") or ""
    print(f"[工具] 加载书籍: {best.get('title', '')} ({len(text)}字)")
    pages = _book_pages(text)
    page_no = _select_book_page(pages, part)
    page_label, page_body = pages[page_no - 1]
    # 元数据放返回头部：卷数这类信息若沉在万字正文之后，模型很可能看不到。
    # （HX1 事故：问「第五卷」时把卷一正文当成第五卷回答，而元数据明写「共四卷」。）
    meta = best.get("metadata", {})
    meta_line = _book_meta_line(meta)
    page_line = _format_page_line(page_no, pages, page_label)

    # 卷号守卫：书名点名了「第N卷」而书里没有这一卷时，直接说清并给出卷目。
    # （HX1 事故：问「极星舞剧集·第五卷」，模型把加载到的卷一内容当成第五卷讲；
    #  元数据放到返回头部也不够，必须在工具层拦掉。）
    early, page_no = _apply_volume_guard(book_name, best.get("title", ""), text, meta, meta_line, pages, page_no)
    if early is not None:
        return early
    page_label, page_body = pages[page_no - 1]
    page_line = _format_page_line(page_no, pages, page_label)

    if query and query.strip():
        snippets = _book_keyword_snippets(text, query)
        if snippets:
            print(f'  [关键词定位] "{query}" -> {len(snippets)} 个片段')
            result = (
                f'\n【{best["title"]}】（共{len(text)}字）{meta_line}\n[关键词定位: "{query}", {len(snippets)}个片段]\n'
            )
            result += "\n---\n".join(snippets)
            return result
        return (
            f"\n【{best['title']}】{meta_line}{page_line}\n"
            f'[关键词"{query}"未在书中找到，返回第 {page_no} 页 = {page_label}]\n'
            f"{page_body}{_page_hint(best['title'], pages, page_no, len(text))}"
        )

    return (
        f"\n【{best['title']}】{meta_line}{page_line}\n{page_body}"
        f"{_page_hint(best['title'], pages, page_no, len(text))}"
    )


def _resolve_act_quests(quest_name: str):
    """解析幕名：幕→子任务映射 / 活动传说别名 / 任务知识库系列名，返回子任务列表或 None。"""
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
    return act_quests


def _collect_quest_matches(quest_name: str, act_quests):
    """扫描 content_data/quests_*.json，返回 [(标题, 分类, 正文)]。"""
    results = []
    for filename in os.listdir(CONTENT_DIR):
        if not filename.startswith("quests_") or not filename.endswith(".json"):
            continue
        if filename == "quests_processed.json":
            continue
        try:
            with open(os.path.join(CONTENT_DIR, filename), "r", encoding="utf-8") as f:
                quests = json.load(f)
        except (OSError, ValueError) as exc:
            print(f"[工具] 跳过无法解析的任务文件 {filename!r}: {type(exc).__name__}")
            continue
        if not isinstance(quests, list):
            continue
        for q in quests:
            title = (q.get("title") or "").strip() if isinstance(q, dict) else ""
            if not title:
                # 畸形记录：缺标题的外部 JSON 条目跳过。
                continue
            if act_quests:
                # 幕名匹配：检查子任务标题是否在映射列表中
                if title in act_quests:
                    results.append((title, q.get("category", ""), q.get("text", "")))
            else:
                # 标题匹配（原有逻辑）
                series = q.get("系列任务") or ""
                if quest_name in title or quest_name in series:
                    results.append((title, q.get("category", ""), q.get("text", "")))
    return results


def _rerank_chunks(query: str, top_chunks: list) -> list:
    """Reranker 精排：None 回退前 3 个，空列表表示无合格结果。"""
    reranked_idx = _rerank(query, top_chunks, top_n=3)
    if reranked_idx is None:
        return top_chunks[:3]
    if reranked_idx:
        return [top_chunks[i] for i in reranked_idx[:3]]
    return []


def _format_long_quest(title: str, cat: str, text: str, char_count: int, query: str) -> str:
    """已预处理长任务：有 query 走 BM25+Reranker，无 query 返回离线大纲。"""
    p = _quests_processed[title]
    if not (query and query.strip()):
        # 轨道 A：返回离线摘要
        return f"\n【{title}】（{cat}）\n[剧情大纲]\n{p['summary']}"
    # 轨道 B：BM25 粗召回 + Reranker 精排
    chunks = p.get("chunks_bm25") or p.get("chunks", [])
    if not chunks:
        # 无切片（异常情况），用前 9000 字
        return f"\n【{title}】（{cat}）\n{text[:9000]}"
    bm25 = SimpleBM25(chunks)
    # 多拿一些候选交给 Reranker 筛选
    top_chunks = bm25.search(query, top_k=8)
    if not top_chunks:
        # BM25 无命中，回退到大纲
        return f"\n【{title}】（{cat}）\n[剧情大纲]\n{p['summary']}"
    top_chunks = _rerank_chunks(query, top_chunks)
    print(f'  -> BM25+Reranker: "{query}" -> {len(top_chunks)} 个切片')
    chunk_texts = "\n\n---\n\n".join(top_chunks)
    return (
        f"\n【{title}】（{cat}）\n"
        f'[BM25+Reranker检索: "{query}", 共{char_count}字, 命中{len(top_chunks)}个切片]\n'
        f"{chunk_texts}"
    )


def _format_quest_entry(title: str, cat: str, text: str, query: str) -> str:
    """格式化单条任务：短/中文本直接返回，长文本走大纲或原文检索。"""
    char_count = len(text)
    if char_count <= 9000:
        # 短文本：>3000字注入结构索引头，<=3000字直接返回全文
        if char_count > 3000:
            text_with_header = _build_quest_header(text, char_count)
            return f"\n【{title}】（{cat}）\n{text_with_header}"
        return f"\n【{title}】（{cat}）\n{text}"
    if title in _quests_processed:
        return _format_long_quest(title, cat, text, char_count, query)
    # 未预处理的长任务（不应出现），返回前 9000 字
    return f"\n【{title}】（{cat}）\n{text[:9000]}\n...（共{char_count}字，仅显示前9000字）"


@tool
def load_quest_content(quest_name: str, query: str = "") -> str:
    """加载指定任务的剧情内容。长任务会自动返回大纲或按关键词检索原文片段。
    quest_name: 任务名称或幕名。支持玩家熟知的幕名（如\"虚空鼓动，劫火高扬\"），会自动匹配其下属子任务。
    query: 用户的具体问题或关键词（可选）。传入后会在长任务中精准检索相关原文片段，不走大纲。"""
    act_quests = _resolve_act_quests(quest_name)
    results = _collect_quest_matches(quest_name, act_quests)
    if not results:
        return f"未找到包含「{quest_name}」的任务。"

    _load_processed_data()
    print(f"[工具] 加载任务: {quest_name} -> {len(results)} 条匹配")

    output = []
    for title, cat, text in results[:4]:
        # 指定标题强制标注为活动剧情（活动剧情类数据本身已使用该分类名）
        if title in FORCE_ACTIVITY_TITLES:
            cat = "活动剧情"
        output.append(_format_quest_entry(title, cat, text, query))
    return "\n".join(output)

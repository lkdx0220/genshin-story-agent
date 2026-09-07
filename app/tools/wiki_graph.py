# -*- coding: utf-8 -*-
"""本地 wiki 词条链接图工具（轻量 GraphRAG 试点）。

数据源：kb_vectors/wiki_entry_graph.json（由 wiki_entry_graph.py 构建）。
设计：Agent 先 search 找到词条，再 expand 顺链接找相邻词条，最后 get 取定向片段。
不返回整篇 3 万字全文，避免撑爆 Plan 上下文；get 支持 focus 参数定位原文。
"""
import re
from typing import List, Optional

from langchain_core.tools import tool

from wiki_entry_graph import WikiEntryGraph, DEFAULT_OUTPUT, WikiEntry


# 模块级懒加载：第一次调用工具时才读 JSON，不影响启动速度。
_graph: Optional[WikiEntryGraph] = None


def _load_graph() -> WikiEntryGraph:
    global _graph
    if _graph is None:
        _graph = WikiEntryGraph.load(DEFAULT_OUTPUT)
    return _graph


def _format_header(entry: WikiEntry) -> str:
    parts = [f"词条ID: {entry.entry_id}", f"标题: {entry.title}", f"类型: {entry.entry_type}"]
    if entry.region:
        parts.append(f"地区: {entry.region}")
    if entry.filters:
        parts.append(f"分类: {'/'.join(entry.filters)}")
    return " | ".join(parts)


def _snippets_around(text: str, focus: str, max_snippets: int = 5, width: int = 300) -> List[str]:
    """返回文本中围绕 focus 出现的若干片段。"""
    if not focus:
        return []
    out: List[str] = []
    start = 0
    while len(out) < max_snippets:
        idx = text.find(focus, start)
        if idx < 0:
            break
        a = max(0, idx - width)
        b = min(len(text), idx + len(focus) + width)
        out.append(text[a:b].replace("\n", " ").strip())
        start = idx + max(1, len(focus))
    return out


@tool
def wiki_graph_search(keyword: str) -> str:
    """在本地 wiki 词条链接图里搜索词条。适合用户提到的专名/任务/地点/组织/物品找不到时，先定位词条ID。
    keyword: 要搜索的关键词（标题、别名、正文子串均可）"""
    graph = _load_graph()
    entries = graph.search(keyword, limit=15)
    if not entries:
        return f"本地 wiki 链接图里未找到与「{keyword}」相关的词条。"
    lines = [f"找到 {len(entries)} 个词条："]
    for e in entries:
        lines.append("- " + _format_header(e))
    return "\n".join(lines)


@tool
def wiki_graph_expand(entry_id: str) -> str:
    """列出某个 wiki 词条的链接邻居（出边 + 反向引用），用于顺着链接找地图文本/圣遗物/任务等相关词条。
    entry_id: 词条ID，来自 wiki_graph_search 返回的 ID"""
    graph = _load_graph()
    entry = graph.get(entry_id)
    if entry is None:
        return f"本地 wiki 链接图里没有词条ID {entry_id}。"
    links = entry.links
    back = graph.backlinks(entry_id)
    lines = [f"词条「{entry.title}」的链接邻居："]
    lines.append(f"[出边] {len(links)} 个（本词条链接到的）：")
    if not links:
        lines.append("  无")
    for i, (target, link) in enumerate(graph.expand(entry_id, limit=30), 1):
        status = "本地已收录" if target is not None else "本地未收录"
        if target is not None:
            lines.append(f"{i}. {link.target_name} (ID {link.target_id}, {target.entry_type}) [{status}]")
        else:
            lines.append(f"{i}. {link.target_name} (ID {link.target_id}) [{status}]")
    lines.append(f"[反向引用] {len(back)} 个（链接到本词条的）：")
    if not back:
        lines.append("  无")
    for i, (source, link) in enumerate(back, 1):
        lines.append(f"{i}. {source.title} (ID {source.entry_id}, {source.entry_type})")
    return "\n".join(lines)


@tool
def wiki_graph_get(entry_id: str, focus: str = "") -> str:
    """读取本地 wiki 词条的定向片段。给 focus 时返回含该词的多个上下文；不给 focus 时返回开头部分和链接列表。
    entry_id: 词条ID，来自 wiki_graph_search 返回的 ID
    focus: 可选，要定位的原文关键词（如角色名/地名/事件名）"""
    graph = _load_graph()
    entry = graph.get(entry_id)
    if entry is None:
        return f"本地 wiki 链接图里没有词条ID {entry_id}。"
    header = _format_header(entry)

    if focus:
        snippets = _snippets_around(entry.full_text, focus, max_snippets=5, width=300)
        if snippets:
            parts = [header, f"与「{focus}」相关的片段："]
            for i, s in enumerate(snippets, 1):
                parts.append(f"[片段{i}]\n{s}")
            return "\n\n".join(parts)
        # focus 命中不了时退化为开头 + 链接，便于模型换词再试。
        return f"{header}\n未在正文中找到「{focus}」，以下为词条开头：\n{entry.full_text[:1200]}"

    link_lines = [f"{l.target_name}({l.target_id})" for l in entry.links[:15]]
    link_text = "；".join(link_lines) if link_lines else "无"
    return (
        f"{header}\n"
        f"别名: {'/'.join(entry.aliases) if entry.aliases else '无'}\n"
        f"正文开头:\n{entry.full_text[:1500]}\n"
        f"内部链接({len(entry.links)}): {link_text}"
    )

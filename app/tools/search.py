# -*- coding: utf-8 -*-
"""Search 类工具：活动剧情搜索、首次提及溯源。

辅助函数 search_all / search_lore 保留供内部使用（已通过 hybrid_search 替代主搜索路径）。
"""
import os
import re
import json

from langchain_core.tools import tool

from app.config import CONTENT_DIR
from app.data import (
    角色知识库, 地区知识库, 主线剧情知识库, 武器知识库, 任务知识库,
    圣遗物知识库, _npcs_data, _match_all_in, _load_content_json,
)
from app.formatters import (
    _format_role_info, _format_region_info, _format_story_info,
)
from app.retrieval import _expand_query_with_aliases, _rerank

def _quest_files(sorted_by_activity: bool = False) -> list:
    """任务类原始文件列表。

    sorted_by_activity=True 时把活动文件排前面（search_activity 原文口径）；
    默认保持 os.listdir 原始顺序（find_first_mention 依赖此顺序的最后排序稳定性）。
    """
    files = [f for f in os.listdir(CONTENT_DIR) if f.startswith("quests_") and f.endswith(".json")]
    if sorted_by_activity:
        files.sort(key=lambda f: (0 if "活动" in f else 1, f))
    return files


def _load_quest_list(filename: str) -> list:
    """读单个 quests_*.json，失败或非列表返回空列表。"""
    # 文件名只来自 os.listdir：这里做防御性归一——basename 必须与原值相同（不含目录成分）、
    # 只读 .json；静态分析据此可确认拼进路径的字符集与基目录都受控。
    safe_name = os.path.basename(filename or "")
    if safe_name != filename or not safe_name.endswith(".json") or ".." in safe_name:
        return []
    path = os.path.join(CONTENT_DIR, safe_name)
    try:
        with open(path, "r", encoding="utf-8") as f:
            quests = json.load(f)
    except Exception:
        return []
    return quests if isinstance(quests, list) else []


def _activity_snippet(text: str, matched_term: str) -> str:
    """用匹配到的词定位片段（前 80、后 120 字）。

    防御：空/纯空白词会让 split()[0] 抛 IndexError，直接返回空片段而不是打穿整个工具。
    """
    if not matched_term or not matched_term.split():
        return ""
    first_kw = matched_term.split()[0]
    idx = text.find(first_kw)
    start = max(0, idx - 80)
    end = min(len(text), idx + len(matched_term) + 120)
    return text[start:end].replace('\n', ' ').strip()


def _activity_matches(keyword: str, activity_name: str) -> list:
    """扫活动/任务文件，返回 [{title, category, snippet}]。"""
    results = []
    search_terms = _expand_query_with_aliases(keyword)
    for filename in _quest_files(sorted_by_activity=True):
        quests = _load_quest_list(filename)
        for q in quests:
            title = (q.get("title") or "").strip()
            if not title:
                # 畸形记录：缺标题的外部 JSON 条目跳过，不让 KeyError / 空标题进入结果。
                continue
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
                category = q.get("category", "")
                # 在标题、元数据各字段中查找活动名
                meta_text = json.dumps(meta, ensure_ascii=False)
                if activity_name not in title and activity_name not in meta_text and activity_name not in category:
                    continue

            results.append({
                "title": title,
                "category": q.get("category", ""),
                "snippet": _activity_snippet(text, matched_term),
            })
    return results


def _rerank_or_all(keyword: str, docs: list, results: list, top_n: int = 10) -> list:
    """Reranker 重排：rerank 不可用（None）取前 top_n，返回空则结果为空。"""
    reranked = _rerank(keyword, docs, top_n=top_n)
    if reranked is None:
        return results[:top_n]
    if reranked:
        # reranker 可能返回越界/非整数下标：过滤后才索引，全部非法时退回未重排结果。
        valid = [i for i in reranked if isinstance(i, int) and 0 <= i < len(results)]
        return [results[i] for i in valid] if valid else results[:top_n]
    return []


def _render_activity_results(keyword: str, activity_name: str, total: int, ordered: list) -> str:
    """渲染活动搜索结果。"""
    lines = [
        f"\n===== 活动剧情搜索「{keyword}」"
        + (f"（{activity_name}）" if activity_name else "")
        + f" ({total}条候选，取前{len(ordered)}条) ====="
    ]
    for r in ordered:
        lines.append(f"\n【{r['title']}】（{r['category']}）\n  片段: ...{r['snippet']}...")
    return "\n".join(lines)


@tool
def search_activity(keyword: str, activity_name: str = "") -> str:
    """搜索活动剧情中的对话内容。当用户提到具体活动名（如\"风花的呼吸\"、\"海灯节\"）时，优先用此工具代替 search_all。
    keyword: 要在活动剧情中搜索的关键词（如角色名、概念、台词片段）
    activity_name: 活动名称（可选）。提供后只搜索该活动相关页面；不提供则搜索所有活动类内容。"""
    results = _activity_matches(keyword, activity_name)

    if not results:
        hint = f"（限定活动「{activity_name}」）" if activity_name else ""
        return f"未在活动剧情中找到与「{keyword}」相关的内容。{hint}"

    print(f"[工具] 活动搜索: {keyword}" + (f" @ {activity_name}" if activity_name else "") + f" -> {len(results)}条")

    # Reranker 重排序
    rerank_docs = [f"【{r['title']}】{r['snippet']}" for r in results]
    ordered = _rerank_or_all(keyword, rerank_docs, results, top_n=10)
    return _render_activity_results(keyword, activity_name, len(results), ordered)


def _mention_version(metadata: dict) -> tuple:
    """(排序用版本号, 原始版本字符串)；解析失败记 999.0。"""
    version_str = metadata.get("所属版本", "")
    try:
        version = float(version_str) if version_str else 999.0
    except ValueError:
        version = 999.0
    return version, version_str


def _mention_scan(keyword: str, type_priority: dict) -> list:
    """扫全部任务文件（保持 os.listdir 顺序），收集所有提及，供后续按版本排序。"""
    if not keyword or not keyword.split():
        # 空/纯空白关键词会让下面的 keyword.split()[0] 抛 IndexError。
        return []
    all_matches = []
    for filename in _quest_files():
        if filename == "quests_processed.json":
            continue
        for q in _load_quest_list(filename):
            title = (q.get("title") or "").strip()
            if not title:
                # 畸形记录：缺标题的外部 JSON 条目跳过。
                continue
            text = q.get("text", "")
            if not _match_all_in(keyword, text):
                continue
            category = q.get("category", "")
            metadata = q.get("metadata", {})
            version, version_str = _mention_version(metadata)
            # 多词时用第一个词定位
            first_kw = keyword.split()[0]
            idx = text.find(first_kw)
            start = max(0, idx - 100)
            end = min(len(text), idx + len(keyword) + 100)
            snippet = text[start:end].replace('\n', ' ').strip()
            speaker_match = re.search(r'\*「?([^」\n：]{1,10})」?：', text[max(0, idx-200):idx+50])
            all_matches.append({
                "title": title, "category": category,
                "version": version, "version_str": version_str,
                "priority": type_priority.get(category, 99),
                "speaker": speaker_match.group(1) if speaker_match else "未知",
                "snippet": snippet,
                "chapter_name": metadata.get("chapter_name", ""),
                "act_name": metadata.get("act_name", ""),
            })
    return all_matches


def _render_first_mention(first: dict, total: int, others: list) -> str:
    """渲染首次提及（章/幕层级 + 说话角色 + 其他提及位置）。"""
    result = "\n【首次提及位置】\n"
    # 构建层级信息（章/幕）
    hierarchy_parts = []
    if first.get("chapter_name") and first["chapter_name"] != "开场动画":
        hierarchy_parts.append(first["chapter_name"])
    if first.get("act_name"):
        hierarchy_parts.append(first["act_name"])
    if first.get("chapter_name") == "开场动画":
        hierarchy_parts.append("开场动画")
    hierarchy_str = "，".join(hierarchy_parts) + "，" if hierarchy_parts else ""
    if first['version_str']:
        result += f"任务: {first['title']}（{first['category']}，{hierarchy_str}版本 {first['version_str']}）\n"
    else:
        result += f"任务: {first['title']}（{first['category']}）\n"
    result += f"说话角色: {first['speaker']}\n"
    result += f"原文片段: 「...{first['snippet']}...」\n"
    if total > 1:
        result += f"\n其他提及位置（共{total}处）:\n"
        for m in others:
            v = f"（版本 {m['version_str']}）" if m['version_str'] else ""
            result += f"  - {m['title']}（{m['category']}）{v}\n"
    return result


@tool
def find_first_mention(keyword: str) -> str:
    """
    查找某个关键词在剧情文本中首次出现的位置。按游戏版本号排序，优先返回版本最早的任务。
    keyword: 要查找的关键词（如\"降临者\"、\"深渊\"）
    """
    print(f"[工具] 查找首次提及: {keyword!r}")
    if not keyword or not keyword.strip():
        return "请提供要查找的关键词。"
    type_priority = {"魔神任务": 1, "传说任务": 2, "世界任务": 3, "部族纪闻": 4, "邀约事件": 5}
    all_matches = _mention_scan(keyword, type_priority)
    if not all_matches:
        return f"未在任务剧情中找到「{keyword}」的提及。"
    all_matches.sort(key=lambda x: (x["version"], x["priority"]))
    first = all_matches[0]
    return _render_first_mention(first, len(all_matches), all_matches[1:6])


# ====== 全局检索工具（任务未命中后的确定性兜底）======
# search_all 已注册为正式工具：当 query_quest/load_quest_content 未命中任务名时，
# 代码层会先强制调用它做全局检索；search_lore 仍保留为内部辅助函数。

def _search_roles(query: str) -> list:
    """角色知识库：角色名称/称号/身份任一命中，输出 ("角色", 文本)。"""
    results = []
    for role in 角色知识库:
        if (_match_all_in(query, role.get("角色名称", ""))
                or _match_all_in(query, role.get("称号", ""))
                or _match_all_in(query, str(role.get("身份", [])))):
            results.append(("角色", _format_role_info(role)))
    return results


def _search_regions(query: str) -> list:
    """地区知识库：地区名称/神明任一命中。"""
    results = []
    for region in 地区知识库:
        if _match_all_in(query, region.get("地区名称", "")) or _match_all_in(query, region.get("神明", "")):
            results.append(("地区", _format_region_info(region)))
    return results


def _search_story_arcs(query: str) -> list:
    """主线剧情知识库：章节名称/章节编号/所属地区任一命中。"""
    results = []
    for arc in 主线剧情知识库:
        if (_match_all_in(query, arc.get("章节名称", ""))
                or _match_all_in(query, arc.get("章节编号", ""))
                or _match_all_in(query, arc.get("所属地区", ""))):
            results.append(("剧情", _format_story_info(arc)))
    return results


def _search_weapons(query: str) -> list:
    """武器知识库：武器名称/关联角色任一命中。"""
    results = []
    for wpn in 武器知识库:
        name = (wpn.get("武器名称") or "").strip()
        if not name:
            # 畸形记录：缺名称条目跳过（原来直接下标会 KeyError）。
            continue
        if _match_all_in(query, name) or _match_all_in(query, wpn.get("关联角色", "")):
            try:
                rarity = int(wpn.get("稀有度") or 0)
            except (TypeError, ValueError):
                rarity = 0
            results.append(("武器", f"\n【{name}】{'★'*rarity} {wpn.get('武器类型')}"))
    return results


def _search_artifacts(query: str) -> list:
    """圣遗物知识库：圣遗物名称命中。"""
    results = []
    for art in 圣遗物知识库:
        name = (art.get("圣遗物名称") or "").strip()
        if not name:
            # 畸形记录：缺名称条目跳过（原来直接下标会 KeyError）。
            continue
        if _match_all_in(query, name):
            effect = (art.get("两件套效果") or "?")[:60]
            results.append(("圣遗物", f"\n【{name}】{art.get('稀有度','')}星 | 两件套: {effect}"))
    return results


def _search_quest_metadata(query: str) -> list:
    """任务元数据：任务名称/关联角色/系列任务/所属角色/章名/幕名任一命中。"""
    results = []
    for q in 任务知识库:
        meta = q.get("metadata", {}) or {}
        if (_match_all_in(query, q.get("任务名称", ""))
                or _match_all_in(query, q.get("关联角色", ""))
                or _match_all_in(query, q.get("系列任务", ""))
                or _match_all_in(query, q.get("所属角色", ""))
                or _match_all_in(query, str(meta.get("chapter_name", "")))
                or _match_all_in(query, str(meta.get("act_name", "")))):
            results.append(("任务", f"\n【{q['任务名称']}】{q.get('任务类型','')} | 关联: {q.get('关联角色','')} | {q.get('简介','')[:100]}"))
    return results


def _search_concepts(query: str) -> list:
    """概念类条目：名称/正文+章节任一命中，取正文前 500 字做预览。"""
    results = []
    concepts = _load_content_json("concepts")
    for c in concepts:
        name = c.get("名称", "")
        text_body = c.get("正文", "") + str(c.get("章节", {}))
        if _match_all_in(query, name) or _match_all_in(query, text_body):
            preview = text_body[:500].replace('\n', ' ')
            results.append(("概念", f"\n【{name}】（{c.get('类型','')}）\n  {preview}..."))
    return results


def _search_monsters(query: str) -> list:
    """怪物条目：名称/别称任一命中。"""
    results = []
    monsters = _load_content_json("monsters")
    for m in monsters:
        if _match_all_in(query, m.get("名称", "")) or _match_all_in(query, m.get("别称", "")):
            results.append(("怪物", f"\n【{m.get('名称','')}】{m.get('怪物类型','')} | {m.get('元素属性','')}"))
    return results


def _search_simple_content(query: str) -> list:
    """材料/食谱/食物/采集物条目：按名称命中，输出格式保持原口径。"""
    results = []
    for mat in _load_content_json("materials"):
        if _match_all_in(query, mat.get("名称", "")):
            results.append(("材料", f"\n【{mat.get('名称','')}】{mat.get('类型','')} | {mat.get('用途','')[:100]}"))
    for r in _load_content_json("recipes"):
        if _match_all_in(query, r.get("名称", "")):
            results.append(("食谱", f"\n【{r.get('名称','')}】{r.get('类型','')} | {r.get('效果','')[:80]}"))
    for fd in _load_content_json("foods"):
        if _match_all_in(query, fd.get("名称", "")):
            results.append(("食物", f"\n【{fd.get('名称','')}】{fd.get('类型','')} | {fd.get('效果','')[:80]}"))
    for col in _load_content_json("collectibles"):
        if _match_all_in(query, col.get("名称", "")):
            results.append(("采集物", f"\n【{col.get('名称','')}】"))
    return results


def _search_books(query: str) -> list:
    """书籍条目：标题/正文任一命中，输出带卷数与来源。"""
    results = []
    books = _load_content_json("books")
    for b in books:
        if _match_all_in(query, b.get("title", "")) or _match_all_in(query, b.get("text", "")):
            vol_count = b.get("metadata", {}).get("卷数", "")
            results.append(("书籍", f"\n【{b.get('title', '')}】（{vol_count}）| 来源: {b.get('source','')}"))
    return results


def _append_quest_file_candidates(filename: str, search_terms: list, candidates: list, seen_candidates: set) -> None:
    """扫单个 quests_*.json：搜索词/别名命中即收候选（单文件最多 5 条、总量上限 200 条），原地追加。"""
    file_count = 0
    for q in _load_quest_list(filename):
        if file_count >= 5 or len(candidates) >= 200:  # 激进上限，防止极端情况
            break
        title = (q.get("title") or "").strip()
        if not title:
            # 畸形记录：缺标题的外部 JSON 条目跳过。
            continue
        text = q.get("text", "")
        # 用所有搜索词（含别名）尝试匹配
        matched_term = None
        for term in search_terms:
            if _match_all_in(term, text):
                matched_term = term
                break
        if matched_term and matched_term.split():
            # 用匹配到的词定位片段
            first_word = matched_term.split()[0]
            idx = text.find(first_word)
            start = max(0, idx - 120)
            end = min(len(text), idx + len(matched_term) + 120)
            snippet = text[start:end].replace('\n', ' ').strip()
            category = q.get('category', '')
            # 去重: (title, category, snippet 前 60 字)
            dedup_key = (title, category, snippet[:60])
            if dedup_key not in seen_candidates:
                seen_candidates.add(dedup_key)
                result_str = f"\n【{title}】（{category}）\n  匹配片段: ...{snippet}..."
                candidates.append((title, category, snippet, result_str))
                file_count += 1


def _collect_quest_content_candidates(search_terms: list) -> list:
    """扫全部 quests_*.json 收集任务内容候选（保持 os.listdir 顺序，总量上限 200 条）。"""
    candidates = []
    seen_candidates = set()
    for filename in _quest_files():
        if filename == "quests_processed.json":
            continue
        if len(candidates) >= 200:  # 激进上限，防止极端情况
            break
        _append_quest_file_candidates(filename, search_terms, candidates, seen_candidates)
    return candidates


def _rerank_quest_content_candidates(query: str, candidates: list) -> list:
    """任务内容候选重排：rerank 不可用（None）取前 10，返回空则丢弃全部弱命中。"""
    if not candidates:
        return []
    # 带标题上下文，帮助区分不同意图；用原始 query 做 Rerank（保持语义精度）
    rerank_docs = [f"【{c[0]}】{c[2]}" for c in candidates]
    reranked = _rerank(query, rerank_docs, top_n=10)
    if reranked is None:
        return [("任务内容", c[3]) for c in candidates[:10]]
    if reranked:
        return [("任务内容", candidates[idx][3]) for idx in reranked]
    # rerank 已执行但全部低于阈值，不输出弱任务结果
    return []


def _render_search_all_results(query: str, all_results: list) -> str:
    """无命中直接返回提示；否则打印命中计数并拼接全部结果文本。"""
    if not all_results:
        return f"未找到与「{query}」相关的任何内容。"
    print(f"[工具] 全局搜索: {query} -> {len(all_results)}条结果")
    lines = [f"\n===== 搜索「{query}」({len(all_results)}条结果) ====="]
    for _cat, text in all_results:
        lines.append(text)
    return "\n".join(lines)


@tool
def search_all(query: str) -> str:
    """全局搜索原神知识库，在角色、地区、剧情、武器、任务、概念、怪物、材料、书籍、食谱、食物、采集物、任务内容中模糊匹配。query: 搜索关键词"""
    all_results = []
    # 结构化知识库：角色、地区、主线剧情、武器、圣遗物、任务元数据
    all_results.extend(_search_roles(query))
    all_results.extend(_search_regions(query))
    all_results.extend(_search_story_arcs(query))
    all_results.extend(_search_weapons(query))
    all_results.extend(_search_artifacts(query))
    all_results.extend(_search_quest_metadata(query))
    # content_data 条目：概念、怪物、材料、食谱、食物、采集物、书籍
    all_results.extend(_search_concepts(query))
    all_results.extend(_search_monsters(query))
    all_results.extend(_search_simple_content(query))
    all_results.extend(_search_books(query))

    # 任务内容全文搜索 - 收集候选后用 Reranker 重排序
    # 术语别名扩展：同一实体可能有多种称呼，都作为搜索词尝试
    search_terms = _expand_query_with_aliases(query)
    quest_candidates = _collect_quest_content_candidates(search_terms)
    all_results.extend(_rerank_quest_content_candidates(query, quest_candidates))

    return _render_search_all_results(query, all_results)


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
            # 标题也是命中源：地图文本类条目的地区/子区域名只写在标题里
            # （如"地图文本/稻妻 / 清籁岛"），正文只有碎片内容。
            if term in entry["title"] or term in entry["text"]:
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
    if reranked is None:
        ordered = candidates[:8]
    elif reranked:
        ordered = [candidates[i] for i in reranked]
    else:
        ordered = []

    lines = [f"\n===== 世界观设定「{keyword}」({len(candidates)}条候选，取前{len(ordered)}条) ====="]
    for c in ordered:
        lines.append(f"\n【{c['title']}】（{c['source']}）\n  {c['text']}")
    return "\n".join(lines)




def _search_lore_snippets(keyword: str, max_candidates: int = 5, snippet_chars: int = 400) -> str:
    """轻量世界观搜索：只返回命中条目标题与关键片段，避免把整段 lore 灌给 LLM。"""
    lore_path = os.path.join(CONTENT_DIR, "lore.json")
    if not os.path.exists(lore_path):
        return "世界观设定数据(lore.json)尚未构建。"
    try:
        with open(lore_path, "r", encoding="utf-8") as f:
            lore = json.load(f)
    except Exception:
        return "世界观设定数据读取失败。"
    if not lore:
        return "世界观设定数据为空。"

    search_terms = [keyword]
    if len(keyword) >= 3:
        search_terms.append(keyword[:-1])
    if len(keyword) >= 4:
        search_terms.append(keyword[:-2])

    candidates = []
    seen = set()
    for term in search_terms:
        for entry in lore:
            # 标题也是命中源（命中在标题时下面 idx<0 会退化为从头截片段）。
            if term in entry["title"] or term in entry["text"]:
                key = entry["title"] + entry["text"][:40]
                if key not in seen:
                    seen.add(key)
                    candidates.append((entry, term))
                if len(candidates) >= max_candidates * 2:
                    break
        if len(candidates) >= max_candidates * 2:
            break

    if not candidates:
        return f"在世界观设定中未找到与「{keyword}」相关的内容。"

    lines = [f"===== 世界观设定「{keyword}」({len(candidates)}条候选) ====="]
    for entry, term in candidates[:max_candidates]:
        text = entry["text"]
        idx = text.find(term)
        if idx < 0:
            idx = 0
        start = max(0, idx - snippet_chars // 2)
        end = min(len(text), idx + len(term) + snippet_chars // 2)
        snippet = text[start:end].replace(chr(10), " ")
        lines.append(f"【{entry['title']}】（{entry.get('source', '')}）")
        lines.append(f"  ...{snippet}...")
        lines.append("")
    return chr(10).join(lines)



def _snippet_around(text: str, keyword: str, width: int = 300) -> str:
    """返回 keyword 在 text 中命中的前后片段，用于武器/圣遗物/书籍等长文本。"""
    idx = text.find(keyword)
    if idx < 0:
        idx = 0
    start = max(0, idx - width // 2)
    end = min(len(text), idx + len(keyword) + width // 2)
    snippet = text[start:end].replace(chr(10), " ")
    return f"...{snippet}..."


# ====== 世界/组织背景补充检索工具（不在初始工具集中，搜索碰壁后才暴露） ======

def _world_npc_hay(name, npc, fields: tuple) -> str:
    """NPC 匹配文本：名字 + 指定字段值；非 dict 时字段位留空（与原口径一致，含分隔空格）。"""
    parts = [str(name)]
    for key in fields:
        parts.append(str(npc.get(key, "")) if isinstance(npc, dict) else "")
    return " ".join(parts)


def _world_npc_formatted(name, npc):
    """NPC 多行展示：组织/种族/职业/地区任一存在才输出；非 dict 返回 None。"""
    if not isinstance(npc, dict):
        return None
    org = npc.get("org") or npc.get("所属组织") or npc.get("org_race") or ""
    race = npc.get("race") or npc.get("种族") or ""
    occupation = npc.get("occupation") or npc.get("职业") or ""
    region = npc.get("region") or npc.get("所在国家") or ""
    lines = [f"【{name}】"]
    if org:
        lines.append(f"所属组织: {org}")
    if race:
        lines.append(f"种族: {race}")
    if occupation:
        lines.append(f"职业: {occupation}")
    if region:
        lines.append(f"地区: {region}")
    return chr(10).join(lines)


def _world_weapon_snippets(query: str) -> list:
    """武器故事：名称/简介/武器故事任一含 query，则取武器故事四周片段。"""
    hits = []
    for wpn in 武器知识库:
        if not isinstance(wpn, dict):
            continue
        weapon_hay = " ".join([
            str(wpn.get("武器名称", "")),
            str(wpn.get("简介", "")),
            str(wpn.get("武器故事", "")),
        ])
        if query in weapon_hay:
            story = str(wpn.get("武器故事", ""))
            hits.append(
                "【武器】" + str(wpn.get("武器名称", "")) + chr(10)
                + _snippet_around(story, query, 400)
            )
    return hits


def _world_artifact_snippets(query: str) -> list:
    """圣遗物部位故事：逐个部位故事查 query，命中即取该部位片段。"""
    hits = []
    for art in 圣遗物知识库:
        if not isinstance(art, dict):
            continue
        stories = art.get("部位故事", {})
        if isinstance(stories, dict):
            for key, val in stories.items():
                if query in str(val):
                    hits.append(
                        "【圣遗物】" + str(art.get("圣遗物名称", "")) + " · " + str(key) + chr(10)
                        + _snippet_around(str(val), query, 400)
                    )
    return hits


def _world_book_snippets(query: str) -> list:
    """书籍：标题/正文任一含 query，则取正文四周片段。"""
    hits = []
    for book in _load_content_json("books"):
        if not isinstance(book, dict):
            continue
        book_hay = " ".join([
            str(book.get("title", "")),
            str(book.get("text", "")),
        ])
        if query in book_hay:
            hits.append(
                "【书籍】" + str(book.get("title", "")) + chr(10)
                + _snippet_around(str(book.get("text", "")), query, 400)
            )
    return hits


def _world_npc_hits_from_data(query: str) -> list:
    """npcs_processed.json（_npcs_data）：名字/组织/种族/职业字段命中即收集。"""
    hits = []
    for name, npc in _npcs_data.items():
        text = _world_npc_hay(name, npc, ("org", "所属组织", "org_race", "职业", "occupation"))
        if query not in text:
            continue
        fmt = _world_npc_formatted(name, npc)
        if fmt:
            hits.append(fmt)
    return hits


def _world_npc_hits_from_wiki(query: str, existing: list) -> list:
    """npcs_wiki_details.json（含更完整 org_race）：与 existing 去重后收集；文件缺失/损坏视为无数据。"""
    wiki_path = os.path.join(CONTENT_DIR, "npcs_wiki_details.json")
    if not os.path.exists(wiki_path):
        return []
    try:
        with open(wiki_path, "r", encoding="utf-8") as f:
            wiki_npcs = json.load(f)
    except Exception:
        wiki_npcs = {}
    if not isinstance(wiki_npcs, dict):
        return []
    hits = []
    for name, npc in wiki_npcs.items():
        text = _world_npc_hay(name, npc, ("org_race", "origin", "region"))
        if query not in text:
            continue
        fmt = _world_npc_formatted(name, npc)
        if fmt and fmt not in existing and fmt not in hits:
            hits.append(fmt)
    return hits


def _world_npc_hits(query: str) -> list:
    """NPC/组织命中清单：先 npcs_processed，再接 wiki 明细（同名同格式去重）。"""
    hits = _world_npc_hits_from_data(query)
    hits.extend(_world_npc_hits_from_wiki(query, hits))
    return hits


@tool
def search_world(query: str) -> str:
    """搜索世界观设定与组织/NPC背景（lore.json + NPC所属组织）。

    这是 search_all 的补充工具：search_all 不覆盖 lore.json 和 NPC 所属组织字段，
    当普通全局搜索碰壁时再暴露给 Planner 使用。
    """
    results = []

    # 1) lore.json 世界观设定（只用关键片段，避免长文本塞满上下文）
    lore_text = _search_lore_snippets(query)
    if not lore_text.startswith("在世界观设定中未找到"):
        results.append(lore_text)

    # 2) 武器故事 / 圣遗物故事 / 书籍正文（卢契烬等组织名常出现在这些长文本里）
    results.extend(_world_weapon_snippets(query))
    results.extend(_world_artifact_snippets(query))
    results.extend(_world_book_snippets(query))

    # 3) NPC / 组织字段
    npc_hits = _world_npc_hits(query)
    if npc_hits:
        results.append("相关组织/NPC：" + chr(10) + chr(10).join(npc_hits[:20]))

    if not results:
        return f"在世界观设定与组织/NPC数据中未找到与「{query}」相关的内容。"
    return chr(10).join(results)


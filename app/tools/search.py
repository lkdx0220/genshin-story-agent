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
    圣遗物知识库, _npcs_data, _match_all_in, _load_content_json, _normalize_for_match,
)
from app.formatters import (
    _format_role_info, _format_region_info, _format_story_info,
)
from app.retrieval import _expand_query_with_aliases, _rerank


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
    if reranked is None:
        ordered = results[:10]
    elif reranked:
        ordered = [results[i] for i in reranked]
    else:
        ordered = []

    lines = [f"\n===== 活动剧情搜索「{keyword}」" + (f"（{activity_name}）" if activity_name else "") + f" ({len(results)}条候选，取前{len(ordered)}条) ====="]
    for r in ordered:
        lines.append(f"\n【{r['title']}】（{r['category']}）\n  片段: ...{r['snippet']}...")
    return "\n".join(lines)


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


# ====== 全局检索工具（任务未命中后的确定性兜底）======
# search_all 已注册为正式工具：当 query_quest/load_quest_content 未命中任务名时，
# 代码层会先强制调用它做全局检索；search_lore 仍保留为内部辅助函数。

@tool
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
        if _match_all_in(query, arc.get("章节名称", "")) or _match_all_in(query, arc.get("章节编号", "")) or _match_all_in(query, arc.get("所属地区", "")):
            all_results.append(("剧情", _format_story_info(arc)))
    # 武器
    for wpn in 武器知识库:
        if _match_all_in(query, wpn.get("武器名称", "")) or _match_all_in(query, wpn.get("关联角色", "")):
            all_results.append(("武器", f"\n【{wpn['武器名称']}】{'★'*wpn['稀有度']} {wpn.get('武器类型')}"))
    # 圣遗物
    for art in 圣遗物知识库:
        if _match_all_in(query, art.get("圣遗物名称", "")):
            all_results.append(("圣遗物", f"\n【{art['圣遗物名称']}】{art.get('稀有度','')}星 | 两件套: {art.get('两件套效果','?')[:60]}"))
    # 任务元数据（含系列任务/章幕/所属角色，确保“全局检索”能覆盖任务体系字段）
    for q in 任务知识库:
        meta = q.get("metadata", {}) or {}
        if (_match_all_in(query, q.get("任务名称", ""))
                or _match_all_in(query, q.get("关联角色", ""))
                or _match_all_in(query, q.get("系列任务", ""))
                or _match_all_in(query, q.get("所属角色", ""))
                or _match_all_in(query, str(meta.get("chapter_name", "")))
                or _match_all_in(query, str(meta.get("act_name", "")))):
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
        if reranked is None:
            for c in quest_candidates[:10]:
                all_results.append(("任务内容", c[3]))
        elif reranked:
            for idx in reranked:
                all_results.append(("任务内容", quest_candidates[idx][3]))
        # else: rerank 已执行但全部低于阈值，不输出弱任务结果

    if not all_results:
        return f"未找到与「{query}」相关的任何内容。"

    print(f"[工具] 全局搜索: {query} -> {len(all_results)}条结果")
    lines = [f"\n===== 搜索「{query}」({len(all_results)}条结果) ====="]
    for cat, text in all_results:
        lines.append(text)
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
            results.append(
                "【武器】" + str(wpn.get("武器名称", "")) + chr(10)
                + _snippet_around(story, query, 400)
            )

    for art in 圣遗物知识库:
        if not isinstance(art, dict):
            continue
        art_hay = str(art.get("圣遗物名称", ""))
        stories = art.get("部位故事", {})
        if isinstance(stories, dict):
            for key, val in stories.items():
                if query in str(val):
                    results.append(
                        "【圣遗物】" + str(art.get("圣遗物名称", "")) + " · " + str(key) + chr(10)
                        + _snippet_around(str(val), query, 400)
                    )

    for book in _load_content_json("books"):
        if not isinstance(book, dict):
            continue
        book_hay = " ".join([
            str(book.get("title", "")),
            str(book.get("text", "")),
        ])
        if query in book_hay:
            results.append(
                "【书籍】" + str(book.get("title", "")) + chr(10)
                + _snippet_around(str(book.get("text", "")), query, 400)
            )

    # 3) NPC / 组织字段
    def _npc_formatted(name, npc):
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

    npc_hits = []
    # 3) npcs_processed.json（已加载到 _npcs_data）
    for name, npc in _npcs_data.items():
        text = " ".join([
            str(name),
            str(npc.get("org", "")) if isinstance(npc, dict) else "",
            str(npc.get("所属组织", "")) if isinstance(npc, dict) else "",
            str(npc.get("org_race", "")) if isinstance(npc, dict) else "",
            str(npc.get("职业", "")) if isinstance(npc, dict) else "",
            str(npc.get("occupation", "")) if isinstance(npc, dict) else "",
        ])
        if query in text:
            fmt = _npc_formatted(name, npc)
            if fmt:
                npc_hits.append(fmt)

    # 4) npcs_wiki_details.json（含更完整的 org_race）
    wiki_path = os.path.join(CONTENT_DIR, "npcs_wiki_details.json")
    if os.path.exists(wiki_path):
        try:
            with open(wiki_path, "r", encoding="utf-8") as f:
                wiki_npcs = json.load(f)
        except Exception:
            wiki_npcs = {}
        if isinstance(wiki_npcs, dict):
            for name, npc in wiki_npcs.items():
                text = " ".join([
                    str(name),
                    str(npc.get("org_race", "")) if isinstance(npc, dict) else "",
                    str(npc.get("origin", "")) if isinstance(npc, dict) else "",
                    str(npc.get("region", "")) if isinstance(npc, dict) else "",
                ])
                if query in text:
                    fmt = _npc_formatted(name, npc)
                    if fmt and fmt not in npc_hits:
                        npc_hits.append(fmt)

    if npc_hits:
        results.append("相关组织/NPC：" + chr(10) + chr(10).join(npc_hits[:20]))

    if not results:
        return f"在世界观设定与组织/NPC数据中未找到与「{query}」相关的内容。"
    return chr(10).join(results)


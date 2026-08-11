# -*- coding: utf-8 -*-
"""List 类工具：目录/列表查询（角色筛选、各类总览目录、台词统计）。

list_all_* 系列使用 24h TTL 缓存避免重复生成大目录文本。
"""
import os
import re
import json
import time as _time

from langchain_core.tools import tool

from app.config import CONTENT_DIR
from app.data import (
    角色知识库, 地区知识库, 武器知识库, 任务知识库, 圣遗物知识库,
    _load_content_json,
)
from app.progress import _emit_progress
from character_aliases import resolve_aliases

# 缓存目录常量
_DIR_CACHE_TTL = 86400  # 24 小时

# ====== 全角色任务目录缓存 ======
_quest_series_cache = None
_quest_series_cache_time = 0.0
_QUEST_SERIES_CACHE_TTL = 86400  # 24 小时


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


@tool
def list_all_quest_series() -> str:
    """列出知识库中所有有"系列任务"的剧情条目，按角色分组，显示章节/幕/子任务结构。
    仅返回层级骨架（不含剧情文本、任务描述、奖励等详情），token 消耗极低。
    使用场景：用户要求"列出全部传说任务"、"每个角色有几章任务"、"全角色任务目录"等总览类查询。
    详情查询请使用 query_quest(角色名) 获取单个角色的子任务列表。"""
    global _quest_series_cache, _quest_series_cache_time
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
_books_cache = None
_books_cache_time = 0.0
_lore_cache = None
_lore_cache_time = 0.0
_weapon_artifact_cache = None
_weapon_artifact_cache_time = 0.0
_items_cache = None
_items_cache_time = 0.0


def _cache_get(cache_var, cache_time: float):
    """检查缓存是否有效：cache_var 不为 None 且未过期（24h）。"""
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

    result = "\n".join(lines)
    _items_cache = result
    _items_cache_time = _time.time()
    print("[工具] 游戏物品目录（已生成）")
    return result


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

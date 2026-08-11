# -*- coding: utf-8 -*-
"""工具子包：聚合所有 @tool 工具，导出统一列表和按名映射。

按类别分文件：
- query.py: 具体实体查询（角色/地区/剧情/武器/任务/怪物/圣遗物/材料/采集物/食谱/食物/书籍元数据）
- list.py: 目录/列表查询（角色筛选、各类总览目录、台词统计）
- search.py: 搜索（活动剧情、首次提及）+ 内部辅助函数（search_all/search_lore）
- content.py: 加载完整内容（书籍/任务剧情）

注：hybrid_search 和 kb_vector_search 在 app/retrieval.py 中（与检索逻辑耦合）。
"""
from app.tools.query import (
    query_character, query_region, query_story, query_weapon, query_quest,
    query_monster, query_artifact, query_material, query_collectible,
    query_recipe, query_food, get_book_metadata,
)
from app.tools.list import (
    list_characters_by_element, list_characters_by_region,
    list_characters_by_weapon, list_characters_by_rarity,
    list_all_quest_series, list_all_books, list_all_lore_entries,
    list_all_weapons_and_artifacts, list_all_game_items,
    list_collectibles_by_region, count_character_lines,
)
from app.tools.search import (
    search_activity, find_first_mention,
    search_all, search_lore,  # 内部辅助函数，供特殊场景调用
)
from app.tools.content import (
    load_book_content, load_quest_content,
)
from app.retrieval import hybrid_search, kb_vector_search


# ====== 工具列表（顺序决定 LLM 看到的工具顺序）======
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

# ====== 工具名 → 函数映射（供意图路由器动态注入）======
tools_by_name = {t.name: t for t in tools}


# ====== 代码加固：熔断触发工具 ======
# 这些工具一旦在本轮中成功返回内容，后续所有工具调用将被系统截断
MELTDOWN_TRIGGER_TOOLS = {"load_book_content", "load_quest_content", "find_first_mention"}


def get_tools_by_name() -> dict:
    """返回工具名 → 函数映射的副本，避免外部修改内部映射。"""
    return dict(tools_by_name)

# -*- coding: utf-8 -*-
"""全量 wiki 链接图的频道/类型映射（观测枢频道树实测数据）。

供 wiki_entry_graph.py 和 wiki_data_tools/_fetch_mihoyo_channel.py 共用，
避免两处各自维护一份不一致的类型表。
"""

# channel_id -> graph entry_type
CHANNEL_TYPE_MAP = {
    43: "task",
    251: "map_text",
    20: "npc",
    25: "character",
    261: "character_anecdote",
    5: "weapon",
    218: "artifact",
    68: "book",
    6: "monster",
    255: "organization",
    13: "item",
    105: "activity",
    278: "gadget",
    276: "region_feature",
    54: "domain",
    55: "adventure_guild",
    257: "story_chapter",
    21: "food",
    49: "animal",
    227: "tutorial",
    252: "achievement",
    244: "avatar",
    109: "namecard",
    211: "outfit",
    130: "housing",
    65: "abyss",
    249: "theater",
    275: "challenge",
    260: "tavern_challenge",
}

# 抓取阶段：Tier 1 是叙事/世界观核心，Tier 2 次之，Tier 3 是纯玩法/COS 频道。
CHANNEL_TIERS = {
    "task": 1,
    "map_text": 1,
    "npc": 1,
    "character": 1,
    "character_anecdote": 1,
    "weapon": 1,
    "artifact": 1,
    "book": 1,
    "monster": 1,
    "organization": 1,
    "item": 1,
    "activity": 1,
    "gadget": 1,
    "region_feature": 1,
    "domain": 1,
    "adventure_guild": 1,
    "story_chapter": 1,
    "food": 2,
    "animal": 2,
    "tutorial": 2,
    "achievement": 2,
    "avatar": 3,
    "namecard": 3,
    "outfit": 3,
    "housing": 3,
    "abyss": 3,
    "theater": 3,
    "challenge": 3,
    "tavern_challenge": 3,
}

# 各频道的观测枢菜单中文名（日志/报告展示用）
CHANNEL_NAMES = {
    43: "任务", 251: "地图文本", 20: "NPC&商店", 25: "角色", 261: "角色逸闻",
    5: "武器", 218: "圣遗物", 68: "书籍", 6: "敌人", 255: "组织", 13: "背包",
    105: "活动", 278: "「灰眸」", 276: "地区供奉&聚所", 54: "秘境",
    55: "冒险家协会", 257: "空月之歌", 21: "食物", 49: "动物", 227: "教程",
    252: "成就", 244: "头像", 109: "名片", 211: "装扮", 130: "洞天",
    65: "深境螺旋", 249: "幻想真境剧诗", 275: "幽境危战", 260: "酒馆挑战",
}

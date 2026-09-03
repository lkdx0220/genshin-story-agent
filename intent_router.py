#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
意图路由器 -- Stage 0 实体锚定 + Stage 1 LLM 路由。
在 Plan Agent 启动前决定本次对话暴露哪些工具。
"""

import ast
import os
import re
import json
from typing import List, Tuple
from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI
from dotenv import load_dotenv

load_dotenv()

# ====== 路由器模型（轻量级，不需要强模型） ======
ROUTER_MODEL_NAME = os.getenv("ROUTER_MODEL", "qwen-plus")
DASHSCOPE_API_KEY = os.getenv("DASHSCOPE_API_KEY")
DASHSCOPE_BASE_URL = os.getenv("DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")

_router_llm = ChatOpenAI(
    model=ROUTER_MODEL_NAME,
    api_key=DASHSCOPE_API_KEY,
    base_url=DASHSCOPE_BASE_URL,
    temperature=0,
    max_tokens=50,
    request_timeout=30,
)

# ====== 工具分组映射 ======
TOOL_GROUPS = {
    "A": ["hybrid_search", "search_activity"],
    "B": [
        "query_character",
        "list_characters_by_element",
        "list_characters_by_region",
        "list_characters_by_weapon",
        "list_characters_by_rarity",
        "count_character_lines",
        "list_all_quest_series",
    ],
    "C1": ["query_weapon", "query_artifact", "query_material", "query_recipe", "query_food", "list_all_weapons_and_artifacts"],
    "C2": [
        "query_monster",
        "query_collectible",
        "query_region",
        "list_collectibles_by_region",
        "list_all_lore_entries",
        "list_all_game_items",
    ],
    "D": ["query_story", "query_quest", "load_quest_content", "search_all"],
    "E": ["load_book_content", "get_book_metadata", "list_all_books"],
    "F": ["find_first_mention"],
}

ALL_TOOL_NAMES = []
for _group_names in TOOL_GROUPS.values():
    for _name in _group_names:
        if _name not in ALL_TOOL_NAMES:
            ALL_TOOL_NAMES.append(_name)

# ====== 标签中文名（供 grounding 展示） ======
LABEL_CN = {
    "A": "搜索检索", "B": "角色查询", "C1": "装备养成",
    "C2": "世界生态", "D": "剧情任务", "E": "书籍文献",
    "F": "溯源追踪", "ALL": "全量工具",
}

ROUTER_PROMPT = """你是一个意图分类器。根据用户问题，输出 1-2 个意图标签，或特殊标签 "ALL"。

===== 实体锚定（已预先查询知识库，无需你判断以下实体的类型）=====
{grounding_section}

===== 标签列表 =====

A  —— 搜索检索：问题要求"搜索/查找/有没有提到/在哪出现"某段文本，未指定已知的实体类型
B  —— 角色查询：问角色的信息、属性、按元素/地区/武器筛选、角色台词统计
C1 —— 装备养成：问武器、圣遗物、材料、食谱、食物的详情
C2 —— 世界生态：问怪物、采集物、地区介绍、游戏概念/组织的详情
D  —— 剧情任务：问主线剧情、传说任务、世界任务、活动剧情、任务讲了什么、角色在任务中做了什么
E  —— 书籍文献：问游戏内书籍的内容、作者、版本
F  —— 溯源追踪："第一次出现/首次登场/最早提到/在哪出场"
ALL—— 全量工具：问题明确要求跨多个维度的综合检索

===== 实体锚定使用规则（重要）=====
- 实体锚定只告诉你"这是什么类型的实体"，不告诉你"用户想对这个实体做什么"——后者由判别规则决定
- 锚定结果中的标签作为候选参考，但不是必须全部采纳
- 如果判别规则与锚定结果冲突，判别规则优先
- 特别提醒：锚定到 C2(世界生态) 的怪物/NPC，如果用户问的是其关系/经历/故事，应路由到 B+D 或 D，而非 C2
  示例："若陀龙王和钟离是什么关系"——若陀锚定为C2，但"关系"触发规则1 → 应输出 ["B", "D"]

===== 判别规则 =====

1. "列出/浏览/有哪些" + "所有/全部/每个/目录" → 目录查询，按主题路由（注意：不是问具体内容，是问"有哪些"）：
   - 角色的任务/传说任务/属性 → B
   - 书籍/书目 → E
   - 武器/圣遗物 → C1
   - 地区/概念/怪物/物品 → C2
   这组规则优先级最高，不应被后续规则覆盖。

2. 问题主语是角色/NPC + 询问其过去经历或演变过程 → ["B", "D"]
   触发词包括但不限于："经历/成长/变化/做了什么/在哪出现/故事/怎么来的/
   身世/来历/为什么变成这样/和XX的关系/如何成为"
   简言之：问的是"一个角色/NPC 的过去经历或演变过程"，而非"当前状态/属性"→ 加 D。
   示例："蒂蕾娜经历了什么" → ["B", "D"]
   示例："钟离在璃月主线做了什么" → ["B", "D"]
   示例："阿佩普的绿洲守望者是怎么来的" → ["C2", "D"]（主语是怪物/NPC，
         "怎么来的"是询问来历/过程，query_monster 词条不足以覆盖，需任务全文）

3. "有哪些/列出/筛选" + 明确的筛选维度（元素/地区/武器等）→ B
   示例："有哪些火元素角色" → ["B"]
   示例："蒙德有哪些特产" → ["C2"]

4. 问题含"原话/原文/台词/说过" → 判断主语：
   - 主语是角色名 + 在某个任务中 → ["B", "D"]
   - 主语是角色名，未指定任务 → ["B", "A"]

5. 引述验证（"这段话在库里吗"、"原文检索'...'"）→ ["A"]

6. 对比分析（"A和B有什么异同/变化"）→ 判断A、B的实体类型：
   - 两个都是任务/剧情 → ["D"]
   - 一方是任务一方是角色 → ["B", "D"]
   - 对比角色在不同任务中的表现/态度变化 → ["B", "D"]
   - 两本不同的书 → ["E"]
   - 优先参考实体锚定结果来判断类型

7. 主观评价/价值判断（"怎么看"、"做得对不对"）→ 按主题正常分类，
   不要因为"降级研究"改变路由

8. 问题含"所有/全部/汇总/一手资料/各方面/全貌/完整呈现"等全局词汇 → ["ALL"]
   示例："帮我汇总'命运的织机'的所有一手资料" → ["ALL"]
   示例："关于坎瑞亚的全部信息" → ["ALL"]
   注意："原因/为什么/动机"等因果询问词不触发 ALL
   注意：如被规则1（目录查询）优先匹配，则不触发此规则

===== 兜底规则 =====

- 拿不准时，多输出一个标签比少输出好
- 如果只能输出一个标签但犹豫，输出最可能的那一个
- 不要输出超过 2 个标签（除非输出 ["ALL"]）
- 实体锚定结果中的标签如果没有被用户意图排除，默认保留

===== 多轮对话规则 =====

如果提供了对话历史摘要，结合最近对话判断用户当前问题的指代对象。
特别注意：对话历史摘要中的实体链（如"小女孩=幽灵小九九=胡桃传说任务"），
在当前问题指代模糊时必须追溯。

===== 输出格式 =====

只输出 JSON 数组，不要其他内容。示例：
["B", "D"]
["A"]
["E"]
["ALL"]

当前是第几轮对话：{turn_number}
对话历史摘要：{conversation_summary}
用户问题：{user_question}"""


# ======================================================================
# Stage 0: 实体注册表 + 短名索引（纯代码，0 LLM 调用）
# ======================================================================

CONTENT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "content_data")

_entity_registry = None        # {name: (labels_set, [short_names])}
_shortname_index = None        # {short_name: (name, labels_set)}
_exact_index_bracketless = None  # {bracketless_name: (name, labels_set)}

# ======================================================================
# 传说任务章节映射（角色名 → 章节名）
# 从 传说任务章节列表.txt 解析，过滤掉版本活动/非传说任务
# ======================================================================

_legendary_quest_map = None           # {canonical_name: chapter_name}
_alias_to_legendary_map = None        # {alias: (canonical, chapter)}
_aliases_sorted_for_legendary = None  # [alias, ...] 按长度降序
_pseudo_legendary_map = None          # {戏称: (活动名, 备注)} 如 "兹白传说任务"→("奔霄颂玉轮", "2026年海灯节")
_pseudo_aliases_sorted = None         # [戏称, ...] 按长度降序


def _get_legendary_quest_map():
    """解析传说任务章节列表，构建角色名→章节名的映射。
    同时捕获"被戏称为XX传说任务"的活动条目，供 grounding 说明。
    """
    global _legendary_quest_map, _alias_to_legendary_map, _aliases_sorted_for_legendary
    global _pseudo_legendary_map, _pseudo_aliases_sorted
    if _legendary_quest_map is not None:
        return _legendary_quest_map

    txt_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "传说任务章节列表.txt")
    _legendary_quest_map = {}
    _pseudo_legendary_map = {}

    if not os.path.exists(txt_path):
        print("[传说任务映射] 文件不存在，跳过")
        _alias_to_legendary_map = {}
        _aliases_sorted_for_legendary = []
        _pseudo_aliases_sorted = []
        return _legendary_quest_map

    # 匹配 "活动名：备注"
    pattern = re.compile(r'^(.+?)[：:,，]\s*(.+)$')
    # 提取戏称关键词: "被戏称为是兹白传说任务" → 兹白传说任务
    pseudo_pattern = re.compile(r'被戏称为(?:是)?(.+?)(?:（.+）)?$')

    with open(txt_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            if any(kw in line for kw in ["版本活动", "不是传说任务"]):
                # 检查是否包含"被戏称为"
                if "被戏称为" in line:
                    m = pattern.match(line)
                    if m:
                        activity_name = m.group(1).strip()
                        remark = m.group(2).strip()
                        # 从备注中提取戏称别名
                        pm = pseudo_pattern.search(remark)
                        if pm:
                            pseudo_alias = pm.group(1).strip()
                            _pseudo_legendary_map[pseudo_alias] = (activity_name, remark)
                            # 括号内的简称也加入，如 "胡桃传说任务第二幕（胡桃传说任务2）"
                            bracket_match = re.search(r'（(.+?)）', remark)
                            if bracket_match:
                                short_alias = bracket_match.group(1).strip()
                                _pseudo_legendary_map[short_alias] = (activity_name, remark)
                continue
            m = pattern.match(line)
            if m:
                chapter = m.group(1).strip()
                character = m.group(2).strip()
                # 如果备注中包含"被戏称为"，这是伪传说任务
                if "被戏称为" in character:
                    pm = pseudo_pattern.search(character)
                    if pm:
                        pseudo_alias = pm.group(1).strip()
                        _pseudo_legendary_map[pseudo_alias] = (chapter, character)
                        bracket_match = re.search(r'（(.+?)）', character)
                        if bracket_match:
                            short_alias = bracket_match.group(1).strip()
                            _pseudo_legendary_map[short_alias] = (chapter, character)
                    continue
                if len(character) > 10 or not character:
                    continue
                _legendary_quest_map[character] = chapter

    _pseudo_aliases_sorted = sorted(_pseudo_legendary_map.keys(), key=len, reverse=True)

    # ---- 构建反向别名→章节映射 ----
    try:
        from character_aliases import CHARACTER_ALIASES
        _alias_to_legendary_map = {}
        for canonical, aliases in CHARACTER_ALIASES.items():
            if canonical not in _legendary_quest_map:
                continue
            chapter = _legendary_quest_map[canonical]
            for alias in aliases:
                clean = alias.strip().replace("「", "").replace("」", "")
                if len(clean) < 2:
                    continue
                if clean not in _alias_to_legendary_map:
                    _alias_to_legendary_map[clean] = (canonical, chapter)
        _aliases_sorted_for_legendary = sorted(
            _alias_to_legendary_map.keys(), key=len, reverse=True
        )
        print(f"[传说任务映射] 已构建: {len(_legendary_quest_map)} 个角色→章节 + "
              f"{len(_alias_to_legendary_map)} 个反向别名 + "
              f"{len(_pseudo_legendary_map)} 个戏称活动映射")
    except ImportError:
        print("[传说任务映射] 无法导入 character_aliases，跳过别名锚定")
        _alias_to_legendary_map = {}
        _aliases_sorted_for_legendary = []

    return _legendary_quest_map


def _strip_brackets(text: str) -> str:
    """去除书名号/引号等装饰符号"""
    return re.sub(r'[《》「」『』""''（）\\s]', '', text).strip()


def _generate_short_names(name: str) -> List[str]:
    """从实体名生成短名列表，用于部分匹配（"裁叶"→"裁叶萃光"）。"""
    shorts = []
    clean = _strip_brackets(name)
    if len(clean) >= 2:
        shorts.append(clean[:2])
    if len(clean) >= 3:
        shorts.append(clean[:3])
    if len(clean) >= 4 and clean[:4] != clean[:2]:
        shorts.append(clean[:4])
    return shorts


def _get_entity_registry():
    """Lazy-load: 构建实体名→标签映射 + 短名索引。
    首次调用时扫描所有知识库数据源，后续走缓存。
    """
    global _entity_registry, _shortname_index, _exact_index_bracketless
    if _entity_registry is not None:
        return _entity_registry, _shortname_index, _exact_index_bracketless

    # registry: name → (labels_set, [short_names])
    # labels_set 是集合，同一实体名跨类别时合并标签（如"天空"同时是武器C1和地区C2）
    registry = {}

    def _add(name, label):
        if not name or len(name) < 2:
            return
        if name in registry:
            existing_labels, existing_shorts = registry[name]
            existing_labels.add(label)
            registry[name] = (existing_labels, existing_shorts)
        else:
            registry[name] = ({label}, _generate_short_names(name))

    # --- 武器 → C1 ---
    try:
        from genshin_knowledge_base import 武器知识库
        for w in 武器知识库:
            _add(w.get("武器名称", ""), "C1")
    except Exception:
        pass

    # --- 圣遗物 → C1 ---
    try:
        from genshin_knowledge_base import 圣遗物知识库
        for a in 圣遗物知识库:
            _add(a.get("圣遗物名称", ""), "C1")
    except Exception:
        pass

    # --- 素材 → C1 ---
    try:
        from genshin_knowledge_base import 素材知识库
        for m in 素材知识库:
            _add(m.get("素材名称", ""), "C1")
    except Exception:
        pass

    mat_path = os.path.join(CONTENT_DIR, "materials.json")
    try:
        with open(mat_path, "r", encoding="utf-8") as f:
            materials = json.load(f)
        for m in materials:
            _add(m.get("名称", ""), "C1")
    except Exception:
        pass

    # --- 角色 → B ---
    try:
        from genshin_knowledge_base import 角色知识库
        for c in 角色知识库:
            _add(c.get("角色名称", ""), "B")
    except Exception:
        pass

    # --- NPC → B ---
    npcs_path = os.path.join(CONTENT_DIR, "npcs_processed.json")
    if os.path.exists(npcs_path):
        try:
            with open(npcs_path, "r", encoding="utf-8") as f:
                npcs = json.load(f)
            for name in npcs.keys():
                _add(name, "B")
        except Exception:
            pass

    # --- 任务 → D ---
    # 从 content_data/quests_*.json 提取任务标题
    for fname in os.listdir(CONTENT_DIR):
        if not fname.startswith("quests_") or not fname.endswith(".json"):
            continue
        if fname == "quests_processed.json":
            continue
        try:
            with open(os.path.join(CONTENT_DIR, fname), "r", encoding="utf-8") as f:
                quests = json.load(f)
        except Exception:
            continue
        if not isinstance(quests, list):
            continue
        for q in quests:
            _add(q.get("title", ""), "D")

    # --- 书籍 → E ---
    book_path = os.path.join(CONTENT_DIR, "books.json")
    if os.path.exists(book_path):
        try:
            with open(book_path, "r", encoding="utf-8") as f:
                books = json.load(f)
            for b in books:
                _add(b.get("title", ""), "E")
        except Exception:
            pass

    # --- 怪物 → C2 ---
    mon_path = os.path.join(CONTENT_DIR, "monsters.json")
    if os.path.exists(mon_path):
        try:
            with open(mon_path, "r", encoding="utf-8") as f:
                monsters = json.load(f)
            for m in monsters:
                _add(m.get("名称", ""), "C2")
        except Exception:
            pass

    # --- 采集物 → C2 ---
    col_path = os.path.join(CONTENT_DIR, "collectibles.json")
    if os.path.exists(col_path):
        try:
            with open(col_path, "r", encoding="utf-8") as f:
                colls = json.load(f)
            for c in colls:
                _add(c.get("名称", ""), "C2")
        except Exception:
            pass

    # --- 地区 → C2 ---
    try:
        from genshin_knowledge_base import 地区知识库
        for r in 地区知识库:
            _add(r.get("地区名称", ""), "C2")
    except Exception:
        pass

    # --- 概念/组织 → C2 ---
    con_path = os.path.join(CONTENT_DIR, "concepts.json")
    if os.path.exists(con_path):
        try:
            with open(con_path, "r", encoding="utf-8") as f:
                concepts = json.load(f)
            for c in concepts:
                _add(c.get("名称", ""), "C2")
        except Exception:
            pass

    # --- 食谱 → C1 ---
    rec_path = os.path.join(CONTENT_DIR, "recipes.json")
    if os.path.exists(rec_path):
        try:
            with open(rec_path, "r", encoding="utf-8") as f:
                recipes = json.load(f)
            for r in recipes:
                _add(r.get("名称", ""), "C1")
        except Exception:
            pass

    # --- 食物 → C1 ---
    food_path = os.path.join(CONTENT_DIR, "foods.json")
    if os.path.exists(food_path):
        try:
            with open(food_path, "r", encoding="utf-8") as f:
                foods = json.load(f)
            for fd in foods:
                _add(fd.get("名称", ""), "C1")
        except Exception:
            pass

    # ---- 构建短名索引 ----
    # 临时统计每个短名命中了多少实体
    shortname_counter = {}
    for name, (labels, shorts) in registry.items():
        for sn in shorts:
            if sn not in shortname_counter:
                shortname_counter[sn] = []
            shortname_counter[sn].append((name, labels))

    # 只保留唯一命中的短名（1 个实体），过滤掉歧义短名
    shortname_index = {}
    for sn, entries in shortname_counter.items():
        if len(entries) == 1:
            shortname_index[sn] = entries[0]  # (name, labels_set)

    # ---- 构建去除括号后的精确索引 ----
    exact_index_bracketless = {}
    for name, (labels, _) in registry.items():
        clean = _strip_brackets(name)
        if clean and clean != name and len(clean) >= 3:
            # 只有当去除括号后的名字不与注册表中已有名字冲突时才加入
            if clean not in registry:
                existing = exact_index_bracketless.get(clean)
                if existing is None:
                    exact_index_bracketless[clean] = (name, labels)

    _entity_registry = registry
    _shortname_index = shortname_index
    _exact_index_bracketless = exact_index_bracketless

    total = len(registry)
    unique_short = len(shortname_index)
    print(f"[实体注册表] 已构建: {total} 个实体, {unique_short} 个唯一短名")
    return registry, shortname_index, exact_index_bracketless


def _anchor_entities(query: str) -> Tuple[List[str], List[Tuple[str, List[str]]]]:
    """Stage 0：扫描查询中的已知实体，返回 (候选标签, 锚定实体列表)。

    匹配策略（按优先级）：
    1. 精确匹配：实体全名出现在查询中
    2. 去括号精确匹配：用户不加书名号也能命中
    3. 短名索引匹配：查询中的 2-4 字子串命中唯一短名

    截断策略：候选标签超过 3 个时，按匹配优先级保留前 3 个。
    未命中时返回空列表，LLM 路由器独立工作（退化为原方案）。
    """
    registry, shortname_index, exact_index_bracketless = _get_entity_registry()

    anchored_map = {}  # name → {labels}，用于去重
    candidate_labels_ordered = []  # 保持插入顺序，用于截断
    candidate_labels_set = set()

    def _add_anchor(name: str, labels):
        """将实体及其所有标签加入锚定结果，按 name 去重（同实体命中多次匹配策略时合并标签）。"""
        if name in anchored_map:
            anchored_map[name].update(labels)
        else:
            anchored_map[name] = set(labels)
        # 更新全局候选标签
        for lbl in sorted(labels):
            if lbl not in candidate_labels_set:
                candidate_labels_set.add(lbl)
                candidate_labels_ordered.append(lbl)

    # Pass 1: 精确全名匹配
    for name, (labels, _) in registry.items():
        if name in query:
            _add_anchor(name, labels)

    # Pass 2: 去括号精确匹配（用户不带书名号也能命中）
    for clean, (name, labels) in exact_index_bracketless.items():
        if clean in query:
            _add_anchor(name, labels)

    # Pass 3: 短名索引（部分匹配）
    qlen = len(query)
    seen_sub = set()
    for i in range(qlen):
        max_j = min(i + 5, qlen + 1)  # 只查 2-4 字子串
        for j in range(i + 2, max_j):
            sub = query[i:j]
            if sub in seen_sub:
                continue
            seen_sub.add(sub)
            entry = shortname_index.get(sub)
            if entry:
                name, labels = entry
                _add_anchor(name, labels)

    # Pass 4: 别名→传说任务锚定（最长匹配优先）
    # "草神"→纳西妲→智慧主之章，"心海"→珊瑚宫心海→眠龙之章
    if _aliases_sorted_for_legendary is None:
        _get_legendary_quest_map()
    if _aliases_sorted_for_legendary:
        for alias in _aliases_sorted_for_legendary:
            if alias in query:
                canonical, chapter = _alias_to_legendary_map[alias]
                _add_anchor(f"{canonical}(传说任务「{chapter}」)", {"D"})
                break  # 最长匹配命中即停止，避免"心海"和"珊瑚宫心海"重复锚定

    # Pass 5: 伪传说任务检测（活动被戏称为传说任务）
    # "兹白传说任务"→奔霄颂玉轮(2026年海灯节)，"胡桃传说任务2"→春曦画桃符(2025年海灯节)
    if _pseudo_aliases_sorted is None:
        _get_legendary_quest_map()
    pseudo_note = ""
    if _pseudo_aliases_sorted:
        for alias in _pseudo_aliases_sorted:
            if alias in query:
                activity_name, remark = _pseudo_legendary_map[alias]
                pseudo_note = (
                    f"注意：查询中的「{alias}」并非真正的传说任务，"
                    f"而是社区对活动「{activity_name}」({remark})的戏称。"
                    f"路由时请按活动剧情处理（D组），回答时需先澄清这一点。"
                )
                _add_anchor(f"{activity_name}(活动剧情，被戏称为传说任务)", {"D"})
                break

    # 转换为列表输出
    anchored = [(n, sorted(lbs)) for n, lbs in anchored_map.items()]

    # ---- 截断：候选标签超过 3 个时保留前 3 个 ----
    truncated = len(candidate_labels_ordered) > 3
    if truncated:
        keep_labels = set(candidate_labels_ordered[:3])
        candidate_labels_ordered = candidate_labels_ordered[:3]
        candidate_labels_set = keep_labels
        # 过滤 anchored，只保留至少有一个标签在截断集合中的实体
        anchored = [(n, [l for l in lbs if l in keep_labels]) for n, lbs in anchored]
        anchored = [(n, lbs) for n, lbs in anchored if lbs]

    return candidate_labels_ordered, anchored, pseudo_note


def _format_grounding(anchored: List[Tuple[str, List[str]]]) -> str:
    """将锚定结果格式化为 LLM 可读的 grounding 文本（支持多标签实体）。"""
    if not anchored:
        return "（未检测到知识库中已收录的实体名，请按判别规则自行分类）"

    lines = ["以下实体名已在知识库中找到，类型已确认（仅作参考，不代表用户意图）："]
    for name, labels in anchored:
        label_str = ", ".join(f"{l}({LABEL_CN.get(l, l)})" for l in labels)
        lines.append(f"  - 「{name}」→ {label_str}")
    return "\n".join(lines)


# ======================================================================
# Stage 1: LLM 路由器
# ======================================================================

_route_cache: dict = {}
_last_raw_response: str = ""


def get_last_raw_response() -> str:
    """返回路由器 LLM 最近一次原始回复，供测试脚本查看。"""
    return _last_raw_response


def route_intent(
    user_query: str,
    conversation_summary: str = "",
    turn_number: int = 0,
) -> List[str]:
    """Stage 0 + Stage 1 联合路由，返回意图标签列表。"""
    global _last_raw_response

    # 缓存 key：取 query 前 80 字 + 摘要前 40 字
    cache_key = f"{user_query[:80]}|{conversation_summary[:40]}|{turn_number}"
    if cache_key in _route_cache:
        return _route_cache[cache_key]

    # ---- Stage 0: 实体锚定 ----
    candidate_labels, anchored, pseudo_note = _anchor_entities(user_query)
    grounding_section = _format_grounding(anchored)
    if pseudo_note:
        grounding_section = grounding_section.rstrip() + "\n" + pseudo_note

    # ---- Stage 1: LLM 路由 ----
    prompt = ROUTER_PROMPT.format(
        grounding_section=grounding_section,
        turn_number=turn_number,
        conversation_summary=conversation_summary or "（无，这是第一轮对话）",
        user_question=user_query,
    )

    try:
        response = _router_llm.invoke([HumanMessage(content=prompt)])
        raw = response.content.strip()
        _last_raw_response = raw
        match = re.search(r'\[.*?\]', raw, re.DOTALL)
        if match:
            labels = ast.literal_eval(match.group(0))
        else:
            # 兜底：如果 LLM 没有输出 JSON，但 Stage 0 有锚定结果，用锚定结果
            if candidate_labels:
                labels = list(candidate_labels)[:2]
                _last_raw_response = f"(无 JSON 匹配，使用锚定结果: {labels})"
            else:
                labels = ["ALL"]
                _last_raw_response = f"(无 JSON 匹配，原始回复: {raw})"
    except Exception as e:
        if candidate_labels:
            labels = list(candidate_labels)[:2]
            _last_raw_response = f"(调用失败: {e}，使用锚定结果: {labels})"
        else:
            labels = ["ALL"]
            _last_raw_response = f"(调用失败: {e})"

    # 验证标签合法性
    valid_labels = {"A", "B", "C1", "C2", "D", "E", "F", "ALL"}
    labels = [l for l in labels if l in valid_labels]

    if not labels:
        labels = ["ALL"]

    _route_cache[cache_key] = labels
    return labels


def get_pseudo_legendary_note(query: str) -> str:
    """检查查询中是否包含伪传说任务戏称，返回 Plan Agent 提示。
    如 "兹白传说任务" → 返回 "请注意：兹白没有传说任务。用户问的「兹白传说任务」
    实际是2026年海灯节活动「奔霄颂玉轮」，请直接调用 load_quest_content("奔霄颂玉轮")。"
    """
    # 确保映射已加载
    _get_legendary_quest_map()
    if not _pseudo_aliases_sorted:
        return ""
    for alias in _pseudo_aliases_sorted:
        if alias in query:
            activity_name, remark = _pseudo_legendary_map[alias]
            return (
                f"[戏称提醒] 用户查询中的「{alias}」不是真正的传说任务，"
                f"而是社区对活动「{activity_name}」({remark.split('、')[0] if '、' in remark else remark})的戏称。"
                f"请直接调用 load_quest_content(\"{activity_name}\") 加载该活动内容，"
                f"不要调用 query_quest。回答时需先说明「{alias.split('传说任务')[0] if '传说任务' in alias else alias}没有传说任务，"
                f"但'{remark.split('、')[0] if '、' in remark else remark}'被社区戏称为{alias}」。"
            )
    return ""


# ======================================================================
# 工具选择
# ======================================================================

def get_tools_for_intent(labels: List[str], all_tools_by_name: dict) -> list:
    """根据意图标签返回该轮可用的工具列表。

    Args:
        labels: 路由标签列表，如 ["B", "D"]
        all_tools_by_name: {tool_name: tool_function} 的全量映射

    Returns:
        工具函数列表
    """
    if "ALL" in labels:
        return list(all_tools_by_name.values())

    tool_names: set = set()
    for label in labels:
        if label in TOOL_GROUPS:
            tool_names.update(TOOL_GROUPS[label])

    # 常驻注入 hybrid_search
    tool_names.add("hybrid_search")

    tools = []
    for name in tool_names:
        if name in all_tools_by_name:
            tools.append(all_tools_by_name[name])

    return tools

# -*- coding: utf-8 -*-
"""Query 类工具：具体实体查询（角色/地区/剧情/武器/任务/怪物/圣遗物/材料/采集物/食谱/食物/书籍元数据）。

每个工具负责一类实体的精确查询，模糊匹配后调用 formatter 渲染为 LLM 友好文本。
"""
import os
import json

from langchain_core.tools import tool

from app.config import CONTENT_DIR
from app.data import (
    角色知识库, 地区知识库, 主线剧情知识库, 武器知识库,
    任务知识库, 圣遗物知识库, _npcs_data, _load_content_json,
    _normalize_for_match, _aggregate_map_text,
)
from app.formatters import (
    _format_role_info, _format_npc_info, _format_region_info, _format_story_info,
)
from character_aliases import ALIAS_MAP, resolve_aliases


# 戏称映射：角色名 → 被戏称为该角色传说任务的版本活动
FAKE_LEGEND_QUESTS = {
    "兹白": "奔霄颂玉轮",
    "布伦妮": "幻友绮旅",
    "嘉明": "彩鹞栉春风",
    "菲米尼": "特尔克西的奇幻历险",
    "卡维": "盛典与慧业",
    "夏沃蕾": "蔷薇与铳枪",
    "胡桃": "春曦画桃符",
}

# 部族纪闻映射：角色名 → 对应的部族纪闻章节名

TRIBAL_CHRONICLES = {
    "玛拉妮": "流泉所归之处",
    "基尼奇": "尤潘基的回火",
    "希诺宁": "祈祝福愿，倾告嵴锋",
    "恰斯卡": "花之归尘，羽之将坠",
    "瓦雷莎": "蘑境菌奇",
    "茜特菈莉": "流淌着色彩的回忆",
}


@tool
def query_character(name: str) -> str:
    """查询原神角色详细信息。name: 角色名称或常用别名（如\"胡桃\"、\"钟离\"、\"散兵\"）"""
    # 别名反向展开：散兵 → 流浪者，确保社区常用名能查到规范名资料
    candidates = []
    seen = set()
    for candidate in resolve_aliases(name):
        c = candidate.strip().replace("「", "").replace("」", "")
        if c and c not in seen:
            seen.add(c)
            candidates.append(c)
    # 原始 name 放在最前，保持精确匹配优先级
    candidates = [name] + [c for c in candidates if c != name]

    for candidate in candidates:
        for role in 角色知识库:
            if candidate in role.get("角色名称", "") or candidate in role.get("称号", ""):
                print(f"[工具] 查询角色: {name} -> {candidate}")
                return _format_role_info(role)

        # 兜底：查 NPC 数据
        npc = _npcs_data.get(candidate)
        if npc:
            print(f"[工具] 查询NPC: {name} -> {candidate}")
            return _format_npc_info(candidate, npc)

    return f"未找到角色「{name}」的信息。知识库还在完善中，建议前往 Bilibili Wiki 查看。"


@tool
def query_region(name: str) -> str:
    """查询原神地区（国家）信息。name: 地区名称（如\"蒙德\"、\"璃月\"、\"稻妻\"）"""
    for region in 地区知识库:
        if name in region.get("地区名称", ""):
            print(f"[工具] 查询地区: {name}")
            return _format_region_info(region)
    return f"未找到地区「{name}」的信息。"


def _story_matches(arc: dict, keyword: str) -> bool:
    """匹配主线条目：章节名、章节编号、地区、幕名，以及幕下任务名。"""
    if not keyword:
        return False
    fields = [
        arc.get("章节名称", ""),
        arc.get("章节编号", ""),
        arc.get("所属地区", ""),
    ]
    for act in arc.get("幕列表", []):
        fields.append(act.get("幕编号", ""))
        fields.append(act.get("幕名称", ""))
        fields.extend(act.get("任务", []))
    return any(keyword in f for f in fields if f)


@tool
def query_story(arc_name: str) -> str:
    """查询版本主线/魔神任务剧情信息。arc_name: 章节/幕关键词（如\"辞行久远之躯\"、\"第五章\"、\"虚空鼓动，劫火高扬\"）"""
    results = []
    for arc in 主线剧情知识库:
        if _story_matches(arc, arc_name):
            results.append(arc)
    if len(results) == 1:
        print(f"[工具] 查询剧情: {arc_name}")
        return _format_story_info(results[0])
    elif results:
        print(f"[工具] 查询剧情(多条): {arc_name}")
        return "\n\n".join(_format_story_info(a) for a in results)
    return (f"未找到与「{arc_name}」相关的剧情。如果用户问的是活动剧情（活动剧情不在 query_story 的主线知识库中），"
            f"请改用 load_quest_content 加载该活动任务全文。")


@tool
def query_weapon(name: str) -> str:
    """查询武器信息。name: 武器名称（如\"护摩之杖\"、\"雾切之回光\"）"""
    for wpn in 武器知识库:
        if name in wpn.get("武器名称", ""):
            print(f"[工具] 查询武器: {name}")
            star_count = wpn.get('稀有度', 0)
            star_str = '★' * star_count if isinstance(star_count, int) and star_count > 0 else str(star_count)
            lines = [f"\n{'='*50}", f"【{wpn['武器名称']}】{star_str}",
                     f"类型: {wpn.get('武器类型', '未知')}"]
            if wpn.get('副属性'):
                lines.append(f"副属性: {wpn['副属性']}")
            if wpn.get('技能名称'):
                lines.append(f"技能: {wpn['技能名称']}")
            if wpn.get('实装版本'):
                lines.append(f"实装版本: {wpn['实装版本']}")
            lines.append(f"\n简介: {wpn.get('简介', '暂无')}")
            if wpn.get('武器故事'):
                story = wpn['武器故事']
                lines.append(f"\n武器故事:\n{story}")
            lines.append(f"{'='*50}")
            return "\n".join(lines)
    return f"未找到武器「{name}」的信息。"


@tool
def query_quest(name: str) -> str:
    """查询传说任务/世界任务信息。name: 任务名称或角色名。
    返回时自动按系列任务（章节名）分组。显示所属角色，区分主角视角与客串出场。"""
    # 获取名字的所有变体（瓦雷莎/瓦蕾莎等异体字问题）
    name_variants = {name}
    if "传说任务" in name:
        candidate = name.replace("的传说任务", "").replace("传说任务", "").strip()
        if candidate:
            name_variants.add(candidate)
    if name in ALIAS_MAP:
        name_variants.add(ALIAS_MAP[name])  # 规范名
    for alias, canon in ALIAS_MAP.items():
        if canon in name_variants or alias in name_variants:
            name_variants.add(alias)
            name_variants.add(canon)


    # 戏称映射：先正常搜索真实传说/世界任务；只有确实没有真实结果时才返回戏称活动，
    # 避免像胡桃这种既有真实传说任务（引蝶之章/奈何蝶飞去）又有玩家戏称活动的角色被误导。
    matched_fake = None
    for v in name_variants:
        if v in FAKE_LEGEND_QUESTS:
            matched_fake = FAKE_LEGEND_QUESTS[v]
            break


    # 部族纪闻映射：该角色的部族纪闻可能未全部关联角色名，需按章节名补全
    is_tribal = False
    results = []
    seen = set()
    matched_tribal = None
    for v in name_variants:
        if v in TRIBAL_CHRONICLES:
            matched_tribal = TRIBAL_CHRONICLES[v]
            break
    if matched_tribal:
        for q in 任务知识库:
            if q.get("系列任务", "").startswith(matched_tribal):
                results.append(q)
                seen.add(q.get("任务名称", ""))
        is_tribal = True

    for q in 任务知识库:
        if q.get("任务名称") in seen:
            continue
        matched = False
        for v in name_variants:
            if v in q.get("任务名称", "") or v in q.get("关联角色", "") or v in q.get("系列任务", ""):
                matched = True
                break
        if matched:
            results.append(q)
            seen.add(q.get("任务名称", ""))
    if results:
        # 按系列任务（完整"章节,幕名"）分组，区分同一章节下的多幕
        series_groups = {}  # {"章节,幕名": [任务列表]}
        standalone = []
        for q in results:
            series = q.get("系列任务", "")
            if series:
                if series not in series_groups:
                    series_groups[series] = []
                series_groups[series].append(q)
            else:
                standalone.append(q)

        lines = []
        if is_tribal:
            lines.append(f"注意：「{name}」没有专属传说任务，以下为其所在的部族纪闻：")
            lines.append("-" * 40)

        # 补全：同一系列任务下，纳入所有子任务（包括元数据缺失的孤立条目）
        all_series = set(series_groups.keys())
        if all_series:
            for q in 任务知识库:
                s = q.get("系列任务", "")
                if s and s in all_series and q.get("任务名称") not in seen:
                    series_groups[s].append(q)
                    seen.add(q.get("任务名称", ""))

        for series, quests in series_groups.items():
            owner = quests[0].get("所属角色", "")
            is_main = (owner == name)  # 搜索角色等于所属角色=主角视角

            parts = series.split(",")
            chapter = parts[0].strip()
            act_name = parts[1].strip() if len(parts) > 1 else ""
            qtype = quests[0].get("任务类型", "")
            tag = " 【主角】" if is_main else " 【客串出场】"
            lines.append(f"\n【{chapter}】{qtype}{tag}")
            if owner:
                lines.append(f"所属角色: {owner}")
            if act_name:
                lines.append(f"幕: {act_name}")
            lines.append(f"子任务({len(quests)}个): " + " | ".join(q["任务名称"] for q in quests))
            lines.append("-" * 40)

        for q in standalone:
            lines.append(f"\n【{q['任务名称']}】")
            lines.append(f"类型: {q.get('任务类型')}  |  关联角色: {q.get('关联角色')}")
            owner = q.get("所属角色", "")
            if owner:
                lines.append(f"所属角色: {owner}")
            lines.append(f"简介: {q.get('简介', '暂无')}")
            lines.append("-" * 40)

        print(f"[工具] 查询任务: {name}")
        return "\n".join(lines)
    # 真实传说/世界任务全部未命中时，才降级为玩家戏称的“版本活动”
    if matched_fake:
        activity_results = [q for q in 任务知识库 if matched_fake in q.get("任务名称", "")]
        if activity_results:
            lines = [f"注意：「{name}」没有传说任务，以下是被戏称为「{name}传说任务」的版本活动："]
            for q in activity_results:
                lines.append(f"\n【{q['任务名称']}】版本活动")
                lines.append(f"简介: {q.get('简介', '暂无')}")
            print(f"[工具] 查询任务(戏称映射): {name} → {matched_fake}")
            return "\n".join(lines)


    return f"未找到任务「{name}」的信息。"

# 简单同音字组表：用于短名/同音错别字的保守纠错。
# 只收录常见任务/章节用字，后续遇到真实漏网再补充。
# 规则是“同长度 + 至少一个同位置字完全相同 + 其余不同字必须在同一组内”，避免“烟绯”这类仅共享单字但位置不同的误匹配。
_COMMON_HOMOPHONE_GROUPS = [
    "绯非菲飞匪废费",
    "记纪际计继既寄季己几",
    "影引印隐因音银饮",
    "烟焰炎岩盐演严言颜眼",
    "蝶叠跌谍",
    "石时史事世士式室示是诗施湿实食使始",
    "云陨韵运允",
    "明名命鸣冥",
    "文闻温稳问",
    "灵零铃岭领凌",
    "璃丽力立礼李里理莉利离",
    "青清轻情庆倾",
    "真镇震阵珍",
    "风封峰枫丰凤",
    "胡湖虎护互户狐壶",
    "桃逃挑条跳",
    "长常场唱尝",
    "堂唐糖",
    "公宫功工攻贡供",
    "星行形兴醒姓刑",
    "神深身申沈慎",
]
_COMMON_HOMOPHONE_SETS = [set(g) for g in _COMMON_HOMOPHONE_GROUPS]


def _homophone_aligned_score(name: str, candidate: str):
    """同长度逐位对齐的同音/形近候选分；不满足返回 0。"""
    if len(name) != len(candidate) or not name:
        return 0.0
    exact = 0
    for a, b in zip(name, candidate):
        if a == b:
            exact += 1
            continue
        if not any(a in g and b in g for g in _COMMON_HOMOPHONE_SETS):
            return 0.0
    if exact == 0:
        return 0.0
    # 与 0.6 阈值区间衔接：同音候选至少有 1 个同位置字相同，分数按相同比例上浮。
    return 0.6 + 0.3 * (exact / len(name))


def find_similar_quest_names(name: str, top_n: int = 3):
    """返回与输入名称最相似的任务/章节候选，用于任务未命中后的疑似错别字判断。

    候选来源包括：任务名称、系列任务（章节,幕）、所属角色、metadata.chapter_name/act_name。
    相似度阈值 0.6 用于过滤大量“XX之章”类低区分度候选；
    同长度同音/形近候选单独走 _homophone_aligned_score，补足“绯石→匪石”这类两字短名漏网。
    """
    candidates = set()

    def _add(value):
        if not value:
            return
        for part in str(value).replace("，", ",").replace("\n", ",").split(","):
            part = part.strip()
            if part:
                candidates.add(part)

    for q in 任务知识库:
        _add(q.get("任务名称", ""))
        _add(q.get("系列任务", ""))
        _add(q.get("所属角色", ""))
        meta = q.get("metadata", {}) or {}
        _add(meta.get("chapter_name", ""))
        _add(meta.get("act_name", ""))

    from difflib import SequenceMatcher
    scored = []
    for candidate in candidates:
        ratio = SequenceMatcher(None, name, candidate).ratio()
        if ratio >= 0.6:
            scored.append((ratio, candidate))
            continue
        h_score = _homophone_aligned_score(name, candidate)
        if h_score > 0:
            scored.append((h_score, candidate))
    scored.sort(key=lambda x: (-x[0], len(x[1]), x[1]))
    return [candidate for _, candidate in scored[:top_n]]


@tool
def query_monster(name: str) -> str:
    """查询怪物图鉴：属性、抗性、掉落、技能。name: 怪物名称（模糊匹配）"""
    monsters = _load_content_json("monsters")
    if not monsters:
        return "怪物数据库为空。"
    matches = [m for m in monsters if name in m.get("名称", "")]
    if not matches:
        matches = [m for m in monsters if name in m.get("别称", "")]
    if not matches:
        names = [m["名称"] for m in monsters[:30]]
        return f"未找到「{name}」。部分怪物: {', '.join(names)}"
    results = []
    for m in matches[:5]:
        info = f"\n【{m.get('名称', '?')}】"
        if m.get("别称"):
            info += f" ({m['别称']})"
        info += f"\n  类型: {m.get('怪物类型', '?')} | 元素: {m.get('元素属性', '?')}"
        info += f"\n  攻击属性: {m.get('攻击属性', '?')}"
        if m.get("抗性"):
            info += f"\n  抗性: {', '.join(f'{k}={v}' for k, v in m['抗性'].items() if v)}"
        if m.get("掉落"):
            for dk, dv in m["掉落"].items():
                info += f"\n  {dk}: {dv[:150]}"
        if m.get("出现地点"):
            info += f"\n  出现地点: {m['出现地点'][:200]}"
        if m.get("技能名"):
            info += f"\n  技能: {m['技能名']}"
        results.append(info)
    return "\n".join(results)


@tool
def query_artifact(name: str) -> str:
    """查询圣遗物套装：两件套/四件套效果、部位故事。name: 套装名称（模糊匹配）"""
    if not 圣遗物知识库:
        return "圣遗物知识库为空。"
    matches = [a for a in 圣遗物知识库 if name in a.get("圣遗物名称", "")]
    if not matches:
        names = [a["圣遗物名称"] for a in 圣遗物知识库[:20]]
        return f"未找到「{name}」。部分圣遗物: {', '.join(names)}"
    results = []
    for a in matches[:5]:
        info = f"\n【{a.get('圣遗物名称', '?')}】({a.get('稀有度', '?')}星)"
        info += f"\n  两件套: {a.get('两件套效果', '?')}"
        info += f"\n  四件套: {a.get('四件套效果', '?')}"
        stories = a.get("部位故事", {})
        if stories:
            info += f"\n  部位故事 ({len(stories)}篇):"
            for k, v in stories.items():
                info += f"\n    [{k}]: {v[:200]}..."
        results.append(info)
    return "\n".join(results)


@tool
def query_material(name: str) -> str:
    """查询材料：用途、来源、类型。name: 材料名称（模糊匹配）"""
    materials = _load_content_json("materials")
    if not materials:
        return "材料数据库为空。"
    matches = [m for m in materials if name in m.get("名称", "")]
    if not matches:
        names = [m["名称"] for m in materials if m.get("名称")][:30]
        return f"未找到「{name}」。部分材料: {', '.join(names)}"
    results = []
    for m in matches[:8]:
        info = f"\n【{m.get('名称', '?')}】"
        if m.get("类型"):
            info += f" | {m['类型']}"
        if m.get("稀有度"):
            info += f" | {m['稀有度']}星"
        if m.get("用途"):
            info += f"\n  用途: {m['用途'][:200]}"
        if m.get("来源"):
            info += f"\n  来源: {m['来源'][:200]}"
        if m.get("简介"):
            info += f"\n  简介: {m['简介'][:200]}"
        results.append(info)
    return "\n".join(results)


@tool
def query_collectible(name: str) -> str:
    """查询采集物/地区特产：分布地区、刷新时间、用途。name: 采集物名称（模糊匹配）"""
    collectibles = _load_content_json("collectibles")
    if not collectibles:
        return "采集物数据库为空。"
    matches = [c for c in collectibles if name in c.get("名称", "")]
    if not matches:
        names = [c["名称"] for c in collectibles if c.get("名称")][:30]
        return f"未找到「{name}」。部分采集物: {', '.join(names)}"
    results = []
    for c in matches[:8]:
        info = f"\n【{c.get('名称', '?')}】"
        if c.get("类型"):
            info += f" | {c['类型']}"
        if c.get("分布地区"):
            info += f"\n  分布: {c['分布地区'][:300]}"
        if c.get("刷新时间"):
            info += f"\n  刷新: {c['刷新时间']}"
        if c.get("用途"):
            info += f"\n  用途: {c['用途'][:200]}"
        results.append(info)
    return "\n".join(results)


@tool
def query_recipe(name: str) -> str:
    """查询食谱：效果、材料、获取方式。name: 食谱名称（模糊匹配）"""
    recipes = _load_content_json("recipes")
    if not recipes:
        return "食谱数据库为空。"
    matches = [r for r in recipes if name in r.get("名称", "")]
    if not matches:
        names = [r["名称"] for r in recipes if r.get("名称")][:30]
        return f"未找到「{name}」。部分食谱: {', '.join(names)}"
    results = []
    for r in matches[:8]:
        info = f"\n【{r.get('名称', '?')}】"
        if r.get("稀有度"):
            info += f" | {r['稀有度']}星"
        if r.get("效果"):
            info += f"\n  效果: {r['效果'][:200]}"
        if r.get("材料"):
            info += f"\n  材料: {r['材料'][:200]}"
        if r.get("获取方式"):
            info += f"\n  获取: {r['获取方式'][:200]}"
        results.append(info)
    return "\n".join(results)


@tool
def query_food(name: str) -> str:
    """查询食物/料理信息。name: 食物名称（模糊匹配）"""
    foods = _load_content_json("foods")
    if not foods:
        return "食物数据库为空。"
    matches = [f for f in foods if name in f.get("名称", "")]
    if not matches:
        names = [f["名称"] for f in foods[:20]]
        return f"未找到「{name}」。部分食物: {', '.join(names)}"
    results = []
    for f in matches[:10]:
        info = f"\n【{f.get('名称', '?')}】"
        info += f"\n  稀有度: {f.get('稀有度', '?')}星"
        info += f"\n  类型: {f.get('类型', '?')}"
        info += f"\n  效果: {f.get('效果说明', '')[:200]}"
        desc = f.get("介绍", "").replace("<br>", " ")
        info += f"\n  介绍: {desc[:300]}"
        info += f"\n  食材: {f.get('所需食材', '')}"
        info += f"\n  获取: {f.get('获取方式', '')[:200]}"
        if f.get("特殊料理角色"):
            info += f"\n  特殊料理: {f.get('特殊料理角色', '')}"
        results.append(info)
    return "\n".join(results)


@tool
def get_book_metadata(book_name: str) -> str:
    """获取书籍的元数据（作者、版本、体裁、卷数），不返回正文。
    book_name: 书籍名称。当用户问"作者是谁"、"哪个版本"、"什么体裁"等涉及书籍元数据的问题时，优先用此工具而非 load_book_content。"""
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
    meta = best.get("metadata", {})
    lines = [f"\n【{best['title']}】书籍信息"]
    if meta.get("作者"):
        lines.append(f"作者: {meta['作者']}")
    else:
        lines.append(f"作者: 游戏内未提及")
    if meta.get("体裁"):
        lines.append(f"体裁: {meta['体裁']}")
    if meta.get("卷数"):
        lines.append(f"卷数: {meta['卷数']}")
    if meta.get("实装版本"):
        lines.append(f"版本: {meta['实装版本']}")
    return "\n".join(lines)

# -*- coding: utf-8 -*-
"""数据层：知识库加载、全局数据、文本辅助函数。

本模块是数据底座，所有工具和检索模块都依赖这里的知识库与辅助函数。
不依赖 app/ 内其他模块（除 config）。
"""
import os
import re
import json
from collections import OrderedDict
from typing import Dict, List, Optional

from app.config import CONTENT_DIR

# ====== 知识库导入（不可用时降级为空列表）======
try:
    from genshin_knowledge_base import (
        角色知识库, 地区知识库, 主线剧情知识库, 武器知识库,
        任务知识库, 素材知识库, 圣遗物知识库
    )
except ImportError:
    角色知识库, 地区知识库, 主线剧情知识库 = [], [], []
    武器知识库, 任务知识库, 素材知识库, 圣遗物知识库 = [], [], [], []

# ====== NPC 数据（供 query_character 兜底）======
_npcs_data = {}
_npcs_path = os.path.join(CONTENT_DIR, "npcs_processed.json")
if os.path.exists(_npcs_path):
    try:
        with open(_npcs_path, "r", encoding="utf-8") as f:
            _npcs_data = json.load(f)
        print(f"[初始化] NPC 数据已加载: {len(_npcs_data)} 条")
    except Exception as e:
        print(f"[警告] NPC 数据加载失败: {e}")

# ====== 向量知识库 ======
try:
    from kb_vector_store import KBVectorStore
    _vector_store = KBVectorStore()
    _vector_stats = _vector_store.get_stats()
    _vector_total = sum(_vector_stats.values())
    print(f"[初始化] 向量知识库已加载: {_vector_stats}, 总计 {_vector_total} 条")
except Exception as e:
    _vector_store = None
    print(f"[警告] 向量知识库不可用: {e}")

# ====== 内容数据文件路径映射 ======
CONTENT_FILES = {
    "monsters": os.path.join(CONTENT_DIR, "monsters.json"),
    "materials": os.path.join(CONTENT_DIR, "materials.json"),
    "collectibles": os.path.join(CONTENT_DIR, "collectibles.json"),
    "recipes": os.path.join(CONTENT_DIR, "recipes.json"),
    "concepts": os.path.join(CONTENT_DIR, "concepts.json"),
    "foods": os.path.join(CONTENT_DIR, "foods.json"),
    "books": os.path.join(CONTENT_DIR, "books.json"),
}


def _load_content_json(file_key: str) -> List[Dict]:
    """按 key 加载 content_data/ 下的 JSON 文件，失败返回空列表。"""
    path = CONTENT_FILES.get(file_key)
    if not path or not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


# ====== 缓存装饰器（用于 list_all_* 系列工具的结果缓存）======
def _cache_get(cache_var: Optional[str], cache_time: float) -> Optional[str]:
    """检查缓存是否有效：cache_var 不为 None 且未过期（300s）。
    返回 cache_var 表示有效，None 表示需重建。"""
    import time
    if cache_var is not None and (time.time() - cache_time) < 300:
        return cache_var
    return None


# ====== 文本匹配辅助 ======
def _normalize_for_match(s: str) -> str:
    """去除装饰性标点，用于文本匹配。
    中英文书名号、引号、括号等纯装饰性标点被移除，保留语义内容。
    避免"天理的维系者"无法匹配"「天理」的维系者"这类问题。"""
    for ch in "「」『』”“''（）()【】《》":
        s = s.replace(ch, "")
    return s


def _aggregate_map_text(book_name: str) -> str:
    """聚合 lore.json 中的地图文本（如「万国诸卷拾遗」），按地区分组列出子区域。

    「万国诸卷拾遗」是北陆图书馆的地图文本系列，数据存在 lore.json 而非 books.json。
    书籍查询工具在 books.json 中查不到书名时，用本函数判断它是否是地图文本。
    返回空字符串表示该名称不是地图文本；否则返回按地区分组的聚合结果。
    """
    lore_path = os.path.join(CONTENT_DIR, "lore.json")
    if not os.path.exists(lore_path):
        return ""
    try:
        with open(lore_path, "r", encoding="utf-8") as f:
            lore = json.load(f)
    except Exception:
        return ""
    normalized_book = _normalize_for_match(book_name)
    if not normalized_book:
        return ""
    # 收集 title 含该书名的条目，例如 title="万国诸卷拾遗/稻妻 / 鸣神岛"
    entries = [e for e in lore if normalized_book in _normalize_for_match(e.get("title", ""))]
    if not entries:
        return ""
    # 解析 title 结构：[书名, 地区, 子区域]
    region_map: Dict[str, set] = {}
    for e in entries:
        parts = [p.strip() for p in e.get("title", "").split("/") if p.strip()]
        if len(parts) >= 2:
            region = parts[1]
            region_map.setdefault(region, set())
            if len(parts) >= 3:
                region_map[region].add(parts[2])
    if not region_map:
        return ""
    lines = [f"\n「{book_name}」是游戏内地图文本的分类名称，不是一本书籍。按地区收录的区域如下："]
    for region in region_map:
        subs = region_map[region]
        if subs:
            lines.append(f"- {region}：{'、'.join(sorted(subs))}")
        else:
            lines.append(f"- {region}")
    return "\n".join(lines)


def _match_all_in(query: str, text: str) -> bool:
    """检查 text 中是否包含 query 的至少一半词（空格分词）。
    宽松 AND 逻辑：单关键词退化为普通子串匹配；多关键词要求至少 ceil(N/2) 个词在 text 中。
    避免 Plan Agent 构造搜索词时加入多余词导致漏召回。
    匹配前对 query 和 text 做标点归一化。"""
    words = query.split()
    if not words:
        return False
    normalized_text = _normalize_for_match(text)
    matched = sum(1 for w in words if _normalize_for_match(w) in normalized_text)
    threshold = max(1, (len(words) + 1) // 2)  # 至少一半词命中（向上取整）
    return matched >= threshold


# ====== 长任务预处理数据 ======
_quests_processed: Dict[str, dict] = {}


def _load_processed_data():
    """惰性加载 quests_processed.json（长任务的预切片+大纲）。

    用 .update() 而非重新赋值，确保其他模块通过 from app.data import _quests_processed
    拿到的引用能感知到加载结果。
    """
    global _quests_processed
    if _quests_processed:
        return
    path = os.path.join(CONTENT_DIR, "quests_processed.json")
    if not os.path.exists(path):
        return
    try:
        with open(path, "r", encoding="utf-8") as f:
            _quests_processed.update(json.load(f))
        print(f"[预处理] 加载 quests_processed.json ({len(_quests_processed)} 条长任务)")
    except Exception as e:
        print(f"[警告] 加载 quests_processed.json 失败: {e}")


# 角色名黑名单（非角色的 *前缀 行）
_NON_CHARACTER = {'旁白', '系统', '画外音', '系统提示', '任务提示', '说明'}


def _extract_scenes(text: str) -> list:
    """提取场景流转列表（按出场顺序）。兼容两种文本格式。"""
    # 格式1: ====标题====（世界任务/活动任务）
    scenes = []
    for m in re.finditer(r'^====(.+?)====$', text, re.MULTILINE):
        title = m.group(1).strip()
        if title and not re.match(r'^\d{1,2}:\d{2}', title):
            scenes.append(title[:30] + ('...' if len(title) > 30 else ''))
    if scenes:
        return scenes

    # 格式2: [场景描述]（传说任务/魔神任务）
    for m in re.finditer(r'^\[([^\]]{10,80})\]$', text, re.MULTILINE):
        desc = m.group(1).strip()
        if not desc.startswith('{{'):
            scenes.append(desc[:30] + ('...' if len(desc) > 30 else ''))
    return scenes


def _extract_characters(text: str) -> list:
    """提取出场角色（去重，按首次出场顺序排列，含字符偏移量）。"""
    pattern = re.compile(r'^\*([^*\s:：]{1,8})[：:]', re.MULTILINE)
    seen = OrderedDict()
    for match in pattern.finditer(text):
        name = match.group(1).strip()
        if not name or len(name) < 1:
            continue
        if re.search(r'[{}|【】]', name):
            continue
        if name in _NON_CHARACTER:
            continue
        if name not in seen:
            seen[name] = match.start()
    return list(seen.items())


def _build_quest_header(text: str, char_count: int) -> str:
    """为长任务文本（>3000字）生成结构索引头，帮助 Answer Agent 定位关键段落。"""
    if char_count <= 3000:
        return text

    scenes = _extract_scenes(text)
    characters = _extract_characters(text)

    header_parts = [f"[结构概览] 全文长度: {char_count} 字符"]

    if scenes:
        scene_flow = " → ".join(scenes[:8])
        if len(scenes) > 8:
            scene_flow += f" ... (共{len(scenes)}个场景)"
        header_parts.append(f"场景流转: {scene_flow}")

    if characters:
        char_list = ", ".join(
            f"{name}(L{pos // 1000 * 1000})" if pos >= 1000 else f"{name}(L0)"
            for name, pos in characters[:12]
        )
        if len(characters) > 12:
            char_list += f" ... (共{len(characters)}位)"
        header_parts.append(f"出场角色: {char_list}")

    header = "\n".join(header_parts)
    return header + "\n\n--- 以下为任务完整剧情 ---\n\n" + text


# ====== 幕名 → 子任务名映射 ======
# 从 Bilibili Wiki 爬取的魔神任务 / 间章 / 空月之歌 幕→子任务映射
# 格式：幕名 → [子任务标题列表]，子任务标题与 content_data/quests_*.json 中的 title 字段一一对应
ACT_TO_QUESTS: Dict[str, List[str]] = {
    # 序章 捕风的异乡人
    "捕风的异乡人": ["鸟瞰风物", "异常的权柄", "林间相会", "随风而来的骑士", "与轻风同行", "自由之都", "龙灾", "西风骑士团", "昔日的风", "骑士的现场教习", "书页里的电火花"],
    "为了没有眼泪的明天": ["阴影下的蒙德", "不期而遇", "那个绿色的家伙", "听凭风引", "温迪的计划", "温迪的新计划", "逃亡", "幕后谈话", "追逐暗影", "至宝的现状", "遗落之泪", "藏匿之泪", "被夺之泪", "澄净之泪", "与巨龙重逢"],
    "巨龙与自由之歌": ["深渊法师", "障壁", "无人之家", "导光仪式", "在终曲之前", "为了青色的身影", "尾声，风停之后", "尾声的尾声"],
    # 第一章 辞行久远之躯
    "浮世浮生千岩间": ["请仙", "惊变", "望舒", "叠山", "留云", "返尘"],
    "辞行久远之躯": ["往生", "指月", "传香", "壶天", "市井", "归终（任务）", "邀约"],
    "迫近的客星": ["浮城", "玑衡", "孤芳", "离心", "回天", "送仙"],
    "我们终将重逢": ["非自愿的祭献", "无信者的使徒", "不荣誉的试炼", "有隔阂的魂灵"],
    # 第二章 千手百眼，天下人间
    "振袖秋风问红叶": ["冲破雷暴之法", "南十字武斗会", "一路随风"],
    "不动鸣神，恒常乐土": ["起航之日", "异乡人的忏悔录", "离岛逃离计划", "三个心愿", "无意义的等待的意义", "愿仁义之人被仁义以待", "剑道家的道路铺满剑道梦的碎片", "于狱中绽放之花"],
    "无念无想，泡影断灭": ["在审判的雷鸣声中", "以反抗之人的名义"],
    "千手百眼，天下人间": ["剑与鱼与反抗者", "渴求神明注视之人", "邪眼", "眷属的践行", "定罪公文", "愚忠与愚勇", "御前决斗", "千手百眼", "愿望"],
    "回响渊底的安魂曲": ["渊底不期的再会", "被守护者的灵柩", "因提瓦特的记忆", "黑蛇骑士的荣光"],
    # 第三章 虚空劫灰往世书
    "穿越烟帷与暗林": ["林中遇变", "疗养观察", "痼疾", "缄默的求知者", "智慧之神的踪影", "失物匿于繁华", "近在咫尺的目标"],
    "千朵玫瑰带来的黎明": ["终将到来的花神诞祭", "已然来临的花神诞祭", "流转存续的花神诞祭", "轮回意志的花神诞祭", "因果命运的花神诞祭", "空幻回响的花神诞祭", "终将结束的花神诞祭"],
    "迷梦与空幻与欺骗": ["如凯旋的英雄一般", "来自某位「神明」的凝视", "剑拔弩张四人众"],
    "赤土之王与三朝圣者": ["失踪的守村人", "魔鳞病医院的哭声", "热沙中的秘密"],
    "虚空鼓动，劫火高扬": ["行于黎明前夜幕里", "如临神之畔", "识藏日", "意识之舟所至之处", "请饮下祝胜之酒"],
    "卡利贝尔": ["如命运般的相逢", "嘲弄命运的资格", "命运尽头的垂泪者", "既已写下的命运"],
    # 第四章 罪人舞步旋
    "白露与黑潮的序诗": ["独舞者的序幕", "细雨眷恋之城", "聚光灯下谎言成影"],
    "仿若无因飘落的轻雨": ["如同昔日的微茫月明", "真相流逝于雨后", "当一切回归于水"],
    "向深水中的晨星": ["锋芒难掩的茶会", "梅洛彼得堡", "匿于日常之禁忌", "深海的迷路者"],
    "谕示胎动的终焉之刻": ["探入水底迷雾", "真相隐于影中", "守秘者与禁区", "灾厄的脚步", "片刻安宁"],
    "罪人舞步旋": ["怒涛之灾", "相见亦是离别", "狩猎者，预见者", "审判日", "黑潮与白露的歌剧", "终幕礼"],
    "睡前故事": ["积存已久的委托", "不应存在的记忆", "以世界之格的诉说"],
    # 第五章 炽烈的还魂诗
    "荣花与炎日之途": ["纳塔！新的旅程", "归火圣夜巡礼", "温泉之乡"],
    "黑石湮落白石下": ["抉择", "古名寻回之旅", "生命的回响", "坠入永夜", "过去与未来"],
    "镜与谜烟的彼方": ["皆为崇高之名", "向着迷烟飘往之处", "摇曳灯火一分为二"],
    "命定将焚的虹光": ["秘源之下", "共睹那日之将落", "席卷而来的暗潮", "绝望高悬天之上", "我们不会孤军奋战", "名为「命运」的燃料"],
    "万火归一": ["为同一片土地"],
    "炽烈的还魂诗": ["地中残景", "正如日出日落", "星与火的征途", "众望所归", "当一切镌刻成碑"],
    "你存在的时空": ["命运始动的钥匙", "「救世主」", "你所不在的时空"],
    # 空月之歌
    "归途": ["墟火", "阴燃", "逆焰"],
    "雪浪与苍林之舞": ["月亮升起的地方", "于月光下重逢", "在夜的阴影中"],
    "尘与灯的挽歌": ["轰鸣与暗涌", "窥见记忆的暗面", "曾有人追猎月亮", "灰白的秩序熊熊燃烧", "遥不可及的安息"],
    "不存在的国土": ["匿于阴影", "特别行动", "如月长存"],
    "回望湮灭的月光": ["皆为预言", "蛇与蝎的亡命舞", "命运的回声"],
    "终北的夜行诗": ["月影轮番登台", "无法传达的涟漪"],
    "散于晨雾的月芒": ["名于何处？", "月亮回家的夜晚", "回到月亮上去"],
    "如果在冬夜，一个旅人": ["无月之夜", "你我交错的时空", "循着过往的足迹"],
    "真实之月": ["最初的那抹月光", "月之将坠", "空月归乡"],
    "身土坏空，五蕴识转": ["花于何处醒来", "通向自我的歧途", "旧影重现"],
    "虚空劫灰往世书（系列任务）": ["幽暗时分", "妄念与真知的通天塔", "虚空劫灰往世书", "你的往事如一座花园"],
    # 间章
    "风起鹤归": ["琼台玉阁", "鸣海栖霞", "往事如尘", "此心安处"],
    "危途疑踪": ["意外之客", "岩下迷境", "危机四伏", "穷途末路", "绝处逢生"],
    "倾落伽蓝": ["夜中飞鸟坠于三段", "乱世轮舞", "幕切——倾奇之末", "如朝露一般"],
    "悖理": ["第一及第二罪行", "众生的渴求", "园丁"],
}

# ====== 活动剧情→传说任务别名映射 ======
# 以下活动剧情常被玩家戏称为某角色的"传说任务"。
# 单向映射：用户查询时可按别名定位到活动剧情，但回答中禁止称其为传说任务。
ACTIVITY_LEGENDARY_ALIAS: Dict[str, List[str]] = {
    "兹白传说任务":    ["在云间", "在岩间", "在人间", "白马闲游记"],
    "兹白的传说任务":  ["在云间", "在岩间", "在人间", "白马闲游记"],
    "胡桃传说任务2":   ["璃月港佳节兴，八奇现瘴疠隐", "往生堂三日无主，玉京台遣将调兵", "奇门术息灾平昏寿，护摩法净世定幽冥", "终回：八奇炼桃都"],
    "胡桃传2":        ["璃月港佳节兴，八奇现瘴疠隐", "往生堂三日无主，玉京台遣将调兵", "奇门术息灾平昏寿，护摩法净世定幽冥", "终回：八奇炼桃都"],
    "胡桃的第二个传说任务": ["璃月港佳节兴，八奇现瘴疠隐", "往生堂三日无主，玉京台遣将调兵", "奇门术息灾平昏寿，护摩法净世定幽冥", "终回：八奇炼桃都"],
    "卡维传说任务":    ["蝶去蝶来", "沙起沙落", "人聚人散", "落幕时分"],
    "卡维的传说任务":  ["蝶去蝶来", "沙起沙落", "人聚人散", "落幕时分"],
    "菲米尼传说任务":  ["水妖的猜想", "王子的国度", "奇迹的冠冕"],
    "菲米尼的传说任务":["水妖的猜想", "王子的国度", "奇迹的冠冕"],
    "夏沃蕾传说任务":  ["划破宁静的枪响", "景框内外的虚实", "雾中隐现的孤岛", "何处盛放的蔷薇", "两个铳枪手的凯旋"],
    "夏沃蕾的传说任务":["划破宁静的枪响", "景框内外的虚实", "雾中隐现的孤岛", "何处盛放的蔷薇", "两个铳枪手的凯旋"],
    "布伦妮传说任务":  ["野林猪与小魔女", "若你遗忘梦的入口", "永不失效的魔法"],
    "布伦妮的传说任务":["野林猪与小魔女", "若你遗忘梦的入口", "永不失效的魔法"],
    "嘉明传说任务":    ["风莺梳春，开天呈祥", "描露摹晖，诉愿云海", "故鸢吹归，再宿堂楼", "人来人往"],
    "嘉明的传说任务":  ["风莺梳春，开天呈祥", "描露摹晖，诉愿云海", "故鸢吹归，再宿堂楼", "人来人往"],
}

# 以下子任务标题在知识库中的实际分类是活动剧情（非传说任务）
# 回答时禁止称其为传说任务，必须标注为活动剧情
FORCE_ACTIVITY_TITLES: set = {
    "在云间", "在岩间", "在人间", "白马闲游记",
    "璃月港佳节兴，八奇现瘴疠隐", "往生堂三日无主，玉京台遣将调兵",
    "奇门术息灾平昏寿，护摩法净世定幽冥", "终回：八奇炼桃都",
    "蝶去蝶来", "沙起沙落", "人聚人散", "落幕时分",
    "水妖的猜想", "王子的国度", "奇迹的冠冕",
    "划破宁静的枪响", "景框内外的虚实", "雾中隐现的孤岛",
    "何处盛放的蔷薇", "两个铳枪手的凯旋",
    "野林猪与小魔女", "若你遗忘梦的入口", "永不失效的魔法",
    "风莺梳春，开天呈祥", "描露摹晖，诉愿云海", "故鸢吹归，再宿堂楼", "人来人往",
}

# ====== 术语别名表 ======
# 同一实体在游戏文本中可能有多种称呼，搜A时自动也搜B。
# 维护：遇到新的同实体异名时追加。
TERM_ALIASES: Dict[str, List[str]] = {
    "天理的维系者": ["陌生的神灵", "阿斯莫代"],
}

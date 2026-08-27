#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
主线剧情知识库数据（动态生成）

数据源：content_data/quests_魔神任务.json
按 章 -> 幕 -> 任务 层级聚合成 主线剧情知识库，
供 query_story / search_all / web 统计使用。
如果数据文件缺失，降级为空列表。
"""
import os
import re
import json
from collections import OrderedDict

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_QUEST_FILE = os.path.join(_BASE_DIR, "content_data", "quests_魔神任务.json")
_PROCESSED_FILE = os.path.join(_BASE_DIR, "content_data", "quests_processed.json")
_ORDER_FILE = os.path.join(_BASE_DIR, "content_data", "魔神任务架构.txt")

# 章节顺序（用于把 Wiki 原始顺序整理为剧情顺序）
_CHAPTER_ORDER = {
    "开场动画": -1,
    "序章": 0,
    "第一章": 1,
    "第二章": 2,
    "第三章": 3,
    "第四章": 4,
    "第五章": 5,
    "空月之歌": 6,
    "第七章": 7,
    "间章": 8,
}

# 部分新章节没有 act_no，使用已知幕名顺序兜底
_FALLBACK_ACT_ORDER = {
    "无神怜爱的雪国": 1,
    "死魂灵的夜曲": 2,
}

# 权威文件里的章节副标题（当数据只有章节编号、没有独立章节名时使用）
_CHAPTER_SUBTITLE = {
    "第七章": "无神怜爱的雪国",
}

# 权威任务顺序（来源：原神魔神任务架构.txt）
# 仅收录当前源数据顺序与权威文件不一致的幕；其他幕按任务编号/原顺序即可。
_FALLBACK_TASK_ORDER = {
    ("第三章", "迷梦与空幻与欺骗"): [
        "如凯旋的英雄一般",
        "来自某位「神明」的凝视",
        "剑拔弩张四人众",
    ],
    ("第四章", "白露与黑潮的序诗"): [
        "独舞者的序幕",
        "细雨眷恋之城",
        "聚光灯下谎言成影",
    ],
    ("第四章", "罪人舞步旋"): [
        "怒涛之灾",
        "相见亦是离别",
        "狩猎者，预见者",
        "审判日",
        "黑潮与白露的歌剧",
        "终幕礼",
    ],
    ("第五章", "镜与谜烟的彼方"): [
        "皆为崇高之名",
        "向着迷烟飘往之处",
        "摇曳灯火一分为二",
    ],
    ("第五章", "命定将焚的虹光"): [
        "秘源之下",
        "共睹那日之将落",
        "席卷而来的暗潮",
        "绝望高悬天之上",
        "我们不会孤军奋战",
        "名为「命运」的燃料",
    ],
    ("第五章", "炽烈的还魂诗"): [
        "地中残景",
        "正如日出日落",
        "星与火的征途",
        "众望所归",
        "当一切镌刻成碑",
    ],
    ("空月之歌", "尘与灯的挽歌"): [
        "轰鸣与暗涌",
        "窥见记忆的暗面",
        "曾有人追猎月亮",
        "灰白的秩序熊熊燃烧",
        "遥不可及的安息",
    ],
    ("空月之歌", "终北的夜行诗"): [
        "月影轮番登台",
        "无法传达的涟漪",
    ],
    ("空月之歌", "散于晨雾的月芒"): [
        "名于何处？",
        "月亮回家的夜晚",
        "回到月亮上去",
    ],
    ("间章", "危途疑踪"): [
        "意外之客",
        "岩下迷境",
        "危机四伏",
        "穷途末路",
        "绝处逢生",
    ],
}

# 解析权威架构文件（content_data/魔神任务架构.txt），得到全量 章->幕->任务 顺序。
# 若文件缺失或解析失败，退回上方手工维护的差异幕顺序。
def _load_task_order_from_file():
    if not os.path.exists(_ORDER_FILE):
        return {}
    try:
        with open(_ORDER_FILE, "r", encoding="utf-8") as f:
            lines = [line.strip() for line in f if line.strip()]
    except Exception:
        return {}
    order = {}
    cur_chapter = None
    cur_act = None
    for line in lines:
        if line.startswith("开场动画："):
            cur_chapter = "开场动画"
            title = line.split("：", 1)[1].split("（")[0].strip()
            if title:
                order.setdefault((cur_chapter, "开场动画"), []).append(title)
            cur_act = None
            continue
        m = re.match(r'^(序章|第一章|第二章|第三章|第四章|第五章|第七章|间章|空月之歌)(?:\s+(.*))?$', line)
        if m:
            cur_chapter = m.group(1)
            cur_act = None
            continue
        m = re.match(r'^(序幕|幕间|序奏|第一幕|第二幕|第三幕|第四幕|第五幕|第六幕|第七幕|第八幕|第九幕|第十幕)[：:]\s*(.*)$', line)
        if m and cur_chapter is not None:
            cur_act = m.group(2).strip()
            order.setdefault((cur_chapter, cur_act), [])
            continue
        m = re.match(r'^(\d+)\s*(.*)$', line)
        if m and cur_chapter is not None and cur_act is not None:
            order[(cur_chapter, cur_act)].append(m.group(2).strip())
    return order


_TASK_ORDER = _load_task_order_from_file() or _FALLBACK_TASK_ORDER


_CN_NUM = {
    "零": 0, "一": 1, "二": 2, "三": 3, "四": 4,
    "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
}


def _parse_cn_number(text: str):
    """把 '第五幕' / '第十幕' 等中文序号解析为 int，失败返回 None。"""
    if not text:
        return None
    m = re.search(r'[零一二三四五六七八九十]+', text)
    if not m:
        return None
    s = m.group()
    # 十 / 十X / X十 / X十Y
    if s == "十":
        return 10
    if s.startswith("十"):
        tail = _CN_NUM.get(s[1:], 0) if len(s) > 1 else 0
        return 10 + tail
    if s.endswith("十"):
        head = _CN_NUM.get(s[0], 0)
        return head * 10 if head else 10
    # 一位或两位（如 十一 已处理，十一中没有这种情况）
    if len(s) == 1:
        return _CN_NUM.get(s, None)
    return None


def _act_sort_key(act_no: str, act_name: str):
    """幕排序：序幕/序奏 为 0，数字幕按数字。

    第五章的“幕间：万火归一”在权威架构中位于第四幕与第五幕之间，
    因此按 4.5 排序，而不是放到最后。
    """
    if act_no in ("序幕", "序奏", "序章"):
        return (0, act_name)
    if act_no == "幕间":
        return (4.5, act_name)
    n = _parse_cn_number(act_no)
    if n is not None:
        return (n, act_name)
    # 没有 act_no 时，尝试按已知幕名顺序
    n = _FALLBACK_ACT_ORDER.get(act_name)
    if n is not None:
        return (n, act_name)
    return (500, act_name)


def _normalize_task_title(title: str) -> str:
    """去掉 Wiki 消歧义后缀，便于与权威架构文件中的任务名对齐。"""
    return (title or "").replace("（任务）", "").replace("(任务)", "").strip()


def _task_order_index(chapter_key: str, act_name: str, title: str):
    """按权威任务顺序返回排序索引；未收录的幕返回 None。"""
    key = (chapter_key, act_name)
    order = _TASK_ORDER.get(key)
    if not order:
        return None
    name = _normalize_task_title(title)
    try:
        return order.index(name)
    except ValueError:
        return None


def _task_sort_key(chapter_key: str, act_name: str, title: str, order: list):
    """权威任务顺序排序键：已收录按索引，未收录排最后。"""
    idx = _task_order_index(chapter_key, act_name, title)
    return (idx is None, idx if idx is not None else len(order))


def _chapter_key(meta: dict):
    """返回 (chapter_order, chapter_no, chapter_name) 用于章节排序。"""
    no = meta.get("chapter_no", "") or ""
    name = meta.get("chapter_name", "") or ""
    order = _CHAPTER_ORDER.get(no)
    if order is None:
        order = _CHAPTER_ORDER.get(name, 100)
    return (order, no, name)


def _unique_preserve(items):
    seen = set()
    result = []
    for x in items:
        if not x:
            continue
        for part in str(x).replace("、", ",").split(","):
            part = part.strip()
            if part and part not in seen:
                seen.add(part)
                result.append(part)
    return result


def _build_main_story():
    if not os.path.exists(_QUEST_FILE):
        return []
    try:
        with open(_QUEST_FILE, "r", encoding="utf-8") as f:
            quests = json.load(f)
    except Exception:
        return []
    if not isinstance(quests, list):
        return []

    # 尝试加载长任务摘要
    processed = {}
    if os.path.exists(_PROCESSED_FILE):
        try:
            with open(_PROCESSED_FILE, "r", encoding="utf-8") as f:
                processed = json.load(f)
        except Exception:
            processed = {}

    # 按 章 -> 幕 聚合
    chapters = OrderedDict()
    for q in quests:
        meta = q.get("metadata", {}) or {}
        title = q.get("title", "") or meta.get("任务名称", "")
        if not title:
            continue
        ck = _chapter_key(meta)
        act_no = meta.get("act_no", "") or ""
        act_name = meta.get("act_name", "") or ""
        # 第七章等新章节可能缺 act_no，按已知幕名补上正式幕编号
        if not act_no:
            fallback_no = _FALLBACK_ACT_ORDER.get(act_name)
            if fallback_no == 1:
                act_no = "第一幕"
            elif fallback_no == 2:
                act_no = "第二幕"
        # 部分条目 chapter_no 为空但 chapter_name 是“第七章”，此时章节名用原始 chapter_name
        chapter_name = meta.get("chapter_name", "") or ck[1] or "未命名章节"
        raw_no = meta.get("chapter_no", "") or ck[1]
        if not raw_no and re.match(r'^第[一二三四五六七八九十]+章$', chapter_name):
            raw_no = chapter_name
        chapter_no = raw_no
        if chapter_name == chapter_no and chapter_no in _CHAPTER_SUBTITLE:
            chapter_name = _CHAPTER_SUBTITLE[chapter_no]
        key = (chapter_no, chapter_name)
        chapters.setdefault(key, {
            "chapter_no": chapter_no,
            "chapter_name": chapter_name,
            "acts": OrderedDict(),
        })
        act_key = _act_sort_key(act_no, act_name)
        acts = chapters[key]["acts"]
        acts.setdefault((act_no, act_name, act_key), {
            "act_no": act_no,
            "act_name": act_name or ("开场动画" if (chapter_no == "开场动画" or chapter_name == "开场动画") else "未命名幕"),
            "act_key": act_key,
            "tasks": [],
        })
        acts[(act_no, act_name, act_key)]["tasks"].append({
            "title": title,
            "region": meta.get("任务地区", ""),
            "roles": meta.get("出场人物", ""),
            "description": meta.get("任务描述", ""),
            "summary": processed.get(title, {}).get("summary", ""),
            "version": meta.get("所属版本", ""),
        })

    result = []
    for (chapter_no, chapter_name), ch in chapters.items():
        acts = list(ch["acts"].values())
        # 同章内按幕排序
        acts.sort(key=lambda a: a["act_key"])

        # 幕内任务按权威架构顺序排序（未收录的幕保持原顺序）
        chapter_key = chapter_no or chapter_name
        for act in acts:
            order = _TASK_ORDER.get((chapter_key, act["act_name"]))
            if order is not None:
                act["tasks"].sort(key=lambda t: _task_sort_key(
                    chapter_key, act["act_name"], t["title"], order
                ))

        # 聚合地区、角色、任务
        regions = []
        roles = []
        task_titles = []
        for act in acts:
            for t in act["tasks"]:
                if t["region"]:
                    regions.append(t["region"])
                if t["roles"]:
                    roles.append(t["roles"])
                task_titles.append(_normalize_task_title(t["title"]))
        region = _unique_preserve(regions)
        role_list = _unique_preserve(roles)

        # 剧情概要：每幕用长任务摘要；无摘要则退回任务描述
        overview_parts = []
        for act in acts:
            summaries = [t["summary"] for t in act["tasks"] if t["summary"]]
            if summaries:
                overview_parts.append(f"{act['act_name']}：{summaries[0]}")
            else:
                descs = [t["description"] for t in act["tasks"] if t["description"]]
                if descs:
                    overview_parts.append(f"{act['act_name']}：{descs[0]}")
        overview = "\n".join(overview_parts)
        if len(overview) > 2400:
            overview = overview[:2400] + "……"

        result.append({
            "章节名称": chapter_name,
            "章节编号": chapter_no,
            "所属地区": "、".join(region) if region else "",
            "主要角色": role_list,
            "剧情概要": overview,
            "关键事件": task_titles,
            "幕列表": [
                {
                    "幕编号": act["act_no"],
                    "幕名称": act["act_name"],
                    "任务": [_normalize_task_title(t["title"]) for t in act["tasks"]],
                }
                for act in acts
            ],
            "_chapter_order": _CHAPTER_ORDER.get(chapter_no,
                                  _CHAPTER_ORDER.get(chapter_name, 100)),
            "_source_count": len(task_titles),
        })

    # 按章节顺序输出
    result.sort(key=lambda r: (r["_chapter_order"], r.get("_source_count", 0)))
    # 移除内部排序字段
    for r in result:
        r.pop("_chapter_order", None)
        r.pop("_source_count", None)
    return result


主线剧情知识库 = _build_main_story()

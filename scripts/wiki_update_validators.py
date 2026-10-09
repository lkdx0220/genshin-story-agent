#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""自动更新完整性校验器。

只做读取和判断，不写任何数据；供 daily_wiki_update.py 在 dry-run 阶段调用。

校验对象：
- B站 Wiki raw wikitext
- 米游社观测枢 entry_page JSON

设计原则：
- 校验规则集中在本文件，便于审查和调整；
- 任何字段缺失都返回 ok=False + reasons，不做自动修补；
- 不执行外部命令、不访问网络。
"""
import json
import re
from typing import Any, Dict, List, Tuple


# ====== 通用工具 ======

def clean_wiki_markup(text: str) -> str:
    """去掉注释、常见模板、HTML 标签和链接语法，只保留可读文本。"""
    if not text:
        return ""
    text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\{\{[^{}]*\}\}", "", text)
    text = re.sub(r"\[\[([^\]|]+)\|([^\]]+)\]\]", r"\2", text)
    text = re.sub(r"\[\[([^\]]+)\]\]", r"\1", text)
    text = text.replace("'''", "")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def wiki_template_field(wikitext: str, field: str) -> str:
    """提取 wikitext 中 |field=... 的值，支持多行和值内含简单模板。"""
    if not wikitext:
        return ""
    pattern = re.compile(
        r"\|\s*" + re.escape(field) + r"\s*=\s*(.*?)(?=\n\s*\||\n\s*\}\}|$)",
        re.DOTALL,
    )
    match = pattern.search(wikitext)
    if not match:
        return ""
    return match.group(1).strip()


def wiki_section(wikitext: str, heading: str) -> str:
    """提取 ==heading== 到下一个顶级 == 之间的内容。"""
    if not wikitext:
        return ""
    pattern = re.compile(
        r"==\s*" + re.escape(heading) + r"\s*==\s*\n(.*?)(?=\n==[^=]|\Z)",
        re.DOTALL,
    )
    match = pattern.search(wikitext)
    return match.group(1).strip() if match else ""


def is_redirect(wikitext: str) -> bool:
    return bool(re.match(r"\s*#(?:REDIRECT|重定向)\s*", wikitext or "", re.I))


def is_disambiguation(wikitext: str) -> bool:
    text = wikitext or ""
    return "{{消歧义" in text or "{{Disambiguation" in text or "{{disambig" in text.lower()


def is_quest_container(wikitext: str) -> bool:
    text = wikitext or ""
    if "{{系列任务" not in text and "{{多重系列任务" not in text:
        return False
    return "==任务剧情==" not in text


def nonempty(value: str, min_len: int = 1) -> bool:
    return len(clean_wiki_markup(value or "")) >= min_len


def _result(ok: bool, reasons: List[str], details: Dict[str, Any] = None) -> Dict[str, Any]:
    return {
        "ok": ok,
        "reasons": reasons,
        "details": details or {},
    }


# ====== B站 Wiki 校验 ======

def validate_bwiki(category: str, title: str, wikitext: str, rules: Dict[str, Any]) -> Dict[str, Any]:
    """B站 Wiki 原始 wikitext 按分类校验。"""
    if not wikitext or not wikitext.strip():
        return _result(False, ["页面正文为空"])
    if is_redirect(wikitext):
        return _result(True, [], {"skipped": "redirect"})
    if is_disambiguation(wikitext):
        return _result(True, [], {"skipped": "disambiguation"})
    if category == "quests" and is_quest_container(wikitext):
        return _result(True, [], {"skipped": "quest_container"})
    if category == "roles" and "{{角色" not in wikitext:
        return _result(True, [], {"skipped": "not_role_template"})

    if category == "roles":
        return _validate_bwiki_role(title, wikitext, rules)
    if category == "npcs":
        return _validate_bwiki_npc(title, wikitext, rules)
    if category == "quests":
        return _validate_bwiki_quest(title, wikitext, rules)
    if category == "map_text":
        return _validate_bwiki_map_text(title, wikitext, rules)
    return _validate_bwiki_generic(category, title, wikitext, rules)


def _validate_bwiki_role(title: str, wikitext: str, rules: Dict[str, Any]) -> Dict[str, Any]:
    required = ["角色详细"] + [f"角色故事{i}" for i in range(1, 6)]
    missing = [field for field in required if not nonempty(wiki_template_field(wikitext, field), 1)]
    special_fields = ["神之眼描述", "其他", "冒险笔记名称", "冒险笔记"]
    has_special = any(nonempty(wiki_template_field(wikitext, field), 1) for field in special_fields)
    reasons = []
    for field in missing:
        reasons.append(f"缺少或为空：{field}")
    if not has_special:
        reasons.append("缺少神之眼/其他/冒险笔记内容")
    details = {
        "missing_fields": missing,
        "has_special": has_special,
        "required_story_count": 5,
    }
    return _result(not reasons, reasons, details)


def _validate_bwiki_npc(title: str, wikitext: str, rules: Dict[str, Any]) -> Dict[str, Any]:
    strong_fields = ["职业", "所属组织", "种族", "对话赠礼", "相关系统"]
    values = {field: wiki_template_field(wikitext, field) for field in strong_fields}
    has_dialogue = bool(
        "{{NPC对话" in wikitext
        or "==NPC对话==" in wikitext
        or nonempty(wiki_template_field(wikitext, "对话"), 1)
    )
    nonempty_fields = [field for field, value in values.items() if nonempty(value, 1)]
    reasons = []
    if not nonempty_fields and not has_dialogue:
        reasons.append("除名称外没有可用的NPC信息（职业/组织/种族/赠礼/对话均为空）")
    details = {
        "nonempty_fields": nonempty_fields,
        "has_dialogue": has_dialogue,
    }
    return _result(not reasons, reasons, details)


def _validate_bwiki_quest(title: str, wikitext: str, rules: Dict[str, Any]) -> Dict[str, Any]:
    text = clean_wiki_markup(wiki_section(wikitext, "任务剧情"))
    min_len = int(rules.get("quest_min_text_len", 80))
    reasons = []
    if not nonempty(wiki_template_field(wikitext, "任务名称"), 1) and not title:
        reasons.append("任务名称为空")
    if len(text) < min_len:
        reasons.append(f"任务剧情内容过短或为空（有效长度 {len(text)}，要求至少 {min_len}）")
    details = {
        "text_len": len(text),
        "has_series": nonempty(wiki_template_field(wikitext, "系列任务"), 1),
        "has_number": nonempty(wiki_template_field(wikitext, "任务编号"), 1),
    }
    return _result(not reasons, reasons, details)


def _validate_bwiki_map_text(title: str, wikitext: str, rules: Dict[str, Any]) -> Dict[str, Any]:
    headings = re.findall(r"^={3,5}\s*(.+?)\s*={3,5}\s*$", wikitext or "", flags=re.M)
    body = clean_wiki_markup(re.sub(r"^=+.*?=+$", "", wikitext or "", flags=re.M))
    min_len = int(rules.get("map_text_min_len", 500))
    reasons = []
    if len(body) < min_len:
        reasons.append(f"地图文本正文过短（有效长度 {len(body)}，要求至少 {min_len}）")
    if len(headings) < int(rules.get("map_text_min_headings", 3)):
        reasons.append(f"地图文本标题结构过少（{len(headings)} 个标题）")
    details = {
        "text_len": len(body),
        "heading_count": len(headings),
    }
    return _result(not reasons, reasons, details)


def _validate_bwiki_generic(category: str, title: str, wikitext: str, rules: Dict[str, Any]) -> Dict[str, Any]:
    field_map = {
        "weapons": ["介绍", "故事", "描述"],
        "artifacts": ["故事", "介绍", "描述"],
        "books": ["书籍内容", "内容", "正文", "介绍"],
        "materials": ["描述", "介绍", "来源"],
        "monsters": ["介绍", "描述", "故事"],
        "foods": ["介绍", "描述", "效果"],
    }
    fields = field_map.get(category, ["介绍", "描述", "内容", "正文"])
    nonempty_fields = [field for field in fields if nonempty(wiki_template_field(wikitext, field), 10)]
    reasons = []
    if not nonempty_fields:
        reasons.append(f"缺少有效内容字段：{'/'.join(fields)}")
    details = {"nonempty_fields": nonempty_fields}
    return _result(not reasons, reasons, details)


# ====== 米游社观测枢校验 ======

def _mihoyo_module_texts(page: Dict[str, Any]) -> Dict[str, str]:
    """把 entry_page 的 modules 提取为 模块名 -> 纯文本。"""
    result: Dict[str, str] = {}
    for module in (page or {}).get("modules") or []:
        name = str(module.get("name") or "")
        texts: List[str] = []
        for component in module.get("components") or []:
            data_raw = component.get("data")
            if isinstance(data_raw, str):
                try:
                    data = json.loads(data_raw)
                except Exception:
                    data = data_raw
            else:
                data = data_raw
            texts.extend(_collect_texts(data))
        merged = clean_wiki_markup("\n".join(t for t in texts if t))
        if name and merged:
            result[name] = merged
    return result


def _collect_texts(data: Any) -> List[str]:
    """递归收集 JSON 里的文本字段，忽略 https 链接和图片地址。"""
    texts: List[str] = []
    if isinstance(data, str):
        if not data.startswith("http") and len(data.strip()) >= 2:
            texts.append(data)
    elif isinstance(data, dict):
        for key, value in data.items():
            if key in {"audio_url", "avatar_pc", "avatar_m", "icon", "url"}:
                continue
            texts.extend(_collect_texts(value))
    elif isinstance(data, list):
        for item in data:
            texts.extend(_collect_texts(item))
    return texts


def validate_mihoyo(channel: str, title: str, page: Dict[str, Any], rules: Dict[str, Any]) -> Dict[str, Any]:
    """米游社 entry_page 按频道校验。"""
    if not isinstance(page, dict):
        return _result(False, ["详情页结构为空"])
    modules = _mihoyo_module_texts(page)

    if channel == "roles":
        reasons = []
        if not nonempty(modules.get("角色详细", ""), 20):
            reasons.append("缺少或为空：角色详细")
        story_count = sum(1 for i in range(1, 6) if nonempty(modules.get(f"角色故事{i}", ""), 20))
        if story_count < int(rules.get("mihoyo_role_min_story", 5)):
            reasons.append(f"角色故事完整度不足（{story_count}/5）")
        has_special = any(
            nonempty(text, 20)
            for name, text in modules.items()
            if name not in {"角色详细", "角色故事1", "角色故事2", "角色故事3", "角色故事4", "角色故事5"}
            and name not in {"基础信息", "角色突破", "推荐装备", "攻略推荐", "天赋", "命之座", "角色展示", "名片", "特殊料理", "角色CV", "配音展示", "角色宣发时间轴", "角色媒体资料", "关联词条", "天赋演示", "角色关系网"}
        )
        if not has_special:
            reasons.append("缺少神之眼/特殊档案内容")
        details = {"module_count": len(modules), "story_count": story_count}
        return _result(not reasons, reasons, details)

    if channel == "npcs":
        meaningful = [name for name, text in modules.items() if nonempty(text, 10)]
        strong = [name for name in meaningful if name not in {"基础信息", "npc_base_info", "base_info"}]
        reasons = []
        if not strong:
            reasons.append("NPC 只有名称/基础栏，没有对话、任务或其它正文信息")
        details = {"module_count": len(modules), "meaningful_modules": meaningful[:20]}
        return _result(not reasons, reasons, details)

    if channel in {"quests", "map_text"}:
        text = "\n".join(modules.values())
        min_len = int(rules.get(f"mihoyo_{channel}_min_len", 80))
        reasons = []
        if len(text) < min_len:
            reasons.append(f"正文内容过短或为空（有效长度 {len(text)}，要求至少 {min_len}）")
        details = {"text_len": len(text), "module_count": len(modules)}
        return _result(not reasons, reasons, details)

    # 其它频道先做通用非空校验
    text = "\n".join(modules.values())
    if len(text) < int(rules.get("mihoyo_generic_min_len", 30)):
        return _result(False, [f"详情页正文过短（{len(text)}）"], {"text_len": len(text)})
    return _result(True, [], {"text_len": len(text)})

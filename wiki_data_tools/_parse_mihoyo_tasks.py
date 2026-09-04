# -*- coding: utf-8 -*-
"""
米游社观测枢 7.0 任务解析脚本。

输入：content_data/mihoyo_tasks_raw.json（由 _fetch_mihoyo_tasks.py 抓取）
输出：把缺失的 7.0 世界任务/限时任务写入 content_data/quests_世界任务.json
      或 content_data/quests_活动剧情.json；主线的章/幕子任务已在本地存有则不重复写入。

用法：
  python _parse_mihoyo_tasks.py                 # 备份后解析并写入
  python _parse_mihoyo_tasks.py --dry-run       # 只打印计划不写文件
"""

import argparse
import json
import os
import re
import html
import difflib
import glob
import time

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(SCRIPT_DIR)
CONTENT_DIR = os.path.join(BASE_DIR, "content_data")
RAW_PATH = os.path.join(CONTENT_DIR, "mihoyo_tasks_raw.json")

STANDARD_MODULES = {
    "任务概述", "任务过程", "任务奖励", "地图说明", "攻略方法",
    "剧情对话", "任务流程", "任务概览", "任务目标",
}

SKIP_DATA_KEYS = {
    "root_id", "child_ids", "image", "icon", "tab_name",
    "component_id", "style", "layout", "id", "switch",
    "is_show_switch", "without_border", "repeated", "is_submodule",
    "origin_module_id", "can_delete", "is_hidden", "rich_text_editing",
    "is_poped", "is_customize_name", "is_abstract", "show_switch",
    "module_id", "module_name", "name", "component_type",
}

VALUE_EMPTY = {"", "暂无", "无", "-", "—"}


def log(msg):
    print(f"{time.strftime('%H:%M:%S')} {msg}", flush=True)


def plain(s):
    if not isinstance(s, str):
        return ""
    s = html.unescape(s)
    s = re.sub(r"<br\s*/?>", "\n", s, flags=re.I)
    s = re.sub(r"</p>", "\n", s, flags=re.I)
    s = re.sub(r"</li>", "\n", s, flags=re.I)
    s = re.sub(r"</div>", "\n", s, flags=re.I)
    s = re.sub(r"</h[1-6]>", "\n", s, flags=re.I)
    s = re.sub(r"</a>", "\n", s, flags=re.I)
    s = re.sub(r"<[^>]+>", "", s)
    s = s.replace("&nbsp;", " ")
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n\s*\n+", "\n", s)
    return s.strip()


def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def norm_data(d):
    if isinstance(d, str):
        try:
            return json.loads(d)
        except Exception:
            return d
    return d


def clean_value(v):
    if isinstance(v, list):
        parts = []
        for x in v:
            t = clean_value(x)
            if t and t not in parts:
                parts.append(t)
        return "，".join(parts)
    t = plain(str(v))
    if t in VALUE_EMPTY:
        return ""
    return t


def parse_module_blocks(module):
    """返回 [(key_or_None, text), ...]，只保留人类可读内容。"""
    if not isinstance(module, dict):
        return []
    blocks = []
    for comp in module.get("components", []) or []:
        if not isinstance(comp, dict):
            continue
        data = norm_data(comp.get("data", ""))
        if isinstance(data, str):
            t = plain(data)
            if t:
                blocks.append((None, t))
            continue
        if not isinstance(data, dict):
            continue

        # 键值型：list 中每个元素是 {"key": ..., "value": [...]}
        if "list" in data and isinstance(data["list"], list):
            for item in data["list"]:
                if not isinstance(item, dict):
                    continue
                key = str(item.get("key", "")).strip()
                if key in SKIP_DATA_KEYS:
                    continue
                val = clean_value(item.get("value", ""))
                if key and val:
                    blocks.append((key, val))

        # 富文本/说明
        for rk in ("rich_text", "text", "desc", "description"):
            if rk in data and isinstance(data[rk], str):
                t = plain(data[rk])
                if t:
                    blocks.append((None, t))

        # 对话：contents 是 id -> {option,dialogue}
        if "contents" in data and isinstance(data["contents"], dict):
            for node in data["contents"].values():
                if not isinstance(node, dict):
                    continue
                for dk in ("option", "dialogue"):
                    if dk in node:
                        t = plain(node[dk])
                        if t:
                            blocks.append((None, t))

        # 其他嵌套 list/dict 再递归兜底
        for key in ("content", "contents", "list"):
            if key in data and isinstance(data[key], (dict, list)):
                # list 已处理；contents 已处理；content 可能是纯文本结构
                if key == "content":
                    blocks += parse_module_blocks({"components": [{"data": data[key]}]})
    return blocks


def module_text(module):
    blocks = parse_module_blocks(module)
    lines = []
    for key, text in blocks:
        if key:
            lines.append(f"{key}: {text}")
        else:
            lines.append(text)
    return "\n".join(lines)


def group_modules(modules):
    """按非标准模块名切分任务小节，返回 list[list[module]]。"""
    groups = []
    cur = []
    seen_non_standard = False
    for m in modules:
        if not isinstance(m, dict):
            continue
        name = (m.get("name") or "").strip()
        is_title = bool(name) and name not in STANDARD_MODULES
        if is_title:
            if cur:
                groups.append(cur)
            cur = [m]
            seen_non_standard = True
        elif cur:
            cur.append(m)
    if cur:
        groups.append(cur)
    if not groups:
        # 兜底：整页作为一组
        groups = [modules]
    return groups


def group_modules_from_page(page):
    """优先按 template_layout.module_group 取真实任务分组，避免 modules 平铺顺序误导。"""
    groups = []
    tl = page.get("template_layout") or {}
    tabs = tl.get("tab") or []
    mods_by_id = {}
    for m in page.get("modules", []) or []:
        if isinstance(m, dict) and m.get("id") is not None:
            mods_by_id[str(m["id"])] = m
    if tabs:
        for tab in tabs:
            for group in tab.get("module_group") or []:
                ids = []
                for item in group.get("module") or []:
                    if isinstance(item, dict) and item.get("id") is not None:
                        ids.append(str(item["id"]))
                mods = [mods_by_id[i] for i in ids if i in mods_by_id]
                if mods:
                    groups.append(mods)
    if groups:
        return groups
    return group_modules(page.get("modules", []) or [])


def group_title(group):
    for m in group:
        if isinstance(m, dict):
            n = (m.get("name") or "").strip()
            if n and n not in STANDARD_MODULES:
                return n
    return ""


def group_text(group):
    lines = []
    for m in group:
        if not isinstance(m, dict):
            continue
        name = (m.get("name") or "").strip()
        if not name:
            continue
        txt = module_text(m)
        if not txt:
            continue
        lines.append(f"【{name}】")
        lines.append(txt)
    return "\n".join(lines)


def parse_filters(item):
    text = (item.get("filters", {}).get("filters_text", "") or "")
    if not text:
        return {}
    try:
        arr = json.loads(text)
    except Exception:
        return {}
    out = {}
    for entry in arr:
        if "/" in entry:
            k, v = entry.split("/", 1)
            out[k] = v
    return out


def extract_metadata(group, filters, series):
    meta = {
        "任务名称": group_title(group),
        "所属版本": filters.get("版本号", "7.0"),
        "任务地区": filters.get("任务区域", ""),
        "任务类型": filters.get("任务类型", ""),
    }
    if series:
        meta["系列任务"] = series

    chars = []
    conditions = []
    for m in group:
        for key, value in parse_module_blocks(m):
            if key == "前置任务" and value:
                meta["前置任务"] = value
            elif key == "后续任务" and value:
                meta["后续任务"] = value
            elif key == "相关活动" and value:
                meta["相关活动"] = value
            elif key in ("起始npc", "结束npc") and value:
                for c in re.split(r"[、，,\s]+", value):
                    c = c.strip()
                    if c and c not in chars and c not in VALUE_EMPTY:
                        chars.append(c)
            elif key == "触发条件" and value:
                conditions.append(f"触发条件：{value}")
            elif key == "等级限制" and value:
                conditions.append(f"等级限制：{value}")
            elif key == "特殊限制" and value:
                conditions.append(f"特殊限制：{value}")
            elif key == "任务描述" and value:
                meta.setdefault("任务描述", value)
    if chars:
        meta["出场人物"] = "、".join(chars)
    if conditions:
        meta.setdefault("任务条件", "\n".join(conditions))

    # 过程/奖励
    for m in group:
        if isinstance(m, dict) and (m.get("name") or "") == "任务过程":
            txt = module_text(m)
            if txt:
                meta["任务流程"] = txt
                first_line = txt.split("\n", 1)[0].strip()
                if first_line and len(first_line) > 5:
                    meta.setdefault("任务描述", first_line)
        if isinstance(m, dict) and (m.get("name") or "") == "任务奖励":
            txt = module_text(m)
            if txt:
                meta["奖励"] = txt
    return {k: v for k, v in meta.items() if v != ""}


def load_existing_titles():
    titles = set()
    for fn in glob.glob(os.path.join(CONTENT_DIR, "quests_*.json")):
        if os.path.basename(fn) == "quests_processed.json":
            continue
        try:
            data = load_json(fn)
        except Exception:
            continue
        if not isinstance(data, list):
            continue
        for it in data:
            if not isinstance(it, dict):
                continue
            t = it.get("title") or (it.get("metadata") or {}).get("任务名称", "")
            if t:
                titles.add(t)
    return titles


_TITLE_PAREN_RE = re.compile(r"[（(].*?[)）]")
_TITLE_SUFFIX_RE = re.compile(
    r"[·•‧]\s*(其[一二三四五六七八九十]+|第[一二三四五六七八九十]+[幕回日]|上|中|下|序|初篇|终篇|篇)\s*$"
)
_TITLE_NUM_PAREN_RE = re.compile(r"[（(][一二三四五六七八九十\d]+[)）]")
_TITLE_PUNCT_RE = re.compile(r'[·•‧、，。！？?!：:；;／/“”‘’「」『』【】《》<>\[\]{}()（）\s\-—.…⋯]+')


def normalize_title(s):
    """用于跨来源任务名去重的规范化：去括号、去常见序号后缀、去标点空白。"""
    if not isinstance(s, str):
        return ""
    s = _TITLE_PAREN_RE.sub("", s)
    s = _TITLE_SUFFIX_RE.sub("", s)
    s = _TITLE_NUM_PAREN_RE.sub("", s)
    s = _TITLE_PUNCT_RE.sub("", s)
    return s.lower()


def load_existing_normalized_titles():
    norms = []
    for fn in glob.glob(os.path.join(CONTENT_DIR, "quests_*.json")):
        if os.path.basename(fn) == "quests_processed.json":
            continue
        try:
            data = load_json(fn)
        except Exception:
            continue
        if not isinstance(data, list):
            continue
        for it in data:
            if not isinstance(it, dict):
                continue
            t = it.get("title") or (it.get("metadata") or {}).get("任务名称", "")
            if t:
                n = normalize_title(t)
                if n:
                    norms.append(n)
    return norms


def is_duplicate_title(title, existing_norms):
    """判断该任务名是否与本地已有任务名重复（精确、包含、高相似）。"""
    n = normalize_title(title)
    if not n:
        return False
    for en in existing_norms:
        if en == n:
            return True
        # 新名是已有名的子串：常见于「奇石历险记」对应本地「奇石历险记·其一」等。
        if len(n) >= 4 and n in en:
            return True
        # 已有名是新名的子串：常见于本地「幽潭心」对应米游社「游水酝诗籍 第三首 幽潭心」。
        if len(en) >= 4 and en in n:
            return True
        if difflib.SequenceMatcher(None, n, en).ratio() >= 0.85:
            return True
    return False


def build_world_entry(group, filters, series, pageid=None):
    title = group_title(group)
    meta = extract_metadata(group, filters, series)
    meta["任务名称"] = title
    return {
        "title": title,
        "text": group_text(group),
        "metadata": meta,
        "category": "世界任务",
    }


def build_activity_entry(group, filters, series, pageid):
    title = group_title(group)
    meta = extract_metadata(group, filters, series)
    meta["任务名称"] = title
    # 活动任务沿用本地活动文件的任务类型写法
    meta.setdefault("任务类型", "活动事件")
    return {
        "source": "米游社观测枢",
        "category": "活动剧情",
        "title": title,
        "pageid": pageid,
        "metadata": meta,
        "text": group_text(group),
    }


def main():
    parser = argparse.ArgumentParser(description="解析米游社任务")
    parser.add_argument("--dry-run", action="store_true", help="只打印计划不写入")
    parser.add_argument("--raw", default=RAW_PATH, help="米游社原始详情 JSON 路径")
    args = parser.parse_args()

    if not os.path.exists(args.raw):
        log(f"未找到原始数据: {args.raw}")
        return

    raw = load_json(args.raw)
    items = raw.get("items", [])
    log(f"原始条目: {len(items)}")

    existing = load_existing_titles()
    existing_norms = load_existing_normalized_titles()
    log(f"本地已有任务名: {len(existing)}")

    world_entries = []
    activity_entries = []
    skipped_existing = []
    skipped_main = []
    logs = []

    planned = []

    for idx, item in enumerate(items):
        page = item.get("page")
        if not isinstance(page, dict):
            logs.append(f"[{idx}] 无页面：{item.get('title','')}")
            continue
        filters = parse_filters(item)
        task_type = filters.get("任务类型", "")
        series = item.get("title", "").strip()
        groups = group_modules_from_page(page)
        module_names = [group_title(g) for g in groups if group_title(g)]
        is_container = len(set(module_names)) > 1 or (series.startswith("至冬 ") and len(module_names) >= 1 and module_names[0] != series)
        if len(module_names) == 1 and module_names[0] == series:
            is_container = False

        for gi, group in enumerate(groups):
            title = group_title(group) or series
            if not title:
                continue
            if task_type == "魔神任务":
                if title in existing:
                    skipped_main.append(title)
                else:
                    logs.append(f"[{idx}] 魔神任务缺失? {title}")
                continue
            if title in existing:
                skipped_existing.append(title)
                continue
            if is_duplicate_title(title, existing_norms):
                skipped_existing.append(title)
                continue
            if is_container:
                s = series
            else:
                s = ""
            try:
                type_arr = json.loads(item.get("filters", {}).get("filters_text", "") or "[]")
                type_set = {x.split("/", 1)[1] for x in type_arr if x.startswith("任务类型/")}
            except Exception:
                type_set = set()
            if "魔神任务" in type_set:
                if title in existing:
                    skipped_main.append(title)
                else:
                    logs.append(f"[{idx}] 魔神任务缺失? {title}")
            elif "限时任务" in type_set or "传说任务" in type_set:
                planned.append(("activity", title, group, filters, s, item.get("content_id")))
            elif "世界任务" in type_set:
                planned.append(("world", title, group, filters, s, item.get("content_id")))
            else:
                logs.append(f"[{idx}] 未知任务类型 {task_type}: {title}")

    merged = {}
    for kind, title, group, filters, series, pageid in planned:
        key = (kind, title)
        if key not in merged:
            merged[key] = {
                "kind": kind,
                "title": title,
                "groups": [],
                "filters": filters,
                "series": series,
                "pageids": [],
            }
        merged[key]["groups"].append(group)
        merged[key]["pageids"].append(pageid)

    for key, m in merged.items():
        kind, title = key
        groups = m["groups"]
        if len(groups) == 1:
            group = groups[0]
            text = group_text(group)
            meta = extract_metadata(group, m["filters"], m["series"])
            meta["任务名称"] = title
        else:
            text_parts = []
            for gi, group in enumerate(groups, 1):
                text_parts.append(f"【阶段{gi}】\n{group_text(group)}")
            text = "\n\n".join(text_parts)
            meta = extract_metadata(groups[0], m["filters"], m["series"])
            meta["任务名称"] = title
            for group in groups:
                t = extract_metadata(group, m["filters"], m["series"])
                if t.get("前置任务"):
                    meta.setdefault("前置任务", t["前置任务"])
                if t.get("后续任务"):
                    meta.setdefault("后续任务", t["后续任务"])
                if t.get("任务流程") and t["任务流程"] not in (meta.get("任务流程", ""),):
                    meta["任务流程"] = (meta.get("任务流程", "") + "\n" + t["任务流程"]).strip()
                if t.get("任务描述") and t["任务描述"] not in (meta.get("任务描述", ""),):
                    meta["任务描述"] = (meta.get("任务描述", "") + "\n" + t["任务描述"]).strip()

        if kind == "world":
            world_entries.append({
                "title": title,
                "text": text,
                "metadata": meta,
                "category": "世界任务",
            })
        else:
            activity_entries.append({
                "source": "米游社观测枢",
                "category": "活动剧情",
                "title": title,
                "pageid": m["pageids"][0],
                "metadata": meta,
                "text": text,
            })

    log(f"待新增世界任务: {len(world_entries)}，活动任务: {len(activity_entries)}")
    log(f"跳过已存在: {len(skipped_existing)}，跳过主线已有: {len(skipped_main)}")
    for e in world_entries:
        log(f"  + 世界 {e['title']} (text_len={len(e['text'])})")
    for e in activity_entries:
        log(f"  + 活动 {e['title']} (text_len={len(e['text'])})")

    if args.dry_run:
        log("dry-run，不写文件")
        return

    if world_entries:
        world_path = os.path.join(CONTENT_DIR, "quests_世界任务.json")
        bk = world_path + f".bak-before-mihoyo-parse-{time.strftime('%Y%m%d')}"
        if not os.path.exists(bk):
            import shutil
            shutil.copy2(world_path, bk)
            log(f"备份: {bk}")
        data = load_json(world_path)
        data.extend(world_entries)
        save_json(world_path, data)
        log(f"已写入 {len(world_entries)} 条到 {os.path.basename(world_path)}，总数 {len(data)}")

    if activity_entries:
        act_path = os.path.join(CONTENT_DIR, "quests_活动剧情.json")
        bk = act_path + f".bak-before-mihoyo-parse-{time.strftime('%Y%m%d')}"
        if not os.path.exists(bk):
            import shutil
            shutil.copy2(act_path, bk)
            log(f"备份: {bk}")
        data = load_json(act_path)
        data.extend(activity_entries)
        save_json(act_path, data)
        log(f"已写入 {len(activity_entries)} 条到 {os.path.basename(act_path)}，总数 {len(data)}")


if __name__ == "__main__":
    main()

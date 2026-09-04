# -*- coding: utf-8 -*-
"""
米游社任务列表 vs 本地 quests 对齐脚本。

只做列表级分类，不抓详情、不改数据。
输出：
  - 本地已精确命中
  - 疑似容器页/章节幕卡（通常不需要新增独立任务）
  - 疑似真实缺失（需要后续抓详情确认）
  - 按任务类型/版本/地区统计
"""
import json
import os
import re
from collections import Counter

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONTENT = os.path.join(BASE, "content_data")
TASK_LIST = os.path.join(CONTENT, "mihoyo_tasks_list.json")
OUTPUT = os.path.join(BASE, "_mihoyo_task_alignment.json")

REGION_PREFIXES = [
    "蒙德", "璃月", "稻妻", "须弥", "枫丹", "纳塔", "挪德卡莱",
    "霜月", "至冬", "层岩巨渊", "渊下宫",
]


def norm(s):
    s = re.sub(r"[「」『〗“‘”\s【】()（）《》]", "", s or "")
    return s


def load_local_quests():
    titles = set()
    archon_acts = set()
    legend_acts = set()
    for fn in sorted(os.listdir(CONTENT)):
        if not fn.startswith("quests_") or not fn.endswith(".json") or fn == "quests_processed.json":
            continue
        data = json.load(open(os.path.join(CONTENT, fn), encoding="utf-8"))
        for e in data:
            t = str(e.get("title", "")).strip()
            if t:
                titles.add(t)
            md = e.get("metadata", {}) or {}
            if fn == "quests_魔神任务.json":
                for k in ("act_name", "act_no", "chapter_name", "chapter_no", "系列任务", "任务名称"):
                    v = md.get(k, "")
                    if v:
                        archon_acts.add(str(v))
            if fn == "quests_传说任务.json":
                for k in ("act_name", "chapter_name", "系列任务", "任务名称", "所属角色"):
                    v = md.get(k, "")
                    if v:
                        legend_acts.add(str(v))
    return {
        "titles": titles,
        "normalized_titles": {norm(t) for t in titles},
        "archon_acts": {norm(x) for x in archon_acts},
        "legend_acts": {norm(x) for x in legend_acts},
        "all_meta_names": {norm(x) for x in (archon_acts | legend_acts)},
    }


def parse_filters(it):
    try:
        return json.loads(it.get("filters", {}).get("filters_text", "") or "[]")
    except Exception:
        return []


def get(prefix, arr):
    for x in arr:
        if x.startswith(prefix):
            return x[len(prefix):]
    return ""


def extract_quoted_names(title):
    names = []
    for m in re.finditer(r"[「《]([^」》]+)[」》]", title):
        names.append(norm(m.group(1)))
    return names


def is_container_or_act_card(title, ftype, local):
    if not title:
        return False
    # 区域开头的容器卡：如“至冬 一边是宫殿一边是陵阙”
    for region in REGION_PREFIXES:
        if title.startswith(region + " "):
            return True
    # 明显的容器词
    if title.endswith("（任务）"):
        return True
    if "系列任务" in title or "合集" in title or re.search(r"轶事之[一二三]", title):
        return True
    if re.match(r"^[至蒙璃稻须枫纳挪霜层渊][^ ]* .+", title):
        return True
    # 章节/幕卡、活动回目：只要引号里的具体幕/回名在本地已有，就算已覆盖
    quoted = extract_quoted_names(title)
    if any(q in local["all_meta_names"] or q in local["normalized_titles"] for q in quoted):
        return True
    if re.search(r"第[一二三四五六七八九十百0-9]+[章节幕回]", title):
        return True
    # 活动系列容器：如“幻友绮旅”“春曦画桃符”等，若有“其一/其二”或“第X回”说明是子任务卡
    if re.search(r"其[一二三四五]|第[一二三四五六七八九十百0-9]+[回幕章]", title):
        # 取系列主名（引号前主体）判断本地是否有该系列
        base = re.split(r"[ 　]*[其第]", title)[0].strip()
        base_norm = norm(base)
        if base_norm and (base_norm in local["all_meta_names"] or base_norm in local["normalized_titles"]):
            return True
    return False


def main():
    data = json.load(open(TASK_LIST, encoding="utf-8"))
    items = data.get("items", [])
    local = load_local_quests()

    matches = []
    containers = []
    missing = []
    for it in items:
        title = str(it.get("title", "")).strip()
        arr = parse_filters(it)
        ftype = get("任务类型/", arr)
        fver = get("版本号/", arr)
        freg = get("任务区域/", arr)
        rec = {
            "content_id": it.get("content_id"),
            "title": title,
            "type": ftype,
            "version": fver,
            "region": freg,
        }
        if norm(title) in local["normalized_titles"]:
            rec["via"] = "exact_title"
            matches.append(rec)
        elif is_container_or_act_card(title, ftype, local):
            # 再看是否是幕名已覆盖
            m = re.search(r"「([^」]+)」", title)
            rec["via"] = "container_or_act"
            rec["matched_meta_name"] = m.group(1) if m and norm(m.group(1)) in local["all_meta_names"] else ""
            containers.append(rec)
        else:
            missing.append(rec)

    def summarize(records):
        c = Counter()
        for r in records:
            c[(r["type"], r["version"], r["region"])] += 1
        return c

    result = {
        "total": len(items),
        "exact_match_count": len(matches),
        "container_or_act_count": len(containers),
        "missing_candidate_count": len(missing),
        "exact_by_type_version_region": [{"k": list(k), "n": n} for k, n in summarize(matches).most_common()],
        "container_by_type_version_region": [{"k": list(k), "n": n} for k, n in summarize(containers).most_common()],
        "missing_by_type_version_region": [{"k": list(k), "n": n} for k, n in summarize(missing).most_common()],
        "missing_items": missing,
    }
    with open(OUTPUT, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print("total", len(items))
    print("exact", len(matches))
    print("container_or_act", len(containers))
    print("missing_candidate", len(missing))
    print("output", OUTPUT)


if __name__ == "__main__":
    main()

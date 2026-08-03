#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
从 materials.json 重建 collectibles.json，补全类型/分布地区等元数据
"""
import json
import re
import os

BASE = os.path.dirname(os.path.abspath(__file__))

# 读取 materials.json
with open(os.path.join(BASE, "content_data", "materials.json"), "r", encoding="utf-8") as f:
    materials = json.load(f)

# 读取旧的 collectibles.json 获取已有 ID 和名称对照
old_col = {}
old_path = os.path.join(BASE, "content_data", "collectibles.json")
if os.path.exists(old_path):
    with open(old_path, "r", encoding="utf-8") as f:
        for item in json.load(f):
            old_col[item.get("名称", "")] = item

def parse_wiki_type(desc):
    """从简介的 wiki 模板中提取关键字段"""
    result = {}
    patterns = {
        "类型": r"\|类型\s*=\s*(.+?)(?:\n|\|)",
        "稀有度": r"\|稀有度\s*=\s*(\d+)",
        "来源": r"\|来源\s*=\s*(.+?)(?:\n\||\n\})",
        "介绍": r"\|介绍\s*=\s*(.+?)(?:\n\||\n\})",
        "用处": r"\|用处\s*=\s*(.+?)(?:\n\||\n\})",
    }
    for key, pat in patterns.items():
        m = re.search(pat, desc)
        if m:
            val = m.group(1).strip()
            # 清理 wiki 标记
            val = re.sub(r"'''.+?'''", "", val)
            val = re.sub(r"\[\[.+?\]\]", "", val)
            val = re.sub(r"\{\{.+?\}\}", "", val)
            val = val.strip()
            if val:
                result[key] = val
    return result

new_collectibles = []
seen_names = set()

for m in materials:
    name = m.get("名称", "")
    if not name or name in seen_names:
        continue

    desc = m.get("简介", "")
    if not desc:
        continue

    wiki = parse_wiki_type(desc)
    col_type = wiki.get("类型", "")

    # 只保留"区域特产"类型的物品
    if "区域特产" not in col_type:
        continue

    seen_names.add(name)

    # 优先用旧 ID，没有则新生成
    old = old_col.get(name, {})
    col_id = old.get("采集物ID", f"COL_{name}")

    # 提取分布地区
    source = wiki.get("来源", "")

    new_collectibles.append({
        "采集物ID": col_id,
        "名称": name,
        "类型": col_type,
        "分布地区": source,
        "刷新时间": old.get("刷新时间", "48小时"),
        "用途": wiki.get("用处", ""),
        "简介": wiki.get("介绍", ""),
    })

# 写入 collectibles.json
out_path = os.path.join(BASE, "content_data", "collectibles.json")
with open(out_path, "w", encoding="utf-8") as f:
    json.dump(new_collectibles, f, ensure_ascii=False, indent=2)

print(f"[完成] 从 {len(materials)} 条材料中筛选出 {len(new_collectibles)} 条区域特产")
print(f"  输出: {out_path}")
# 按地区分组统计
from collections import Counter
region_counter = Counter()
for c in new_collectibles:
    t = c["类型"]
    region_counter[t] += 1
print(f"  分布: {dict(region_counter)}")

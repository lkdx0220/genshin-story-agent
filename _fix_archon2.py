# -*- coding: utf-8 -*-
"""修复 2 个魔神任务顺序 + 奔霄颂玉轮（用权威 txt 数据）"""
import sys
import os
import shutil
import re

BASE_DIR = r'C:\Users\24701\Desktop\原神剧情\CASE-原神剧情助手-修改用'
KB_DIR = os.path.join(BASE_DIR, 'genshin_knowledge_base')
QUEST_FILE = os.path.join(KB_DIR, 'quests.py')

sys.path.insert(0, KB_DIR)
from quests import 任务知识库

def normalize_name(name):
    return re.sub(r'[（(][^）)]*[）)]', '', name).strip()

# 需要修复的系列及正确顺序
FIX_ORDERS = {
    # === 魔神任务 (2) ===
    "第一章,我们终将重逢": ["非自愿的祭献", "无信者的使徒", "不荣誉的试炼", "有隔阂的魂灵"],
    "空月之歌,虚空劫灰往世书": ["幽暗时分", "妄念与真知的通天塔", "虚空劫灰往世书", "你的往事如一座花园"],
    # === 活动剧情 (1) ===
    "奔霄颂玉轮": ["在云间", "在岩间", "在人间", "白马闲游记"],
}

fixed = 0
for series_name, wiki_order in FIX_ORDERS.items():
    # 找到该系列的所有条目
    entries = [(i, q) for i, q in enumerate(任务知识库) if q.get('系列任务', '') == series_name]
    if not entries:
        print(f"[跳过] 未找到: {series_name}")
        continue
    
    current_names = [e[1].get('任务名称', '') for e in entries]
    wiki_norm = [normalize_name(n) for n in wiki_order]
    current_norm = [normalize_name(n) for n in current_names]
    
    if current_norm == wiki_norm:
        print(f"[已正确] {series_name}")
        continue
    
    if sorted(current_norm) != sorted(wiki_norm):
        print(f"[警告] {series_name}: 任务名集合不匹配")
        print(f"  Wiki: {sorted(wiki_norm)}")
        print(f"  DB:   {sorted(current_norm)}")
        continue
    
    # 建立名称到条目的映射
    name_map = {}
    for idx, entry in entries:
        nn = normalize_name(entry.get('任务名称', ''))
        name_map[nn] = (idx, entry)
    
    # 按正确顺序排列
    new_ordered = []
    for name in wiki_order:
        nn = normalize_name(name)
        if nn in name_map:
            new_ordered.append(name_map[nn])
        else:
            print(f"[警告] {series_name}: 找不到 '{name}'")
            break
    else:
        # 在原列表中替换
        indices = sorted([e[0] for e in entries])
        for pos, (_, entry) in zip(indices, new_ordered):
            任务知识库[pos] = entry
        
        print(f"[修复] {series_name}")
        print(f"  旧: {' -> '.join(current_names)}")
        print(f"  新: {' -> '.join(wiki_order)}")
        fixed += 1
        continue

print(f"\n===== 修复了 {fixed} 个 =====")

if fixed == 0:
    sys.exit(0)

# 写回
bak_file = QUEST_FILE + '.bak4'
if not os.path.exists(bak_file):
    shutil.copy2(QUEST_FILE, bak_file)
    print(f"[备份] {bak_file}")

lines = ['任务知识库 = [']
for i, entry in enumerate(任务知识库):
    if i > 0:
        lines.append('    {')
    else:
        lines.append('    {')

    field_order = ['任务名称', '任务类型', '简介', '关联角色', '系列任务', '所属角色', '是否伪传说任务']
    output_fields = []
    for field in field_order:
        if field in entry:
            output_fields.append((field, entry[field]))
    seen = set(field_order)
    for field, val in entry.items():
        if field not in seen:
            output_fields.append((field, val))
            seen.add(field)

    for j, (field, val) in enumerate(output_fields):
        is_last = (j == len(output_fields) - 1)
        comma = '' if is_last else ','
        if isinstance(val, str):
            escaped = val.replace('\\', '\\\\').replace('"', '\\"')
            lines.append(f'        "{field}": "{escaped}"{comma}')
        else:
            lines.append(f'        "{field}": {repr(val)}{comma}')

    if i < len(任务知识库) - 1:
        lines.append('    },')
    else:
        lines.append('    }')

lines.append(']')
content = '\n'.join(lines) + '\n'

with open(QUEST_FILE, 'w', encoding='utf-8') as f:
    f.write(content)

print(f"[完成] 已写入 {len(任务知识库)} 条")

# -*- coding: utf-8 -*-
"""修复传说任务 18 个子任务顺序"""
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
    """标准化：去除括号 + 全角标点 → 半角"""
    name = re.sub(r'[（(][^）)]*[）)]', '', name)
    # 去掉全角/半角标点用于匹配
    name = name.replace('，', ',').replace('、', ',').replace('·', ',')
    name = name.replace('「', '').replace('」', '').replace('！', '')
    return name.strip()

def simple_match(n1, n2):
    """宽松匹配"""
    return normalize_name(n1) == normalize_name(n2)

# 正确顺序（wiki TOC / 用户提供）
# 只修复之前跳过的（赤龙之章需要映射 幕间小憩 → 幕间小憩·第X日）
FIX_ORDERS = {
    "赤龙之章,名为故事的魔法": ["一位母亲的遗愿", "幕间小憩·第一日", "来自过去的预言", "幕间小憩·第二日", "命运的诡计", "幕间小憩·第三日", "星空降下的启示", "幕间小憩·第四日", "命运的题解"],
}

fixed = 0
skipped = []

for series_name, correct_order in FIX_ORDERS.items():
    entries = [(i, q) for i, q in enumerate(任务知识库) if q.get('系列任务', '') == series_name]
    if not entries:
        print(f"[跳过] 未找到: {series_name}")
        skipped.append(series_name)
        continue

    current_names = [e[1].get('任务名称', '') for e in entries]

    # 检查是否已正确
    match_ok = True
    if len(current_names) == len(correct_order):
        for c, w in zip(current_names, correct_order):
            if not simple_match(c, w):
                match_ok = False
                break
        if match_ok:
            print(f"[已正确] {series_name}")
            continue

    # 建立匹配
    # 对于重名（如幕间小憩），用位置队列
    name_to_indices = {}
    for idx, entry in entries:
        nn = normalize_name(entry.get('任务名称', ''))
        if nn not in name_to_indices:
            name_to_indices[nn] = []
        name_to_indices[nn].append((idx, entry))

    # 按 wiki 顺序排列
    used = {}
    new_ordered = []
    ok = True
    for wn in correct_order:
        wnn = normalize_name(wn)
        if wnn not in name_to_indices:
            # 尝试更宽松匹配
            found_key = None
            for db_key in name_to_indices:
                if simple_match(wn, db_key) or wnn in db_key or db_key in wnn:
                    found_key = db_key
                    break
            if found_key is None:
                # 打印详细对比信息
                print(f"\n[不匹配] {series_name}")
                print(f"  Wiki: {correct_order}")
                print(f"  DB:   {current_names}")
                print(f"  搜索 '{wn}' (norm: {wnn}) 未找到")
                print(f"  DB 中可用的: {list(name_to_indices.keys())}")
                ok = False
                break
            pool = name_to_indices[db_key]
        else:
            pool = name_to_indices[wnn]

        idx_count = used.get(wnn, 0)
        if idx_count >= len(pool):
            print(f"\n[重名耗尽] {series_name}: '{wn}' 出现 {idx_count+1} 次但只有 {len(pool)} 个")
            ok = False
            break
        new_ordered.append(pool[idx_count])
        used[wnn] = idx_count + 1

    if not ok:
        skipped.append(series_name)
        continue

    # 在原列表中替换
    indices = sorted([e[0] for e in entries])
    for pos, (_, entry) in zip(indices, new_ordered):
        任务知识库[pos] = entry

    print(f"[修复] {series_name}")
    print(f"  旧: {' -> '.join(current_names)}")
    print(f"  新: {' -> '.join(correct_order)}")
    fixed += 1

print(f"\n===== 传说任务修复: {fixed} 个, 跳过: {len(skipped)} 个 =====")
if skipped:
    print(f"跳过的: {skipped}")

# 写回
bak_file = QUEST_FILE + '.bak6'
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

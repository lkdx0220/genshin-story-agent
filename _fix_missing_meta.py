# -*- coding: utf-8 -*-
"""修复魔神任务元数据缺失 + 条目归位 + 排序（用权威 txt 数据）"""
import sys
import os
import re

BASE_DIR = r'C:\Users\24701\Desktop\原神剧情\CASE-原神剧情助手-修改用'
KB_DIR = os.path.join(BASE_DIR, 'genshin_knowledge_base')
QUEST_FILE = os.path.join(KB_DIR, 'quests.py')

sys.path.insert(0, KB_DIR)
from quests import 任务知识库

def normalize_name(name):
    return re.sub(r'[（(][^）)]*[）)]', '', name).strip()

print("===== 步骤1: 补充缺失的系列任务字段 =====")

# 6个孤立任务应归属的系列
ORPHAN_FIXES = {
    "遥不可及的安息": "空月之歌,尘与灯的挽歌",
    "剑拔弩张四人众": "第三章,迷梦与空幻与欺骗",
    "名为「命运」的燃料": "第五章,命定将焚的虹光",
    "席卷而来的暗潮": "第五章,命定将焚的虹光",
    "共睹那日之将落": "第五章,命定将焚的虹光",
    "绝望高悬天之上": "第五章,命定将焚的虹光",
}

for q in 任务知识库:
    name = q.get('任务名称', '')
    if name in ORPHAN_FIXES:
        q['系列任务'] = ORPHAN_FIXES[name]
        print(f"  [补字段] {name} → {ORPHAN_FIXES[name]}")

print("\n===== 步骤2: 修正缺少 '第四章,' 前缀的系列 =====")

# 这两个系列的所有条目，将系列名改为带"第四章,"前缀
PREFIX_FIXES = {
    "白露与黑潮的序诗": "第四章,白露与黑潮的序诗",
    "罪人舞步旋": "第四章,罪人舞步旋",
}

for q in 任务知识库:
    s = q.get('系列任务', '')
    if s in PREFIX_FIXES:
        old = s
        q['系列任务'] = PREFIX_FIXES[s]
        print(f"  [修正前缀] {q.get('任务名称','')}: '{old}' → '{PREFIX_FIXES[s]}'")

print("\n===== 步骤3: 检查并修复所有受影响系列的子任务顺序 =====")

# 受影响系列的正确顺序（从权威 txt）
REORDER_MAP = {
    # 散落任务归位 + 排序
    "序章,为了没有眼泪的明天": [
        "阴影下的蒙德", "不期而遇", "那个绿色的家伙", "听凭风引",
        "温迪的计划", "温迪的新计划", "逃亡", "幕后谈话", "追逐暗影",
        "至宝的现状", "遗落之泪", "藏匿之泪", "被夺之泪", "澄净之泪", "与巨龙重逢"
    ],
    "第三章,穿越烟帷与暗林": [
        "林中遇变", "疗养观察", "痼疾", "缄默的求知者", "智慧之神的踪影",
        "失物匿于繁华", "近在咫尺的目标"
    ],
    "第四章,白露与黑潮的序诗": [
        "独舞者的序幕", "细雨眷恋之城", "聚光灯下谎言成影"
    ],
    "第四章,罪人舞步旋": [
        "怒涛之灾", "相见亦是离别", "狩猎者，预见者", "审判日",
        "黑潮与白露的歌剧", "终幕礼"
    ],
    "空月之歌,尘与灯的挽歌": [
        "轰鸣与暗涌", "窥见记忆的暗面", "曾有人追猎月亮", "灰白的秩序熊熊燃烧", "遥不可及的安息"
    ],
    "第三章,迷梦与空幻与欺骗": [
        "如凯旋的英雄一般", "来自某位「神明」的凝视", "剑拔弩张四人众"
    ],
    "第五章,命定将焚的虹光": [
        "秘源之下", "共睹那日之将落", "席卷而来的暗潮",
        "绝望高悬天之上", "我们不会孤军奋战", "名为「命运」的燃料"
    ],
}

fixed = 0
for series_name, correct_order in REORDER_MAP.items():
    # 找到该系列所有条目
    entries = [(i, q) for i, q in enumerate(任务知识库) if q.get('系列任务', '') == series_name]
    if not entries:
        print(f"  [警告] 未找到系列: {series_name}")
        continue

    current_names = [e[1].get('任务名称', '') for e in entries]
    correct_norm = [normalize_name(n) for n in correct_order]
    current_norm = [normalize_name(n) for n in current_names]

    if sorted(current_norm) != sorted(correct_norm):
        print(f"  [警告] {series_name}: 集合不匹配")
        print(f"    应有: {sorted(correct_norm)}")
        print(f"    现有: {sorted(current_norm)}")
        continue

    if current_norm == correct_norm:
        continue  # already correct order

    # 名称到索引的映射（注意：可能有重名任务）
    # 为简单起见，建立位置映射
    # 每个 current 条目找到其正确位置
    name_to_indices = {}
    for idx, entry in entries:
        nn = normalize_name(entry.get('任务名称', ''))
        if nn not in name_to_indices:
            name_to_indices[nn] = []
        name_to_indices[nn].append((idx, entry))

    # 处理重名
    used_counts = {}
    new_ordered = []
    ok = True
    for cn in correct_order:
        cnn = normalize_name(cn)
        if cnn not in name_to_indices:
            print(f"  [警告] {series_name}: 找不到 '{cn}'")
            ok = False
            break
        pool = name_to_indices[cnn]
        idx_count = used_counts.get(cnn, 0)
        if idx_count >= len(pool):
            print(f"  [警告] {series_name}: '{cn}' 重名耗尽")
            ok = False
            break
        new_ordered.append(pool[idx_count])
        used_counts[cnn] = idx_count + 1

    if not ok:
        continue

    # 在列表中替换
    indices = sorted([e[0] for e in entries])
    for pos, (_, entry) in zip(indices, new_ordered):
        任务知识库[pos] = entry

    print(f"  [排序] {series_name}")
    print(f"    旧: {' -> '.join(current_names)}")
    print(f"    新: {' -> '.join(correct_order)}")
    fixed += 1

print(f"\n===== 排序完成: {fixed} 个 =====")

# 写回
import shutil
bak_file = QUEST_FILE + '.bak5'
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

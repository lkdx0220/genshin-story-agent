# -*- coding: utf-8 -*-
"""提取世界任务多子任务系列"""
import sys
sys.path.insert(0, r'C:\Users\24701\Desktop\原神剧情\CASE-原神剧情助手-修改用\genshin_knowledge_base')
from quests import 任务知识库
from collections import OrderedDict

series = OrderedDict()
no_series = []
for q in 任务知识库:
    if q.get('任务类型') != '世界任务':
        continue
    s = q.get('系列任务', '')
    if s:
        if s not in series:
            series[s] = []
        series[s].append(q.get('任务名称', ''))
    else:
        no_series.append(q.get('任务名称', ''))

multi = {k: v for k, v in series.items() if len(v) > 1}
print(f"世界任务: 总条目 {sum(len(v) for v in series.values()) + len(no_series)}")
print(f"系列数: {len(series)}, 无系列: {len(no_series)}")
print(f"多子任务系列: {len(multi)}")

# 只显示前20个作为样本
count = 0
for s, tasks in multi.items():
    count += 1
    print(f"\n  {s} ({len(tasks)})")
    for t in tasks:
        print(f"    - {t}")
    if count >= 20:
        print(f"\n  ... 还有 {len(multi)-20} 个多子任务系列")
        break

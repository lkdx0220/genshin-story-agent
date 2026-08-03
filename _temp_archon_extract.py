# -*- coding: utf-8 -*-
"""提取所有魔神任务系列及其子任务列表"""
import sys
sys.path.insert(0, r'C:\Users\24701\Desktop\原神剧情\CASE-原神剧情助手-修改用\genshin_knowledge_base')
from quests import 任务知识库
from collections import OrderedDict

series = OrderedDict()
no_series = []
for q in 任务知识库:
    if q.get('任务类型') != '魔神任务':
        continue
    s = q.get('系列任务', '')
    if s:
        if s not in series:
            series[s] = []
        series[s].append(q.get('任务名称', ''))
    else:
        no_series.append(q.get('任务名称', ''))

print(f"===== 魔神任务统计 =====")
print(f"总条目数: {sum(len(v) for v in series.values()) + len(no_series)}")
print(f"系列数: {len(series)}")
print(f"无系列任务数: {len(no_series)}")

print(f"\n===== 多子任务系列 (有顺序问题的可能) =====")
multi_count = 0
for s, tasks in series.items():
    if len(tasks) > 1:
        multi_count += 1
        print(f"\n  {s} ({len(tasks)} 个子任务):")
        for i, t in enumerate(tasks):
            print(f"    {i+1}. {t}")

print(f"\n多子任务系列数: {multi_count}")

print(f"\n===== 单子任务系列 =====")
for s, tasks in series.items():
    if len(tasks) == 1:
        print(f"  {s}: {tasks[0]}")

if no_series:
    print(f"\n===== 无系列任务 ({len(no_series)}) =====")
    for t in no_series:
        print(f"  {t}")

# -*- coding: utf-8 -*-
"""提取活动剧情和部族纪闻系列"""
import sys
sys.path.insert(0, r'C:\Users\24701\Desktop\原神剧情\CASE-原神剧情助手-修改用\genshin_knowledge_base')
from quests import 任务知识库
from collections import OrderedDict

for task_type in ['活动剧情', '部族纪闻']:
    series = OrderedDict()
    no_series = []
    for q in 任务知识库:
        if q.get('任务类型') != task_type:
            continue
        s = q.get('系列任务', '')
        if s:
            if s not in series:
                series[s] = []
            series[s].append(q.get('任务名称', ''))
        else:
            no_series.append(q.get('任务名称', ''))

    print(f"===== {task_type} =====")
    print(f"总条目: {sum(len(v) for v in series.values()) + len(no_series)}, 系列: {len(series)}")

    multi = {k: v for k, v in series.items() if len(v) > 1}
    print(f"多子任务系列: {len(multi)}")
    for s, tasks in multi.items():
        print(f"  {s} ({len(tasks)})")
        for t in tasks:
            print(f"    - {t}")

    if no_series:
        print(f"无系列 ({len(no_series)}):")
        for t in no_series:
            print(f"  - {t}")
    print()

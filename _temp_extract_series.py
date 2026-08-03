# -*- coding: utf-8 -*-
import sys
sys.path.insert(0, r'C:\Users\24701\Desktop\原神剧情\CASE-原神剧情助手-修改用\genshin_knowledge_base')
from quests import 任务知识库
from collections import OrderedDict

series = OrderedDict()
for q in 任务知识库:
    if q.get('任务类型') != '传说任务':
        continue
    if not q.get('所属角色', ''):
        continue
    s = q.get('系列任务', '')
    if not s:
        continue
    if s not in series:
        series[s] = {'owner': q.get('所属角色', ''), 'tasks': []}
    series[s]['tasks'].append(q.get('任务名称', ''))

for s, info in series.items():
    if len(info['tasks']) > 1:
        parts = s.split(',')
        chapter = parts[0].strip()
        act = parts[1].strip() if len(parts) > 1 else ''
        tasks_str = ' -> '.join(info['tasks'])
        print(f"{chapter} [{info['owner']}] ({len(info['tasks'])} tasks): {tasks_str}")

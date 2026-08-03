# -*- coding: utf-8 -*-
"""检查剩余传说任务系列的正确顺序，输出对照表"""
import sys
sys.path.insert(0, r'C:\Users\24701\Desktop\原神剧情\CASE-原神剧情助手-修改用\genshin_knowledge_base')
from quests import 任务知识库
from collections import OrderedDict
import json

# 已检查完成的系列名
checked = {
    "丝切铗之章,当他们谈起今夜", "香氛瓶之章,花债血偿", "引蝶之章,奈何蝶飞去",
    "貘枕之章,食梦者的忧郁", "白垩之章,旅行者观察报告", "赤龙之章,名为故事的魔法",
    "磷星之章,星与夜的低语", "夜枭之章,暗夜英雄的不在场证明",
    "四叶草之章,真正的宝物", "迅捷剑之章,夜色无声",
    "孔雀羽之章,海盗秘宝", "天隼之章,乌合的虚像",
    "野蔷薇之章,共渡潮落", "仙狐之章,鸣神御祓祈愿祭",
    "小狼之章,卢皮卡的意义", "古闻之章,盐花", "古闻之章,匪石",
    "黑斑猫之章,被遗忘的怪盗", "海精之章,谎言的温度",
    "金狼之章,沉沙归寂", "金狼之章,守诺者",
    "净炼火之章,炉火熄灭之际", "琉金之章,如梦如电的隽永", "琉金之章,拾星之旅",
}

series = OrderedDict()
for q in 任务知识库:
    if q.get('任务类型') != '传说任务':
        continue
    if not q.get('所属角色', ''):
        continue
    s = q.get('系列任务', '')
    if not s:
        continue
    if s in checked:
        continue
    if s not in series:
        series[s] = []
    series[s].append(q.get('任务名称', ''))

print("===== 剩余需要检查的传说任务系列 =====")
for s, tasks in series.items():
    if len(tasks) > 1:
        parts = s.split(',')
        chapter = parts[0].strip()
        act = parts[1].strip() if len(parts) > 1 else ''
        print(f"\n章节: {chapter} | 幕: {act}")
        print(f"DB顺序: {' -> '.join(tasks)}")

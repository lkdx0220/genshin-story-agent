# -*- coding: utf-8 -*-
"""提取问题题目的执行报告差异"""
import json

with open(r'c:\Users\24701\Desktop\原神剧情\CASE-原神剧情助手-修改用\e2e_stable.json', 'r', encoding='utf-8') as f:
    stable = json.load(f)
with open(r'c:\Users\24701\Desktop\原神剧情\CASE-原神剧情助手-修改用\e2e_modified.json', 'r', encoding='utf-8') as f:
    modified = json.load(f)

problem_ids = [3, 7, 10, 15, 19]

for qid in problem_ids:
    s = stable['results'][qid-1]
    m = modified['results'][qid-1]
    
    print(f"{'='*70}")
    print(f"#{qid} [{s['category']}]")
    print(f"问题: {s['query']}")
    print(f"稳定版标签: {s['intent_labels']} | 工具: {s['tool_calls']} | 迭代: {s.get('iteration')}")
    print(f"修改版标签: {m['intent_labels']} | 工具: {m['tool_calls']} | 迭代: {m.get('iteration')}")
    
    s_plan = s.get('execution_plan', '')
    m_plan = m.get('execution_plan', '')
    
    # 提取【执行报告】核心部分
    if s_plan:
        print(f"\n--- 稳定版【执行报告】(前600字) ---")
        print(s_plan[:600])
    if m_plan:
        print(f"\n--- 修改版【执行报告】(前600字) ---")
        print(m_plan[:600])
    
    print()

# -*- coding: utf-8 -*-
"""分析端到端对比测试结果"""
import json

with open(r'c:\Users\24701\Desktop\原神剧情\CASE-原神剧情助手-修改用\e2e_stable.json', 'r', encoding='utf-8') as f:
    stable = json.load(f)
with open(r'c:\Users\24701\Desktop\原神剧情\CASE-原神剧情助手-修改用\e2e_modified.json', 'r', encoding='utf-8') as f:
    modified = json.load(f)

print('=== 逐题质量分析 ===')
print()

stable_issues = 0
modified_issues = 0

for s, m in zip(stable['results'], modified['results']):
    qid = s['id']
    cat = s['category']
    
    s_tools = s.get('tool_calls', [])
    m_tools = m.get('tool_calls', [])
    s_ans = s.get('final_answer', '')
    m_ans = m.get('final_answer', '')
    s_iter = s.get('iteration', 0)
    m_iter = m.get('iteration', 0)
    s_time = s.get('elapsed_seconds', 0)
    m_time = m.get('elapsed_seconds', 0)
    s_len = s.get('answer_length', 0)
    m_len = m.get('answer_length', 0)
    s_labels = s.get('intent_labels', [])
    m_labels = m.get('intent_labels', [])
    
    issues = []
    
    # 工具数量差异
    if len(s_tools) != len(m_tools):
        issues.append(f'工具数: {len(s_tools)} vs {len(m_tools)}')
    elif set(s_tools) != set(m_tools):
        issues.append(f'工具不同: {sorted(s_tools)[:5]} vs {sorted(m_tools)[:5]}')
    
    # 标签差异
    if set(s_labels) != set(m_labels):
        issues.append(f'标签不同: {s_labels} vs {m_labels}')
    
    # 回答长度差异
    if s_len > 0 and m_len > 0:
        ratio = max(s_len, m_len) / min(s_len, m_len)
        if ratio > 2:
            issues.append(f'回答长度差异大: {s_len} vs {m_len}字')
    
    # 迭代差异
    if s_iter != m_iter:
        issues.append(f'迭代: {s_iter} vs {m_iter}轮')
    
    # 空回答
    if not s_ans.strip():
        issues.append('稳定版空回答')
        stable_issues += 1
    if not m_ans.strip():
        issues.append('修改版空回答')
        modified_issues += 1
    
    # 路由退化
    if not m_labels:
        issues.append('修改版无路由标签')
    
    # 幻觉检查
    dangerous = ['根据训练数据', '据我所知', '我记忆中', '根据我的知识']
    for kw in dangerous:
        if kw in m_ans and kw not in s_ans:
            issues.append(f'修改版新增幻觉信号: {kw}')
        if kw in s_ans and kw not in m_ans:
            issues.append(f'修改版消除了幻觉信号: {kw}')
    
    if issues:
        print(f'#{qid:2d} [{cat}] {" | ".join(issues)}')
    else:
        print(f'#{qid:2d} [{cat}]  一致 (工具:{len(s_tools)}, 迭代:{s_iter}, 回答:{s_len}字, {s_time:.0f}s)')

print()
print('=== 总览 ===')
s_total_tools = sum(len(r['tool_calls']) for r in stable['results'])
m_total_tools = sum(len(r['tool_calls']) for r in modified['results'])
s_total_time = stable['total_elapsed']
m_total_time = modified['total_elapsed']
s_total_len = sum(r.get('answer_length', 0) for r in stable['results'])
m_total_len = sum(r.get('answer_length', 0) for r in modified['results'])

print(f'工具总数:   稳定版 {s_total_tools:3d} vs 修改版 {m_total_tools:3d} ({m_total_tools-s_total_tools:+d})')
print(f'总耗时:     稳定版 {s_total_time:.0f}s vs 修改版 {m_total_time:.0f}s ({m_total_time-s_total_time:+.0f}s)')
print(f'回答总字数: 稳定版 {s_total_len:5d} vs 修改版 {m_total_len:5d} ({m_total_len-s_total_len:+d})')
print(f'平均工具/题: 稳定版 {s_total_tools/20:.1f}  vs 修改版 {m_total_tools/20:.1f}')
print(f'平均回答长度: 稳定版 {s_total_len/20:.0f}  vs 修改版 {m_total_len/20:.0f}')
print(f'严重问题:    稳定版 {stable_issues}  vs 修改版 {modified_issues}')

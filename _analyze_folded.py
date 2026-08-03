# -*- coding: utf-8 -*-
"""第一步：分析所有JSON任务页面，检测折叠/过场动画标记及其内容收录情况"""
import os
import json
import re
from collections import defaultdict

CONTENT_DIR = r'C:\Users\24701\Desktop\原神剧情\CASE-原神剧情助手-修改用\content_data'

# 统计
stats = {
    'total_quest_pages': 0,
    'has_cutscene_marker': 0,      # 文本中提到"过场动画"
    'has_fold_marker': 0,          # 文本中提到"折叠"
    'has_both_markers': 0,         # 两者都有
    'cutscene_content_present': 0, # 过场动画后确实有内容（非空）
    'cutscene_content_missing': 0, # 过场动画标记后无实际内容
    'pages_with_issues': [],       # 有问题的页面详情
}

def count_nonempty_chars(text, start_pos):
    """从start_pos之后统计非空白字符数"""
    rest = text[start_pos:]
    return len(re.sub(r'\s', '', rest))

results = []

for fname in sorted(os.listdir(CONTENT_DIR)):
    if not fname.startswith('quests_') or not fname.endswith('.json'):
        continue
    if fname == 'quests_processed.json':
        # quests_processed 是长任务的切片，另行分析
        continue

    filepath = os.path.join(CONTENT_DIR, fname)
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            quests = json.load(f)
    except Exception as e:
        print(f"[错误] 无法读取 {fname}: {e}")
        continue

    for q in quests:
        title = q.get('title', '')
        text = q.get('text', '')
        category = q.get('category', '')
        pageid = q.get('pageid', '')

        if not text:
            continue

        stats['total_quest_pages'] += 1

        has_cutscene = '过场动画' in text
        has_fold = '折叠' in text

        if has_cutscene:
            stats['has_cutscene_marker'] += 1
        if has_fold:
            stats['has_fold_marker'] += 1
        if has_cutscene and has_fold:
            stats['has_both_markers'] += 1

        if not has_cutscene and not has_fold:
            continue

        # 找到"过场动画"最后出现的位置，检查后面是否有实质内容
        issue = None

        if has_cutscene:
            # 找最后一个"过场动画"出现的位置
            last_pos = text.rfind('过场动画')
            after_text = text[last_pos + 4:]  # 跳过"过场动画"四个字

            # 检查后面是否有实际对话内容（不是空/纯标记）
            # 正常的过场动画后应该有角色对话如 "*xxx：" 或叙述文本
            after_clean = after_text.strip()

            # 判断后面是否有实质内容
            has_dialogue = bool(re.search(r'[\*:：）\)]', after_clean[:200]))
            has_substantial_text = len(after_clean) > 50 and not after_clean.startswith('|折叠')

            if after_clean.startswith('|折叠') or after_clean.startswith('\n折叠') or after_clean.startswith('折叠'):
                # 明确标记为折叠但无实际内容
                issue = 'cutscene_fold_empty'
                stats['cutscene_content_missing'] += 1
            elif after_clean.startswith('{{折叠'):
                # {{折叠|过场动画|...}} - 这表示内容已收入
                # 检查折叠内是否有实际内容
                fold_match = re.search(r'\{\{折叠\|([^|]+)\|([^}]+)\}\}', after_clean[:500])
                if fold_match:
                    fold_content = fold_match.group(2).strip()
                    if len(fold_content) > 50:
                        issue = None  # 有内容
                    else:
                        issue = 'cutscene_fold_placeholder'
                else:
                    # {{折叠 开头但格式不完整
                    issue = 'fold_truncated'
            elif len(after_clean) < 100 and ('折叠' in after_clean or not has_dialogue):
                issue = 'cutscene_content_short'
            else:
                stats['cutscene_content_present'] += 1

        # 检查单独的"折叠"标记（非过场动画）
        if has_fold and not has_cutscene:
            fold_pattern = re.findall(r'\{\{折叠\|', text)
            if fold_pattern:
                # 检查折叠内是否有实际内容
                fold_blocks = re.findall(r'\{\{折叠\|([^}]*)\}\}', text)
                empty_folds = sum(1 for fb in fold_blocks if len(fb.strip()) < 50)
                if empty_folds > 0 and empty_folds == len(fold_blocks):
                    issue = 'fold_empty_all'
                elif empty_folds > 0:
                    issue = 'fold_partial_empty'

        if issue:
            wiki_url = f"https://wiki.biligame.com/ys/{title}"
            stats['pages_with_issues'].append({
                'title': title,
                'category': category,
                'pageid': pageid,
                'file': fname,
                'issue': issue,
                'wiki_url': wiki_url,
                'text_end': text[-200:] if len(text) > 200 else text,
            })
        elif has_cutscene or has_fold:
            wiki_url = f"https://wiki.biligame.com/ys/{title}"
            results.append({
                'title': title,
                'category': category,
                'pageid': pageid,
                'file': fname,
                'status': 'OK',
                'wiki_url': wiki_url,
            })

# 输出统计
print("=" * 60)
print("  折叠/过场动画收录分析报告")
print("=" * 60)
print(f"\n任务页面总数: {stats['total_quest_pages']}")
print(f"含'过场动画'标记: {stats['has_cutscene_marker']}")
print(f"含'折叠'标记: {stats['has_fold_marker']}")
print(f"两者皆含: {stats['has_both_markers']}")
print(f"过场动画后有实质内容: {stats['cutscene_content_present']}")
print(f"过场动画后无实质内容: {stats['cutscene_content_missing']}")
print(f"\n【有问题页面】: {len(stats['pages_with_issues'])} 个")

# 按 issue 类型分组
by_issue = defaultdict(list)
for p in stats['pages_with_issues']:
    by_issue[p['issue']].append(p)

for issue_type, pages in sorted(by_issue.items()):
    print(f"\n--- {issue_type} ({len(pages)} 个) ---")
    for p in pages[:20]:
        print(f"  [{p['category']}] {p['title']} (pageid={p['pageid']})")
        if len(p['text_end']) > 80:
            print(f"    末尾: ...{p['text_end'][-80:]}")
        else:
            print(f"    末尾: {p['text_end']}")
    if len(pages) > 20:
        print(f"  ... 还有 {len(pages) - 20} 个")

print(f"\n【正常收录页面】: {len(results)} 个")

# 保存详细报告
report_path = os.path.join(os.path.dirname(CONTENT_DIR), '_fold_analysis_report.json')
with open(report_path, 'w', encoding='utf-8') as f:
    json.dump({
        'summary': {k: v for k, v in stats.items() if k != 'pages_with_issues'},
        'summary_detail': {
            'total_quest_pages': stats['total_quest_pages'],
            'has_cutscene_marker': stats['has_cutscene_marker'],
            'has_fold_marker': stats['has_fold_marker'],
            'has_both': stats['has_both_markers'],
            'cutscene_present': stats['cutscene_content_present'],
            'cutscene_missing': stats['cutscene_content_missing'],
            'issue_pages': len(stats['pages_with_issues']),
            'ok_pages': len(results),
        },
        'issues': stats['pages_with_issues'],
        'ok': results,
    }, f, ensure_ascii=False, indent=2)

print(f"\n详细报告已保存: {report_path}")

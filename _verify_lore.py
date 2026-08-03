import json

with open(r'c:\Users\24701\Desktop\原神剧情\CASE-原神剧情助手-修改用\content_data\lore.json', 'r', encoding='utf-8') as f:
    lore = json.load(f)

print(f'总条目: {len(lore)}')

# 查看限定文本条目
limited = [e for e in lore if '限定文本' in e['title']]
print(f'限定文本条目: {len(limited)}')

# 按区域分组
from collections import Counter
regions = Counter()
for e in limited:
    parts = e['title'].split(' / ')
    if len(parts) >= 2:
        regions[parts[1]] += 1
print('\n按区域分布:')
for r, c in regions.most_common():
    print(f'  {r}: {c}')

# 查看枫丹的条目
print('\n--- 枫丹条目 ---')
for e in limited:
    if '枫丹' in e['title']:
        print(f"\n  TITLE: {e['title']}")
        print(f"  TEXT: {e['text'][:200]}...")

# 查看蒙德最后一条
print('\n--- 蒙德条目抽样 ---')
mond = [e for e in limited if '蒙德' in e['title']]
for e in mond[-3:]:
    print(f"\n  TITLE: {e['title']}")
    print(f"  TEXT: {e['text'][:200]}...")

# 检查是否有 wikitext 残留
import re
issues = []
for e in limited:
    if re.search(r'\{\{|\}\}|\[\[|\]\]', e['title']):
        issues.append(('title 残留', e['title'][:80]))
    if re.search(r'\{\{|\}\}', e['text']):
        issues.append(('text 残留 {{}}', e['title'][:80]))
    if re.search(r'<br', e['text']):
        issues.append(('text 残留 <br>', e['title'][:80]))

if issues:
    print(f'\n--- 残留问题 ({len(issues)} 个) ---')
    for t, detail in issues[:10]:
        print(f'  [{t}] {detail}')
else:
    print('\n--- 无 wikitext 残留 ---')

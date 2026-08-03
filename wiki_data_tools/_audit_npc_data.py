# -*- coding: utf-8 -*-
"""审计 npcs_wiki_details.json 的数据质量"""
import json, os

CONTENT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'content_data')
DETAILS_FILE = os.path.join(CONTENT_DIR, 'npcs_wiki_details.json')
SMW_FILE = os.path.join(CONTENT_DIR, 'npcs_smw_raw.json')

with open(DETAILS_FILE, 'r', encoding='utf-8') as f:
    details = json.load(f)
with open(SMW_FILE, 'r', encoding='utf-8') as f:
    smw = json.load(f)

total_smw = len(smw)
total_details = len(details)

# 分类统计
ok_count = 0          # _status == 'ok'
error_count = 0       # _status 包含 'fetch_error'
empty_count = 0       # _status == 'empty_page'
no_status = 0         # 没有 _status（旧数据，从旧脚本来的）
has_quests = 0        # related_quests 非空
has_dialogue = 0      # dialogue 非空

error_npcs = []
no_status_npcs = []
empty_dialogue_ok = []  # status=ok 但 dialogue 为空

for name, d in details.items():
    st = d.get('_status', '')
    if st == 'ok':
        ok_count += 1
    elif 'fetch_error' in st:
        error_count += 1
        error_npcs.append(name)
    elif st == 'empty_page':
        empty_count += 1
    else:
        no_status += 1
        no_status_npcs.append(name)
    
    if d.get('related_quests'):
        has_quests += 1
    if d.get('dialogue', '').strip():
        has_dialogue += 1
    if st == 'ok' and not d.get('dialogue', '').strip():
        empty_dialogue_ok.append(name)

print('=' * 60)
print(' npcs_wiki_details.json 数据审计')
print('=' * 60)
print(f' SMW 总 NPC:   {total_smw}')
print(f' details 总条目: {total_details}')
print()
print(f' 状态分布:')
print(f'   新脚本-成功 (ok):        {ok_count}')
print(f'   新脚本-失败 (fetch_error): {error_count}')
print(f'   新脚本-空页 (empty_page):  {empty_count}')
print(f'   旧数据 (无 _status):      {no_status}')
print(f'   合计:                     {ok_count + error_count + empty_count + no_status}')
print()
print(f' 字段质量:')
print(f'   有 related_quests 的:    {has_quests} / {total_details} ({100*has_quests/total_details:.1f}%)')
print(f'   有 dialogue 的:          {has_dialogue} / {total_details} ({100*has_dialogue/total_details:.1f}%)')
print(f'   status=ok 但无对话的:    {len(empty_dialogue_ok)}')

if error_npcs:
    print(f'\n 失败的 NPC ({len(error_npcs)} 个):')
    for n in error_npcs[:20]:
        print(f'   - {n}')
    if len(error_npcs) > 20:
        print(f'   ... 还有 {len(error_npcs)-20} 个')

if no_status_npcs:
    print(f'\n 旧数据 NPC ({len(no_status_npcs)} 个，未用新脚本重建):')
    for n in no_status_npcs[:10]:
        has_q = bool(details[n].get('related_quests'))
        has_d = bool(details[n].get('dialogue', '').strip())
        print(f'   - {n} (related_quests={has_q}, dialogue={has_d})')
    if len(no_status_npcs) > 10:
        print(f'   ... 还有 {len(no_status_npcs)-10} 个')

if empty_dialogue_ok:
    print(f'\n status=ok 但无对话的 NPC ({len(empty_dialogue_ok)} 个):')
    for n in empty_dialogue_ok[:10]:
        has_q = bool(details[n].get('related_quests'))
        print(f'   - {n} (related_quests={has_q})')
    if len(empty_dialogue_ok) > 10:
        print(f'   ... 还有 {len(empty_dialogue_ok)-10} 个')

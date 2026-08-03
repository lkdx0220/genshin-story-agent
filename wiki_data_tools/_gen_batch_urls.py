#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""生成批量 wiki API URL（50 NPC/批）"""
import json
import os
import urllib.parse

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONTENT_DIR = os.path.join(SCRIPT_DIR, 'content_data')
INPUT_FILE = os.path.join(CONTENT_DIR, 'npcs_smw_raw.json')
URLS_FILE = os.path.join(CONTENT_DIR, 'npc_batch_urls.json')

BATCH_SIZE = 50

with open(INPUT_FILE, 'r', encoding='utf-8') as f:
    npcs_data = json.load(f)

npc_names = list(npcs_data.keys())
total = len(npc_names)
print(f'NPC 总数: {total}')

batches = []
for i in range(0, total, BATCH_SIZE):
    batch_names = npc_names[i:i+BATCH_SIZE]
    titles = '|'.join(urllib.parse.quote(n) for n in batch_names)
    url = f'https://wiki.biligame.com/ys/api.php?action=query&titles={titles}&prop=revisions&rvprop=content&rvslots=main&format=json'
    batches.append({
        'batch_id': i // BATCH_SIZE,
        'start_idx': i,
        'end_idx': min(i + BATCH_SIZE, total),
        'names': batch_names,
        'url': url,
    })

with open(URLS_FILE, 'w', encoding='utf-8') as f:
    json.dump(batches, f, ensure_ascii=False, indent=2)

print(f'生成 {len(batches)} 个批次 URL')
print(f'保存到: {URLS_FILE}')
print(f'URL 数量: {len(batches)}')

# 打印第一个 URL 作为示例
if batches:
    first = batches[0]
    print(f'\n批次0 URL (长度 {len(first["url"])}):')
    print(first['url'][:200] + '...')

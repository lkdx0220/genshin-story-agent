#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""为剩余未抓取的 NPC 生成批量 wiki API URL（每批 50 个）"""
import json
import urllib.parse

CP_FILE = 'content_data/npc_wiki_cp.json'
SMW_FILE = 'content_data/npcs_smw_raw.json'
OUT_FILE = 'content_data/npc_batch_urls_remaining.json'
BATCH_SIZE = 50

with open(CP_FILE, 'r', encoding='utf-8') as f:
    cp = json.load(f)
with open(SMW_FILE, 'r', encoding='utf-8') as f:
    npcs_data = json.load(f)

npc_items = list(npcs_data.items())
start_idx = cp.get('idx', 0)
total = len(npc_items)
print(f'进度: {start_idx}/{total}, 剩余: {total - start_idx}')

batches = []
for i in range(start_idx, total, BATCH_SIZE):
    batch_names = [npc_items[j][0] for j in range(i, min(i + BATCH_SIZE, total))]
    titles = '|'.join(urllib.parse.quote(n) for n in batch_names)
    url = f'https://wiki.biligame.com/ys/api.php?action=query&titles={titles}&prop=revisions&rvprop=content&rvslots=main&format=json'
    batches.append({
        'batch_id': len(batches) + 1,
        'start_idx': i,
        'end_idx': min(i + BATCH_SIZE, total),
        'count': len(batch_names),
        'names': batch_names,
        'url': url,
    })

with open(OUT_FILE, 'w', encoding='utf-8') as f:
    json.dump(batches, f, ensure_ascii=False, indent=2)

print(f'已生成 {len(batches)} 个批次 => {OUT_FILE}')
print(f'第1批({batches[0]["count"]}个): {batches[0]["names"][:3]}...')
print(f'URL: {batches[0]["url"][:120]}...')

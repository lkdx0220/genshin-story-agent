#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""只索引 quests_魔神任务.json（断点续跑）"""
import json, os, time

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONTENT_DIR = os.path.join(SCRIPT_DIR, 'content_data')
VECTOR_DIR = os.path.join(SCRIPT_DIR, 'kb_vectors')
CP_FILE = os.path.join(VECTOR_DIR, 'archon_cp.json')

from kb_vector_store import KBVectorStore

store = KBVectorStore()

# 加载魔神任务
with open(os.path.join(CONTENT_DIR, 'quests_魔神任务.json'), 'r', encoding='utf-8') as f:
    quests = json.load(f)

# 加载 processed
processed_path = os.path.join(CONTENT_DIR, 'quests_processed.json')
processed_titles = set()
if os.path.exists(processed_path):
    with open(processed_path, 'r', encoding='utf-8') as f:
        processed_titles = set(json.load(f).keys())

# 筛选需要索引的任务
to_index = []
for q in quests:
    title = q.get('title', '')
    if not title:
        continue
    if title in processed_titles:
        continue
    text = q.get('text', '')
    if not text or len(text.strip()) < 50:
        continue
    to_index.append(q)

# 读取断点
start_idx = 0
if os.path.exists(CP_FILE):
    with open(CP_FILE, 'r', encoding='utf-8') as f:
        start_idx = json.load(f).get('idx', 0)

print(f'魔神任务: {len(to_index)} 条, 从 {start_idx} 开始')

BATCH = 5
total = 0
ids, docs, metas = [], [], []

for i in range(start_idx, len(to_index)):
    q = to_index[i]
    title = q['title']
    text = q['text']
    category = q.get('category', '') or '魔神任务'
    meta = q.get('metadata', {})
    version = meta.get('所属版本', '') or meta.get('版本', '') or ''

    ids.append(f'quest:{title}:full')
    docs.append(text)
    metas.append({
        'source_file': 'quests_魔神任务.json', 'title': title,
        'entry_type': category, 'version': version,
        'chunk_index': 0, 'total_chunks': 1,
        'text_preview': text[:200], 'source': 'quest_full',
    })

    if len(ids) >= BATCH:
        for retry in range(5):
            try:
                store.add('kb_quests', ids, docs, metas)
                break
            except Exception as e:
                wait = (retry + 1) * 10
                print(f'  retry {retry+1}: {e}')
                time.sleep(wait)
        else:
            print('  致命错误')
            exit(1)
        total += len(ids)
        print(f'  [{i+1}/{len(to_index)}] total={total}')
        ids, docs, metas = [], [], []
        # 保存断点
        with open(CP_FILE, 'w', encoding='utf-8') as f:
            json.dump({'idx': i + 1}, f)
        time.sleep(3)

if ids:
    store.add('kb_quests', ids, docs, metas)
    total += len(ids)
    print(f'  total={total}')

# 完成，删除断点
if os.path.exists(CP_FILE):
    os.remove(CP_FILE)
print(f'完成: {total} 条')
print(store.get_stats())

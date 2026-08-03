# -*- coding: utf-8 -*-
"""检查世界任务多子任务系列 wiki 顺序"""
import sys
import re
import time
import urllib.parse
from collections import OrderedDict

sys.path.insert(0, r'C:\Users\24701\Desktop\原神剧情\CASE-原神剧情助手-修改用\genshin_knowledge_base')
from quests import 任务知识库

import requests

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9',
    'Accept-Language': 'zh-CN,zh;q=0.9',
    'Referer': 'https://wiki.biligame.com/ys/',
}
session = requests.Session()
session.headers.update(HEADERS)

def fetch_wiki_toc(page_name, retries=2):
    encoded = urllib.parse.quote(page_name)
    url = f'https://wiki.biligame.com/ys/{encoded}'
    for attempt in range(retries):
        try:
            resp = session.get(url, timeout=20)
            resp.encoding = 'utf-8'
            if resp.status_code == 200:
                html = resp.text
                break
            elif resp.status_code == 567:
                time.sleep(5)
                continue
            return None
        except:
            return None
    pattern = r'<li class="toclevel-1"[^>]*?>.*?<span class="toctext">([^<]+)</span>'
    matches = re.findall(pattern, html, re.DOTALL)
    if not matches:
        return None
    seen = set()
    result = []
    for m in matches:
        m = m.strip()
        if m and m not in seen:
            seen.add(m)
            result.append(m)
    return result

def normalize_name(name):
    return re.sub(r'[（(][^）)]*[）)]', '', name).strip()

# 分组世界任务
series_groups = OrderedDict()
for q in 任务知识库:
    if q.get('任务类型') != '世界任务':
        continue
    s = q.get('系列任务', '')
    if not s:
        continue
    if s not in series_groups:
        series_groups[s] = []
    series_groups[s].append(q.get('任务名称', ''))

multi = {k: v for k, v in series_groups.items() if len(v) > 1}
print(f"世界任务多子任务系列: {len(multi)}")

wrong_list = []
for i, (series_name, db_tasks) in enumerate(multi.items()):
    if ',' in series_name:
        page_name = series_name.split(',', 1)[1].strip()
    else:
        page_name = series_name.strip()

    print(f"\n[{i+1}/{len(multi)}] {series_name} -> {page_name}", end='')
    
    wiki_toc = fetch_wiki_toc(page_name)
    if wiki_toc is None:
        print(f" [FAIL]")
        continue

    db_norm = [normalize_name(t) for t in db_tasks]
    db_set = set(db_norm)
    wiki_filtered = [t for t in wiki_toc if normalize_name(t) in db_set]

    if len(wiki_filtered) != len(db_norm):
        print(f"\n  DB({len(db_norm)}): {db_norm}")
        print(f"  Wiki({len(wiki_toc)}): {wiki_toc}")
        print(f"  Match({len(wiki_filtered)}): {wiki_filtered}")
        continue

    wiki_order = [normalize_name(t) for t in wiki_filtered]
    if wiki_order == db_norm:
        print(" [OK]")
    else:
        print(f"\n  [WRONG] Wiki: {' -> '.join(wiki_filtered)}")
        print(f"          DB:   {' -> '.join(db_tasks)}")
        wrong_list.append((series_name, db_tasks, wiki_filtered))
    
    time.sleep(3)

print(f"\n===== 世界任务结果: {len(wrong_list)} 错误 =====")
for s, db, wiki in wrong_list:
    print(f"  {s}")

# -*- coding: utf-8 -*-
"""统一批量检查：活动剧情、部族纪闻、世界任务 的子任务 wiki 顺序"""
import sys
import re
import time
import urllib.parse
from collections import OrderedDict

sys.path.insert(0, r'C:\Users\24701\Desktop\原神剧情\CASE-原神剧情助手-修改用\genshin_knowledge_base')
from quests import 任务知识库

import requests

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
    'Accept-Language': 'zh-CN,zh;q=0.9',
    'Referer': 'https://wiki.biligame.com/ys/',
}
session = requests.Session()
session.headers.update(HEADERS)

def fetch_wiki_toc(page_name, retries=3):
    """抓取 wiki 页面，提取 TOC 中的子任务名列表"""
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
                if attempt < retries - 1:
                    wait = (attempt + 1) * 5
                    time.sleep(wait)
                    continue
                return None
            else:
                return None
        except Exception:
            if attempt < retries - 1:
                time.sleep(2)
                continue
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

def check_type(task_type, base_dir):
    """检查一种任务类型的所有系列"""
    series_groups = OrderedDict()
    for q in 任务知识库:
        if q.get('任务类型') != task_type:
            continue
        s = q.get('系列任务', '')
        if not s:
            continue
        if s not in series_groups:
            series_groups[s] = []
        series_groups[s].append(q.get('任务名称', ''))

    multi = {k: v for k, v in series_groups.items() if len(v) > 1}
    if not multi:
        print(f"  [{task_type}] 无多子任务系列，跳过")
        return [], [], [], []

    print(f"\n{'='*60}")
    print(f"===== {task_type}: {len(multi)} 个多子任务系列 =====")

    correct_list = []
    wrong_list = []
    mismatch_list = []
    fetch_failed = []

    for i, (series_name, db_tasks) in enumerate(multi.items()):
        if ',' in series_name:
            page_name = series_name.split(',', 1)[1].strip()
        else:
            page_name = series_name.strip()

        print(f"[{i+1}/{len(multi)}] {series_name}", end='', flush=True)
        
        wiki_toc = fetch_wiki_toc(page_name)
        if wiki_toc is None:
            fetch_failed.append((series_name, db_tasks, page_name))
            print(f" [FAIL: {page_name}]")
            time.sleep(3)
            continue

        db_norm = [normalize_name(t) for t in db_tasks]
        db_set = set(db_norm)
        
        wiki_filtered = []
        for t in wiki_toc:
            if normalize_name(t) in db_set:
                wiki_filtered.append(t)

        if len(wiki_filtered) != len(db_norm):
            print(f"\n  DB({len(db_norm)}): {db_norm}")
            print(f"  Wiki({len(wiki_toc)}): {wiki_toc}")
            print(f"  Matched({len(wiki_filtered)}): {wiki_filtered}")
            mismatch_list.append((series_name, db_tasks, wiki_toc))
        else:
            wiki_order = [normalize_name(t) for t in wiki_filtered]
            if wiki_order == db_norm:
                print(" [OK]")
                correct_list.append((series_name, db_tasks))
            else:
                print(f"\n  [WRONG]")
                print(f"    Wiki: {' -> '.join(wiki_filtered)}")
                print(f"    DB:   {' -> '.join(db_tasks)}")
                wrong_list.append((series_name, db_tasks, wiki_filtered))

        time.sleep(3)

    print(f"\n  {task_type} 结果: 正确{len(correct_list)} / 错误{len(wrong_list)} / 不匹配{len(mismatch_list)} / 失败{len(fetch_failed)}")
    return correct_list, wrong_list, mismatch_list, fetch_failed


def main():
    all_results = {}
    for task_type in ['活动剧情', '部族纪闻']:
        correct, wrong, mismatch, failed = check_type(task_type, 
            r'C:\Users\24701\Desktop\原神剧情\CASE-原神剧情助手-修改用')
        all_results[task_type] = {
            'correct': correct, 'wrong': wrong, 'mismatch': mismatch, 'failed': failed
        }

    # 保存活动剧情 + 部族纪闻的错误数据
    out_path = r'C:\Users\24701\Desktop\原神剧情\CASE-原神剧情助手-修改用\_event_tribal_wrong.py'
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write('# -*- coding: utf-8 -*-\n')
        f.write('# 活动剧情 + 部族纪闻 wiki 正确顺序\n')
        f.write('ALL_WRONG = {\n')
        for task_type in ['活动剧情', '部族纪闻']:
            r = all_results.get(task_type, {})
            for s, db, wiki in r.get('wrong', []):
                f.write(f'    ("{task_type}", "{s}", {repr(wiki)}),\n')
        f.write('}\n')
    print(f"\n错误数据已保存到 {out_path}")
    
    # 汇总
    print("\n" + "=" * 60)
    print("===== 汇总 =====")
    total_correct = sum(len(r['correct']) for r in all_results.values())
    total_wrong = sum(len(r['wrong']) for r in all_results.values())
    total_mismatch = sum(len(r['mismatch']) for r in all_results.values())
    total_failed = sum(len(r['failed']) for r in all_results.values())
    print(f"正确: {total_correct}, 错误: {total_wrong}, 不匹配: {total_mismatch}, 失败: {total_failed}")


if __name__ == '__main__':
    main()

# -*- coding: utf-8 -*-
"""全自动批量抓取魔神任务 wiki TOC，对照 DB 子任务顺序（带重试和延迟）"""
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
                    print(f"  [567 重试 {attempt+1}/{retries}, 等待{wait}s]", end='', flush=True)
                    time.sleep(wait)
                    continue
                else:
                    print(f"  [HTTP 567 最终失败] {page_name}")
                    return None
            else:
                print(f"  [HTTP {resp.status_code}] {page_name}")
                return None
        except Exception as e:
            if attempt < retries - 1:
                print(f"  [异常 重试{attempt+1}]: {e}", end='', flush=True)
                time.sleep(2)
                continue
            else:
                print(f"  [异常 最终]: {e}")
                return None
    
    # 提取 toclevel-1 的章节标题
    # 精确匹配：在 <li class="toclevel-1"> 内的第一个 <span class="toctext">
    pattern = r'<li class="toclevel-1"[^>]*?>.*?<span class="toctext">([^<]+)</span>'
    matches = re.findall(pattern, html, re.DOTALL)
    
    if not matches:
        print(f"  [无TOC] {page_name}")
        return None
    
    # 去重保持顺序
    seen = set()
    result = []
    for m in matches:
        m = m.strip()
        if m and m not in seen:
            seen.add(m)
            result.append(m)
    
    return result


def normalize_name(name):
    """标准化任务名：去除括号注释如（任务）"""
    return re.sub(r'[（(][^）)]*[）)]', '', name).strip()


def main():
    # 1. 分组所有魔神任务
    series_groups = OrderedDict()
    for q in 任务知识库:
        if q.get('任务类型') != '魔神任务':
            continue
        s = q.get('系列任务', '')
        if not s:
            continue
        if s not in series_groups:
            series_groups[s] = []
        series_groups[s].append(q.get('任务名称', ''))

    multi_series = {k: v for k, v in series_groups.items() if len(v) > 1}
    print(f"===== 魔神任务多子任务系列: {len(multi_series)} 个 =====")

    correct_list = []
    wrong_list = []
    mismatch_list = []
    fetch_failed = []

    for i, (series_name, db_tasks) in enumerate(multi_series.items()):
        if ',' in series_name:
            page_name = series_name.split(',', 1)[1].strip()
        else:
            page_name = series_name.strip()

        print(f"\n[{i+1}/{len(multi_series)}] {series_name}", end='', flush=True)

        wiki_toc = fetch_wiki_toc(page_name)
        if wiki_toc is None:
            fetch_failed.append((series_name, db_tasks, page_name))
            time.sleep(2)
            continue

        # 标准化对比
        db_norm = [normalize_name(t) for t in db_tasks]
        db_set = set(db_norm)

        # 过滤 wiki TOC：只保留与 DB 任务名匹配的章节
        wiki_filtered = []
        for t in wiki_toc:
            n = normalize_name(t)
            if n in db_set:
                wiki_filtered.append(t)

        if len(wiki_filtered) != len(db_norm):
            wiki_norm_all = [normalize_name(t) for t in wiki_toc]
            wiki_set_all = set(wiki_norm_all)
            only_db = db_set - wiki_set_all
            only_wiki = wiki_set_all - db_set
            
            print(f"\n  DB ({len(db_norm)}): {db_norm}")
            print(f"  Wiki TOC ({len(wiki_toc)}): {wiki_toc}")
            print(f"  Wiki匹配 ({len(wiki_filtered)}): {wiki_filtered}")
            if only_db:
                print(f"  仅DB: {only_db}")
            if only_wiki:
                print(f"  仅Wiki: {only_wiki}")
            
            if len(wiki_filtered) == 0:
                mismatch_list.append((series_name, db_tasks, wiki_toc))
            elif len(wiki_filtered) != len(db_norm):
                mismatch_list.append((series_name, db_tasks, wiki_filtered))
        else:
            wiki_order = [normalize_name(t) for t in wiki_filtered]
            if wiki_order == db_norm:
                print(f"  [OK]")
                correct_list.append((series_name, db_tasks))
            else:
                print(f"\n  [WRONG] Wiki: {' -> '.join([t for t in wiki_filtered])}")
                print(f"          DB:   {' -> '.join(db_tasks)}")
                wrong_list.append((series_name, db_tasks, wiki_filtered))

        time.sleep(5)  # 请求间隔（避免触发反爬）

    # 输出报告
    print("\n" + "=" * 60)
    print(f"===== 魔神任务顺序对照报告 =====")
    print(f"正确: {len(correct_list)}")
    print(f"错误: {len(wrong_list)}")
    print(f"不匹配: {len(mismatch_list)}")
    print(f"抓取失败: {len(fetch_failed)}")

    if wrong_list:
        print(f"\n--- 错误 ({len(wrong_list)}) ---")
        for s, db, wiki in wrong_list:
            print(f"  {s}")
            print(f"    Wiki: {' -> '.join(wiki)}")
            print(f"    DB:   {' -> '.join(db)}")

    if mismatch_list:
        print(f"\n--- 名称不匹配 ({len(mismatch_list)}) ---")
        for s, db, wiki in mismatch_list:
            print(f"  {s}: DB={db} Wiki={wiki}")

    if fetch_failed:
        print(f"\n--- 抓取失败 ({len(fetch_failed)}) ---")
        for s, db, pn in fetch_failed:
            print(f"  {s} (page: {pn})")

    # 保存错误数据
    if wrong_list:
        out_path = r'C:\Users\24701\Desktop\原神剧情\CASE-原神剧情助手-修改用\_archon_wrong.py'
        with open(out_path, 'w', encoding='utf-8') as f:
            f.write('# -*- coding: utf-8 -*-\n')
            f.write('# 魔神任务 wiki 正确顺序\n')
            f.write('ARCHON_WRONG = [\n')
            for s, db, wiki in wrong_list:
                f.write(f'    ("{s}", {repr(wiki)}),\n')
            f.write(']\n')
        print(f"\n错误数据已保存到 {out_path}")


if __name__ == '__main__':
    main()

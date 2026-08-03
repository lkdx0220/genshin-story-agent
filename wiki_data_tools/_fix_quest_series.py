#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
从 wiki SMW API 获取所有世界任务的系列关系，并更新知识库。
修复"未完成的喜剧"等系列任务缺失系列归属的问题。
"""

import urllib.request
import urllib.parse
import json
import re
import os
import time

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
CONTENT_DIR = os.path.join(PROJECT_DIR, 'content_data')
REQUEST_DELAY = 0.5


def ask(query, limit=500):
    """查询 wiki SMW API"""
    params = {
        'action': 'ask',
        'query': query,
        'format': 'json',
        'api_version': '3',
        'limit': str(limit),
    }
    url = 'https://wiki.biligame.com/ys/api.php?' + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    try:
        r = urllib.request.urlopen(req, timeout=30)
        d = json.loads(r.read())
        results = d.get('query', {}).get('results', {})
        return list(results.values()) if isinstance(results, dict) else (results or [])
    except Exception as e:
        print(f"  [WARN] 查询失败: {e}")
        return []


def extract_series_name(raw):
    """从 [[水仙的追迹|水仙的追迹]] 中提取纯文本"""
    if not raw:
        return ''
    text = raw.replace('[[', '').replace(']]', '')
    parts = text.split('|')
    return parts[-1].strip()


def load_task_series_map():
    """从 wiki API 加载所有地图事件的系列关系"""
    regions = ['枫丹', '须弥', '稻妻', '璃月', '蒙德', '纳塔',
               '龙脊雪山', '层岩巨渊·地下矿区', '渊下宫', '旧日之海']
    task_series_map = {}
    for region in regions:
        print(f"  查询 {region} ...")
        time.sleep(REQUEST_DELAY)
        # 查询所有地图事件（不限制系列任务2），在代码中过滤
        query = (
            f'[[分类:地图事件]][[任务地区::~*{region}*]]'
            f'|?系列任务1|?系列任务2|limit=500'
        )
        results = ask(query)
        for item in results:
            # API v3: 每条结果是 {页面标题: {fulltext, printouts, ...}}
            if isinstance(item, dict) and len(item) == 1:
                name = next(iter(item.keys()))
                page_data = item[name]
            else:
                name = item.get('fulltext', '')
                page_data = item
            if not name:
                continue
            po = page_data.get('printouts', {})
            s1_vals = po.get('系列任务1', [])
            s2_vals = po.get('系列任务2', [])
            s1 = extract_series_name(s1_vals[0]) if s1_vals else ''
            s2 = extract_series_name(s2_vals[0]) if s2_vals else ''
            # 过滤：系列任务1必须有效（非空、非None、非"其他任务"）
            if not s1 or s1 == 'None' or s1 == '其他任务':
                continue
            # 系列任务2可能为"None"，此时只用系列任务1
            if s2 and s2 != 'None':
                task_series_map[name] = f'{s1},{s2}'
            else:
                task_series_map[name] = s1
        print(f"    {len(results)} 条, 累计 {len(task_series_map)}")
    return task_series_map


def fix_quests_py(task_series_map):
    """更新 genshin_knowledge_base/quests.py —— 用正则替换整个文件"""
    path = os.path.join(PROJECT_DIR, 'genshin_knowledge_base', 'quests.py')
    with open(path, 'r', encoding='utf-8') as f:
        content = f.read()

    updated = 0

    for task_name, series_val in task_series_map.items():
        escaped = re.escape(task_name)

        # 情况1：条目中已有 "系列任务" 字段 → 替换值
        # 使用 (?:(?!"任务名称":)[\s\S]) 限定在同一条目内，防止跨条目匹配
        pat_existing = re.compile(
            rf'("任务名称":\s*"{escaped}"(?:(?!"任务名称":)[\s\S])*?)"系列任务":\s*"[^"]*"',
            re.DOTALL
        )
        m = pat_existing.search(content)
        if m:
            new_block = m.group(1) + f'"系列任务": "{series_val}"'
            content = content.replace(m.group(0), new_block)
            updated += 1
            continue

        # 情况2：条目中没有 "系列任务" 字段 → 在 "关联角色" 之后插入
        pat_no_series = re.compile(
            rf'("任务名称":\s*"{escaped}"[\s\S]*?"关联角色":\s*"[^"]*")(\s*\n(\s*)[}}\]](,?))',
            re.DOTALL
        )
        m2 = pat_no_series.search(content)
        if m2:
            # group(3) 是 closing } 的缩进（通常 4 空格），field indent 需要加倍
            closing_indent = m2.group(3)
            field_indent = closing_indent * 2  # "关联角色" 等字段的缩进 = 双层
            prefix = m2.group(1)
            closing = m2.group(2)
            new_block = prefix + ',\n' + field_indent + f'"系列任务": "{series_val}"' + '\n' + closing_indent + closing.strip()
            content = content.replace(m2.group(0), new_block)
            updated += 1

    with open(path, 'w', encoding='utf-8') as f:
        f.write(content)
    return updated


def fix_json_file(task_series_map, json_filename):
    """更新 content_data 中的 JSON 文件"""
    path = os.path.join(CONTENT_DIR, json_filename)
    if not os.path.exists(path):
        return 0
    with open(path, 'r', encoding='utf-8') as f:
        quests = json.load(f)
    updated = 0
    for q in quests:
        title = q.get('title', '')
        if title in task_series_map:
            series_val = task_series_map[title]
            meta = q.get('metadata', {})
            if meta.get('系列任务') != series_val:
                meta['系列任务'] = series_val
                q['metadata'] = meta
                updated += 1
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(quests, f, ensure_ascii=False, indent=2)
    return updated


def main():
    print("=== 从 wiki API 获取系列任务关系 ===")
    task_series_map = load_task_series_map()
    print(f"共计 {len(task_series_map)} 个任务有系列归属\n")

    # 检查"未完成的喜剧"子任务
    for check_name in ['一报还一报', '从此以后…', '富豪游戏', '待揭晓的谜底', '尚未结束的故事']:
        if check_name in task_series_map:
            print(f"  {'✓':<2} {check_name} -> {task_series_map[check_name]}")
        else:
            print(f"  {'✗':<2} {check_name} -> 未找到系列信息")

    print("\n=== 更新 quests.py ===")
    py_updated = fix_quests_py(task_series_map)
    print(f"  更新了 {py_updated} 条")

    print("\n=== 更新 quests_世界任务.json ===")
    j1 = fix_json_file(task_series_map, 'quests_世界任务.json')
    print(f"  更新了 {j1} 条")

    for jf in ['quests_部族纪闻.json']:
        jn = fix_json_file(task_series_map, jf)
        if jn:
            print(f"\n=== 更新 {jf} ===")
            print(f"  更新了 {jn} 条")

    print("\n=== 完成 ===")


if __name__ == '__main__':
    main()

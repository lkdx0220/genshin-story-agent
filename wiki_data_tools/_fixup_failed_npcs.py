# -*- coding: utf-8 -*-
"""
补漏脚本：重新获取 npcs_wiki_details.json 中标记为 fetch_error 的 NPC

用法：python wiki_data_tools/_fixup_failed_npcs.py
"""

import json
import os
import sys
import time
import urllib.parse
import urllib.request
import urllib.error

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
CONTENT_DIR = os.path.join(PROJECT_DIR, 'content_data')
DETAILS_FILE = os.path.join(CONTENT_DIR, 'npcs_wiki_details.json')

# 从 _rebuild_npc_wiki_details 导入解析函数
sys.path.insert(0, SCRIPT_DIR)
from _rebuild_npc_wiki_details import (
    fetch_wiki, extract_dialogue_from_wikitext,
    extract_related_quests_from_html, extract_org_race_from_wikitext
)

DELAY = 3.0        # 每个请求间延迟 (秒)
MAX_RETRIES = 5    # 单个 NPC 最大重试次数
SAVE_EVERY = 5     # 每成功 N 个保存一次


def main():
    print('=' * 60)
    print(' 补漏脚本 - 重试 fetch_error 的 NPC')
    print('=' * 60)

    # 读取当前数据
    with open(DETAILS_FILE, 'r', encoding='utf-8') as f:
        details = json.load(f)

    # 找出所有失败的
    failed = []
    for name, d in details.items():
        st = d.get('_status', '')
        if 'fetch_error' in st:
            failed.append(name)

    if not failed:
        print('没有需要补漏的 NPC，数据已完整')
        return

    print(f' 需要补漏: {len(failed)} 个 NPC')
    print(f' 预计耗时: ~{len(failed) * (DELAY + 3) // 60} 分钟')
    print()

    # 健康检查：先试一个请求，确认 IP 已解封
    print(' 健康检查: 尝试请求 "提米"...')
    wt, html, err_msg = fetch_wiki('提米')
    if wt is None and 'HTTP 567' in err_msg:
        print(f'  IP 仍被 Cloudflare 封锁 ({err_msg})')
        print('  请等待更长时间（建议 30 分钟以上），然后重试')
        sys.exit(1)
    if wt is None:
        print(f'  健康检查失败: {err_msg}，但非 567，继续...')
    else:
        print(f'  健康检查通过!')
    print()

    # 读取 SMW 获取地区信息
    smw_file = os.path.join(CONTENT_DIR, 'npcs_smw_raw.json')
    with open(smw_file, 'r', encoding='utf-8') as f:
        smw = json.load(f)

    fixed = 0
    still_failed = 0
    start_time = time.time()

    for i, name in enumerate(failed):
        time.sleep(DELAY)

        # 请求 wiki API
        wt, html, err_msg = None, None, ''
        last_err = ''
        for retry in range(MAX_RETRIES):
            wt, html, err_msg = fetch_wiki(name)
            if wt is not None:
                break
            last_err = err_msg
            if retry == 0:
                print(f'  [{i+1}/{len(failed)}] {name} -> {err_msg}，重试中...')
            time.sleep(5)  # 重试前多等一会

        if wt is None:
            still_failed += 1
            print(f'  [{i+1}/{len(failed)}] {name} -> 最终失败 ({last_err})')
            continue

        if not wt:
            # 空页面
            details[name] = {
                'dialogue': '',
                'related_quests': [],
                'origin': smw.get(name, {}).get('所在国家', ''),
                'region': smw.get(name, {}).get('所在国家', ''),
                'org_race': '',
                '_status': 'empty_page'
            }
            fixed += 1
            continue

        # 提取数据
        dialogue = extract_dialogue_from_wikitext(wt)
        related_quests = extract_related_quests_from_html(html)
        org, race = extract_org_race_from_wikitext(wt)
        smw_entry = smw.get(name, {})
        origin = smw_entry.get('所在国家', '')

        details[name] = {
            'dialogue': dialogue,
            'related_quests': related_quests,
            'origin': origin,
            'region': origin,
            'org_race': f'{org}、{race}' if org and race else (org or race),
            '_status': 'ok'
        }
        fixed += 1
        print(f'  [{i+1}/{len(failed)}] {name} -> OK (对话 {len(dialogue)} 字, 任务 {len(related_quests)} 个)')

        # 定期保存
        if fixed % SAVE_EVERY == 0:
            with open(DETAILS_FILE, 'w', encoding='utf-8') as f:
                json.dump(details, f, ensure_ascii=False, indent=2)
            print(f'  [已保存]')

    # 最终保存
    with open(DETAILS_FILE, 'w', encoding='utf-8') as f:
        json.dump(details, f, ensure_ascii=False, indent=2)

    elapsed = time.time() - start_time
    print()
    print('=' * 60)
    print(f' 补漏完成! 耗时: {elapsed:.0f}s')
    print(f' 修复: {fixed}')
    print(f' 仍然失败: {still_failed}')
    print('=' * 60)


if __name__ == '__main__':
    main()

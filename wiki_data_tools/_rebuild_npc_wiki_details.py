# -*- coding: utf-8 -*-
"""
NPC wiki 详情全量重建

功能：
1. 读取 npcs_smw_raw.json 获取所有 NPC 名称
2. 逐个调用 wiki API (action=parse, prop=text|wikitext) 获取 wikitext + HTML
3. 从 wikitext 提取对话 ({{NPC对话}} 模板)
4. 从 HTML 提取 related_quests ("相关剧情" 段落里的任务/事件链接)
5. 写入 npcs_wiki_details.json
6. 支持断点续跑 (checkpoint 机制)

用法：python wiki_data_tools/_rebuild_npc_wiki_details.py
"""

import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
import urllib.error
from _safe_http import ensure_wiki_url

# 路径配置
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
CONTENT_DIR = os.path.join(PROJECT_DIR, 'content_data')
SMW_FILE = os.path.join(CONTENT_DIR, 'npcs_smw_raw.json')
OUTPUT_FILE = os.path.join(CONTENT_DIR, 'npcs_wiki_details.json')
CHECKPOINT_FILE = os.path.join(CONTENT_DIR, '_npc_wiki_rebuild_cp.json')

# 请求配置
BATCH_SIZE = 10         # 每批处理 NPC 数（降低避免触发 Cloudflare）
BATCH_DELAY = 5.0       # 批次间延迟 (秒，Cloudflare 限流时需加长)
REQUEST_DELAY = 1.5     # 单个请求间延迟 (秒)
MAX_RETRIES = 3         # 单个 NPC 最大重试次数

# API 配置
API_URL = 'https://wiki.biligame.com/ys/api.php'
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
}


def fetch_wiki(name, debug=False):
    """获取 NPC 的 wikitext 和 HTML，返回 (wikitext, html, error_msg)"""
    encoded = urllib.parse.quote(name)
    url = f'{API_URL}?action=parse&page={encoded}&prop=text%7Cwikitext&format=json'
    ensure_wiki_url(url)

    wiki_req = urllib.request.Request(url, headers=HEADERS)
    try:
        r = urllib.request.urlopen(wiki_req, timeout=30)
        d = json.loads(r.read())
    except urllib.error.HTTPError as e:
        return None, None, f'HTTP {e.code}'
    except urllib.error.URLError as e:
        return None, None, f'网络错误: {e.reason}'
    except Exception as e:
        return None, None, f'{type(e).__name__}: {e}'

    if 'error' in d:
        error_code = d.get('error', {}).get('code', '')
        error_info = d.get('error', {}).get('info', '')
        if error_code == 'missingtitle':
            return '', '', 'ok'  # 空页面，非错误
        return None, None, f'API错误: {error_code} - {error_info}'

    parse_data = d.get('parse', {})
    wt = parse_data.get('wikitext', {}).get('*', '')
    html = parse_data.get('text', {}).get('*', '')
    return wt, html, 'ok'


def extract_dialogue_from_wikitext(wikitext):
    """从 wikitext 提取所有对话文本（复用原 _fetch_npc_wiki_v2.py 的逻辑）"""
    if not wikitext:
        return ''

    dialogs = []
    # 匹配所有 {{NPC对话|...}} 块（嵌套处理）
    pos = 0
    while True:
        idx = wikitext.find('{{NPC对话', pos)
        if idx == -1:
            break

        # 找到对应的 }}
        start = idx
        depth = 0
        i = idx
        while i < len(wikitext) - 1:
            if wikitext[i:i+2] == '{{' and i >= start:
                depth += 1
                i += 2
                continue
            if wikitext[i:i+2] == '}}':
                depth -= 1
                if depth == 0:
                    block = wikitext[start:i+2]
                    dialogs.append(block)
                    pos = i + 2
                    break
                i += 2
                continue
            i += 1
        else:
            break  # 未闭合，放弃

    if not dialogs:
        return ''

    # 解析每个对话块
    results = []
    for block in dialogs:
        # 提取标题
        title_m = re.search(r'\|标题\s*=\s*(.+?)(?:\||\n\|)', block)
        title = title_m.group(1).strip() if title_m else ''

        # 去除内部嵌套的 {{NPC剧情|...}} 子块
        text = re.sub(r'\{\{NPC剧情[\s\S]*?\}\}', '', block, flags=re.DOTALL)

        # 逐行清理：保留对话文本行，去除模板结构行
        lines = text.split('\n')
        clean_lines = []
        for line in lines:
            stripped = line.strip()
            # 跳过模板标记行
            if stripped.startswith('{{') or stripped.startswith('}}'):
                continue
            # 跳过字段定义行 (|字段名=值)
            if re.match(r'\|[^\|]+\s*=', stripped):
                # 但保留值部分（对话内容）
                val = re.sub(r'^\|[^\|]+\s*=\s*', '', stripped)
                if val and len(val) > 2:
                    clean_lines.append(val)
                continue
            clean_lines.append(line)

        text = '\n'.join(clean_lines)

        # wiki 链接展开: [[页面名]] 或 [[页面名|显示文本]] -> 显示文本 或 页面名
        text = re.sub(r'\[\[([^\]|]+)(?:\|[^\]]+)?\]\]', r'\1', text)

        # 去除粗体标记
        text = text.replace("'''", '')

        # <br> -> 换行
        text = text.replace('<br>', '\n').replace('<br/>', '\n').replace('<br />', '\n')

        # 去除 HTML 注释
        text = re.sub(r'<!--.*?-->', '', text, flags=re.DOTALL)

        # 去除首行（通常是标题的重复）
        lines = text.strip().split('\n')
        if lines and (lines[0].startswith('【') or len(lines[0]) < 3):
            lines = lines[1:]
        # 去除尾部残留（%xxx}} 或 }}）
        if lines:
            lines[-1] = re.sub(r'%.*$', '', lines[-1]).rstrip()
        text = '\n'.join(lines).strip()

        # 合并多余空行
        text = re.sub(r'\n\s*\n\s*\n+', '\n\n', text)
        text = text.strip()

        if text and len(text) > 10:
            if title:
                results.append(f'【{title}】\n{text}')
            else:
                results.append(text)

    return '\n\n'.join(results)


def extract_related_quests_from_html(html):
    """从 HTML 提取"相关剧情"段落里的任务/事件名称"""
    if not html:
        return []

    # 找"相关剧情" h2 段落
    idx = html.find('id="相关剧情"')
    if idx < 0:
        # 搜索 URL 编码版本
        idx = html.find('.E7.9B.B8.E5.85.B3.E5.89.A7.E6.83.85')
        if idx < 0:
            return []
        # 找到包含这个 id 的 h2 标签开始
        idx = html.rfind('<h2', 0, idx)
        if idx < 0:
            return []
    else:
        # 找到包含这个 id 的 h2 标签开始
        idx = html.rfind('<h2', 0, idx)
        if idx < 0:
            return []

    # 找到下一个 h2 作为结束位置
    next_h2 = html.find('<h2', idx + 100)
    if next_h2 < 0:
        next_h2 = len(html)

    section = html[idx:next_h2]

    # 提取所有链接 (title="任务名")
    links = re.findall(r'<a[^>]*title="([^"]+)"[^>]*>', section)
    quests = []
    for title in links:
        title = title.strip()
        # 过滤掉非任务链接（角色名、用户页、文件页等）
        if not title or title.startswith('文件:') or title.startswith('User:') or title.startswith('Special:'):
            continue
        # 去掉 wiki 引用括号
        title = re.sub(r'^「|」$', '', title)
        if title and len(title) >= 2:
            quests.append(title)

    # 去重并保持顺序
    seen = set()
    result = []
    for q in quests:
        if q not in seen:
            seen.add(q)
            result.append(q)
    return result


def extract_org_race_from_wikitext(wikitext):
    """从 wikitext 提取所属组织和种族"""
    org = ''
    race = ''
    if not wikitext:
        return org, race

    # 找 {{NPC 模板中的字段
    npc_m = re.search(r'\{\{NPC\s*\n([\s\S]*?)\n\}\}', wikitext)
    if not npc_m:
        npc_m = re.search(r'\{\{NPC\s*\n([\s\S]*?\}\})', wikitext)
        if not npc_m:
            return org, race
    fields = npc_m.group(1)

    org_m = re.search(r'\|\s*所属组织\s*=\s*(.+)', fields)
    if org_m:
        org = org_m.group(1).strip()
    race_m = re.search(r'\|\s*种族\s*=\s*(.+)', fields)
    if race_m:
        race = race_m.group(1).strip()

    return org, race


def load_checkpoint():
    """加载断点"""
    if os.path.exists(CHECKPOINT_FILE):
        with open(CHECKPOINT_FILE, 'r', encoding='utf-8') as f:
            cp = json.load(f)
        print(f'[断点] 已加载，从第 {cp["next_idx"]} 个 NPC 继续 (共 {cp["total"]} 个)')
        return cp
    return None


def save_checkpoint(next_idx, total, results):
    """保存断点"""
    # 先写入已有结果，再保存 checkpoint
    if results:
        with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
    cp = {'next_idx': next_idx, 'total': total}
    with open(CHECKPOINT_FILE, 'w', encoding='utf-8') as f:
        json.dump(cp, f)


def load_existing_results():
    """加载已有结果（从断点恢复）"""
    if os.path.exists(OUTPUT_FILE):
        with open(OUTPUT_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}


def main():
    print('=' * 60)
    print(' NPC wiki 详情全量重建')
    print('=' * 60)
    print(f' SMW 数据: {SMW_FILE}')
    print(f' 输出文件: {OUTPUT_FILE}')
    print(f' 批次大小: {BATCH_SIZE} 个/批')
    print()

    # 读取所有 NPC 名称
    if not os.path.exists(SMW_FILE):
        print(f'[错误] SMW 文件不存在: {SMW_FILE}')
        sys.exit(1)

    with open(SMW_FILE, 'r', encoding='utf-8') as f:
        smw_data = json.load(f)

    all_names = list(smw_data.keys())
    total = len(all_names)
    print(f' 共 {total} 个 NPC')

    # 断点恢复
    cp = load_checkpoint()
    if cp:
        results = load_existing_results()
        start_idx = cp['next_idx']
    else:
        results = {}
        start_idx = 0

    print(f' 从第 {start_idx} 个开始处理 (0-indexed)')
    print()

    # 统计
    success_count = 0
    error_count = 0
    empty_count = 0
    error_types = {}  # 统计各类错误数量
    batch_start_time = time.time()

    try:
        for i in range(start_idx, total):
            name = all_names[i]
            time.sleep(REQUEST_DELAY)

            # 请求 wiki API
            wt, html, err_msg = None, None, ''
            last_err = ''
            for retry in range(MAX_RETRIES):
                wt, html, err_msg = fetch_wiki(name)
                if wt is not None:  # 成功或空页面
                    break
                last_err = err_msg
                if retry < MAX_RETRIES - 1:
                    wait = (retry + 1) * 2
                    if retry == 0:
                        print(f'  [{i+1}/{total}] {name} -> {err_msg}，重试中...')
                    time.sleep(wait)

            if wt is None:
                error_count += 1
                error_types[last_err] = error_types.get(last_err, 0) + 1
                results[name] = {
                    'dialogue': '',
                    'related_quests': [],
                    'origin': '',
                    'region': '',
                    'org_race': '',
                    '_status': f'fetch_error: {last_err}'
                }
                if (i + 1) % BATCH_SIZE == 0 or i == total - 1:
                    print(f'  [{i+1}/{total}] {name} -> 最终失败 ({last_err})')
                continue

            if not wt:
                # 空页面（wiki 上不存在此 NPC 的独立页面）
                empty_count += 1
                results[name] = {
                    'dialogue': '',
                    'related_quests': [],
                    'origin': '',
                    'region': '',
                    'org_race': '',
                    '_status': 'empty_page'
                }
                continue

            # 提取对话
            dialogue = extract_dialogue_from_wikitext(wt)

            # 提取相关任务
            related_quests = extract_related_quests_from_html(html)

            # 提取组织/种族
            org, race = extract_org_race_from_wikitext(wt)

            # 从 SMW 获取国家和地区
            smw = smw_data.get(name, {})
            origin = smw.get('所在国家', '')
            region = smw.get('所在国家', '')

            results[name] = {
                'dialogue': dialogue,
                'related_quests': related_quests,
                'origin': origin,
                'region': region,
                'org_race': f'{org}、{race}' if org and race else (org or race),
                '_status': 'ok'
            }

            success_count += 1

            # 进度显示
            if (i + 1) % BATCH_SIZE == 0 or i == total - 1:
                elapsed = time.time() - batch_start_time
                print(f'  [{i+1}/{total}] 进度: {100*(i+1-start_idx)/(total-start_idx):.1f}%'
                      f' | 成功: {success_count} | 错误: {error_count} | 空页: {empty_count}'
                      f' | 耗时: {elapsed:.1f}s')
                # 保存 checkpoint
                save_checkpoint(i + 1, total, results)
                # 批次间休息
                if i < total - 1:
                    time.sleep(BATCH_DELAY)
                    batch_start_time = time.time()

    except KeyboardInterrupt:
        print(f'\n[中断] 已保存断点，下次从第 {i} 个继续')
        save_checkpoint(i, total, results)
        sys.exit(0)

    # 完成
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    # 清理 checkpoint
    if os.path.exists(CHECKPOINT_FILE):
        os.remove(CHECKPOINT_FILE)

    print()
    print('=' * 60)
    print(' 重建完成!')
    print(f' 总 NPC: {total}')
    print(f' 成功: {success_count}')
    print(f' 错误: {error_count}')
    print(f' 空页: {empty_count}')
    if error_types:
        print(' 错误分类:')
        for err_type, count in sorted(error_types.items(), key=lambda x: -x[1])[:5]:
            print(f'    {count}x {err_type}')
    print(f' 输出: {OUTPUT_FILE}')
    print('=' * 60)
    print()
    print(' 下一步: 运行 _build_npc_full.py 重建 npcs_processed.json 和向量库')


if __name__ == '__main__':
    main()

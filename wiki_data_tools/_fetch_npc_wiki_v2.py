#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
NPC wiki 详情抓取 v2 - 使用 WebFetch 逐批获取 wikitext
绕过 sandbox IP 被 CDN 限流的问题。

策略：每次运行生成下一批 50 个 NPC 的 URL 列表，用户手动逐个 WebFetch，
每次获取后立即解析并保存结果。

用法：
  1. 运行 python _fetch_npc_wiki_v2.py
  2. 脚本输出下一批 URL，用户复制后用 WebFetch 工具获取
  3. 获取结果后，粘贴到 content_data/npc_wiki_fetch_queue.json
  4. 再次运行 python _fetch_npc_wiki_v2.py --process 解析结果
"""
import json
import os
import re
import sys
import time

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONTENT_DIR = os.path.join(SCRIPT_DIR, 'content_data')

INPUT_FILE = os.path.join(CONTENT_DIR, 'npcs_smw_raw.json')
OUTPUT_FILE = os.path.join(CONTENT_DIR, 'npcs_wiki_details.json')
CHECKPOINT_FILE = os.path.join(CONTENT_DIR, 'npc_wiki_cp.json')
QUEUE_FILE = os.path.join(CONTENT_DIR, 'npc_wiki_fetch_queue.json')
BATCH_RESULTS_FILE = os.path.join(CONTENT_DIR, 'npc_wiki_batch_results.json')

MAX_PER_RUN = 50


def log(msg):
    line = f'{time.strftime("%H:%M:%S")} {msg}'
    print(line, flush=True)


def safe_write(filepath, data):
    tmp = filepath + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, filepath)


# ====== Wikitext 解析（复用 v1 的解析逻辑） ======

def _extract_template_block(text, template_name):
    pattern = r'\{\{' + re.escape(template_name)
    match = re.search(pattern, text)
    if not match:
        return None
    pos = match.end()
    depth = 1
    while pos < len(text) and depth > 0:
        if text[pos:pos+2] == '{{':
            depth += 1
            pos += 2
        elif text[pos:pos+2] == '}}':
            depth -= 1
            pos += 2
        else:
            pos += 1
    if depth != 0:
        return None
    inner = text[match.end():pos-2]
    return match.start(), pos, inner


def _parse_template_params(content):
    params = {}
    lines = content.split('\n')
    current_key = None
    current_lines = []
    brace_depth = 0
    for line in lines:
        brace_depth += line.count('{{') - line.count('}}')
        if current_key is None:
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith('|'):
                eq_pos = stripped.find('=')
                if eq_pos > 1:
                    key = stripped[1:eq_pos].strip()
                    value_start = stripped[eq_pos+1:]
                    if brace_depth > 0:
                        current_key = key
                        current_lines = [value_start] if value_start else []
                    else:
                        params[key] = value_start
        else:
            current_lines.append(line)
            if brace_depth <= 0:
                params[current_key] = '\n'.join(current_lines)
                current_key = None
                current_lines = []
    if current_key is not None and current_lines:
        params[current_key] = '\n'.join(current_lines)
    return params


def _clean_wikitext(text):
    text = re.sub(r"'''(.*?)'''", r'\1', text)
    text = re.sub(r"''(.*?)''", r'\1', text)
    text = re.sub(r'<br\s*/?>', '\n', text)
    text = re.sub(r'\[\[([^\]|]+)\|([^\]]+)\]\]', r'\2', text)
    text = re.sub(r'\[\[([^\]]+)\]\]', r'\1', text)
    text = re.sub(r'\{\{NPC剧情[^}]*?\}\}', '', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def _extract_trailing_text(inner):
    lines = inner.split('\n')
    brace_depth = 0
    last_param_end = 0
    for i, line in enumerate(lines):
        brace_depth += line.count('{{') - line.count('}}')
        if brace_depth == 0:
            stripped = line.strip()
            if stripped.startswith('|') and '=' in stripped:
                last_param_end = i + 1
    if last_param_end > 0 and last_param_end < len(lines):
        trailing = '\n'.join(lines[last_param_end:])
        if trailing.strip():
            return trailing
    return ''


def _extract_dialogue_text(block_full):
    result = _extract_template_block(block_full, 'NPC对话')
    if not result:
        return ''
    inner = result[2]
    params = _parse_template_params(inner)
    dialogue_parts = []
    title = params.get('标题', '')
    content = params.get('内容', '')
    if title:
        cleaned_title = _clean_wikitext(title)
        dialogue_parts.append(f'【{cleaned_title}】')
    if content:
        cleaned = _clean_wikitext(content)
        if cleaned.strip():
            dialogue_parts.append(cleaned)
    trailing = _extract_trailing_text(inner)
    if trailing:
        cleaned = _clean_wikitext(trailing)
        if cleaned.strip():
            dialogue_parts.append(cleaned)
    return '\n'.join(dialogue_parts)


def _extract_all_dialogues(npc_content):
    all_texts = []
    params = _parse_template_params(npc_content)
    dialogue_field = params.get('对话内容', '')
    if dialogue_field:
        search_start = 0
        while True:
            result = _extract_template_block(dialogue_field[search_start:], 'NPC对话')
            if not result:
                break
            block_start, block_end, inner = result
            block_full = dialogue_field[search_start + block_start:search_start + block_end]
            text = _extract_dialogue_text(block_full)
            if text.strip():
                all_texts.append(text)
            search_start += block_end
    search_start = 0
    while True:
        result = _extract_template_block(npc_content[search_start:], 'NPC对话')
        if not result:
            break
        block_start, block_end, inner = result
        block_full = npc_content[search_start + block_start:search_start + block_end]
        if dialogue_field and block_full in dialogue_field:
            search_start += block_end
            continue
        text = _extract_dialogue_text(block_full)
        if text.strip():
            all_texts.append(text)
        search_start += block_end
    return '\n\n'.join(all_texts)


def parse_npc_wikitext(wikitext):
    """解析 wikitext 提取对话和基本信息。返回 dict 或 None。"""
    if '{{NPC' not in wikitext:
        return None
    template = _extract_template_block(wikitext, 'NPC')
    if not template:
        return None

    template_inner = template[2]
    dialogue = _extract_all_dialogues(template_inner)

    # 提取基础字段
    origin = _extract_template_field(template_inner, '所属国家')       # 出身
    region = _extract_template_field(template_inner, '所在国家')       # 所在国家
    org_race = _extract_template_field(template_inner, '所属组织')     # 所属组织/种族

    return {
        'dialogue': dialogue,
        'origin': origin,
        'region': region,
        'org_race': org_race,
    }


def _extract_template_field(template_inner, field_name):
    """从 NPC 模板内部提取指定字段的值（单行字段）"""
    pattern = r'\|\s*' + re.escape(field_name) + r'\s*=\s*(.+?)(?:\n|\}\}|\|)'
    match = re.search(pattern, template_inner)
    if not match:
        return ''
    value = match.group(1).strip()
    return _clean_wikitext(value)


# ====== 主流程 ======

def show_help():
    print("""
NPC wiki 抓取工具 v2

用法:
  python _fetch_npc_wiki_v2.py              生成下一批 URL 列表
  python _fetch_npc_wiki_v2.py --process     解析已保存的 wikitext 结果
  python _fetch_npc_wiki_v2.py --status      查看当前进度
  python _fetch_npc_wiki_v2.py --add NAME    手动添加单个 NPC 的 wikitext（需要手动粘贴）
""")


def cmd_status():
    cp = {}
    if os.path.exists(CHECKPOINT_FILE):
        with open(CHECKPOINT_FILE, 'r', encoding='utf-8') as f:
            cp = json.load(f)
    existing = {}
    if os.path.exists(OUTPUT_FILE):
        with open(OUTPUT_FILE, 'r', encoding='utf-8') as f:
            existing = json.load(f)
    ok_count = sum(1 for v in existing.values() if v.get('_status') == 'ok')
    has_dialogue = sum(1 for v in existing.values() if len(v.get('dialogue', '')) > 10)
    has_quests = sum(1 for v in existing.values() if len(v.get('related_quests', [])) > 0)
    print(f'进度: {cp.get("idx", 0)}/2422')
    print(f'结果总数: {len(existing)}')
    print(f'成功(ok): {ok_count}')
    print(f'有对话: {has_dialogue}')
    print(f'有剧情: {has_quests}')


def cmd_generate():
    """生成下一批要抓取的 URL 列表"""
    with open(INPUT_FILE, 'r', encoding='utf-8') as f:
        npcs_data = json.load(f)
    npc_items = list(npcs_data.items())
    total = len(npc_items)

    cp = {}
    if os.path.exists(CHECKPOINT_FILE):
        with open(CHECKPOINT_FILE, 'r', encoding='utf-8') as f:
            cp = json.load(f)

    start_idx = cp.get('idx', 0)
    if start_idx >= total:
        print('所有 NPC 已处理完毕！')
        return

    end_idx = min(start_idx + MAX_PER_RUN, total)
    urls = []
    for i in range(start_idx, end_idx):
        name, info = npc_items[i]
        encoded = urllib.parse.quote(name)
        url = f'https://wiki.biligame.com/ys/api.php?action=parse&page={encoded}&prop=wikitext&format=json'
        urls.append({'index': i, 'name': name, 'url': url})

    queue_data = {
        'start_idx': start_idx,
        'end_idx': end_idx,
        'urls': urls,
    }
    safe_write(QUEUE_FILE, queue_data)
    print(f'已生成 {len(urls)} 个 URL（idx {start_idx}-{end_idx-1}）')
    print(f'文件: {QUEUE_FILE}')
    print()
    for i, u in enumerate(urls[:5]):
        print(f'  [{i+1}] {u["name"]}')
    if len(urls) > 5:
        print(f'  ... 共 {len(urls)} 条')


def cmd_process():
    """解析已保存的 batch_results 文件"""
    if not os.path.exists(BATCH_RESULTS_FILE):
        print(f'错误: 找不到 {BATCH_RESULTS_FILE}')
        print('请先使用 WebFetch 工具批量获取 wikitext，结果保存到此文件')
        print('格式: {"results": [{"name": "NPC名", "wikitext": "{{NPC...}}"}, ...]}')
        sys.exit(1)

    with open(BATCH_RESULTS_FILE, 'r', encoding='utf-8') as f:
        batch = json.load(f)

    results = batch.get('results', [])
    if not results:
        print('batch_results 为空')
        sys.exit(1)

    # 加载已有结果
    existing = {}
    if os.path.exists(OUTPUT_FILE):
        with open(OUTPUT_FILE, 'r', encoding='utf-8') as f:
            existing = json.load(f)

    # 加载 queue 获取索引信息
    queue = {}
    if os.path.exists(QUEUE_FILE):
        with open(QUEUE_FILE, 'r', encoding='utf-8') as f:
            queue = json.load(f)

    processed = 0
    for item in results:
        name = item['name']
        wikitext = item.get('wikitext', '')

        if not wikitext:
            existing[name] = {'dialogue': '', 'related_quests': [], 'origin': '', 'region': '', 'org_race': '', '_status': 'empty'}
            processed += 1
            continue

        if '{{NPC' not in wikitext:
            existing[name] = {'dialogue': '', 'related_quests': [], 'origin': '', 'region': '', 'org_race': '', '_status': 'no_template'}
            processed += 1
            continue

        parsed = parse_npc_wikitext(wikitext)
        if parsed is None:
            existing[name] = {'dialogue': '', 'related_quests': [], 'origin': '', 'region': '', 'org_race': '', '_status': 'template_parse_fail'}
            processed += 1
            continue

        existing[name] = {
            'dialogue': parsed['dialogue'],
            'related_quests': [],  # 相关剧情需要 HTML，这里暂时为空，后续补充
            'origin': parsed.get('origin', ''),
            'region': parsed.get('region', ''),
            'org_race': parsed.get('org_race', ''),
            '_status': 'ok',
        }
        processed += 1
        dlen = len(parsed['dialogue'])
        log(f'  {name}: ok, dialogue={dlen}字')

    # 保存
    safe_write(OUTPUT_FILE, existing)

    # 更新 checkpoint
    cp = {}
    if os.path.exists(CHECKPOINT_FILE):
        with open(CHECKPOINT_FILE, 'r', encoding='utf-8') as f:
            cp = json.load(f)
    cp['idx'] = max(cp.get('idx', 0), queue.get('end_idx', 0))
    cp['done'] = cp.get('done', 0) + processed
    safe_write(CHECKPOINT_FILE, cp)

    # 清理 batch_results
    if os.path.exists(BATCH_RESULTS_FILE):
        os.remove(BATCH_RESULTS_FILE)

    log(f'处理完成: {processed} 条, 总计 {len(existing)}')


def main():
    import urllib.parse  # 用于 URL 编码
    if len(sys.argv) > 1:
        cmd = sys.argv[1]
        if cmd == '--process':
            cmd_process()
        elif cmd == '--status':
            cmd_status()
        elif cmd == '--help':
            show_help()
        else:
            print(f'未知命令: {cmd}')
            show_help()
    else:
        cmd_generate()


if __name__ == '__main__':
    main()

#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""解析 WebFetch 批量获取的 wiki wikitext，提取对话/origin/region/org_race 并保存"""
import json
import os
import re
import sys
import time

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONTENT_DIR = os.path.join(SCRIPT_DIR, 'content_data')
OUTPUT_FILE = os.path.join(CONTENT_DIR, 'npcs_wiki_details.json')
CHECKPOINT_FILE = os.path.join(CONTENT_DIR, 'npc_wiki_cp.json')


def log(msg):
    print(f'{time.strftime("%H:%M:%S")} {msg}', flush=True)


def safe_write(filepath, data):
    tmp = filepath + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, filepath)


def _clean_wikitext(text):
    text = re.sub(r"'''(.*?)'''", r'\1', text)
    text = re.sub(r"''(.*?)''", r'\1', text)
    text = re.sub(r'<br\s*/?>', '\n', text)
    text = re.sub(r'\[\[([^\]|]+)\|([^\]]+)\]\]', r'\2', text)
    text = re.sub(r'\[\[([^\]]+)\]\]', r'\1', text)
    text = re.sub(r'\{\{NPC剧情[^}]*?\}\}', '', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def _extract_template_field(template_inner, field_name):
    """从 NPC 模板内部提取指定字段的值"""
    pattern = r'\|\s*' + re.escape(field_name) + r'\s*=\s*(.+?)(?:\n|\}\}|\|)'
    match = re.search(pattern, template_inner)
    if not match:
        return ''
    return _clean_wikitext(match.group(1).strip())


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
    parts = []
    title = params.get('标题', '')
    content = params.get('内容', '')
    if title:
        parts.append(f'【{_clean_wikitext(title)}】')
    if content:
        c = _clean_wikitext(content)
        if c.strip():
            parts.append(c)
    trailing = _extract_trailing_text(inner)
    if trailing:
        t = _clean_wikitext(trailing)
        if t.strip():
            parts.append(t)
    return '\n'.join(parts)


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
            block_start, block_end, _ = result
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
        block_start, block_end, _ = result
        block_full = npc_content[search_start + block_start:search_start + block_end]
        if dialogue_field and block_full in dialogue_field:
            search_start += block_end
            continue
        text = _extract_dialogue_text(block_full)
        if text.strip():
            all_texts.append(text)
        search_start += block_end
    return '\n\n'.join(all_texts)


def parse_one_wikitext(wikitext):
    """解析单个 NPC 的 wikitext，返回 dict"""
    if '{{NPC' not in wikitext:
        return None
    template = _extract_template_block(wikitext, 'NPC')
    if not template:
        return None
    inner = template[2]
    return {
        'dialogue': _extract_all_dialogues(inner),
        'origin': _extract_template_field(inner, '所属国家'),
        'region': _extract_template_field(inner, '所在国家'),
        'org_race': _extract_template_field(inner, '所属组织'),
        '_status': 'ok',
    }


def parse_batch(batch_json_path):
    """解析 WebFetch 批量结果，用正则提取每个 NPC 的 wikitext"""
    with open(batch_json_path, 'r', encoding='utf-8-sig') as f:
        raw_text = f.read()

    # 用正则提取每个 page：匹配 "PAGEID":{"pageid":N,"ns":0,"title":"TITLE","revisions":[{"slots":{"main":{..."*":"WIKITEXT"}}}}]}
    # 采用两步策略：先找 title，再找最近的 "*":" 来匹配 wikitext
    page_entries = {}
    title_pattern = re.compile(r'"title":"((?:[^"\\]|\\.)*)"')
    wikitext_pattern = re.compile(r'"\*":"((?:[^"\\]|\\.)*)"')

    # 更稳健的策略：匹配每个 page 块
    # 格式: "数字":{"pageid":...,"title":"名称",..."*":"内容"}}
    # 找到所有 page id + title 对，以及它们对应的 wikitext
    for m in re.finditer(r'"(\d+)":\{"pageid":\d+,"ns":0,"title":"((?:[^"\\]|\\.)*)"', raw_text):
        pageid = m.group(1)
        title = m.group(2).replace('\\"', '"')
        # 解码 Unicode 转义序列（如 \u6ed1\u5934\u9b3c → 滑头鬼）
        title = re.sub(r'\\u([0-9a-fA-F]{4})', lambda x: chr(int(x.group(1), 16)), title)
        # 在这个 page 块中找 "*":" 后面的 wikitext
        rest_start = m.end()
        # 找下一个 page 块或结束
        next_page = re.search(r'"\d+":\{"pageid":', raw_text[rest_start:])
        block_end = rest_start + next_page.start() if next_page else len(raw_text)
        block = raw_text[rest_start:block_end]

        wt_match = re.search(r'"\*":"((?:[^"\\]|\\[\\"/bfnrt]|\\u[0-9a-fA-F]{4})*)"', block)
        wikitext_raw = ''
        if wt_match:
            wikitext_raw = wt_match.group(1)
            # 解析 JSON 转义
            wikitext_raw = wikitext_raw.replace('\\"', '"').replace('\\\\', '\\')
            wikitext_raw = wikitext_raw.replace('\\n', '\n').replace('\\t', '\t')
            wikitext_raw = wikitext_raw.replace('\\r', '\r')
            # 解码 Unicode 转义序列
            wikitext_raw = re.sub(r'\\u([0-9a-fA-F]{4})', lambda x: chr(int(x.group(1), 16)), wikitext_raw)

        page_entries[title] = wikitext_raw

    log(f'从原始文本提取到 {len(page_entries)} 个 page')

    if not page_entries:
        log('错误: 未提取到任何 page 数据')
        return

    # 加载已有结果
    existing = {}
    if os.path.exists(OUTPUT_FILE):
        with open(OUTPUT_FILE, 'r', encoding='utf-8') as f:
            existing = json.load(f)

    processed = 0
    for title, wikitext in page_entries.items():
        # 跳过已有 ok 数据的
        if title in existing and existing[title].get('_status') == 'ok':
            continue

        if not wikitext:
            existing[title] = {'dialogue': '', 'related_quests': [], 'origin': '', 'region': '', 'org_race': '', '_status': 'empty'}
            processed += 1
            continue

        parsed = parse_one_wikitext(wikitext)

        if parsed is None:
            existing[title] = {'dialogue': '', 'related_quests': [], 'origin': '', 'region': '', 'org_race': '', '_status': 'no_template'}
        else:
            existing[title] = {
                'dialogue': parsed['dialogue'],
                'related_quests': [],
                'origin': parsed['origin'],
                'region': parsed['region'],
                'org_race': parsed['org_race'],
                '_status': 'ok',
            }
        processed += 1
        dlen = len(parsed['dialogue']) if parsed else 0
        log(f'  {title}: {"ok" if parsed else "fail"}, dialogue={dlen}字')

    # 保存
    safe_write(OUTPUT_FILE, existing)

    # 更新 checkpoint
    with open(os.path.join(CONTENT_DIR, 'npcs_smw_raw.json'), 'r', encoding='utf-8') as f:
        smw_data = json.load(f)
    npc_items = list(smw_data.items())

    max_idx = 0
    for i, (name, _) in enumerate(npc_items):
        if name in page_entries:
            max_idx = max(max_idx, i + 1)

    cp = {}
    if os.path.exists(CHECKPOINT_FILE):
        with open(CHECKPOINT_FILE, 'r', encoding='utf-8') as f:
            cp = json.load(f)
    cp['idx'] = max(cp.get('idx', 0), max_idx)
    cp['done'] = cp.get('done', 0) + processed
    with open(CHECKPOINT_FILE, 'w', encoding='utf-8') as f:
        json.dump(cp, f)

    ok_count = sum(1 for v in existing.values() if v.get('_status') == 'ok')
    log(f'处理完成: 本批 {processed} 条, 总计 {len(existing)} 条, 成功 {ok_count}')

    # 删除临时文件
    os.remove(batch_json_path)


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print('用法: python _parse_batch.py <batch_json_path>')
        sys.exit(1)
    parse_batch(sys.argv[1])

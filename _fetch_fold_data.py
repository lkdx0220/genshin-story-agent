# -*- coding: utf-8 -*-
"""批量抓取wiki页面的折叠内容，使用MediaWiki API"""
import os
import json
import re
import time
import requests
from collections import defaultdict
from urllib.parse import quote

CONTENT_DIR = r'C:\Users\24701\Desktop\原神剧情\CASE-原神剧情助手-修改用\content_data'
API_URL = "https://wiki.biligame.com/ys/api.php"
OUTPUT_FILE = os.path.join(os.path.dirname(CONTENT_DIR), '_fold_wiki_data.json')

session = requests.Session()
session.headers.update({
    'User-Agent': 'GenshinStoryBot/1.0 (research project; contact@example.com)',
})

def fetch_wikitext(title):
    """通过API获取页面的原始wikitext"""
    params = {
        'action': 'parse',
        'page': title,
        'prop': 'wikitext',
        'format': 'json',
        'formatversion': '2',
    }
    try:
        resp = session.get(API_URL, params=params, timeout=30)
        data = resp.json()
        if 'parse' in data and 'wikitext' in data['parse']:
            return data['parse']['wikitext']
        elif 'error' in data:
            print(f"  [API错误] {title}: {data['error'].get('info', '未知')}")
            return None
    except Exception as e:
        print(f"  [请求失败] {title}: {e}")
        return None

def extract_folds(wikitext):
    """从wikitext中提取所有折叠内容"""
    results = []

    # 找到所有 {{折叠|...}} 或 {{Fold|...}} 模板
    # 匹配嵌套的 {{...}} 块
    pattern = r'\{\{折叠\s*\|'
    for m in re.finditer(pattern, wikitext, re.IGNORECASE):
        start = m.start()
        depth = 0
        pos = start
        while pos < len(wikitext):
            if wikitext[pos:pos+2] == '{{':
                depth += 1
                pos += 1
            elif wikitext[pos:pos+2] == '}}':
                depth -= 1
                if depth == 0:
                    block = wikitext[start:pos+2]
                    break
            pos += 1
        else:
            # 未闭合
            block = wikitext[start:]

        results.append(('fold', block))

    # 也查找过场动画相关的独立折叠标记
    # 有些页面用简单的"过场动画\n折叠"格式
    return results

def extract_cutscene_text(wikitext, page_title):
    """专门提取过场动画cutscene文本"""
    results = []

    # 模式1: {{折叠|过场动画|...内容...}}
    pattern1 = re.compile(r'\{\{折叠\s*\|\s*过场动画\s*\|\s*(.+?)\}\}(?:\s*\}\})?', re.DOTALL)
    for m in pattern1.finditer(wikitext):
        content = m.group(1).strip()
        results.append({
            'type': 'cutscene',
            'title': '过场动画',
            'content': content,
            'format': '{{折叠|过场动画|...}}',
        })

    # 模式2: {{折叠|折叠=是|标题=过场动画|内容=...}}
    pattern2 = re.compile(
        r'\{\{折叠\s*\|\s*折叠\s*=\s*是\s*\|\s*标题\s*=\s*过场动画\s*\|\s*内容\s*=\s*(.+?)\}\}',
        re.DOTALL
    )
    for m in pattern2.finditer(wikitext):
        content = m.group(1).strip()
        results.append({
            'type': 'cutscene',
            'title': '过场动画',
            'content': content,
            'format': '{{折叠|折叠=是|标题=过场动画|内容=...}}',
        })

    return results

def extract_extra_dialogue(wikitext):
    """提取额外对话折叠"""
    results = []

    # {{折叠|折叠=是|标题=额外对话|内容=...}}
    # {{折叠|折叠=是|标题=额外对话（某角色）|内容=...}}
    pattern = re.compile(
        r'\{\{折叠\s*\|\s*折叠\s*=\s*是\s*\|\s*标题\s*=\s*额外对话[^|]*\s*\|\s*内容\s*=\s*(.+?)\}\}',
        re.DOTALL
    )
    for m in pattern.finditer(wikitext):
        content = m.group(1).strip()
        # 提取角色名
        title_match = re.search(r'额外对话[（(]([^）)]+)[）)]', m.group(0))
        char = title_match.group(1) if title_match else '未知'
        results.append({
            'type': 'extra_dialogue',
            'title': f'额外对话（{char}）',
            'character': char,
            'content': content,
        })

    return results

def clean_wikitext(text):
    """清理wiki标记，提取纯文本"""
    # 移除 ''' 粗体标记
    text = re.sub(r"'''", '', text)
    # 移除 [[link|text]] -> text, [[link]] -> link
    text = re.sub(r'\[\[([^\]|]+)\|([^\]]+)\]\]', r'\2', text)
    text = re.sub(r'\[\[([^\]]+)\]\]', r'\1', text)
    # 移除 HTML 标签
    text = re.sub(r'<[^>]+>', '', text)
    # 移除 {{颜色|...}}
    text = re.sub(r'\{\{颜色\|[^}]+\}\}', '', text)
    # 移除 {{图标|...}}
    text = re.sub(r'\{\{图标\|[^}]+\}\}', '', text)
    # 合并多余空行
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()

# ==== 第一步：收集所有需要抓取的页面 ====
print("=" * 60)
print("  第一步：收集需要抓取的页面")
print("=" * 60)

# 从详细报告中读取
with open(os.path.join(os.path.dirname(CONTENT_DIR), '_fold_detailed_report.json'), 'r', encoding='utf-8') as f:
    report = json.load(f)

# 收集所有有问题的页面
pages_to_fetch = set()

# 过场动画缺失页面
for item in report['missing_cutscenes']:
    pages_to_fetch.add(item['page_title'])

# 从第一个分析报告中也获取
with open(os.path.join(os.path.dirname(CONTENT_DIR), '_fold_analysis_report.json'), 'r', encoding='utf-8') as f:
    report2 = json.load(f)
for item in report2.get('issues', []):
    pages_to_fetch.add(item['title'])

# 额外：查找所有 quest JSON 中被 :: 或 : 占位的过场动画
print("\n扫描JSON中过场动画后的占位符...")
cutscene_pages = set()
for fname in sorted(os.listdir(CONTENT_DIR)):
    if not fname.startswith('quests_') or not fname.endswith('.json'):
        continue
    if fname == 'quests_processed.json':
        continue
    with open(os.path.join(CONTENT_DIR, fname), 'r', encoding='utf-8') as f:
        quests = json.load(f)
    for q in quests:
        title = q.get('title', '')
        text = q.get('text', '')
        if '过场动画' in text:
            # 检查过场动画标记后是否有实质内容
            last_pos = text.rfind('过场动画')
            after = text[last_pos+4:].strip()
            if len(after) < 200:
                cutscene_pages.add(title)
                pages_to_fetch.add(title)

print(f"  过场动画页(疑似占位): {len(cutscene_pages)}")

# 去重
pages_to_fetch = sorted(pages_to_fetch)
print(f"  总计需抓取页面: {len(pages_to_fetch)} 个")

# ==== 第二步：批量抓取wiki页面 ====
print("\n" + "=" * 60)
print("  第二步：抓取wiki页面")
print("=" * 60)

wiki_data = {}
failed_pages = []

for i, title in enumerate(pages_to_fetch):
    if (i + 1) % 10 == 0:
        print(f"  进度: {i+1}/{len(pages_to_fetch)}")
        time.sleep(0.5)

    wikitext = fetch_wikitext(title)
    if not wikitext:
        failed_pages.append(title)
        continue

    # 提取折叠内容
    cutscenes = extract_cutscene_text(wikitext, title)
    dialogues = extract_extra_dialogue(wikitext)

    wiki_data[title] = {
        'cutscenes': cutscenes,
        'extra_dialogues': dialogues,
        'has_cutscene_content': len(cutscenes) > 0,
        'has_dialogue_content': len(dialogues) > 0,
    }

    # 简要输出
    if cutscenes or dialogues:
        parts = []
        if cutscenes:
            parts.append(f"过场动画x{len(cutscenes)}")
        if dialogues:
            parts.append(f"额外对话x{len(dialogues)}")
        print(f"  [{i+1}/{len(pages_to_fetch)}] {title}: 找到 {', '.join(parts)}")
    else:
        # 没找到折叠内容但有问题的页面，保存原始wikitext供检查
        wiki_data[title]['wikitext_preview'] = wikitext[:500]

print(f"\n  抓取完成: {len(wiki_data)} 成功, {len(failed_pages)} 失败")

# ==== 第三步：保存结果 ====
print("\n" + "=" * 60)
print("  第三步：保存结果")
print("=" * 60)

# 按类型统计
cutscene_found = sum(1 for d in wiki_data.values() if d['has_cutscene_content'])
dialogue_found = sum(1 for d in wiki_data.values() if d['has_dialogue_content'])
any_found = sum(1 for d in wiki_data.values() if d['has_cutscene_content'] or d['has_dialogue_content'])

output = {
    'summary': {
        'total_pages_fetched': len(pages_to_fetch),
        'pages_succeeded': len(wiki_data),
        'pages_failed': len(failed_pages),
        'pages_with_cutscenes': cutscene_found,
        'pages_with_dialogues': dialogue_found,
        'pages_with_any_fold': any_found,
        'failed_pages': failed_pages,
    },
    'wiki_data': {k: {kk: vv for kk, vv in v.items() if kk != 'wikitext_preview'} for k, v in wiki_data.items()},
}

with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
    json.dump(output, f, ensure_ascii=False, indent=2)

print(f"\n  结果已保存: {OUTPUT_FILE}")
print(f"\n===== 汇总 =====")
print(f"  抓取页面: {len(pages_to_fetch)}")
print(f"  成功: {len(wiki_data)}")
print(f"  失败: {len(failed_pages)}")
print(f"  含过场动画折叠: {cutscene_found} 页")
print(f"  含额外对话折叠: {dialogue_found} 页")
print(f"  含任意折叠内容: {any_found} 页")

# 展示关键发现
if cutscene_found > 0:
    print(f"\n--- 过场动画折叠内容 ---")
    for title, data in wiki_data.items():
        for cs in data.get('cutscenes', []):
            content = cs['content']
            # 显示前200字符
            preview = content[:200].replace('\n', '\\n')
            print(f"  [{title}] {cs['title']}")
            print(f"    {preview}...")
            if len(content) > 200:
                print(f"    (全文 {len(content)} 字)")

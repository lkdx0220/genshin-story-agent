# -*- coding: utf-8 -*-
"""修正后的wiki折叠内容批量提取，使用正确正则"""
import os
import json
import re
import time
import requests

CONTENT_DIR = r'C:\Users\24701\Desktop\原神剧情\CASE-原神剧情助手-修改用\content_data'
API_URL = "https://wiki.biligame.com/ys/api.php"
OUTPUT_FILE = os.path.join(os.path.dirname(CONTENT_DIR), '_fold_wiki_data_v2.json')

session = requests.Session()
session.headers.update({'User-Agent': 'GenshinStoryBot/1.0'})

def fetch_wikitext(title):
    """通过API获取页面的原始wikitext"""
    params = {
        'action': 'parse', 'page': title,
        'prop': 'wikitext', 'format': 'json', 'formatversion': '2',
    }
    try:
        resp = session.get(API_URL, params=params, timeout=30)
        data = resp.json()
        if 'parse' in data and 'wikitext' in data['parse']:
            return data['parse']['wikitext']
    except Exception as e:
        print(f"  [失败] {title}: {e}")
    return None

def extract_all_folds(wikitext):
    """从wikitext中提取所有折叠内容 (格式: {{折叠|标题=xxx|内容=xxx|折叠=是/否}})"""
    folds = []

    # 匹配所有 {{折叠|...}} - 处理嵌套
    pattern = re.compile(r'\{\{折叠\s*\|', re.IGNORECASE)
    for m in pattern.finditer(wikitext):
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
            block = wikitext[start:]

        folds.append(block)

    results = []

    for fold in folds:
        # 提取标题: |标题=xxx| 或 |标题 = xxx|
        title_m = re.search(r'\|\s*标题\s*=\s*([^|{}]+?)\s*(?:\||})', fold)
        if not title_m:
            # 简单格式: {{折叠|标题|内容}}
            simple_m = re.search(r'\{\{折叠\s*\|\s*([^|=}]+?)\s*\|\s*(.+?)\}\}$', fold, re.DOTALL)
            if simple_m:
                title = simple_m.group(1).strip()
                content = simple_m.group(2).strip()
            else:
                continue
        else:
            title = title_m.group(1).strip()
            # 提取内容: |内容=...|
            content_m = re.search(r'\|\s*内容\s*=\s*(.+?)(?:\|\s*折叠\s*=|}}\s*$)', fold, re.DOTALL)
            if not content_m:
                # 内容可能是到 }} 之前
                content_m = re.search(r'\|\s*内容\s*=\s*(.+?)\}\}$', fold, re.DOTALL)
            if content_m:
                content = content_m.group(1).strip()
            else:
                continue

        # 清理HTML标签
        content = re.sub(r'<br\s*/?>', '\n', content)
        content = re.sub(r'<[^>]+>', '', content)

        # 去除嵌套的 {{...}} 模板（但要保留内容）
        # 简单地移除 {{图标|...}} {{颜色|...}} 等小模板
        content = re.sub(r'\{\{图标\|[^}]+\}\}', '', content)
        content = re.sub(r'\{\{颜色\|[^}]+\}\}', '', content)
        content = re.sub(r'\{\{黑幕\|([^}]+)\}\}', r'\1', content)
        content = re.sub(r'\{\{任务描述\|([^}]+)\}\}', r'[\1]', content)
        content = re.sub(r'\{\{[^}]+\}\}', '', content)

        # 移除 '''粗体''' 标记
        content = re.sub(r"'''", '', content)

        # 判断类型
        fold_type = 'other'
        if '过场动画' in title:
            fold_type = 'cutscene'
        elif '额外对话' in title:
            fold_type = 'extra_dialogue'

        results.append({
            'title': title,
            'type': fold_type,
            'content': content,
            'content_len': len(content),
        })

    return results

def clean_wikitext(text):
    """清理wiki文本"""
    text = re.sub(r'<br\s*/?>', '\n', text)
    text = re.sub(r'<[^>]+>', '', text)
    text = re.sub(r"'''", '', text)
    text = re.sub(r'\{\{图标\|[^}]+\}\}', '', text)
    text = re.sub(r'\{\{颜色\|[^}]+\}\}', '', text)
    text = re.sub(r'\{\{黑幕\|([^}]+)\}\}', r'\1', text)
    return text.strip()

# ==== 收集页面 ====
print("=" * 60)
print("  收集需要抓取的页面")
print("=" * 60)

all_quest_pages = []
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
        category = q.get('category', '')
        if title and text:
            all_quest_pages.append((title, category, text))

# 筛选：文本中有'折叠'或'过场动画'关键词但内容疑似不完整的
pages_to_check = []
for title, category, text in all_quest_pages:
    has_fold_marker = '折叠' in text
    has_cutscene = '过场动画' in text
    if not has_fold_marker and not has_cutscene:
        continue

    # 检查是否有 ::  占位符（表示过场动画被截断）
    has_placeholder = bool(re.search(r'(?<!\w)::(?!\w)', text[-500:]))

    if has_placeholder or (has_cutscene and len(text.split('过场动画')[-1].strip()) < 200):
        pages_to_check.append((title, category))

# 去重
seen = set()
pages_to_fetch = []
for title, category in pages_to_check:
    if title not in seen:
        seen.add(title)
        pages_to_fetch.append((title, category))

print(f"  需抓取页面: {len(pages_to_fetch)}")

# ==== 抓取 ====
print("\n" + "=" * 60)
print("  抓取wiki页面")
print("=" * 60)

wiki_data = {}
cutscene_count = 0
dialogue_count = 0

for i, (title, category) in enumerate(pages_to_fetch):
    if (i + 1) % 20 == 0:
        print(f"  进度: {i+1}/{len(pages_to_fetch)}")
        time.sleep(0.3)

    wikitext = fetch_wikitext(title)
    if not wikitext:
        continue

    folds = extract_all_folds(wikitext)
    cutscenes = [f for f in folds if f['type'] == 'cutscene']
    dialogues = [f for f in folds if f['type'] == 'extra_dialogue']

    if cutscenes:
        cutscene_count += 1
    if dialogues:
        dialogue_count += 1

    wiki_data[title] = {
        'category': category,
        'cutscenes': cutscenes,
        'extra_dialogues': dialogues,
        'total_folds': len(folds),
    }

    if cutscenes or dialogues:
        parts = []
        if cutscenes:
            parts.append(f"过场x{len(cutscenes)}")
        if dialogues:
            parts.append(f"对话x{len(dialogues)}")
        print(f"  [{i+1}] {title}: {', '.join(parts)}")

# ==== 保存 ====
output = {
    'summary': {
        'pages_fetched': len(wiki_data),
        'pages_with_cutscenes': cutscene_count,
        'pages_with_dialogues': dialogue_count,
        'total_fold_blocks_found': sum(len(d['cutscenes']) + len(d['extra_dialogues']) for d in wiki_data.values()),
    },
    'details': wiki_data,
}

with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
    json.dump(output, f, ensure_ascii=False, indent=2)

# ==== 展示 ====
print("\n" + "=" * 60)
print("  结果汇总")
print("=" * 60)
print(f"  抓取页面: {len(wiki_data)}")
print(f"  含过场动画: {cutscene_count} 页")
print(f"  含额外对话: {dialogue_count} 页")

if cutscene_count > 0:
    print(f"\n--- 过场动画详情 ---")
    for title, data in wiki_data.items():
        for cs in data.get('cutscenes', []):
            preview = cs['content'][:300].replace('\n', ' ')[:200]
            print(f"  [{title}] ({cs['content_len']}字): {preview}...")

print(f"\n输出: {OUTPUT_FILE}")

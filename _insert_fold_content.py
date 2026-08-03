# -*- coding: utf-8 -*-
"""将wiki抓取的折叠内容插入到JSON对应占位符位置"""
import os
import json
import re
import shutil

CONTENT_DIR = r'C:\Users\24701\Desktop\原神剧情\CASE-原神剧情助手-修改用\content_data'
WIKI_DATA_FILE = os.path.join(os.path.dirname(CONTENT_DIR), '_fold_wiki_data_v2.json')

# 加载wiki数据
with open(WIKI_DATA_FILE, 'r', encoding='utf-8') as f:
    wiki_data = json.load(f)['details']

# 统计
stats = {'cutscene_inserted': 0, 'dialogue_inserted': 0, 'pages_modified': 0}

def insert_content(text, fold_content, title):
    """将折叠内容插入到text中的占位符位置"""
    if not fold_content:
        return text, False

    # 策略：找到最后一个 :: 占位符并替换
    # 或者找到过场动画标记后的位置

    # 查找模式: :: (单独作为对话分隔符，后跟换行或文本)
    # 如果 text 以 "::\n" 结尾或附近有这样的模式，替换之
    
    # 先找末尾的 ::
    placeholder_pattern = r'(?<!\w)::(?!\w)'
    matches = list(re.finditer(placeholder_pattern, text))

    if matches:
        # 替换第一个 :: 占位符
        match = matches[0]
        new_text = text[:match.start()] + fold_content + text[match.end():]
        return new_text, True

    # 如果没有 :: 占位符，尝试在过场动画标记后插入
    cutscene_idx = text.rfind('过场动画')
    if cutscene_idx >= 0:
        after = text[cutscene_idx + 4:].lstrip()
        if len(after) < 20:
            # 过场动画后基本没内容，直接追加
            new_text = text[:cutscene_idx] + '过场动画\n\n' + fold_content + '\n' + after
            return new_text, True

    # 兜底：在文本末尾追加，标记为【过场动画】
    new_text = text + '\n\n【过场动画】\n' + fold_content
    return new_text, True

# 遍历所有JSON文件
for fname in sorted(os.listdir(CONTENT_DIR)):
    if not fname.startswith('quests_') or not fname.endswith('.json'):
        continue
    if fname == 'quests_processed.json':
        continue

    filepath = os.path.join(CONTENT_DIR, fname)
    with open(filepath, 'r', encoding='utf-8') as f:
        quests = json.load(f)

    modified = False

    for q in quests:
        title = q.get('title', '')
        if title not in wiki_data:
            continue

        wdata = wiki_data[title]
        text = q.get('text', '')
        original_text = text

        # 插入过场动画
        for cs in wdata.get('cutscenes', []):
            content = cs['content'].strip()
            if not content:
                continue

            # 清理内容格式
            # 将 *角色：对话 格式保留
            text, inserted = insert_content(text, content, title)
            if inserted:
                stats['cutscene_inserted'] += 1
                print(f"  [过场动画] {title}: {cs['title']} ({len(content)}字) -> 已插入")

        # 插入额外对话
        for dl in wdata.get('extra_dialogues', []):
            content = dl['content'].strip()
            if not content:
                continue
            if len(content) < 5:
                continue

            # 额外对话追加到文本末尾
            # 查找是否已有这个额外对话（避免重复）
            first_line = content.split('\n')[0].strip()[:30]
            if first_line and first_line in text:
                continue

            marker = f'\n\n【额外对话·{dl["title"]}】\n{content}'
            text += marker
            stats['dialogue_inserted'] += 1

        if text != original_text:
            q['text'] = text
            modified = True
            stats['pages_modified'] += 1

    if modified:
        # 备份原文件
        # backup_path = filepath + '.bak_fold'
        # shutil.copy(filepath, backup_path)
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(quests, f, ensure_ascii=False, indent=2)
        print(f"\n[文件] {fname}: 已更新")

print("\n" + "=" * 60)
print("  插入完成")
print("=" * 60)
print(f"  过场动画插入: {stats['cutscene_inserted']} 条")
print(f"  额外对话插入: {stats['dialogue_inserted']} 条")
print(f"  修改页面数: {stats['pages_modified']}")

# -*- coding: utf-8 -*-
"""精准插入wiki折叠内容：只替换缺失cutscene的 :: 分隔符，额外对话追加到末尾"""
import os
import json
import re
import shutil

CONTENT_DIR = r'C:\Users\24701\Desktop\原神剧情\CASE-原神剧情助手-修改用\content_data'
WIKI_DATA_FILE = os.path.join(os.path.dirname(CONTENT_DIR), '_fold_wiki_data_v2.json')

with open(WIKI_DATA_FILE, 'r', encoding='utf-8') as f:
    wiki_data = json.load(f)['details']

stats = {'cs_inserted': 0, 'cs_skipped': 0, 'dl_inserted': 0, 'pages_modified': 0}

# 为每个缺失的过场动画手动指定替换的 :: 定位上下文
# (title, cutscene_index) -> (before_context, after_context)
# 用首行+末行做指纹匹配
CUTSCENE_CONTEXTS = {
    ('此心安处', 2): {
        'before': '*诗羽：哎，你们看，云先生上台了！',
        'after': '*云堇：怎么样，我唱得还不错吧？',
    },
    ('惊遇！等待众人的将是…？', 1): {
        'before': '让妈妈…再看一眼',
        'after': '*班尼特：妈妈…',
    },
    ('惊遇！等待众人的将是…？', 2): {
        'before': '*玛薇卡：希望你们喜欢这个惊喜',
        'after': '*玛薇卡：有没有人愿意登上台来',
    },
    ('幽潭心', 1): {
        'before': '这代表誓约与爱的礼物，就现在赠予你吧',
        'after': '*老芬奇：好啦，好啦',
    },
    ('我们不会孤军奋战', 1): {
        'before': '*玛拉妮：我们赢了，哈哈哈哈，终于赢了！',
        'after': '*希诺宁：呼……',
    },
    ('于狱中绽放之花', 1): {
        'before': '派蒙：「绫华，你的最后一个愿望也已经实现了。」',
        'after': '派蒙：「绫华？」',
    },
    ('坠入永夜', 1): {
        'before': '*玛拉妮：加油，再快点！',
        'after': '*派蒙：要来不及了！',
    },
}

def insert_at_context(text, before, after, content):
    """在 before 和 after 之间插入 content，替换中间的 :: """
    # 找 before 在text中的位置
    before_idx = text.find(before)
    if before_idx < 0:
        return text, False

    # 在 before 之后找 after
    after_idx = text.find(after, before_idx + len(before))
    if after_idx < 0:
        return text, False

    # 取中间部分
    middle = text[before_idx + len(before):after_idx]

    # 确认中间有 ::
    if '::' not in middle:
        return text, False

    # 替换：保留 before，插入 content，保留 after
    new_text = text[:before_idx + len(before)] + '\n\n' + content + '\n\n' + text[after_idx:]
    return new_text, True

def content_already_exists(text, content):
    """检查content的前30字符是否已在text中存在"""
    first_line = content.strip().split('\n')[0].strip()[:30]
    if len(first_line) > 10 and first_line in text:
        return True
    # 检查前3行
    lines = content.strip().split('\n')
    if len(lines) >= 2:
        check = (lines[0].strip() + lines[1].strip())[:50]
        if len(check) > 15 and check in text:
            return True
    return False

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
        original_len = len(text)

        # --- 处理过场动画 ---
        for i, cs in enumerate(wdata.get('cutscenes', [])):
            content = cs['content'].strip()
            if not content or len(content) < 20:
                continue

            # 检查是否已存在
            if content_already_exists(text, content):
                stats['cs_skipped'] += 1
                continue

            # 查找上下文
            key = (title, i + 1)
            ctx = CUTSCENE_CONTEXTS.get(key)
            if ctx:
                new_text, ok = insert_at_context(text, ctx['before'], ctx['after'], content)
                if ok:
                    text = new_text
                    stats['cs_inserted'] += 1
                    print(f"  [过场] {title}[{i+1}]: {len(content)}字 -> 已插入")
                else:
                    print(f"  [过场] {title}[{i+1}]: 上下文匹配失败，跳过")
            else:
                print(f"  [过场] {title}[{i+1}]: 无上下文定位，跳过")

        # --- 处理额外对话 ---
        for dl in wdata.get('extra_dialogues', []):
            content = dl['content'].strip()
            if not content or len(content) < 5:
                continue

            if content_already_exists(text, content):
                continue

            marker = f'\n\n【额外对话·{dl["title"]}】\n{content}'
            text += marker
            stats['dl_inserted'] += 1

        if len(text) != original_len:
            q['text'] = text
            modified = True
            stats['pages_modified'] += 1

    if modified:
        shutil.copy(filepath, filepath + '.before_fold')
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(quests, f, ensure_ascii=False, indent=2)
        print(f"\n[文件] {fname}: 已更新 (备份: {fname}.before_fold)")

print("\n" + "=" * 60)
print("  插入完成")
print("=" * 60)
print(f"  过场动画插入: {stats['cs_inserted']} 条")
print(f"  过场动画跳过(已存在): {stats['cs_skipped']} 条")
print(f"  额外对话插入: {stats['dl_inserted']} 条")
print(f"  修改页面数: {stats['pages_modified']}")

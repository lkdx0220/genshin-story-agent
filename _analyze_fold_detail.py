# -*- coding: utf-8 -*-
"""第二步：精准检测 {{折叠|...}} 模板内容是否被收录 vs 被替换为占位符"""
import os
import json
import re
from collections import defaultdict

CONTENT_DIR = r'C:\Users\24701\Desktop\原神剧情\CASE-原神剧情助手-修改用\content_data'

# 分析结果
total_pages = 0
fold_sections_found = []  # 找到的所有折叠块
pages_with_cutscene_fold = []  # 有过场动画折叠的页面

for fname in sorted(os.listdir(CONTENT_DIR)):
    if not fname.startswith('quests_') or not fname.endswith('.json'):
        continue
    if fname == 'quests_processed.json':
        continue

    filepath = os.path.join(CONTENT_DIR, fname)
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            quests = json.load(f)
    except Exception as e:
        print(f"[错误] {fname}: {e}")
        continue

    for q in quests:
        title = q.get('title', '')
        text = q.get('text', '')
        category = q.get('category', '')
        if not text:
            continue
        total_pages += 1

        # 找所有 {{折叠|...}} 模板
        # 模式: {{折叠|key1=val1|key2=val2|...}} 或 {{折叠|title|content}}
        fold_pattern = re.finditer(r'\{\{折叠\|', text)
        for m in fold_pattern:
            start = m.start()
            # 匹配大括号直到闭合
            depth = 0
            end = start
            for i in range(start, len(text)):
                if text[i:i+2] == '{{':
                    depth += 1
                    i += 1
                elif text[i:i+2] == '}}':
                    depth -= 1
                    if depth == 0:
                        end = i + 2
                        break
                    i += 1

            fold_block = text[start:end]
            fold_start = start

            # 检查折叠块前后文
            context_before = text[max(0, start-50):start].strip()
            context_after = text[end:min(len(text), end+100)].strip()

            # 提取折叠标题
            title_match = re.search(r'\{\{折叠\s*\|([^|}=]+)(?=[}|=])', fold_block)
            fold_title = title_match.group(1).strip() if title_match else ''

            # 提取折叠内容
            # 格式1: {{折叠|标题|内容}}
            # 格式2: {{折叠|key=val|key=val|内容=...}}
            content_match = re.search(r'\|\s*内容\s*=\s*(.+?)\}\}$', fold_block, re.DOTALL)
            if content_match:
                fold_content = content_match.group(1).strip()
            else:
                # 简单格式 {{折叠|标题|内容}}
                simple_match = re.search(r'\{\{折叠\s*\|\s*[^|}]+\s*\|\s*(.+?)\}\}$', fold_block, re.DOTALL)
                if simple_match:
                    fold_content = simple_match.group(1).strip()
                else:
                    fold_content = fold_block

            content_len = len(fold_content)
            has_actual_content = content_len > 50 and bool(re.search(r'[*>]', fold_content[:300]))

            # 判断是过场动画还是对话
            is_cutscene = '过场动画' in fold_title or '过场动画' in fold_block[:50]
            is_dialogue = '对话' in fold_title or '额外' in fold_title
            is_other = not is_cutscene and not is_dialogue

            fold_sections_found.append({
                'page_title': title,
                'category': category,
                'file': fname,
                'fold_title': fold_title,
                'is_cutscene': is_cutscene,
                'is_dialogue': is_dialogue,
                'is_other': is_other,
                'content_len': content_len,
                'has_content': has_actual_content,
                'context_before': context_before[-80:],
                'content_preview': fold_content[:150],
            })

            if is_cutscene:
                pages_with_cutscene_fold.append({
                    'page_title': title,
                    'category': category,
                    'file': fname,
                    'fold_title': fold_title,
                    'content_len': content_len,
                    'has_content': has_actual_content,
                    'wiki_url': f"https://wiki.biligame.com/ys/{title}",
                })

# 统计
print("=" * 70)
print("  折叠模板收录分析报告")
print("=" * 70)

print(f"\n总页面数: {total_pages}")
print(f"含 {{折叠|...}} 模板的页面: {len(set(f['page_title'] for f in fold_sections_found))} 个")
print(f"总折叠块数: {len(fold_sections_found)}")

# 按类型分组
cutscene_folds = [f for f in fold_sections_found if f['is_cutscene']]
dialogue_folds = [f for f in fold_sections_found if f['is_dialogue']]
other_folds = [f for f in fold_sections_found if f['is_other']]

print(f"\n--- 过场动画折叠 ({len(cutscene_folds)} 个) ---")
cutscene_empty = [f for f in cutscene_folds if not f['has_content']]
cutscene_ok = [f for f in cutscene_folds if f['has_content']]
print(f"  有实质内容: {len(cutscene_ok)}")
print(f"  无实质内容/被截断: {len(cutscene_empty)}")

for f in cutscene_empty:
    print(f"\n  [{f['category']}] {f['page_title']}")
    print(f"    折叠标题: {f['fold_title']}")
    print(f"    内容长度: {f['content_len']} 字")
    print(f"    内容预览: {f['content_preview'][:100]}")

print(f"\n--- 角色对话折叠 ({len(dialogue_folds)} 个) ---")
dialogue_empty = [f for f in dialogue_folds if not f['has_content']]
dialogue_ok = [f for f in dialogue_folds if f['has_content']]
print(f"  有实质内容: {len(dialogue_ok)}")
print(f"  无实质内容: {len(dialogue_empty)}")

for f in dialogue_empty[:30]:
    print(f"\n  [{f['category']}] {f['page_title']} → '{f['fold_title']}'")
    print(f"    内容预览: {f['content_preview'][:100]}")

if len(dialogue_empty) > 30:
    print(f"\n  ... 还有 {len(dialogue_empty) - 30} 个")

print(f"\n--- 其他折叠 ({len(other_folds)} 个) ---")
other_empty = [f for f in other_folds if not f['has_content']]
print(f"  有实质内容: {len(other_folds) - len(other_empty)}")
print(f"  无实质内容: {len(other_empty)}")

# 汇总
print("\n" + "=" * 70)
print("  汇总")
print("=" * 70)
print(f"  过场动画折叠: {len(cutscene_folds)} 个 (空: {len(cutscene_empty)}, OK: {len(cutscene_ok)})")
print(f"  角色对话折叠: {len(dialogue_folds)} 个 (空: {len(dialogue_empty)}, OK: {len(dialogue_ok)})")
print(f"  其他折叠:     {len(other_folds)} 个 (空: {len(other_empty)}, OK: {len(other_folds) - len(other_empty)})")

total_empty = len(cutscene_empty) + len(dialogue_empty) + len(other_empty)
total_ok = len(fold_sections_found) - total_empty
print(f"\n  总计: {len(fold_sections_found)} 个折叠块")
print(f"    ✅ 内容完整: {total_ok} ({total_ok*100//len(fold_sections_found) if fold_sections_found else 0}%)")
print(f"    ❌ 内容缺失: {total_empty} ({total_empty*100//len(fold_sections_found) if fold_sections_found else 0}%)")

# 按任务类型统计
by_category = defaultdict(lambda: {'total': 0, 'empty': 0})
for f in fold_sections_found:
    by_category[f['category']]['total'] += 1
    if not f['has_content']:
        by_category[f['category']]['empty'] += 1

print("\n--- 按任务类型 ---")
for cat, counts in sorted(by_category.items()):
    print(f"  {cat}: {counts['total']} 个折叠块, {counts['empty']} 个缺失")

# 保存详细结果
from pathlib import Path
report_dir = os.path.dirname(CONTENT_DIR)
with open(os.path.join(report_dir, '_fold_detailed_report.json'), 'w', encoding='utf-8') as f:
    json.dump({
        'summary': {
            'total_pages': total_pages,
            'pages_with_folds': len(set(x['page_title'] for x in fold_sections_found)),
            'total_fold_blocks': len(fold_sections_found),
            'cutscene_total': len(cutscene_folds),
            'cutscene_ok': len(cutscene_ok),
            'cutscene_missing': len(cutscene_empty),
            'dialogue_total': len(dialogue_folds),
            'dialogue_ok': len(dialogue_ok),
            'dialogue_missing': len(dialogue_empty),
            'other_total': len(other_folds),
            'other_missing': len(other_empty),
        },
        'missing_cutscenes': cutscene_empty,
        'missing_dialogues': dialogue_empty,
        'by_category': dict(by_category),
    }, f, ensure_ascii=False, indent=2)

print(f"\n详细报告: {os.path.join(report_dir, '_fold_detailed_report.json')}")

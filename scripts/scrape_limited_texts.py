#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""完整版：抓取各区域「限定文本」板块并写入 lore.json

wikitext 结构：
  == 限定文本 ==          (level 2 - 顶层)
  ==== 迫近的客星 ====     (level 4 - 子板块/任务名)
  ===== 奇怪的记事本 ===== (level 5 - 条目标题)
  : 文本内容...            (定义列表内容)
  * 对话文本...            (列表项内容)
  {{模板|...}}            (模板，含嵌套内容)
"""

import requests
import json
import re
import os
import time

BASE = os.path.dirname(os.path.abspath(__file__))
LOREFILE = os.path.join(BASE, "content_data", "lore.json")

REGIONS = ["蒙德", "璃月", "稻妻", "枫丹", "纳塔"]
WIKI_BASE = "https://wiki.biligame.com/ys/index.php"

# ---- 抓取 ----
def fetch_raw(page_title):
    url = f"{WIKI_BASE}?title={page_title}&action=raw"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    resp = requests.get(url, headers=headers, timeout=60)
    resp.encoding = 'utf-8'
    return resp.text

def extract_section(wikitext, section_title):
    """从 wikitext 中提取指定 section"""
    lines = wikitext.split('\n')
    in_section = False
    section_lines = []
    section_level = None
    
    for line in lines:
        m = re.match(r'^(=+)\s*(.+?)\s*\1\s*$', line)
        if m:
            level = len(m.group(1))
            title = m.group(2).strip()
            if title == section_title and not in_section:
                in_section = True
                section_level = level
                continue
            if in_section and level <= section_level:
                break
        if in_section:
            section_lines.append(line)
    
    return '\n'.join(section_lines)

# ---- 清洗 wikitext ----
def clean_wikitext(text):
    """清洗 wikitext 标记，保留纯文本"""
    # 移除 {{提示|...}} 和 {{#info:...}}
    text = re.sub(r'\{\{提示\|[^}]*\}\}', '', text)
    text = re.sub(r'\{\{#info:[^}]*\}\}', '', text)
    # 移除 [[link|display]] → display, [[link]] → link
    text = re.sub(r'\[\[([^\]|]+)\|([^\]]+)\]\]', r'\2', text)
    text = re.sub(r'\[\[([^\]]+)\]\]', r'\1', text)
    # 移除 '''bold''' 和 ''italic''
    text = re.sub(r"'''", '', text)
    text = re.sub(r"''", '', text)
    # 移除 {{注音|字|音}} → 字
    text = re.sub(r'\{\{注音\|([^|]+)\|[^}]*\}\}', r'\1', text)
    # 移除 {{剧情选项|...}} - 复杂嵌套，先尝试提取 : 开头的文本行
    # 移除 <br> 和 <br />
    text = re.sub(r'<br\s*/?>', '\n', text)
    # 移除其他 HTML 标签
    text = re.sub(r'<[^>]+>', '', text)
    # 移除残留的 {{...}} 模板
    text = re.sub(r'\{\{[^}]*\}\}', '', text)
    # 移除行尾残留的 }}（来自跨行模板闭合）
    text = text.replace('}}', '')
    # 移除行首残留的 {{（来自跨行模板开始）
    text = text.replace('{{', '')
    # 压缩多余空行
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()

# ---- 核心解析 ----
def parse_limited_text_section(section_text):
    """解析「限定文本」板块，返回条目列表
    
    wikitext 有两种结构：
    1. ==== 板块名 ==== 直接跟文本（无 ===== 子标题）
    2. ==== 板块名 ==== → ===== 条目标题 ===== → 文本
    
    每个条目: { 'subsection': str, 'title': str, 'text': str }
    """
    lines = section_text.split('\n')
    entries = []
    
    current_subsection = None       # level 4 header
    current_entry_title = None      # level 5 header (或当无 level5 时用 subsection 名)
    current_entry_is_auto = False   # 标题目动从 subsection 生成
    current_text_lines = []
    
    # 模板嵌套深度追踪
    template_depth = 0
    
    def flush_entry():
        nonlocal current_entry_title, current_text_lines, current_entry_is_auto
        if current_entry_title is not None:
            raw = '\n'.join(current_text_lines)
            cleaned = clean_wikitext(raw)
            if cleaned.strip():
                # 如果标题是自动生成的，sub 设为空（因为标题本身就是 sub）
                sub = '' if current_entry_is_auto else (current_subsection or '')
                entries.append({
                    'subsection': sub,
                    'title': clean_wikitext(current_entry_title),
                    'text': cleaned
                })
            current_entry_title = None
            current_text_lines = []
            current_entry_is_auto = False
    
    def ensure_entry_title():
        """如果还没有条目标题，用当前 subsection 名自动创建一个"""
        nonlocal current_entry_title, current_entry_is_auto
        if current_entry_title is None and current_subsection is not None:
            current_entry_title = current_subsection
            current_entry_is_auto = True
    
    for line in lines:
        stripped = line.strip()
        if not stripped:
            if current_entry_title is not None:
                current_text_lines.append('')
            continue
        
        # 追踪 {{ }} 模板深度
        open_count = stripped.count('{{')
        close_count = stripped.count('}}')
        if template_depth > 0:
            template_depth += open_count - close_count
            if template_depth <= 0:
                template_depth = 0
            # 在模板内部，提取 : 或 * 开头的文本片段
            inner_texts = re.findall(r'[:：]\s*(.+?)(?=\||$)', stripped)
            for t in inner_texts:
                t = t.strip().strip("'").strip('"')
                if t and len(t) > 1:
                    ensure_entry_title()
                    current_text_lines.append(t)
            continue
        
        if open_count > close_count:
            # 进入嵌套模板
            template_depth = open_count - close_count
            inner_texts = re.findall(r'[:：]\s*(.+?)(?=\||$)', stripped)
            for t in inner_texts:
                t = t.strip().strip("'").strip('"')
                if t and len(t) > 1:
                    ensure_entry_title()
                    current_text_lines.append(t)
            continue
        
        # ==== level 4 header: subsection ====
        m4 = re.match(r'^====(?!=)\s*(.+?)\s*(?<!=)====$', stripped)
        if m4:
            flush_entry()
            current_subsection = m4.group(1).strip()
            continue
        
        # ===== level 5 header: entry title =====
        m5 = re.match(r'^=====(?!=)\s*(.+?)\s*(?<!=)=====$', stripped)
        if m5:
            flush_entry()
            current_entry_title = m5.group(1).strip()
            current_entry_is_auto = False
            continue
        
        # : 开头的定义列表行
        if stripped.startswith(':'):
            ensure_entry_title()
            text = stripped[1:].strip()
            current_text_lines.append(text)
        # * 开头的列表行
        elif stripped.startswith('*'):
            ensure_entry_title()
            text = stripped[1:].strip()
            current_text_lines.append(text)
        # ---- 分隔线
        elif stripped.startswith('----'):
            current_text_lines.append('')
        # 普通文本行（不含特殊标记）
        elif not re.match(r'^[={|}]', stripped):
            ensure_entry_title()
            current_text_lines.append(stripped)
    
    flush_entry()
    return entries

# ---- 构建 lore.json 条目 ----
def build_lore_entries(region, parsed_entries):
    """将解析结果转为 lore.json 条目"""
    result = []
    for entry in parsed_entries:
        sub = clean_wikitext(entry['subsection'])
        title = entry['title']
        text = entry['text']
        
        # 构建 title 路径
        if sub:
            lore_title = f"地图文本/{region} / 限定文本 / {sub} / {title}"
            breadcrumb = f"首页 > 北陆图书馆 > 地图文本/{region} > 限定文本 > {sub} > {title}"
        else:
            lore_title = f"地图文本/{region} / 限定文本 / {title}"
            breadcrumb = f"首页 > 北陆图书馆 > 地图文本/{region} > 限定文本 > {title}"
        
        result.append({
            "title": lore_title,
            "source": "北陆图书馆",
            "text": f"{breadcrumb}\n\n{text}",
            "entry_type": "游戏内文本"
        })
    return result

# ---- 主流程 ----
def main():
    all_new_entries = []
    stats = {}
    
    for region in REGIONS:
        page_title = f"万国诸卷拾遗/{region}"  # B站Wiki原页面名，仅作抓取源，输出使用“地图文本”
        print(f"\n{'='*50}")
        print(f"  正在抓取: {page_title}")
        
        try:
            raw = fetch_raw(page_title)
            section = extract_section(raw, "限定文本")
            
            if not section.strip():
                print(f"  [跳过] 未找到「限定文本」section")
                stats[region] = 0
                continue
            
            parsed = parse_limited_text_section(section)
            entries = build_lore_entries(region, parsed)
            
            print(f"  解析条目: {len(parsed)} 条")
            for e in parsed[:3]:
                preview = e['text'][:100].replace('\n', ' ')
                print(f"    [{e['subsection'] or '无'}] {e['title']}: {preview}...")
            if len(parsed) > 3:
                print(f"    ... 还有 {len(parsed) - 3} 条")
            
            all_new_entries.extend(entries)
            stats[region] = len(parsed)
            
        except Exception as e:
            print(f"  [错误] {e}")
            import traceback
            traceback.print_exc()
            stats[region] = -1
        
        time.sleep(0.5)
    
    # 汇总
    print(f"\n{'='*50}")
    print(f"  汇总:")
    for r, n in stats.items():
        print(f"    {r}: {n} 条")
    print(f"  总计新增: {len(all_new_entries)} 条")
    
    # 写入 lore.json
    if all_new_entries:
        with open(LOREFILE, 'r', encoding='utf-8') as f:
            lore = json.load(f)
        
        print(f"\n  原条目数: {len(lore)}")
        lore.extend(all_new_entries)
        print(f"  新条目数: {len(lore)}")
        
        with open(LOREFILE, 'w', encoding='utf-8') as f:
            json.dump(lore, f, ensure_ascii=False, indent=2)
        
        print("  写入完成!")
    else:
        print("\n  没有新增条目，跳过写入。")

if __name__ == "__main__":
    main()

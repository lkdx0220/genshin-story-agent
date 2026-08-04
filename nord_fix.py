#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""挪德卡莱限定文本重建：删除旧11条无层级条目，按 wiki 限定文本板块重建为新格式
复用 scrape_limited_texts.py 的 extract_section / parse_limited_text_section / build_lore_entries 函数
用法：
  python nord_fix.py --dry-run    # 仅打印解析结果，不写文件
  python nord_fix.py              # 执行删除旧条目 + 写入新条目
"""
import sys
import os
import json
import re
import argparse

BASE = os.path.dirname(os.path.abspath(__file__))
LOREFILE = os.path.join(BASE, "content_data", "lore.json")

# wiki raw wikitext 临时文件路径（WebFetch 保存的）
WIKI_RAW = r"C:\Users\24701\AppData\Local\Temp\trae\toolcall-output\143eaee7-5de1-44e1-959d-e3ed4dc14fcc.txt"

# 复用 scrape_limited_texts.py 的函数
sys.path.insert(0, BASE)
from scrape_limited_texts import extract_section, parse_limited_text_section, build_lore_entries


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="仅打印解析结果，不写文件")
    args = parser.parse_args()

    # 读取 wiki raw wikitext
    with open(WIKI_RAW, "r", encoding="utf-8") as f:
        raw = f.read()

    # 提取限定文本 section
    section = extract_section(raw, "限定文本")
    if not section.strip():
        print("[错误] 未找到「限定文本」section")
        return

    # 预处理：去掉 [[file:...]] 图片链接整行（clean_wikitext 会残留 center|500px 参数噪音）
    section = re.sub(r'^\[\[file:[^\]]*\]\].*$', '', section, flags=re.MULTILINE | re.IGNORECASE)

    # 解析为条目
    parsed = parse_limited_text_section(section)
    new_entries = build_lore_entries("挪德卡莱", parsed)

    print(f"=== 解析结果（{len(new_entries)} 条新条目）===")
    for e in new_entries:
        print(f"\nTITLE: {e['title']}")
        print(f"SOURCE: {e['source']}")
        print(f"ENTRY_TYPE: {e['entry_type']}")
        print(f"TEXT (前200字): {e['text'][:200]}...")

    if args.dry_run:
        print(f"\n[dry-run] 共 {len(new_entries)} 条，未写入文件")
        return

    # 读取 lore.json
    with open(LOREFILE, "r", encoding="utf-8") as f:
        lore = json.load(f)

    old_len = len(lore)

    # 删除旧11条（title == "万国诸卷拾遗/挪德卡莱 / 限定文本"）
    old_title = "万国诸卷拾遗/挪德卡莱 / 限定文本"
    lore = [e for e in lore if e.get("title") != old_title]
    after_del = len(lore)
    print(f"\n删除旧条目: {old_len} -> {after_del}（删除 {old_len - after_del} 条）")

    # 追加新条目
    lore.extend(new_entries)
    print(f"追加新条目: {after_del} -> {len(lore)}（新增 {len(new_entries)} 条）")

    # 写回
    with open(LOREFILE, "w", encoding="utf-8") as f:
        json.dump(lore, f, ensure_ascii=False, indent=2)
    print(f"写入完成: {LOREFILE}")


if __name__ == "__main__":
    main()

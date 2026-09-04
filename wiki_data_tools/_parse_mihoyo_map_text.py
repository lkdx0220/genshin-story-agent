# -*- coding: utf-8 -*-
"""
把米游社观测枢抓取的地图文本原始 JSON 解析成 lore.json 条目。

输入：content_data/mihoyo_map_text_raw_full.json（默认）
输出：content_data/lore.json（追加条目前自动备份）

条目格式使用用户期望的「地图文本」命名：
  {
    "title": "地图文本/至冬 / 白冕宫",
    "source": "米游社观测枢",
    "text": "地图文本正文"
  }
"""
import argparse
import json
import os
import re
import time
from bs4 import BeautifulSoup

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONTENT = os.path.join(BASE, "content_data")
DEFAULT_RAW = os.path.join(CONTENT, "mihoyo_map_text_raw_full.json")
LORE = os.path.join(CONTENT, "lore.json")


def log(msg):
    print(f"{time.strftime('%H:%M:%S')} {msg}", flush=True)


def clean_html(html):
    """把米游社交互文本 HTML 转成纯文本，保留段落/列表的大致换行。"""
    soup = BeautifulSoup(html or "", "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()
    text = soup.get_text("\n")
    lines = []
    for line in text.split("\n"):
        s = re.sub(r"[ \t]+", " ", line.strip())
        if s:
            lines.append(s)
    return "\n".join(lines)


def find_component_data(page, component_id):
    for module in page.get("modules", []):
        for comp in module.get("components", []):
            if comp.get("component_id") == component_id and comp.get("data"):
                return comp.get("data")
    return None


def extract_interactive_texts(page):
    data = find_component_data(page, "interactive_dialogue")
    if not data:
        return []
    try:
        obj = json.loads(data)
    except Exception:
        return []
    texts = []
    for root in obj.get("list", []):
        contents = root.get("contents") or {}
        for key, val in contents.items():
            dialogue = (val or {}).get("dialogue", "")
            if dialogue:
                text = clean_html(dialogue)
                if text:
                    texts.append(text)
    return texts


def region_from_item(item):
    try:
        arr = json.loads(item.get("filters", {}).get("filters_text", "") or "[]")
    except Exception:
        return ""
    for x in arr:
        if x.startswith("地区/"):
            return x.split("/", 1)[1]
    return ""


def location_from_title(title):
    m = re.search(r"【([^】]+)】", title or "")
    if m:
        return m.group(1)
    return (title or "").strip()


def norm_text(s):
    return re.sub(r"\s+", "", s or "")


def main():
    parser = argparse.ArgumentParser(description="解析米游社地图文本")
    parser.add_argument("--raw", default=DEFAULT_RAW, help="米游社地图文本原始 JSON 路径")
    parser.add_argument("--prefix", default="地图文本", help="lore 条目标题前缀")
    parser.add_argument("--dry-run", action="store_true", help="只统计不写文件")
    args = parser.parse_args()

    if not os.path.exists(args.raw):
        log(f"未找到原始数据: {args.raw}")
        return

    with open(args.raw, encoding="utf-8") as f:
        raw = json.load(f)
    with open(LORE, encoding="utf-8") as f:
        lore = json.load(f)

    existing_texts = {norm_text(e.get("text", "")) for e in lore}
    new_entries = []
    seen_texts = set()
    regions = set()
    locations = set()

    for item in raw.get("items", []):
        page = item.get("page") or {}
        title = item.get("title", "")
        region = region_from_item(item)
        location = location_from_title(title)
        texts = extract_interactive_texts(page)
        if not texts:
            continue
        regions.add(region)
        locations.add((region, location))
        for text in texts:
            nt = norm_text(text)
            if nt in existing_texts or nt in seen_texts:
                continue
            seen_texts.add(nt)
            new_entries.append({
                "title": f"{args.prefix}/{region} / {location}",
                "source": "米游社观测枢",
                "text": text,
            })

    log(f"总区域: {len(regions)}，地点: {len(locations)}，可新增文本: {len(new_entries)}")
    if args.dry_run:
        log("dry-run，不写文件")
        return

    if not new_entries:
        log("没有可新增条目")
        return

    backup = LORE + f".bak-before-full-map-text-{time.strftime('%Y%m%d')}"
    if not os.path.exists(backup):
        with open(backup, "w", encoding="utf-8") as f:
            json.dump(lore, f, ensure_ascii=False, indent=2)
        log(f"已备份: {backup}")

    lore.extend(new_entries)
    with open(LORE, "w", encoding="utf-8") as f:
        json.dump(lore, f, ensure_ascii=False, indent=2)
    log(f"lore.json 原 {len(lore) - len(new_entries)} 条，新增 {len(new_entries)} 条，现 {len(lore)} 条")


if __name__ == "__main__":
    main()

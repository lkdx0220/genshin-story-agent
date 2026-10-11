#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""T2：按 pageid 重爬「有键但空」的真任务页，只补 metadata.出场人物。

硬约束：
- 不修改 text 与其他 metadata 字段；
- 页面确实没有该字段时写 metadata.cast_absent = true，并删除空的出场人物键；
- 幂等：重复运行结果一致；
- 写前备份 quests_*.json 为 *.bak-before-cast-fix-20261010。
"""
import argparse
import json
import re
import sys
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR))
sys.path.insert(0, str(BASE_DIR / "scripts"))

from daily_wiki_update import BWIKI_API, BWIKI_HEADERS, curl_json  # noqa: E402
from wiki_entry_graph import _clean_cast_parts  # noqa: E402

DEFAULT_INPUT = Path("C:/Users/24701/Desktop/原神剧情/_cast_需重爬清单.json")
CONTENT_DIR = BASE_DIR / "content_data"
BACKUP_SUFFIX = ".bak-before-cast-fix-20261010"

_FIELD_LINE_RE = re.compile(r"^\|\s*出场人物\s*=\s*(.*)$")


def clean_cast_value(value: str) -> str:
    """去掉注释、HTML、wiki 链接，把换行/分隔符统一成顿号。"""
    value = re.sub(r"<!--.*?-->", "", value or "", flags=re.DOTALL)
    value = value.replace("<br>", "、").replace("<br/>", "、").replace("<br />", "、")
    value = re.sub(r"\[\[([^\]|]+)\|([^\]]+)\]\]", r"\2", value)
    value = re.sub(r"\[\[([^\]]+)\]\]", r"\1", value)
    value = value.replace("\r", "").replace("\n", "、")
    value = re.sub(r"[ \t\u3000]+", " ", value)
    value = re.sub(r"[、,，;；/]+", "、", value)
    value = value.strip().strip("、").strip()
    return value


def extract_cast_field(wikitext: str) -> str:
    """从 {{任务}} 模板里提取 |出场人物=...，支持跨行续写。"""
    lines = (wikitext or "").splitlines()
    for index, line in enumerate(lines):
        match = _FIELD_LINE_RE.match(line)
        if not match:
            continue
        parts = [match.group(1).strip()]
        cursor = index + 1
        while cursor < len(lines):
            nxt = lines[cursor]
            if nxt.startswith("|") or nxt.startswith("}}") or nxt.startswith("=="):
                break
            if nxt.strip():
                parts.append(nxt.strip())
            cursor += 1
        value = clean_cast_value("、".join(part for part in parts if part))
        return value
    return ""


def fetch_wikitext(pageid, title: str):
    params = {
        "action": "parse",
        "prop": "wikitext",
        "format": "json",
        "origin": "*",
    }
    if pageid:
        params["pageid"] = str(pageid)
    else:
        params["page"] = title
    data = curl_json(BWIKI_API, params, headers=BWIKI_HEADERS)
    if not data:
        return None
    parse_data = data.get("parse") or {}
    if not parse_data:
        return None
    return (parse_data.get("wikitext") or {}).get("*", "")


def find_records(items, pageid, title, category):
    """优先 pageid+title，其次 title+category；pageid=0 时只按标题匹配。"""
    pid = str(pageid or "")
    title = str(title or "")
    category = str(category or "")
    matches = []
    fallback = []
    for item in items:
        if not isinstance(item, dict):
            continue
        item_title = str(item.get("title") or "")
        item_cat = str(item.get("category") or "")
        item_pid = str(item.get("pageid") or "")
        if item_title != title or item_cat != category:
            continue
        if pid and item_pid == pid:
            matches.append(item)
        else:
            fallback.append(item)
    return matches or fallback


def main():
    parser = argparse.ArgumentParser(description="T2 重爬真任务页补出场人物")
    parser.add_argument("--input", default=str(DEFAULT_INPUT))
    parser.add_argument("--delay", type=float, default=0.6)
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"输入清单不存在: {input_path}", file=sys.stderr)
        return 1
    targets = json.loads(input_path.read_text(encoding="utf-8"))
    if args.limit > 0:
        targets = targets[:args.limit]

    # 一次性加载所有 quests 文件
    quest_files = {}
    for path in sorted(CONTENT_DIR.glob("quests_*.json")):
        if "processed" in path.name or ".bak" in path.name or "before" in path.name:
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        quest_files[path] = data

    # 写前备份
    for path in quest_files:
        backup = Path(str(path) + BACKUP_SUFFIX)
        if not backup.exists():
            backup.write_text(json.dumps(quest_files[path], ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"[备份] {backup.name}")

    stats = {
        "target_total": len(targets),
        "updated": 0,
        "cast_absent": 0,
        "fetch_failed": 0,
        "invalid_field": 0,
        "not_found": 0,
        "errors": [],
    }
    dirty_files = set()

    for index, target in enumerate(targets, 1):
        pageid = target.get("pageid")
        title = target.get("title")
        category = target.get("category")
        records = []
        for path, data in quest_files.items():
            found = find_records(data, pageid, title, category)
            for item in found:
                records.append((path, item))
        if not records:
            stats["not_found"] += 1
            stats["errors"].append(f"未找到本地记录: {title} pageid={pageid}")
            continue

        wikitext = fetch_wikitext(pageid, title)
        if wikitext is None:
            stats["fetch_failed"] += 1
            stats["errors"].append(f"抓取失败: {title} pageid={pageid}")
            continue

        raw_value = extract_cast_field(wikitext)
        clean_parts = _clean_cast_parts(raw_value)
        for path, item in records:
            metadata = item.setdefault("metadata", {})
            if raw_value and clean_parts:
                metadata["出场人物"] = raw_value
                metadata.pop("cast_absent", None)
                stats["updated"] += 1
            elif not raw_value:
                metadata.pop("出场人物", None)
                metadata["cast_absent"] = True
                stats["cast_absent"] += 1
            else:
                metadata.pop("出场人物", None)
                metadata["cast_absent"] = True
                stats["invalid_field"] += 1
            dirty_files.add(path)

        if index % 10 == 0 or index == len(targets):
            print(f"[进度] {index}/{len(targets)} updated={stats['updated']} absent={stats['cast_absent']} failed={stats['fetch_failed']}")
        time.sleep(args.delay)

    for path in sorted(dirty_files):
        path.write_text(json.dumps(quest_files[path], ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[写入] {path.name}")

    report_path = BASE_DIR / "reports" / "cast_fix_t2_report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

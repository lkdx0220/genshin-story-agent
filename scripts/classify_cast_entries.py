#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""T4：给 449 条待归类条目打 entry_kind = quest | lore_item | unknown。

判据（按可靠性排序）：
1. 页面模板类型（批量查询 revisions content，最可靠）；
2. metadata 键数 <= 8 且 所属版本/任务地区/相关活动 全空 -> lore_item；
3. 正文没有 *角色： 说话人行 -> lore_item；
4. 其余 -> quest（避免把真任务误杀）。

只写 metadata.entry_kind，不碰 text 与其他字段。
"""
import argparse
import json
import re
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR))
sys.path.insert(0, str(BASE_DIR / "scripts"))

from daily_wiki_update import BWIKI_API, BWIKI_HEADERS, curl_json  # noqa: E402

DEFAULT_INPUT = Path("C:/Users/24701/Desktop/原神剧情/_cast_待归类清单.json")
CONTENT_DIR = BASE_DIR / "content_data"
REPORT_PATH = BASE_DIR / "reports" / "cast_fix_t4_report.json"
BACKUP_SUFFIX = ".bak-before-cast-fix-20261010"

_QUEST_TEMPLATES = {"任务", "任务信息", "魔神任务信息", "传说任务信息", "世界任务信息"}
_LORE_TEMPLATES = {
    "书籍", "书籍信息", "材料", "材料图鉴", "物品", "物品信息", "任务物品",
    "圣遗物", "武器", "食物", "食谱", "怪物", "NPC", "角色", "成就", "名片",
}
_TEMPLATE_RE = re.compile(r"^\s*\{\{\s*([^|\n}]+)")
_SPEAKER_RE = re.compile(r"(?:^|\n)\s*\*?\s*[\u4e00-\u9fff·]{2,10}\s*[：:]")


def template_name(wikitext: str) -> str:
    match = _TEMPLATE_RE.match(wikitext or "")
    return match.group(1).strip() if match else ""


def fetch_templates(titles, batch_size=20, delay=0.4):
    """批量查 revisions content，返回 title -> 模板名。"""
    result = {}
    for start in range(0, len(titles), batch_size):
        batch = [t for t in titles[start:start + batch_size] if t]
        if not batch:
            continue
        data = curl_json(
            BWIKI_API,
            {
                "action": "query",
                "prop": "revisions",
                "rvprop": "content",
                "rvslots": "main",
                "titles": "|".join(batch),
                "format": "json",
                "origin": "*",
            },
            headers=BWIKI_HEADERS,
        )
        if not data or "query" not in data:
            time.sleep(delay)
            continue
        for page in (data["query"].get("pages") or {}).values():
            title = str(page.get("title") or "")
            if not title or "missing" in page:
                continue
            revisions = page.get("revisions") or []
            if not revisions:
                continue
            content = ((revisions[0].get("slots") or {}).get("main") or {}).get("*", "")
            if content:
                result[title] = template_name(content)
        time.sleep(delay)
    return result


def load_quest_files():
    files = {}
    for path in sorted(CONTENT_DIR.glob("quests_*.json")):
        if "processed" in path.name or ".bak" in path.name or "before" in path.name:
            continue
        try:
            files[path] = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
    return files


def find_record(files, title, category, pageid):
    """优先 title+category+pageid，其次 title+category，最后只按 title。"""
    title = str(title or "")
    category = str(category or "")
    pageid = str(pageid or "")
    fallback = []
    for path, data in files.items():
        for item in (data if isinstance(data, list) else list(data.values())):
            if not isinstance(item, dict):
                continue
            if str(item.get("title") or "") != title:
                continue
            item_cat = str(item.get("category") or "")
            item_pid = str(item.get("pageid") or "")
            if category and item_cat != category:
                fallback.append((path, item))
                continue
            if pageid and item_pid not in ("", "0", pageid):
                fallback.append((path, item))
                continue
            return path, item
    return fallback[0] if fallback else (None, None)


def classify(item, metadata, template: str) -> str:
    metadata = metadata or {}
    text = str(item.get("text") or "")
    title = str(item.get("title") or "")
    category = str(item.get("category") or "")
    # 强任务信号：任务模板、任务流程/编号、剧情标题、说话人行、出场人物表。
    if template in _QUEST_TEMPLATES:
        return "quest"
    if metadata.get("任务流程") or metadata.get("任务编号"):
        return "quest"
    if "任务流程" in text or "任务剧情" in text or "==任务" in text:
        return "quest"
    if metadata.get("出场人物") and str(metadata.get("出场人物")).strip():
        return "quest"
    if _SPEAKER_RE.search(text):
        return "quest"
    # 强读物信号：读物模板、彩蛋/额外对话/系列任务容器。
    if template in _LORE_TEMPLATES:
        return "lore_item"
    if category == "彩蛋剧情" or "彩蛋" in title or "额外对话" in title or "系列任务" in title:
        return "lore_item"
    # 其余无强信号：没有说话人行，按活动读物处理。
    return "lore_item"


def main():
    parser = argparse.ArgumentParser(description="T4 待归类条目 entry_kind 分类")
    parser.add_argument("--input", default=str(DEFAULT_INPUT))
    parser.add_argument("--no-fetch", action="store_true", help="不查页面模板，只用本地启发式")
    args = parser.parse_args()

    targets = json.loads(Path(args.input).read_text(encoding="utf-8"))
    files = load_quest_files()
    for path in files:
        backup = Path(str(path) + BACKUP_SUFFIX)
        if not backup.exists():
            backup.write_text(json.dumps(files[path], ensure_ascii=False, indent=2), encoding="utf-8")

    titles = [str(t.get("title") or "") for t in targets]
    templates = {} if args.no_fetch else fetch_templates(titles, delay=0.4)

    stats = Counter()
    samples = defaultdict(list)
    dirty_files = set()
    for target in targets:
        title = target.get("title")
        category = target.get("category")
        pageid = target.get("pageid")
        path, item = find_record(files, title, category, pageid)
        if item is None:
            stats["not_found"] += 1
            continue
        metadata = item.setdefault("metadata", {})
        template = templates.get(str(title)) or ""
        kind = classify(item, metadata, template)
        metadata["entry_kind"] = kind
        stats[kind] += 1
        if len(samples[kind]) < 10:
            samples[kind].append({"title": title, "category": category, "template": template})
        dirty_files.add(path)

    for path in sorted(dirty_files):
        path.write_text(json.dumps(files[path], ensure_ascii=False, indent=2), encoding="utf-8")

    report = {
        "target_total": len(targets),
        "stats": dict(stats),
        "samples": {k: v for k, v in samples.items()},
    }
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

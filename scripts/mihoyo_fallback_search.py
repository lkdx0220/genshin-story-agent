#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""米游社 Wiki 补搜：当 B站 Wiki 缺失/抓取失败/无字段时，去米游社任务/活动频道查同名剧情。

用法：
  python scripts/mihoyo_fallback_search.py [--input reports/activity_cast_rules_recheck.json]
输出：
  reports/mihoyo_fallback_search.json
"""
import argparse
import json
import re
import sys
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR))
sys.path.insert(0, str(BASE_DIR / "wiki_data_tools"))

import _fetch_mihoyo_channel as mh  # noqa: E402
import _parse_mihoyo_tasks as mt  # noqa: E402

REPORT_PATH = BASE_DIR / "reports" / "mihoyo_fallback_search.json"
CARD_CACHE = BASE_DIR / "reports" / "mihoyo_card_index.json"
TASK_CHANNEL = 43
ACTIVITY_CHANNEL = 105


def norm_title(s: str) -> str:
    s = str(s or "").strip()
    s = re.sub(r"[（(].*?[)）]", "", s)
    s = re.sub(r"\s+", "", s)
    return s


def load_card_index(force=False):
    if CARD_CACHE.exists() and not force:
        return json.loads(CARD_CACHE.read_text(encoding="utf-8"))
    cards = []
    for ch in (TASK_CHANNEL, ACTIVITY_CHANNEL):
        for card in mh.fetch_channel_list(ch):
            title = str(card.get("title") or "")
            cid = card.get("content_id")
            if title and cid:
                cards.append({"title": title, "content_id": cid, "channel": ch})
    CARD_CACHE.parent.mkdir(parents=True, exist_ok=True)
    CARD_CACHE.write_text(json.dumps(cards, ensure_ascii=False, indent=2), encoding="utf-8")
    return cards


def find_matches(cards, title: str):
    q = norm_title(title)
    if not q:
        return []
    exact = [c for c in cards if norm_title(c["title"]) == q]
    if exact:
        return exact
    contains = [c for c in cards if q and (q in norm_title(c["title"]) or norm_title(c["title"]) in q)]
    return contains[:5]


def parse_page(content_id):
    page = mh.fetch_entry_page(content_id)
    if not page:
        return None
    groups = mt.group_modules_from_page(page)
    best = None
    for group in groups:
        title = mt.group_title(group)
        text = mt.group_text(group)
        if not text:
            continue
        if best is None or len(text) > len(best[1]):
            best = (title, text, group)
    if not best:
        return None
    title, text, group = best
    meta = mt.extract_metadata(group, {}, "")
    speakers = re.findall(r"(?:^|\n)\s*\*?\s*([^\n：:]{1,20}?)\s*[：:]", text)
    return {
        "group_title": title,
        "text": text,
        "text_len": len(text),
        "speaker_lines": len(speakers),
        "cast": meta.get("出场人物", ""),
        "metadata": meta,
    }


def collect_targets(report_path: Path):
    report = json.loads(report_path.read_text(encoding="utf-8"))
    targets = []
    for rec in report.get("wiki_recheck", {}).get("records", []):
        if rec.get("status") in ("fetch_failed", "wiki_empty"):
            targets.append({"title": rec.get("title"), "source": rec.get("status")})
    for rec in report.get("extra_filter", {}).get("pending", []):
        targets.append({"title": rec.get("title"), "source": "extra_pending"})
    # dedupe
    out = []
    seen = set()
    for t in targets:
        key = norm_title(t["title"])
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(t)
    return out


def main():
    parser = argparse.ArgumentParser(description="米游社 Wiki 补搜")
    parser.add_argument("--input", default=str(BASE_DIR / "reports" / "activity_cast_rules_recheck.json"))
    parser.add_argument("--delay", type=float, default=0.3)
    parser.add_argument("--force-index", action="store_true")
    args = parser.parse_args()

    cards = load_card_index(args.force_index)
    targets = collect_targets(Path(args.input))
    print(f"card index {len(cards)}; targets {len(targets)}")
    records = []
    for index, target in enumerate(targets, 1):
        title = target["title"]
        matches = find_matches(cards, title)
        if not matches:
            records.append({"title": title, "source": target["source"], "found": False})
        else:
            match = matches[0]
            parsed = parse_page(match["content_id"])
            records.append({
                "title": title,
                "source": target["source"],
                "found": True,
                "match_title": match["title"],
                "content_id": match["content_id"],
                "channel": match["channel"],
                "parsed": parsed,
            })
        if index % 10 == 0 or index == len(targets):
            print(f"[{index}/{len(targets)}]")
        time.sleep(args.delay)

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps({"target_total": len(targets), "records": records}, ensure_ascii=False, indent=2), encoding="utf-8")
    found = sum(1 for r in records if r.get("found"))
    print(f"found {found}/{len(records)}; report {REPORT_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

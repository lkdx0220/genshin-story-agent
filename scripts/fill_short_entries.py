#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""P1：尝试用 B站 Wiki 补全正文 <100 字的极短/占位条目。

只写 text / metadata.story_placeholder / source，不删除条目。
"""
import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR))
sys.path.insert(0, str(BASE_DIR / "scripts"))

from fix_folded_story_text import fetch_page_html, extract_story_from_html, speaker_stats  # noqa: E402

CONTENT_DIR = BASE_DIR / "content_data"
REPORT_PATH = BASE_DIR / "reports" / "short_entry_fill_report.json"
BACKUP_SUFFIX = ".bak-before-short-fill-20261011"


def main():
    parser = argparse.ArgumentParser(description="补全极短条目")
    parser.add_argument("--delay", type=float, default=0.25)
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    files = {}
    for path in sorted(CONTENT_DIR.glob("quests_*.json")):
        if "processed" in path.name or ".bak" in path.name or "before" in path.name:
            continue
        try:
            files[path] = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
    originals = {p: json.dumps(d, ensure_ascii=False, indent=2) for p, d in files.items()}

    targets = []
    for path, data in files.items():
        for item in (data if isinstance(data, list) else list(data.values())):
            if not isinstance(item, dict):
                continue
            if len(str(item.get("text") or "")) < 100:
                targets.append((path, item))
    if args.limit > 0:
        targets = targets[:args.limit]

    stats = Counter()
    samples = []
    dirty = set()
    for index, (path, item) in enumerate(targets, 1):
        title = str(item.get("title") or "")
        old_text = str(item.get("text") or "")
        html = fetch_page_html(item.get("pageid"), title)
        new_text = extract_story_from_html(html) if html else ""
        lines, _, _ = speaker_stats(new_text)
        if new_text and len(new_text) >= 200 and lines:
            item["text"] = new_text
            md = item.setdefault("metadata", {})
            md.pop("story_placeholder", None)
            item["source"] = item.get("source") or "bwiki"
            stats["updated"] += 1
            dirty.add(path)
            if len(samples) < 10:
                samples.append({"title": title, "status": "updated", "old": len(old_text), "new": len(new_text)})
        else:
            md = item.setdefault("metadata", {})
            md["story_placeholder"] = True
            stats["placeholder"] += 1
            dirty.add(path)
            if len(samples) < 10:
                samples.append({"title": title, "status": "placeholder", "old": len(old_text), "new": len(new_text or "")})
        if index % 10 == 0 or index == len(targets):
            print(f"[{index}/{len(targets)}] updated={stats['updated']} placeholder={stats['placeholder']}")
        time.sleep(args.delay)

    for path in sorted(dirty):
        backup = Path(str(path) + BACKUP_SUFFIX)
        if not backup.exists():
            backup.write_text(originals[path], encoding="utf-8")
        path.write_text(json.dumps(files[path], ensure_ascii=False, indent=2), encoding="utf-8")

    report = {"targets": len(targets), "stats": dict(stats), "samples": samples}
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

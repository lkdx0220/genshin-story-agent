#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""活动剧情 cast 规则校对：

1. 重新校对所有带“相关活动”的活动子任务 Wiki 页面；
   - 子任务页有官方 `出场人物` 时，以子任务页为准，覆盖本地 speaker_lines/缺失值；
   - 子任务页没有该字段时，保留说话人提取结果，并记录冲突。
2. 额外对话/彩蛋：
   - 角色若不在所属活动任意子任务的 cast 中（优先官方子任务 cast，无官方则用子任务说话人 cast），
     视为未在剧情中登场，从 extra/egg cast 中移除。
3. 剧情 PV：
   - 只出现在 `过场动画/PV` 段落里的说话人，从 cast 中移出并记录到 cast_pv_pending，
     留给人工仲裁。
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
from fill_cast_by_speakers import extract_speakers, is_valid_cast  # noqa: E402
from fix_cast_by_recrawl import fetch_wikitext  # noqa: E402

CONTENT_DIR = BASE_DIR / "content_data"
REPORT_PATH = BASE_DIR / "reports" / "activity_cast_rules_recheck.json"
BACKUP_SUFFIX = ".bak-before-activity-recheck-20261011"

_FIELD_RE = re.compile(r"^\|\s*出场人物\s*=\s*(.*)$")
_PV_MARKERS = ("过场动画", "剧情PV", "剧情 PV", "CG")


def extract_wiki_cast(wikitext: str) -> str:
    for line in (wikitext or "").splitlines():
        match = _FIELD_RE.match(line)
        if match:
            return match.group(1).strip()
    return ""


def clean_cast(value: str):
    from wiki_entry_graph import _clean_cast_parts
    return _clean_cast_parts(value)


def strip_link(value: str) -> str:
    value = str(value or "").strip()
    value = re.sub(r"^\[\[|\]\]$", "", value)
    return value.split("|")[-1].strip()


def split_speakers(text: str):
    return extract_speakers(text or "")


def parse_pv_speakers(text: str):
    """返回 (pv_only_candidates, outside_names, inside_names)。"""
    lines = (text or "").splitlines()
    in_pv = False
    pv_lines = []
    outside_lines = []
    for line in lines:
        marker = any(m in line for m in _PV_MARKERS)
        if marker:
            in_pv = True
        if line.startswith("===") or line.startswith("=="):
            in_pv = False
        if in_pv:
            pv_lines.append(line)
        else:
            outside_lines.append(line)
    inside = split_speakers("\n".join(pv_lines))
    outside = split_speakers("\n".join(outside_lines))
    pv_only = [x for x in inside if x not in outside]
    return pv_only, outside, inside


def main():
    parser = argparse.ArgumentParser(description="活动 cast 规则校对")
    parser.add_argument("--delay", type=float, default=0.25)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    files = {}
    for name in ("quests_活动剧情.json", "quests_彩蛋剧情.json"):
        path = CONTENT_DIR / name
        if path.exists():
            files[path] = json.loads(path.read_text(encoding="utf-8"))
    # 修改前立即备份
    if not args.dry_run:
        for path, data in files.items():
            backup = Path(str(path) + BACKUP_SUFFIX)
            if not backup.exists():
                backup.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    report = {
        "wiki_recheck": {"targets": 0, "updated": 0, "empty_wiki": 0, "fetch_failed": 0, "records": []},
        "extra_filter": {"removed": [], "pending": []},
        "pv_pending": [],
    }

    # ---------- 1. Wiki 子任务校对 ----------
    children = []
    for path, data in files.items():
        for item in (data if isinstance(data, list) else list(data.values())):
            if not isinstance(item, dict):
                continue
            md = item.get("metadata") or {}
            title = str(item.get("title") or "")
            related = strip_link(md.get("相关活动"))
            is_extra = "额外对话" in title or "彩蛋" in title or str(item.get("category") or "") == "彩蛋剧情"
            if related and not is_extra:
                children.append((path, item, related))
    if args.limit > 0:
        children = children[:args.limit]
    report["wiki_recheck"]["targets"] = len(children)

    for index, (path, item, related) in enumerate(children, 1):
        md = item.setdefault("metadata", {})
        title = str(item.get("title") or "")
        pageid = item.get("pageid")
        wikitext = fetch_wikitext(pageid, title)
        if wikitext is None:
            report["wiki_recheck"]["fetch_failed"] += 1
            report["wiki_recheck"]["records"].append({"title": title, "status": "fetch_failed"})
            continue
        wiki_cast_str = extract_wiki_cast(wikitext)
        wiki_cast = clean_cast(wiki_cast_str)
        local_cast = clean_cast(md.get("出场人物"))
        source = str(md.get("cast_source") or "")
        if wiki_cast:
            if set(wiki_cast) != set(local_cast) or source in ("speaker_lines", "protagonist_only"):
                md["出场人物"] = wiki_cast_str
                md.pop("cast_source", None)
                md.pop("cast_absent", None)
                report["wiki_recheck"]["updated"] += 1
                report["wiki_recheck"]["records"].append({
                    "title": title,
                    "status": "updated_from_wiki",
                    "local": local_cast,
                    "wiki": wiki_cast,
                })
        else:
            report["wiki_recheck"]["empty_wiki"] += 1
            report["wiki_recheck"]["records"].append({
                "title": title,
                "status": "wiki_empty",
                "local": local_cast,
                "local_source": source,
            })
        if index % 20 == 0 or index == len(children):
            print(f"[wiki {index}/{len(children)}] updated={report['wiki_recheck']['updated']} empty={report['wiki_recheck']['empty_wiki']} failed={report['wiki_recheck']['fetch_failed']}")
        time.sleep(args.delay)

    # ---------- 2. 构建 activity -> 子任务 cast 并过滤 extra/egg ----------
    official_union = defaultdict(set)
    speaker_union = defaultdict(set)
    for path, data in files.items():
        for item in (data if isinstance(data, list) else list(data.values())):
            if not isinstance(item, dict):
                continue
            md = item.get("metadata") or {}
            title = str(item.get("title") or "")
            related = strip_link(md.get("相关活动"))
            if not related:
                continue
            is_extra = "额外对话" in title or "彩蛋" in title or str(item.get("category") or "") == "彩蛋剧情"
            if is_extra:
                continue
            cast = clean_cast(md.get("出场人物"))
            if not cast:
                continue
            source = str(md.get("cast_source") or "")
            if source in ("speaker_lines", "protagonist_only"):
                speaker_union[related].update(cast)
            else:
                official_union[related].update(cast)

    for path, data in files.items():
        for item in (data if isinstance(data, list) else list(data.values())):
            if not isinstance(item, dict):
                continue
            md = item.get("metadata") or {}
            title = str(item.get("title") or "")
            related = strip_link(md.get("相关活动"))
            is_extra = "额外对话" in title or "彩蛋" in title or str(item.get("category") or "") == "彩蛋剧情"
            if not is_extra:
                continue
            cast = clean_cast(md.get("出场人物"))
            if not cast:
                continue
            allowed = official_union.get(related) or speaker_union.get(related)
            if not allowed:
                report["extra_filter"]["pending"].append({"title": title, "related": related, "cast": cast})
                continue
            removed = [x for x in cast if x not in allowed]
            kept = [x for x in cast if x in allowed]
            if not removed:
                continue
            report["extra_filter"]["removed"].append({
                "title": title,
                "related": related,
                "removed": removed,
                "kept": kept,
            })
            if kept:
                md["出场人物"] = "、".join(kept)
                md["cast_source"] = "speaker_lines"
                md.pop("cast_absent", None)
            else:
                md.pop("出场人物", None)
                md["cast_absent"] = True
                md["cast_source"] = "protagonist_only"

    # ---------- 3. PV-only 说话人移出 cast ----------
    for path, data in files.items():
        for item in (data if isinstance(data, list) else list(data.values())):
            if not isinstance(item, dict):
                continue
            md = item.get("metadata") or {}
            if str(md.get("cast_source") or "") != "speaker_lines":
                continue
            cast = clean_cast(md.get("出场人物"))
            if not cast:
                continue
            pv_only, outside, inside = parse_pv_speakers(item.get("text") or "")
            pv_only = [x for x in pv_only if x in cast]
            if not pv_only:
                continue
            kept = [x for x in cast if x not in pv_only]
            report["pv_pending"].append({
                "title": item.get("title"),
                "category": item.get("category"),
                "pv_only": pv_only,
                "kept": kept,
            })
            if kept:
                md["出场人物"] = "、".join(kept)
            else:
                md.pop("出场人物", None)
                md["cast_absent"] = True
                md["cast_source"] = "protagonist_only"
            md["cast_pv_pending"] = "、".join(pv_only)

    # ---------- 写回 ----------
    if not args.dry_run:
        for path, data in files.items():
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "wiki_recheck": {k: v for k, v in report["wiki_recheck"].items() if k != "records"},
        "extra_removed": len(report["extra_filter"]["removed"]),
        "extra_pending": len(report["extra_filter"]["pending"]),
        "pv_pending_tasks": len(report["pv_pending"]),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

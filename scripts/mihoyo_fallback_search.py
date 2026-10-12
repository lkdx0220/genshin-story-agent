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

from _wiki_source_bootstrap import ensure_wiki_source  # noqa: E402
import _parse_mihoyo_tasks as mt  # noqa: E402  # 业务元数据解析（extract_metadata 等），不在迁移范围

ensure_wiki_source()
from wiki_source import WikiSource  # noqa: E402
from wiki_source.mihoyo import extract_dialogue  # noqa: E402

REPORT_PATH = BASE_DIR / "reports" / "mihoyo_fallback_search.json"
TASK_CHANNEL = 43
ACTIVITY_CHANNEL = 105

_client: WikiSource | None = None


def get_client() -> WikiSource:
    global _client
    if _client is None:
        _client = WikiSource()
    return _client


def norm_title(s: str) -> str:
    s = str(s or "").strip()
    s = re.sub(r"[（(].*?[)）]", "", s)
    s = re.sub(r"\s+", "", s)
    return s


def parse_page(content_id):
    """词条解析（0.2.0 迁移）：取数经 wiki_source；metadata 仍走项目业务解析。

    对话提取显式 order="occurrence"（迁移过渡期口径，见任务书 §四：
    与项目现状逐行一致；未来切 tree 需按顺序 diff 报告评估）。
    """
    entry_env = get_client().entry(content_id)
    if not entry_env["found"]:
        return None
    page = entry_env["page"] or {}
    dialogue_text = extract_dialogue(page, order="occurrence")["dialogue"]
    groups = mt.group_modules_from_page(page)
    best = None
    for group in groups:
        title = mt.group_title(group)
        text = mt.group_text(group)
        if not text:
            continue
        if best is None or len(text) > len(best[1]):
            best = (title, text, group)
    if not best and not dialogue_text:
        return None
    if best:
        title, full_text, group = best
        meta = mt.extract_metadata(group, {}, "")
    else:
        title, full_text, group, meta = "", "", None, {}
    if dialogue_text:
        return {
            "group_title": title,
            "text": dialogue_text,
            "text_len": len(dialogue_text),
            "speaker_lines": len(re.findall(r"(?:^|\n)\s*\*?\s*([^\n：:]{1,20}?)\s*[：:]", dialogue_text)),
            "cast": meta.get("出场人物", ""),
            "metadata": meta,
            "has_dialogue": True,
            "full_text": full_text,
        }
    return {
        "group_title": title,
        "text": full_text,
        "text_len": len(full_text),
        "speaker_lines": len(re.findall(r"(?:^|\n)\s*\*?\s*([^\n：:]{1,20}?)\s*[：:]", full_text)),
        "cast": meta.get("出场人物", ""),
        "metadata": meta,
        "has_dialogue": False,
        "full_text": full_text,
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
    parser.add_argument(
        "--force-index",
        action="store_true",
        help="兼容保留：卡片索引现由 wiki_source 客户端内存缓存，此参数无效果",
    )
    args = parser.parse_args()

    targets = collect_targets(Path(args.input))
    print(f"targets {len(targets)}")
    records = []
    for index, target in enumerate(targets, 1):
        title = target["title"]
        find_env = get_client().find(title)
        if find_env.get("ambiguous"):
            print(f"  [ambiguous] {title}: exact={len(find_env['exact_matches'])} 候选，按序尝试")
        matches = find_env["candidates"]
        chosen = None
        chosen_parsed = None
        for match in matches:
            parsed = parse_page(match["content_id"])
            if not parsed:
                time.sleep(args.delay)
                continue
            if chosen is None or (parsed.get("has_dialogue") and not (chosen_parsed or {}).get("has_dialogue")):
                chosen = match
                chosen_parsed = parsed
            if parsed.get("has_dialogue"):
                break
            time.sleep(args.delay)
        if not chosen:
            records.append({"title": title, "source": target["source"], "found": False})
        else:
            records.append({
                "title": title,
                "source": target["source"],
                "found": True,
                "match_title": chosen["title"],
                "content_id": chosen["content_id"],
                "channel": chosen["channel"],
                "parsed": chosen_parsed,
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

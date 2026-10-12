#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""修复 B站 Wiki {{折叠}} 剧情抓取不完整问题。

原理：不再解析 wikitext 模板，而是取 B站 action=parse&prop=text 渲染后的
“任务剧情”章节 HTML，折叠内容已经被展开，提取纯文本写回 item["text"]。

只处理检测到折叠残留 / 增益效果重复的条目；写前备份，支持 dry-run。
"""
import argparse
import json
import re
import sys
import time
from collections import Counter
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR))
sys.path.insert(0, str(BASE_DIR / "scripts"))

from _wiki_source_bootstrap import ensure_wiki_source  # noqa: E402

ensure_wiki_source()
from wiki_source import WikiSource  # noqa: E402
from wiki_source.folding import (  # noqa: E402,F401
    expand_local_folds,  # re-export：本地折叠递归展开（深度护栏 24 层）
    speaker_stats,  # re-export
)

CONTENT_DIR = BASE_DIR / "content_data"
REPORT_PATH = BASE_DIR / "reports" / "cast_fix_folded_text_report.json"
BACKUP_SUFFIX = ".bak-before-fold-fix-20261011"


_wiki_source: WikiSource | None = None


def _get_wiki_source() -> WikiSource:
    global _wiki_source
    if _wiki_source is None:
        _wiki_source = WikiSource()
    return _wiki_source


def looks_broken(text: str) -> bool:
    """折叠残留检测：wiki_source 通用判定 + 本脚本特有的"增益效果重复"损坏启发式（业务保留）。"""
    return _folding_looks_broken(text) or (
        bool(text) and str(text).count("增益效果：") >= 3
    )


def fetch_page_html(pageid, title: str):
    """渲染后 HTML（pageid 失效/为空自动按 title 回退，wiki_source 内置）。"""
    env = _get_wiki_source().bwiki.page(
        title=title or "", pageid=pageid or None, mode="text"
    )
    return env["html"]


def extract_story_from_html(html: str) -> str:
    """剧情段落提取。兼容旧签名（返回 str）；bs4 缺失时抛
    wiki_source.bwiki.CapabilityError（含安装指引），不再让整脚本 import 崩。"""
    from wiki_source.bwiki import extract_story_from_html as _ws_extract

    return _ws_extract(html)[0]


from wiki_source.folding import looks_broken as _folding_looks_broken  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description="修复折叠剧情 text")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--title", default="")
    parser.add_argument("--delay", type=float, default=0.35)
    args = parser.parse_args()

    files = {}
    for path in sorted(CONTENT_DIR.glob("quests_*.json")):
        if "processed" in path.name or ".bak" in path.name or "before" in path.name:
            continue
        try:
            files[path] = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
    original_text = {path: json.dumps(data, ensure_ascii=False, indent=2) for path, data in files.items()}

    targets = []
    for path, data in files.items():
        for item in (data if isinstance(data, list) else list(data.values())):
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or "")
            if args.title and title != args.title:
                continue
            if args.title == title or looks_broken(item.get("text") or ""):
                targets.append((path, item))
    if args.limit > 0:
        targets = targets[:args.limit]

    stats = Counter()
    samples = []
    dirty_files = set()
    for index, (path, item) in enumerate(targets, 1):
        title = str(item.get("title") or "")
        pageid = item.get("pageid")
        html = fetch_page_html(pageid, title)
        new_text = extract_story_from_html(html) if html else ""
        old_text = str(item.get("text") or "")
        old_lines, old_unique, _ = speaker_stats(old_text)
        new_lines, new_unique, new_names = speaker_stats(new_text)
        if not new_text or len(new_text) < 200 or not new_lines:
            fallback = expand_local_folds(old_text)
            fb_lines, fb_unique, fb_names = speaker_stats(fallback)
            if len(fallback) >= 200 and fb_lines:
                new_text, new_lines, new_unique, new_names = fallback, fb_lines, fb_unique, fb_names
        if not new_text or len(new_text) < 200 or not new_lines:
            stats["skipped"] += 1
            if len(samples) < 10:
                samples.append({"title": title, "status": "skip", "reason": "empty/short", "old": len(old_text), "new": len(new_text)})
            continue
        item["text"] = new_text
        dirty_files.add(path)
        stats["updated"] += 1
        if len(samples) < 10:
            samples.append({
                "title": title,
                "status": "updated",
                "old_len": len(old_text),
                "new_len": len(new_text),
                "old_speaker_lines": old_lines,
                "new_speaker_lines": new_lines,
                "new_speakers": new_names[:12],
            })
        if index % 20 == 0 or index == len(targets):
            print(f"[进度] {index}/{len(targets)} updated={stats['updated']} skipped={stats['skipped']}")
        time.sleep(args.delay)

    if not args.dry_run:
        for path in sorted(dirty_files):
            backup = Path(str(path) + BACKUP_SUFFIX)
            if not backup.exists():
                backup.write_text(original_text[path], encoding="utf-8")
            path.write_text(json.dumps(files[path], ensure_ascii=False, indent=2), encoding="utf-8")

    report = {"stats": dict(stats), "targets": len(targets), "samples": samples, "dry_run": args.dry_run}
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

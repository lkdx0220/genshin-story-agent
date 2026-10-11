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

from daily_wiki_update import BWIKI_API, BWIKI_HEADERS, curl_json  # noqa: E402
from bs4 import BeautifulSoup, NavigableString, Tag  # noqa: E402

CONTENT_DIR = BASE_DIR / "content_data"
REPORT_PATH = BASE_DIR / "reports" / "cast_fix_folded_text_report.json"
BACKUP_SUFFIX = ".bak-before-fold-fix-20261011"


def looks_broken(text: str) -> bool:
    if not text:
        return False
    return "{{折叠" in text or "|折叠=是}}" in text or text.count("增益效果：") >= 3


def extract_story_from_html(html: str) -> str:
    soup = BeautifulSoup(html or "", "lxml")
    for tag in soup(["script", "style"]):
        tag.decompose()
    for selector in (
        ".mw-editsection", ".toc", "table.infobox", ".navbox",
        ".mw-collapsible-toggle", ".mw-collapsible-toggle-placeholder",
        ".printfooter", ".mw-jump-link",
    ):
        for tag in soup.select(selector):
            tag.decompose()

    heading = soup.find(lambda t: t.name == "h2" and "任务剧情" in t.get_text())
    if heading is None:
        return ""

    parts = []
    for sib in heading.next_siblings:
        if getattr(sib, "name", None) == "h2":
            break
        if getattr(sib, "name", None) == "h3":
            title = sib.get_text(" ", strip=True)
            if title:
                parts.append("===" + title + "===")
        elif isinstance(sib, Tag):
            txt = sib.get_text("\n", strip=True)
            if txt:
                parts.append(txt)
        elif isinstance(sib, NavigableString):
            txt = str(sib).strip()
            if txt:
                parts.append(txt)

    text = "\n".join(parts)
    for marker in ("NewPP limit report", "Saved in parser cache"):
        idx = text.find(marker)
        if idx >= 0:
            text = text[:idx]
    text = re.sub(r"\[ ?编辑 ?\]", "", text)
    lines = []
    seen_blank = False
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line in ("折叠", "展开", "[折叠]", "[展开]"):
            if not seen_blank:
                lines.append("")
                seen_blank = True
            continue
        if "MediaWiki:" in line:
            continue
        seen_blank = False
        # 说话人行重新加 *，与原有 quest text 格式一致
        if re.match(r"^[^\n：:]{1,20}[：:]", line):
            line = "*" + line
        lines.append(line)
    text = "\n".join(lines).strip()
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text


def fetch_page_html(pageid, title: str):
    """优先 pageid；pageid 失效或返回空时退回标题查询。"""
    for params in (
        {"action": "parse", "prop": "text", "format": "json", "origin": "*", "pageid": str(pageid)} if pageid else None,
        {"action": "parse", "prop": "text", "format": "json", "origin": "*", "page": title},
    ):
        if not params:
            continue
        data = curl_json(BWIKI_API, params, headers=BWIKI_HEADERS)
        html = (data.get("parse", {}).get("text") or {}).get("*", "") if data else ""
        if html:
            return html
    return ""


def speaker_stats(text: str):
    names = re.findall(r"(?:^|\n)\s*\*?\s*([^\n：:]{1,20}?)\s*[：:]", text or "")
    return len(names), len(set(names)), names


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

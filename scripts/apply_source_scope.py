#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
来源隔离落地脚本 v1

只处理 source_scope 分类里 replacement_ready=True 的 lore 记录：
  - 用 official_doc_id 指向的官方正文块替换本地 B站文本；
  - 写 source=米游社观测枢；
  - 同一 official_doc_id 被多条本地记录命中时，默认跳过并写入去重报告，
    避免把同一段官方正文复制成多份。

用法：
  python scripts/apply_source_scope.py             # dry-run，只报告
  python scripts/apply_source_scope.py --apply     # 备份后写入 lore.json
"""

import argparse
import json
import re
import shutil
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPTS_DIR.parent
sys.path.insert(0, str(SCRIPTS_DIR))

from preprocess_source_scope import load_official_map_text_contents  # noqa: E402

CONTENT_DIR = PROJECT_DIR / "content_data"
DEFAULT_SCOPE_DIR = CONTENT_DIR / "source_scope"
DEFAULT_LORE_FILE = CONTENT_DIR / "lore.json"

BLOCK_ID_RE = re.compile(r"^map_text:(?P<page>\d+):block:(?P<index>\d+)$")
BLOCKS_ID_RE = re.compile(r"^map_text:(?P<page>\d+):blocks:(?P<indexes>[\d,]+)$")


def _load_jsonl(path: Path) -> list:
    rows = []
    with open(path, "r", encoding="utf-8") as file:
        for line in file:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _parse_block_indexes(doc_id: str):
    match = BLOCK_ID_RE.match(doc_id or "")
    if match:
        return match.group("page"), [int(match.group("index"))]
    match = BLOCKS_ID_RE.match(doc_id or "")
    if match:
        indexes = [int(value) for value in match.group("indexes").split(",") if value]
        return match.group("page"), indexes
    return None, None


def _build_replacement_text(page: dict, indexes: list) -> str:
    blocks = page.get("blocks") or []
    parts = []
    for index in indexes:
        if index < 0 or index >= len(blocks):
            return ""
        parts.append(blocks[index]["text"])
    return "\n".join(parts)


def main() -> int:
    parser = argparse.ArgumentParser(description="应用来源隔离替换清单到 lore.json")
    parser.add_argument("--scope-dir", default=str(DEFAULT_SCOPE_DIR), help="source_scope 目录")
    parser.add_argument("--lore-file", default=str(DEFAULT_LORE_FILE), help="lore.json 路径")
    parser.add_argument("--apply", action="store_true", help="写入 lore.json；默认 dry-run")
    parser.add_argument("--include-duplicates", action="store_true",
                        help="同一 official_doc_id 的重复组也参与替换（默认跳过）")
    args = parser.parse_args()

    scope_dir = Path(args.scope_dir)
    lore_file = Path(args.lore_file)
    classification_path = scope_dir / "classification.jsonl"

    rows = _load_jsonl(classification_path)
    ready = [
        row for row in rows
        if row.get("replacement_ready") and row.get("module") == "lore"
    ]
    print(f"replacement_ready lore: {len(ready)}")

    groups = defaultdict(list)
    for row in ready:
        groups[row.get("official_doc_id") or ""].append(row)
    duplicate_groups = {
        doc_id: group for doc_id, group in groups.items() if len(group) > 1
    }
    duplicate_records = sum(len(group) for group in duplicate_groups.values())
    print(f"official_doc_id 唯一组: {len(groups)}；重复组: {len(duplicate_groups)}；涉及记录: {duplicate_records}")

    pages = {
        page["content_id"]: page
        for page in load_official_map_text_contents()
    }

    selected = []
    skipped_duplicates = []
    missing_pages = []
    errors = []
    for doc_id, group in groups.items():
        if len(group) > 1 and not args.include_duplicates:
            skipped_duplicates.extend(group)
            continue
        page_id, indexes = _parse_block_indexes(doc_id)
        if not page_id or not indexes:
            errors.append({"official_doc_id": doc_id, "reason": "doc_id 无法解析"})
            continue
        page = pages.get(page_id)
        if not page:
            missing_pages.append(doc_id)
            continue
        replacement_text = _build_replacement_text(page, indexes)
        if not replacement_text.strip():
            errors.append({"official_doc_id": doc_id, "reason": "官方块正文为空"})
            continue
        for row in group:
            selected.append((row, page_id, indexes, replacement_text))

    print(f"可替换记录: {len(selected)}；跳过重复记录: {len(skipped_duplicates)}；"
          f"缺页面: {len(missing_pages)}；错误: {len(errors)}")

    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "mode": "apply" if args.apply else "dry-run",
        "classification": str(classification_path),
        "ready_total": len(ready),
        "selected_total": len(selected),
        "skipped_duplicate_total": len(skipped_duplicates),
        "missing_pages": missing_pages,
        "errors": errors,
        "duplicate_groups": {
            doc_id: [row.get("record_id") for row in group]
            for doc_id, group in duplicate_groups.items()
        },
    }

    if not args.apply:
        report_path = scope_dir / "_apply_dry_run.json"
        with open(report_path, "w", encoding="utf-8") as file:
            json.dump(report, file, ensure_ascii=False, indent=2)
        print(f"dry-run 报告: {report_path}")
        return 0

    if not lore_file.exists():
        print(f"lore.json 不存在: {lore_file}")
        return 1

    lore = json.loads(lore_file.read_text(encoding="utf-8"))
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = lore_file.with_name(f"{lore_file.name}.bak-before-source-replace-{timestamp}")
    shutil.copy2(lore_file, backup_path)
    print(f"已备份: {backup_path}")

    changed = 0
    for row, page_id, indexes, replacement_text in selected:
        source_index = row.get("source_index")
        if not isinstance(source_index, int) or source_index < 0 or source_index >= len(lore):
            errors.append({"record_id": row.get("record_id"), "reason": "source_index 越界"})
            continue
        item = lore[source_index]
        item["text"] = replacement_text
        item["source"] = "米游社观测枢"
        changed += 1

    lore_file.write_text(
        json.dumps(lore, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    report["changed_total"] = changed
    report["backup_file"] = str(backup_path)
    report_path = scope_dir / "_apply_report.json"
    with open(report_path, "w", encoding="utf-8") as file:
        json.dump(report, file, ensure_ascii=False, indent=2)
    print(f"已替换 {changed} 条；报告: {report_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""T3：把权威出场人物结构化写入下游产物。

- wiki_entry_graph.json：entry_type == "task" 的条目写入 cast / cast_source；
- quests_processed.json：同步写入 cast / cast_source；
- 只写目标字段，不碰 text / 其他 metadata；
- 运行前必须已有备份（脚本会检查 __b_ 备份是否存在）。
"""
import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR))

from wiki_entry_graph import WikiEntryGraph, apply_cast_to_graph, _load_cast_records, _norm_meta_key  # noqa: E402

GRAPH_PATH = BASE_DIR / "kb_vectors" / "wiki_entry_graph.json"
PROCESSED_PATH = BASE_DIR / "content_data" / "quests_processed.json"
REPORT_PATH = BASE_DIR / "reports" / "cast_fix_t3_report.json"


def load_processed(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def save_processed(path: Path, data: dict):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def main():
    parser = argparse.ArgumentParser(description="T3 出场人物结构化落库")
    parser.add_argument("--graph", default=str(GRAPH_PATH))
    parser.add_argument("--processed", default=str(PROCESSED_PATH))
    args = parser.parse_args()

    graph_path = Path(args.graph)
    processed_path = Path(args.processed)

    backup_graph = Path(str(graph_path) + ".bak-before-cast-fix-20261010")
    backup_processed = Path(str(processed_path) + ".bak-before-cast-fix-20261010")
    if not backup_graph.exists():
        print(f"缺少备份，拒绝写入: {backup_graph}", file=sys.stderr)
        return 1
    if not backup_processed.exists():
        print(f"缺少备份，拒绝写入: {backup_processed}", file=sys.stderr)
        return 1

    started = time.time()
    graph = WikiEntryGraph.load(str(graph_path))
    before_tasks = [e for e in graph.entries.values() if e.entry_type == "task"]
    before_cast = sum(1 for e in before_tasks if e.cast)
    stats = apply_cast_to_graph(graph)
    after_tasks = [e for e in graph.entries.values() if e.entry_type == "task"]
    after_cast = sum(1 for e in after_tasks if e.cast)
    graph.save(graph_path)

    records = _load_cast_records()
    processed = load_processed(processed_path)
    processed_updated = 0
    for title, item in processed.items():
        if not isinstance(item, dict):
            continue
        pair = records.get(title) or records.get(_norm_meta_key(title))
        if pair:
            item["cast"] = list(pair[0])
            item["cast_source"] = pair[1]
            processed_updated += 1
        else:
            item["cast"] = None
            item["cast_source"] = None
    save_processed(processed_path, processed)

    report = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "graph": {
            "task_total": len(after_tasks),
            "with_cast_before": before_cast,
            "with_cast_after": after_cast,
            "cast_source": dict(Counter(e.cast_source for e in after_tasks if e.cast)),
            "apply_stats": stats,
        },
        "processed": {
            "total": len(processed),
            "updated": processed_updated,
        },
        "elapsed_seconds": round(time.time() - started, 1),
    }
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[T3] graph task {len(after_tasks)}，cast {before_cast} -> {after_cast}")
    print(f"[T3] processed {len(processed)}，写入 cast {processed_updated}")
    print(f"[T3] 报告: {REPORT_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

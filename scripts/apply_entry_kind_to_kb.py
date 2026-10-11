#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""T4：把 entry_kind（quest/lore_item/unknown）写入 wiki_entry_graph.json 的任务条目。"""
import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR))

from wiki_entry_graph import WikiEntryGraph, apply_entry_kind_to_graph  # noqa: E402

GRAPH_PATH = BASE_DIR / "kb_vectors" / "wiki_entry_graph.json"
REPORT_PATH = BASE_DIR / "reports" / "cast_fix_t4_graph_report.json"


def main():
    parser = argparse.ArgumentParser(description="T4 entry_kind 写入图")
    parser.add_argument("--graph", default=str(GRAPH_PATH))
    args = parser.parse_args()
    graph_path = Path(args.graph)
    backup = Path(str(graph_path) + ".bak-before-cast-fix-20261010")
    if not backup.exists():
        print(f"缺少备份，拒绝写入: {backup}", file=sys.stderr)
        return 1

    graph = WikiEntryGraph.load(graph_path)
    before = Counter(e.entry_kind for e in graph.entries.values() if e.entry_type == "task")
    stats = apply_entry_kind_to_graph(graph)
    after = Counter(e.entry_kind for e in graph.entries.values() if e.entry_type == "task")
    graph.save(graph_path)

    report = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "before": dict(before),
        "after": dict(after),
        "apply_stats": stats,
    }
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

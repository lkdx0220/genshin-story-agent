#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""只重建主库 kb_vectors/kb_quests_vec（text-embedding-v4），不动其他集合。"""
import json
import shutil
import sys
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR))
sys.path.insert(0, str(BASE_DIR / "scripts"))

from kb_vector_store import KBVectorStore  # noqa: E402
from kb_build_index import build_parent_map, index_quests  # noqa: E402

VECTOR_DIR = BASE_DIR / "kb_vectors"
BACKUP_SUFFIX = ".bak-before-main-quest-rebuild-20261011"


def backup_existing():
    for suffix in ("_vectors.npy", "_meta.json", "_docs.json"):
        path = VECTOR_DIR / f"kb_quests_vec{suffix}"
        if path.exists():
            backup = Path(str(path) + BACKUP_SUFFIX)
            if not backup.exists():
                shutil.copy(path, backup)
                print(f"[备份] {backup.name}")


def main():
    started = time.time()
    backup_existing()
    store = KBVectorStore()
    print("[重建] 删除旧 kb_quests_vec ...")
    store.delete_collection("kb_quests_vec")
    print("[重建] 开始索引任务（text-embedding-v4）...")
    index_quests(store, build_parent_map())
    elapsed = time.time() - started
    stats = store.get_stats()
    report = {
        "collection": "kb_quests_vec",
        "count": stats.get("kb_quests_vec", 0),
        "seconds": round(elapsed, 1),
        "minutes": round(elapsed / 60, 1),
        "vector_dir": str(VECTOR_DIR),
    }
    report_path = BASE_DIR / "reports" / "main_quests_rebuild_report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

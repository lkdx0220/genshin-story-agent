#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""只重建 kb_vectors_m3/kb_quests_vec（本地 BGE-M3），不动其他集合。

用途：剧情文本更新后，先单独重建本地 M3 的 quest 向量，测量耗时。
注意：本脚本直接从当前 content_data 重新捕获切片，因此不再校验旧 kb_vectors 快照；
     旧主库 kb_vectors/kb_quests_vec 仍保持不动，后续需要另行走主库重建。
"""
import argparse
import json
import shutil
import sys
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR))
sys.path.insert(0, str(BASE_DIR / "scripts"))

import build_m3_vectors as m3  # noqa: E402

OUT_DIR = Path(m3.OUT_DIR)
COLLECTION = "kb_quests_vec"
BACKUP_SUFFIX = ".bak-before-m3-quest-rebuild-20261011"


def backup_existing():
    for suffix in ("_vectors.npy", "_meta.json", "_docs.json"):
        path = OUT_DIR / f"{COLLECTION}{suffix}"
        if path.exists():
            backup = Path(str(path) + BACKUP_SUFFIX)
            if not backup.exists():
                shutil.copy(path, backup)
                print(f"[备份] {backup.name}")


def main():
    parser = argparse.ArgumentParser(description="只重建 M3 quest 向量")
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    started = time.time()
    print("[1/3] 捕获当前 quest 切片...")
    groups = m3.capture_chunks()
    data = groups[COLLECTION]
    ids = data["ids"]
    docs = data["docs"]
    if args.limit > 0:
        ids = ids[:args.limit]
        docs = docs[:args.limit]
    print(f"      quest 切片数量：{len(ids)}")

    backup_existing()
    print(f"[2/3] 调用 Ollama {m3.DEFAULT_MODEL} 编码（batch={args.batch_size}）...")
    embeddings = []
    encode_started = time.time()
    for start in range(0, len(ids), args.batch_size):
        batch_docs = docs[start:start + args.batch_size]
        embeddings.extend(
            m3.ollama_embed(
                batch_docs,
                model=m3.DEFAULT_MODEL,
                url=m3.DEFAULT_OLLAMA_URL,
                num_ctx=m3.DEFAULT_NUM_CTX,
            )
        )
        done = start + len(batch_docs)
        if done % (args.batch_size * 10) == 0 or done == len(ids):
            elapsed = time.time() - encode_started
            speed = done / elapsed if elapsed > 0 else 0
            remain = (len(ids) - done) / speed if speed > 0 else 0
            print(f"      {done}/{len(ids)}  {speed:.1f} docs/s  预计剩余 {remain/60:.1f} min", flush=True)
    encode_elapsed = time.time() - encode_started

    print("[3/3] 保存 kb_quests_vec ...")
    m3.save_collection(COLLECTION, embeddings, ids, docs)
    total = time.time() - started
    report = {
        "collection": COLLECTION,
        "count": len(ids),
        "batch_size": args.batch_size,
        "encode_seconds": round(encode_elapsed, 1),
        "total_seconds": round(total, 1),
        "docs_per_second": round(len(ids) / encode_elapsed, 2) if encode_elapsed else 0,
        "snapshot_dir": str(m3.SNAPSHOT_DIR),
        "note": "本次未校验旧 kb_vectors 快照；主库需另行重建",
    }
    report_path = BASE_DIR / "reports" / "m3_quests_rebuild_report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

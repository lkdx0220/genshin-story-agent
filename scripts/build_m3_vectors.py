#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""使用本地 Ollama bge-m3 构建 M3 向量库。

硬约束：
- 复用现有 text-embedding-v4 向量库的 chunk ID、顺序与正文；
- 不修改现有 kb_vectors/；
- 不调用远程 embedding API；
- 产物单独写入 kb_vectors_m3/。
"""

import argparse
import hashlib
import importlib.util
import json
import os
import sys
import time
import urllib.request
from collections import OrderedDict
from datetime import datetime

import numpy as np

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SNAPSHOT_DIR = os.path.join(BASE_DIR, "kb_vectors")
OUT_DIR = os.path.join(BASE_DIR, "kb_vectors_m3")
COLLECTIONS = [
    "kb_quests_vec",
    "kb_lore",
    "kb_books",
    "kb_characters",
    "kb_npcs",
    "kb_regions",
]
DEFAULT_MODEL = "bge-m3:latest"
DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434/api/embed"
DEFAULT_NUM_CTX = 4096


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_kbi():
    """加载 scripts/kb_build_index.py，复用其中完全相同的切片逻辑。"""
    if BASE_DIR not in sys.path:
        sys.path.insert(0, BASE_DIR)
    path = os.path.join(BASE_DIR, "scripts", "kb_build_index.py")
    spec = importlib.util.spec_from_file_location("kbi_m3", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["kbi_m3"] = module
    spec.loader.exec_module(module)
    return module


class CaptureStore:
    """只接收 store.add，用于捕获切片，不写入任何文件。"""

    def __init__(self):
        self.calls = []

    def add(self, collection, ids, documents, metadatas):
        self.calls.append((collection, list(ids), list(documents), list(metadatas)))


def capture_chunks():
    kbi = load_kbi()
    store = CaptureStore()
    original_sleep = time.sleep
    kbi.time.sleep = lambda seconds: None
    try:
        kbi.index_quests(store, kbi.build_parent_map())
        kbi.index_lore(store)
        kbi.index_books(store)
        kbi.index_characters(store)
        kbi.index_npcs(store)
        kbi.index_regions(store)
    finally:
        kbi.time.sleep = original_sleep

    groups = OrderedDict(
        (collection, {"ids": [], "docs": [], "metas": []}) for collection in COLLECTIONS
    )
    for collection, ids, docs, metas in store.calls:
        if collection not in groups:
            raise RuntimeError(f"捕获到未知集合: {collection}")
        groups[collection]["ids"].extend(ids)
        groups[collection]["docs"].extend(docs)
        groups[collection]["metas"].extend(metas)
    return groups


def verify_against_snapshot(groups):
    """确认捕获结果与现有 text-embedding-v4 向量库的 ID、正文完全一致。"""
    for collection in COLLECTIONS:
        old_ids = json.load(
            open(os.path.join(SNAPSHOT_DIR, f"{collection}_meta.json"), encoding="utf-8")
        )
        old_docs = json.load(
            open(os.path.join(SNAPSHOT_DIR, f"{collection}_docs.json"), encoding="utf-8")
        )
        new_ids = groups[collection]["ids"]
        new_docs = groups[collection]["docs"]
        if old_ids == new_ids and old_docs == new_docs:
            continue
        if old_ids != new_ids:
            for index, (old, new) in enumerate(zip(old_ids, new_ids)):
                if old != new:
                    raise RuntimeError(
                        f"{collection} 的 chunk ID 与现有快照不一致: index={index} old={old!r} new={new!r}"
                    )
            raise RuntimeError(
                f"{collection} 的 chunk 数量与现有快照不一致: old={len(old_ids)} new={len(new_ids)}"
            )
        for index, (old, new) in enumerate(zip(old_docs, new_docs)):
            if old != new:
                raise RuntimeError(
                    f"{collection} 的 chunk 正文与现有快照不一致: index={index} id={new_ids[index]!r}"
                )
        raise RuntimeError(
            f"{collection} 的 chunk 正文数量与现有快照不一致: old={len(old_docs)} new={len(new_docs)}"
        )
    print("[校验] 捕获切片与现有 kb_vectors 快照完全一致")


def write_chunk_dump(groups, path):
    """导出 A/B 对照用的 chunk 清单。"""
    with open(path, "w", encoding="utf-8") as f:
        for collection in COLLECTIONS:
            data = groups[collection]
            for index, (chunk_id, document, meta) in enumerate(
                zip(data["ids"], data["docs"], data["metas"])
            ):
                record = {
                    "id": chunk_id,
                    "collection": collection,
                    "document": document,
                    "title": meta.get("title", ""),
                    "category": meta.get("entry_type", ""),
                    "source": meta.get("source_file", ""),
                    "chunk_index": meta.get("chunk_index", index),
                    "total_chunks": meta.get("total_chunks"),
                    "parent": meta.get("parent", ""),
                    "version": meta.get("version", ""),
                    "chapter_name": meta.get("chapter_name", ""),
                    "act_name": meta.get("act_name", ""),
                }
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
    print(f"[导出] chunk_dump: {path}")


def load_chunk_dump(path):
    groups = OrderedDict((collection, {"ids": [], "docs": []}) for collection in COLLECTIONS)
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            collection = record["collection"]
            if collection not in groups:
                raise RuntimeError(f"chunk_dump 中出现未知集合: {collection}")
            groups[collection]["ids"].append(record["id"])
            groups[collection]["docs"].append(record["document"])
    return groups


def ollama_embed(texts, model, url, num_ctx, retries=3):
    payload = json.dumps(
        {
            "model": model,
            "input": texts,
            "truncate": False,
            "options": {"num_ctx": num_ctx},
        },
        ensure_ascii=False,
    ).encode("utf-8")
    last_error = None
    for attempt in range(1, retries + 1):
        try:
            request = urllib.request.Request(
                url, data=payload, headers={"Content-Type": "application/json"}
            )
            with urllib.request.urlopen(request, timeout=1800) as response:
                data = json.loads(response.read().decode("utf-8"))
            embeddings = data.get("embeddings")
            if not isinstance(embeddings, list) or len(embeddings) != len(texts):
                raise RuntimeError(
                    f"Ollama 返回数量异常: {len(embeddings) if isinstance(embeddings, list) else type(embeddings)} != {len(texts)}"
                )
            dimensions = {len(vector) for vector in embeddings}
            if dimensions != {1024}:
                raise RuntimeError(f"Ollama 返回维度异常: {dimensions}")
            return embeddings
        except Exception as error:
            last_error = error
            if attempt < retries:
                wait = 2 ** (attempt - 1)
                print(
                    f"  [重试] Ollama 编码失败({type(error).__name__}: {error})，{wait}s 后重试 {attempt + 1}/{retries}"
                )
                time.sleep(wait)
    raise RuntimeError(f"Ollama 编码连续失败: {last_error}")


def output_ready(collection, expected_ids, expected_docs):
    vector_path = os.path.join(OUT_DIR, f"{collection}_vectors.npy")
    meta_path = os.path.join(OUT_DIR, f"{collection}_meta.json")
    docs_path = os.path.join(OUT_DIR, f"{collection}_docs.json")
    if not (
        os.path.exists(vector_path)
        and os.path.exists(meta_path)
        and os.path.exists(docs_path)
    ):
        return False
    try:
        vectors = np.load(vector_path)
        ids = json.load(open(meta_path, encoding="utf-8"))
        docs = json.load(open(docs_path, encoding="utf-8"))
    except Exception:
        return False
    return (
        vectors.shape == (len(expected_ids), 1024)
        and ids == expected_ids
        and docs == expected_docs
    )


def save_collection(collection, embeddings, ids, docs):
    vectors = np.asarray(embeddings, dtype=np.float32)
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    vectors = (vectors / norms).astype(np.float32)
    np.save(os.path.join(OUT_DIR, f"{collection}_vectors.npy"), vectors)
    with open(os.path.join(OUT_DIR, f"{collection}_meta.json"), "w", encoding="utf-8") as f:
        json.dump(ids, f, ensure_ascii=False, indent=2)
    with open(os.path.join(OUT_DIR, f"{collection}_docs.json"), "w", encoding="utf-8") as f:
        json.dump(docs, f, ensure_ascii=False, indent=2)


def main():
    parser = argparse.ArgumentParser(description="用 Ollama bge-m3 构建 M3 向量库")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--ollama-url", default=DEFAULT_OLLAMA_URL)
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--num-ctx", type=int, default=DEFAULT_NUM_CTX)
    parser.add_argument("--from-dump", action="store_true", help="直接读取 kb_vectors_m3/chunk_dump.jsonl")
    parser.add_argument("--dump-only", action="store_true", help="只捕获并导出 chunk_dump，不编码")
    parser.add_argument("--force", action="store_true", help="覆盖已存在的 M3 产物")
    args = parser.parse_args()

    os.makedirs(OUT_DIR, exist_ok=True)
    dump_path = os.path.join(OUT_DIR, "chunk_dump.jsonl")

    if args.from_dump:
        if not os.path.exists(dump_path):
            raise SystemExit(f"未找到 {dump_path}")
        groups = load_chunk_dump(dump_path)
        print(f"[输入] 从 chunk_dump 读取: {dump_path}")
    else:
        print("[捕获] 复用现有切片逻辑捕获 chunk（不调用 embedding）...")
        captured = capture_chunks()
        verify_against_snapshot(captured)
        write_chunk_dump(captured, dump_path)
        groups = OrderedDict(
            (collection, {"ids": captured[collection]["ids"], "docs": captured[collection]["docs"]})
            for collection in COLLECTIONS
        )

    if args.dump_only:
        print("[完成] 已导出 chunk_dump，未进行编码")
        return

    snapshot_hash = sha256_file(dump_path)
    counts = {}
    started_at = time.time()
    for collection in COLLECTIONS:
        ids = groups[collection]["ids"]
        docs = groups[collection]["docs"]
        counts[collection] = len(ids)
        if not args.force and output_ready(collection, ids, docs):
            print(f"[跳过] {collection} 已完成，{len(ids)} 条")
            continue
        print(f"[编码] {collection}: {len(ids)} 条，batch={args.batch_size}")
        embeddings = []
        collection_started = time.time()
        for start in range(0, len(ids), args.batch_size):
            batch_docs = docs[start : start + args.batch_size]
            embeddings.extend(
                ollama_embed(
                    batch_docs,
                    model=args.model,
                    url=args.ollama_url,
                    num_ctx=args.num_ctx,
                )
            )
            done = start + len(batch_docs)
            if done % (args.batch_size * 10) == 0 or done == len(ids):
                elapsed = time.time() - collection_started
                speed = done / elapsed if elapsed > 0 else 0.0
                remaining = (len(ids) - done) / speed if speed > 0 else 0.0
                print(
                    f"  {collection} {done}/{len(ids)}  {speed:.1f} 条/秒  预计剩余 {remaining / 60:.1f} 分钟"
                )
        save_collection(collection, embeddings, ids, docs)
        print(f"[完成] {collection}: {len(ids)} 条，用时 {time.time() - collection_started:.1f}s")

    manifest = {
        "embedding_model": "BAAI/bge-m3",
        "runtime": f"ollama/{args.model} (GGUF F16)",
        "ollama_url": args.ollama_url,
        "dim": 1024,
        "normalized": True,
        "max_length": args.num_ctx,
        "batch_size": args.batch_size,
        "collection_counts": counts,
        "kb_snapshot_hash": snapshot_hash,
        "chunk_dump": dump_path,
        "built_at": datetime.now().isoformat(timespec="seconds"),
    }
    with open(os.path.join(OUT_DIR, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    print(f"[完成] 全部集合: {counts}，总用时 {time.time() - started_at:.1f}s")
    print(f"[输出] {OUT_DIR}")


if __name__ == "__main__":
    main()

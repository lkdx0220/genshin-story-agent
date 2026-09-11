#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A/B 检索对比：text-embedding-v4 向量库 vs BGE-M3(Ollama) 向量库。

只做检索层对比，不跑 Agent、不跑 Answer LLM。

对比模式：
- vector：纯 dense 向量检索，直接看两套 embedding 的排序质量。
- hybrid：复用生产 hybrid_search 的关键词路 + 向量路 + RRF，
          关键词路和 RRF 完全一致，只替换向量路来源。

输入默认使用根目录 golden_test_set.json 的 question，
用 must_contain 作为“证据是否被召回”的代理指标。
"""

import argparse
import json
import os
import sys
import time
import urllib.request
from collections import OrderedDict
from datetime import datetime

import numpy as np

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

# 复用生产检索逻辑，保证关键词路/RRF 与线上一致。
from app.retrieval import (  # noqa: E402
    _get_doc_key,
    _keyword_search_docs,
    _rrf_fusion,
    VECTOR_MIN_SIMILARITY,
)
from kb_vector_store import GenshinEmbedder  # noqa: E402

COLLECTIONS = [
    "kb_quests_vec",
    "kb_lore",
    "kb_books",
    "kb_characters",
    "kb_npcs",
    "kb_regions",
]
DEFAULT_GOLDEN = os.path.join(
    os.path.dirname(BASE_DIR), "golden_test_set.json"
)
DEFAULT_VECTORS_A = os.path.join(BASE_DIR, "kb_vectors")
DEFAULT_VECTORS_B = os.path.join(BASE_DIR, "kb_vectors_m3")
DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434/api/embed"
DEFAULT_OLLAMA_MODEL = "bge-m3:latest"
DEFAULT_OLLAMA_NUM_CTX = 4096
DEFAULT_TOP_K = 10


class QwenEmbedder:
    """远程 text-embedding-v4 查询编码器，带进程内缓存。"""

    name = "text-embedding-v4"
    is_local = False

    def __init__(self):
        self.cache = {}

    def embed(self, text):
        if text not in self.cache:
            vector = GenshinEmbedder.embed_single(text)
            if vector is None:
                raise RuntimeError(f"text-embedding-v4 编码失败: {text[:40]!r}")
            self.cache[text] = np.asarray(vector, dtype=np.float32)
        return self.cache[text]


class OllamaEmbedder:
    """本地 Ollama BGE-M3 查询编码器，带进程内缓存。

    参数与 scripts/build_m3_vectors.py 的建库参数保持一致：
    /api/embed + truncate=False + options.num_ctx=4096。
    """

    name = "bge-m3"

    def __init__(self, url, model, num_ctx):
        self.url = url
        self.model = model
        self.num_ctx = num_ctx
        self.cache = {}

    def _embed_batch(self, texts):
        payload = json.dumps(
            {
                "model": self.model,
                "input": texts,
                "truncate": False,
                "options": {"num_ctx": self.num_ctx},
            },
            ensure_ascii=False,
        ).encode("utf-8")
        request = urllib.request.Request(
            self.url,
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=600) as response:
            data = json.loads(response.read().decode("utf-8"))
        embeddings = data.get("embeddings")
        if not isinstance(embeddings, list) or len(embeddings) != len(texts):
            raise RuntimeError(
                f"Ollama 返回数量异常: {type(embeddings)} / {len(texts)}"
            )
        vectors = []
        for vector in embeddings:
            arr = np.asarray(vector, dtype=np.float32)
            norm = float(np.linalg.norm(arr))
            if norm <= 0:
                raise RuntimeError("Ollama 返回零向量")
            vectors.append(arr / norm)
        return vectors

    def embed(self, text):
        if text not in self.cache:
            self.cache[text] = self._embed_batch([text])[0]
        return self.cache[text]


class LocalVectorIndex:
    """从 kb_vectors 目录读取 .npy + meta/docs，做本地余弦检索。"""

    def __init__(self, directory, embedder, name):
        self.directory = directory
        self.embedder = embedder
        self.name = name
        self.data = OrderedDict()
        self.key_text = {}
        self.load()

    def _load_json(self, path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def load(self):
        for collection in COLLECTIONS:
            vec_path = os.path.join(self.directory, f"{collection}_vectors.npy")
            meta_path = os.path.join(self.directory, f"{collection}_meta.json")
            doc_path = os.path.join(self.directory, f"{collection}_docs.json")
            if not (os.path.exists(vec_path) and os.path.exists(meta_path)):
                print(f"[警告] {self.name} 缺少集合文件: {collection}")
                continue
            vectors = np.load(vec_path).astype(np.float32)
            ids = self._load_json(meta_path)
            docs = self._load_json(doc_path) if os.path.exists(doc_path) else []
            if vectors.ndim != 2 or vectors.shape[1] != 1024:
                raise RuntimeError(
                    f"{self.name}/{collection} 向量维度异常: {vectors.shape}"
                )
            norms = np.linalg.norm(vectors, axis=1, keepdims=True)
            norms[norms == 0] = 1.0
            vectors = vectors / norms
            self.data[collection] = (vectors, ids, docs)

            for index, doc_id in enumerate(ids):
                doc = docs[index] if index < len(docs) else ""
                item = {"id": doc_id, "collection": collection, "document": doc}
                key = _get_doc_key(item)
                self.key_text.setdefault(key, []).append(doc)

        counts = {c: len(self.data[c][1]) for c in self.data}
        print(f"[加载] {self.name}: {counts}")

    def search_raw(self, query, per_collection, top_k):
        """纯向量检索：每个集合取 per_collection，全局合并取 top_k。"""
        query_vec = self.embedder.embed(query)
        merged = []
        for collection, (vectors, ids, docs) in self.data.items():
            scores = np.dot(vectors, query_vec)
            take = min(per_collection, len(scores))
            if take <= 0:
                continue
            top_indices = np.argsort(scores)[-take:][::-1]
            for index in top_indices:
                merged.append(
                    {
                        "id": ids[index],
                        "collection": collection,
                        "document": docs[index] if index < len(docs) else "",
                        "score": float(scores[index]),
                    }
                )
        merged.sort(key=lambda item: item["score"], reverse=True)
        return merged[:top_k]

    def evidence_texts(self, keys):
        parts = []
        for key in keys:
            parts.extend(self.key_text.get(key, []))
        return "\n".join(parts)


def load_queries(golden_path, limit=None):
    with open(golden_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict):
        questions = data.get("questions", [])
    else:
        questions = data
    if limit:
        questions = questions[:limit]
    return questions


def dedup_keys(items):
    keys = []
    seen = set()
    for item in items:
        key = _get_doc_key(item)
        if key not in seen:
            seen.add(key)
            keys.append(key)
    return keys


def coverage(index, keys, terms):
    """must_contain 词在命中文档中的覆盖率（检索层代理指标）。"""
    if not terms:
        return {"hit": 0, "total": 0, "ratio": 1.0, "hits": [], "missing": []}
    text = index.evidence_texts(keys)
    hits = [term for term in terms if term in text]
    missing = [term for term in terms if term not in text]
    return {
        "hit": len(hits),
        "total": len(terms),
        "ratio": len(hits) / len(terms),
        "hits": hits,
        "missing": missing,
    }


def overlap_at(keys_a, keys_b, k):
    set_a = set(keys_a[:k])
    set_b = set(keys_b[:k])
    if not set_a or not set_b:
        return 0.0
    return len(set_a & set_b) / k


def run_case(index_a, index_b, case, top_k):
    query = case["question"]
    terms = case.get("must_contain") or []

    # ---------- 纯向量模式 ----------
    # 生产 hybrid_search 向量路先取 max(top_k,15) 再过滤阈值；
    # 这里一次取 15，纯向量模式截前 top_k，混合模式用完整 15 条。
    search_depth = max(top_k, 15)
    t0 = time.perf_counter()
    vec_a_all = index_a.search_raw(
        query, per_collection=search_depth, top_k=search_depth
    )
    t_a_vec = time.perf_counter() - t0

    t0 = time.perf_counter()
    vec_b_all = index_b.search_raw(
        query, per_collection=search_depth, top_k=search_depth
    )
    t_b_vec = time.perf_counter() - t0

    vec_a = vec_a_all[:top_k]
    vec_b = vec_b_all[:top_k]
    keys_a_vec = dedup_keys(vec_a)
    keys_b_vec = dedup_keys(vec_b)

    # ---------- 混合检索模式 ----------
    t0 = time.perf_counter()
    kw_docs = _keyword_search_docs(query, top_k=search_depth)
    t_keyword = time.perf_counter() - t0

    vec_a_filtered = [
        item for item in vec_a_all if item["score"] >= VECTOR_MIN_SIMILARITY
    ]
    vec_b_filtered = [
        item for item in vec_b_all if item["score"] >= VECTOR_MIN_SIMILARITY
    ]
    merged_a = _rrf_fusion(kw_docs, vec_a_filtered, k=60)[:top_k]
    merged_b = _rrf_fusion(kw_docs, vec_b_filtered, k=60)[:top_k]
    keys_a_hybrid = [key for key, _ in merged_a]
    keys_b_hybrid = [key for key, _ in merged_b]

    # ---------- 指标 ----------
    result = {
        "id": case.get("id", ""),
        "category": case.get("category", ""),
        "question": query,
        "must_contain": terms,
        "vector": {
            "a_keys": keys_a_vec,
            "b_keys": keys_b_vec,
            "overlap@10": overlap_at(keys_a_vec, keys_b_vec, top_k),
            "a_coverage": coverage(index_a, keys_a_vec, terms),
            "b_coverage": coverage(index_b, keys_b_vec, terms),
            "a_latency_s": t_a_vec,
            "b_latency_s": t_b_vec,
        },
        "hybrid": {
            "a_keys": keys_a_hybrid,
            "b_keys": keys_b_hybrid,
            "overlap@10": overlap_at(keys_a_hybrid, keys_b_hybrid, top_k),
            "a_coverage": coverage(index_a, keys_a_hybrid, terms),
            "b_coverage": coverage(index_b, keys_b_hybrid, terms),
            "keyword_latency_s": t_keyword,
            "a_vec_empty": len(vec_a_filtered) == 0,
            "b_vec_empty": len(vec_b_filtered) == 0,
        },
    }
    return result


def summarize(cases, mode, top_k):
    a_cov = [c[mode]["a_coverage"]["ratio"] for c in cases]
    b_cov = [c[mode]["b_coverage"]["ratio"] for c in cases]
    overlaps = [c[mode]["overlap@10"] for c in cases]
    exact_hits_a = sum(c[mode]["a_coverage"]["hit"] for c in cases)
    exact_total = sum(c[mode]["a_coverage"]["total"] for c in cases)
    exact_hits_b = sum(c[mode]["b_coverage"]["hit"] for c in cases)

    wins_a = sum(
        1
        for c in cases
        if c[mode]["a_coverage"]["ratio"] > c[mode]["b_coverage"]["ratio"]
    )
    wins_b = sum(
        1
        for c in cases
        if c[mode]["b_coverage"]["ratio"] > c[mode]["a_coverage"]["ratio"]
    )
    ties = len(cases) - wins_a - wins_b

    return {
        "mode": mode,
        "case_count": len(cases),
        "a_macro_coverage": sum(a_cov) / len(a_cov) if a_cov else 0.0,
        "b_macro_coverage": sum(b_cov) / len(b_cov) if b_cov else 0.0,
        "a_micro_coverage": exact_hits_a / exact_total if exact_total else 0.0,
        "b_micro_coverage": exact_hits_b / exact_total if exact_total else 0.0,
        "a_wins": wins_a,
        "b_wins": wins_b,
        "ties": ties,
        "mean_overlap@10": sum(overlaps) / len(overlaps) if overlaps else 0.0,
    }


def main():
    parser = argparse.ArgumentParser(description="A/B 检索对比")
    parser.add_argument("--golden", default=DEFAULT_GOLDEN, help="golden_test_set.json 路径")
    parser.add_argument("--vectors-a", default=DEFAULT_VECTORS_A, help="A 向量库目录")
    parser.add_argument("--vectors-b", default=DEFAULT_VECTORS_B, help="B 向量库目录")
    parser.add_argument("--ollama-url", default=DEFAULT_OLLAMA_URL, help="Ollama /api/embed 地址")
    parser.add_argument("--ollama-model", default=DEFAULT_OLLAMA_MODEL, help="Ollama 模型名")
    parser.add_argument("--ollama-num-ctx", type=int, default=DEFAULT_OLLAMA_NUM_CTX)
    parser.add_argument("--top-k", type=int, default=DEFAULT_TOP_K)
    parser.add_argument("--limit", type=int, default=0, help="只跑前 N 题，0 表示全部")
    parser.add_argument("--out", default="", help="结果 JSON 输出路径")
    args = parser.parse_args()

    questions = load_queries(args.golden, limit=args.limit or None)
    print(f"[查询集] {args.golden}，共 {len(questions)} 题")

    embedder_a = QwenEmbedder()
    embedder_b = OllamaEmbedder(args.ollama_url, args.ollama_model, args.ollama_num_ctx)
    index_a = LocalVectorIndex(args.vectors_a, embedder_a, "A/text-embedding-v4")
    index_b = LocalVectorIndex(args.vectors_b, embedder_b, "B/bge-m3")

    # 公平性检查：两套库的 chunk ID 必须一致。
    for collection in COLLECTIONS:
        if collection not in index_a.data or collection not in index_b.data:
            continue
        ids_a = index_a.data[collection][1]
        ids_b = index_b.data[collection][1]
        if ids_a != ids_b:
            print(f"[警告] {collection} 两套库 ID 不一致，A/B 结果不可比")

    cases = []
    for number, case in enumerate(questions, 1):
        case_id = case.get("id", f"Q{number}")
        print(f"[{number}/{len(questions)}] {case_id} {case['question'][:50]}")
        result = run_case(index_a, index_b, case, args.top_k)
        cases.append(result)
        v = result["vector"]
        h = result["hybrid"]
        print(
            "  vector  A覆盖%.0f%% B覆盖%.0f%% overlap%.2f  "
            "hybrid  A覆盖%.0f%% B覆盖%.0f%% overlap%.2f"
            % (
                v["a_coverage"]["ratio"] * 100,
                v["b_coverage"]["ratio"] * 100,
                v["overlap@10"],
                h["a_coverage"]["ratio"] * 100,
                h["b_coverage"]["ratio"] * 100,
                h["overlap@10"],
            )
        )

    summary = {
        "vector": summarize(cases, "vector", args.top_k),
        "hybrid": summarize(cases, "hybrid", args.top_k),
    }

    print("\n===== 汇总 =====")
    for mode in ("vector", "hybrid"):
        s = summary[mode]
        print(
            "%s: A宏覆盖=%.1f%% B宏覆盖=%.1f%% | A微覆盖=%.1f%% B微覆盖=%.1f%% | "
            "A胜=%d B胜=%d 平=%d | mean overlap@%d=%.2f"
            % (
                mode,
                s["a_macro_coverage"] * 100,
                s["b_macro_coverage"] * 100,
                s["a_micro_coverage"] * 100,
                s["b_micro_coverage"] * 100,
                s["a_wins"],
                s["b_wins"],
                s["ties"],
                args.top_k,
                s["mean_overlap@10"],
            )
        )

    out_path = args.out
    if not out_path:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        report_dir = os.path.join(BASE_DIR, "reports")
        os.makedirs(report_dir, exist_ok=True)
        out_path = os.path.join(report_dir, f"ab_retrieval_{stamp}.json")
    else:
        out_dir = os.path.dirname(os.path.abspath(out_path))
        os.makedirs(out_dir, exist_ok=True)

    payload = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "golden": args.golden,
        "vectors_a": args.vectors_a,
        "vectors_b": args.vectors_b,
        "embedding_a": embedder_a.name,
        "embedding_b": f"ollama/{args.ollama_model}",
        "top_k": args.top_k,
        "summary": summary,
        "cases": cases,
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"\n[输出] {out_path}")


if __name__ == "__main__":
    main()

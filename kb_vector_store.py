#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
原神知识库向量存储 - 基于 NumPy 文件存储 + text-embedding-v4

提供：
- 全量建库：从原始 JSON 切片 → 嵌入 → 写入 .npy + .json 文件
- 语义搜索：余弦相似度向量检索
- 精准管理：按元数据增/删/改，支持大范围调整后重建

相比 ChromaDB 版本的优点：
- 无外部依赖（仅 NumPy），不会出现 SQLite 锁问题
- 文件存储不怕进程被强杀（写入原子性由 rename 保证）
- 跨平台一致行为
"""

import os
import sys
import json
import time
from typing import Dict, List, Optional

import numpy as np
import requests
from dotenv import load_dotenv

if getattr(sys, 'frozen', False):
    # exe 模式：从 exe 同目录读取外部 .env，避免把密钥打进成品。
    _ENV_PATH = os.path.join(os.path.dirname(sys.executable), '.env')
    if os.path.exists(_ENV_PATH):
        load_dotenv(_ENV_PATH)
else:
    load_dotenv()

# text-embedding-v4 不在 token-plan 新接口中，固定使用原 DashScope + 旧 Key（若未配置则退回当前 Key）。
QWEN_API_KEY = os.getenv("DASHSCOPE_FALLBACK_API_KEY") or os.getenv("DASHSCOPE_API_KEY") or ""
if not QWEN_API_KEY:
    raise RuntimeError("缺少嵌入 API Key：请配置 DASHSCOPE_API_KEY 或 DASHSCOPE_FALLBACK_API_KEY")
EMBEDDING_MODEL = "text-embedding-v4"
EMBEDDING_DIM = 1024
EMBEDDING_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1/embeddings"

# 向量存储目录
VECTOR_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "kb_vectors")

# 集合名称
# kb_quests 已废弃，拆分为 kb_quests_vec（向量语义检索）和 kb_quests_bm25（关键词检索）
COLLECTIONS = ["kb_quests_vec", "kb_quests_bm25", "kb_lore", "kb_books", "kb_characters", "kb_regions"]

# 嵌入批大小（API 限制每批最多 10 条）
BATCH_SIZE = 10


class GenshinEmbedder:
    """text-embedding-v4 嵌入器"""

    @staticmethod
    def embed(texts: List[str], max_retries: int = 3) -> Optional[List[List[float]]]:
        """批量嵌入文本列表。失败时重试（递增退避+抖动），返回向量列表或 None。"""
        import random
        if not texts:
            return []
        for attempt in range(max_retries):
            try:
                resp = requests.post(
                    EMBEDDING_URL,
                    headers={
                        "Authorization": f"Bearer {QWEN_API_KEY}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": EMBEDDING_MODEL,
                        "input": texts,
                    },
                    timeout=60,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    embeddings = data.get("data", [])
                    embeddings.sort(key=lambda x: x.get("index", 0))
                    return [e["embedding"] for e in embeddings]
                elif resp.status_code == 429:
                    # 限流退避：10s 起步，递增 + 随机抖动
                    wait = (attempt + 1) * 10 + random.uniform(0, 3)
                    print(f"  [Embedding] 限流，等待 {wait:.1f}s 后重试 ({attempt+1}/{max_retries})...")
                    time.sleep(wait)
                else:
                    wait = (attempt + 1) * 5 + random.uniform(0, 2)
                    print(f"  [Embedding] API 错误 {resp.status_code}，等待 {wait:.1f}s 后重试 ({attempt+1}/{max_retries})...")
                    time.sleep(wait)
            except Exception as e:
                wait = (attempt + 1) * 3 + random.uniform(0, 1)
                print(f"  [Embedding] 网络错误: {e}，等待 {wait:.1f}s 后重试 ({attempt+1}/{max_retries})...")
                time.sleep(wait)
        return None

    @staticmethod
    def embed_single(text: str) -> Optional[np.ndarray]:
        """嵌入单条文本，返回归一化向量"""
        result = GenshinEmbedder.embed([text])
        if result:
            vec = np.array(result[0], dtype=np.float32)
            return vec / np.linalg.norm(vec)
        return None


class KBVectorStore:
    """知识库向量存储（NumPy 文件版）"""

    def __init__(self):
        os.makedirs(VECTOR_DIR, exist_ok=True)

    # ====== 内部文件路径 ======

    def _validate_collection(self, collection: str) -> None:
        if collection not in COLLECTIONS:
            raise ValueError(f"未知的向量集合: {collection!r}")

    def _vec_path(self, collection: str) -> str:
        self._validate_collection(collection)
        return os.path.join(VECTOR_DIR, f"{collection}_vectors.npy")

    def _meta_path(self, collection: str) -> str:
        self._validate_collection(collection)
        return os.path.join(VECTOR_DIR, f"{collection}_meta.json")

    def _doc_path(self, collection: str) -> str:
        self._validate_collection(collection)
        return os.path.join(VECTOR_DIR, f"{collection}_docs.json")

    # ====== 加载/保存 ======

    def _load(self, collection: str):
        """加载集合的所有数据。返回 (vectors, ids, docs) 或 (None, [], [])。"""
        vp = self._vec_path(collection)
        mp = self._meta_path(collection)
        dp = self._doc_path(collection)
        if not os.path.exists(vp):
            return None, [], []
        vectors = np.load(vp)
        ids = json.load(open(mp, "r", encoding="utf-8")) if os.path.exists(mp) else []
        docs = json.load(open(dp, "r", encoding="utf-8")) if os.path.exists(dp) else []
        return vectors, ids, docs

    def _save(self, collection: str, vectors: np.ndarray, ids: List[str], docs: List[str]):
        """保存集合数据到文件（原子写入：先写临时文件，再 rename，避免进程强杀导致文件损坏）。
        带重试机制应对 Windows Defender 文件锁。"""
        vp = self._vec_path(collection)
        mp = self._meta_path(collection)
        dp = self._doc_path(collection)

        # 原子写入：临时文件 + rename
        vp_tmp = vp + '.tmp.npy'
        mp_tmp = mp + '.tmp'
        dp_tmp = dp + '.tmp'

        # 先写入临时文件
        np.save(vp_tmp[:-4], vectors)  # np.save 自动加 .npy
        json.dump(ids, open(mp_tmp, "w", encoding="utf-8"), ensure_ascii=False)
        json.dump(docs, open(dp_tmp, "w", encoding="utf-8"), ensure_ascii=False)

        # 带重试的 rename（应对 Windows Defender 文件锁）
        paths = [(vp_tmp, vp), (mp_tmp, mp), (dp_tmp, dp)]
        for tmp, target in paths:
            for attempt in range(5):
                try:
                    os.replace(tmp, target)
                    break
                except (OSError, PermissionError) as e:
                    if attempt < 4:
                        wait = (attempt + 1) * 2
                        print(f"  [Save] 文件锁重试 {attempt+1}/5: {e}，等待 {wait}s")
                        time.sleep(wait)
                    else:
                        raise RuntimeError(f"保存失败 {target}: {e}")

    # ====== 写入 ======

    def add(self, collection: str, ids: List[str],
            documents: List[str], metadatas: List[dict]):
        """批量添加向量记录（自动嵌入 + 写入文件）"""
        if not ids:
            return

        # 嵌入
        total_batches = (len(documents) + BATCH_SIZE - 1) // BATCH_SIZE
        all_embeddings = []
        for i in range(0, len(documents), BATCH_SIZE):
            batch_no = i // BATCH_SIZE + 1
            batch_end = min(i + BATCH_SIZE, len(documents))
            print(f"  [嵌入] {batch_no}/{total_batches} ({batch_end}/{len(documents)})", end=" ", flush=True)
            batch_docs = documents[i:i + BATCH_SIZE]
            batch_embeddings = GenshinEmbedder.embed(batch_docs)
            if batch_embeddings is None:
                raise RuntimeError(f"向量嵌入失败，集合={collection}, 批={batch_no - 1}")
            all_embeddings.extend(batch_embeddings)
            print("OK")
            if i + BATCH_SIZE < len(documents):
                time.sleep(1.0)

        new_vectors = np.array(all_embeddings, dtype=np.float32)

        # 归一化
        norms = np.linalg.norm(new_vectors, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        new_vectors = new_vectors / norms

        # 合并已有数据
        existing_vecs, existing_ids, existing_docs = self._load(collection)
        if existing_vecs is not None and len(existing_ids) > 0:
            all_vecs = np.vstack([existing_vecs, new_vectors])
            all_ids = existing_ids + ids
            all_docs = existing_docs + documents
        else:
            all_vecs = new_vectors
            all_ids = list(ids)
            all_docs = list(documents)

        self._save(collection, all_vecs, all_ids, all_docs)

    # ====== 搜索 ======

    def search(self, query: str, collection: str = None,
               top_k: int = 5, exclude: List[str] = None) -> List[dict]:
        """语义搜索（余弦相似度）。
        collection: 指定集合名，为空则搜全部集合，每个集合各取 top_k。
        exclude: 排除的集合名列表。
        """
        query_vec = GenshinEmbedder.embed_single(query)
        if query_vec is None:
            return []

        if collection:
            collections = [collection]
        else:
            collections = list(COLLECTIONS)

        if exclude:
            collections = [c for c in collections if c not in exclude]

        all_results = []
        for col_name in collections:
            vectors, ids, docs = self._load(col_name)
            if vectors is None or len(ids) == 0:
                continue
            # 余弦相似度 = 向量点积（已归一化）
            scores = np.dot(vectors, query_vec)
            top_indices = np.argsort(scores)[-top_k:][::-1]
            for idx in top_indices:
                all_results.append({
                    "id": ids[idx],
                    "collection": col_name,
                    "document": docs[idx] if idx < len(docs) else "",
                    "score": float(scores[idx]),
                })

        all_results.sort(key=lambda x: x["score"], reverse=True)
        return all_results[:top_k]

    # ====== 元数据操作 ======

    def delete_collection(self, collection: str):
        """删除集合的所有文件"""
        for path in [self._vec_path(collection), self._meta_path(collection), self._doc_path(collection)]:
            try:
                if os.path.exists(path):
                    os.remove(path)
            except Exception:
                pass

    # ====== 统计 ======

    def get_stats(self) -> Dict[str, int]:
        """返回各集合条目数"""
        stats = {}
        for name in COLLECTIONS:
            vp = self._vec_path(name)
            if os.path.exists(vp):
                try:
                    vectors = np.load(vp)
                    stats[name] = vectors.shape[0]
                except Exception:
                    stats[name] = 0
            else:
                stats[name] = 0
        return stats

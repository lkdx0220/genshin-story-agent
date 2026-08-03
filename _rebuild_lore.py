# -*- coding: utf-8 -*-
"""重建 kb_lore 向量集合（含切片），替换旧的整条嵌入方式"""

import json
import os
import re
import sys
import time

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)
from kb_vector_store import KBVectorStore

CONTENT_DIR = os.path.join(SCRIPT_DIR, "content_data")
CHUNK_MIN = 200   # 切片最小长度（字），短于此不切
CHUNK_MAX = 2000  # 切片目标最大长度（字），超出的按段落切
BATCH = 100       # 嵌入批次大小
COLLECTION = "kb_lore"


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def chunk_text(title: str, text: str, source: str) -> list:
    """将长文本按标题层级递归切分，返回 [(doc_id, doc_text)] 列表。"""
    if len(text) <= CHUNK_MAX:
        return [(f"lore:{title}", f"【{title}】({source})\n{text}")]

    # 递归切分：先 ## 再 ### 再段落再强制字数
    chunks = _recursive_split(text, CHUNK_MAX)
    total = len(chunks)
    result = []
    for idx, chunk in enumerate(chunks):
        chunk_id = f"lore:{title}:chunk:{idx}"
        header = f"【{title}】({source})" if idx == 0 else f"【{title} · 续】({source})"
        result.append((chunk_id, f"{header}\n{chunk}"))
    return result


def _recursive_split(text: str, max_len: int, depth: int = 0) -> list:
    """递归切分文本，优先用标题切分，层级递进。"""
    if len(text) <= max_len:
        return [text] if text.strip() else []

    # 第 1 层：## 标题；第 2 层：### 标题；第 3 层：段落；第 4 层：强制字数
    patterns = [
        r'\n(?=##\s)',    # depth=0: ## 标题
        r'\n(?=###\s)',   # depth=1: ### 标题
        r'\n\n+',         # depth=2: 段落
    ]
    if depth < len(patterns):
        sections = re.split(patterns[depth], text)
    else:
        sections = [text]

    if len(sections) <= 1:
        # 当前层级切不动，降一级
        if depth < 3:
            return _recursive_split(text, max_len, depth + 1)
        # 所有层级都切不动，强制按字数分
        result = []
        for i in range(0, len(text), max_len):
            result.append(text[i:i + max_len])
        return result

    # 递归处理每个 section
    result = []
    for sec in sections:
        sec = sec.strip()
        if not sec:
            continue
        if len(sec) <= max_len:
            result.append(sec)
        else:
            result.extend(_recursive_split(sec, max_len, depth + 1))
    return result


def main():
    log("===== 重建 kb_lore =====")

    # 读取 lore.json
    lore_path = os.path.join(CONTENT_DIR, "lore.json")
    with open(lore_path, "r", encoding="utf-8") as f:
        lore = json.load(f)
    log(f"lore.json: {len(lore)} 条")

    store = KBVectorStore()

    # 删除旧集合
    log(f"删除旧集合 {COLLECTION}...")
    store.delete_collection(COLLECTION)
    time.sleep(1)

    # 切片 & 嵌入
    total_chunks = 0
    ids_batch, docs_batch, metas_batch = [], [], []

    for entry in lore:
        title = entry.get("title", "未知")
        text = entry.get("text", "")
        if not text or len(text.strip()) < 50:
            continue
        source = entry.get("source", "")

        chunks = chunk_text(title, text, source)
        for chunk_id, doc_text in chunks:
            ids_batch.append(chunk_id)
            docs_batch.append(doc_text)
            metas_batch.append({
                "source_file": "lore.json",
                "title": title,
                "entry_type": "世界观设定",
                "source": "lore",
                "text_preview": doc_text[:200],
            })

            if len(ids_batch) >= BATCH:
                store.add(COLLECTION, ids_batch, docs_batch, metas_batch)
                total_chunks += len(ids_batch)
                log(f"  已嵌入 {total_chunks} 条...")
                ids_batch, docs_batch, metas_batch = [], [], []
                time.sleep(0.5)

    # 最后一批
    if ids_batch:
        store.add(COLLECTION, ids_batch, docs_batch, metas_batch)
        total_chunks += len(ids_batch)

    log(f"\n===== 完成 =====")
    log(f"总计切片: {total_chunks}")
    log(f"Stats: {store.get_stats()}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""只重建 kb_characters 向量索引。

统一复用 scripts/kb_build_index.py::index_characters，确保角色主条目和
角色档案段落（角色详细/角色故事/特殊档案/神之眼）使用同一套切片逻辑。
"""

import os
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)
sys.path.insert(0, os.path.join(BASE_DIR, "scripts"))

from kb_vector_store import KBVectorStore  # noqa: E402
import kb_build_index as kbi  # noqa: E402


def main():
    store = KBVectorStore()
    print(f"[建库] 向量目录: {store.vector_dir}")
    try:
        store.delete_collection("kb_characters")
        print("[清理] 旧 kb_characters 集合已删除")
    except Exception as error:
        print(f"[警告] 删除旧集合失败: {error}")
    kbi.index_characters(store)
    print(f"[完成] 向量统计: {store.get_stats()}")


if __name__ == "__main__":
    main()

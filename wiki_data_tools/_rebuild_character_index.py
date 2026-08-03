#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""只重建 kb_characters 向量索引（生日数据更新后）。
使用现有 KBVectorStore + DashScope text-embedding-v4 API，与生产环境一致。"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from kb_vector_store import KBVectorStore
from genshin_knowledge_base import 角色知识库


def main():
    print("=" * 50)
    print("  角色向量索引重建（含生日 + 派蒙）")
    print("=" * 50)

    store = KBVectorStore()

    # 删除旧集合
    try:
        store.delete_collection("kb_characters")
        print("[清理] 旧 kb_characters 集合已删除")
    except Exception as e:
        print(f"[警告] 删除旧集合失败: {e}")

    # 构建文档文本（与 kb_build_index.py 的 index_characters 保持一致，
    # 额外加入生日信息）
    docs = []
    metas = []
    ids_list = []

    for role in 角色知识库:
        role_id = role.get("角色ID", "")
        name = role.get("角色名称", "")

        # 构建可搜索文本：与正文相同的字段 + 生日
        parts = [name]
        if role.get("称号"):
            parts.append(role["称号"])
        if role.get("神之眼"):
            parts.append(f"{role['神之眼']}元素")
        if role.get("武器类型"):
            parts.append(role["武器类型"])
        if role.get("所属"):
            parts.append(role["所属"])
        if role.get("生日"):
            parts.append(f"生日{role['生日']}")
        if role.get("TAG"):
            parts.extend(role["TAG"])
        # 简介取前 300 字
        intro = role.get("简介", "")
        if intro:
            parts.append(intro[:300])

        doc_text = "。".join(parts)
        docs.append(doc_text)
        metas.append(role)
        ids_list.append(role_id)

    print(f"[建库] 索引角色（共 {len(角色知识库)} 位）...")

    # KBVectorStore.add() 内部调用 text-embedding-v4 API，自动分批嵌入
    store.add("kb_characters", ids_list, docs, metas)

    stats = store.get_stats()
    print(f"\n[完成] kb_characters 统计: {stats}")


if __name__ == "__main__":
    main()

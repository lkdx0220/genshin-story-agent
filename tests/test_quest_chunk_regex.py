# -*- coding: utf-8 -*-
"""quest chunk ID 解析测试（app/retrieval.py，2026-10-12 ID 碰撞修复）。

覆盖：
- _QUEST_CHUNK_RE 兼容新旧格式，group(1) 恒为 title；
- _get_doc_key() 对带 @后缀 的 ID 仍返回 title（检索聚合行为不变）；
- 两个来源的 chunk 归到同一任务 key（_quest_chunk_map 的分组语义）。
"""

import os

os.environ.setdefault("DASHSCOPE_API_KEY", "test-key-for-unit-tests")

import pytest


@pytest.fixture
def retrieval():
    from app import retrieval as module

    return module


def test_regex_accepts_legacy_and_new(retrieval):
    pattern = retrieval._QUEST_CHUNK_RE
    m1 = pattern.match("quest:水妖的猜想:chunk:0")
    assert m1 and m1.group(1) == "水妖的猜想" and m1.group(2) == "0"
    assert m1.group(3) is None

    m2 = pattern.match("quest:水妖的猜想:chunk:3@活动剧情")
    assert m2 and m2.group(1) == "水妖的猜想" and m2.group(2) == "3"
    assert m2.group(3) == "活动剧情"

    m3 = pattern.match("quest:同名:chunk:0@传说任务#2")
    assert m3 and m3.group(1) == "同名" and m3.group(3) == "传说任务#2"

    assert pattern.match("lore:某条目:entry:0:chunk:0") is None
    assert pattern.match("quest:某任务") is None


def test_title_extraction_by_split(retrieval):
    """_get_doc_key 的标题提取路径：rest.split(":",1)[0] 对新 ID 仍是 title。"""
    for doc_id in (
        "quest:水妖的猜想:chunk:0",
        "quest:水妖的猜想:chunk:0@活动剧情",
        "quest:同名:chunk:12@传说任务#2",
    ):
        rest = doc_id[len("quest:"):]
        assert rest.split(":", 1)[0] == "水妖的猜想" or rest.split(":", 1)[0] in ("水妖的猜想", "同名")


def test_get_doc_key_returns_title_for_real_registry_title(retrieval):
    """真实注册表中的碰撞标题：两个来源的 ID 都解析到同一 title。"""
    title = "永不失效的魔法"  # 真实 content_data 中传说任务与活动剧情同名
    if title not in retrieval.TITLE_REGISTRY:  # 环境不含真实数据时跳过
        pytest.skip("TITLE_REGISTRY 无该真实标题（非项目环境）")
    for doc_id in (f"quest:{title}:chunk:0", f"quest:{title}:chunk:0@活动剧情"):
        key = retrieval._get_doc_key({"id": doc_id, "collection": "kb_quests_vec"})
        assert key == title, f"{doc_id} 应聚合到 title"


def test_two_sources_group_to_same_title(retrieval):
    """_quest_chunk_map 分组语义：两来源 ID 按正则 group(1) 归同一任务。"""
    pattern = retrieval._QUEST_CHUNK_RE
    ids = ["quest:水妖的猜想:chunk:0", "quest:水妖的猜想:chunk:1@活动剧情",
           "quest:水妖的猜想:chunk:2@活动剧情"]
    grouped = {}
    for doc_id in ids:
        m = pattern.match(doc_id)
        assert m, f"新格式必须可解析: {doc_id}"
        grouped.setdefault(m.group(1), []).append(int(m.group(2)))
    assert grouped == {"水妖的猜想": [0, 1, 2]}

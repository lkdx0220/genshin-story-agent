# -*- coding: utf-8 -*-
"""离线流水线数据加载器的不变量测试（重构安全网）。

为什么是不变量而不是黄金快照：content_data 每天增量更新，快照会天天红；
这里守的是"形状与语义约束"，重构不得改变它们。
"""
import importlib

import pytest


@pytest.fixture(scope="module")
def pss():
    return importlib.import_module("preprocess_source_scope")


@pytest.fixture(scope="module")
def weg():
    return importlib.import_module("wiki_entry_graph")


RECORD_KEYS = {"record_id", "module", "title", "source", "text", "source_file", "source_index", "metadata"}


def test_load_local_records_shape_and_uniqueness(pss):
    records = pss.load_local_records()
    assert len(records) > 10000, f"记录数异常偏少：{len(records)}"
    assert all(RECORD_KEYS <= set(r) for r in records), "记录字段不完整"
    ids = [r["record_id"] for r in records]
    assert len(ids) == len(set(ids)), "record_id 必须全局唯一"
    modules = {r["module"] for r in records}
    assert {"lore", "quests", "npcs"} <= modules, f"关键模块缺失：{sorted(modules)}"
    assert all(isinstance(r["source_index"], int) for r in records), "source_index 必须是整数"


def test_load_local_records_lore_has_text(pss):
    records = [r for r in pss.load_local_records() if r["module"] == "lore"]
    assert records, "lore 模块不应为空"
    with_text = [r for r in records if r["text"].strip()]
    assert len(with_text) / len(records) > 0.9, "lore 记录正文覆盖率异常偏低"
    assert all(r["source_file"] == "content_data/lore.json" for r in records)


BWIKI_KEYS = {"title", "entry_type", "chapter", "act", "task", "region", "text", "meta_text", "file"}


def test_load_bwiki_entries_shape(weg):
    entries = weg._load_bwiki_entries()
    assert len(entries) > 1000, f"B 站条目数异常偏少：{len(entries)}"
    assert all(BWIKI_KEYS <= set(e) for e in entries), "条目字段不完整"
    assert all(str(e["title"]).strip() for e in entries), "标题不得为空"
    types = {str(e["entry_type"]) for e in entries}
    allowed = {
        "魔神任务", "传说任务", "世界任务", "委托任务", "活动剧情", "游逸旅闻",
        "伴月纪闻", "部族纪闻", "隐藏任务", "地图事件", "彩蛋剧情", "其他任务", "",
    }
    assert types <= allowed, f"entry_type 出现未预期取值：{sorted(types - allowed)}"
    dirty = [t for t in types if "\n" in t or "|" in t]
    assert not dirty, f"entry_type 混入描述文本（解析泄漏）：{dirty}"


def test_load_bwiki_entries_prefers_raw_text_and_falls_back(weg):
    """规则：优先用原始 text；text 为空时用 quests_processed 的 chunks 兜底。"""
    entries = weg._load_bwiki_entries()
    empty_text = [e for e in entries if not str(e["text"]).strip()]
    assert not empty_text, f"存在正文为空的条目（兜底规则失效）：{len(empty_text)} 条"

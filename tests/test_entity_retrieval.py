# -*- coding: utf-8 -*-
"""实体检索原语测试 v2（app/entity_retrieval.py）。

原则（来自人工抽查反馈，已固化为回归用例）：
1. 「任务概述 / 前情提要 / 词条元数据」写的是上一章剧情或页面信息，**不能**作为证据；
2. 「出场人物表」是权威参与关系：有表时以表为准，不在表里 = 不参与；
3. 双实体交集必须**双方都有强证据**（表或台词），否则会把"一方被转述提及"算成共同任务。
"""
import pytest


@pytest.fixture(scope="module")
def er():
    from app import entity_retrieval as module
    return module


def test_normalize_entity_strips_variants(er):
    base, ids = er.normalize_entity("香菱")
    assert base == "香菱" and ids
    assert er.normalize_entity("行秋【逸闻】")[0] == "行秋"
    assert er.normalize_entity("不存在的角色XYZ")[1] == []


def test_parse_cast_matches_wiki_source(er):
    """出场人物表解析必须与 wiki 原文逐字一致（对照实测样本）。"""
    by_id, _ = er._graph_and_index()
    entry = next((e for e in by_id.values() if "幼狮之章 第一幕" in str(e.get("title"))), None)
    assert entry is not None
    cast = er.parse_cast(str(entry.get("full_text") or ""))
    assert cast == {"凯亚", "温迪", "迪卢克", "丽莎", "安柏", "琴", "芭芭拉", "斯万"}


def test_parse_cast_drops_dirty_values(er):
    """实测存在 `出场人物：所属版本=1.0` 这类错位脏数据，必须丢弃而不是当成角色名。"""
    cast = er.parse_cast("出场人物：所属版本=1.0\n")
    assert cast == set()


def test_story_body_strips_prev_chapter_regions(er):
    """前情提要/任务概述/元数据行必须从正文里剔除。"""
    entry = {"story_text": "[任务概述] 前置任务 序章 [任务过程] *胡桃：你好呀。", "full_text": ""}
    body = er.story_body(entry)
    assert "前置任务" not in body and "任务概述" not in body
    assert "胡桃：你好呀" in body
    entry2 = {"full_text": "任务名称：X\n出场人物：胡桃、钟离\n任务编号：01\n*胡桃：正文台词。", "story_text": ""}
    body2 = er.story_body(entry2)
    assert "出场人物" not in body2 and "胡桃：正文台词" in body2


def test_evidence_never_from_prev_chapter(er):
    """回归：钟离 × 第一章第一幕 曾把「前置任务」元数据当证据（人工抽查第 12 条）。"""
    hit = next((h for h in er.entity_docs("钟离", top_k=999).hits if "浮世浮生千岩间" in h.title), None)
    if hit is not None:
        assert "前置任务" not in hit.evidence and "任务概述" not in hit.evidence
        assert hit.level in er.LEVEL_ORDER


def test_cast_authoritative_exclusion(er):
    """有出场人物表、但表里没有该角色 → 不算参与（权威排除）。"""
    by_id, _ = er._graph_and_index()
    with_cast = [e for e in by_id.values()
                 if e.get("entry_type") == "task" and er.parse_cast(str(e.get("full_text") or ""))]
    assert with_cast, "样本应存在带出场人物表的任务"
    entry = with_cast[0]
    cast = er.parse_cast(str(entry.get("full_text") or ""))
    outsider = "一个肯定不在表里的角色名"
    level, _ev, _c, size = er.grade_body(entry, [outsider], cast)
    assert level is None and size == len(cast)


def test_entity_docs_invariants(er):
    result = er.entity_docs("香菱", top_k=999)
    assert result.display == "香菱" and result.hits
    assert {h.level for h in result.hits} <= set(er.LEVEL_ORDER)
    assert all(h.title and h.task_type and h.evidence for h in result.hits)
    keys = [(-h.weight, -h.count) for h in result.hits]
    assert keys == sorted(keys)


def test_dedupe_folds_same_task_two_sources(er):
    titles = {h.title for h in er.entity_docs("香菱", top_k=999).hits}
    assert len([t for t in titles if "叶间泪" in t]) == 1


def test_intersect_requires_both_sides_strong(er):
    """回归：胡桃 ∩ 行秋 不得包含「往生堂三日无主（第二回）/ 第三回」——行秋在那里只是被转述提及。"""
    titles = [h.title for h in er.entity_intersect("胡桃", "行秋", top_k=999).hits]
    assert titles, "两人应有共同出场任务"
    assert not any("第二回" in t for t in titles), f"第二回应被排除：{titles}"
    assert not any("第三回" in t for t in titles), f"第三回应被排除：{titles}"
    # 留下的每条，双方都必须有强证据
    a = {h.title: h for h in er.entity_docs("胡桃", top_k=999).hits}
    b = {h.title: h for h in er.entity_docs("行秋", top_k=999).hits}
    floor = er.LEVEL_ORDER[er.LEVEL_LINE]
    for t in titles:
        assert min(a[t].weight, b[t].weight) >= floor


def test_negative_result_carries_coverage_note(er):
    result = er.entity_docs("不存在的角色XYZ")
    assert result.hits == []
    assert "覆盖度" in result.coverage_note and "出场人物表" in result.coverage_note
    assert "未检出" in er.format_entity_docs(result)


def test_format_entity_docs_shape(er):
    text = er.format_entity_docs(er.entity_docs("香菱", top_k=3))
    assert "实体检索" in text and "证据：" in text and "覆盖度" in text

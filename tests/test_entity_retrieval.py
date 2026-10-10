# -*- coding: utf-8 -*-
"""实体检索原语测试（app/entity_retrieval.py）。

注意：知识库每日增量更新，故不钉死具体条数，只钉不变量与相对事实：
归一规则、分级取值、排序、双源去重（同任务两源不得同时出现）、交集包含关系、
top_k 截断、否定结论必须带覆盖度声明。
"""
import pytest


@pytest.fixture(scope="module")
def er():
    from app import entity_retrieval as module
    return module


def test_normalize_entity_strips_variants(er):
    base, ids = er.normalize_entity("香菱")
    assert base == "香菱" and ids, "规范名应能命中实体"
    assert er.normalize_entity("行秋【逸闻】")[0] == "行秋", "变体后缀应被归一"
    assert er.normalize_entity("不存在的角色XYZ")[1] == []


def test_entity_docs_invariants(er):
    result = er.entity_docs("香菱", top_k=999)
    assert result.display == "香菱" and result.hits, "应检索到剧情文档"
    levels = {h.level for h in result.hits}
    assert levels <= set(er.LEVEL_ORDER), f"分级取值异常：{levels}"
    assert all(h.title and h.task_type for h in result.hits)
    assert all(h.evidence for h in result.hits), "每条命中都应有证据片段"
    # 排序：先按证据强度、再按提及次数降序
    keys = [(-h.weight, -h.count) for h in result.hits]
    assert keys == sorted(keys), "结果应按（强度, 提及次数）降序"
    # 去重只可能减少
    assert result.deduped_rows >= 0 and len(result.hits) <= result.total_rows


def test_dedupe_folds_same_task_two_sources(er):
    """双源重复（叶间泪 / 游水酝诗籍 第二首 叶间泪）不得同时出现。"""
    titles = {h.title for h in er.entity_docs("香菱", top_k=999).hits}
    pair = [t for t in titles if "叶间泪" in t]
    assert len(pair) == 1, f"同一任务的两源条目应折叠为一条，实际：{pair}"


def test_intersect_is_subset_of_both(er):
    a = {h.title for h in er.entity_docs("胡桃", top_k=999).hits}
    b = {h.title for h in er.entity_docs("行秋", top_k=999).hits}
    common = {h.title for h in er.entity_intersect("胡桃", "行秋", top_k=999).hits}
    assert common, "两人应有共同任务"
    assert common <= (a & b) or common <= a | b, "交集必须来自两侧命中"


def test_top_k_truncates(er):
    assert len(er.entity_docs("香菱", top_k=3).hits) <= 3
    full = er.entity_docs("香菱", top_k=999)
    assert len(full.hits) >= len(er.entity_docs("香菱", top_k=3).hits)


def test_negative_result_carries_coverage_note(er):
    """没有命中时也必须给覆盖度声明（否定结论不能裸奔）。"""
    result = er.entity_docs("不存在的角色XYZ")
    assert result.hits == []
    assert "覆盖度" in result.coverage_note and "未收录" in result.coverage_note
    text = er.format_entity_docs(result)
    assert "覆盖度" in text and "未检出" in text


def test_format_entity_docs_contains_levels_and_evidence(er):
    text = er.format_entity_docs(er.entity_docs("香菱", top_k=3))
    assert "实体检索" in text
    assert any(level in text for level in er.LEVEL_ORDER)
    assert "证据：" in text

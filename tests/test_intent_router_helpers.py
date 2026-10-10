# -*- coding: utf-8 -*-
"""意图路由纯函数测试（Stage-0 实体锚定链路）——提升覆盖率并钉住锚定行为。"""
import pytest


@pytest.fixture(scope="module")
def ir():
    import intent_router as module
    return module


def test_strip_brackets(ir):
    assert ir._strip_brackets("《裁叶萃光》") == "裁叶萃光"
    assert ir._strip_brackets("「往生堂」") == "往生堂"
    assert ir._strip_brackets("  空格  名  ") == "空格名"
    assert ir._strip_brackets("") == ""


def test_generate_short_names(ir):
    assert ir._generate_short_names("裁叶萃光") == ["裁叶", "裁叶萃", "裁叶萃光"]
    assert ir._generate_short_names("胡桃") == ["胡桃"]
    assert ir._generate_short_names("") == []
    # 装饰符号先剥掉再切短名
    assert ir._generate_short_names("《裁叶萃光》")[0] == "裁叶"


def test_anchor_state_add_dedup(ir):
    state = ir._new_anchor_state()
    ir._anchor_add(state, "胡桃", ["角色"])
    ir._anchor_add(state, "胡桃", ["任务"])  # 同实体合并标签
    assert state["anchored"]["胡桃"] == {"角色", "任务"}
    assert state["labels_ordered"] == ["任务", "角色"] or set(state["labels_ordered"]) == {"角色", "任务"}
    assert state["labels_set"] == {"角色", "任务"}


def test_anchor_truncate_keeps_top3_and_drops_empty(ir):
    state = ir._new_anchor_state()
    ir._anchor_add(state, "实体A", ["l1"])
    ir._anchor_add(state, "实体B", ["l2"])
    ir._anchor_add(state, "实体C", ["l3"])
    ir._anchor_add(state, "实体D", ["l4"])
    ir._anchor_add(state, "实体E", ["l5"])
    anchored = ir._anchor_truncate(state)
    assert len(state["labels_ordered"]) == 3, "候选标签应截断到 3 个"
    # 只保留仍带有保留标签的实体，且每个实体的标签都被裁剪
    names = [n for n, _ in anchored]
    assert names and all(n for n in names)
    for _name, labels in anchored:
        assert labels and set(labels) <= set(state["labels_ordered"])


def test_format_grounding(ir):
    empty = ir._format_grounding([])
    assert "未检测到" in empty
    text = ir._format_grounding([("胡桃", ["角色"])])
    assert "「胡桃」" in text and "角色" in text

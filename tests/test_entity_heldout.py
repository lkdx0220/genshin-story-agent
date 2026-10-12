# -*- coding: utf-8 -*-
"""关系类 held-out 题（第 3 步 3b）：用集合命中做确定性验收，不依赖 LLM 判分。

每题都锚在**人工抽查确认过的真实事实**上（含一条负例：行秋并未出现在「往生堂三日无主 第二回」）。
"""
import pytest


@pytest.fixture(scope="module")
def tool():
    from app.tools.entity import entity_query_tool
    return entity_query_tool


def _call(tool, **kw):
    return tool.invoke(kw)


def _conclusion(text):
    part = text.split("【结论区")[1]
    return part.split("【线索区")[0] if "【线索区" in part else part


def test_heldout_hutao_xingqiu_common(tool):
    """胡桃与行秋的共同剧情：必须命中「人前人后」，且**不得**出现「往生堂三日无主」（人工抽查确认行秋不在场）。"""
    text = _call(tool, entity="胡桃", relation="co_occur", target="行秋", top_k=999)
    assert "人前人后" in text
    assert "往生堂三日无主" not in text, "行秋在该幕只是被转述提及，不得算共同出场"


def test_heldout_xiangling_legend(tool):
    text = _call(tool, entity="香菱", task_type="传说任务", top_k=999)
    strong = _conclusion(text)
    assert ("六情通" in strong) or ("料理对决" in strong)


def test_heldout_zhongli_archon(tool):
    text = _call(tool, entity="钟离", task_type="魔神任务", top_k=999)
    assert "迫近的客星" in _conclusion(text)


def test_heldout_hutao_archon_is_only_weak(tool):
    """负例：胡桃在魔神任务里只有弱证据 → 结论区必须为空（只能进线索区）。"""
    text = _call(tool, entity="胡桃", task_type="魔神任务", top_k=999)
    strong = _conclusion(text)
    assert "弱·仅文字提及" not in strong and "中·有动作" not in strong
    assert "（无强证据命中）" in strong


def test_heldout_nonexistent_entity(tool):
    text = _call(tool, entity="不存在的角色XYZ")
    assert "覆盖度：" in text
    assert "未收录" in text


def test_heldout_ganyu_keqing(tool):
    text = _call(tool, entity="甘雨", relation="co_occur", target="刻晴", top_k=999)
    assert "甘雨 ∩ 刻晴" in text
    assert ("强·出场人物表" in text) or ("（无强证据命中）" in text)


def test_heldout_relation_enum_rejected(tool):
    with pytest.raises(Exception):
        _call(tool, entity="胡桃", relation="bogus")

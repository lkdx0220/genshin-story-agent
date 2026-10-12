# -*- coding: utf-8 -*-
"""实体查询工具的契约测试（第 ③ 步 3a）。

守的是三条"人工抽查换来的规矩"：
1. 弱/中证据不得进入结论区；
2. 否定结论必须带覆盖度声明；
3. 非法参数必须报错（不静默降级）——否则 3b 里被 LLM 传错参数会悄悄给错结果。
"""
import pytest


@pytest.fixture(scope="module")
def tool():
    from app.tools import entity as module
    return module


def _sections(text):
    """把渲染结果切成 结论区 / 线索区 / 覆盖度。"""
    strong = text.split("【结论区")[1].split("【线索区")[0] if "【线索区" in text else text.split("【结论区")[1]
    weak = text.split("【线索区")[1].split("覆盖度：")[0] if "【线索区" in text else ""
    return strong, weak


def test_participated_basic(tool):
    out = tool.entity_query("香菱", top_k=5)
    assert "实体查询「香菱」" in out
    assert "结论区" in out and "覆盖度：" in out
    strong, weak = _sections(out)
    assert "强·" in strong, "结论区应有强证据"
    if weak.strip():
        assert "不可" in weak, "线索区必须标注不可作结论"
        assert "强·" not in weak


def test_weak_never_in_conclusion(tool):
    """核心契约：中/弱证据不得出现在结论区。"""
    out = tool.entity_query("香菱", top_k=999)
    strong, _weak = _sections(out)
    assert "弱·仅文字提及" not in strong
    assert "中·有动作" not in strong


def test_cooccur_requires_target(tool):
    with pytest.raises(ValueError):
        tool.entity_query("胡桃", relation="co_occur")
    out = tool.entity_query("胡桃", relation="co_occur", target="行秋", top_k=5)
    assert "胡桃 ∩ 行秋" in out
    assert "共同出场" in out


def test_negative_result_has_coverage(tool):
    out = tool.entity_query("不存在的角色XYZ")
    assert "未检出" in out or "无强证据" in out
    assert "覆盖度：" in out and "出场人物表" in out


def test_invalid_args_raise(tool):
    with pytest.raises(ValueError):
        tool.entity_query("", )
    with pytest.raises(ValueError):
        tool.entity_query("胡桃", relation="nonsense")
    with pytest.raises(ValueError):
        tool.entity_query("胡桃", task_type="不存在的类型")
    with pytest.raises(ValueError):
        tool.entity_query("胡桃", min_level="超强")
    with pytest.raises(ValueError):
        tool.entity_query("胡桃", top_k=0)


def test_task_type_filter(tool):
    out = tool.entity_query("胡桃", task_type="魔神任务", top_k=999)
    assert "实体查询「胡桃」" in out
    for line in out.splitlines():
        if line.startswith(("1.", "2.", "3.", "4.", "5.")) and "（" in line:
            assert "魔神任务" in line or "任务" in line


def test_description_exists(tool):
    assert "出场人物表" in tool.ENTITY_QUERY_DESCRIPTION
    assert "覆盖度" in tool.ENTITY_QUERY_DESCRIPTION


def test_cast_of_task(tool):
    """任务 → 角色（正跳）：给任务名返回该任务出场人物。"""
    out = tool.entity_query("", relation="cast_of_task", target="人前人后")
    assert "人前人后" in out
    assert "胡桃" in out


def test_cast_of_task_requires_target(tool):
    with pytest.raises(ValueError):
        tool.entity_query("", relation="cast_of_task")

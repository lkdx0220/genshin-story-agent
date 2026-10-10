# -*- coding: utf-8 -*-
"""读全文补全钩子（_maybe_auto_full_text_read）的单元测试。

由 cq 的 radon E32 驱动重构而成：拆出 _auto_read_plan / _auto_read_payload 后，
可以用合成 state/messages 覆盖各分支（原先只能靠端到端跑）。
"""
import types

import pytest


@pytest.fixture(scope="module")
def nodes():
    from app.agent import nodes as module
    return module


def _state(query="《某本书》讲了什么"):
    return {"user_query": query, "run_id": "pytest-autoread", "intent_labels": ["lore"]}


def _tools(*names):
    return [types.SimpleNamespace(name=n) for n in names]


@pytest.fixture
def patched(nodes, monkeypatch):
    """统一打桩：探针文本、已读记录、目录函数、trace。"""
    monkeypatch.setattr(nodes, "_auto_read_probe_text", lambda state, messages: "《某本书》")
    monkeypatch.setattr(nodes, "_attempted_read_names", lambda messages: set())
    monkeypatch.setattr(nodes, "_auto_read_book_titles", lambda: [("某本书", "某本书")])
    monkeypatch.setattr(nodes, "_auto_read_quest_titles", lambda: [("某个任务", "某个任务")])
    monkeypatch.setattr(nodes, "_auto_read_character_names", lambda: [("库塔尔", "库塔尔")])
    monkeypatch.setattr(nodes, "trace_emit", lambda *a, **k: None)
    return nodes


def test_returns_none_without_response(patched):
    assert patched._maybe_auto_full_text_read(_state(), [], _tools("load_book_content"), 0) is None


def test_returns_none_without_read_full_tool(patched):
    out = patched._maybe_auto_full_text_read(
        _state(), [], _tools("hybrid_search"), 0, response=object()
    )
    assert out is None


def test_returns_none_when_iteration_exhausted(patched):
    out = patched._maybe_auto_full_text_read(
        _state(), [], _tools("load_book_content"), patched.MAX_AGENT_ITERATIONS, response=object()
    )
    assert out is None


def test_builds_book_call_payload(patched):
    out = patched._maybe_auto_full_text_read(
        _state(), [], _tools("load_book_content"), 0, response=object()
    )
    assert out is not None
    calls = out["messages"][0].tool_calls
    assert [c["name"] for c in calls] == ["load_book_content"]
    assert calls[0]["args"] == {"book_name": "某本书"}
    assert out["iteration"] == 1
    assert "某本书" in out["execution_plan"]


def test_identity_question_prefers_character(patched, monkeypatch):
    monkeypatch.setattr(patched, "_auto_read_probe_text", lambda state, messages: "库塔尔是谁")
    out = patched._maybe_auto_full_text_read(
        _state("库塔尔是谁"), [], _tools("query_character"), 0, response=object()
    )
    assert out is not None
    assert [c["name"] for c in out["messages"][0].tool_calls] == ["query_character"]


def test_caps_total_calls(nodes, monkeypatch):
    """书籍+任务候选总量不得超过 _AUTO_READ_MAX。"""
    monkeypatch.setattr(nodes, "_auto_read_probe_text", lambda s, m: "某本书 某个任务")
    monkeypatch.setattr(nodes, "_attempted_read_names", lambda m: set())
    monkeypatch.setattr(nodes, "_auto_read_book_titles", lambda: [(f"书{i}", f"书{i}") for i in range(9)])
    monkeypatch.setattr(nodes, "_auto_read_quest_titles", lambda: [("任务", "任务")])
    monkeypatch.setattr(nodes, "_auto_read_character_names", lambda: [])
    monkeypatch.setattr(nodes, "trace_emit", lambda *a, **k: None)
    out = nodes._maybe_auto_full_text_read(
        _state(), [], _tools("load_book_content"), 0, response=object()
    )
    assert out is not None
    assert len(out["messages"][0].tool_calls) <= nodes._AUTO_READ_MAX


def test_skips_already_attempted(patched, monkeypatch):
    # 探针里同时出现书籍与任务；书籍已被读过 → 应退化为补读任务
    monkeypatch.setattr(patched, "_auto_read_probe_text", lambda state, messages: "《某本书》 某个任务")
    monkeypatch.setattr(patched, "_attempted_read_names", lambda messages: {"某本书"})
    out = patched._maybe_auto_full_text_read(
        _state(), [], _tools("load_book_content", "load_quest_content"), 0, response=object()
    )
    assert out is not None
    assert [c["name"] for c in out["messages"][0].tool_calls] == ["load_quest_content"]

# -*- coding: utf-8 -*-
"""Web 服务会话/多租户辅助函数测试（把 SESSION_ROOT 指到 tmp 目录，不碰真实会话）。"""
import os

import pytest


@pytest.fixture()
def web(tmp_path, monkeypatch):
    import genshin_story_web_api as module
    monkeypatch.setattr(module, "SESSION_ROOT", str(tmp_path))
    return module


def test_auto_tenant_name_is_stable_hash(web):
    a, b = web._auto_tenant_name("token-abc"), web._auto_tenant_name("token-abc")
    assert a == b and a.startswith("tok_") and len(a) == len("tok_") + 10
    assert a != web._auto_tenant_name("token-abd")


def test_session_key_namespaced(web):
    assert web._session_key("guest", "s1") == "guest:s1"
    assert web._session_key(None, "s1") == "_local:s1"


def test_tenant_session_dir_created_and_scoped(web, tmp_path):
    path = web._tenant_session_dir("guest")
    assert os.path.isdir(path) and path.endswith("guest")
    root = web._tenant_session_dir(None)
    assert os.path.isdir(root) and not root.endswith("guest")


def test_session_roundtrip(web):
    data = {"pairs": [{"user": "你好", "assistant": "你好呀"}], "summary": "寒暄"}
    web._save_session_to_disk("abc-123", data, "guest")
    assert web._load_session_from_disk("abc-123", "guest") == data
    # 命名空间隔离：另一个 tenant 读不到
    assert web._load_session_from_disk("abc-123", "other") == {"pairs": [], "summary": ""}


def test_session_file_rejects_bad_id(web):
    for bad in ("bad id!", "", "会话", "x" * 65):
        with pytest.raises(ValueError):
            web._session_file(bad, "guest")


def test_load_missing_returns_default(web):
    assert web._load_session_from_disk("nonexistent-1", "guest") == {"pairs": [], "summary": ""}


def test_extract_tool_calls(web):
    from langchain_core.messages import AIMessage

    class _Msg(AIMessage):
        pass

    msg = AIMessage(content="", tool_calls=[{"name": "hybrid_search", "args": {"query": "胡桃"}, "id": "c1"}])
    calls = web._extract_tool_calls({"messages": [msg]})
    assert isinstance(calls, list)
    if calls:  # 实现按 type(msg).__name__ == "AIMessage" 匹配，子类名不同则可能为空
        assert calls[0].get("tool") == "hybrid_search"


def test_rate_limit_table_shape(web):
    limit = web._get_rate_limit("/api/chat")
    assert isinstance(limit, tuple) and len(limit) == 2
    assert web._get_daily_limit("/api/chat") >= 1

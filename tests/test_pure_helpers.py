# -*- coding: utf-8 -*-
"""纯函数单测：把易回归、无需外部依赖的运行时逻辑钉住（提升覆盖率的主要来源）。"""
import pytest


# ====== 查询消毒（app/retrieval.py）======
@pytest.fixture(scope="module")
def retrieval():
    from app import retrieval as module
    return module


def test_sanitize_prompt_text_strips_control_chars_only(retrieval):
    assert retrieval.sanitize_prompt_text("a\nb\tc") == "a b c"
    assert retrieval.sanitize_prompt_text("问（含幕间）任务") == "问（含幕间）任务"  # 括号保留
    assert retrieval.sanitize_prompt_text("  x  ") == "x"
    assert retrieval.sanitize_prompt_text("") == ""
    assert retrieval.sanitize_prompt_text(None) == ""


def test_sanitize_query_alias_scope(retrieval):
    # 别名扫描口径：剥括号 + 去尾标点 + 限长 200
    assert retrieval._sanitize_query("问（含幕间）任务") == "问任务"
    assert retrieval._sanitize_query("问号结尾？？？") == "问号结尾"
    assert len(retrieval._sanitize_query("长" * 500)) == 200
    assert retrieval._sanitize_query("") == ""


def test_is_compound_hit(retrieval):
    # "风龙" 出现在 "风龙废墟" 中 → 复合词命中（相邻 CJK）
    assert retrieval._is_compound_hit("风龙废墟", "风龙", 0) is True
    # 非 CJK 相邻（标点）→ 不算复合词
    assert retrieval._is_compound_hit("风龙!之力", "风龙", 0) is False
    assert retrieval._is_compound_hit("问风龙", "风龙", 1) is True  # 前面是 CJK
    # 注意：该启发式只看相邻字符是否 CJK，"风龙的力量" 会判为复合词（已知粗粒度）


# ====== RRF 融合（app/retrieval.py）======
def test_rrf_fusion_prefers_dual_hit(retrieval):
    kw = [{"id": "a"}, {"id": "b"}]
    vec = [{"id": "b"}, {"id": "c"}]
    fused = dict(retrieval._rrf_fusion(kw, vec, k=60))
    assert fused["b"] > fused["a"] and fused["b"] > fused["c"], "两路都命中的文档应排最前"
    assert pytest.approx(fused["b"]) == 1 / 62 + 1 / 61


def test_rrf_fusion_uses_doc_key_fallback(retrieval):
    """_get_doc_key 对任意检索结果都必须返回 str（不能抛异常）。"""
    for item in ({"id": "x1", "collection": "kb_lore"}, {"collection": "kb_lore"}, {"id": "quest:某任务:chunk:0", "collection": "kb_quests_vec"}):
        assert isinstance(retrieval._get_doc_key(item), str)


# ====== LLM 重试/降级策略（app/llm.py）======
@pytest.fixture(scope="module")
def llm():
    from app import llm as module
    return module


class _HttpError(Exception):
    def __init__(self, status_code):
        super().__init__(f"http {status_code}")
        self.status_code = status_code


def test_status_code_and_summary(llm):
    assert llm._status_code(_HttpError(429)) == 429
    assert llm._status_code(ValueError("boom")) is None
    summary = llm._error_summary(_HttpError(500))
    assert "_HttpError" in summary and "500" in summary
    assert "Authorization" not in llm._error_summary(_HttpError(401))  # 不带供应方原文


def test_should_switch_only_infra_errors(llm):
    assert llm._should_switch(_HttpError(401)) is True
    assert llm._should_switch(_HttpError(503)) is True
    assert llm._should_switch(ValueError("业务参数错误")) is False


def test_should_retry_semantics(llm):
    assert llm._should_retry(_HttpError(429), attempt=0) is True
    assert llm._should_retry(_HttpError(400), attempt=0) is False  # 确定性 4xx 不重试
    assert llm._should_retry(TimeoutError("timeout"), attempt=0) is True
    # 完全无法识别的异常只给一次机会
    assert llm._should_retry(KeyError("weird"), attempt=0) is True
    assert llm._should_retry(KeyError("weird"), attempt=1) is False


def test_retry_wait_seconds(llm):
    assert llm._retry_wait_seconds(Exception("rate limit exceeded"), 0) == (5, True)
    assert llm._retry_wait_seconds(Exception("429 too many requests"), 1) == (10, True)
    assert llm._retry_wait_seconds(Exception("connection reset"), 0) == (2, False)


# ====== Web 服务工具函数（genshin_story_web_api.py）======
@pytest.fixture(scope="module")
def web_api():
    import genshin_story_web_api as module
    return module


def test_env_int_falls_back(web_api, monkeypatch):
    monkeypatch.delenv("DSH_TEST_INT", raising=False)
    assert web_api._env_int("DSH_TEST_INT", 7) == 7
    monkeypatch.setenv("DSH_TEST_INT", "12")
    assert web_api._env_int("DSH_TEST_INT", 7) == 12
    monkeypatch.setenv("DSH_TEST_INT", "abc")
    assert web_api._env_int("DSH_TEST_INT", 7) == 7
    monkeypatch.setenv("DSH_TEST_INT", "0")
    assert web_api._env_int("DSH_TEST_INT", 7) == 7  # 非正数退回默认


@pytest.mark.parametrize("bad", ["", "bad id!", "会话", "a" * 200])
def test_session_id_rejects_bad(web_api, bad):
    assert web_api._valid_session_id(bad) is False


def test_session_id_accepts_uuid_like(web_api):
    assert web_api._valid_session_id("a1b2c3d4-e5f6-7890-abcd-ef1234567890") is True


# ====== L3 全景取全文（app/agent/l3_panorama.py）======
def test_find_panorama_text():
    from langchain_core.messages import HumanMessage, ToolMessage
    from app.agent import l3_panorama

    header = l3_panorama._FULL_TEXT_HEADER
    plain = HumanMessage(content="无关内容")
    hit = ToolMessage(content=f"{header} 任务正文", tool_call_id="t1")
    assert l3_panorama._find_panorama_text([plain]) == ""
    assert l3_panorama._find_panorama_text([plain, hit]).startswith(header)
    # content 为 list 时不得抛 AttributeError（回归护栏）；str() 后不以 header 开头 → 返回空串
    weird = ToolMessage(content=[header, "分段正文"], tool_call_id="t2")
    assert l3_panorama._find_panorama_text([weird]) == ""


def test_coref_window_and_trigger():
    from app.agent import coref

    history = [{"user": "库塔尔是谁？", "assistant": "她是愚人众「少女」"}, {"user": "还有呢", "assistant": "……"}]
    window = coref._recent_window({"conversation_history": history})
    assert window.startswith("[") and "库塔尔是谁" in window, "窗口应是 JSON 数组且含历史"
    assert coref._recent_window({"conversation_history": []}) == ""
    # 触发条件：有历史 + 命中指代词
    assert coref.needs_resolution({"user_query": "她是谁？", "conversation_history": history}) is True
    assert coref.needs_resolution({"user_query": "她是谁？", "conversation_history": []}) is False
    assert coref.needs_resolution({"user_query": "胡桃的传说任务", "conversation_history": history}) is False

"""Offline contracts: real sanitizer, alias table, rewrite and LangGraph reducer.

Only provider/config/data/tool startup boundaries are substituted. No live keys,
model transports, vector stores, services or installed project are accessed.
"""

import importlib
import re
import socket
import sys
from copy import deepcopy
from pathlib import Path
from threading import Event
from types import ModuleType, SimpleNamespace

import pytest
from langchain_core.messages import ToolMessage

ROOT = Path(__file__).resolve().parents[1]
F5 = "外号大娘的角色是谁？"


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    counts = {
        "alias": 0,
        "coref": 0,
        "assess": 0,
        "plan": 0,
        "answer": 0,
        "tools": 0,
        "network": 0,
        "summary": 0,
    }
    state = SimpleNamespace(verdict="accepted", coref=None)

    def blocked(*_args, **_kwargs):
        counts["network"] += 1
        raise AssertionError("external_transport_blocked")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)
    import dotenv
    import httpx
    import requests

    monkeypatch.setattr(httpx.Client, "send", blocked)
    monkeypatch.setattr(httpx.AsyncClient, "send", blocked)
    monkeypatch.setattr(requests.Session, "send", blocked)
    monkeypatch.setattr(dotenv, "load_dotenv", blocked)
    monkeypatch.setenv("LLM_WIKI_MODE", "agent_evidence")
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "false")
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    monkeypatch.setenv("API_TOKEN", "")
    monkeypatch.setenv("API_TOKENS", "")
    monkeypatch.setenv("SESSION_DIR", str(tmp_path / "sessions"))

    def module(name, **values):
        value = ModuleType(name)
        value.__dict__.update(values)
        monkeypatch.setitem(sys.modules, name, value)
        return value

    module(
        "app.config",
        MAX_AGENT_ITERATIONS=10,
        MAX_PLAN_RETRIES=2,
        MAX_FAST_ITERATIONS=2,
        RECENT_TURNS=3,
        CONTENT_DIR=str(tmp_path / "data"),
        PROMPTS_DIR=str(ROOT / "prompts"),
        QWEN_FALLBACK_API_KEY="",
    )

    def alias_call(messages, **_kwargs):
        counts["alias"] += 1
        if state.verdict == "timeout":
            raise TimeoutError("offline injection")
        n = len(re.findall('"序号"', messages[0].content))
        decision = "REPLACE" if state.verdict == "accepted" else "KEEP"
        return SimpleNamespace(
            content="\n".join(f"{i} {decision}" for i in range(1, n + 1))
            if n
            else decision
        )

    def coref_call(*_args, **_kwargs):
        counts["coref"] += 1
        return SimpleNamespace(content=state.coref or "她是谁？")

    def forbidden_model(*_args, **_kwargs):
        raise AssertionError("unexpected_model_boundary")

    spy = SimpleNamespace(
        invoke=forbidden_model,
        stream=forbidden_model,
        model_name="offline",
        model_kwargs={},
        max_tokens=1,
    )
    spy.bind_tools = lambda *_a, **_k: spy
    names = (
        "llm",
        "plan_llm",
        "plan_llm_l2",
        "assess_llm",
        "answer_llm_medium",
        "answer_llm_deep",
        "answer_llm_l3",
        "mechanism_judge_llm",
    )
    module(
        "app.llm",
        **dict.fromkeys(names, spy),
        alias_judge_llm=SimpleNamespace(invoke=alias_call),
        coref_llm=SimpleNamespace(invoke=coref_call),
        llm_invoke_with_retry=forbidden_model,
        _select_answer_llm=lambda *_a, **_k: spy,
    )
    tool_module = module(
        "app.tools", tools=[], tools_by_name={}, MELTDOWN_TRIGGER_TOOLS=set()
    )
    tool_module.__path__ = []
    module("app.tools.search", search_world=forbidden_model)
    module("app.tools.query", find_similar_quest_names=forbidden_model)
    data_names = (
        "角色知识库",
        "地区知识库",
        "任务知识库",
        "武器知识库",
        "圣遗物知识库",
        "素材知识库",
        "_npcs_data",
    )
    module(
        "app.data",
        **{name: {} for name in data_names},
        _match_all_in=forbidden_model,
        _load_content_json=forbidden_model,
        _normalize_for_match=lambda q: q,
    )
    module(
        "intent_router",
        route_intent=forbidden_model,
        get_tools_for_intent=forbidden_model,
        get_pseudo_legendary_note=lambda _q: "",
    )
    module(
        "wiki_entry_graph",
        WikiEntryGraph=forbidden_model,
        DEFAULT_OUTPUT=tmp_path / "absent.json",
    )
    targets = (
        "app.workflow",
        "app.schema",
        "app.agent",
        "app.agent.nodes",
        "app.agent.executor",
        "app.agent.coref",
        "app.agent.alias_identity",
        "app.retrieval",
        "genshin_story_web_api",
    )
    for name in targets:
        monkeypatch.delitem(sys.modules, name, raising=False)
    retrieval = importlib.import_module("app.retrieval")
    nodes = importlib.import_module("app.agent.nodes")
    identity = importlib.import_module("app.agent.alias_identity")
    workflow = importlib.import_module("app.workflow")
    from character_aliases import ALIAS_MAP

    assert ALIAS_MAP["大娘"] == "兹白" and ALIAS_MAP["岩王爷"] == "钟离"
    assert ALIAS_MAP["巴巴托斯"] == "温迪"
    assert Path(retrieval.__file__).resolve() == ROOT / "app/retrieval.py"

    def request(query=F5, **extra):
        return dict(
            raw_query=query, user_query=retrieval.sanitize_prompt_text(query), **extra
        )

    def rewritten(query=F5, **extra):
        initial = request(query, **extra)
        current = {**initial, **identity.initialize_request(initial)}
        current.update(nodes.rewrite_query(current))
        return current

    yield SimpleNamespace(
        nodes=nodes,
        identity=identity,
        workflow=workflow,
        retrieval=retrieval,
        counts=counts,
        state=state,
        request=request,
        rewritten=rewritten,
        module=module,
        aliases=ALIAS_MAP,
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
    )
    assert counts["network"] == 0
    for name in targets:
        sys.modules.pop(name, None)


@pytest.mark.parametrize(
    "question,expected",
    [
        ("岩王爷是谁？", ("岩王爷",)),
        ("请问，巴巴托斯是哪位?", ("巴巴托斯",)),
        ("岩王爷和巴巴托斯是什么人", ("岩王爷", "巴巴托斯")),
        (F5, ("大娘",)),
        ("绰号大娘的角色是谁", ("大娘",)),
        ("是谁", None),
        ("岩王爷和是谁", None),
        ("岩王爷是谁？？", None),
        ("钟离是谁", None),
        ("火神是谁", None),
        ("外号叫大娘的角色是谁", None),
        ("“大娘”是谁", None),
        ("岩王爷是谁（再讲经历）", None),
        ("岩王爷是谁？不要只答身份", None),
        ("岩王爷和不存在是谁", None),
        ("岩王爷是谁？" + "尾部任务" * 60, None),
        ("岩王爷\n是谁", None),
        ("岩王爷是谁，并比较她的经历", None),
        ("岩王爷不是谁", None),
    ],
)
def test_full_question_grammar(runtime, question, expected):
    assert runtime.identity.parse_identity(question, runtime.aliases) == expected
    assert runtime.identity.GRAMMAR_VERSION == "alias-identity-grammar-v1"
    assert runtime.identity.GRAMMAR_EXTENSIONS == ()


def test_ambiguous_full_parse(runtime):
    aliases = {"甲": "A", "乙": "B", "甲和乙": "C"}
    assert runtime.identity.parse_identity("甲和乙是谁", aliases) is None
    assert runtime.identity.parse_identity("甲是谁", {"甲": ["A", "B"]}) is None


@pytest.mark.parametrize("decision", ["accepted", "rejected", "timeout", "short"])
def test_f5_alias_decision_outcomes(runtime, decision):
    runtime.state.verdict = decision
    if decision == "short":
        runtime.monkeypatch.setattr(
            runtime.nodes, "_judge_alias_sandbox_batch", lambda _c: []
        )
    current = runtime.rewritten()
    result = runtime.identity.alias_identity_gate(current)
    if decision == "accepted":
        assert "兹白" in result["final_response"]
        assert result["route_reason"] == "alias_identity_complete"
        assert result["route_diagnostics"]["wiki_validation"] == "not_applicable"
        assert runtime.counts["alias"] == 1
    else:
        assert result == {} and current["user_query"] == F5
        assert current["alias_pairs"] is None
    assert sum(runtime.counts[k] for k in ("assess", "plan", "answer", "tools")) == 0


@pytest.mark.parametrize(
    "change",
    [
        "nonce",
        "question",
        "pairs",
        "map",
        "binding",
        "no_raw",
        "clean",
        "unicode",
        "control",
    ],
)
def test_rewrite_binding_and_stale_input(runtime, change):
    current = runtime.rewritten()
    if change == "nonce":
        current["request_nonce"] = "old"
    if change == "question":
        current["user_query"] += "附加请求"
    if change == "pairs":
        current["alias_pairs"] = [("大娘", "错误")]
    if change == "map":
        runtime.monkeypatch.setitem(runtime.aliases, "大娘", "错误")
    if change == "binding":
        current["alias_binding"]["question_digest"] = "old"
    if change == "no_raw":
        current.pop("raw_query")
    if change == "clean":
        current["raw_query"] += "（隐藏尾部）"
    if change == "unicode":
        current["raw_query"] = "\u3000" + F5
    if change == "control":
        current["raw_query"] = F5 + "\n"
    assert runtime.identity.alias_identity_gate(current) == {}


def test_ascii_outer_spaces_and_short_multi_result(runtime):
    current = runtime.rewritten("  " + F5 + " ")
    assert (
        runtime.identity.alias_identity_gate(current)["termination_kind"]
        == "alias_identity"
    )
    runtime.monkeypatch.setattr(
        runtime.nodes, "_judge_alias_sandbox_batch", lambda _c: [True]
    )
    multi = runtime.rewritten("岩王爷和巴巴托斯是谁")
    assert multi["alias_pairs"] and multi["alias_binding"] is None
    assert runtime.identity.alias_identity_gate(multi) == {}


def test_coreference_and_cancellation(runtime):
    runtime.state.coref = F5
    current = runtime.rewritten(
        "她是谁？", conversation_history=[{"user": "兹白", "assistant": "角色"}]
    )
    assert (
        current["coref_changed"] and runtime.identity.alias_identity_gate(current) == {}
    )
    event = Event()
    event.set()
    from app.progress import _cancel_events

    runtime.monkeypatch.setitem(_cancel_events, "cancelled-test", event)
    current = runtime.rewritten(run_id="cancelled-test")
    result = runtime.identity.alias_identity_gate(current)
    assert (
        result["termination_kind"] == "cancelled"
        and "兹白" not in result["final_response"]
    )
    assert runtime.identity.has_unresolved_reference(
        "巴巴托斯和她是谁", [("巴巴托斯", "温迪")]
    )
    assert not runtime.identity.has_unresolved_reference("他宝是谁", [("他宝", "角色")])


def test_initializer_preserves_caller_and_reducer_clear(runtime):
    old = ToolMessage(content="stale source", tool_call_id="old", id="old-tool")
    initial = runtime.request(
        messages=[old],
        final_response="stale final",
        count_result={"count": 999},
        wiki_evidence={"old": True},
        wiki_route={"route": "wiki"},
        alias_pairs=[("大娘", "错误")],
        alias_binding={"old": True},
        conversation_history=[{"user": "旧题", "assistant": "旧答"}],
        run_id="reused",
    )
    before = deepcopy(initial)
    graph = runtime.workflow.create_agent_workflow()
    first = graph.invoke(initial)
    second = graph.invoke(initial)
    assert first["request_nonce"] != second["request_nonce"]
    assert first["messages"] == second["messages"] == []
    assert first["wiki_evidence"] is None and first["wiki_route"] is None
    assert first["count_result"] is None and first["execution_mode"] is None
    assert first["alias_binding"]["request_nonce"] == first["request_nonce"]
    assert "兹白" in first["final_response"] and initial == before
    assert first["conversation_history"] is not initial["conversation_history"]
    assert first["run_id"] == second["run_id"] == "reused"

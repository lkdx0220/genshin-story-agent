"""A alias contracts plus classified B real graph and Flask SSE entry."""

import hashlib
import importlib
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, ToolMessage
from test_alias_identity_gate import F5

pytest_plugins = ["test_alias_identity_gate", "test_wiki_fast_evidence"]


def fallback_boundaries(rt, *, tools=False):
    """Controlled expensive-node boundaries; actual graph edges remain loaded."""
    observed = []

    def assess(state):
        rt.counts["assess"] += 1
        observed.append(deepcopy(state))
        return {"execution_mode": "L2"}

    def plan(state):
        rt.counts["plan"] += 1
        if tools and rt.counts["plan"] == 1:
            return {
                "messages": [
                    AIMessage(
                        content="",
                        tool_calls=[
                            {"id": "current-tool", "name": "probe", "args": {}}
                        ],
                    )
                ],
                "execution_mode": "L2",
                "iteration": 1,
            }
        return {
            "messages": [AIMessage(content="plan complete")],
            "execution_mode": "L2",
            "iteration": 2,
        }

    def answer(state):
        rt.counts["answer"] += 1
        return {"final_response": "LEGACY:" + state["user_query"]}

    def execute(_state):
        rt.counts["tools"] += 1
        return {
            "messages": [
                ToolMessage(
                    content="current result", tool_call_id="current-tool", name="probe"
                )
            ]
        }

    rt.monkeypatch.setattr(rt.workflow, "assess_query", assess)
    rt.monkeypatch.setattr(rt.workflow, "plan_agent", plan)
    rt.monkeypatch.setattr(rt.workflow, "answer_agent", answer)
    rt.monkeypatch.setattr(rt.workflow, "tool_executor", execute)
    return observed


@pytest.mark.parametrize("decision", ["accepted", "rejected", "timeout", "short"])
def test_alias_graph_and_http_entry(runtime, decision):
    rt = runtime
    rt.state.verdict = decision
    if decision == "short":
        rt.monkeypatch.setattr(rt.nodes, "_judge_alias_sandbox_batch", lambda _c: [])
    observed = fallback_boundaries(rt)
    graph = rt.workflow.create_agent_workflow()
    final = graph.invoke(rt.request())
    expected = final["final_response"]
    if decision == "accepted":
        assert "兹白" in expected and not observed
        assert (
            rt.counts["assess"]
            == rt.counts["plan"]
            == rt.counts["answer"]
            == rt.counts["tools"]
            == 0
        )
    else:
        assert expected == "LEGACY:" + F5 and observed[0]["user_query"] == F5
    from app.progress import _cancel_events, set_progress_hook

    def summarize(*_args):
        rt.counts["summary"] += 1
        return "summary"

    rt.module(
        "genshin_story_agent",
        tools=[],
        RAG_AVAILABLE=False,
        角色知识库={},
        地区知识库={},
        主线剧情知识库={},
        _summarize_conversation=summarize,
        SUMMARY_TRIGGER=5,
        RECENT_TURNS=3,
        set_progress_hook=set_progress_hook,
        _cancel_events=_cancel_events,
        sanitize_prompt_text=rt.retrieval.sanitize_prompt_text,
        create_agent_workflow=rt.workflow.create_agent_workflow,
    )
    api = importlib.import_module("genshin_story_web_api")
    captured = []

    class BoundGraph:
        def invoke(self, initial):
            captured.append(deepcopy(initial))
            return graph.invoke(initial)

    rt.monkeypatch.setattr(api, "_get_workflow", lambda: BoundGraph())
    with api.app.test_client() as client:
        response = client.post(
            "/api/chat",
            json={
                "message": F5,
                "session_id": "alias-test",
                "alias_pairs": [["大娘", "伪造"]],
                "raw_query": "客户端伪造",
                "final_response": "伪造",
            },
        )
        assert response.status_code == 200
        events = [
            json.loads(line[6:])
            for line in response.get_data(as_text=True).splitlines()
            if line.startswith("data: ")
        ]
    done = [e for e in events if e["type"] == "done"]
    assert (
        len(done) == 1 and done[0]["answer"] == expected and done[0]["tool_calls"] == []
    )
    assert captured[0]["raw_query"] == F5 and captured[0]["user_query"] == F5
    assert "alias_pairs" not in captured[0] and "alias_binding" not in captured[0]
    public = json.dumps(events, ensure_ascii=False)
    assert "alias_map_digest" not in public and "question_digest" not in public
    assert "content_data/" not in public and "request_nonce" not in public
    assert not api._cancel_events and rt.counts["summary"] == 0


@pytest.mark.parametrize(
    "query",
    ["须弥有多少特产", "须弥有哪些特产", "璃月有多少世界任务", "海灯节有多少角色参与"],
)
@pytest.mark.parametrize("mode", ["off", "finite", "agent_evidence"])
def test_statistics_precedence(runtime, query, mode):
    from app.statistics import catalog, routing
    from app.statistics.contracts import canonical_bytes

    runtime.monkeypatch.setenv("LLM_WIKI_MODE", mode)
    if mode == "finite":
        from test_statistics_workflow import finite_fixture
        config = {"version": 1, "entities": {}}
        for entity in ("char:wanderer", "char:nahida"):
            folder = runtime.tmp_path / entity.replace(":", "-")
            folder.mkdir()
            config["entities"][entity] = finite_fixture(folder, entity)
        config_raw = canonical_bytes(config)
        config_path = runtime.tmp_path / "finite-statistics.json"
        config_path.write_bytes(config_raw)
        runtime.monkeypatch.setenv("LLM_WIKI_CONFIG", str(config_path))
        runtime.monkeypatch.setenv("LLM_WIKI_CONFIG_SHA256", hashlib.sha256(config_raw).hexdigest())

    data = [{"采集物ID": "synthetic-1", "名称": "合成特产", "类型": "须弥区域特产"}]
    raw = canonical_bytes(data)
    root = runtime.tmp_path / "statistics"
    target = root / "content_data/collectibles.json"
    target.parent.mkdir(parents=True)
    target.write_bytes(raw)
    runtime.monkeypatch.setattr(
        routing,
        "count_catalog",
        lambda request: catalog.count_catalog(
            request, root=root, trusted_sha256=hashlib.sha256(raw).hexdigest()
        ),
    )
    from app.statistics import story_cast

    entry = {
        "entry_id": "synthetic-story",
        "title": "海灯节",
        "entry_type": "activity",
        "cast": ["甲", "乙"],
        "cast_source": "metadata",
        "cast_scope": "activity",
        "story_text": "",
        "full_text": "",
        "metadata": {"出场人物": ["甲", "乙"]},
        "coverage": {
            "scope": {"scope_level": "activity", "scope_id": "synthetic-story"},
            "field_parse_ok": True,
            "complete": True,
        },
        "story_placeholder": False,
        "cast_absent": False,
        "aliases": [],
    }
    graph_raw = canonical_bytes({"entries": [entry]})
    alias_raw = canonical_bytes(
        {
            "version": "test-v1",
            "canonical_to_variants": {"甲": [], "乙": []},
            "variant_to_canonical": {},
            "conflict_notes": {},
        }
    )
    (root / "kb_vectors").mkdir()
    (root / "kb_vectors/wiki_entry_graph.json").write_bytes(graph_raw)
    (root / "kb_vectors/entity_alias_map.json").write_bytes(alias_raw)
    runtime.monkeypatch.setattr(
        routing,
        "story_result_for_query",
        lambda question: story_cast.story_result_for_query(
            question,
            root=root,
            graph_sha256=hashlib.sha256(graph_raw).hexdigest(),
            alias_sha256=hashlib.sha256(alias_raw).hexdigest(),
        ),
    )
    initial = runtime.request(
        query, final_response="旧答案", count_result={"count": 999}
    )
    expected = routing.statistics_gate(initial)
    graph = runtime.workflow.create_agent_workflow()
    assert ("initialize_request" in graph.get_graph().nodes) == (mode == "agent_evidence")
    events = list(graph.stream(initial, stream_mode="updates"))
    assert [next(iter(e)) for e in events] == (
        ["initialize_request", "statistics_gate"] if mode == "agent_evidence" else ["statistics_gate"]
    )
    final = events[-1]["statistics_gate"]
    assert final["execution_mode"] == "STATISTICS"
    assert final["final_response"] == expected["final_response"]
    assert final["count_result"]["status"] == expected["count_result"]["status"]
    assert sum(runtime.counts.values()) == 0


@pytest.mark.parametrize("mode", ["off", "finite", "agent_evidence"])
def test_safe_read_mode_integrity(runtime, mode):
    from app.statistics.contracts import Snapshot, canonical_bytes
    from app.statistics.snapshot_reads import SnapshotReadContext

    runtime.monkeypatch.setenv("LLM_WIKI_MODE", mode)
    root = runtime.tmp_path
    raw = canonical_bytes({"members": ["甲"]})
    (root / "probe.json").write_bytes(raw)
    digest = hashlib.sha256(raw).hexdigest()
    source = Snapshot.read(root, "probe.json", digest)
    reads = SnapshotReadContext(
        root=root,
        original_request="probe",
        entry_kind="structured",
        expected_sources={"probe.json": digest},
    )
    first = reads.copy_at(source)
    first["members"].append("污染")
    assert reads.copy_at(source) == {"members": ["甲"]}
    runtime.monkeypatch.setenv("LLM_WIKI_MODE", "finite" if mode != "finite" else "off")
    with pytest.raises(ValueError, match="reads_mode_changed"):
        reads.copy_at(source)
    runtime.monkeypatch.setenv("LLM_WIKI_MODE", mode)
    object.__setattr__(source, "raw", b"{}")
    with pytest.raises(ValueError, match="reads_binding_changed"):
        reads.copy_at(source)
    reads.close()
    runtime.monkeypatch.setenv("LLM_WIKI_MODE", "unknown")
    with pytest.raises(ValueError, match="reads_mode"):
        SnapshotReadContext(
            root=root,
            original_request="probe",
            entry_kind="structured",
            expected_sources={},
        )


@pytest.mark.parametrize(
    "query",
    [
        F5,
        "岩王爷是谁（另讲经历）",
        "岩王爷和不存在是谁",
        "岩王爷是谁？" + "附加任务" * 70,
        "她是谁？",
    ],
)
def test_b_preserves_classified_l2(runtime, query):
    runtime.state.verdict = "rejected"
    observed = fallback_boundaries(runtime)
    final = runtime.workflow.create_agent_workflow().invoke(runtime.request(query))
    assert (
        runtime.counts["assess"]
        == runtime.counts["plan"]
        == runtime.counts["answer"]
        == 1
    )
    assert final["final_response"] == "LEGACY:" + query
    assert observed[0]["user_query"] == query and not final.get("termination_kind")
    assert runtime.nodes.route_after_assess({"execution_mode": "L1"}) == "fast_agent"
    assert runtime.nodes.route_after_assess({"execution_mode": "L2"}) == "plan_agent"


def test_complex_fallback_preserves_accepted_context(runtime):
    query = "岩王爷是谁（再讲经历）"
    observed = fallback_boundaries(runtime)
    final = runtime.workflow.create_agent_workflow().invoke(runtime.request(query))
    assert observed[0]["user_query"] == query
    assert ("岩王爷", "钟离") in observed[0]["alias_pairs"]
    assert "钟离" in observed[0]["alias_notes"]
    assert final["final_response"] == "LEGACY:" + query


def test_graph_without_raw_pair_keeps_legacy(runtime):
    observed = fallback_boundaries(runtime)
    final = runtime.workflow.create_agent_workflow().invoke({"user_query": F5})
    assert observed[0]["user_query"] == F5
    assert final["final_response"] == "LEGACY:" + F5


@pytest.mark.parametrize("mode", ["off", "finite"])
def test_off_finite_compatibility(runtime, mode):
    from test_statistics_workflow import finite_fixture

    rt = runtime
    rt.monkeypatch.setenv("LLM_WIKI_MODE", mode)
    if mode == "finite":
        config = {"version": 1, "entities": {}}
        for entity in ("char:wanderer", "char:nahida"):
            folder = rt.tmp_path / entity.replace(":", "-")
            folder.mkdir()
            config["entities"][entity] = finite_fixture(folder, entity)
        raw = json.dumps(config, ensure_ascii=False).encode("utf-8")
        path = rt.tmp_path / "finite.json"
        path.write_bytes(raw)
        rt.monkeypatch.setenv("LLM_WIKI_CONFIG", str(path))
        rt.monkeypatch.setenv("LLM_WIKI_CONFIG_SHA256", hashlib.sha256(raw).hexdigest())
    graph = rt.workflow.create_agent_workflow()
    names = graph.get_graph().nodes
    assert "initialize_request" not in names and "alias_identity_gate" not in names
    assert rt.nodes.route_after_assess({"execution_mode": "L1"}) == "fast_agent"
    if mode == "finite":
        final = graph.invoke(rt.request("纳西妲的元素是什么"))
        assert (
            final["execution_mode"] == "WIKI" and final["wiki_route"]["route"] == "wiki"
        )
        assert sum(rt.counts.values()) == 0
    else:
        rt.monkeypatch.setattr(
            rt.workflow, "rewrite_query", lambda _s: {"final_response": "legacy"}
        )
        rt.monkeypatch.setattr(
            rt.workflow, "assess_query", lambda _s: {"execution_mode": "L1"}
        )
        rt.monkeypatch.setattr(
            rt.workflow, "fast_agent", lambda _s: {"final_response": "legacy"}
        )
        rt.monkeypatch.setattr(rt.workflow, "route_after_fast", lambda _s: "end")
        final = rt.workflow.create_agent_workflow().invoke(rt.request("普通问题"))
        assert final["final_response"] == "legacy" and not final.get("request_nonce")


def test_tool_continuation_never_reinitializes(runtime):
    fallback_boundaries(runtime, tools=True)
    rt = runtime
    calls = {"init": 0, "alias": 0, "nonce": []}
    original_init, original_gate = (
        rt.workflow.initialize_request,
        rt.workflow.alias_identity_gate,
    )

    def initialize(state):
        calls["init"] += 1
        result = original_init(state)
        calls["nonce"].append(result["request_nonce"])
        return result

    def gate(state):
        calls["alias"] += 1
        assert state["request_nonce"] == calls["nonce"][0]
        return original_gate(state)

    rt.monkeypatch.setattr(rt.workflow, "initialize_request", initialize)
    rt.monkeypatch.setattr(rt.workflow, "alias_identity_gate", gate)
    final = rt.workflow.create_agent_workflow().invoke(rt.request("普通问题"))
    assert calls["init"] == calls["alias"] == 1
    assert rt.counts["tools"] == 1 and rt.counts["plan"] == 2
    assert any(
        isinstance(m, ToolMessage) and m.tool_call_id == "current-tool"
        for m in final["messages"]
    )
    assert final["request_nonce"] == calls["nonce"][0]


def test_concurrent_same_run_id_isolated(runtime):
    graph = runtime.workflow.create_agent_workflow()
    inputs = [
        runtime.request(
            run_id="same", conversation_history=[{"user": str(i), "assistant": "a"}]
        )
        for i in range(4)
    ]
    before = deepcopy(inputs)
    with ThreadPoolExecutor(max_workers=4) as pool:
        outputs = list(pool.map(graph.invoke, inputs))
    assert len({x["request_nonce"] for x in outputs}) == 4
    assert [x["conversation_history"] for x in outputs] == [
        x["conversation_history"] for x in inputs
    ]
    assert all("兹白" in x["final_response"] and x["messages"] == [] for x in outputs)
    assert inputs == before


@pytest.mark.parametrize("case", ["bundle", "missing", "partial", "l2", "tool-loop", "max-budget"])
def test_l1_route_and_tool_continuation(evidence, case):
    from test_wiki_fast_evidence import DRAFT, QUESTION

    ev, rt = evidence, evidence.rt
    query = QUESTION if case != "missing" else "普通问题"
    if case == "missing":
        ev.outputs.append(AIMessage(content="本轮暂无可用证据"))
    calls = {"init": 0, "tools": 0}
    original_init = rt.workflow.initialize_request

    def initialize(state):
        calls["init"] += 1
        return original_init(state)

    rt.monkeypatch.setattr(rt.workflow, "initialize_request", initialize)
    if case in {"tool-loop", "max-budget", "partial"}:
        count = 1 if case != "max-budget" else rt.nodes.MAX_FAST_ITERATIONS
        ev.outputs.extend(AIMessage(content="", tool_calls=[{"name": "probe", "id": f"t{i}", "args": {}}]) for i in range(count))
        ev.outputs.append(AIMessage(content=DRAFT))

        def execute(state):
            calls["tools"] += 1
            call = state["messages"][-1].tool_calls[0]
            return {"messages": [ToolMessage(content="未找到资料", tool_call_id=call["id"], name=call["name"])]}

        rt.monkeypatch.setattr(rt.workflow, "tool_executor", execute)
    if case == "l2":
        rt.monkeypatch.setattr(rt.workflow, "assess_query", lambda _s: {"execution_mode": "L2"})
        # Only intent/tool registration boundaries are synthetic; real nodes.
        rt.monkeypatch.setattr(rt.nodes, "route_intent", lambda **_k: ["ALL"])
        rt.monkeypatch.setattr(rt.nodes, "get_tools_for_intent", lambda *_a: [])
    graph = rt.workflow.create_agent_workflow()
    started = time.perf_counter()
    events = list(graph.stream(rt.request(query, run_id="same-run"), stream_mode=["updates", "values"]))
    result = [v for kind, v in events if kind == "values"][-1]
    path = [next(iter(v)) for kind, v in events if kind == "updates"]
    assert path.count("initialize_request") == path.count("alias_identity_gate") == path.count("assess_query") == 1
    assert ("plan_agent" in path) == (case == "l2")
    assert ("answer_agent" in path) == (case == "l2")
    target = Path(os.environ.get("PROMOTION_RUN", str(rt.tmp_path))) / "graph-cases"
    target.mkdir(exist_ok=True)
    (target / f"{case}.json").write_text(json.dumps({"case": case, "query": query, "path": path,
        "answer": result["final_response"], "citations": result.get("answer_validation", {}).get("accepted_citations", []),
        "model_application_calls": len(ev.inputs), "tool_calls": calls["tools"], "http_attempts": 0,
        "elapsed_s": time.perf_counter() - started, "boundary": "offline controlled providers; not production latency",
        "request_nonce": result["request_nonce"]}, ensure_ascii=False, indent=2), encoding="utf-8")
    assert calls["init"] == 1
    assert result["execution_mode"] == ("L2" if case == "l2" else "L1")
    if case == "l2":
        assert result["answer_validation"]["verdict"] == "pass"
    elif case == "missing":
        assert result["termination_kind"] == "validated"
        assert "知识库未收录" not in result["final_response"]
    else:
        assert result["termination_kind"] == "validated"
        assert result["fast_terminal_nonce"] == result["request_nonce"]
        assert result["final_response"].count("[^L1]:") == 1
        assert calls["tools"] == (1 if case in {"tool-loop", "partial", "max-budget"} else 0)
        assert all("fast-answer-citations-v1" in str(m[1].content) for m in ev.inputs)


def load_evidence_api(rt, graph):
    from app.progress import _cancel_events, set_progress_hook
    rt.module("genshin_story_agent", tools=[], RAG_AVAILABLE=False, 角色知识库={}, 地区知识库={}, 主线剧情知识库={},
              _summarize_conversation=lambda *_a: "summary", SUMMARY_TRIGGER=5, RECENT_TURNS=3,
              set_progress_hook=set_progress_hook, _cancel_events=_cancel_events,
              sanitize_prompt_text=rt.retrieval.sanitize_prompt_text,
              create_agent_workflow=rt.workflow.create_agent_workflow)
    api = importlib.import_module("genshin_story_web_api")
    rt.monkeypatch.setattr(api, "_get_workflow", lambda: graph)
    return api


@pytest.mark.parametrize("case", ["valid", "reject", "error", "cancel-during", "cancel-before-publish", "publish-before-cancel", "parallel", "hook-restored", "progress-private"])
def test_http_sse_terminal_consistency(evidence, case):
    from queue import Queue
    from threading import Event

    from test_wiki_fast_evidence import DRAFT, QUESTION

    ev, rt = evidence, evidence.rt
    graph = rt.workflow.create_agent_workflow()
    api = load_evidence_api(rt, graph)
    hook_calls = []
    if case == "hook-restored":
        from threading import get_ident

        import genshin_story_agent as agent_module

        original_hook = agent_module.set_progress_hook

        def observed_hook(hook):
            hook_calls.append((get_ident(), hook is None))
            original_hook(hook)

        rt.monkeypatch.setattr(agent_module, "set_progress_hook", observed_hook)
    if case in {"cancel-before-publish", "publish-before-cancel"}:
        guard = api._EvidencePublication(Event())
        events = Queue()
        events.put({"type": "answer_delta", "delta": "PRIVATE_DRAFT"})
        events.put({"type": "done", "answer": "validated", "tool_calls": [], "session_id": "s", "private": "PRIVATE_DRAFT"})
        if case == "cancel-before-publish":
            assert guard.cancel()
        public = list(api._evidence_events(events, guard))
        assert len(public) == 1 and public[0]["type"] == ("cancelled" if case == "cancel-before-publish" else "done")
        assert not guard.cancel() and guard.claim({"type": "done"}) is None
        assert "PRIVATE_DRAFT" not in str(public)
        # The real cancellation API must also work after worker-side cleanup.
        run_id = "queued-done"
        api._publication_guards[run_id] = api._EvidencePublication(Event())
        api._session_cancel_keys["_local:queued"] = run_id
        if case == "publish-before-cancel":
            api._publication_guards[run_id].claim({"type": "done", "answer": "validated"})
        with api.app.test_client() as client:
            response = client.post("/api/cancel", json={"session_id": "queued"})
        assert response.status_code == (200 if case == "cancel-before-publish" else 409)
        assert api._publication_guards[run_id].event.is_set() == (case == "cancel-before-publish")
        api._publication_guards.clear()
        api._session_cancel_keys.clear()
        return
    if case == "reject":
        ev.outputs.append(AIMessage(content="PRIVATE_DRAFT 无引用"))
    elif case == "error":
        ev.outputs.extend([RuntimeError("PRIVATE_DRAFT"), RuntimeError("PRIVATE_DRAFT")])
    elif case == "cancel-during":
        def cancel(_messages):
            for event in list(api._cancel_events.values()):
                event.set()
            return AIMessage(content="PRIVATE_DRAFT" + DRAFT)
        ev.outputs.append(cancel)
    recorded = []
    class RecordedGraph:
        def invoke(self, request):
            if case == "progress-private":
                from app.progress import _emit_progress
                _emit_progress("answer_delta", {"delta": "PRIVATE_DRAFT"})
                _emit_progress("tool_start", {"tool": "search", "path": "content_data/private", "digest": "f" * 64})
            result = graph.invoke(request)
            recorded.append(result)
            return result

    rt.monkeypatch.setattr(api, "_get_workflow", lambda: RecordedGraph())
    def request(index):
        with api.app.test_client() as client:
            response = client.post("/api/chat", json={"message": QUESTION, "session_id": f"b-{index}"})
            assert response.status_code == 200
            return [json.loads(line[6:]) for line in response.get_data(as_text=True).splitlines() if line.startswith("data: ")]

    if case == "parallel":
        with ThreadPoolExecutor(max_workers=4) as pool:
            responses = list(pool.map(request, range(4)))
        assert len({r["request_nonce"] for r in recorded}) == 4
    else:
        responses = [request(0)]
    for events in responses:
        terminal = [e for e in events if e["type"] in {"done", "error", "cancelled", "timeout"}]
        assert len(terminal) == 1
        assert terminal[0]["type"] == ("cancelled" if case == "cancel-during" else "error" if case == "error" else "done")
        if case != "cancel-during":
            assert terminal[0]["error" if case == "error" else "answer"] == recorded[0]["final_response"]
        public = json.dumps(events, ensure_ascii=False)
        assert "PRIVATE_DRAFT" not in public and "content_data/" not in public
        assert "request_nonce" not in public and "bundle:" not in public and "f" * 64 not in public
    assert not api._publication_guards and not api._cancel_events and not api._session_cancel_keys
    if case == "hook-restored":
        from app import progress
        assert getattr(progress._tls, "hook", None) is None
        assert hook_calls and all(reset for reset in dict(hook_calls).values())

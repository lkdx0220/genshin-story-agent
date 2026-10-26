"""B offline contracts; synthetic bytes/pins, actual readers/graph/validators.

Provider/config startup comes from the A runtime fixture. No real KB/provider or
service is used here. Rebuilt R08 probes are labelled, not claimed as old tests.
"""

import ast
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
from threading import Event
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

pytest_plugins = ["test_alias_identity_gate"]
QUESTION = "流浪者的记忆如何恢复"
DRAFT = "已核对本轮记忆资料。[^L1]"


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


@pytest.fixture
def evidence(runtime):
    from app.wiki_evidence import bridge, canonical_bridge, canonical_local, store

    rt = runtime
    root = rt.tmp_path / "synthetic-kb"
    source = root / store.SOURCE_PATH
    source.parent.mkdir(parents=True)
    text = ("合成本地剧情：流浪者恢复记忆，纳西妲保存备份。" * 1000)
    rows = [{} for _ in range(230)]
    rows[215] = {"pageid": 143592, "title": "幕切——倾奇之末", "text": text}
    raw = json.dumps(rows, ensure_ascii=False).encode()
    source.write_bytes(raw)
    rt.monkeypatch.setattr(store, "SOURCE_SHA256", sha(raw))
    rt.monkeypatch.setattr(store, "TEXT_SHA256", sha(text.encode()))
    rt.monkeypatch.setattr(canonical_local, "SOURCE_SHA256", sha(raw))
    rt.monkeypatch.setattr(canonical_bridge, "ROOT", root)
    bundle = root / "bundle.json"
    bundle.write_text(json.dumps(store.compile_topic(root), ensure_ascii=False), encoding="utf-8")
    config = root / "config.json"
    config.write_text(json.dumps({"version": 1, "kb_root": str(root),
                                 "bundle_path": str(bundle), "bundle_sha256": sha(bundle.read_bytes())}), encoding="utf-8")
    rt.monkeypatch.setenv("LLM_WIKI_EVIDENCE_CONFIG", str(config))
    rt.monkeypatch.setenv("LLM_WIKI_EVIDENCE_CONFIG_SHA256", sha(config.read_bytes()))
    rt.monkeypatch.setenv("LLM_WIKI_DIAGNOSTICS_DIR", str(rt.tmp_path / "diagnostics"))
    inputs = []
    outputs = []

    def model(messages, **_kwargs):
        inputs.append(deepcopy(messages))
        answer = outputs.pop(0) if outputs else AIMessage(content=DRAFT)
        if isinstance(answer, Exception):
            raise answer
        return answer(messages) if callable(answer) else answer

    rt.monkeypatch.setattr(rt.nodes.plan_llm, "invoke", model)
    rt.monkeypatch.setattr(rt.nodes, "llm_invoke_with_retry",
                          lambda messages, *, llm_instance: llm_instance.invoke(messages))
    # Actual classifier routing is retained; classification model is controlled.
    rt.monkeypatch.setattr(rt.workflow, "assess_query", lambda _s: {"execution_mode": "L1"})
    return SimpleNamespace(rt=rt, bridge=bridge, canonical=canonical_bridge,
                           local=canonical_local, source=source, root=root,
                           bundle=bundle, config=config, text=text,
                           inputs=inputs, outputs=outputs)


def initial(ev, query=QUESTION, **extra):
    rt = ev.rt
    state = rt.request(query, messages=[], run_id="same-run", execution_mode="L1", **extra)
    current = dict(state, **rt.identity.initialize_request(state))
    # initialize_request returns a reducer delta; apply its empty reset here.
    current.update(messages=[], execution_mode="L1")
    return current


def metadata_messages():
    return [AIMessage(content="", tool_calls=[{"name": "query_character", "id": "meta", "args": {}}]),
            ToolMessage(content="合成资料：角色来自测试地区。", name="query_character", tool_call_id="meta")]


def prepare_terminal_case(evidence, case):
    ev, rt = evidence, evidence.rt
    state = initial(ev)
    if case.startswith("forced"):
        state["fast_iteration"] = rt.nodes.MAX_FAST_ITERATIONS
    if case == "metadata":
        state.update(user_query="流浪者的记忆原始资料", messages=metadata_messages())
    if case.startswith("not-found"):
        state["messages"] = [ToolMessage(content="未找到资料", tool_call_id="absent", name="search")]
        if case == "not-found-empty":
            state["user_query"] = "普通问题"
    if case in {"empty", "forced-empty"}:
        ev.outputs.append(AIMessage(content=""))
    if case in {"invoke-error", "forced-error"}:
        ev.outputs.extend([RuntimeError("private draft path"), RuntimeError("private retry")])
    if case in {"invoke-fallback", "forced-fallback"}:
        ev.outputs.extend([RuntimeError("first attempt"), AIMessage(content=DRAFT)])
    if case in {"cancel-before", "cancel-during"}:
        cancel = Event()
        rt.monkeypatch.setitem(rt.nodes._cancel_events, "same-run", cancel)
        if case == "cancel-before":
            cancel.set()
        else:
            ev.outputs.append(lambda _m: (cancel.set(), AIMessage(content=DRAFT))[1])
    if case == "invalid-preparation":
        ev.config.write_bytes(b"{}")
    if case == "program-error":
        rt.monkeypatch.setattr(ev.bridge, "prepare_evidence_state", lambda _s: (_ for _ in ()).throw(RuntimeError("bug")))
    if case == "pending-tools":
        ev.outputs.append(AIMessage(content="private draft", tool_calls=[{"name": "canonical_query", "id": "new-call", "args": {}}]))
    return state


@pytest.mark.parametrize("case", ["normal", "forced", "metadata", "not-found-with-wiki", "not-found-empty",
                                "empty", "forced-empty", "invoke-error", "forced-error", "cancel-before",
                                "cancel-during", "invalid-preparation", "program-error", "pending-tools", "unfinalized",
                                "invoke-fallback", "forced-fallback"])
def test_all_fast_terminal_paths(evidence, case):
    ev, rt = evidence, evidence.rt
    state = prepare_terminal_case(ev, case)
    if case == "unfinalized":
        for bad in ({}, {"messages": [AIMessage(content="raw")]}, {"final_response": "unvalidated"}):
            with pytest.raises(ValueError, match="fast_terminal_not_finalized"):
                rt.nodes.route_after_fast(dict(state, **bad))
        return
    before = deepcopy(state)
    result = rt.nodes.fast_agent(state)
    assert state == before
    if case == "pending-tools":
        assert not result["fast_finalized"] and not result.get("final_response")
        assert rt.nodes.route_after_fast(dict(state, **result)) == "tools"
        assert result["messages"][-1].additional_kwargs["local_canonical_run_id"] == "same-run"
        return
    assert result["fast_finalized"] and result["fast_terminal_nonce"] == state["request_nonce"]
    assert rt.nodes.route_after_fast(dict(state, **result)) == "end"
    assert_terminal_outcome(ev, case, result)


def assert_terminal_outcome(ev, case, result):
    kind = result["termination_kind"]
    if case in {"cancel-before", "cancel-during"}:
        assert kind == "cancelled"
    elif case in {"invoke-error", "forced-error", "program-error"}:
        assert kind == "error"
    elif case in {"empty", "forced-empty"}:
        assert kind == "empty_output"
    elif case == "invalid-preparation":
        assert kind == "evidence_invalid" and not ev.inputs
    elif case == "not-found-empty":
        assert kind == "insufficient_evidence" and not ev.inputs
        assert "不表示整个知识库未收录" in result["final_response"]
    else:
        assert_validated_terminal(ev, case, result)


def assert_validated_terminal(ev, case, result):
    assert result["termination_kind"] == "validated", result
    assert result["answer_validation"]["verdict"] == "pass"
    assert sum(e["boundary"] == "finalize" for e in result["answer_diagnostics"]["validation_events"]) == 1
    assert result["final_response"].count("[^L1]:") == (0 if case == "metadata" else 1)
    if case == "metadata":
        assert not ev.inputs and result["final_response"] == metadata_messages()[-1].content
    else:
        assert result["raw_draft"] == DRAFT
        assert ev.inputs and "fast-answer-citations-v1" in str(ev.inputs[-1][1].content)
        if case in {"invoke-fallback", "forced-fallback"}:
            assert [c["status"] for c in result["answer_diagnostics"]["model_calls"]] == ["error", "completed"]


@pytest.mark.parametrize("case", ["valid", "source-before", "source-after", "package-after", "bad-config", "bad-json", "unreadable", "unclassified", "pending-after"])
def test_source_and_package_drift(evidence, case):
    ev, rt = evidence, evidence.rt
    state = initial(ev)
    if case == "source-before":
        ev.source.write_bytes(b"[]")
    elif case == "bad-config":
        ev.config.write_bytes(b"{}")
    elif case == "bad-json":
        ev.config.write_bytes(b"{")
        rt.monkeypatch.setenv("LLM_WIKI_EVIDENCE_CONFIG_SHA256", sha(b"{"))
    elif case == "unreadable":
        ev.source.unlink()
    elif case in {"source-after", "package-after", "pending-after"}:
        def drift(_messages):
            (ev.source if case in {"source-after", "pending-after"} else ev.bundle).write_bytes(b"[]")
            if case == "pending-after":
                return AIMessage(content="", tool_calls=[{"name": "search", "id": "p", "args": {}}])
            return AIMessage(content=DRAFT)
        ev.outputs.append(drift)
    elif case == "unclassified":
        from app.wiki_evidence import store
        rt.monkeypatch.setattr(store.EvidenceStore, "query", lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("unclassified")))
    result = rt.nodes.fast_agent(state)
    expected = "validated" if case == "valid" else "error" if case == "unclassified" else "evidence_invalid"
    assert result["termination_kind"] == expected, result
    if case not in {"valid", "source-after", "package-after", "pending-after"}:
        assert not ev.inputs
    if case != "valid":
        assert "[^L1]" not in result["final_response"]
        assert not result["messages"]


def canonical_messages(ev):
    fact = SimpleNamespace(record_id="synthetic-record", text="合成有限事实")
    start, end = 16615, 16727
    span = (215, 143592, sha(ev.text.encode()), start, end, sha(ev.text[start:end].encode()))
    ev.rt.monkeypatch.setattr(ev.local, "TRUSTED_BINDINGS", {(fact.record_id, sha(fact.text.encode())): [span]})
    envelope = ev.local.build_envelope("原始候选不能授权事实", [(None, fact)], ev.root)
    assert envelope["local_provenance_status"] == "bound"
    return [AIMessage(content="", additional_kwargs={"local_canonical_run_id": "same-run"},
                      tool_calls=[{"name": "canonical_query", "id": "c1", "args": {}}]),
            ToolMessage(content=ev.local.dumps_envelope(envelope), name="canonical_query", tool_call_id="c1")]


@pytest.mark.parametrize("case", ["valid", "old-round", "wrong-id", "duplicate", "wrong-name", "out-of-order"])
def test_current_call_binding_mutations(evidence, case):
    ev = evidence
    state = ev.bridge.prepare_evidence_state(initial(ev, "普通问题"))
    messages = canonical_messages(ev)
    if case == "old-round":
        messages.insert(1, HumanMessage(content="当前用户边界"))
    elif case == "wrong-id":
        messages[-1].tool_call_id = "wrong"
    elif case == "duplicate":
        messages.append(deepcopy(messages[-1]))
    elif case == "wrong-name":
        messages[-1].name = "other"
    elif case == "out-of-order":
        messages.reverse()
    state["messages"] = messages
    info = ev.canonical.gather(state)
    assert bool(info["entries"]) == (case == "valid")
    result = ev.bridge.finish_fast_result(state, {"final_response": DRAFT, "messages": []})
    assert (result["termination_kind"] == "validated") == (case == "valid")
    if case != "valid":
        assert result["answer_validation"]["verdict"] == "reject_source"


@pytest.mark.parametrize("case", ["valid", "unknown-id", "self-footnote", "quote-mismatch", "no-selection", "literal-valid", "literal-character", "literal-old"])
def test_citation_and_literal_witness_mutations(evidence, case):
    ev = evidence
    state = ev.bridge.prepare_evidence_state(initial(ev))
    draft = DRAFT
    result = {"final_response": draft, "messages": []}
    if case == "unknown-id":
        result["final_response"] = "资料。[^L999]"
    elif case == "self-footnote":
        result["final_response"] += "\n[^L1]: 作者自己定义"
    elif case == "quote-mismatch":
        result["final_response"] = "原文写道：“这是一段不存在于来源的伪造句子”。[^L1]"
    elif case == "no-selection":
        result["final_response"] = "未选择引用的答案"
    elif case.startswith("literal"):
        state["messages"] = metadata_messages()
        literal = state["messages"][-1].content
        if case == "literal-old":
            state["messages"].append(HumanMessage(content="当前用户新问题"))
        result.update(final_response=literal + ("变" if case == "literal-character" else ""),
                      _wiki_literal_tool_answer=literal)
    final = ev.bridge.finish_fast_result(state, result)
    assert (final["termination_kind"] == "validated") == (case in {"valid", "literal-valid"}), final
    if case not in {"valid", "literal-valid"}:
        assert final["answer_validation"]["rejected_citations"]


@pytest.mark.parametrize("case", ["shared-forced", "repeat-run", "concurrent", "context-history", "context-handshake"])
def test_request_isolation(evidence, case):
    ev, rt = evidence, evidence.rt
    state = initial(ev)
    if case == "shared-forced":
        state["fast_iteration"] = rt.nodes.MAX_FAST_ITERATIONS
        shared = AIMessage(content="", tool_calls=[{"name": "probe", "id": "p", "args": {}}])
        state["messages"] = [shared]
        before = deepcopy(shared)
        result = rt.nodes.fast_agent(state)
        assert shared == before and result["termination_kind"] == "validated"
        return
    if case in {"context-history", "context-handshake"}:
        state["conversation_history"] = [{"user": "先前用户", "assistant": "先前回答"}]
        state["conversation_summary"] = "历史长摘要"
        state["messages"] = [AIMessage(content="", tool_calls=[{"name": "search", "id": "p", "args": {}}]),
                             ToolMessage(content="本轮原文", tool_call_id="p", name="search")]
        before = deepcopy(state)
        rt.nodes.fast_agent(state)
        assert state == before
        context = ev.inputs[-1]
        assert "历史长摘要" in str(context)
        assert "先前用户" in str(context) and "先前回答" in str(context)
        positions = [i for i, m in enumerate(context) if isinstance(m, AIMessage) and m.tool_calls]
        assert len(positions) == 1 and isinstance(context[positions[0] + 1], ToolMessage)
        assert "fast-answer-citations-v1" not in str(state["messages"])
        return
    graph = rt.workflow.create_agent_workflow()
    requests = [rt.request(QUESTION, run_id="same-run") for _ in range(4)]
    before = deepcopy(requests)
    if case == "concurrent":
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(graph.invoke, requests))
    else:
        results = [graph.invoke(item) for item in requests]
    assert len({r["request_nonce"] for r in results}) == 4
    assert len({r["answer_diagnostics"]["capture_id"] for r in results}) == 4
    assert all(r["termination_kind"] == "validated" for r in results)
    assert len({r["final_response"] for r in results}) == 1 and requests == before


@pytest.mark.parametrize("case", ["existing-final-empty", "existing-final-nonempty", "capture-before-project", "gather-error", "prefetch-asts"])
def test_r08_preserved_contract(evidence, case):
    """Reconstructed from frozen R08 obligations; old migration files absent."""
    ev, rt = evidence, evidence.rt
    original = rt.nodes._answer_agent_original
    if case == "prefetch-asts":
        assert_preserved_answer_asts(rt)
        probe_four_prefetches(ev)
        return
    # These tests exercise the historical body rather than weakening its AST guard.
    if case.startswith("existing-final"):
        messages = [] if case.endswith("empty") and not case.endswith("nonempty") else [HumanMessage(content="current")]
        state = initial(ev)
        state.update(messages=messages, final_response="保留早退")
        result = original(state)
        assert (result["final_response"] == "保留早退") == (case == "existing-final-empty")
        if case == "existing-final-nonempty":
            package = ev.bridge.retrieve_for_state(state)
            state["messages"] = [ev.bridge.data_message(package)]
            wrapped = rt.nodes.answer_agent(state)
            assert wrapped["answer_validation"]["validation_input"] != "保留早退"
        return
    if case == "capture-before-project":
        assert_capture_before_project(rt)
        return
    rt.monkeypatch.setattr(ev.canonical, "gather", lambda _s: (_ for _ in ()).throw(RuntimeError("gather failure")))
    with pytest.raises(RuntimeError, match="gather failure"):
        rt.nodes.answer_agent(initial(ev))


def assert_preserved_answer_asts(rt):
    candidate = Path(rt.nodes.__file__)
    baseline = candidate.parents[3] / "baseline/app/agent/nodes.py"
    trees = [ast.parse(p.read_text(encoding="utf-8")) for p in (baseline, candidate)]
    names = ["_answer_agent_original", "_deterministic_roster_answer", "_deterministic_cooccur_answer",
             "_deterministic_presence_answer", "_deterministic_coverage_answer"]
    for name in names:
        bodies = [next(n for n in t.body if isinstance(n, ast.FunctionDef) and n.name == name) for t in trees]
        assert ast.dump(bodies[0], include_attributes=False) == ast.dump(bodies[1], include_attributes=False)


def assert_capture_before_project(rt):
    tree = ast.parse(Path(rt.nodes.__file__).read_text(encoding="utf-8"))
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_answer_agent_original")
    assignments = [n for n in ast.walk(node) if isinstance(n, ast.Assign)]
    captured = next(n for n in assignments if any(isinstance(t, ast.Name) and t.id == "had_no_messages" for t in n.targets))
    calls = [n for n in ast.walk(node) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "context_for_answer"]
    assert calls and all(captured.lineno < n.lineno for n in calls)


def probe_four_prefetches(ev):
    """Actual old helper/body orchestration, controlled authoritative data edge."""
    rt = ev.rt
    rt.module("intent_router", _get_entity_registry=lambda: {"甲甲": {}, "乙乙": {}})
    rt.monkeypatch.setitem(rt.retrieval.TITLE_REGISTRY, "合成任务", {"title": "合成任务"})
    seen = []

    def entity(name, *, relation, target):
        seen.append((name, relation, target))
        return "【结论区｜强证据】合成结构化结果"

    rt.module("app.tools.entity", entity_query=entity)
    rt.module("app.entity_retrieval", format_presence=lambda *_a, **_k: "存在性证据：合成数据",
              format_coverage=lambda _c: "合成覆盖度")
    queries = ["合成任务有哪些角色出场", "甲甲与乙乙共同出场", "甲甲去过璃月吗", "魔神任务覆盖度"]
    for query in queries:
        state = initial(ev, query)
        planned = rt.nodes.plan_agent(state)
        assert planned["final_response"] and planned["messages"] == []
        result = rt.nodes.answer_agent(dict(state, **planned))
        assert result["final_response"] == planned["final_response"]
        assert result["answer_diagnostics"]["model_calls"] == []
    assert len(seen) == 2 and not ev.inputs

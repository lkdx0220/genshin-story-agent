"""Frozen real data through public statistics APIs; models remain forbidden doubles."""

import hashlib
import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest


def controls():
    manifest = Path(os.environ["REAL_RESOURCE_MANIFEST"])
    raw = manifest.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == os.environ["REAL_RESOURCE_MANIFEST_SHA256"]
    value = json.loads(raw)
    root = Path(os.environ["REAL_RESOURCE_ROOT"])
    for item in value["files"]:
        assert hashlib.sha256((root / item["relative"]).read_bytes()).hexdigest() == item["sha256"]
    return root, value


def finite_configuration(runtime):
    from test_statistics_workflow import finite_fixture

    from app.statistics.contracts import canonical_bytes

    config = {"version": 1, "entities": {}}
    for entity in ("char:wanderer", "char:nahida"):
        folder = runtime.tmp_path / entity.replace(":", "-")
        folder.mkdir()
        config["entities"][entity] = finite_fixture(folder, entity)
    raw = canonical_bytes(config)
    path = runtime.tmp_path / "finite-statistics.json"
    path.write_bytes(raw)
    runtime.monkeypatch.setenv("LLM_WIKI_CONFIG", str(path))
    runtime.monkeypatch.setenv("LLM_WIKI_CONFIG_SHA256", hashlib.sha256(raw).hexdigest())


def assert_observation(runtime, query, mode, events, negative, catalog_expected, manifest):
    names = [next(iter(event)) for event in events]
    expected_nodes = ["initialize_request", "statistics_gate"] if mode == "agent_evidence" else ["statistics_gate"]
    assert names == expected_nodes
    final = events[-1]["statistics_gate"]
    result = final["count_result"]
    assert final["execution_mode"] == "STATISTICS"
    if query in negative:
        assert result["status"] == negative[query] and result["count"] is None
    else:
        assert result["status"] in {"complete", "partial", "zero"}
        assert isinstance(result["count"], int) and result["count"] > 0
    assert final["final_response"] == result["answer_text_hint"]
    if result["tool"] == "count_catalog":
        assert result["count"] == len(catalog_expected)
        assert sorted(member["member_id"] for member in result["members"]) == catalog_expected
    public = final["final_response"]
    assert "content_data/" not in public and "kb_vectors/" not in public
    assert all(item["sha256"] not in public for item in manifest["files"])
    assert sum(runtime.counts.values()) == 0
    return {"query": query, "nodes": names, "result": result, "answer": public}


@pytest.mark.parametrize("mode", ["off", "finite", "agent_evidence"])
def test_frozen_real_resource_statistics(runtime, mode):
    from app.statistics import catalog, routing, story_cast

    root, manifest = controls()
    runtime.monkeypatch.setenv("LLM_WIKI_MODE", mode)
    if mode == "finite":
        finite_configuration(runtime)
    # Supported trusted-Python root injection; no model/source/result substitution.
    runtime.monkeypatch.setattr(
        routing, "count_catalog", lambda request: catalog.count_catalog(request, root=root)
    )
    runtime.monkeypatch.setattr(
        routing, "story_result_for_query",
        lambda question: story_cast.story_result_for_query(question, root=root),
    )
    catalog_rows = json.loads((root / "content_data/collectibles.json").read_bytes())
    catalog_expected = sorted({row["采集物ID"] for row in catalog_rows if row["类型"] == "须弥区域特产"})
    selection_path = Path(os.environ["REAL_QUERY_SELECTION"])
    selection_raw = selection_path.read_bytes()
    assert hashlib.sha256(selection_raw).hexdigest() == os.environ["REAL_QUERY_SELECTION_SHA256"]
    selection = json.loads(selection_raw)
    queries = ["须弥有多少特产", "须弥有哪些特产"]
    queries.extend(item["query"] for item in selection["positive"])
    queries.extend(item["query"] for item in selection["negative"])
    negative = {item["query"]: item["expected_status"] for item in selection["negative"]}
    observed = []
    for query in queries:
        graph = runtime.workflow.create_agent_workflow()
        events = list(graph.stream(runtime.request(query), stream_mode="updates"))
        observed.append(assert_observation(runtime, query, mode, events, negative, catalog_expected, manifest))
    run = Path(os.environ["PROMOTION_RUN"])
    tree = os.environ.get("A_TREE", "candidate")
    round_id = os.environ["A_ROUND"]
    with (run / f"real-{tree}-{round_id}-{mode}.json").open("x", encoding="utf-8") as handle:
        json.dump({"mode": mode, "tree": tree, "source": "three frozen original resources", "observations": observed, "model_counts": runtime.counts}, handle, ensure_ascii=False, indent=2)


@pytest.mark.parametrize("mode", ["off", "finite", "agent_evidence"])
def test_real_alias_snapshot_integrity(runtime, mode):
    from app.statistics.contracts import Snapshot
    from app.statistics.evidence import AliasSnapshot
    from app.statistics.snapshot_reads import SnapshotReadContext

    root, manifest = controls()
    runtime.monkeypatch.setenv("LLM_WIKI_MODE", mode)
    row = next(item for item in manifest["files"] if item["relative"] == "kb_vectors/entity_alias_map.json")
    source = Snapshot.read(root, row["relative"], row["sha256"])
    reads = SnapshotReadContext(root=root, original_request="真实别名完整性", entry_kind="structured", expected_sources={source.path: source.sha256})
    aliases = AliasSnapshot(source, reads)
    original = reads.copy_at(source)
    detached = reads.copy_at(source)
    detached["version"] = "污染"
    assert reads.copy_at(source) == original
    reads.lock_execution({"scope_level": "activity", "scope_id": "1584"}, aliases)
    key = reads.execution_key()
    assert reads.lookup(source, key)["hit"]
    key["parser_adapter_source"]["source"]["snapshot_reads.py"] = "0" * 64
    assert not reads.lookup(source, key)["hit"]
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(reads.copy_at, source)
        with pytest.raises(ValueError, match="reads_owner"):
            future.result()
    runtime.monkeypatch.setenv("LLM_WIKI_MODE", "finite" if mode != "finite" else "off")
    with pytest.raises(ValueError, match="reads_mode_changed"):
        reads.copy_at(source)
    runtime.monkeypatch.setenv("LLM_WIKI_MODE", mode)
    reads.seal()
    object.__setattr__(source, "raw", b"{}")
    with pytest.raises(ValueError, match="reads_binding_changed"):
        reads.copy_at(source)
    reads.close()
    assert hashlib.sha256((root / row["relative"]).read_bytes()).hexdigest() == row["sha256"]

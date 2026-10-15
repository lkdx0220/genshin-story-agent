# -*- coding: utf-8 -*-
"""B4-01 首批：只读增量 plan/explain 契约测试。"""

import hashlib
import json
from pathlib import Path

from app import incremental


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _setup_project(tmp_path, monkeypatch):
    root = tmp_path
    monkeypatch.setattr(incremental, "REPO_ROOT", root)
    monkeypatch.setattr(incremental, "DEFAULT_INPUTS", root / "config/incremental-inputs.json")
    _write(root / "wiki/__schema__/entity-template.md", "# template\n")
    _write(
        root / "wiki/__schema__/entity.schema.json",
        json.dumps({"x-template-sha256": _sha((root / "wiki/__schema__/entity-template.md").read_bytes())}),
    )
    _write(root / "wiki/__schema__/version-registry.json", '{"versions": []}')
    _write(root / "wiki/__schema__/ingest-mini.prompt.md", "{{SCHEMA}}")
    _write(root / "wiki/__schema__/entities/char_mini.json", '{"entity_id": "char:mini"}')
    _write(
        root / "docs/schema-lock.json",
        json.dumps(
            {
                "schema_version": "0.2.0-draft",
                "sha256": {
                    "wiki/__schema__/entity.schema.json": _sha(
                        (root / "wiki/__schema__/entity.schema.json").read_bytes()
                    ),
                    "wiki/__schema__/entity-template.md": _sha(
                        (root / "wiki/__schema__/entity-template.md").read_bytes()
                    ),
                },
            }
        ),
    )
    _write(root / "lint/wiki-lint-rules.yaml", "ruleset_version: test\n")
    case = root / "fixture/raw/case.txt"
    _write(case, "共同事实\n")
    manifest = {
        "data_mode": "fixture",
        "sources": [
            {
                "source_id": "source:mini",
                "file": "case.txt",
                "sha256": _sha(case.read_bytes()),
                "provider": "user_fixture",
                "title": "mini fixture",
                "url": "https://example.invalid/mini",
                "paragraphs": {"p001": {"basis": "explicit_text", "authority": "non_authoritative"}},
            }
        ],
    }
    _write(root / "fixture/raw/manifest.json", json.dumps(manifest, ensure_ascii=False))
    _write(root / "reports/mini.md", "initial output")
    _write(
        root / "fixture-response.md",
        "---\n"
        + json.dumps(
            {
                "entity_id": "char:mini",
                "canonical_name": "迷你",
                "events": [
                    {
                        "event_id": "event:mini",
                        "evidence": [{"source_span_id": "span:mini"}],
                        "player_choice_scope": [],
                    }
                ],
                "field_assertions": [{"path": "/element", "status": "unknown", "evidence": [], "conflict_ids": []}],
            },
            ensure_ascii=False,
        )
        + "\n---\n# 迷你\n",
    )
    inputs = {
        "format_version": 1,
        "inputs": [
            {
                "bundle_key": "mini",
                "root": "fixture/raw",
                "data_mode": "fixture",
                "managed_pages": [
                    {
                        "page_key": "fixture-response.md|fixture|char:mini",
                        "entity_id": "char:mini",
                        "response": "fixture-response.md",
                        "output": "reports/mini.md",
                    }
                ],
                "sources": ["source:mini"],
                "profile_ids": ["char:mini"],
                "prompt": "wiki/__schema__/ingest-mini.prompt.md",
            }
        ],
    }
    _write(root / "config/incremental-inputs.json", json.dumps(inputs, ensure_ascii=False))
    return inputs, root


def _write_baseline(root: Path, observed) -> Path:
    state = root / "reports/incremental/state.json"
    state.parent.mkdir(parents=True, exist_ok=True)
    nodes = [
        {"key": ["page", page_key], "type": "page"}
        for entry in (observed.get("inputs") or {}).values()
        for page_key in (entry.get("pages") or {})
    ]
    graph = {
        "format_version": 1,
        "protocol": incremental.PROTOCOL_VERSION,
        "generation": "g1",
        "nodes": nodes,
        "edges": [],
    }
    state.write_text(
        json.dumps(incremental.make_baseline(observed, generation="g1", graph=graph), ensure_ascii=False),
        encoding="utf-8",
    )
    return state


def test_state_missing_is_full_required(tmp_path, monkeypatch):
    inputs, root = _setup_project(tmp_path, monkeypatch)
    plan = incremental.build_plan(inputs, root / "reports/incremental/missing.json")
    assert plan["full_required"] is True
    assert "state_unavailable" in plan["unknown"]
    assert plan["not_implemented"] == ["rebuild", "runtime_stale"]


def test_plan_does_not_advance_baseline_and_is_stable(tmp_path, monkeypatch):
    inputs, root = _setup_project(tmp_path, monkeypatch)
    state = _write_baseline(root, incremental.fingerprint_inputs(inputs))
    before = state.read_bytes()

    clean = incremental.build_plan(inputs, state)
    assert clean["dirty"] == {}
    assert clean["full_required"] is False
    assert clean["freshness"]["fixture-response.md|fixture|char:mini"] == "fresh"

    case = root / "fixture/raw/case.txt"
    case.write_bytes(case.read_bytes() + b"\nnew paragraph\n")
    first = incremental.build_plan(inputs, state)
    second = incremental.build_plan(inputs, state)
    assert first["dirty"] == second["dirty"]
    assert "source_file_changed" in first["dirty"]["fixture-response.md|fixture|char:mini"]
    assert any("raw_snapshot_hash_mismatch" in item for item in first["blocked"])
    assert state.read_bytes() == before


def test_legal_new_snapshot_marks_source_pages_dirty_without_block(tmp_path, monkeypatch):
    inputs, root = _setup_project(tmp_path, monkeypatch)
    observed = incremental.fingerprint_inputs(inputs)
    state = _write_baseline(root, observed)
    case = root / "fixture/raw/case.txt"
    case.write_bytes(case.read_bytes() + b"\nlegal new snapshot\n")
    manifest_path = root / "fixture/raw/manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["sources"][0]["sha256"] = _sha(case.read_bytes())
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

    plan = incremental.build_plan(inputs, state)
    assert plan["full_required"] is False
    assert "source_file_changed" in plan["dirty"]["fixture-response.md|fixture|char:mini"]
    assert not any("raw_snapshot_hash_mismatch" in item for item in plan["blocked"])


def test_registry_or_schema_change_is_full_required(tmp_path, monkeypatch):
    inputs, root = _setup_project(tmp_path, monkeypatch)
    state = _write_baseline(root, incremental.fingerprint_inputs(inputs))
    _write(root / "wiki/__schema__/version-registry.json", '{"versions": [{"version_id": "x"}]}')
    plan = incremental.build_plan(inputs, state)
    assert plan["full_required"] is True


def test_output_modified_is_stale_and_dirty(tmp_path, monkeypatch):
    inputs, root = _setup_project(tmp_path, monkeypatch)
    state = _write_baseline(root, incremental.fingerprint_inputs(inputs))
    _write(root / "reports/mini.md", "manual edit")
    plan = incremental.build_plan(inputs, state)
    page_key = "fixture-response.md|fixture|char:mini"
    assert "generated_output_modified" in plan["dirty"][page_key]
    assert plan["freshness"][page_key] == "stale"


def test_graph_extracts_records_spans_and_edges(tmp_path, monkeypatch):
    inputs, _root = _setup_project(tmp_path, monkeypatch)
    graph = incremental.collect_dependencies(inputs)
    types = {node["type"] for node in graph["nodes"]}
    assert {"page", "event", "span", "bundle", "source", "paragraph"} <= types
    page_key = "fixture-response.md|fixture|char:mini"
    event_node = (page_key, "event", "event:mini")
    assert any(tuple(edge["consumer"]) == event_node and edge["dependency"][0] == "span" for edge in graph["edges"])


def test_cycle_and_depth_are_bounded(tmp_path, monkeypatch):
    inputs, root = _setup_project(tmp_path, monkeypatch)
    state = _write_baseline(root, incremental.fingerprint_inputs(inputs))
    graph = {
        "edges": [
            {"consumer": ["page", "a"], "dependency": ["page", "b"]},
            {"consumer": ["page", "b"], "dependency": ["page", "a"]},
        ]
    }
    dirty, _blocked, full = incremental.mark_dirty(
        [{"kind": "source_file_changed", "bundle_key": "mini", "page_keys": ["fixture-response.md|fixture|char:mini"]}],
        inputs,
        graph,
    )
    assert full is False
    assert "fixture-response.md|fixture|char:mini" in dirty
    plan = incremental.build_plan(inputs, state)
    assert set(plan) == {
        "blocked",
        "dirty",
        "baseline_generation",
        "capability",
        "events",
        "execution_protocol",
        "field_impacts",
        "format_version",
        "freshness",
        "full_required",
        "generation",
        "graph_diagnostics",
        "not_implemented",
        "observed",
        "unknown",
    }


def test_explain_reports_page_reason(tmp_path, monkeypatch):
    inputs, root = _setup_project(tmp_path, monkeypatch)
    state = _write_baseline(root, incremental.fingerprint_inputs(inputs))
    case = root / "fixture/raw/case.txt"
    case.write_bytes(case.read_bytes() + b"\nchange\n")
    plan = incremental.build_plan(inputs, state)
    info = incremental.explain_page(plan, "fixture-response.md|fixture|char:mini")
    assert info["dirty"]
    assert info["not_implemented"] == ["rebuild", "runtime_stale"]


def test_source_set_change_is_full_required(tmp_path, monkeypatch):
    inputs, root = _setup_project(tmp_path, monkeypatch)
    state = _write_baseline(root, incremental.fingerprint_inputs(inputs))
    extra = root / "fixture/raw/extra.txt"
    _write(extra, "second source\n")
    manifest_path = root / "fixture/raw/manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["sources"].append(
        {
            "source_id": "source:mini-2",
            "file": "extra.txt",
            "sha256": _sha(extra.read_bytes()),
            "paragraphs": {"p001": {"basis": "explicit_text", "authority": "non_authoritative"}},
        }
    )
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    plan = incremental.build_plan(inputs, state)
    assert plan["full_required"] is True
    assert any("input_source_added_or_removed" in item for item in plan["unknown"])


def test_proof_missing_is_dirty_and_blocked(tmp_path, monkeypatch):
    inputs, root = _setup_project(tmp_path, monkeypatch)
    proof = root / "fixture/raw/choice-proofs.json"
    _write(proof, '{"format_version": 1, "proofs": []}')
    manifest_path = root / "fixture/raw/manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["choice_proofs_file"] = "choice-proofs.json"
    manifest["choice_proofs_sha256"] = _sha(proof.read_bytes())
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    state = _write_baseline(root, incremental.fingerprint_inputs(inputs))
    proof.unlink()
    plan = incremental.build_plan(inputs, state)
    page_key = "fixture-response.md|fixture|char:mini"
    assert "proof_changed" in plan["dirty"][page_key]
    assert any("proof_hash_mismatch" in item for item in plan["blocked"])


def test_schema_file_change_is_full_required(tmp_path, monkeypatch):
    inputs, root = _setup_project(tmp_path, monkeypatch)
    state = _write_baseline(root, incremental.fingerprint_inputs(inputs))
    _write(root / "wiki/__schema__/entity.schema.json", '{"changed": true}')
    plan = incremental.build_plan(inputs, state)
    assert plan["full_required"] is True


def test_main_rejects_derived_output_overlap(tmp_path, monkeypatch):
    inputs, root = _setup_project(tmp_path, monkeypatch)
    state = _write_baseline(root, incremental.fingerprint_inputs(inputs))
    before = state.read_bytes()
    graph = root / "reports/incremental/graph.json"
    rc = incremental.main(
        [
            "plan",
            "--inputs",
            str(root / "config/incremental-inputs.json"),
            "--state",
            str(state),
            "--plan",
            str(state),
            "--graph",
            str(graph),
        ]
    )
    assert rc == 2
    assert state.read_bytes() == before

    outside = tmp_path / "outside.json"
    rc = incremental.main(
        [
            "plan",
            "--inputs",
            str(root / "config/incremental-inputs.json"),
            "--state",
            str(state),
            "--plan",
            str(outside),
            "--graph",
            str(graph),
        ]
    )
    assert rc == 2
    assert not outside.exists()


def test_incomplete_baseline_is_full_required(tmp_path, monkeypatch):
    inputs, root = _setup_project(tmp_path, monkeypatch)
    state = root / "reports/incremental/state.json"
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text('{"format_version": 1}', encoding="utf-8")
    plan = incremental.build_plan(inputs, state)
    assert plan["full_required"] is True
    assert "state_not_validated" in plan["unknown"]


def test_missing_output_hashes_are_not_fresh(tmp_path, monkeypatch):
    inputs, root = _setup_project(tmp_path, monkeypatch)
    inputs["inputs"][0]["managed_pages"][0]["output"] = None
    state = _write_baseline(root, incremental.fingerprint_inputs(inputs))
    plan = incremental.build_plan(inputs, state)
    page_key = "fixture-response.md|fixture|char:mini"
    assert plan["freshness"][page_key] == "unknown"


def test_field_impacts_are_reported_for_source_change(tmp_path, monkeypatch):
    inputs, root = _setup_project(tmp_path, monkeypatch)
    state = _write_baseline(root, incremental.fingerprint_inputs(inputs))
    case = root / "fixture/raw/case.txt"
    case.write_bytes(case.read_bytes() + b"\nfield change\n")
    plan = incremental.build_plan(inputs, state)
    page_key = "fixture-response.md|fixture|char:mini"
    impacts = plan["field_impacts"][page_key]
    assert impacts
    assert all("field" in item and item["action"] in ("evidence_rebind", "validation") for item in impacts)


def test_explain_snapshot_detects_outdated_plan(tmp_path, monkeypatch, capsys):
    inputs, root = _setup_project(tmp_path, monkeypatch)
    state = _write_baseline(root, incremental.fingerprint_inputs(inputs))
    plan_path = root / "reports/incremental/plan.json"
    plan_path.write_text(json.dumps(incremental.build_plan(inputs, state), ensure_ascii=False), encoding="utf-8")
    case = root / "fixture/raw/case.txt"
    case.write_bytes(case.read_bytes() + b"\nnew input\n")
    rc = incremental.main(
        [
            "explain",
            "--inputs",
            str(root / "config/incremental-inputs.json"),
            "--state",
            str(state),
            "--plan",
            str(plan_path),
            "--graph",
            str(root / "reports/incremental/graph.json"),
            "--snapshot-only",
            "--page",
            "fixture-response.md|fixture|char:mini",
        ]
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "plan_outdated" in out


def test_baseline_graph_diagnostics_invalid_is_typed(tmp_path, monkeypatch):
    inputs, root = _setup_project(tmp_path, monkeypatch)
    state = _write_baseline(root, incremental.fingerprint_inputs(inputs))
    data = json.loads(state.read_text(encoding="utf-8"))
    data["graph"]["diagnostics"] = 1
    state.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    plan = incremental.build_plan(inputs, state)
    assert plan["full_required"] is True
    assert any(item.startswith("state_") for item in plan["unknown"])


def test_baseline_common_hash_object_is_typed(tmp_path, monkeypatch):
    inputs, root = _setup_project(tmp_path, monkeypatch)
    state = _write_baseline(root, incremental.fingerprint_inputs(inputs))
    data = json.loads(state.read_text(encoding="utf-8"))
    data["inputs"]["common"]["registry"] = {"invalid": "hash"}
    state.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    plan = incremental.build_plan(inputs, state)
    assert plan["full_required"] is True
    assert any(item.startswith("state_") for item in plan["unknown"])


def test_non_utf8_state_is_corrupt_without_crash(tmp_path, monkeypatch):
    inputs, root = _setup_project(tmp_path, monkeypatch)
    state = root / "reports/incremental/state.json"
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_bytes(b"\xff\xfe\x00")
    plan = incremental.build_plan(inputs, state)
    assert plan["full_required"] is True
    assert "state_corrupt" in plan["unknown"]


def test_direct_external_owner_is_not_a_change_seed():
    inputs = {"inputs": [{"bundle_key": "shared", "managed_pages": [{"page_key": "a"}, {"page_key": "b"}]}]}
    graph = {
        "nodes": [
            {"key": ["page", "a"]},
            {"key": ["page", "b"]},
            {"key": ["b", "event", "event:b"]},
        ],
        "edges": [
            {"consumer": ["page", "a"], "dependency": ["b", "event", "event:b"]},
            {"consumer": ["page", "b"], "dependency": ["b", "event", "event:b"]},
        ],
    }
    dirty, _blocked, full = incremental.mark_dirty(
        [{"kind": "response_changed", "bundle_key": "shared", "page_keys": ["a"]}], inputs, graph
    )
    assert full is False
    assert set(dirty) == {"a"}


def _write_plan(root, plan, name="plan.json"):
    path = root / "reports/incremental" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    return path


def test_b201_missing_state_reports_bootstrap_and_scope(tmp_path, monkeypatch):
    inputs, root = _setup_project(tmp_path, monkeypatch)
    plan = incremental.build_plan(inputs, root / "reports/incremental/missing-state.json")
    plan_path = _write_plan(root, plan)
    report = incremental.precheck_rebuild(
        plan_path,
        root / "reports/incremental/missing-state.json",
        root / "config/incremental-inputs.json",
        None,
    )
    assert report["action"] == "reject"
    assert report["executable"] is False
    assert any("state_unavailable" in item for item in report["errors"])
    assert any("full_required_not_supported" in item for item in report["errors"])
    assert report["affected_pages"]


def test_b201_single_page_candidate_binding(tmp_path, monkeypatch):
    inputs, root = _setup_project(tmp_path, monkeypatch)
    state = _write_baseline(root, incremental.fingerprint_inputs(inputs))
    _write(root / "reports/mini.md", "changed-output")
    plan = incremental.build_plan(inputs, state)
    plan_path = _write_plan(root, plan)
    manifest = root / "candidate.json"
    manifest.write_text(
        json.dumps(
            {
                "format_version": 1,
                "candidates": [
                    {
                        "page_key": "fixture-response.md|fixture|char:mini",
                        "response": "fixture-response.md",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    report = incremental.precheck_rebuild(plan_path, state, root / "config/incremental-inputs.json", manifest)
    assert report["action"] == "single_page"
    assert report["executable"] is True
    assert report["candidate_binding_passed"] is True
    assert report["full_validation_passed"] == "not_run"


def test_b201_candidate_binding_mismatch(tmp_path, monkeypatch):
    inputs, root = _setup_project(tmp_path, monkeypatch)
    state = _write_baseline(root, incremental.fingerprint_inputs(inputs))
    _write(root / "reports/mini.md", "changed-output")
    plan = incremental.build_plan(inputs, state)
    plan_path = _write_plan(root, plan)
    other = root / "other-response.md"
    _write(other, "different bytes")
    manifest = root / "candidate.json"
    manifest.write_text(
        json.dumps(
            {
                "format_version": 1,
                "candidates": [
                    {
                        "page_key": "fixture-response.md|fixture|char:mini",
                        "response": "other-response.md",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    report = incremental.precheck_rebuild(plan_path, state, root / "config/incremental-inputs.json", manifest)
    assert report["action"] == "single_page"
    assert report["executable"] is False
    assert "candidate_binding_mismatch" in report["errors"]


def test_b201_tampered_plan_is_rejected(tmp_path, monkeypatch):
    inputs, root = _setup_project(tmp_path, monkeypatch)
    state = _write_baseline(root, incremental.fingerprint_inputs(inputs))
    _write(root / "reports/mini.md", "changed-output")
    plan = incremental.build_plan(inputs, state)
    plan["dirty"] = {}
    plan["affected_pages"] = []
    plan_path = _write_plan(root, plan)
    report = incremental.precheck_rebuild(plan_path, state, root / "config/incremental-inputs.json", None)
    assert report["action"] == "reject"
    assert "plan_mismatch" in report["errors"]


def test_b201_commit_flag_is_not_implemented(tmp_path, monkeypatch, capsys):
    inputs, root = _setup_project(tmp_path, monkeypatch)
    state = _write_baseline(root, incremental.fingerprint_inputs(inputs))
    _write(root / "reports/mini.md", "changed-output")
    plan = incremental.build_plan(inputs, state)
    plan_path = _write_plan(root, plan)
    rc = incremental.main(
        [
            "rebuild",
            "--plan",
            str(plan_path),
            "--state",
            str(state),
            "--inputs",
            str(root / "config/incremental-inputs.json"),
            "--commit",
        ]
    )
    assert rc == 3
    out = capsys.readouterr().out
    assert "commit_not_implemented" in out

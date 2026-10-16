# -*- coding: utf-8 -*-
"""B203 T1-A RED：多页入口契约（当前预期 API 缺失）。"""
import copy
import importlib
import json
from pathlib import Path

import pytest

from . import support


def load_multi_api():
    try:
        return importlib.import_module("app.rebuild_multi")
    except ModuleNotFoundError as exc:
        if exc.name != "app.rebuild_multi":
            raise
        pytest.fail("B203_API_MISSING: app.rebuild_multi", pytrace=False)


def _precheck(api, case):
    return api.precheck_rebuild(case["plan"], case["state"], case["config"], case["manifest"])


def _commit(api, case):
    return api.commit_rebuild(case["plan"], case["state"], case["config"], case["manifest"])


def _assert_files_unchanged(case, before_files):
    assert support.snapshot_files(case["root"]) == before_files


def _assert_files_and_dirs_unchanged(case, before_files, before_dirs):
    _assert_files_unchanged(case, before_files)
    assert support.snapshot_dirs(case["root"]) == before_dirs


def _assert_reject_pair(api, case, expected, commit_status="rejected"):
    report = _precheck(api, case)
    assert report["action"] == "reject", report
    assert report["executable"] is False
    support.assert_error_member(report["errors"], expected)
    _assert_files_and_dirs_unchanged(case, case["_before_files"], case["_before_dirs"])
    result = _commit(api, case)
    assert result["status"] == commit_status, result
    support.assert_error_member(result.get("errors") or [], expected)
    _assert_files_unchanged(case, case["_before_files"])


def _read_manifest(case):
    return json.loads(case["manifest"].read_text(encoding="utf-8"))


def _write_manifest(case, manifest):
    support.write_json(case["manifest"], manifest)


def _prepare_before(case):
    case["_before_files"] = support.snapshot_files(case["root"])
    case["_before_dirs"] = support.snapshot_dirs(case["root"])


def _register_before(case):
    case["_before_files"] = support.snapshot_files(case["root"])
    case["_before_dirs"] = support.snapshot_dirs(case["root"])


def test_multi_happy_two_pages(tmp_path, monkeypatch):
    case = support.make_multi_case(tmp_path, monkeypatch)
    api = load_multi_api()
    _prepare_before(case)
    report = _precheck(api, case)
    assert report["action"] == "multi_page", report
    assert report["executable"] is True
    assert report["build_set"] == sorted((case["page_keys"]["a"], case["page_keys"]["b"]))
    assert report["errors"] == []
    assert report["candidate_binding_passed"] is True
    assert report["full_validation_passed"] == "not_run"
    _assert_files_and_dirs_unchanged(case, case["_before_files"], case["_before_dirs"])

    result = _commit(api, case)
    assert result["status"] == "committed", result
    assert result["request_id"] and result["transaction_id"]

    state = json.loads(case["state"].read_text(encoding="utf-8"))
    assert state["format_version"] == 1
    build = state["build"]
    assert build["version"] == 2
    assert set(build["pages"]) == {case["page_keys"]["a"], case["page_keys"]["b"]}
    assert build["parent_generation"] == case["parent_generation"]
    assert build["plan_generation"] == case["plan_data"]["generation"]
    assert build["generation"] == state["generation"]
    for entry in build["pages"].values():
        assert entry["generation"] == state["generation"]
    assert state["graph"]["generation"] == state["generation"]
    assert state["generation"] != case["parent_generation"]
    loaded, errors = support.inc.load_baseline(case["state"])
    assert loaded is not None, errors

    candidates = _read_manifest(case)["candidates"]
    expected_request = support.expected_request_id(case, case["plan_data"], candidates)
    expected_transaction = support.expected_transaction_id(expected_request, build["execution_token"])
    assert build["request_id"] == result["request_id"] == expected_request
    assert build["transaction_id"] == result["transaction_id"] == expected_transaction

    expected_provenance_dirs = set()
    for page_key, entry in build["pages"].items():
        managed = case["registry"][page_key]
        assert entry["response"]["path"] == managed["response"]
        assert entry["output"]["path"] == managed["output"]
        assert entry["response"]["sha256"] == support.sha256_file(case["root"] / managed["response"])
        assert entry["output"]["sha256"] == support.sha256_file(case["root"] / managed["output"])
        expected_dir = support.expected_provenance_dir(case, result["transaction_id"], page_key)
        assert entry["provenance"]["dir"] == expected_dir
        expected_provenance_dirs.add(expected_dir)
        support.verify_provenance(case["root"], entry, case["snapshots"][page_key])

    assert (case["root"] / "reports/fixtures/a.md").read_bytes() == case["expected_outputs"][
        case["page_keys"]["a"]
    ]
    assert (case["root"] / "reports/fixtures/b.md").read_bytes() == case["expected_outputs"][
        case["page_keys"]["b"]
    ]
    assert (case["root"] / "reports/fixtures/c.md").read_bytes() == case["expected_outputs"][
        case["page_keys"]["c"]
    ]

    allowed = {
        "reports/fixtures/a.md",
        "reports/fixtures/b.md",
        "reports/incremental/state.json",
    }
    support.assert_allowed_changes(
        case["_before_files"],
        support.snapshot_files(case["root"]),
        allowed,
        expected_provenance_dirs,
    )

    assert not (case["root"] / "reports/incremental/commit.lock").exists()
    assert not list((case["root"] / "reports/incremental/journal").glob("*"))
    staging = case["root"] / "reports/incremental/staging"
    assert not staging.exists() or not any(staging.rglob("*"))
    backups = case["root"] / "reports/incremental/backups"
    assert not backups.exists() or not any(backups.rglob("*"))

    graph = state["graph"].copy()
    graph.pop("generation", None)
    original = copy.deepcopy(case["original_graph"])
    original.pop("generation", None)
    assert support.C(graph) == support.C(original)

    current = support.inc.build_plan(case["inputs"], case["state"])
    assert not current["dirty"] and not current["full_required"] and not current["blocked"] and not current["unknown"]


@pytest.mark.parametrize("mutation", ["missing", "extra", "duplicate", "unknown"])
def test_multi_candidate_set_mismatch(tmp_path, monkeypatch, mutation):
    case = support.make_multi_case(tmp_path, monkeypatch)
    api = load_multi_api()
    manifest = _read_manifest(case)
    if mutation == "missing":
        manifest["candidates"] = [
            item for item in manifest["candidates"] if item["page_key"] != case["page_keys"]["b"]
        ]
        expected = "candidate_set_mismatch"
    elif mutation == "extra":
        manifest["candidates"].append(
            {
                "page_key": case["page_keys"]["c"],
                "response": case["registry"][case["page_keys"]["c"]]["response"],
            }
        )
        expected = "candidate_set_mismatch"
    elif mutation == "duplicate":
        manifest["candidates"].append(dict(manifest["candidates"][0]))
        expected = f"candidate_manifest_invalid:{case['page_keys']['a']}"
    else:
        for item in manifest["candidates"]:
            if item["page_key"] == case["page_keys"]["b"]:
                item["page_key"] = "responses/z.md|fixture|char:traveler"
                item["response"] = case["registry"][case["page_keys"]["a"]]["response"]
        expected = "candidate_set_mismatch"
    _write_manifest(case, manifest)
    _register_before(case)
    _assert_reject_pair(api, case, expected)


def test_multi_candidate_binding_mismatch(tmp_path, monkeypatch):
    case = support.make_multi_case(tmp_path, monkeypatch)
    api = load_multi_api()
    source = case["root"] / case["registry"][case["page_keys"]["b"]]["response"]
    copy_path = case["root"] / "responses/b-copy.md"
    copy_path.write_bytes(source.read_bytes() + b"\n")
    assert support.sha256_file(copy_path) != support.sha256_file(source)
    manifest = _read_manifest(case)
    for item in manifest["candidates"]:
        if item["page_key"] == case["page_keys"]["b"]:
            item["response"] = "responses/b-copy.md"
    _write_manifest(case, manifest)
    _register_before(case)
    _assert_reject_pair(api, case, "candidate_binding_mismatch")


def test_multi_replay_all_pages(tmp_path, monkeypatch):
    case = support.make_multi_case(tmp_path, monkeypatch)
    api = load_multi_api()
    first = _commit(api, case)
    assert first["status"] == "committed", first
    before_files = support.snapshot_files(case["root"])
    before_dirs = support.snapshot_dirs(case["root"])
    replay = _commit(api, case)
    assert replay["status"] == "already_committed", replay
    assert replay["request_id"] == first["request_id"]
    assert replay["transaction_id"] == first["transaction_id"]
    _assert_files_and_dirs_unchanged(case, before_files, before_dirs)


PLAN_TAMPERS = (
    ("generation", "tampered-generation", "plan_mismatch"),
    ("observed", "__OBSERVED__", "plan_mismatch"),
    ("dirty", "__DROP_B__", "plan_mismatch"),
    ("full_required", True, "plan_mismatch"),
    ("blocked", ["tampered"], "plan_mismatch"),
    ("unknown", ["tampered"], "plan_mismatch"),
    ("affected_pages", "__ONLY_A__", "plan_mismatch"),
    ("baseline_generation", "tampered-parent", "plan_outdated"),
    ("execution_protocol", "unsupported-protocol", "plan_execution_protocol_mismatch"),
)


@pytest.mark.parametrize("field,value,expected", PLAN_TAMPERS)
def test_multi_plan_tamper(tmp_path, monkeypatch, field, value, expected):
    case = support.make_multi_case(tmp_path, monkeypatch)
    api = load_multi_api()
    plan = json.loads(case["plan"].read_text(encoding="utf-8"))
    if value == "__OBSERVED__":
        for entry in plan["observed"]["inputs"].values():
            if case["page_keys"]["a"] in entry["pages"]:
                entry["pages"][case["page_keys"]["a"]]["output"] = "f" * 64
    elif value == "__DROP_B__":
        plan["dirty"].pop(case["page_keys"]["b"], None)
    elif value == "__ONLY_A__":
        plan["affected_pages"] = [case["page_keys"]["a"]]
    else:
        plan[field] = value
    support.write_json(case["plan"], plan)
    _register_before(case)
    _assert_reject_pair(api, case, expected)


def test_multi_plan_stale(tmp_path, monkeypatch):
    case = support.make_multi_case(tmp_path, monkeypatch)
    api = load_multi_api()
    loaded, errors = support.inc.load_baseline(case["state"])
    assert loaded is not None, errors
    replacement = support.inc.make_baseline(loaded["inputs"], "fixture-parent-v2", loaded["graph"])
    support.write_json(case["state"], replacement)
    loaded, errors = support.inc.load_baseline(case["state"])
    assert loaded is not None, errors
    assert loaded["generation"] == "fixture-parent-v2"
    _register_before(case)
    _assert_reject_pair(api, case, "plan_outdated")


@pytest.mark.parametrize("mode", ["missing", "corrupt"])
def test_multi_state_missing_or_corrupt(tmp_path, monkeypatch, mode):
    case = support.make_multi_case(tmp_path, monkeypatch)
    api = load_multi_api()
    if mode == "missing":
        case["state"].unlink()
        expected = "bootstrap_not_supported"
    else:
        case["state"].write_text("{invalid", encoding="utf-8")
        expected = "unknown:state_corrupt"
    _register_before(case)
    _assert_reject_pair(api, case, expected)


@pytest.mark.parametrize("mode", ["clean", "extra_candidate", "bad_manifest", "unresolved_journal"])
def test_multi_zero_page_contract(tmp_path, monkeypatch, mode):
    case = support.make_multi_case(tmp_path, monkeypatch, dirty=())
    api = load_multi_api()
    manifest = {"format_version": 1, "candidates": []}
    if mode == "extra_candidate":
        manifest["candidates"] = [
            {
                "page_key": case["page_keys"]["a"],
                "response": case["registry"][case["page_keys"]["a"]]["response"],
            }
        ]
    elif mode == "bad_manifest":
        manifest = {}
    _write_manifest(case, manifest)
    if mode == "unresolved_journal":
        journal = case["root"] / "reports/incremental/journal/test-unresolved.json"
        journal.parent.mkdir(parents=True, exist_ok=True)
        journal.write_text('{"status": "prepared"}', encoding="utf-8")
    _register_before(case)

    report = _precheck(api, case)
    if mode == "clean":
        assert report["action"] == "no_op", report
        assert report["executable"] is False
        assert report["build_set"] == []
        assert report["errors"] == []
        _assert_files_and_dirs_unchanged(case, case["_before_files"], case["_before_dirs"])
        result = _commit(api, case)
        assert result["status"] == "no_op", result
        _assert_files_and_dirs_unchanged(case, case["_before_files"], case["_before_dirs"])
        return
    if mode == "extra_candidate":
        expected = "candidate_set_mismatch"
        commit_status = "rejected"
    elif mode == "bad_manifest":
        expected = "candidate_manifest_invalid"
        commit_status = "rejected"
    else:
        expected = "journal_unresolved:test-unresolved.json"
        commit_status = "recovery_required"
    assert report["action"] == "reject", report
    assert report["executable"] is False
    support.assert_error_member(report["errors"], expected)
    _assert_files_and_dirs_unchanged(case, case["_before_files"], case["_before_dirs"])
    result = _commit(api, case)
    assert result["status"] == commit_status, result
    support.assert_error_member(result.get("errors") or [], expected)
    _assert_files_unchanged(case, case["_before_files"])

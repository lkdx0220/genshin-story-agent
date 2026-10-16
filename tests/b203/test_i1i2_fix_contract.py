# -*- coding: utf-8 -*-
"""I1/I2 修复契约：登记保护、最终 CAS、重放、恢复所有权、项目锁。"""
import copy
import json

import pytest

from app import rebuild as single
from app import rebuild_multi as api
from . import recovery_support as rs
from . import support


def commit(case):
    return api.commit_rebuild(case["plan"], case["state"], case["config"], case["manifest"])


def replan(case):
    case["inputs"] = support.inc.load_inputs(case["config"])
    observed = support.inc.fingerprint_inputs(case["inputs"])
    graph = support.inc.collect_dependencies(case["inputs"])
    baseline = support.inc.make_baseline(observed, "fixture-parent", graph)
    for item in case["inputs"]["inputs"]:
        for page in item["managed_pages"]:
            baseline["inputs"]["inputs"][item["bundle_key"]]["pages"][page["page_key"]]["output"] = support.sha256_bytes(
                case["expected_outputs"][page["page_key"]]
            )
    support.write_json(case["state"], baseline)
    plan = support.inc.build_plan(case["inputs"], case["state"])
    assert not plan["unknown"] and not plan["blocked"], plan
    support.write_json(case["plan"], plan)
    scope = api._managed_scope(case["inputs"], plan)
    registry = support.inc._page_registry(case["inputs"])
    support.write_json(
        case["manifest"],
        {"format_version": 1, "candidates": [{"page_key": key, "response": registry[key]["response"]} for key in scope]},
    )
    return plan, registry


@pytest.mark.parametrize("damage", ["outside", "protected", "duplicate"])
def test_i12_r01_rejects_unsafe_registered_outputs(tmp_path, monkeypatch, damage):
    case = support.make_multi_case(tmp_path, monkeypatch)
    config = json.loads(case["config"].read_bytes())
    a = config["inputs"][0]["managed_pages"][0]
    b = config["inputs"][1]["managed_pages"][0]
    if damage == "outside":
        target = case["root"].parent / "outside-sentinel.md"
        target.write_bytes(b"outside must not change")
        a["output"] = "../outside-sentinel.md"
    elif damage == "protected":
        a["output"] = "docs/schema-lock.json"
        target = case["root"] / a["output"]
    else:
        b["output"] = a["output"]
        target = case["root"] / a["output"]
    before = target.read_bytes()
    support.write_json(case["config"], config)
    replan(case)
    report = api.precheck_rebuild(case["plan"], case["state"], case["config"], case["manifest"])
    assert report["action"] == "reject", report
    result = commit(case)
    assert result["status"] != "committed", result
    assert target.read_bytes() == before


@pytest.mark.parametrize("damage", ["response", "plan", "parent"])
def test_i12_r02_final_cas_blocks_staging_changes(tmp_path, monkeypatch, damage):
    case = support.make_multi_case(tmp_path, monkeypatch)
    state_before = case["state"].read_bytes()
    outputs_before = {
        key: (case["root"] / managed["output"]).read_bytes() for key, managed in case["registry"].items()
    }
    original = api._stage_all

    def stage_then_change(ctx):
        staged, error = original(ctx)
        assert error is None
        if damage == "response":
            response = case["root"] / "responses/b.md"
            response.write_bytes(response.read_bytes() + b"\n")
        elif damage == "plan":
            plan = json.loads(case["plan"].read_bytes())
            plan["generation"] = "changed-after-staging"
            support.write_json(case["plan"], plan)
        else:
            state = json.loads(case["state"].read_bytes())
            state["generation"] = "new-parent-after-staging"
            state["graph"]["generation"] = state["generation"]
            support.write_json(case["state"], state)
        return staged, error

    monkeypatch.setattr(api, "_stage_all", stage_then_change)
    result = commit(case)
    assert result["status"] != "committed", result
    if damage != "parent":
        assert case["state"].read_bytes() == state_before
    for key, payload in outputs_before.items():
        assert (case["root"] / case["registry"][key]["output"]).read_bytes() == payload


@pytest.mark.parametrize("damage", ["output", "provenance", "invalid_baseline", "unresolved_journal"])
def test_i12_r03_replay_rejects_broken_resources(tmp_path, monkeypatch, damage):
    case = support.make_multi_case(tmp_path, monkeypatch)
    assert commit(case)["status"] == "committed"
    state = json.loads(case["state"].read_bytes())
    key = case["page_keys"]["b"]
    if damage == "output":
        (case["root"] / state["build"]["pages"][key]["output"]["path"]).write_bytes(b"unrelated")
    elif damage == "provenance":
        (case["root"] / state["build"]["pages"][key]["provenance"]["dir"] / "manifest.json").unlink()
    elif damage == "invalid_baseline":
        state.pop("graph")
        support.write_json(case["state"], state)
    else:
        (case["root"] / "reports/incremental/journal/unfinished.json").write_bytes(b"corrupt")
    before = support.snapshot_files(case["root"])
    result = commit(case)
    assert result["status"] != "already_committed", result
    assert support.snapshot_files(case["root"]) == before


@pytest.mark.parametrize("damage", ["project_id", "output_redirect", "foreign_backup"])
def test_i12_r04_recover_rejects_invalid_journal(tmp_path, monkeypatch, damage):
    case = rs.make_recovery_case(tmp_path, monkeypatch)
    rs.materialize_layout(case, state_variant="old", outputs="old", provenance="full", backups="full")
    journal_path = case["root"] / "reports/incremental/journal" / case["journal_name"]
    journal = json.loads(journal_path.read_bytes())
    target = None
    if damage == "project_id":
        journal["project_root_id"] = "another-project"
    elif damage == "output_redirect":
        page = journal["pages"][0]
        target = case["root"] / "protected-sentinel.txt"
        target.write_bytes(b"must preserve")
        page["output"]["path"] = "protected-sentinel.txt"
        page["output"]["old_exists"] = False
        page["output"]["new_sha256"] = support.sha256_file(target)
    else:
        target = case["root"] / "foreign-backup.txt"
        target.write_bytes(b"not owned by this transaction")
        journal["pages"][0]["backup"]["path"] = "foreign-backup.txt"
    support.write_json(journal_path, journal)
    before = support.snapshot_files(case["root"])
    result = api.recover_transaction(case["txid"])
    assert result["status"] != "ok", result
    assert support.snapshot_files(case["root"]) == before
    if target is not None:
        assert target.exists()


def test_i12_r04_success_preserves_sibling_staging(tmp_path, monkeypatch):
    case = support.make_multi_case(tmp_path, monkeypatch)
    original = api._publish
    sentinels = []

    def publish_with_sibling(ctx, state):
        sentinel = case["root"] / "reports/incremental/staging" / ctx["txid"] / "other-token/sentinel.txt"
        sentinel.parent.mkdir(parents=True)
        sentinel.write_bytes(b"protected sibling")
        sentinels.append(sentinel)
        return original(ctx, state)

    monkeypatch.setattr(api, "_publish", publish_with_sibling)
    assert commit(case)["status"] == "committed"
    assert sentinels and sentinels[0].read_bytes() == b"protected sibling"


def test_i12_r05_recover_respects_project_lock(tmp_path, monkeypatch):
    case = rs.make_recovery_case(tmp_path, monkeypatch)
    rs.materialize_layout(case, state_variant="old", outputs="new", provenance="full", backups="full")
    lock = case["root"] / "reports/incremental/commit.lock"
    lock.write_bytes(b'{"transaction_id":"active-owner","pid":12345}')
    before = support.snapshot_files(case["root"])
    assert single.recover_transaction(case["txid"])["status"] == "lock_busy"
    assert api.recover_transaction(case["txid"])["status"] == "lock_busy"
    assert support.snapshot_files(case["root"]) == before


def test_i12_nb01_journal_phases_and_backup_order(tmp_path, monkeypatch):
    case = support.make_multi_case(tmp_path, monkeypatch)
    original = api._atomic_write_json
    events = []

    def inspect_write(path, payload):
        if path.parent == api._journal_dir():
            events.append(
                {
                    "status": payload["status"],
                    "backups_exist": [
                        (case["root"] / page["backup"]["path"]).is_file() for page in payload["pages"]
                    ],
                }
            )
        return original(path, payload)

    monkeypatch.setattr(api, "_atomic_write_json", inspect_write)
    assert commit(case)["status"] == "committed"
    statuses = [event["status"] for event in events]
    assert "prepared" in statuses and "pages_replaced" in statuses and "committed" in statuses
    prepared = next(event for event in events if event["status"] == "prepared")
    assert all(prepared["backups_exist"])


@pytest.mark.parametrize("damage", ["response", "plan", "parent"])
def test_i12_r02_cas_after_state_construction(tmp_path, monkeypatch, damage):
    case = support.make_multi_case(tmp_path, monkeypatch)
    original = api._build_state

    def build_then_change(ctx, staged):
        result = original(ctx, staged)
        if damage == "response":
            path = case["root"] / "responses/b.md"
            path.write_bytes(path.read_bytes() + b"\n")
        elif damage == "plan":
            plan = json.loads(case["plan"].read_bytes())
            plan["generation"] = "changed-during-state-construction"
            support.write_json(case["plan"], plan)
        else:
            state = json.loads(case["state"].read_bytes())
            state["generation"] = "new-parent-during-state-construction"
            state["graph"]["generation"] = state["generation"]
            support.write_json(case["state"], state)
        return result

    monkeypatch.setattr(api, "_build_state", build_then_change)
    result = commit(case)
    assert result["status"] != "committed", result


def test_i12_r02_frozen_candidate_hash_bound(tmp_path, monkeypatch):
    case = support.make_multi_case(tmp_path, monkeypatch)
    original = api._stage_all

    def stage_transient(ctx):
        response = case["root"] / "responses/b.md"
        original_bytes = response.read_bytes()
        variant = original_bytes.replace(b"---\n{", b"---\n{ ", 1)
        assert variant != original_bytes
        response.write_bytes(variant)
        try:
            staged, error = original(ctx)
            assert error is None
            return staged, error
        finally:
            response.write_bytes(original_bytes)

    monkeypatch.setattr(api, "_stage_all", stage_transient)
    result = commit(case)
    assert result["status"] != "committed", result


@pytest.mark.parametrize("damage", ["member", "registered_output", "transaction_id", "lock"])
def test_i12_r03_replay_residual_rejects(tmp_path, monkeypatch, damage):
    case = support.make_multi_case(tmp_path, monkeypatch)
    assert commit(case)["status"] == "committed"
    state = json.loads(case["state"].read_bytes())
    key = case["page_keys"]["b"]
    entry = state["build"]["pages"][key]
    if damage == "member":
        directory = case["root"] / entry["provenance"]["dir"]
        manifest = json.loads((directory / "manifest.json").read_bytes())
        member = directory / manifest["files"][0]["path"]
        member.write_bytes(member.read_bytes() + b"\ncorrupt")
    elif damage == "registered_output":
        actual = case["root"] / entry["output"]["path"]
        copy_path = case["root"] / "reports/foreign-output.md"
        copy_path.write_bytes(actual.read_bytes())
        actual.write_bytes(b"corrupt registered output")
        entry["output"]["path"] = "reports/foreign-output.md"
        support.write_json(case["state"], state)
    elif damage == "transaction_id":
        state["build"]["transaction_id"] = "not-the-transaction"
        support.write_json(case["state"], state)
    else:
        (case["root"] / "reports/incremental/commit.lock").write_bytes(b'{"transaction_id":"active-owner"}')
    before = support.snapshot_files(case["root"])
    result = commit(case)
    assert result["status"] != "already_committed", result
    if damage == "lock":
        assert result["status"] == "lock_busy", result
    assert support.snapshot_files(case["root"]) == before


def test_i12_r04_state_redirect_rejected(tmp_path, monkeypatch):
    case = rs.make_recovery_case(tmp_path, monkeypatch)
    case["request_payload"]["state"] = "reports/foreign-state.json"
    case["request_id"] = support.sha256_bytes(support.C(case["request_payload"]))
    case["journal"]["request_payload"] = copy.deepcopy(case["request_payload"])
    case["journal"]["request_id"] = case["request_id"]
    case["journal"]["state"]["path"] = "reports/foreign-state.json"
    new_state = json.loads(case["new_state_bytes"])
    new_state["build"]["request_id"] = case["request_id"]
    case["new_state_bytes"] = rs._dump_state(new_state)
    case = rs.clone_with_token(case, "resealed-state-redirect")
    rs.materialize_layout(case, state_variant="new", outputs="new", provenance="full", backups="full")
    (case["root"] / "reports/foreign-state.json").write_bytes(case["old_state_bytes"])
    before = support.snapshot_files(case["root"])
    result = api.recover_transaction(case["txid"])
    assert result["status"] != "ok", result
    assert support.snapshot_files(case["root"]) == before


def test_i12_r03_replay_holds_project_lock(tmp_path, monkeypatch):
    case = support.make_multi_case(tmp_path, monkeypatch)
    assert commit(case)["status"] == "committed"
    other = []
    triggered = {"done": False}
    original_hash = api._hash_bytes

    def hash_with_contender(path):
        text = str(path).replace("\\", "/")
        if not triggered["done"] and "reports/fixtures/" in text:
            triggered["done"] = True
            handle, error = single._acquire_lock("other-active-owner")
            other.append(error or {"status": "lock_acquired"})
            if handle is not None:
                single._release_lock(handle)
        return original_hash(path)

    monkeypatch.setattr(api, "_hash_bytes", hash_with_contender)
    result = commit(case)
    assert result["status"] == "already_committed", result
    assert other and other[0].get("status") == "lock_busy", other
    handle, error = single._acquire_lock("after-replay")
    assert error is None, error
    single._release_lock(handle)

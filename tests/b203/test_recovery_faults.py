# -*- coding: utf-8 -*-
"""C3 真实故障矩阵：replace / provenance / cleanup 边界单次故障。"""
import json
import pathlib
import shutil
from pathlib import Path

import pytest

from app import rebuild_multi as api
from app import rebuild_multi_recovery as recovery
from . import recovery_support as rs
from . import support


def commit(case):
    return api.commit_rebuild(case["plan"], case["state"], case["config"], case["manifest"])


def outputs(case):
    return {key: (case["root"] / case["registry"][key]["output"]).read_bytes() for key in case["registry"]}


def assert_old(case, before):
    for key, payload in before.items():
        assert (case["root"] / case["registry"][key]["output"]).read_bytes() == payload


def assert_no_transaction_residue(case):
    assert not list((case["root"] / "reports/incremental/journal").glob("*"))
    staging = case["root"] / "reports/incremental/staging"
    backups = case["root"] / "reports/incremental/backups"
    assert not staging.exists() or not any(path.is_file() for path in staging.rglob("*"))
    assert not backups.exists() or not any(path.is_file() for path in backups.rglob("*"))


@pytest.mark.parametrize("position", [1, 2, 3])
def test_t211_t214_page_replace_failure_rolls_back_reverse(tmp_path, monkeypatch, position):
    case = support.make_multi_case(tmp_path, monkeypatch, dirty=("a", "b", "c"))
    before = outputs(case)
    state_before = case["state"].read_bytes()
    targets = [(case["root"] / case["registry"][key]["output"]).resolve() for key in sorted(case["registry"])]
    real_replace = api.os.replace
    events = []
    injected = []

    def wrapper(source, destination):
        destination = Path(destination).resolve()
        if destination in targets and not injected:
            events.append(destination)
            if len(events) == position:
                injected.append(True)
                raise PermissionError("injected replace failure")
        return real_replace(source, destination)

    monkeypatch.setattr(api.os, "replace", wrapper)
    result = commit(case)
    assert result["status"] == "rejected", result
    assert len([event for event in events if event in targets]) == position
    assert_old(case, before)
    assert case["state"].read_bytes() == state_before
    assert_no_transaction_residue(case)


def test_t214_state_replace_failure_rolls_back_all_outputs(tmp_path, monkeypatch):
    case = support.make_multi_case(tmp_path, monkeypatch, dirty=("a", "b", "c"))
    before = outputs(case)
    state_path = case["state"].resolve()
    real_replace = api.os.replace
    hit = []

    def wrapper(source, destination):
        destination = Path(destination).resolve()
        if destination == state_path:
            hit.append(True)
            raise PermissionError("injected state replace failure")
        return real_replace(source, destination)

    monkeypatch.setattr(api.os, "replace", wrapper)
    result = commit(case)
    assert result["status"] == "rejected", result
    assert hit == [True]
    assert_old(case, before)
    assert_no_transaction_residue(case)


def test_t215_provenance_move_failure_keeps_old_version(tmp_path, monkeypatch):
    case = support.make_multi_case(tmp_path, monkeypatch, dirty=("a", "b", "c"))
    before = outputs(case)
    state_before = case["state"].read_bytes()
    real_move = api.shutil.move
    hit = []

    def wrapper(source, destination):
        destination = Path(destination)
        if "reports/incremental/provenance" in str(destination).replace("\\", "/"):
            hit.append(True)
            raise PermissionError("injected provenance move failure")
        return real_move(source, destination)

    monkeypatch.setattr(api.shutil, "move", wrapper)
    result = commit(case)
    assert result["status"] == "rejected", result
    assert hit == [True]
    assert_old(case, before)
    assert case["state"].read_bytes() == state_before
    provenance_root = case["root"] / "reports/incremental/provenance"
    assert not provenance_root.exists() or not any(path.is_file() for path in provenance_root.rglob("*"))
    assert_no_transaction_residue(case)


def test_t219_committed_journal_failure_keeps_new_version_pending(tmp_path, monkeypatch):
    case = support.make_multi_case(tmp_path, monkeypatch, dirty=("a", "b", "c"))
    real_write = api._atomic_write_json
    hit = []

    def wrapper(path, payload):
        if payload.get("status") == "committed" and not hit:
            hit.append(True)
            raise PermissionError("injected committed journal failure")
        return real_write(path, payload)

    monkeypatch.setattr(api, "_atomic_write_json", wrapper)
    result = commit(case)
    assert result["status"] == "committed_cleanup_pending", result
    assert hit == [True]
    state = json.loads(case["state"].read_bytes())
    assert state["generation"] != "fixture-parent"
    for key in case["registry"]:
        assert (case["root"] / case["registry"][key]["output"]).read_bytes() == case["expected_outputs"][key]
    assert list((case["root"] / "reports/incremental/journal").glob("*"))

    # fault consumed; recovery must clean the committed new version
    recovery_result = api.recover_transaction()
    assert recovery_result["status"] == "ok", recovery_result
    assert_no_transaction_residue(case)


def test_t220_backup_cleanup_failure_keeps_journal_and_retries(tmp_path, monkeypatch):
    case = support.make_multi_case(tmp_path, monkeypatch, dirty=("a", "b", "c"))
    real_unlink = pathlib.Path.unlink
    hit = []

    def wrapper(self, *args, **kwargs):
        text = str(self).replace("\\", "/")
        if "/reports/incremental/backups/" in text and text.endswith(".bak") and not hit:
            hit.append(True)
            raise PermissionError("injected backup unlink failure")
        return real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(pathlib.Path, "unlink", wrapper)
    result = commit(case)
    assert result["status"] == "committed_cleanup_pending", result
    assert hit == [True]
    assert list((case["root"] / "reports/incremental/journal").glob("*"))

    # fault consumed; recovery sees new state and finishes cleanup
    recovery_result = api.recover_transaction()
    assert recovery_result["status"] == "ok", recovery_result
    assert_no_transaction_residue(case)


def test_t221_staging_cleanup_failure_keeps_journal_and_retries(tmp_path, monkeypatch):
    case = support.make_multi_case(tmp_path, monkeypatch, dirty=("a", "b", "c"))
    real_rmtree = api.shutil.rmtree
    hit = []

    def wrapper(path, *args, **kwargs):
        text = str(path).replace("\\", "/")
        if "/reports/incremental/staging/" in text and not hit:
            hit.append(True)
            raise PermissionError("injected staging cleanup failure")
        return real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(api.shutil, "rmtree", wrapper)
    result = commit(case)
    assert result["status"] == "committed_cleanup_pending", result
    assert hit == [True]
    assert list((case["root"] / "reports/incremental/journal").glob("*"))

    recovery_result = api.recover_transaction()
    assert recovery_result["status"] == "ok", recovery_result
    assert_no_transaction_residue(case)


def test_t222_rollback_midway_failure_then_retry_completes(tmp_path, monkeypatch):
    case = rs.make_recovery_case(tmp_path, monkeypatch)
    rs.materialize_layout(case, state_variant="old", outputs="new", provenance="full", backups="full")
    real_replace = recovery.os.replace
    hit = []
    seen = []

    def wrapper(source, destination):
        destination = Path(destination)
        if destination.name.endswith(".restore.tmp") or ".restore.tmp" in destination.name:
            return real_replace(source, destination)
        text = str(destination).replace("\\", "/")
        if "/reports/fixtures/" in text:
            seen.append(destination)
            if len(seen) == 2 and not hit:
                hit.append(True)
                raise PermissionError("injected rollback failure")
        return real_replace(source, destination)

    monkeypatch.setattr(recovery.os, "replace", wrapper)
    result = api.recover_transaction(case["txid"])
    assert result["status"] == "recovery_required", result
    assert hit == [True]
    journal = case["root"] / "reports/incremental/journal" / case["journal_name"]
    assert journal.exists()
    # reverse order: C restored first, B restore failed, A still new
    assert (case["root"] / case["registry"][case["keys"][2]]["output"]).read_bytes() == case["old_outputs"][case["keys"][2]]
    assert (case["root"] / case["registry"][case["keys"][1]]["output"]).read_bytes() == case["new_outputs"][case["keys"][1]]
    assert (case["root"] / case["registry"][case["keys"][0]]["output"]).read_bytes() == case["new_outputs"][case["keys"][0]]

    retry = api.recover_transaction(case["txid"])
    assert retry["status"] == "ok", retry
    for key in case["scope"]:
        assert (case["root"] / case["registry"][key]["output"]).read_bytes() == case["old_outputs"][key]
    assert not journal.exists()


def test_t257_provenance_cleanup_failure_retries_idempotently(tmp_path, monkeypatch):
    case = rs.make_recovery_case(tmp_path, monkeypatch)
    rs.materialize_layout(case, state_variant="old", outputs="old", provenance="full", backups="full")
    real_rmtree = recovery.shutil.rmtree
    hit = []

    def wrapper(path, *args, **kwargs):
        text = str(path).replace("\\", "/")
        if "/reports/incremental/provenance/" in text and not hit:
            hit.append(True)
            raise PermissionError("injected provenance cleanup failure")
        return real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(recovery.shutil, "rmtree", wrapper)
    result = api.recover_transaction(case["txid"])
    assert result["status"] == "recovery_required", result
    assert hit == [True]
    journal = case["root"] / "reports/incremental/journal" / case["journal_name"]
    assert journal.exists()

    retry = api.recover_transaction(case["txid"])
    assert retry["status"] == "ok", retry
    assert not journal.exists()
    for key in case["scope"]:
        assert not (case["root"] / case["provenance"][key]["dir"]).exists()


def test_t258_state_replace_actually_new_then_error_keeps_new_and_pending(tmp_path, monkeypatch):
    case = support.make_multi_case(tmp_path, monkeypatch, dirty=("a", "b", "c"))
    state_path = case["state"].resolve()
    real_replace = api.os.replace
    hit = []

    def wrapper(source, destination):
        destination = Path(destination).resolve()
        result = real_replace(source, destination)
        if destination == state_path:
            hit.append(True)
            raise PermissionError("injected error after state replace")
        return result

    monkeypatch.setattr(api.os, "replace", wrapper)
    result = commit(case)
    assert result["status"] == "committed_cleanup_pending", result
    assert hit == [True]
    state = json.loads(case["state"].read_bytes())
    assert state["generation"] != "fixture-parent"
    for key in case["registry"]:
        assert (case["root"] / case["registry"][key]["output"]).read_bytes() == case["expected_outputs"][key]

    recovery_result = api.recover_transaction()
    assert recovery_result["status"] == "ok", recovery_result
    assert_no_transaction_residue(case)


def test_t260_retry_does_not_require_already_consumed_backups(tmp_path, monkeypatch):
    case = rs.make_recovery_case(tmp_path, monkeypatch)
    rs.materialize_layout(case, state_variant="old", outputs="new", provenance="full", backups="full")
    real_rmtree = recovery.shutil.rmtree
    hit = []

    def wrapper(path, *args, **kwargs):
        text = str(path).replace("\\", "/")
        if "/reports/incremental/provenance/" in text and not hit:
            hit.append(True)
            raise PermissionError("injected cleanup failure after backups consumed")
        return real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(recovery.shutil, "rmtree", wrapper)
    first = api.recover_transaction(case["txid"])
    assert first["status"] == "recovery_required", first
    assert hit == [True]
    assert all(
        not (case["root"] / case["backups"][key]["path"]).exists() for key in case["scope"]
    ), "first pass consumed backups"

    retry = api.recover_transaction(case["txid"])
    assert retry["status"] == "ok", retry
    for key in case["scope"]:
        assert (case["root"] / case["registry"][key]["output"]).read_bytes() == case["old_outputs"][key]
    assert not (case["root"] / "reports/incremental/journal" / case["journal_name"]).exists()


def assert_no_owned_provenance(case):
    root = case["root"] / "reports/incremental/provenance"
    assert not root.exists() or not any(path.is_file() for path in root.rglob("*"))


def test_c3_r01_replace_completed_then_error_keeps_no_mixed_version(tmp_path, monkeypatch):
    case = support.make_multi_case(tmp_path, monkeypatch, dirty=("a", "b", "c"))
    before = outputs(case)
    state_before = case["state"].read_bytes()
    real_replace = api.os.replace
    hit = []

    def wrapper(source, destination):
        result = real_replace(source, destination)
        if "reports/fixtures/" in str(destination).replace("\\", "/") and not hit:
            hit.append(True)
            raise PermissionError("injected error after real output replace")
        return result

    monkeypatch.setattr(api.os, "replace", wrapper)
    result = commit(case)
    assert hit == [True]
    assert result["status"] in ("rejected", "recovery_required"), result
    journal = case["root"] / "reports/incremental/journal" / f"{result.get('transaction_id', '')}.json"
    if result["status"] == "recovery_required":
        assert journal.exists()
        recovery_result = api.recover_transaction(journal.stem)
        assert recovery_result["status"] == "ok", recovery_result
    assert_old(case, before)
    assert case["state"].read_bytes() == state_before
    assert_no_transaction_residue(case)
    assert_no_owned_provenance(case)


def test_c3_r02_provenance_cleanup_failure_keeps_journal_and_recovers(tmp_path, monkeypatch):
    case = support.make_multi_case(tmp_path, monkeypatch, dirty=("a", "b", "c"))
    before = outputs(case)
    real_replace = api.os.replace
    real_rmtree = api.shutil.rmtree
    replace_hit = []
    rmtree_hit = []

    def replace_wrapper(source, destination):
        result = real_replace(source, destination)
        if "reports/fixtures/" in str(destination).replace("\\", "/") and not replace_hit:
            replace_hit.append(True)
            raise PermissionError("injected output error to enter rollback")
        return result

    def rmtree_wrapper(path, *args, **kwargs):
        if "/reports/incremental/provenance/" in str(path).replace("\\", "/") and not rmtree_hit:
            rmtree_hit.append(True)
            raise PermissionError("injected provenance cleanup failure")
        return real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(api.os, "replace", replace_wrapper)
    monkeypatch.setattr(api.shutil, "rmtree", rmtree_wrapper)
    result = commit(case)
    assert result["status"] == "recovery_required", result
    assert replace_hit == [True] and rmtree_hit == [True]
    journal = case["root"] / "reports/incremental/journal" / f"{result['transaction_id']}.json"
    assert journal.exists()
    assert_old(case, before)  # rollback succeeded before cleanup failure

    recovery_result = api.recover_transaction(journal.stem)
    assert recovery_result["status"] == "ok", recovery_result
    assert not journal.exists()
    assert_no_transaction_residue(case)
    assert_no_owned_provenance(case)


def test_c3_r02_provenance_move_completed_then_error_cleans_publication(tmp_path, monkeypatch):
    case = support.make_multi_case(tmp_path, monkeypatch, dirty=("a", "b", "c"))
    before = outputs(case)
    real_move = api.shutil.move
    hit = []

    def move_wrapper(source, destination):
        result = real_move(source, destination)
        if "/reports/incremental/provenance/" in str(destination).replace("\\", "/") and not hit:
            hit.append(True)
            raise PermissionError("injected error after real provenance move")
        return result

    monkeypatch.setattr(api.shutil, "move", move_wrapper)
    result = commit(case)
    assert hit == [True]
    assert result["status"] in ("rejected", "recovery_required"), result
    journal_name = f"{result.get('transaction_id', '')}.json"
    journal = case["root"] / "reports/incremental/journal" / journal_name
    if result["status"] == "recovery_required":
        recovery_result = api.recover_transaction(journal.stem)
        assert recovery_result["status"] == "ok", recovery_result
    assert_old(case, before)
    assert_no_transaction_residue(case)
    assert_no_owned_provenance(case)


def test_nb02_new_state_with_old_output_is_rejected(tmp_path, monkeypatch):
    case = rs.make_recovery_case(tmp_path, monkeypatch)
    rs.materialize_layout(case, state_variant="new", outputs="new", provenance="full", backups="full")
    key = case["keys"][0]
    (case["root"] / case["registry"][key]["output"]).write_bytes(case["old_outputs"][key])
    before = support.snapshot_files(case["root"])
    result = api.recover_transaction(case["txid"])
    assert result["status"] == "recovery_required", result
    assert support.snapshot_files(case["root"]) == before
    assert (case["root"] / "reports/incremental/journal" / case["journal_name"]).exists()


def test_nb02_corrupt_provenance_member_is_rejected(tmp_path, monkeypatch):
    case = rs.make_recovery_case(tmp_path, monkeypatch)
    rs.materialize_layout(case, state_variant="new", outputs="new", provenance="full", backups="full")
    key = case["keys"][0]
    member = next(iter(case["provenance"][key]["members"]))
    path = case["root"] / case["provenance"][key]["dir"] / member
    path.write_bytes(b"corrupted provenance member")
    before = support.snapshot_files(case["root"])
    result = api.recover_transaction(case["txid"])
    assert result["status"] == "recovery_required", result
    assert support.snapshot_files(case["root"]) == before
    assert (case["root"] / "reports/incremental/journal" / case["journal_name"]).exists()


def test_nb03_v1_journal_dispatches_to_b202_recovery(tmp_path, monkeypatch):
    from app import rebuild as single

    case = support.make_multi_case(tmp_path, monkeypatch, dirty=("a",))
    original = single._atomic_write_json

    class StopAfterPrepared(BaseException):
        pass

    def stop_after_prepared(path, payload):
        original(path, payload)
        if payload.get("status") == "prepared":
            raise StopAfterPrepared()

    monkeypatch.setattr(single, "_atomic_write_json", stop_after_prepared)
    with pytest.raises(StopAfterPrepared):
        single.commit_rebuild(case["plan"], case["state"], case["config"], case["manifest"])
    journal = next((case["root"] / "reports/incremental/journal").glob("*.json"))
    assert json.loads(journal.read_bytes())["format_version"] == 1

    result = api.recover_transaction(journal.stem)
    assert result["status"] == "ok", result
    assert not journal.exists()


def test_nb04_custom_state_path_is_rejected_explicitly(tmp_path, monkeypatch):
    case = support.make_multi_case(tmp_path, monkeypatch)
    custom = case["root"] / "reports/incremental/custom-state.json"
    custom.write_bytes(case["state"].read_bytes())
    before = support.snapshot_files(case["root"])
    report = api.precheck_rebuild(case["plan"], custom, case["config"], case["manifest"])
    assert report["action"] == "reject", report
    support.assert_error_member(report["errors"], "state_path_not_supported")
    result = api.commit_rebuild(case["plan"], custom, case["config"], case["manifest"])
    assert result["status"] == "rejected", result
    support.assert_error_member(result["errors"], "state_path_not_supported")
    assert support.snapshot_files(case["root"]) == before


def _reseal_journal_provenance(case, key, manifest):
    journal_path = case["root"] / "reports/incremental/journal" / case["journal_name"]
    journal = json.loads(journal_path.read_bytes())
    for page in journal["pages"]:
        if page["page_key"] == key:
            page["provenance"]["manifest_sha256"] = support.sha256_bytes(support.C(manifest))
            page["provenance"]["members"] = manifest["files"]
    support.write_json(journal_path, journal)


def test_nb02_r1_resealed_provenance_cannot_override_state_binding(tmp_path, monkeypatch):
    case = rs.make_recovery_case(tmp_path, monkeypatch)
    rs.materialize_layout(case, state_variant="new", outputs="new", provenance="full", backups="full")
    key = case["keys"][0]
    directory = case["root"] / case["provenance"][key]["dir"]
    manifest = json.loads((directory / "manifest.json").read_bytes())
    member = directory / manifest["files"][0]["path"]
    member.write_bytes(b"rewritten provenance member")
    manifest["files"][0]["sha256"] = support.sha256_bytes(member.read_bytes())
    support.write_json(directory / "manifest.json", manifest)
    _reseal_journal_provenance(case, key, manifest)

    before = support.snapshot_files(case["root"])
    result = api.recover_transaction(case["txid"])
    assert result["status"] == "recovery_required", result
    assert support.snapshot_files(case["root"]) == before
    assert (case["root"] / "reports/incremental/journal" / case["journal_name"]).exists()


def test_nb02_r2_duplicate_manifest_member_is_rejected(tmp_path, monkeypatch):
    case = rs.make_recovery_case(tmp_path, monkeypatch)
    rs.materialize_layout(case, state_variant="new", outputs="new", provenance="full", backups="full")
    key = case["keys"][0]
    directory = case["root"] / case["provenance"][key]["dir"]
    manifest = json.loads((directory / "manifest.json").read_bytes())
    manifest["files"].append(dict(manifest["files"][0]))
    support.write_json(directory / "manifest.json", manifest)
    _reseal_journal_provenance(case, key, manifest)

    before = support.snapshot_files(case["root"])
    result = api.recover_transaction(case["txid"])
    assert result["status"] == "recovery_required", result
    assert support.snapshot_files(case["root"]) == before
    assert (case["root"] / "reports/incremental/journal" / case["journal_name"]).exists()


def test_nb02_compat_legacy_state_without_members_is_recoverable(tmp_path, monkeypatch):
    case = rs.make_recovery_case(tmp_path, monkeypatch)
    state = json.loads(case["new_state_bytes"])
    for entry in state["build"]["pages"].values():
        entry["provenance"].pop("members", None)
    case["new_state_bytes"] = rs._dump_state(state)
    case["journal"]["state"]["new_sha256"] = rs.state_digest(state)
    rs.materialize_layout(case, state_variant="new", outputs="new", provenance="full", backups="full")

    result = api.recover_transaction(case["txid"])
    assert result["status"] == "ok", result
    assert not (case["root"] / "reports/incremental/journal" / case["journal_name"]).exists()
    for key in case["scope"]:
        assert (case["root"] / case["registry"][key]["output"]).read_bytes() == case["new_outputs"][key]
        assert (case["root"] / case["provenance"][key]["dir"]).is_dir()


def test_q1_r01_post_state_committed_journal_and_state_read_failure_keeps_new(tmp_path, monkeypatch):
    case = support.make_multi_case(tmp_path, monkeypatch, dirty=("a", "b", "c"))
    state_path = case["state"].resolve()
    real_publish_state = api._publish_state
    real_atomic_write = api._atomic_write_json
    real_read_text = pathlib.Path.read_text
    fault = {"active": False}

    def publish_state_wrapper(ctx, new_state):
        result = real_publish_state(ctx, new_state)
        fault["active"] = True
        return result

    def read_text_wrapper(self, *args, **kwargs):
        if fault["active"] and pathlib.Path(self).resolve() == state_path:
            raise PermissionError("injected state read failure after commit")
        return real_read_text(self, *args, **kwargs)

    def atomic_write_wrapper(path, payload):
        if payload.get("status") == "committed" and fault["active"]:
            raise PermissionError("injected committed journal failure")
        return real_atomic_write(path, payload)

    monkeypatch.setattr(api, "_publish_state", publish_state_wrapper)
    monkeypatch.setattr(api, "_atomic_write_json", atomic_write_wrapper)
    monkeypatch.setattr(pathlib.Path, "read_text", read_text_wrapper)

    result = commit(case)
    assert result["status"] == "committed_cleanup_pending", result
    journal = case["root"] / "reports/incremental/journal" / f"{result['transaction_id']}.json"
    assert journal.exists()
    for key, expected in case["expected_outputs"].items():
        actual = (case["root"] / case["registry"][key]["output"]).read_bytes()
        assert actual == expected

    fault["active"] = False
    recovery_result = api.recover_transaction(result["transaction_id"])
    assert recovery_result["status"] == "ok", recovery_result
    assert not journal.exists()
    for key, expected in case["expected_outputs"].items():
        actual = (case["root"] / case["registry"][key]["output"]).read_bytes()
        assert actual == expected

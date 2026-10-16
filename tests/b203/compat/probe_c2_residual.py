"""Check the explicit R01/R03 closure boundaries, without a recovery substitute."""

import importlib
import json
import shutil
import sys
from pathlib import Path

import pytest

PROJECT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "tests"))
batch = importlib.import_module("b203.test_recovery_entry_contract")
rs = importlib.import_module("b203.recovery_support")


def target(case):
    root = case["root"]
    for key, payload in case["_expected_outputs"].items():
        output = root / case["registry"][key]["output"]
        if payload is None:
            output.unlink(missing_ok=True)
        else:
            output.write_bytes(payload)
    (root / "reports/incremental/journal" / case["journal_name"]).unlink()
    owned = root / case["journal"]["staging_root"]
    if owned.exists():
        shutil.rmtree(owned)
    for key in case["scope"]:
        (root / case["backups"][key]["path"]).unlink(missing_ok=True)
        if case["_provenance_removed"]:
            shutil.rmtree(root / case["provenance"][key]["dir"])


def prepared(scenario, tmp_path, monkeypatch):
    case = batch._prepare(scenario, tmp_path, monkeypatch)
    sibling = case["root"] / "reports/incremental/staging" / case["txid"] / "audit-sibling/sentinel.txt"
    sibling.parent.mkdir(parents=True, exist_ok=True)
    sibling.write_bytes(b"preserve sibling")
    protected = case["root"] / "responses/protected-empty-dir"
    protected.mkdir()
    case["_before"] = rs.snapshot_recovery_resources(case)
    return case


@pytest.mark.parametrize("scenario", ["RC03", "RC04"])
def test_legal_cleanup_targets_pass(scenario, tmp_path, monkeypatch):
    case = prepared(scenario, tmp_path, monkeypatch)
    target(case)
    batch._assert_ok(case, {"status": "ok", "resolved": 1})


@pytest.mark.parametrize("attack", [
    "owned_staging_left", "backup_left", "foreign_backup_changed",
    "foreign_staging_changed", "owner_sentinel_deleted", "sibling_deleted",
    "foreign_provenance_deleted", "protected_empty_dir_removed",
    "unexpected_directory", "unexpected_sibling_file",
])
def test_old_success_assertion_rejects_out_of_scope_resources(attack, tmp_path, monkeypatch):
    case = prepared("RC04", tmp_path, monkeypatch)
    target(case)
    batch._assert_ok(case, {"status": "ok", "resolved": 1})
    root = case["root"]
    if attack == "owned_staging_left":
        owned = root / case["journal"]["staging_root"] / "owned.bin"
        owned.parent.mkdir(parents=True)
        owned.write_bytes(b"owned staging content")
    elif attack == "backup_left":
        key = case["keys"][0]
        (root / case["backups"][key]["path"]).write_bytes(case["old_outputs"][key])
    elif attack == "foreign_backup_changed":
        (root / "reports/incremental/backups/other-transaction/sentinel.txt").write_bytes(b"changed")
    elif attack == "foreign_staging_changed":
        (root / "reports/incremental/staging/other-transaction/token/sentinel.txt").write_bytes(b"changed")
    elif attack == "owner_sentinel_deleted":
        (root / "reports/incremental/staging" / case["txid"] / "owner-sentinel.txt").unlink()
    elif attack == "sibling_deleted":
        (root / "reports/incremental/staging" / case["txid"] / "audit-sibling/sentinel.txt").unlink()
    elif attack == "foreign_provenance_deleted":
        (root / "reports/incremental/provenance/other-transaction/sentinel.txt").unlink()
    elif attack == "protected_empty_dir_removed":
        (root / "responses/protected-empty-dir").rmdir()
    elif attack == "unexpected_directory":
        (root / "responses/unexpected-empty-dir").mkdir()
    elif attack == "unexpected_sibling_file":
        extra = root / "reports/incremental/staging" / case["txid"] / "unexpected-token/new-file.txt"
        extra.parent.mkdir(parents=True)
        extra.write_bytes(b"outside owned token")
    with pytest.raises(AssertionError):
        batch._assert_ok(case, {"status": "ok", "resolved": 1})


@pytest.mark.parametrize("attack", ["owned_staging_left", "backup_left", "foreign_backup_changed"])
def test_new_success_assertion_rejects_cleanup_gaps(attack, tmp_path, monkeypatch):
    case = prepared("RC03", tmp_path, monkeypatch)
    target(case)
    batch._assert_ok(case, {"status": "ok", "resolved": 1})
    root = case["root"]
    if attack == "owned_staging_left":
        owned = root / case["journal"]["staging_root"] / "owned.bin"
        owned.parent.mkdir(parents=True)
        owned.write_bytes(b"owned staging content")
    elif attack == "backup_left":
        key = case["keys"][0]
        (root / case["backups"][key]["path"]).write_bytes(case["old_outputs"][key])
    else:
        (root / "reports/incremental/backups/other-transaction/sentinel.txt").write_bytes(b"changed")
    with pytest.raises(AssertionError):
        batch._assert_ok(case, {"status": "ok", "resolved": 1})


@pytest.mark.parametrize("damage", ["output", "journal", "backup"])
def test_prepare_validates_materialized_scenario(damage, tmp_path, monkeypatch):
    materialize = rs.materialize_layout

    def materialize_and_damage(case, *args, **kwargs):
        result = materialize(case, *args, **kwargs)
        root = case["root"]
        key = case["keys"][0]
        if damage == "output":
            last = case["keys"][-1]
            (root / case["registry"][last]["output"]).write_bytes(b"unexpected output")
        elif damage == "backup":
            (root / case["backups"][key]["path"]).write_bytes(b"unexpected backup")
        else:
            journal = root / "reports/incremental/journal" / case["journal_name"]
            data = json.loads(journal.read_bytes())
            data["request_id"] = "0" * 64
            journal.write_text(json.dumps(data), encoding="utf-8")
        return result

    monkeypatch.setattr(rs, "materialize_layout", materialize_and_damage)
    with pytest.raises(AssertionError):
        batch._prepare("RC05" if damage == "backup" else "RC01", tmp_path, monkeypatch)


def test_rc10_uses_committed_journal(tmp_path, monkeypatch):
    case = batch._prepare("RC10", tmp_path, monkeypatch)
    journal = case["root"] / "reports/incremental/journal" / case["journal_name"]
    assert json.loads(journal.read_bytes())["status"] == "committed"


@pytest.mark.parametrize("kind", ["correct", "wrong_basename", "mixed_types", "resource_changed"])
def test_exact_diagnostics_and_rejection_preservation(kind, tmp_path, monkeypatch):
    case = batch._prepare("RC05", tmp_path, monkeypatch)
    before = rs.snapshot_recovery_resources(case)
    errors = [f"journal_backup_invalid:{case['journal_name']}"]
    if kind == "wrong_basename":
        errors = ["journal_backup_invalid:wrong.json"]
    elif kind == "mixed_types":
        errors.append(1)
    elif kind == "resource_changed":
        (case["root"] / "reports/incremental/backups/other-transaction/sentinel.txt").write_bytes(b"changed")
    result = {"status": "recovery_required", "errors": errors}
    if kind == "correct":
        batch._assert_recovery_required(case, result, before)
    else:
        with pytest.raises(AssertionError):
            batch._assert_recovery_required(case, result, before)

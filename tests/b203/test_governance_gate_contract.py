# -*- coding: utf-8 -*-
"""GOV-R01/R03：治理 gate 的拒绝边界持续回归。"""
import importlib.machinery
import importlib.util
import json
from pathlib import Path

import pytest

PROJECT = Path(__file__).resolve().parents[2]
SCRIPT = PROJECT / "scripts/check_b203_contracts.py"
loader = importlib.machinery.SourceFileLoader("b203_check_contracts", str(SCRIPT))
spec = importlib.util.spec_from_file_location("b203_check_contracts", SCRIPT, loader=loader)
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


def _manifest(snapshot_id="new", chain=None):
    return {"current_snapshot_id": snapshot_id, "snapshot_chain": chain if chain is not None else ["parent"]}


def test_missing_or_empty_snapshot_is_rejected(tmp_path, monkeypatch):
    missing = tmp_path / "missing.json"
    monkeypatch.setattr(gate, "SNAPSHOT_PATH", missing)
    assert gate.check_hashes() is False
    empty = tmp_path / "empty.json"
    empty.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(gate, "SNAPSHOT_PATH", empty)
    assert gate.check_hashes() is False


def test_snapshot_set_mismatch_and_self_parent_are_rejected():
    errors = gate.validate_snapshot(
        {"snapshot_id": "new", "parent_snapshot_id": "new", "files": [{"path": "a", "sha256": "0"}]},
        _manifest("new"),
        {"a", "b"},
    )
    assert any("parent_snapshot_id" in error for error in errors)
    assert any("missing managed files" in error for error in errors)
    errors = gate.validate_snapshot(
        {"snapshot_id": "other", "parent_snapshot_id": "parent", "files": [{"path": "a", "sha256": "0"}]},
        _manifest("new"),
        {"a"},
    )
    assert any("manifest current" in error for error in errors)


def test_freeze_rejects_duplicate_id_and_self_parent(tmp_path):
    snapshots = tmp_path / "snapshots"
    snapshots.mkdir()
    (snapshots / "new.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="already exists"):
        gate.plan_freeze_parent(_manifest("new", chain=["parent"]), "new", snapshots)
    with pytest.raises(ValueError, match="must not equal"):
        gate.plan_freeze_parent(_manifest("new2", chain=["new2"]), "new2", snapshots)


def test_gate_rejects_wrong_api_missing_message():
    errors = gate.classify_results(
        {"a": {"outcome": "failed", "message": "Failed: B203_API_MISSING: app.unrelated"}},
        ["a"],
        message=gate.RED_MESSAGE,
        require_pass=False,
    )
    assert errors


def test_gate_rejects_same_count_replaced_nodeids():
    errors = gate.classify_results(
        {"b": {"outcome": "passed", "message": ""}},
        ["a"],
    )
    assert any("missing nodeids" in error for error in errors)
    assert any("extra nodeids" in error for error in errors)


def test_gate_rejects_skipped_compat_nodeids():
    errors = gate.classify_results(
        {"a": {"outcome": "skipped", "message": "skipped"}},
        ["a"],
    )
    assert errors


def test_partition_rejects_unclassified_and_overlap():
    errors = gate.partition_errors({"a", "b", "c"}, {"a"}, {"b"}, "actual")
    assert any("unclassified" in error for error in errors)
    errors = gate.partition_errors({"a", "b"}, {"a", "b"}, {"b"}, "actual")
    assert any("overlap" in error for error in errors)
    assert not gate.partition_errors({"a", "b"}, {"a"}, {"b"}, "actual")


def _write_snapshot(path, snapshot_id, parent):
    path.write_text(
        json.dumps({"snapshot_id": snapshot_id, "parent_snapshot_id": parent, "files": [{"path": "x", "sha256": "0"}]}),
        encoding="utf-8",
    )


def test_history_chain_rejects_missing_identity_and_bad_parent(tmp_path):
    snapshots = tmp_path / "snapshots"
    snapshots.mkdir()
    manifest = {"snapshot_chain": ["root", "mid", "current"], "current_snapshot_id": "current"}
    _write_snapshot(snapshots / "root.json", "root", None)
    _write_snapshot(snapshots / "mid.json", "mid", "root")
    _write_snapshot(snapshots / "current.json", "current", "mid")
    current = json.loads((snapshots / "current.json").read_text(encoding="utf-8"))
    assert gate.validate_history_chain(manifest, snapshots, current) == []

    (snapshots / "mid.json").unlink()
    errors = gate.validate_history_chain(manifest, snapshots, current)
    assert any("missing" in error for error in errors)

    _write_snapshot(snapshots / "mid.json", "other", "root")
    errors = gate.validate_history_chain(manifest, snapshots, current)
    assert any("identity mismatch" in error for error in errors)

    _write_snapshot(snapshots / "mid.json", "mid", "root")
    bad_current = dict(current)
    bad_current["parent_snapshot_id"] = "ghost"
    errors = gate.validate_history_chain(manifest, snapshots, bad_current)
    assert any("current parent" in error for error in errors)


def test_reviewer_contract_file_and_registry_are_landed():
    contract_doc = PROJECT / "docs/b203-gpt-review-contract.md"
    registry = (PROJECT / "openspec/changes/016-multi-page-commit/contract-registry.md").read_text(encoding="utf-8")
    manifest = json.loads((PROJECT / "tests/b203/contract_manifest.json").read_text(encoding="utf-8"))
    assert contract_doc.exists()
    text = contract_doc.read_text(encoding="utf-8")
    assert "B4-02-B203-GPT-Reviewer-v1.1" in text
    assert "项目侧已落库" in text
    assert "GOV-R04" in registry
    assert "docs/b203-gpt-review-contract.md" in registry
    assert manifest["reviewer_contract"]["path"] == "docs/b203-gpt-review-contract.md"
    assert manifest["reviewer_contract"]["version"] == "B4-02-B203-GPT-Reviewer-v1.1"
    assert any(item.get("contract_id") == "GOV-R04" for item in manifest["contracts"])


def test_reviewer_contract_declares_version_and_status():
    text = (PROJECT / "docs/b203-gpt-review-contract.md").read_text(encoding="utf-8")
    for marker in (
        "Reviewer 明确采纳",
        "Delta:",
        "Blocking / Non-blocking:",
        "无新证据不得新增 blocking",
        "不得每轮无条件要求 298",
        "已关闭项重开规则",
    ):
        assert marker in text, marker

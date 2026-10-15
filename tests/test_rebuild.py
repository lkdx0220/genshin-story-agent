# -*- coding: utf-8 -*-
"""B4-02 B202：单页提交事务回归。"""

import json
import pytest
import shutil
from pathlib import Path

from app import incremental as inc
from app import rebuild

ORIG = Path(__file__).resolve().parents[1]


def _write_state(root, inputs):
    observed = inc.fingerprint_inputs(inputs)
    graph = inc.collect_dependencies(inputs)
    state = inc.make_baseline(observed, "parent-g1", graph)
    path = root / "reports/incremental/state.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    return path


def _setup_real_project(tmp_path, monkeypatch):
    root = tmp_path
    monkeypatch.setattr(inc, "REPO_ROOT", root)
    for relative in ("wiki/__schema__", "lint", "tests/fixtures/traveler/raw"):
        shutil.copytree(ORIG / relative, root / relative)
    (root / "docs").mkdir()
    shutil.copy2(ORIG / "docs/schema-lock.json", root / "docs/schema-lock.json")
    shutil.copy2(ORIG / "tests/fixtures/traveler-response.md", root / "traveler.md")
    (root / "traveler-output.md").write_text("old output", encoding="utf-8")
    config = {
        "format_version": 1,
        "inputs": [
            {
                "bundle_key": "traveler",
                "root": "tests/fixtures/traveler/raw",
                "data_mode": "fixture",
                "managed_pages": [
                    {
                        "page_key": "traveler.md|fixture|char:traveler",
                        "entity_id": "char:traveler",
                        "response": "traveler.md",
                        "output": "traveler-output.md",
                    }
                ],
                "sources": ["source:traveler-fixture"],
                "profile_ids": ["char:traveler"],
                "prompt": "wiki/__schema__/ingest-traveler.prompt.md",
            }
        ],
    }
    config_path = root / "config/incremental-inputs.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(inc, "DEFAULT_INPUTS", config_path)
    inputs = inc.load_inputs(config_path)
    state_path = _write_state(root, inputs)
    (root / "traveler-output.md").write_text("modified output", encoding="utf-8")
    plan = inc.build_plan(inputs, state_path)
    assert plan["dirty"] and not plan["full_required"] and not plan["blocked"] and not plan["unknown"]
    plan_path = root / "reports/incremental/plan.json"
    plan_path.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    manifest = root / "candidate.json"
    manifest.write_text(
        json.dumps(
            {
                "format_version": 1,
                "candidates": [
                    {
                        "page_key": "traveler.md|fixture|char:traveler",
                        "response": "traveler.md",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return root, config_path, state_path, plan_path, manifest


def _snapshot(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def test_b202_single_page_commit_and_replay(tmp_path, monkeypatch):
    root, config, state, plan, manifest = _setup_real_project(tmp_path, monkeypatch)
    result = rebuild.commit_rebuild(plan, state, config, manifest)
    assert result["status"] == "committed", result
    assert not list((root / "reports/incremental/journal").glob("*.json"))
    state_data = json.loads(state.read_text(encoding="utf-8"))
    assert state_data["build"]["status"] == "committed"
    assert state_data["build"]["transaction_id"] == result["transaction_id"]

    after = _snapshot(root)
    replay = rebuild.commit_rebuild(plan, state, config, manifest)
    assert replay["status"] == "already_committed", replay
    assert _snapshot(root) == after


def test_b202_lock_busy_blocks_commit(tmp_path, monkeypatch):
    root, config, state, plan, manifest = _setup_real_project(tmp_path, monkeypatch)
    lock = root / "reports/incremental/commit.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text("locked", encoding="utf-8")
    before = _snapshot(root)
    result = rebuild.commit_rebuild(plan, state, config, manifest)
    assert result["status"] == "lock_busy"
    assert _snapshot(root) == before
    lock.unlink()


def test_b202_unresolved_journal_blocks_commit(tmp_path, monkeypatch):
    root, config, state, plan, manifest = _setup_real_project(tmp_path, monkeypatch)
    journal = root / "reports/incremental/journal/other.json"
    journal.parent.mkdir(parents=True, exist_ok=True)
    journal.write_text('{"status": "prepared"}', encoding="utf-8")
    before = _snapshot(root)
    result = rebuild.commit_rebuild(plan, state, config, manifest)
    assert result["status"] == "recovery_required"
    assert _snapshot(root) == before


def test_b202_staging_failure_rolls_back(tmp_path, monkeypatch):
    root, config, state, plan, manifest = _setup_real_project(tmp_path, monkeypatch)
    before_state = state.read_bytes()
    before_output = (root / "traveler-output.md").read_bytes()

    def boom(*args, **kwargs):
        raise rebuild.RebuildError("staged schema failed")

    monkeypatch.setattr(rebuild, "_stage_candidate", boom)
    result = rebuild.commit_rebuild(plan, state, config, manifest)
    assert result["status"] == "failed", result
    assert state.read_bytes() == before_state
    assert (root / "traveler-output.md").read_bytes() == before_output


def test_b202_state_replace_failure_rolls_back_output(tmp_path, monkeypatch):
    root, config, state, plan, manifest = _setup_real_project(tmp_path, monkeypatch)
    before_state = state.read_bytes()
    before_output = (root / "traveler-output.md").read_bytes()
    real_replace = rebuild.os.replace

    def fail_state_replace(source, destination):
        if Path(destination) == state:
            raise OSError("state replace failed")
        return real_replace(source, destination)

    monkeypatch.setattr(rebuild.os, "replace", fail_state_replace)
    result = rebuild.commit_rebuild(plan, state, config, manifest)
    assert result["status"] == "failed", result
    assert state.read_bytes() == before_state
    assert (root / "traveler-output.md").read_bytes() == before_output


class _Crash(BaseException):
    pass


def _crash_after_output(root, config, state, plan, manifest, monkeypatch):
    original = rebuild._write_journal

    def crash(txid, status, **fields):
        original(txid, status, **fields)
        if status == "output_replaced":
            raise _Crash()

    monkeypatch.setattr(rebuild, "_write_journal", crash)
    with pytest.raises(_Crash):
        rebuild.commit_rebuild(plan, state, config, manifest)


def _journal_payload(root):
    path = next((root / "reports/incremental/journal").glob("*.json"))
    return path, json.loads(path.read_text(encoding="utf-8"))


def test_b202_staging_failure_leaves_no_staging(tmp_path, monkeypatch):
    root, config, state, plan, manifest = _setup_real_project(tmp_path, monkeypatch)

    def boom(*args, **kwargs):
        raise rebuild.RebuildError("staged schema failed")

    monkeypatch.setattr(rebuild, "_stage_candidate", boom)
    result = rebuild.commit_rebuild(plan, state, config, manifest)
    assert result["status"] == "failed"
    staging = root / "reports/incremental/staging"
    assert not staging.exists() or not any(staging.rglob("*"))


def test_b202_journal_txid_recompute_rejects_tampered_page_key(tmp_path, monkeypatch):
    case = _setup_real_project(tmp_path, monkeypatch)
    _crash_after_output(*case, monkeypatch)
    path, payload = _jurnal = _journal_payload(case[0])
    payload["page_key"] = "other.md|fixture|char:other"
    path.write_text(json.dumps(payload), encoding="utf-8")
    result = rebuild.recover_transaction()
    assert result["status"] == "recovery_required", result
    assert path.exists()


def test_b202_journal_rejects_inputs_outside_project(tmp_path, monkeypatch):
    case = _setup_real_project(tmp_path, monkeypatch)
    _crash_after_output(*case, monkeypatch)
    path, payload = _journal_payload(case[0])
    payload["inputs"] = "../outside-config.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    result = rebuild.recover_transaction()
    assert result["status"] == "recovery_required", result
    assert path.exists()


def test_b202_journal_staging_requires_txid_owner(tmp_path, monkeypatch):
    case = _setup_real_project(tmp_path, monkeypatch)
    _crash_after_output(*case, monkeypatch)
    path, payload = _journal_payload(case[0])
    unrelated = case[0] / "reports/incremental/staging/other-transaction"
    unrelated.mkdir(parents=True, exist_ok=True)
    sentinel = unrelated / "keep.txt"
    sentinel.write_bytes(b"keep")
    payload["staging"] = str(unrelated.relative_to(case[0]))
    path.write_text(json.dumps(payload), encoding="utf-8")
    result = rebuild.recover_transaction()
    assert result["status"] == "recovery_required", result
    assert sentinel.exists()
    assert path.exists()


def test_b202_internal_build_failure_is_structured(tmp_path, monkeypatch):
    root, config, state, plan, manifest = _setup_real_project(tmp_path, monkeypatch)
    before_state = state.read_bytes()
    before_output = (root / "traveler-output.md").read_bytes()

    def boom(*args, **kwargs):
        raise OSError("internal graph failure")

    monkeypatch.setattr(rebuild, "_build_state", boom)
    result = rebuild.commit_rebuild(plan, state, config, manifest)
    assert result["status"] == "failed", result
    assert "internal:internal graph failure" in result["errors"]
    assert state.read_bytes() == before_state
    assert (root / "traveler-output.md").read_bytes() == before_output


def test_b202_prepare_failure_is_structured(tmp_path, monkeypatch):
    _root, config, state, plan, manifest = _setup_real_project(tmp_path, monkeypatch)

    def boom(*args, **kwargs):
        raise RuntimeError("prepare exploded")

    monkeypatch.setattr(rebuild, "_prepare_commit", boom)
    result = rebuild.commit_rebuild(plan, state, config, manifest)
    assert result["status"] == "failed", result
    assert "prepare:prepare exploded" in result["errors"]


def _crash_after_status(root, config, state, plan, manifest, monkeypatch, status):
    original = rebuild._write_journal

    def crash(txid, journal_status, **fields):
        original(txid, journal_status, **fields)
        if journal_status == status:
            raise _Crash()

    monkeypatch.setattr(rebuild, "_write_journal", crash)
    with pytest.raises(_Crash):
        rebuild.commit_rebuild(plan, state, config, manifest)


def test_b202_recovery_rejects_state_redirect_after_commit(tmp_path, monkeypatch):
    root, config, state, plan, manifest = _setup_real_project(tmp_path, monkeypatch)
    old_state = state.read_bytes()
    old_copy = root / "old-state-copy.json"
    old_copy.write_bytes(old_state)
    _crash_after_status(root, config, state, plan, manifest, monkeypatch, "committed")
    committed_state = state.read_bytes()
    committed_output = (root / "traveler-output.md").read_bytes()
    path, payload = _journal_payload(root)
    payload["state"] = "old-state-copy.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    result = rebuild.recover_transaction()
    assert result["status"] == "recovery_required", result
    assert state.read_bytes() == committed_state
    assert (root / "traveler-output.md").read_bytes() == committed_output
    assert path.exists()


def test_b202_recovery_rejects_staging_owner_root(tmp_path, monkeypatch):
    root, config, state, plan, manifest = _setup_real_project(tmp_path, monkeypatch)
    _crash_after_output(root, config, state, plan, manifest, monkeypatch)
    path, payload = _journal_payload(root)
    owner = rebuild._staging_dir(payload["transaction_id"])
    sibling = owner / "other-active-invocation"
    sibling.mkdir(parents=True, exist_ok=True)
    sentinel = sibling / "keep.txt"
    sentinel.write_bytes(b"keep")
    payload["staging"] = str(owner.relative_to(root))
    path.write_text(json.dumps(payload), encoding="utf-8")
    result = rebuild.recover_transaction()
    assert result["status"] == "recovery_required", result
    assert sentinel.exists()
    assert path.exists()


def test_b202_recovery_rejects_staging_sibling(tmp_path, monkeypatch):
    root, config, state, plan, manifest = _setup_real_project(tmp_path, monkeypatch)
    _crash_after_output(root, config, state, plan, manifest, monkeypatch)
    path, payload = _journal_payload(root)
    owner = rebuild._staging_dir(payload["transaction_id"])
    sibling = owner / "other-active-invocation"
    sibling.mkdir(parents=True, exist_ok=True)
    sentinel = sibling / "keep.txt"
    sentinel.write_bytes(b"keep")
    payload["staging"] = str(sibling.relative_to(root))
    path.write_text(json.dumps(payload), encoding="utf-8")
    result = rebuild.recover_transaction()
    assert result["status"] == "recovery_required", result
    assert sentinel.exists()
    assert path.exists()

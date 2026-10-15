# -*- coding: utf-8 -*-
"""B4-02 B202：单页提交事务回归。"""

import json
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

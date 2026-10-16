# -*- coding: utf-8 -*-
"""T1-D 真实进程：os._exit 三退出点、双进程竞争、遗留锁。"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from app import rebuild_multi as api
from . import support

PROJECT = Path(__file__).resolve().parents[2]
CHILD = PROJECT / "tests/b203/child_commit.py"


def run_child(case, mode, timeout=90):
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONPATH=str(PROJECT))
    return subprocess.run(
        [sys.executable, str(CHILD), str(case["root"]), mode],
        cwd=str(PROJECT),
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def parse_child(result):
    return json.loads(result.stdout.strip().splitlines()[-1])


def clear_stale_lock(case):
    (case["root"] / "reports/incremental/commit.lock").unlink(missing_ok=True)


@pytest.mark.parametrize("mode", ["middle", "all_pages", "state"])
def test_t1d_process_exit_recovery(tmp_path, monkeypatch, mode):
    case = support.make_multi_case(tmp_path, monkeypatch, dirty=("a", "b", "c"))
    old_outputs = {key: (case["root"] / case["registry"][key]["output"]).read_bytes() for key in case["registry"]}
    old_state = case["state"].read_bytes()

    result = run_child(case, mode)
    assert result.returncode == 77, (mode, result.stdout, result.stderr)
    lock = case["root"] / "reports/incremental/commit.lock"
    assert lock.exists(), "os._exit must leave the project lock for explicit stale handling"

    clear_stale_lock(case)
    recovery = api.recover_transaction()
    assert recovery["status"] == "ok", recovery

    if mode == "state":
        state = json.loads(case["state"].read_bytes())
        assert state["generation"] != "fixture-parent"
        for key in case["registry"]:
            assert (case["root"] / case["registry"][key]["output"]).read_bytes() == case["expected_outputs"][key]
        assert api.commit_rebuild(case["plan"], case["state"], case["config"], case["manifest"])["status"] == "already_committed"
    else:
        assert case["state"].read_bytes() == old_state
        for key in case["registry"]:
            assert (case["root"] / case["registry"][key]["output"]).read_bytes() == old_outputs[key]
        assert not list((case["root"] / "reports/incremental/journal").glob("*"))
    assert not (case["root"] / "reports/incremental/commit.lock").exists()


def test_t1d_dual_process_same_request_competition(tmp_path, monkeypatch):
    case = support.make_multi_case(tmp_path, monkeypatch, dirty=("a", "b", "c"))
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONPATH=str(PROJECT))
    holder = subprocess.Popen(
        [sys.executable, str(CHILD), str(case["root"]), "hold_lock"],
        cwd=str(PROJECT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    ready = case["root"] / "held.flag"
    deadline = time.time() + 30
    while not ready.exists():
        if holder.poll() is not None:
            raise AssertionError(f"holder exited early: {holder.communicate()}")
        if time.time() > deadline:
            holder.kill()
            raise AssertionError("holder did not reach lock window")
        time.sleep(0.05)

    contender = run_child(case, "none")
    contender_result = parse_child(contender)
    assert contender_result["status"] == "lock_busy", contender_result
    state_before = case["state"].read_bytes()

    (case["root"] / "release.flag").write_text("go", encoding="utf-8")
    holder_out, holder_err = holder.communicate(timeout=60)
    assert holder.returncode == 0, (holder_out, holder_err)
    assert json.loads(holder_out.strip().splitlines()[-1])["status"] == "committed"
    assert case["state"].read_bytes() != state_before

    retry = run_child(case, "none")
    retry_result = parse_child(retry)
    assert retry_result["status"] == "already_committed", retry_result
    for key in case["registry"]:
        assert (case["root"] / case["registry"][key]["output"]).read_bytes() == case["expected_outputs"][key]


def test_t1d_stale_lock_is_not_stolen_without_manual_clear(tmp_path, monkeypatch):
    case = support.make_multi_case(tmp_path, monkeypatch, dirty=("a", "b", "c"))
    lock = case["root"] / "reports/incremental/commit.lock"
    lock.write_bytes(b'{"transaction_id":"stale-owner","pid":99999}')
    before = support.snapshot_files(case["root"])

    result = run_child(case, "none")
    payload = parse_child(result)
    assert payload["status"] == "lock_busy", payload
    assert lock.exists()
    assert support.snapshot_files(case["root"]) == before

    lock.unlink()
    result = run_child(case, "none")
    assert parse_child(result)["status"] == "committed"
    assert not lock.exists()

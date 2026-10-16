# -*- coding: utf-8 -*-
"""I5：B203 显式入口、capability、单页委托、B201 只读边界。"""
import json

from app import incremental as inc
from app import rebuild_multi as api
from . import support


def _paths(case):
    return [
        "--plan", str(case["plan"]),
        "--state", str(case["state"]),
        "--inputs", str(case["config"]),
        "--candidate-manifest", str(case["manifest"]),
    ]


def test_i5_capability_enabled_after_q1(tmp_path, monkeypatch):
    case = support.make_multi_case(tmp_path, monkeypatch)
    report = api.precheck_rebuild(case["plan"], case["state"], case["config"], case["manifest"])
    assert report["action"] == "multi_page", report
    assert report["capability"]["multi_page_commit"] is True
    assert report["capability"]["single_page_commit"] is False


def test_i5_cli_precheck_and_commit(tmp_path, monkeypatch, capsys):
    case = support.make_multi_case(tmp_path, monkeypatch)
    assert api.main(["precheck"] + _paths(case)) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["action"] == "multi_page", report
    assert report["capability"]["multi_page_commit"] is True

    assert api.main(["commit"] + _paths(case)) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "committed", result
    assert not list((case["root"] / "reports/incremental/journal").glob("*"))


def test_i5_single_page_still_delegates_to_b202(tmp_path, monkeypatch):
    case = support.make_multi_case(tmp_path, monkeypatch, dirty=("a",))
    report = api.precheck_rebuild(case["plan"], case["state"], case["config"], case["manifest"])
    assert report["action"] == "single_page", report
    result = api.commit_rebuild(case["plan"], case["state"], case["config"], case["manifest"])
    assert result["status"] == "committed", result
    state = json.loads(case["state"].read_bytes())
    assert state["build"]["status"] == "committed"
    assert not list((case["root"] / "reports/incremental/journal").glob("*"))


def test_i5_b201_remains_read_only_and_rejects_multi(tmp_path, monkeypatch):
    case = support.make_multi_case(tmp_path, monkeypatch, dirty=("a", "b"))
    before = support.snapshot_files(case["root"])
    report = inc.precheck_rebuild(case["plan"], case["state"], case["config"], case["manifest"])
    assert report["action"] == "reject", report
    assert report["executable"] is False
    result = inc.commit_rebuild(case["plan"], case["state"], case["config"], case["manifest"])
    assert result["status"] != "committed", result
    assert support.snapshot_files(case["root"]) == before

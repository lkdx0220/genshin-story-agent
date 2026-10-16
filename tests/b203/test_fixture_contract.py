# -*- coding: utf-8 -*-
"""B203 T1-A GREEN：三页真实夹具与 oracle 自检。"""
import json
from pathlib import Path

import pytest

from . import support


def _assert_tree_bytes_equal(copied_root, source_root):
    source = support.PROJECT_ROOT / source_root
    copied = copied_root / source_root
    source_files = {str(path.relative_to(source)): path.read_bytes() for path in source.rglob("*") if path.is_file()}
    copied_files = {str(path.relative_to(copied)): path.read_bytes() for path in copied.rglob("*") if path.is_file()}
    assert copied_files == source_files


def test_g01_real_fixture_ingest_and_lint(tmp_path, monkeypatch):
    case = support.make_multi_case(tmp_path, monkeypatch)
    assert set(case["expected_outputs"]) == set(case["page_keys"].values())
    assert len(case["reports"]) == 3
    for page_key, report in case["reports"].items():
        assert report["publication"] == "draft_only", (page_key, report)
        assert report["page_status"] == "draft", (page_key, report)
        assert report["entity_id"] == "char:traveler"
        assert report["data_mode"] == "fixture"
        assert report["output_sha256"] == support.sha256_bytes(case["expected_outputs"][page_key])
        report_path = case["root"] / report["report_path"]
        assert report_path.is_file()
        assert json.loads(report_path.read_text(encoding="utf-8"))["publication"] == "draft_only"
    _assert_tree_bytes_equal(case["root"], "tests/fixtures/traveler/raw")
    _assert_tree_bytes_equal(case["root"], "tests/fixtures/traveler-conflict/raw")
    _assert_tree_bytes_equal(case["root"], "wiki/__schema__")
    assert (case["root"] / "docs/schema-lock.json").read_bytes() == (
        support.PROJECT_ROOT / "docs/schema-lock.json"
    ).read_bytes()
    provenance = case["root"] / "reports/fixtures/_provenance"
    assert provenance.is_dir() and any(provenance.rglob("*"))


def test_g02_clean_then_exact_two_page_dirty(tmp_path, monkeypatch):
    case = support.make_multi_case(tmp_path, monkeypatch)
    loaded, errors = support.inc.load_baseline(case["state"])
    assert loaded is not None, errors
    assert loaded["generation"] == "fixture-parent"
    plan = case["plan_data"]
    expected_dirty = {case["page_keys"]["a"], case["page_keys"]["b"]}
    assert set(plan["dirty"]) == expected_dirty
    for page_key in expected_dirty:
        assert plan["dirty"][page_key] == ["generated_output_modified"], plan["dirty"][page_key]
    assert plan["full_required"] is False
    assert plan["blocked"] == []
    assert plan["unknown"] == []
    assert plan["freshness"][case["page_keys"]["a"]] == "stale"
    assert plan["freshness"][case["page_keys"]["b"]] == "stale"
    assert plan["freshness"][case["page_keys"]["c"]] == "fresh"
    assert (case["root"] / "reports/fixtures/c.md").read_bytes() == case["expected_outputs"][
        case["page_keys"]["c"]
    ]
    assert case["original_graph"]["diagnostics"] == []


def test_g03_existing_precheck_rejects_multi_and_is_read_only(tmp_path, monkeypatch):
    case = support.make_multi_case(tmp_path, monkeypatch)
    before_files = support.snapshot_files(case["root"])
    before_dirs = support.snapshot_dirs(case["root"])
    report = support.inc.precheck_rebuild(case["plan"], case["state"], case["config"], case["manifest"])
    assert report["action"] == "reject", report
    assert report["executable"] is False
    assert "multi_page_not_supported" in report["errors"], report
    assert support.snapshot_files(case["root"]) == before_files
    assert support.snapshot_dirs(case["root"]) == before_dirs


def test_g04_request_identity_oracle_scope_and_normalization(tmp_path, monkeypatch):
    case = support.make_multi_case(tmp_path, monkeypatch)
    candidates = json.loads(case["manifest"].read_text(encoding="utf-8"))["candidates"]
    payload = support.request_payload(case, case["plan_data"], candidates)
    expected_pages = []
    for page_key in sorted([case["page_keys"]["a"], case["page_keys"]["b"]]):
        managed = next(
            page
            for item in case["inputs"]["inputs"]
            for page in item["managed_pages"]
            if page["page_key"] == page_key
        )
        candidate = next(item for item in candidates if item["page_key"] == page_key)
        expected_pages.append(
            {
                "page_key": page_key,
                "response": managed["response"],
                "output": managed["output"],
                "candidate_sha256": support.sha256_file(case["root"] / candidate["response"]),
            }
        )
    expected = {
        "project_root_id": support.project_root_id(case["root"]),
        "protocol": "b4-02-multi-commit-v1",
        "inputs": {
            "path": "config/incremental-inputs.json",
            "sha256": support.sha256_file(case["config"]),
        },
        "state": "reports/incremental/state.json",
        "plan_generation": case["plan_data"]["generation"],
        "parent_generation": "fixture-parent",
        "page_keys": sorted([case["page_keys"]["a"], case["page_keys"]["b"]]),
        "pages": expected_pages,
        "execution": {
            "observed": case["plan_data"]["observed"],
            "dirty": case["plan_data"]["dirty"],
            "full_required": False,
            "blocked": [],
            "unknown": [],
            "affected_pages": [],
        },
    }
    assert payload == expected
    assert case["page_keys"]["c"] not in payload["page_keys"]
    request_id = support.sha256_bytes(support.C(payload))
    wrong = json.loads(json.dumps(expected))
    wrong["page_keys"] = sorted(case["page_keys"].values())
    assert support.sha256_bytes(support.C(wrong)) != request_id
    with pytest.raises(AssertionError):
        broken = json.loads(json.dumps(case["plan_data"]))
        broken["blocked"] = "not-a-list"
        support.request_payload(case, broken, candidates)
    for bad in (None, "", 0, False, {}):
        broken = json.loads(json.dumps(case["plan_data"]))
        broken["affected_pages"] = bad
        with pytest.raises(AssertionError):
            support.request_payload(case, broken, candidates)
    missing = json.loads(json.dumps(case["plan_data"]))
    missing.pop("affected_pages", None)
    assert support.request_payload(case, missing, candidates)["execution"]["affected_pages"] == []
    empty = json.loads(json.dumps(case["plan_data"]))
    empty["affected_pages"] = []
    assert support.request_payload(case, empty, candidates)["execution"]["affected_pages"] == []
    ordered = json.loads(json.dumps(case["plan_data"]))
    ordered["affected_pages"] = ["c", "a", "a"]
    assert support.request_payload(case, ordered, candidates)["execution"]["affected_pages"] == ["a", "c"]


def test_g05_provenance_oracle_rejects_empty_evidence(tmp_path):
    provenance = tmp_path / "provenance"
    provenance.mkdir()
    manifest = {"protocol": "b4-02-provenance-v1", "files": []}
    manifest_path = provenance / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    page_entry = {
        "provenance": {
            "dir": str(provenance.relative_to(tmp_path)),
            "manifest_sha256": support.sha256_bytes(support.C(manifest)),
        }
    }
    with pytest.raises(AssertionError):
        support.verify_provenance(tmp_path, page_entry, [])


def test_g06_snapshot_posix_and_allowed_change_guard(tmp_path, monkeypatch):
    case = support.make_multi_case(tmp_path, monkeypatch)
    before = support.snapshot_files(case["root"])
    assert "reports/fixtures/a.md" in before
    assert "reports\fixtures\a.md" not in before
    prefix = "reports/incremental/provenance/tx-test"
    after = dict(before)
    after["reports/fixtures/a.md"] = b"new-a"
    after["reports/incremental/state.json"] = b"new-state"
    after[f"{prefix}/manifest.json"] = b"{}"
    support.assert_allowed_changes(
        before,
        after,
        {"reports/fixtures/a.md", "reports/incremental/state.json"},
        {prefix},
    )
    forbidden_content = dict(after)
    forbidden_content["reports/fixtures/c.md"] = b"changed-c"
    with pytest.raises(AssertionError):
        support.assert_allowed_changes(
            before,
            forbidden_content,
            {"reports/fixtures/a.md", "reports/incremental/state.json"},
            {prefix},
        )
    forbidden_new = dict(after)
    forbidden_new["reports/incremental/staging/other.txt"] = b"x"
    with pytest.raises(AssertionError):
        support.assert_allowed_changes(
            before,
            forbidden_new,
            {"reports/fixtures/a.md", "reports/incremental/state.json"},
            {prefix},
        )


def test_g07_error_member_is_exact():
    support.assert_error_member(["candidate_set_mismatch"], "candidate_set_mismatch")
    with pytest.raises(AssertionError):
        support.assert_error_member(["not_candidate_set_mismatch"], "candidate_set_mismatch")
    with pytest.raises(AssertionError):
        support.assert_error_member(["candidate_set_mismatch:extra"], "candidate_set_mismatch")


def test_g08_affected_pages_missing_vs_wrong_type(tmp_path, monkeypatch):
    case = support.make_multi_case(tmp_path, monkeypatch)
    candidates = json.loads(case["manifest"].read_text(encoding="utf-8"))["candidates"]
    plan = json.loads(json.dumps(case["plan_data"]))
    plan.pop("affected_pages", None)
    assert support.request_payload(case, plan, candidates)["execution"]["affected_pages"] == []
    plan["affected_pages"] = []
    assert support.request_payload(case, plan, candidates)["execution"]["affected_pages"] == []
    plan["affected_pages"] = ["c", "a", "a"]
    assert support.request_payload(case, plan, candidates)["execution"]["affected_pages"] == ["a", "c"]
    for bad in (None, "", 0, False, {}):
        plan = json.loads(json.dumps(case["plan_data"]))
        plan["affected_pages"] = bad
        with pytest.raises(AssertionError):
            support.request_payload(case, plan, candidates)


def test_g09_provenance_requires_expected_raw_snapshots(tmp_path, monkeypatch):
    import shutil

    case = support.make_multi_case(tmp_path, monkeypatch)
    page_key = case["page_keys"]["a"]
    source_dir = case["root"] / case["reports"][page_key]["provenance_dir"]
    evidence = tmp_path / "evidence"
    shutil.copytree(source_dir, evidence)
    files = []
    for path in sorted(evidence.rglob("*")):
        if path.is_file():
            files.append(
                {
                    "path": path.relative_to(evidence).as_posix(),
                    "sha256": support.sha256_bytes(path.read_bytes()),
                }
            )
    manifest = {"protocol": "b4-02-provenance-v1", "files": files}
    (evidence / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    entry = {
        "provenance": {
            "dir": "evidence",
            "manifest_sha256": support.sha256_bytes(support.C(manifest)),
        }
    }
    support.verify_provenance(tmp_path, entry, case["snapshots"][page_key])

    snapshot = case["snapshots"][page_key][0]["path"]
    target = evidence / snapshot
    original = target.read_bytes()
    target.unlink()
    with pytest.raises(AssertionError):
        support.verify_provenance(tmp_path, entry, case["snapshots"][page_key])

    target.write_bytes(original + b"x")
    with pytest.raises(AssertionError):
        support.verify_provenance(tmp_path, entry, case["snapshots"][page_key])

# -*- coding: utf-8 -*-
"""B203 T1-B：三页顺序、候选失败、真实计划场景、provenance 契约。"""
import copy
import importlib
import json
import os
import shutil
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


def _config(case):
    return json.loads(case["config"].read_text(encoding="utf-8"))


def _write_config(case, config):
    support.write_json(case["config"], config)


def _page_key(case, key):
    return case["page_keys"][key]


def _add_page_d(case):
    config = _config(case)
    first = config["inputs"][0]
    shutil.copy2(case["root"] / "responses/a.md", case["root"] / "responses/d.md")
    first["managed_pages"].append(
        {
            "page_key": "responses/d.md|fixture|char:traveler",
            "entity_id": "char:traveler",
            "response": "responses/d.md",
            "output": "reports/fixtures/d.md",
        }
    )
    _write_config(case, config)
    inputs = support.inc.load_inputs(case["config"])
    plan = support.inc.build_plan(inputs, case["state"])
    return inputs, plan


def _blocked_plan(case):
    (case["root"] / "docs/schema-lock.json").write_text("{}\n", encoding="utf-8")
    inputs = support.inc.load_inputs(case["config"])
    plan = support.inc.build_plan(inputs, case["state"])
    return inputs, plan


def _removal_plan(case):
    config = _config(case)
    first = config["inputs"][0]
    first["managed_pages"] = [
        page for page in first["managed_pages"] if page["page_key"] != _page_key(case, "c")
    ]
    _write_config(case, config)
    inputs = support.inc.load_inputs(case["config"])
    plan = support.inc.build_plan(inputs, case["state"])
    return inputs, plan


def _lint_failure_case(case):
    response = case["root"] / "responses/a.md"
    text = response.read_text(encoding="utf-8")
    broken = text.replace("## 时间线\n", "", 1)
    assert broken != text
    response.write_text(broken, encoding="utf-8")
    inputs = support.inc.load_inputs(case["config"])
    plan = support.inc.build_plan(inputs, case["state"])
    support.write_json(case["plan"], plan)
    return inputs, plan


def _conservative_full_case(case, single=False):
    inputs = case["inputs"]
    if single:
        config = _config(case)
        config["inputs"] = [config["inputs"][0]]
        config["inputs"][0]["managed_pages"] = [config["inputs"][0]["managed_pages"][0]]
        _write_config(case, config)
        inputs = support.inc.load_inputs(case["config"])
    observed = support.inc.fingerprint_inputs(inputs)
    graph = copy.deepcopy(support.inc.collect_dependencies(inputs))
    dependency = ("page", _page_key(case, "a"))
    for index in range(35):
        consumer = ("index", f"t1b-depth-{index}")
        graph["nodes"].append({"key": consumer, "type": "index"})
        graph["edges"].append({"consumer": consumer, "dependency": dependency})
        dependency = consumer
    baseline = support.inc.make_baseline(observed, "fixture-parent", graph)
    support.write_json(case["state"], baseline)
    loaded, errors = support.inc.load_baseline(case["state"])
    assert loaded is not None, errors
    assert not support.inc.build_plan(inputs, case["state"])["full_required"]
    (case["root"] / "reports/fixtures/a.md").write_bytes(b"stale-full")
    plan = support.inc.build_plan(inputs, case["state"])
    return inputs, plan

def _evidence_package(case, tmp_path):
    page_key = _page_key(case, "a")
    source = case["root"] / case["reports"][page_key]["provenance_dir"]
    evidence = tmp_path / "evidence"
    shutil.copytree(source, evidence)
    return evidence, case["snapshots"][page_key]


def _write_manifest(evidence):
    files = []
    for path in sorted(evidence.rglob("*")):
        if path.is_file() and path.relative_to(evidence).as_posix() != "manifest.json":
            files.append(
                {
                    "path": path.relative_to(evidence).as_posix(),
                    "sha256": support.sha256_bytes(path.read_bytes()),
                }
            )
    manifest = {"protocol": "b4-02-provenance-v1", "files": files}
    (evidence / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return manifest


def _entry(evidence, manifest):
    return {
        "provenance": {
            "dir": evidence.name,
            "manifest_sha256": support.sha256_bytes(support.C(manifest)),
        }
    }


def test_t1b_g10_three_page_identity_order_oracle(tmp_path, monkeypatch):
    case = support.make_multi_case(tmp_path, monkeypatch, dirty=("a", "b", "c"))
    candidates = json.loads(case["manifest"].read_text(encoding="utf-8"))["candidates"]
    reversed_candidates = list(reversed(candidates))
    expected_scope = sorted([_page_key(case, "a"), _page_key(case, "b"), _page_key(case, "c")])
    payload_a = support.request_payload(case, case["plan_data"], candidates)
    payload_b = support.request_payload(case, case["plan_data"], reversed_candidates)
    assert payload_a == payload_b
    assert payload_a["page_keys"] == expected_scope
    assert [page["page_key"] for page in payload_a["pages"]] == expected_scope
    assert set(case["plan_data"]["dirty"]) == set(expected_scope)


def test_t1b_g11_real_planner_full_blocked_unknown_removal(tmp_path, monkeypatch):
    case = support.make_multi_case(tmp_path, monkeypatch, dirty=())
    _inputs, unknown_plan = _add_page_d(case)
    assert unknown_plan["full_required"] is True
    assert unknown_plan["unknown"], unknown_plan

    removal_path = tmp_path / "removal"
    removal_path.mkdir()
    removal_case = support.make_multi_case(removal_path, monkeypatch, dirty=())
    inputs, removal_plan = _removal_plan(removal_case)
    assert removal_plan["full_required"] is True
    assert removal_plan["unknown"], removal_plan
    assert _page_key(removal_case, "c") in support.inc._removed_pages(removal_case["state"], inputs)

    blocked_path = tmp_path / "blocked-case"
    blocked_path.mkdir()
    blocked_case = support.make_multi_case(blocked_path, monkeypatch, dirty=())
    _inputs, blocked_plan = _blocked_plan(blocked_case)
    assert blocked_plan["full_required"] is True
    assert blocked_plan["blocked"], blocked_plan

def test_t1b_g12_provenance_rewrap_negatives(tmp_path, monkeypatch):
    case = support.make_multi_case(tmp_path, monkeypatch)
    evidence, snapshots = _evidence_package(case, tmp_path)
    manifest = _write_manifest(evidence)
    support.verify_provenance(tmp_path, _entry(evidence, manifest), snapshots)

    # 删除全部预期 snapshot，并重新封装 manifest
    shutil.rmtree(evidence, ignore_errors=True)
    shutil.copytree(case["root"] / case["reports"][_page_key(case, "a")]["provenance_dir"], evidence)
    for item in snapshots:
        (evidence / item["path"]).unlink()
    manifest = _write_manifest(evidence)
    with pytest.raises(AssertionError):
        support.verify_provenance(tmp_path, _entry(evidence, manifest), snapshots)

    # 改写 raw 字节，并重新封装 manifest
    shutil.rmtree(evidence, ignore_errors=True)
    shutil.copytree(case["root"] / case["reports"][_page_key(case, "a")]["provenance_dir"], evidence)
    target = evidence / snapshots[0]["path"]
    target.write_bytes(target.read_bytes() + b"tamper")
    manifest = _write_manifest(evidence)
    with pytest.raises(AssertionError):
        support.verify_provenance(tmp_path, _entry(evidence, manifest), snapshots)

    # 改写 sources.json artifact_path，并重新封装 manifest
    shutil.rmtree(evidence, ignore_errors=True)
    shutil.copytree(case["root"] / case["reports"][_page_key(case, "a")]["provenance_dir"], evidence)
    sources_path = evidence / "sources.json"
    sources = json.loads(sources_path.read_text(encoding="utf-8"))
    sources[0]["artifact_path"] = "snapshots/other.txt"
    sources_path.write_text(json.dumps(sources, ensure_ascii=False), encoding="utf-8")
    manifest = _write_manifest(evidence)
    with pytest.raises(AssertionError):
        support.verify_provenance(tmp_path, _entry(evidence, manifest), snapshots)

    # 改写 sources.json snapshot_sha256，并重新封装 manifest
    shutil.rmtree(evidence, ignore_errors=True)
    shutil.copytree(case["root"] / case["reports"][_page_key(case, "a")]["provenance_dir"], evidence)
    sources_path = evidence / "sources.json"
    sources = json.loads(sources_path.read_text(encoding="utf-8"))
    sources[0]["snapshot_sha256"] = "0" * 64
    sources_path.write_text(json.dumps(sources, ensure_ascii=False), encoding="utf-8")
    manifest = _write_manifest(evidence)
    with pytest.raises(AssertionError):
        support.verify_provenance(tmp_path, _entry(evidence, manifest), snapshots)


def test_t1b_three_page_order_entry(tmp_path, monkeypatch):
    case = support.make_multi_case(tmp_path, monkeypatch, dirty=("a", "b", "c"))
    api = load_multi_api()
    manifest = json.loads(case["manifest"].read_text(encoding="utf-8"))
    manifest["candidates"] = list(reversed(manifest["candidates"]))
    support.write_json(case["manifest"], manifest)
    candidates = manifest["candidates"]
    expected = sorted([_page_key(case, "a"), _page_key(case, "b"), _page_key(case, "c")])
    before = support.snapshot_files(case["root"])
    before_dirs = support.snapshot_dirs(case["root"])

    replacements = []
    real_replace = os.replace

    def wrapped(source_path, destination_path):
        destination = Path(destination_path)
        for key in expected:
            if destination == (case["root"] / case["registry"][key]["output"]).resolve():
                replacements.append(key)
        return real_replace(source_path, destination_path)

    monkeypatch.setattr(os, "replace", wrapped)
    report = api.precheck_rebuild(case["plan"], case["state"], case["config"], case["manifest"])
    assert report["action"] == "multi_page", report
    assert report["build_set"] == expected
    assert support.snapshot_files(case["root"]) == before
    assert support.snapshot_dirs(case["root"]) == before_dirs

    result = api.commit_rebuild(case["plan"], case["state"], case["config"], case["manifest"])
    assert result["status"] == "committed", result
    assert replacements == expected
    support.assert_multi_success(case, result, before, expected, candidates)

    state = json.loads(case["state"].read_text(encoding="utf-8"))
    graph = state["graph"].copy()
    graph.pop("generation", None)
    original = copy.deepcopy(case["original_graph"])
    original.pop("generation", None)
    assert support.C(graph) == support.C(original)

def test_t1b_real_bad_response_rejects_whole_plan(tmp_path, monkeypatch):
    case = support.make_multi_case(tmp_path, monkeypatch, dirty=("a", "b"))
    api = load_multi_api()
    _inputs, plan = _lint_failure_case(case)
    assert plan["blocked"], plan
    expected_blocked = f"blocked:{plan['blocked'][0]}"
    before = support.snapshot_files(case["root"])
    before_dirs = support.snapshot_dirs(case["root"])
    report = api.precheck_rebuild(case["plan"], case["state"], case["config"], case["manifest"])
    assert report["action"] == "reject", report
    support.assert_error_member(report["errors"], expected_blocked)
    assert support.snapshot_files(case["root"]) == before
    assert support.snapshot_dirs(case["root"]) == before_dirs
    result = api.commit_rebuild(case["plan"], case["state"], case["config"], case["manifest"])
    assert result["status"] == "rejected", result
    support.assert_error_member(result.get("errors") or [], expected_blocked)
    assert support.snapshot_files(case["root"]) == before


def test_t1b_staging_failure_rejects_whole_plan(tmp_path, monkeypatch):
    case = support.make_multi_case(tmp_path, monkeypatch, dirty=("a", "b"))
    api = load_multi_api()
    b_hash = support.sha256_file(case["root"] / case["registry"][case["page_keys"]["b"]]["response"])
    a_hash = support.sha256_file(case["root"] / case["registry"][case["page_keys"]["a"]]["response"])
    expected_error = (
        f"staging_failed:{case['page_keys']['b']}:staging_lint:L1:injected"
    )
    real_ingest = support.ingest.ingest
    trace = []

    def wrapped(root_arg, schema_arg, *args, **kwargs):
        response_file = kwargs.get("response_file")
        output = kwargs.get("output")
        if support.staging_injection_target(response_file, output, case["root"], b_hash):
            trace.append("B_HIT")
            raise support.ingest.IngestError("staging_lint:L1:injected")
        result = real_ingest(root_arg, schema_arg, *args, **kwargs)
        if response_file and support.sha256_file(Path(response_file)) == a_hash:
            trace.append("A_OK")
        return result

    monkeypatch.setattr(support.ingest, "ingest", wrapped)
    before = support.snapshot_files(case["root"])
    before_dirs = support.snapshot_dirs(case["root"])
    report = api.precheck_rebuild(case["plan"], case["state"], case["config"], case["manifest"])
    assert report["action"] == "multi_page", report
    assert report["full_validation_passed"] == "not_run"
    assert trace == []
    assert support.snapshot_files(case["root"]) == before
    assert support.snapshot_dirs(case["root"]) == before_dirs
    result = api.commit_rebuild(case["plan"], case["state"], case["config"], case["manifest"])
    assert result["status"] == "rejected", result
    support.assert_staging_injection(trace, result.get("errors") or [], case["page_keys"]["b"], expected_error)
    assert support.snapshot_files(case["root"]) == before

@pytest.mark.parametrize("scenario", ["blocked", "unknown", "removal"])
def test_t1b_real_planner_reject_scenarios_entry(tmp_path, monkeypatch, scenario):
    case = support.make_multi_case(tmp_path, monkeypatch, dirty=())
    api = load_multi_api()
    if scenario == "blocked":
        _inputs, plan = _blocked_plan(case)
        expected = f"blocked:{plan['blocked'][0]}"
    elif scenario == "unknown":
        _inputs, plan = _add_page_d(case)
        expected = f"unknown:{plan['unknown'][0]}"
    else:
        _inputs, plan = _removal_plan(case)
        expected = "removal_not_supported"
    support.write_json(case["plan"], plan)
    before_files = support.snapshot_files(case["root"])
    before_dirs = support.snapshot_dirs(case["root"])
    report = api.precheck_rebuild(case["plan"], case["state"], case["config"], case["manifest"])
    assert report["action"] == "reject", report
    assert report["executable"] is False
    support.assert_error_member(report["errors"], expected)
    assert support.snapshot_files(case["root"]) == before_files
    assert support.snapshot_dirs(case["root"]) == before_dirs
    result = api.commit_rebuild(case["plan"], case["state"], case["config"], case["manifest"])
    assert result["status"] == "rejected", result
    support.assert_error_member(result.get("errors") or [], expected)
    assert support.snapshot_files(case["root"]) == before_files


def test_t1b_full_multi_page_entry(tmp_path, monkeypatch):
    case = support.make_multi_case(tmp_path, monkeypatch, dirty=())
    api = load_multi_api()
    inputs, plan = _conservative_full_case(case, single=False)
    assert plan["full_required"] is True
    assert plan["full_required_reasons"] == ["conservative_full"]
    assert not plan["blocked"] and not plan["unknown"] and not plan["graph_diagnostics"]
    case["plan_data"] = plan
    support.write_json(case["plan"], plan)
    candidates = [
        {"page_key": page["page_key"], "response": page["response"]}
        for item in inputs["inputs"]
        for page in item["managed_pages"]
    ]
    candidates.sort(key=lambda item: item["page_key"])
    support.write_json(case["manifest"], {"format_version": 1, "candidates": candidates})
    expected = sorted(page["page_key"] for item in inputs["inputs"] for page in item["managed_pages"])
    before = support.snapshot_files(case["root"])
    before_dirs = support.snapshot_dirs(case["root"])
    report = api.precheck_rebuild(case["plan"], case["state"], case["config"], case["manifest"])
    assert report["action"] == "multi_page", report
    assert report["executable"] is True
    assert report["build_set"] == expected
    assert support.snapshot_files(case["root"]) == before
    assert support.snapshot_dirs(case["root"]) == before_dirs
    result = api.commit_rebuild(case["plan"], case["state"], case["config"], case["manifest"])
    support.assert_multi_success(case, result, before, expected, candidates)

def test_t1b_full_single_page_keeps_b202_reject(tmp_path, monkeypatch):
    case = support.make_multi_case(tmp_path, monkeypatch, dirty=())
    api = load_multi_api()
    inputs, plan = _conservative_full_case(case, single=True)
    assert plan["full_required"] is True
    assert not plan["blocked"] and not plan["unknown"]
    support.write_json(case["plan"], plan)
    manifest = {
        "format_version": 1,
        "candidates": [
            {"page_key": page["page_key"], "response": page["response"]}
            for item in inputs["inputs"]
            for page in item["managed_pages"]
        ],
    }
    support.write_json(case["manifest"], manifest)
    before = support.snapshot_files(case["root"])
    before_dirs = support.snapshot_dirs(case["root"])
    report = api.precheck_rebuild(case["plan"], case["state"], case["config"], case["manifest"])
    assert report["action"] == "reject", report
    assert report["executable"] is False
    support.assert_error_member(report["errors"], "full_required_not_supported")
    assert support.snapshot_files(case["root"]) == before
    assert support.snapshot_dirs(case["root"]) == before_dirs
    result = api.commit_rebuild(case["plan"], case["state"], case["config"], case["manifest"])
    assert result["status"] == "rejected", result
    support.assert_error_member(result.get("errors") or [], "full_required_not_supported")
    assert support.snapshot_files(case["root"]) == before

def test_t1b_g13_conservative_full_oracle(tmp_path, monkeypatch):
    for single in (False, True):
        sub = tmp_path / f"full-{single}"
        sub.mkdir()
        case = support.make_multi_case(sub, monkeypatch, dirty=())
        inputs, plan = _conservative_full_case(case, single=single)
        expected = sorted(page["page_key"] for item in inputs["inputs"] for page in item["managed_pages"])
        assert plan["full_required"] is True
        assert plan["full_required_reasons"] == ["conservative_full"]
        assert plan["blocked"] == [] and plan["unknown"] == []
        assert plan["graph_diagnostics"] == []
        assert not support.inc._removed_pages(case["state"], inputs)
        assert plan["affected_pages"] == expected
        assert len(expected) == (1 if single else 3)
        assert set(plan["dirty"]) == {case["page_keys"]["a"]}


def test_t1b_g14_exact_diagnostic_and_dir_guard_selfcheck(tmp_path, monkeypatch):
    support.assert_error_member(["blocked:common:schema_lock"], "blocked:common:schema_lock")
    with pytest.raises(AssertionError):
        support.assert_error_member(["not_blocked:common:schema_lock"], "blocked:common:schema_lock")
    case = support.make_multi_case(tmp_path, monkeypatch, dirty=())
    before = support.snapshot_dirs(case["root"])
    forbidden = case["root"] / "reports/incremental/staging/forbidden"
    forbidden.mkdir(parents=True)
    assert support.snapshot_dirs(case["root"]) != before
    forbidden.rmdir()
    staging = case["root"] / "reports/incremental/staging"
    if staging.exists():
        staging.rmdir()
    assert support.snapshot_dirs(case["root"]) == before


def test_t1b_g15_staging_injection_oracle_selfcheck(tmp_path, monkeypatch):
    case = support.make_multi_case(tmp_path, monkeypatch, dirty=("a", "b"))
    b_hash = support.sha256_file(case["root"] / case["registry"][case["page_keys"]["b"]]["response"])
    frozen = case["root"] / "reports/incremental/staging/tx/candidate.md"
    frozen.parent.mkdir(parents=True, exist_ok=True)
    frozen.write_bytes((case["root"] / case["registry"][case["page_keys"]["b"]]["response"]).read_bytes())
    output = case["root"] / "reports/incremental/staging/tx/page.md"
    assert support.staging_injection_target(frozen, output, case["root"], b_hash) is True
    assert support.staging_injection_target(frozen, output, case["root"], "0" * 64) is False
    assert support.staging_injection_target(frozen, case["root"] / "reports/fixtures/b.md", case["root"], b_hash) is False
    expected_error = f"staging_failed:{case['page_keys']['b']}:staging_lint:L1:injected"
    support.assert_staging_injection(["A_OK", "B_HIT"], [expected_error], case["page_keys"]["b"], expected_error)
    with pytest.raises(AssertionError):
        support.assert_staging_injection([], [expected_error], case["page_keys"]["b"], expected_error)
    with pytest.raises(AssertionError):
        support.assert_staging_injection(["B_HIT"], [expected_error], case["page_keys"]["b"], expected_error)
    with pytest.raises(AssertionError):
        support.assert_staging_injection(["A_OK", "B_HIT"], ["unrelated_failure"], case["page_keys"]["b"], expected_error)

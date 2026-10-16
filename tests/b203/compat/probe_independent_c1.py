"""Independent contract checks for C1 assets; never run production recovery."""

import copy
import importlib
import json
import sys
from pathlib import Path

import pytest

PROJECT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "tests"))
rs = importlib.import_module("b203.recovery_support")
support = importlib.import_module("b203.support")
batch = importlib.import_module("b203.test_recovery_fixture_contract")


@pytest.fixture
def case(tmp_path, monkeypatch):
    return rs.make_recovery_case(tmp_path, monkeypatch)


def test_positive_complete_provenance_and_identity(case):
    rs.materialize_layout(case, state_variant="new", outputs="new")
    assert rs.validate_journal_projection(case)
    for entry in case["journal"]["pages"]:
        support.verify_provenance(case["root"], entry, case["snapshots"][entry["page_key"]])


def test_positive_default_old_new_outputs_distinct(case):
    assert all(case["old_outputs"][key] != case["new_outputs"][key] for key in case["keys"])


def test_old_layout_still_matches_authoritative_request(case):
    rs.materialize_layout(case, state_variant="old", outputs="old")
    current = support.inc.build_plan(case["inputs"], case["state"])
    assert current["dirty"] == case["request_payload"]["execution"]["dirty"]


@pytest.mark.parametrize("field", [
    "state_path", "inputs_path", "parent_generation", "response_hash",
    "output_path", "project_root_id", "provenance_owner",
])
def test_projection_rejects_single_inconsistent_field(case, field):
    journal = copy.deepcopy(case["journal"])
    if field == "state_path":
        journal["state"]["path"] = "reports/other-state.json"
    elif field == "inputs_path":
        journal["inputs"]["path"] = "config/other-inputs.json"
    elif field == "parent_generation":
        journal["parent_generation"] = "other-parent"
    elif field == "response_hash":
        journal["pages"][0]["response"]["sha256"] = "0" * 64
    elif field == "output_path":
        journal["pages"][0]["output"]["path"] = "responses/a.md"
    elif field == "project_root_id":
        journal["project_root_id"] = "0" * 64
    else:
        journal["pages"][0]["provenance"]["dir"] = "reports/incremental/provenance/other"
    with pytest.raises(AssertionError):
        rs.validate_journal_projection(case, journal)


@pytest.mark.parametrize("damage", ["manifest", "journal_members", "manifest_digest"])
def test_subset_rejects_corrupt_trusted_manifest_material(case, damage):
    rs.materialize_layout(case, outputs="old", provenance="full")
    entry = copy.deepcopy(case["journal"]["pages"][0])
    key = entry["page_key"]
    if damage == "manifest":
        (case["root"] / entry["provenance"]["dir"] / "manifest.json").write_bytes(b"not-json")
    elif damage == "journal_members":
        entry["provenance"]["members"] = []
    else:
        entry["provenance"]["manifest_sha256"] = "0" * 64
    with pytest.raises(AssertionError):
        rs.assert_provenance_subset(case["root"], entry, case["provenance"][key]["members"])


def test_partial_layout_preserves_trusted_original_manifest(case):
    rs.materialize_layout(case, provenance="partial")
    for entry in case["journal"]["pages"]:
        manifest = json.loads((case["root"] / entry["provenance"]["dir"] / "manifest.json").read_text("utf-8"))
        assert support.sha256_bytes(support.C(manifest)) == entry["provenance"]["manifest_sha256"]


def test_subset_accepts_fully_removed_owned_directory(case):
    rs.materialize_layout(case, provenance="missing")
    entry = case["journal"]["pages"][0]
    key = entry["page_key"]
    missing = rs.assert_provenance_subset(case["root"], entry, case["provenance"][key]["members"])
    assert missing == set(case["provenance"][key]["members"])


@pytest.mark.parametrize("mutation", ["schema", "sibling_file", "empty_dir"])
def test_preservation_snapshot_detects_protected_change(case, mutation):
    rs.materialize_layout(case)
    before = rs.snapshot_recovery_resources(case)
    if mutation == "schema":
        (case["root"] / support.SCHEMA).write_bytes(b"third party edit")
    elif mutation == "sibling_file":
        sentinel = case["root"] / "reports/incremental/staging" / case["txid"] / "other-token/sentinel.txt"
        sentinel.parent.mkdir(parents=True)
        sentinel.write_bytes(b"must be detected")
    else:
        (case["root"] / "protected-empty-directory").mkdir()
    after = rs.snapshot_recovery_resources(case)
    with pytest.raises(AssertionError, match="recovery resources changed"):
        rs.assert_recovery_preserved(before, after)


def test_missing_old_variant_can_be_snapshotted(tmp_path, monkeypatch):
    variant = rs.make_recovery_case(tmp_path, monkeypatch, missing_old=("c",))
    rs.materialize_layout(variant, outputs="old")
    snapshot = rs.snapshot_recovery_resources(variant)
    relative = variant["registry"][variant["page_keys"]["c"]]["output"]
    assert snapshot[relative] is rs.MISSING


def test_missing_old_backup_still_has_derived_path(tmp_path, monkeypatch):
    variant = rs.make_recovery_case(tmp_path, monkeypatch, missing_old=("c",))
    key = variant["page_keys"]["c"]
    entry = next(item for item in variant["journal"]["pages"] if item["page_key"] == key)
    assert entry["backup"]["path"] == f"reports/incremental/backups/{variant['txid']}/{rs.page_slug(key)}.bak"


def test_mixed_layout_is_really_a_new_b_new_c_old(case):
    rs.materialize_layout(case, outputs="mixed")
    key = case["page_keys"]["c"]
    assert (case["root"] / case["registry"][key]["output"]).read_bytes() == case["old_outputs"][key]


def test_g01_rejects_missing_build_contract(tmp_path, monkeypatch):
    original = rs.make_recovery_case

    def broken(*args, **kwargs):
        result = original(*args, **kwargs)
        state = json.loads(result["new_state_bytes"])
        del state["build"]
        result["new_state_bytes"] = json.dumps(state, ensure_ascii=False).encode("utf-8")
        return result

    monkeypatch.setattr(rs, "make_recovery_case", broken)
    with pytest.raises(AssertionError):
        batch.test_c1_g01_legal_master_fixture(tmp_path, monkeypatch)


def test_g02_rejects_swapped_response_and_output_digest(tmp_path, monkeypatch):
    original = rs.make_recovery_case

    def broken(*args, **kwargs):
        result = original(*args, **kwargs)
        for entry in result["journal"]["pages"]:
            entry["response"]["sha256"] = entry["output"]["new_sha256"]
            entry["output"]["new_sha256"] = "0" * 64
        return result

    monkeypatch.setattr(rs, "make_recovery_case", broken)
    with pytest.raises(AssertionError):
        batch.test_c1_g02_digest_separation(tmp_path, monkeypatch)


def test_g10_requires_valid_master_before_attack(tmp_path, monkeypatch):
    original = rs.make_recovery_case

    def broken(*args, **kwargs):
        result = original(*args, **kwargs)
        result["journal"]["request_id"] = "0" * 64
        return result

    monkeypatch.setattr(rs, "make_recovery_case", broken)
    with pytest.raises(AssertionError):
        batch.test_c1_g10_resource_ownership_attacks(tmp_path, monkeypatch)


def test_g11_rejects_snapshot_that_only_watches_output(tmp_path, monkeypatch):
    def output_only(case):
        key = case["keys"][0]
        path = case["root"] / case["registry"][key]["output"]
        return {path.relative_to(case["root"]).as_posix(): path.read_bytes()}

    monkeypatch.setattr(rs, "snapshot_recovery_resources", output_only)
    with pytest.raises(AssertionError):
        batch.test_c1_g11_expectation_isolation(tmp_path, monkeypatch)

"""Focused checks for unresolved C1 contracts after C01-C06 delivery."""

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


def test_build_output_matches_proposal_sha256_field(case):
    state = json.loads(case["new_state_bytes"])
    for key in case["scope"]:
        output = state["build"]["pages"][key]["output"]
        assert output.get("sha256") == support.sha256_bytes(case["new_outputs"][key])


def test_journal_state_digest_binds_actual_saved_new_state(case):
    state = json.loads(case["new_state_bytes"])
    state["build"]["built_at"] = "2026-10-15T01:00:00+00:00"
    case["new_state_bytes"] = json.dumps(state, ensure_ascii=False).encode("utf-8")
    assert rs.state_digest(state) != case["journal"]["state"]["new_sha256"]
    with pytest.raises(AssertionError):
        rs.validate_journal_projection(case)


@pytest.mark.parametrize("damage", ["members", "old_exists_type"])
def test_complete_projection_rejects_remaining_bad_fields(case, damage):
    journal = copy.deepcopy(case["journal"])
    if damage == "members":
        journal["pages"][0]["provenance"]["members"] = []
    else:
        journal["pages"][0]["output"]["old_exists"] = 1
    with pytest.raises(AssertionError):
        rs.validate_journal_projection(case, journal)


def test_build_page_generation_is_validated(case):
    state = json.loads(case["new_state_bytes"])
    state["build"]["pages"][case["scope"][0]]["generation"] = "different-generation"
    with pytest.raises(AssertionError):
        rs.validate_state_build(case, state)


def test_g03_rejects_invalid_journal_token(tmp_path, monkeypatch):
    original = rs.make_recovery_case

    def broken(*args, **kwargs):
        result = original(*args, **kwargs)
        result["journal"]["execution_token"] = "unbound-token"
        return result

    monkeypatch.setattr(rs, "make_recovery_case", broken)
    with pytest.raises(AssertionError):
        batch.test_c1_g03_two_tokens_identity(tmp_path, monkeypatch)


def test_g08_covers_equal_hash_page_inside_transaction(tmp_path, monkeypatch):
    original = rs.make_recovery_case
    made = []

    def recording(*args, **kwargs):
        result = original(*args, **kwargs)
        made.append(result)
        return result

    monkeypatch.setattr(rs, "make_recovery_case", recording)
    batch.test_c1_g08_old_equal_and_missing_old(tmp_path, monkeypatch)
    assert any(
        item["old_outputs"][key] == item["new_outputs"][key]
        for item in made for key in item["scope"]
    ), "G08 never constructs an equal-hash page inside the transaction"


def test_g08_materializes_new_output_without_old(tmp_path, monkeypatch):
    original = rs.materialize_layout
    made_new_without_old = []

    def recording(case, *args, **kwargs):
        result = original(case, *args, **kwargs)
        for key in case["scope"]:
            if case["old_outputs"][key] is None:
                output = case["root"] / case["registry"][key]["output"]
                made_new_without_old.append(output.exists() and output.read_bytes() == case["new_outputs"][key])
        return result

    monkeypatch.setattr(rs, "materialize_layout", recording)
    batch.test_c1_g08_old_equal_and_missing_old(tmp_path, monkeypatch)
    assert any(made_new_without_old), "G08 only constructs the already-absent old output"


def test_g08_variant_passes_its_own_complete_contract(tmp_path, monkeypatch):
    variant = rs.make_recovery_case(tmp_path, monkeypatch, old_equal=("a",), missing_old=("c",))
    assert rs.validate_journal_projection(variant)

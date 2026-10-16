"""Focused G10 self-test check and concrete resealed-journal test cases."""

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


def test_existing_g10_detects_blind_journal_validator(tmp_path, monkeypatch):
    original = rs.validate_journal_projection

    def blind_to_mutated_journal(case, journal=None):
        if journal is not None:
            return True
        return original(case)

    monkeypatch.setattr(rs, "validate_journal_projection", blind_to_mutated_journal)
    with pytest.raises(pytest.fail.Exception):
        added = getattr(batch, "test_c1_g15_resealed_journal_ownership", None)
        if added is None:
            batch.test_c1_g10_resource_ownership_attacks(tmp_path, monkeypatch)
        else:
            added(tmp_path, monkeypatch, "owner_root")


@pytest.mark.parametrize("attack", ["owner_root", "sibling_token", "output_redirect"])
def test_g10_resealed_journal_ownership(tmp_path, monkeypatch, attack):
    case = rs.make_recovery_case(tmp_path, monkeypatch)
    assert rs.validate_journal_projection(case)
    rs.materialize_layout(case, outputs="old", provenance="full", backups="full")

    sentinel_paths = [
        f"reports/incremental/staging/{case['txid']}/owner-sentinel.txt",
        f"reports/incremental/staging/{case['txid']}/other-token/sentinel.txt",
        "reports/incremental/staging/other-transaction/token/sentinel.txt",
        "reports/incremental/backups/other-transaction/sentinel.txt",
        "reports/incremental/provenance/other-transaction/sentinel.txt",
    ]
    for relative in sentinel_paths:
        sentinel = case["root"] / relative
        sentinel.parent.mkdir(parents=True, exist_ok=True)
        sentinel.write_bytes(b"must remain unchanged")
    before = rs.snapshot_recovery_resources(case)

    # Start each attack from a different, valid execution identity.
    candidate = rs.clone_with_token(case, "g10-resealed-token")
    assert rs.validate_journal_projection(candidate)
    journal = copy.deepcopy(candidate["journal"])
    if attack == "owner_root":
        journal["staging_root"] = f"reports/incremental/staging/{candidate['txid']}"
        expected = "staging owner mismatch"
    elif attack == "sibling_token":
        journal["staging_root"] = f"reports/incremental/staging/{candidate['txid']}/other-token"
        expected = "staging owner mismatch"
    else:
        key = candidate["scope"][0]
        redirect = candidate["registry"][key]["response"]
        for page in candidate["request_payload"]["pages"]:
            if page["page_key"] == key:
                page["output"] = redirect
        candidate["request_id"] = support.sha256_bytes(support.C(candidate["request_payload"]))
        candidate["journal"]["request_payload"] = copy.deepcopy(candidate["request_payload"])
        candidate["journal"]["request_id"] = candidate["request_id"]
        for page in candidate["journal"]["pages"]:
            if page["page_key"] == key:
                page["output"]["path"] = redirect
        state = json.loads(candidate["new_state_bytes"])
        state["build"]["request_id"] = candidate["request_id"]
        state["build"]["pages"][key]["output"]["path"] = redirect
        candidate["new_state_bytes"] = rs._dump_state(state)
        candidate = rs.clone_with_token(candidate, "g10-redirect-token")
        journal = copy.deepcopy(candidate["journal"])
        expected = "output path mismatch"

    # Identity checks must pass so the target ownership check sees the attack.
    assert journal["request_id"] == support.sha256_bytes(support.C(journal["request_payload"]))
    assert journal["transaction_id"] == support.expected_transaction_id(
        journal["request_id"], journal["execution_token"]
    )
    with pytest.raises(AssertionError) as caught:
        rs.validate_journal_projection(candidate, journal)
    assert str(caught.value) == expected
    rs.assert_recovery_preserved(before, rs.snapshot_recovery_resources(case))

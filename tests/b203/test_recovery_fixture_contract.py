# -*- coding: utf-8 -*-
"""B203 T1-C C1：恢复 fixture 与独立断言自检（G01–G12）。"""
import copy
import json
from pathlib import Path

import pytest

from . import recovery_support as rs
from . import support


def _journal_page(case, key):
    return next(page for page in case["journal"]["pages"] if page["page_key"] == key)


def test_c1_g01_legal_master_fixture(tmp_path, monkeypatch):
    case = rs.make_recovery_case(tmp_path, monkeypatch)
    assert rs.validate_journal_projection(case)
    assert case["journal"]["format_version"] == 2
    assert case["journal"]["protocol"] == "b4-02-multi-commit-v1"
    assert case["journal"]["status"] == "prepared"
    assert [page["page_key"] for page in case["journal"]["pages"]] == case["keys"]
    assert rs.derive_staging_root(case) == case["journal"]["staging_root"]
    assert rs.state_digest(json.loads(case["state"].read_text(encoding="utf-8"))) == case["journal"]["state"]["old_sha256"]
    rs.validate_state_build(case, json.loads(case["new_state_bytes"]))
    case["state"].write_bytes(case["new_state_bytes"])
    loaded, errors = support.inc.load_baseline(case["state"])
    assert loaded is not None, errors
    case["state"].write_bytes(case["old_state_bytes"])
    loaded, errors = support.inc.load_baseline(case["state"])
    assert loaded is not None, errors
    rs.materialize_layout(case, state_variant="new", outputs="new", provenance="full", backups="full")
    for key in case["keys"]:
        entry = _journal_page(case, key)
        support.verify_provenance(case["root"], entry, case["snapshots"][key])


def test_c1_g02_digest_separation(tmp_path, monkeypatch):
    case = rs.make_recovery_case(tmp_path, monkeypatch)
    sample = {"b": [2, 1], "a": 1}
    assert support.C(sample) != rs.canonical_state(sample)
    assert support.sha256_bytes(b"a\r\n") != support.sha256_bytes(b"a\n")
    for key in case["keys"]:
        managed = case["registry"][key]
        response = support.sha256_file(case["root"] / managed["response"])
        output = support.sha256_bytes(case["new_outputs"][key])
        assert response == support.sha256_bytes((case["root"] / managed["response"]).read_bytes())
        assert output == support.sha256_bytes(case["new_outputs"][key])
    for page in case["journal"]["pages"]:
        managed = case["registry"][page["page_key"]]
        assert page["response"]["sha256"] == support.sha256_file(case["root"] / managed["response"])
        assert page["output"]["new_sha256"] == support.sha256_bytes(case["new_outputs"][page["page_key"]])
    state = json.loads(case["new_state_bytes"])
    assert case["journal"]["state"]["new_sha256"] == rs.state_digest(state)
    for page in case["journal"]["pages"]:
        assert page["provenance"]["manifest_sha256"] == case["provenance"][page["page_key"]]["manifest_sha256"]


def test_c1_g03_two_tokens_identity(tmp_path, monkeypatch):
    case = rs.make_recovery_case(tmp_path, monkeypatch)
    assert rs.validate_journal_projection(case)
    token_a = "token-a"
    token_b = "token-b"
    txid_a = support.expected_transaction_id(case["request_id"], token_a)
    txid_b = support.expected_transaction_id(case["request_id"], token_b)
    assert txid_a != txid_b
    assert case["request_id"] == support.sha256_bytes(support.C(case["request_payload"]))
    assert "built_at" not in case["request_payload"]
    assert rs.derive_staging_root({**case, "token": token_a}) != rs.derive_staging_root({**case, "token": token_b})


def test_c1_g04_old_state_partial_new_outputs(tmp_path, monkeypatch):
    case = rs.make_recovery_case(tmp_path, monkeypatch)
    rs.materialize_layout(case, state_variant="old", outputs="old", provenance="full", backups="full")
    for key in (case["page_keys"]["a"], case["page_keys"]["b"]):
        (case["root"] / case["registry"][key]["output"]).write_bytes(case["new_outputs"][key])
    before = rs.snapshot_recovery_resources(case)
    assert (case["root"] / case["registry"][case["page_keys"]["a"]]["output"]).read_bytes() == case["new_outputs"][case["page_keys"]["a"]]
    assert (case["root"] / case["registry"][case["page_keys"]["b"]]["output"]).read_bytes() == case["new_outputs"][case["page_keys"]["b"]]
    assert (case["root"] / case["registry"][case["page_keys"]["c"]]["output"]).read_bytes() == case["old_outputs"][case["page_keys"]["c"]]
    assert before
    target_rollback = list(reversed([case["page_keys"]["a"], case["page_keys"]["b"]]))
    assert target_rollback == [case["page_keys"]["b"], case["page_keys"]["a"]]


def test_c1_g05_new_state_full_resources(tmp_path, monkeypatch):
    case = rs.make_recovery_case(tmp_path, monkeypatch)
    rs.materialize_layout(case, state_variant="new", outputs="new", provenance="full", backups="full")
    before = rs.snapshot_recovery_resources(case)
    loaded, errors = support.inc.load_baseline(case["state"])
    assert loaded is not None, errors
    for key in case["keys"]:
        support.verify_provenance(case["root"], _journal_page(case, key), case["snapshots"][key])
    assert (case["root"] / "reports/incremental/journal" / case["journal_name"]).exists()
    assert before


def test_c1_g06_partial_provenance(tmp_path, monkeypatch):
    case = rs.make_recovery_case(tmp_path, monkeypatch)
    rs.materialize_layout(case, state_variant="old", outputs="old", provenance="partial", backups="full")
    key = case["keys"][0]
    entry = _journal_page(case, key)
    missing = rs.assert_provenance_subset(case["root"], entry, case["provenance"][key]["members"])
    assert missing
    with pytest.raises(AssertionError):
        support.verify_provenance(case["root"], entry, case["snapshots"][key])
    directory = case["root"] / case["provenance"][key]["dir"]
    (directory / "extra.txt").write_bytes(b"extra")
    with pytest.raises(AssertionError):
        rs.assert_provenance_subset(case["root"], entry, case["provenance"][key]["members"])
    (directory / "extra.txt").unlink()
    first = sorted(case["provenance"][key]["members"])[0]
    (directory / first).write_bytes(b"rewritten")
    with pytest.raises(AssertionError):
        rs.assert_provenance_subset(case["root"], entry, case["provenance"][key]["members"])


def test_c1_g07_manifest_missing_and_nested(tmp_path, monkeypatch):
    case = rs.make_recovery_case(tmp_path, monkeypatch)
    key = case["keys"][0]
    entry = _journal_page(case, key)
    rs.materialize_layout(case, state_variant="old", outputs="old", provenance="full", backups="full")
    directory = case["root"] / case["provenance"][key]["dir"]
    (directory / "manifest.json").unlink()
    assert rs.assert_provenance_subset(case["root"], entry, case["provenance"][key]["members"]) == set()
    nested = directory / "snapshots/manifest.json"
    nested.parent.mkdir(parents=True, exist_ok=True)
    nested.write_bytes(b"nested evidence")
    with pytest.raises(AssertionError):
        rs.assert_provenance_subset(case["root"], entry, case["provenance"][key]["members"])
    expected = dict(case["provenance"][key]["members"])
    expected["snapshots/manifest.json"] = b"nested evidence"
    entry = copy.deepcopy(entry)
    files = [
        {"path": relative, "sha256": rs.sha256_bytes(payload)}
        for relative, payload in sorted(expected.items())
    ]
    entry["provenance"]["members"] = files
    entry["provenance"]["manifest_sha256"] = rs.sha256_bytes(
        support.C({"protocol": "b4-02-provenance-v1", "files": files})
    )
    assert rs.assert_provenance_subset(case["root"], entry, expected) == set()


def test_c1_g08_old_equal_and_missing_old(tmp_path, monkeypatch):
    variant = rs.make_recovery_case(
        tmp_path, monkeypatch, old_equal=("a",), missing_old=("c",), conservative_full=True
    )
    a = variant["page_keys"]["a"]
    c = variant["page_keys"]["c"]
    assert variant["old_outputs"][a] == variant["new_outputs"][a]
    assert a in variant["scope"]
    assert variant["old_outputs"][c] is None
    a_entry = _journal_page(variant, a)
    c_entry = _journal_page(variant, c)
    assert a_entry["output"]["old_sha256"] == a_entry["output"]["new_sha256"]
    assert c_entry["output"]["old_exists"] is False
    assert c_entry["output"]["old_sha256"] is None
    assert c_entry["backup"]["path"] == f"reports/incremental/backups/{variant['txid']}/{rs.page_slug(c)}.bak"
    assert c_entry["backup"]["sha256"] is None
    assert rs.validate_journal_projection(variant)
    rs.materialize_layout(variant, state_variant="old", outputs="old", provenance="full", backups="full")
    assert not (variant["root"] / variant["registry"][c]["output"]).exists()
    rs.materialize_layout(variant, state_variant="old", outputs="new", provenance="full", backups="full")
    assert (variant["root"] / variant["registry"][c]["output"]).read_bytes() == variant["new_outputs"][c]


def test_c1_g09_identity_projection_mutations(tmp_path, monkeypatch):
    case = rs.make_recovery_case(tmp_path, monkeypatch)
    broken_payload = copy.deepcopy(case["journal"])
    broken_payload["request_payload"].pop("page_keys", None)
    with pytest.raises(AssertionError):
        rs.validate_journal_projection(case, broken_payload)
    bad_token = copy.deepcopy(case["journal"])
    bad_token["execution_token"] = "other-token"
    with pytest.raises(AssertionError):
        rs.validate_journal_projection(case, bad_token)
    bad_order = copy.deepcopy(case["journal"])
    bad_order["pages"] = list(reversed(bad_order["pages"]))
    with pytest.raises(AssertionError):
        rs.validate_journal_projection(case, bad_order)
    bad_request = copy.deepcopy(case["journal"])
    bad_request["request_id"] = "0" * 64
    with pytest.raises(AssertionError):
        rs.validate_journal_projection(case, bad_request)


def test_c1_g10_resource_ownership_attacks(tmp_path, monkeypatch):
    case = rs.make_recovery_case(tmp_path, monkeypatch)
    assert rs.validate_journal_projection(case)
    with pytest.raises(AssertionError):
        rs.assert_staging_owner(case, f"reports/incremental/staging/{case['txid']}")
    with pytest.raises(AssertionError):
        rs.assert_staging_owner(case, f"reports/incremental/staging/{case['txid']}/other-token")
    with pytest.raises(AssertionError):
        rs.assert_registered_output(case, case["keys"][0], "unregistered-output.md")
    with pytest.raises(AssertionError):
        rs.assert_not_protected(case, case["registry"][case["keys"][0]]["response"])
    with pytest.raises(AssertionError):
        rs.assert_not_protected(case, "wiki/__schema__/entity-template.md")


def test_c1_g11_expectation_isolation(tmp_path, monkeypatch):
    def fresh(name):
        sub = tmp_path / name
        sub.mkdir()
        return rs.make_recovery_case(sub, monkeypatch)

    # output mutation
    case = fresh("output")
    rs.materialize_layout(case, state_variant="old", outputs="old", provenance="full", backups="full")
    before = rs.snapshot_recovery_resources(case)
    key = case["keys"][0]
    (case["root"] / case["registry"][key]["output"]).write_bytes(b"changed")
    after = rs.snapshot_recovery_resources(case)
    assert after != before
    with pytest.raises(AssertionError, match="recovery resources changed"):
        rs.assert_recovery_preserved(before, after)

    # provenance member mutation
    case = fresh("member")
    rs.materialize_layout(case, state_variant="old", outputs="old", provenance="full", backups="full")
    before = rs.snapshot_recovery_resources(case)
    key = case["keys"][0]
    expected_members = copy.deepcopy(case["provenance"][key]["members"])
    (case["root"] / case["provenance"][key]["dir"] / "sources.json").write_bytes(b"changed")
    after = rs.snapshot_recovery_resources(case)
    assert after != before
    with pytest.raises(AssertionError, match="recovery resources changed"):
        rs.assert_recovery_preserved(before, after)
    assert case["provenance"][key]["members"] == expected_members

    # backup mutation
    case = fresh("backup")
    rs.materialize_layout(case, state_variant="old", outputs="old", provenance="full", backups="full")
    before = rs.snapshot_recovery_resources(case)
    key = case["keys"][0]
    (case["root"] / case["backups"][key]["path"]).write_bytes(b"changed")
    after = rs.snapshot_recovery_resources(case)
    assert after != before
    with pytest.raises(AssertionError, match="recovery resources changed"):
        rs.assert_recovery_preserved(before, after)


def test_c1_g12_no_change_assertions_detect_mutations(tmp_path, monkeypatch):
    case = rs.make_recovery_case(tmp_path, monkeypatch)
    rs.materialize_layout(case, state_variant="old", outputs="old", provenance="full", backups="full")
    before = rs.snapshot_recovery_resources(case)
    assert rs.snapshot_recovery_resources(case) == before
    key = case["keys"][0]
    (case["root"] / case["registry"][key]["output"]).write_bytes(b"x")
    assert rs.snapshot_recovery_resources(case) != before
    rs.materialize_layout(case, state_variant="old", outputs="old", provenance="full", backups="full")
    (case["root"] / case["registry"][key]["output"]).unlink()
    assert rs.snapshot_recovery_resources(case) != before
    rs.materialize_layout(case, state_variant="old", outputs="old", provenance="full", backups="full")
    sentinel = case["root"] / "reports/incremental/staging" / case["txid"] / case["token"] / "sentinel.txt"
    sentinel.parent.mkdir(parents=True, exist_ok=True)
    sentinel.write_bytes(b"sentinel")
    assert rs.snapshot_recovery_resources(case) != before
    rs.materialize_layout(case, state_variant="old", outputs="old", provenance="full", backups="full")
    assert rs.snapshot_recovery_resources(case) == before


def test_c1_g13_build_and_projection_residual_negatives(tmp_path, monkeypatch):
    case = rs.make_recovery_case(tmp_path, monkeypatch)
    state = json.loads(case["new_state_bytes"])
    for key in case["scope"]:
        assert state["build"]["pages"][key]["output"]["sha256"] == support.sha256_bytes(case["new_outputs"][key])

    mutated = copy.deepcopy(state)
    mutated["build"]["built_at"] = "2026-10-15T01:00:00+00:00"
    case["new_state_bytes"] = json.dumps(mutated, ensure_ascii=False).encode("utf-8")
    assert rs.state_digest(mutated) != case["journal"]["state"]["new_sha256"]
    with pytest.raises(AssertionError, match="new state digest mismatch"):
        rs.validate_journal_projection(case)
    case["new_state_bytes"] = rs._dump_state(state)

    bad_members = copy.deepcopy(case["journal"])
    bad_members["pages"][0]["provenance"]["members"] = []
    with pytest.raises(AssertionError, match="provenance members mismatch"):
        rs.validate_journal_projection(case, bad_members)

    bad_bool = copy.deepcopy(case["journal"])
    bad_bool["pages"][0]["output"]["old_exists"] = 1
    with pytest.raises(AssertionError, match="old_exists type mismatch"):
        rs.validate_journal_projection(case, bad_bool)

    bad_generation = copy.deepcopy(state)
    bad_generation["build"]["pages"][case["scope"][0]]["generation"] = "other"
    with pytest.raises(AssertionError, match="build page generation mismatch"):
        rs.validate_state_build(case, bad_generation)

    bad_token = copy.deepcopy(case["journal"])
    bad_token["execution_token"] = "unbound-token"
    with pytest.raises(AssertionError, match="transaction_id mismatch"):
        rs.validate_journal_projection(case, bad_token)


def test_c1_g14_two_token_materials_both_valid(tmp_path, monkeypatch):
    case = rs.make_recovery_case(tmp_path, monkeypatch)
    assert rs.validate_journal_projection(case)
    for token in ("token-a", "token-b"):
        variant = rs.clone_with_token(case, token)
        assert rs.validate_journal_projection(variant)
    first = rs.clone_with_token(case, "token-a")
    second = rs.clone_with_token(case, "token-b")
    assert first["request_id"] == second["request_id"]
    assert first["txid"] != second["txid"]
    assert first["journal"]["staging_root"] != second["journal"]["staging_root"]
    invalid = copy.deepcopy(case["journal"])
    invalid["execution_token"] = "unbound-token"
    with pytest.raises(AssertionError):
        rs.validate_journal_projection(case, invalid)


@pytest.mark.parametrize("attack", ["owner_root", "sibling_token", "output_redirect"])
def test_c1_g15_resealed_journal_ownership(tmp_path, monkeypatch, attack):
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

    assert journal["request_id"] == support.sha256_bytes(support.C(journal["request_payload"]))
    assert journal["transaction_id"] == support.expected_transaction_id(
        journal["request_id"], journal["execution_token"]
    )
    with pytest.raises(AssertionError) as caught:
        rs.validate_journal_projection(candidate, journal)
    assert str(caught.value) == expected
    rs.assert_recovery_preserved(before, rs.snapshot_recovery_resources(case))

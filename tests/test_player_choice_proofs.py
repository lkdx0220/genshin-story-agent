# -*- coding: utf-8 -*-
"""F01: 受控 choice-proofs 回归。"""

import hashlib

import pytest

from app.choice_proofs import ChoiceProofError, generate_fixture_choice_proofs, validate_choice_bindings


def _sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _bundle():
    source_hash = _sha("raw")
    return {
        "mode": "fixture",
        "manifest": {"data_mode": "fixture"},
        "sources": [{"source_id": "source:fixture", "snapshot_sha256": source_hash}],
        "paragraphs": [
            {
                "span_id": "span:anchor",
                "source_id": "source:fixture",
                "quote": "【玩家旅行者=空】",
                "quote_sha256": _sha("【玩家旅行者=空】"),
            },
            {
                "span_id": "span:fact",
                "source_id": "source:fixture",
                "quote": "空使用风元素。",
                "quote_sha256": _sha("空使用风元素。"),
            },
        ],
    }


def test_fixture_choice_proofs_positive_and_reject_wrong_coverage():
    bundle = _bundle()
    proofs = generate_fixture_choice_proofs(bundle)
    assert proofs and proofs[0]["scope"] == ["aether"]
    record = {
        "player_choice_scope": ["aether"],
        "player_choice_branch": {"choice_id": "aether", "kind": "source_metadata", "source_span_id": "span:anchor"},
        "player_choice_proof_refs": [proofs[0]["proof_id"]],
        "evidence": [{"source_span_id": "span:fact"}],
    }
    validate_choice_bindings({"events": [record]}, proofs)
    record["evidence"] = [{"source_span_id": "span:missing"}]
    with pytest.raises(ChoiceProofError):
        validate_choice_bindings({"events": [record]}, proofs)


def test_multiple_proofs_same_scope_cover_different_evidence():
    bundles = _bundle()
    proofs = generate_fixture_choice_proofs(bundles)
    proof = proofs[0]
    second = dict(proof)
    second["proof_id"] = "proof:fixture-aether-002"
    second["anchor_span_id"] = "span:fact"
    second["covered_span_ids"] = ["span:fact"]
    record = {
        "player_choice_scope": ["aether"],
        "player_choice_branch": {"choice_id": "aether", "kind": "source_metadata", "source_span_id": "span:anchor"},
        "player_choice_proof_refs": [proof["proof_id"], second["proof_id"]],
        "evidence": [{"source_span_id": "span:fact"}],
    }
    validate_choice_bindings({"events": [record]}, [proof, second])


def test_conflict_refs_must_equal_alternative_union():
    proofs = generate_fixture_choice_proofs(_bundle())
    proof = proofs[0]
    data = {
        "conflicts": [
            {
                "conflict_id": "conflict:c1",
                "player_choice_proof_refs": [],
                "alternatives": [{"alternative_id": "alt:a", "player_choice_proof_refs": [proof["proof_id"]]}],
            }
        ]
    }
    with pytest.raises(ChoiceProofError):
        validate_choice_bindings(data, [proof])

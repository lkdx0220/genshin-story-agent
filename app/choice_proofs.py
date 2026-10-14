# -*- coding: utf-8 -*-
"""F01 受控 choice-proofs 证明目录。"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

PROOF_ID_RE = re.compile(r"^proof:[a-z0-9][a-z0-9_-]*$")
MARKER_TO_SCOPE = {
    "【玩家旅行者=空】": ["aether"],
    "【玩家旅行者=荧】": ["lumine"],
    "【玩家旅行者=共同】": [],
}


class ChoiceProofError(ValueError):
    pass


def _sha_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _sources_by_id(bundle: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {item.get("source_id"): item for item in bundle.get("sources") or []}


def _paragraphs(bundle: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {item.get("span_id"): item for item in bundle.get("paragraphs") or []}


def generate_fixture_choice_proofs(bundle: Dict[str, Any]) -> List[Dict[str, Any]]:
    if bundle.get("mode") != "fixture":
        raise ChoiceProofError("fixture-choice-marker-v1 only supports fixture mode")
    paragraphs = list(bundle.get("paragraphs") or [])
    sources = _sources_by_id(bundle)
    proofs: List[Dict[str, Any]] = []
    current: Optional[Dict[str, Any]] = None
    current_source = None
    for paragraph in paragraphs:
        if paragraph.get("source_id") != current_source:
            if current:
                proofs.append(current)
                current = None
            current_source = paragraph.get("source_id")
        quote = paragraph.get("quote") or ""
        if quote in MARKER_TO_SCOPE:
            if current:
                proofs.append(current)
            current = {
                "proof_id": "proof:fixture-" + str(paragraph.get("span_id", "")).replace(":", "-"),
                "scope": list(MARKER_TO_SCOPE[quote]),
                "source_id": paragraph.get("source_id"),
                "source_sha256": (sources.get(paragraph.get("source_id")) or {}).get("snapshot_sha256"),
                "anchor_span_id": paragraph.get("span_id"),
                "anchor_quote_sha256": paragraph.get("quote_sha256") or _sha_text(quote),
                "covered_span_ids": [],
                "rule_id": "fixture-choice-marker-v1",
            }
            continue
        if current is not None:
            current["covered_span_ids"].append(paragraph.get("span_id"))
    if current:
        proofs.append(current)
    return proofs


def _load_external_proofs(source_dir: Path, manifest: Dict[str, Any]) -> List[Dict[str, Any]]:
    rel = manifest.get("choice_proofs_file")
    root = Path(source_dir).resolve()
    path = (root / str(rel)).resolve()
    if root not in path.parents and path != root:
        raise ChoiceProofError("choice_proofs_file escapes source_dir")
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ChoiceProofError("choice_proofs_file unreadable: " + str(exc)) from exc
    expected_sha = manifest.get("choice_proofs_sha256")
    if not expected_sha:
        raise ChoiceProofError("choice entity requires manifest.choice_proofs_sha256")
    if hashlib.sha256(raw).hexdigest() != expected_sha:
        raise ChoiceProofError("choice_proofs_sha256 mismatch")
    try:
        data = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise ChoiceProofError("choice_proofs JSON invalid") from exc
    if not isinstance(data, dict) or data.get("format_version") != 1:
        raise ChoiceProofError("choice_proofs format_version must be 1")
    proofs = data.get("proofs")
    if not isinstance(proofs, list):
        raise ChoiceProofError("choice proofs must be an array")
    return proofs


def _check_fixture_proofs(proofs: List[Dict[str, Any]], bundle: Dict[str, Any]) -> None:
    if not any(proof.get("rule_id") == "fixture-choice-marker-v1" for proof in proofs):
        return
    if bundle.get("mode") != "fixture":
        raise ChoiceProofError("fixture choice proof rule is fixture-only")
    if any(source.get("provider") != "user_fixture" for source in bundle.get("sources") or []):
        raise ChoiceProofError("fixture choice proof rule requires user_fixture provider")
    if _proof_signature(generate_fixture_choice_proofs(bundle)) != _proof_signature(proofs):
        raise ChoiceProofError("choice_proofs does not match raw marker coverage")


def load_choice_proofs(source_dir: Path, bundle: Dict[str, Any], required: bool = False) -> List[Dict[str, Any]]:
    manifest = bundle.get("manifest") or {}
    rel = manifest.get("choice_proofs_file")
    if not rel:
        if required:
            raise ChoiceProofError("choice entity requires manifest.choice_proofs_file")
        proofs = generate_fixture_choice_proofs(bundle) if bundle.get("mode") == "fixture" else []
    else:
        proofs = _load_external_proofs(source_dir, manifest)
    _validate_proof_structure(proofs, bundle)
    _check_fixture_proofs(proofs, bundle)
    return proofs


def _proof_signature(proofs: List[Dict[str, Any]]):
    return sorted(
        (
            proof.get("proof_id"),
            tuple(proof.get("scope") or []),
            proof.get("source_id"),
            proof.get("source_sha256"),
            proof.get("anchor_span_id"),
            proof.get("anchor_quote_sha256"),
            tuple(proof.get("covered_span_ids") or []),
            proof.get("rule_id"),
        )
        for proof in proofs
        if isinstance(proof, dict)
    )


def _validate_proof_entry(
    proof: Dict[str, Any], sources: Dict[str, Any], paragraphs: Dict[str, Any], seen: set
) -> None:
    proof_id = proof.get("proof_id")
    if not isinstance(proof_id, str) or not PROOF_ID_RE.fullmatch(proof_id) or proof_id in seen:
        raise ChoiceProofError("bad proof_id")
    seen.add(proof_id)
    scope = proof.get("scope")
    if scope not in ([], ["aether"], ["lumine"]):
        raise ChoiceProofError("bad proof scope")
    source_id = str(proof.get("source_id") or "")
    source = sources.get(source_id)
    if source is None or source.get("snapshot_sha256") != proof.get("source_sha256"):
        raise ChoiceProofError("proof source hash mismatch")
    anchor = paragraphs.get(str(proof.get("anchor_span_id") or ""))
    if anchor is None or anchor.get("source_id") != proof.get("source_id"):
        raise ChoiceProofError("proof anchor span unresolved")
    if _sha_text(anchor.get("quote") or "") != proof.get("anchor_quote_sha256"):
        raise ChoiceProofError("proof anchor quote hash mismatch")
    covered = proof.get("covered_span_ids")
    if not isinstance(covered, list) or not covered or len(set(covered)) != len(covered):
        raise ChoiceProofError("proof covered_span_ids invalid")
    for span_id in covered:
        span = paragraphs.get(span_id)
        if span is None or span.get("source_id") != proof.get("source_id"):
            raise ChoiceProofError("proof covered span unresolved: " + str(span_id))


def _validate_proof_structure(proofs: List[Dict[str, Any]], bundle: Dict[str, Any]) -> None:
    sources = _sources_by_id(bundle)
    paragraphs = _paragraphs(bundle)
    seen = set()
    for proof in proofs:
        if not isinstance(proof, dict):
            raise ChoiceProofError("choice proof must be an object")
        _validate_proof_entry(proof, sources, paragraphs, seen)
    if any(proof.get("rule_id") != "fixture-choice-marker-v1" for proof in proofs):
        raise ChoiceProofError("unsupported choice proof rule")


def validate_choice_bindings(data: Dict[str, Any], proofs: List[Dict[str, Any]]) -> None:
    proofs_by_id = {item["proof_id"]: item for item in proofs}
    for obj in _descendants(data):
        if "player_choice_scope" not in obj:
            continue
        scope = obj.get("player_choice_scope")
        if scope not in ([], ["aether"], ["lumine"]):
            raise ChoiceProofError("invalid player_choice_scope")
        refs = obj.get("player_choice_proof_refs")
        if not isinstance(refs, list) or len(set(refs)) != len(refs):
            raise ChoiceProofError("player_choice_proof_refs required")
        selected = [proofs_by_id.get(ref) for ref in refs]
        if any(item is None for item in selected):
            raise ChoiceProofError("player_choice_proof_refs references unknown proof")
        if scope:
            _validate_scoped_record(obj, refs, selected)
        else:
            _validate_common_record(obj, selected)
    _validate_conflict_refs(data, proofs_by_id)


def _validate_scoped_record(obj, refs, selected):
    if not refs:
        raise ChoiceProofError("scoped record needs proof refs")
    branch = obj.get("player_choice_branch")
    if not isinstance(branch, dict) or branch.get("kind") != "source_metadata":
        raise ChoiceProofError("scoped record needs source_metadata branch")
    current_scope = obj.get("player_choice_scope")
    if any(item.get("scope") not in ([], current_scope) for item in selected):
        raise ChoiceProofError("incompatible proof scope referenced")
    matching_scope = [item for item in selected if item.get("scope") in ([], current_scope)]
    if not matching_scope:
        raise ChoiceProofError("branch scope not covered by proof")
    if not any(item.get("anchor_span_id") == branch.get("source_span_id") for item in matching_scope):
        raise ChoiceProofError("branch anchor not covered by proof")
    evidence_spans = {e.get("source_span_id") for e in obj.get("evidence") or []}
    covered = set()
    for item in matching_scope:
        covered.update(item.get("covered_span_ids") or [])
    if evidence_spans - covered:
        raise ChoiceProofError("record evidence not covered by choice proof")


def _validate_common_record(obj, selected):
    if not obj.get("evidence"):
        return
    common = [item for item in selected if item.get("scope") == []]
    if not common:
        raise ChoiceProofError("common record needs common proof")
    covered = set()
    for item in common:
        covered.update(item.get("covered_span_ids") or [])
    if {e.get("source_span_id") for e in obj.get("evidence") or []} - covered:
        raise ChoiceProofError("common evidence not covered by common proof")


def _validate_conflict_refs(data: Dict[str, Any], proofs_by_id: Dict[str, Any]) -> None:
    conflicts = data.get("conflicts") or ([data] if data.get("conflict_id") else [])
    for conflict in conflicts:
        parent_refs = set(conflict.get("player_choice_proof_refs") or [])
        child_refs = set()
        for alternative in conflict.get("alternatives") or []:
            child_refs.update(alternative.get("player_choice_proof_refs") or [])
        if not conflict.get("evidence") and parent_refs != child_refs:
            raise ChoiceProofError("conflict refs must equal union of alternative refs")
        for ref in parent_refs | child_refs:
            if ref not in proofs_by_id:
                raise ChoiceProofError("conflict proof ref unknown: " + str(ref))


def _descendants(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _descendants(child)
    elif isinstance(value, list):
        for child in value:
            yield from _descendants(child)

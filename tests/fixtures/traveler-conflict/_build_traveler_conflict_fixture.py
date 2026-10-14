# -*- coding: utf-8 -*-
"""Build the traveler conflict fixture for strict narrowing acceptance."""

import hashlib
import json
from pathlib import Path

import ingest

ROOT = Path(__file__).resolve().parents[3]
RAW = ROOT / "tests" / "fixtures" / "traveler-conflict" / "raw"
RESPONSE = ROOT / "tests" / "fixtures" / "traveler-conflict-response.md"
SOURCE_RESPONSE = ROOT / "tests" / "fixtures" / "traveler-response.md"

CASE = """【玩家旅行者=共同】

旅行者共同使用岩元素。

【玩家旅行者=空】

空使用风元素。

【玩家旅行者=荧】

荧使用岩元素。
"""


def sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_raw():
    RAW.mkdir(parents=True, exist_ok=True)
    case = RAW / "case.txt"
    case.write_text(CASE, encoding="utf-8", newline="\n")
    manifest = {
        "data_mode": "fixture",
        "sources": [
            {
                "source_id": "source:traveler-conflict",
                "file": "case.txt",
                "sha256": sha_bytes(case.read_bytes()),
                "provider": "user_fixture",
                "title": "B3 traveler conflict fixture",
                "url": "https://example.invalid/traveler-conflict",
                "entry_ids": [],
                "revision_id": None,
                "game_version": None,
                "paragraphs": {
                    "p001": {"basis": "explicit_text", "authority": "non_authoritative", "game_version": None},
                    "p002": {"basis": "explicit_text", "authority": "non_authoritative", "game_version": "5.0"},
                    "p003": {"basis": "explicit_text", "authority": "non_authoritative", "game_version": None},
                    "p004": {"basis": "explicit_text", "authority": "non_authoritative", "game_version": "5.0"},
                    "p005": {"basis": "explicit_text", "authority": "non_authoritative", "game_version": None},
                    "p006": {"basis": "explicit_text", "authority": "non_authoritative", "game_version": "5.0"},
                },
            }
        ],
    }
    (RAW / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


def write_proofs():
    bundle = ingest.load_bundle(RAW)
    from app.choice_proofs import generate_fixture_choice_proofs

    proofs = generate_fixture_choice_proofs(bundle)
    payload = json.dumps({"format_version": 1, "proofs": proofs}, ensure_ascii=False, indent=2).encode("utf-8")
    (RAW / "choice-proofs.json").write_bytes(payload)
    manifest = json.loads((RAW / "manifest.json").read_text(encoding="utf-8"))
    manifest["choice_proofs_file"] = "choice-proofs.json"
    manifest["choice_proofs_sha256"] = sha_bytes(payload)
    (RAW / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return bundle, proofs


def proof_id(proofs, scope):
    for proof in proofs:
        if proof["scope"] == scope:
            return proof["proof_id"], proof["anchor_span_id"]
    raise SystemExit("missing proof " + str(scope))


def remap(data, bundle, proofs):
    paragraphs = bundle["paragraphs"]
    common_span = paragraphs[1]["span_id"]
    aether_span = paragraphs[3]["span_id"]
    lumine_span = paragraphs[5]["span_id"]
    fact_span = {(): common_span, ("aether",): aether_span, ("lumine",): lumine_span}
    common_proof, common_anchor = proof_id(proofs, [])
    aether_proof, aether_anchor = proof_id(proofs, ["aether"])
    lumine_proof, lumine_anchor = proof_id(proofs, ["lumine"])
    anchors = {(): common_anchor, ("aether",): aether_anchor, ("lumine",): lumine_anchor}
    refs = {(): common_proof, ("aether",): aether_proof, ("lumine",): lumine_proof}

    def scope_key(obj):
        scope = obj.get("player_choice_scope")
        return tuple(scope or [])

    def walk(obj):
        if isinstance(obj, dict):
            key = scope_key(obj)
            if "evidence" in obj and isinstance(obj["evidence"], list):
                for evidence in obj["evidence"]:
                    if isinstance(evidence, dict):
                        evidence["source_span_id"] = fact_span.get(key, common_span)
            if "player_choice_branch" in obj and isinstance(obj["player_choice_branch"], dict):
                obj["player_choice_branch"]["source_span_id"] = anchors.get(key, common_anchor)
            if "player_choice_proof_refs" in obj and isinstance(obj["player_choice_proof_refs"], list):
                obj["player_choice_proof_refs"] = [refs.get(key, common_proof)]
            for value in obj.values():
                walk(value)
        elif isinstance(obj, list):
            for value in obj:
                walk(value)

    walk(data)
    return common_span, aether_span, lumine_span, common_proof, aether_proof, common_anchor, aether_anchor


def build_response(bundle, proofs):
    data, _ = ingest.parse_page(SOURCE_RESPONSE.read_text(encoding="utf-8"))
    common_span, aether_span, lumine_span, common_proof, aether_proof, common_anchor, aether_anchor = remap(
        data, bundle, proofs
    )
    version = {"introduced_in": None, "valid_from": None, "valid_to": None, "verified_in": []}
    unknown_scope = {
        "label": None,
        "from_event_id": None,
        "from_boundary": "unknown",
        "to_event_id": None,
        "to_boundary": "unknown",
    }
    common_evidence = [{"source_span_id": common_span, "basis": "explicit_text", "authority": "non_authoritative"}]
    aether_evidence = [{"source_span_id": aether_span, "basis": "explicit_text", "authority": "non_authoritative"}]
    target = {
        "attribute_id": "attr:traveler-element",
        "key": "element",
        "value": "geo",
        "value_label": None,
        "identity_version_ids": ["identity:traveler"],
        "story_scope": unknown_scope,
        "version": version,
        "status": "candidate",
        "evidence": common_evidence,
        "conflict_ids": ["conflict:element"],
        "player_choice_scope": [],
        "player_choice_proof_refs": [common_proof],
        "observed_at": [],
    }
    branch_record = {
        "attribute_id": "attr:traveler-anemo",
        "key": "element",
        "value": "anemo",
        "value_label": None,
        "identity_version_ids": ["identity:traveler"],
        "story_scope": unknown_scope,
        "version": version,
        "status": "candidate",
        "evidence": aether_evidence,
        "conflict_ids": ["conflict:element"],
        "player_choice_scope": ["aether"],
        "player_choice_branch": {"choice_id": "aether", "kind": "source_metadata", "source_span_id": aether_anchor},
        "player_choice_proof_refs": [aether_proof],
        "observed_at": [],
    }
    narrowed = dict(target)
    narrowed.update(
        player_choice_scope=["aether"],
        player_choice_branch={"choice_id": "aether", "kind": "source_metadata", "source_span_id": common_anchor},
        player_choice_proof_refs=[common_proof],
    )
    conflict = {
        "conflict_id": "conflict:element",
        "kind": "source_disagreement",
        "target_ref": "attribute:attr:traveler-element",
        "summary": "共同岩元素原值与空的风元素条件值并存",
        "status": "open",
        "player_choice_scope": ["aether"],
        "player_choice_branch": {"choice_id": "aether", "kind": "source_metadata", "source_span_id": aether_anchor},
        "player_choice_proof_refs": [common_proof, aether_proof],
        "alternatives": [
            {
                "alternative_id": "alt-original",
                "statement": "共同原值在空分支下保持岩元素",
                "proposed_value": narrowed,
                "version": version,
                "evidence": common_evidence,
                "player_choice_scope": ["aether"],
                "player_choice_branch": {
                    "choice_id": "aether",
                    "kind": "source_metadata",
                    "source_span_id": common_anchor,
                },
                "player_choice_proof_refs": [common_proof],
            },
            {
                "alternative_id": "alt-new",
                "statement": "空使用风元素",
                "proposed_value": dict(branch_record, attribute_id="attr:traveler-element"),
                "version": version,
                "evidence": aether_evidence,
                "player_choice_scope": ["aether"],
                "player_choice_branch": {
                    "choice_id": "aether",
                    "kind": "source_metadata",
                    "source_span_id": aether_anchor,
                },
                "player_choice_proof_refs": [aether_proof],
            },
        ],
        "resolution": None,
    }
    affiliation_state = {
        "state_id": "affiliation-state:traveler-affiliation",
        "identity_version_ids": ["identity:traveler"],
        "state": "affiliated",
        "organization_ids": ["org:traveler"],
        "coverage": {
            "extent": "named_organizations",
            "organization_ids": ["org:traveler"],
            "source_ids": ["source:traveler-conflict"],
            "reviewed_sources": 1,
            "authoritative_sources": 0,
            "completeness": "partial",
            "limitations": None,
        },
        "observed_at": [
            {
                "game_version": "5.0",
                "story_event_id": None,
                "position": "unknown",
                "identity_version_id": "identity:traveler",
                "player_choice": None,
            }
        ],
        "version": version,
        "story_scope": unknown_scope,
        "status": "candidate",
        "evidence": common_evidence,
        "conflict_ids": [],
        "player_choice_scope": [],
        "player_choice_proof_refs": [common_proof],
    }
    data["attribute_records"] = [target, branch_record]
    data["affiliation_states"] = [affiliation_state]
    data["affiliation"] = ["org:traveler"]
    data["conflicts"] = [conflict]
    data["element"] = None
    for index, assertion in enumerate(data.get("field_assertions") or []):
        if assertion.get("path") == "/element":
            data["field_assertions"][index] = {
                "path": "/element",
                "status": "disputed",
                "evidence": [],
                "conflict_ids": ["conflict:element"],
            }
        if assertion.get("path") == "/affiliation":
            data["field_assertions"][index] = {
                "path": "/affiliation",
                "status": "candidate",
                "evidence": common_evidence,
                "conflict_ids": [],
            }
    body = ingest.render_body(data, bundle)
    text = "---\n" + json.dumps(data, ensure_ascii=False, indent=2) + "\n---\n" + body
    RESPONSE.write_text(text, encoding="utf-8", newline="\n")
    return data


def main():
    write_raw()
    bundle, proofs = write_proofs()
    bundle = ingest.load_bundle(RAW)
    data = build_response(bundle, proofs)
    try:
        ingest.schema_check(data, ROOT / "wiki" / "__schema__" / "entity-template.md", bundle)
        print("schema_check OK")
    except Exception as exc:
        print("schema_check FAILED:", type(exc).__name__, str(exc))
        raise
    print("response written", RESPONSE)


if __name__ == "__main__":
    main()

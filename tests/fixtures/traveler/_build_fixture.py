# -*- coding: utf-8 -*-
"""Build a deterministic traveler fixture for B3 acceptance."""
import hashlib
import json
from pathlib import Path

import ingest

ROOT = Path(__file__).resolve().parents[3]
RAW = ROOT / "tests" / "fixtures" / "traveler" / "raw"
RESPONSE = ROOT / "tests" / "fixtures" / "traveler-response.md"


def sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


CASE = """【玩家旅行者=共同】

旅行者是在提瓦特旅行的主角。

【玩家旅行者=空】

空使用风元素。

【玩家旅行者=荧】

荧使用岩元素。
"""


def build_raw():
    RAW.mkdir(parents=True, exist_ok=True)
    case_path = RAW / "case.txt"
    case_path.write_text(CASE, encoding="utf-8", newline="\n")
    digest = sha_bytes(case_path.read_bytes())
    manifest = {
        "data_mode": "fixture",
        "sources": [
            {
                "source_id": "source:traveler-fixture",
                "file": "case.txt",
                "sha256": digest,
                "provider": "user_fixture",
                "title": "B3 traveler fixture",
                "url": "https://example.invalid/traveler",
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
    return manifest


def build_proofs():
    bundle = ingest.load_bundle(RAW)
    from app.choice_proofs import generate_fixture_choice_proofs

    proofs = generate_fixture_choice_proofs(bundle)
    data = {"format_version": 1, "proofs": proofs}
    raw = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
    path = RAW / "choice-proofs.json"
    path.write_bytes(raw)
    manifest = json.loads((RAW / "manifest.json").read_text(encoding="utf-8"))
    manifest["choice_proofs_file"] = "choice-proofs.json"
    manifest["choice_proofs_sha256"] = hashlib.sha256(raw).hexdigest()
    (RAW / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return proofs


def proof_for(proofs, scope):
    for proof in proofs:
        if proof["scope"] == scope:
            return proof
    raise SystemExit("missing proof for scope " + str(scope))


def ev(span_id):
    return [{"source_span_id": span_id, "basis": "explicit_text", "authority": "non_authoritative"}]


def build_response(proofs):
    bundle = ingest.load_bundle(RAW)
    paragraphs = bundle["paragraphs"]
    common_span = paragraphs[1]["span_id"]
    aether_span = paragraphs[3]["span_id"]
    lumine_span = paragraphs[5]["span_id"]
    common_proof = proof_for(proofs, [])
    aether_proof = proof_for(proofs, ["aether"])
    lumine_proof = proof_for(proofs, ["lumine"])
    version = {"introduced_in": None, "valid_from": None, "valid_to": None, "verified_in": []}
    unknown_scope = {"label": None, "from_event_id": None, "from_boundary": "unknown", "to_event_id": None, "to_boundary": "unknown"}
    data = {
        "schema_version": "0.2.0-draft",
        "entity_id": "char:traveler",
        "entity_type": "character",
        "data_mode": "fixture",
        "page_status": "draft",
        "as_of_version": None,
        "snapshot": {"game_version": "5.0", "story_event_id": None, "position": "unknown", "identity_version_id": "identity:traveler"},
        "canonical_name": "旅行者",
        "aliases": ["旅行者"],
        "element": None,
        "weapon": None,
        "nation": None,
        "affiliation": [],
        "player_choice_options": [
            {"choice_id": "aether", "label": "空", "aliases": ["空", "哥哥"]},
            {"choice_id": "lumine", "label": "荧", "aliases": ["荧", "妹妹"]},
        ],
        "field_assertions": [
            {"path": "/canonical_name", "status": "candidate", "evidence": ev(common_span), "conflict_ids": []},
            {"path": "/element", "status": "unknown", "evidence": [], "conflict_ids": []},
            {"path": "/weapon", "status": "unknown", "evidence": [], "conflict_ids": []},
            {"path": "/nation", "status": "unknown", "evidence": [], "conflict_ids": []},
            {"path": "/affiliation", "status": "unknown", "evidence": [], "conflict_ids": []},
            {"path": "/aliases/0", "status": "candidate", "evidence": ev(common_span), "conflict_ids": []},
        ],
        "identity_versions": [
            {
                "identity_version_id": "identity:traveler",
                "label": "旅行者",
                "predecessor_ids": [],
                "transition_event_id": None,
                "version": version,
                "story_scope": unknown_scope,
                "status": "candidate",
                "evidence": ev(common_span),
                "observed_at": [],
                "conflict_ids": [],
                "names": [
                    {"name_id": "name:traveler", "value": "旅行者", "kind": "primary", "version": version, "story_scope": unknown_scope, "status": "candidate", "evidence": ev(common_span), "observed_at": [], "conflict_ids": [], "player_choice_scope": [], "player_choice_proof_refs": [common_proof["proof_id"]]},
                    {"name_id": "name:aether", "value": "空", "kind": "alias", "version": version, "story_scope": unknown_scope, "status": "candidate", "evidence": ev(aether_span), "observed_at": [], "conflict_ids": [], "player_choice_scope": ["aether"], "player_choice_branch": {"choice_id": "aether", "kind": "source_metadata", "source_span_id": aether_proof["anchor_span_id"]}, "player_choice_proof_refs": [aether_proof["proof_id"]]},
                    {"name_id": "name:lumine", "value": "荧", "kind": "alias", "version": version, "story_scope": unknown_scope, "status": "candidate", "evidence": ev(lumine_span), "observed_at": [], "conflict_ids": [], "player_choice_scope": ["lumine"], "player_choice_branch": {"choice_id": "lumine", "kind": "source_metadata", "source_span_id": lumine_proof["anchor_span_id"]}, "player_choice_proof_refs": [lumine_proof["proof_id"]]},
                ],
            }
        ],
        "events": [
            {"event_id": "event:common", "event_entity_id": None, "title": "共同旅行", "description": "旅行者是在提瓦特旅行的主角。", "role": "participant", "identity_version_ids": ["identity:traveler"], "version": dict(version, introduced_in="5.0"), "story_time": {"label": None, "after_event_ids": [], "before_event_ids": []}, "status": "candidate", "evidence": ev(common_span), "conflict_ids": [], "player_choice_scope": [], "player_choice_proof_refs": [common_proof["proof_id"]]},
            {"event_id": "event:aether", "event_entity_id": None, "title": "空的风", "description": "空使用风元素。", "role": "participant", "identity_version_ids": ["identity:traveler"], "version": dict(version, introduced_in="5.0"), "story_time": {"label": None, "after_event_ids": [], "before_event_ids": []}, "status": "candidate", "evidence": ev(aether_span), "conflict_ids": [], "player_choice_scope": ["aether"], "player_choice_branch": {"choice_id": "aether", "kind": "source_metadata", "source_span_id": aether_proof["anchor_span_id"]}, "player_choice_proof_refs": [aether_proof["proof_id"]]},
            {"event_id": "event:lumine", "event_entity_id": None, "title": "荧的岩", "description": "荧使用岩元素。", "role": "participant", "identity_version_ids": ["identity:traveler"], "version": dict(version, introduced_in="5.0"), "story_time": {"label": None, "after_event_ids": [], "before_event_ids": []}, "status": "candidate", "evidence": ev(lumine_span), "conflict_ids": [], "player_choice_scope": ["lumine"], "player_choice_branch": {"choice_id": "lumine", "kind": "source_metadata", "source_span_id": lumine_proof["anchor_span_id"]}, "player_choice_proof_refs": [lumine_proof["proof_id"]]},
        ],
        "relations": [],
        "memory_records": [],
        "affiliation_states": [],
        "conflicts": [],
        "test_refs": [],
    }
    body = ingest.render_body(data, bundle)
    text = "---\n" + json.dumps(data, ensure_ascii=False, indent=2) + "\n---\n" + body
    RESPONSE.write_text(text, encoding="utf-8", newline="\n")
    return data, bundle


def main():
    build_raw()
    proofs = build_proofs()
    data, bundle = build_response(proofs)
    try:
        ingest.schema_check(data, ROOT / "wiki" / "__schema__" / "entity-template.md", bundle)
        print("schema_check OK")
    except Exception as exc:
        print("schema_check FAILED:", type(exc).__name__, str(exc))
        raise
    print("response written", RESPONSE)


if __name__ == "__main__":
    main()

# -*- coding: utf-8 -*-
"""Wave1 通用性回归（change 010）：版本标签、谓词、冲突指针、归属投影。"""

import hashlib
import json
from pathlib import Path

import pytest

import ingest
import wiki_lint


def _write_bundle(tmp_path: Path, text: str, paragraph_meta: dict):
    case = tmp_path / "case.txt"
    case.write_bytes(text.encode("utf-8"))
    manifest = {
        "data_mode": "fixture",
        "sources": [
            {
                "source_id": "source:test-month3",
                "file": "case.txt",
                "sha256": hashlib.sha256(case.read_bytes()).hexdigest(),
                "provider": "user_fixture",
                "title": "Wave1 generality test",
                "url": "https://example.invalid/test",
                "revision_id": None,
                "game_version": None,
                "paragraphs": {"p001": paragraph_meta},
            }
        ],
    }
    (tmp_path / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    return ingest.load_bundle(tmp_path)


def test_non_numeric_version_label_loads_and_passes_l5(tmp_path):
    bundle = _write_bundle(
        tmp_path,
        "月之三测试原文。",
        {
            "game_version": None,
            "game_version_label": "月之三",
            "basis": "explicit_text",
            "authority": "non_authoritative",
        },
    )
    span_id = bundle["paragraphs"][0]["span_id"]
    data = {
        "events": [
            {
                "event_id": "event:test",
                "version": {
                    "introduced_in": None,
                    "label": "月之三",
                    "valid_from": None,
                    "valid_to": None,
                    "verified_in": [],
                },
                "evidence": [{"source_span_id": span_id}],
            }
        ]
    }
    body = "## 时间线\n- [版本 月之三] 测试事件。\n"
    wiki_lint._check_l5(
        {
            "id": "L5",
            "events_path": "events",
            "version_path": "version.introduced_in",
            "label_path": "version.label",
            "body_marker_template": "[版本 {version}]",
            "require_direct_cited_paragraph_game_version_match": True,
        },
        data,
        body,
        bundle,
    )


def test_numeric_missing_label_but_bad_cited_label_is_rejected(tmp_path):
    bundle = _write_bundle(
        tmp_path,
        "月之四测试原文。",
        {
            "game_version": None,
            "game_version_label": "月之四",
            "basis": "explicit_text",
            "authority": "non_authoritative",
        },
    )
    span_id = bundle["paragraphs"][0]["span_id"]
    data = {
        "events": [
            {
                "event_id": "event:test",
                "version": {
                    "introduced_in": None,
                    "label": "月之三",
                    "valid_from": None,
                    "valid_to": None,
                    "verified_in": [],
                },
                "evidence": [{"source_span_id": span_id}],
            }
        ]
    }
    with pytest.raises(ValueError) as exc:
        wiki_lint._check_l5(
            {
                "id": "L5",
                "events_path": "events",
                "version_path": "version.introduced_in",
                "label_path": "version.label",
                "body_marker_template": "[版本 {version}]",
                "require_direct_cited_paragraph_game_version_match": True,
            },
            data,
            "## 时间线\n- [版本 月之三] 测试事件。\n",
            bundle,
        )
    assert "source version label mismatch" in str(exc.value)


def test_conflict_target_pointer_must_exist():
    data = {"relations": [{"object_id": "org:fatui"}]}
    assert ingest._resolve_json_pointer(data, "/relations/0/object_id") == "org:fatui"
    with pytest.raises(ingest.IngestError):
        ingest._resolve_json_pointer(data, "/relations/999")


def test_conflict_proposed_value_shape_must_match_target():
    assert ingest._value_shape_matches({"a": 1}, {"b": 2}) is True
    assert ingest._value_shape_matches({"a": 1}, "bad") is False
    assert ingest._value_shape_matches("org:fatui", "org:demo") is True
    assert ingest._value_shape_matches(["a"], "bad") is False


def test_created_by_predicate_and_target_types():
    assert "created_by" in ingest.PREDICATES
    assert ingest.RELATION_TARGET_PREFIXES["created_by"] == ("char:", "org:")
    assert "aspect_of" in ingest.PREDICATES
    assert ingest.RELATION_TARGET_PREFIXES["aspect_of"] == ("char:",)
    assert "segment_of" in ingest.PREDICATES
    assert ingest.RELATION_TARGET_PREFIXES["segment_of"] == ("char:", "org:", "event:")
    rules = json.loads((Path("lint/wiki-lint-rules.yaml")).read_text(encoding="utf-8"))
    l3 = next(rule for rule in rules["rules"] if rule["id"] == "L3")
    assert {"created_by", "aspect_of", "segment_of"}.issubset(set(l3["allowed"]))


def test_affiliation_state_selection_uses_snapshot_not_array_order():
    before = {
        "state": "unknown",
        "organization_ids": [],
        "identity_version_ids": ["identity:wanderer"],
        "story_scope": {"from_event_id": None, "to_event_id": "event:irminsul-erasure"},
    }
    after = {
        "state": "affiliated",
        "organization_ids": ["org:fatui"],
        "identity_version_ids": ["identity:wanderer"],
        "story_scope": {"from_event_id": "event:irminsul-erasure", "to_event_id": None},
    }
    snapshot = {
        "identity_version_id": "identity:wanderer",
        "story_event_id": "event:irminsul-erasure",
        "position": "after",
    }
    selected = ingest._select_affiliation_state([before, after], snapshot, "identity:wanderer")
    assert selected is after
    selected_reversed = ingest._select_affiliation_state([after, before], snapshot, "identity:wanderer")
    assert selected_reversed is after


def _single_stage_page(bundle, entity_id="char:zhongli", name="钟离"):
    span = bundle["paragraphs"][0]["span_id"]
    evidence = {"source_span_id": span, "basis": "explicit_text", "authority": "non_authoritative"}
    version = {"introduced_in": None, "label": None, "valid_from": None, "valid_to": None, "verified_in": []}
    story_scope = {
        "label": None,
        "from_event_id": None,
        "from_boundary": "unknown",
        "to_event_id": None,
        "to_boundary": "unknown",
    }
    identity_id = "identity:" + entity_id.split(":")[-1]
    name_id = "name:" + entity_id.split(":")[-1]
    snapshot = {"game_version": None, "story_event_id": None, "position": "unknown", "identity_version_id": identity_id}
    assertions = [
        {"path": "/canonical_name", "status": "candidate", "evidence": [evidence], "conflict_ids": []},
        {"path": "/element", "status": "unknown", "evidence": [], "conflict_ids": []},
        {"path": "/weapon", "status": "unknown", "evidence": [], "conflict_ids": []},
        {"path": "/nation", "status": "unknown", "evidence": [], "conflict_ids": []},
        {"path": "/affiliation", "status": "unknown", "evidence": [], "conflict_ids": []},
        {"path": "/aliases/0", "status": "candidate", "evidence": [evidence], "conflict_ids": []},
    ]
    return {
        "schema_version": "0.2.0-draft",
        "entity_id": entity_id,
        "entity_type": "character",
        "data_mode": bundle["mode"],
        "page_status": "draft",
        "as_of_version": None,
        "snapshot": snapshot,
        "canonical_name": name,
        "aliases": [name],
        "element": None,
        "weapon": None,
        "nation": None,
        "affiliation": [],
        "field_assertions": assertions,
        "identity_versions": [
            {
                "identity_version_id": identity_id,
                "label": name + "时期",
                "predecessor_ids": [],
                "transition_event_id": None,
                "version": dict(version),
                "story_scope": dict(story_scope),
                "observed_at": [],
                "names": [
                    {
                        "name_id": name_id,
                        "value": name,
                        "kind": "primary",
                        "version": dict(version),
                        "story_scope": dict(story_scope),
                        "observed_at": [],
                        "status": "candidate",
                        "evidence": [evidence],
                        "conflict_ids": [],
                    }
                ],
                "status": "candidate",
                "evidence": [evidence],
                "conflict_ids": [],
            }
        ],
        "events": [],
        "relations": [],
        "memory_records": [],
        "affiliation_states": [
            {
                "state_id": "affiliation-state:" + entity_id.split(":")[-1],
                "identity_version_ids": [identity_id],
                "state": "unknown",
                "organization_ids": [],
                "version": dict(version),
                "story_scope": dict(story_scope),
                "observed_at": [],
                "coverage": {
                    "extent": "all_organizations",
                    "organization_ids": [],
                    "source_ids": [bundle["sources"][0]["source_id"]],
                    "reviewed_sources": 1,
                    "authoritative_sources": 0,
                    "completeness": "partial",
                    "limitations": "single-stage NPC generality test",
                },
                "status": "candidate",
                "evidence": [],
                "conflict_ids": [],
            }
        ],
        "conflicts": [],
        "test_refs": [],
    }


def test_single_stage_npc_end_to_end(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    bundle = _write_bundle(
        raw,
        "钟离测试原文。",
        {
            "game_version": None,
            "game_version_label": "测试",
            "basis": "explicit_text",
            "authority": "non_authoritative",
        },
    )
    # _write_bundle uses a fixed source id / url; ensure production fixture mode remains fixture so URLs are allowed.
    data = _single_stage_page(bundle)
    body = ingest.render_body(data, bundle)
    page = tmp_path / "zhongli-response.md"
    page.write_text("---\n" + json.dumps(data, ensure_ascii=False, indent=2) + "\n---\n" + body, encoding="utf-8")
    prompt = tmp_path / "prompt.md"
    prompt.write_text("schema={{SCHEMA}}\nspan={{SOURCE_SPAN_FORMAT}}\nbundle={{SOURCE_BUNDLE}}\n", encoding="utf-8")
    text, result_bundle = ingest.ingest(
        raw,
        Path("wiki/__schema__/entity-template.md"),
        response_file=page,
        provider="response-file",
        prompt_path=prompt,
    )
    assert result_bundle["mode"] == "fixture"
    assert "钟离" in text


def test_affiliation_selection_rejects_identity_or_scope_mismatch():
    scaramouche = {
        "state": "affiliated",
        "organization_ids": ["org:fatui"],
        "identity_version_ids": ["identity:scaramouche"],
        "story_scope": {"from_event_id": None, "to_event_id": "event:irminsul-erasure"},
    }
    ended_wanderer = {
        "state": "affiliated",
        "organization_ids": ["org:fatui"],
        "identity_version_ids": ["identity:wanderer"],
        "story_scope": {"from_event_id": None, "to_event_id": "event:irminsul-erasure"},
    }
    current_unknown = {
        "state": "unknown",
        "organization_ids": [],
        "identity_version_ids": ["identity:wanderer"],
        "story_scope": {"from_event_id": "event:irminsul-erasure", "to_event_id": None},
    }
    snapshot = {
        "identity_version_id": "identity:wanderer",
        "story_event_id": "event:irminsul-erasure",
        "position": "after",
    }
    assert ingest._select_affiliation_state([scaramouche], snapshot, "identity:wanderer") is None
    assert ingest._select_affiliation_state([ended_wanderer], snapshot, "identity:wanderer") is None
    assert (
        ingest._select_affiliation_state([scaramouche, current_unknown], snapshot, "identity:wanderer")
        is current_unknown
    )


def test_conflict_field_and_record_type_validation():
    schema = json.loads(Path("wiki/__schema__/entity.schema.json").read_text(encoding="utf-8"))
    fixture_text = Path("tests/fixtures/wanderer-response.md").read_text(encoding="utf-8")
    data, _ = ingest.parse_page(fixture_text)
    relation = data["relations"][0]
    relation_ref = "relation:" + relation["relation_id"]
    # nullable element field: legal enum candidate must pass
    _validate = ingest._validate_proposed_value
    _validate(data, {"target_ref": "field:/element"}, {"proposed_value": "anemo"}, schema)
    # relation object type must match predicate
    with pytest.raises(ingest.IngestError):
        _validate(data, {"target_ref": "field:/relations/0/object_id"}, {"proposed_value": "char:not-an-org"}, schema)
    _validate(data, {"target_ref": "field:/relations/0/object_id"}, {"proposed_value": "org:demo"}, schema)
    # relation target accepts a complete RelationRecord, rejects a MemoryRecord-shaped object
    _validate(
        data,
        {"target_ref": relation_ref},
        {"proposed_value": relation, "version": relation["version"], "evidence": relation["evidence"]},
        schema,
    )
    memory = data["memory_records"][0]
    with pytest.raises(ingest.IngestError):
        _validate(
            data,
            {"target_ref": relation_ref},
            {"proposed_value": memory, "version": relation["version"], "evidence": relation["evidence"]},
            schema,
        )


def test_ingest_rejects_entity_mismatch(tmp_path):
    output = tmp_path / "zhongli.md"
    with pytest.raises(ingest.IngestError):
        ingest.ingest(
            Path("tests/fixtures/wanderer/raw"),
            Path("wiki/__schema__/entity-template.md"),
            response_file=Path("tests/fixtures/wanderer-response.md"),
            provider="response-file",
            expected_entity_id="char:zhongli",
            output=output,
        )
    assert not output.exists()


def test_runtime_excludes_fixture_and_rejected_records(tmp_path):
    from app.canonical import CanonicalIndex

    def write_page(name, data_mode, relations):
        data = {
            "entity_id": "char:" + name,
            "canonical_name": name,
            "aliases": [name],
            "data_mode": data_mode,
            "relations": relations,
            "identity_versions": [],
            "events": [],
            "memory_records": [],
            "affiliation_states": [],
            "conflicts": [],
        }
        (tmp_path / (name + ".md")).write_text(
            "---\n" + json.dumps(data, ensure_ascii=False) + "\n---\n# " + name + "\n",
            encoding="utf-8",
        )

    write_page("fix", "fixture", [])
    write_page("prod", "production", [{"status": "rejected", "statement": "不应返回", "evidence": []}])
    index = CanonicalIndex.from_dir(tmp_path)
    assert [p.entity_id for p in index.pages] == ["char:prod"]
    assert not any(fact.text == "不应返回" for fact in index.pages[0].facts)


def test_l5_both_numeric_and_label_must_match(tmp_path):
    bundle = _write_bundle(
        tmp_path,
        "双版本测试原文。",
        {
            "game_version": None,
            "game_version_label": "月之四",
            "basis": "explicit_text",
            "authority": "non_authoritative",
        },
    )
    span_id = bundle["paragraphs"][0]["span_id"]
    data = {
        "events": [
            {
                "event_id": "event:test",
                "version": {
                    "introduced_in": "3.3",
                    "label": "月之三",
                    "valid_from": None,
                    "valid_to": None,
                    "verified_in": [],
                },
                "evidence": [{"source_span_id": span_id}],
            }
        ]
    }
    with pytest.raises(ValueError):
        wiki_lint._check_l5(
            {
                "id": "L5",
                "events_path": "events",
                "version_path": "version.introduced_in",
                "label_path": "version.label",
                "body_marker_template": "[版本 {version}]",
                "require_direct_cited_paragraph_game_version_match": True,
            },
            data,
            "## 时间线\n- [版本 3.3] 双版本测试。\n",
            bundle,
        )


def test_unknown_scope_requires_explicit_observation():
    state = {
        "state": "affiliated",
        "organization_ids": ["org:fatui"],
        "identity_version_ids": ["identity:wanderer"],
        "story_scope": {
            "from_event_id": None,
            "to_event_id": None,
            "from_boundary": "unknown",
            "to_boundary": "unknown",
        },
        "observed_at": [],
    }
    snapshot = {
        "identity_version_id": "identity:wanderer",
        "story_event_id": "event:irminsul-erasure",
        "position": "after",
    }
    assert ingest._select_affiliation_state([state], snapshot, "identity:wanderer") is None
    state["observed_at"] = [
        {"identity_version_id": "identity:wanderer", "story_event_id": "event:irminsul-erasure", "position": "after"}
    ]
    assert ingest._select_affiliation_state([state], snapshot, "identity:wanderer") is state


def test_version_range_and_introduced_in_semantics():
    assert (
        ingest._version_applies(
            {"introduced_in": None, "valid_from": "3.2", "valid_to": "3.3"}, {"game_version": "5.1"}
        )
        is False
    )
    assert (
        ingest._version_applies(
            {"introduced_in": "3.2", "valid_from": "3.2", "valid_to": None}, {"game_version": "3.3"}
        )
        is True
    )
    assert (
        ingest._version_applies(
            {"introduced_in": "3.2", "valid_from": None, "valid_to": "3.3"}, {"game_version": "3.3"}
        )
        is False
    )


def test_observed_label_mismatch_is_not_applicable():
    state = {
        "observed_at": [
            {"identity_version_id": "identity:wanderer", "game_version_label": "月之三", "position": "after"}
        ],
    }
    snapshot = {
        "identity_version_id": "identity:wanderer",
        "game_version_label": "月之七",
        "story_event_id": None,
        "position": "unknown",
    }
    assert ingest._observed_applies(state, snapshot, "identity:wanderer") is False


def test_relation_candidate_semantics_and_wrapper_consistency():
    schema = json.loads(Path("wiki/__schema__/entity.schema.json").read_text(encoding="utf-8"))
    data, _ = ingest.parse_page(Path("tests/fixtures/wanderer-response.md").read_text(encoding="utf-8"))
    relation = data["relations"][0]
    relation_ref = "relation:" + relation["relation_id"]
    bad_object = json.loads(json.dumps(relation))
    bad_object["object_id"] = "char:wrong"
    with pytest.raises(ingest.IngestError):
        ingest._validate_proposed_value(
            data,
            {"target_ref": relation_ref},
            {"proposed_value": bad_object, "version": relation["version"], "evidence": relation["evidence"]},
            schema,
        )
    bad_wrapper = json.loads(json.dumps(relation))
    bad_wrapper["evidence"] = [
        {"source_span_id": "span:not-same", "basis": "explicit_text", "authority": "non_authoritative"}
    ]
    with pytest.raises(ingest.IngestError):
        ingest._validate_proposed_value(
            data,
            {"target_ref": relation_ref},
            {"proposed_value": bad_wrapper, "version": relation["version"], "evidence": relation["evidence"]},
            schema,
        )
    ingest._validate_proposed_value(
        data,
        {"target_ref": relation_ref},
        {"proposed_value": relation, "version": relation["version"], "evidence": relation["evidence"]},
        schema,
    )
    with pytest.raises(ingest.IngestError):
        ingest._validate_proposed_value(
            data, {"target_ref": "field:/entity_id"}, {"proposed_value": "char:zhongli"}, schema
        )

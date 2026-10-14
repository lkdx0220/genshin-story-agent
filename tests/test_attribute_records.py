# -*- coding: utf-8 -*-
"""B2 attribute_records 契约回归。"""

import json
from pathlib import Path

import pytest

import ingest

SCHEMA = json.loads(Path("wiki/__schema__/entity.schema.json").read_text(encoding="utf-8"))
DEFS = SCHEMA["$defs"]


def _record(key, value):
    return {
        "attribute_id": "attr:test",
        "key": key,
        "value": value,
        "value_label": None,
        "identity_version_ids": [],
        "story_scope": {
            "label": None,
            "from_event_id": None,
            "from_boundary": "unknown",
            "to_event_id": None,
            "to_boundary": "unknown",
        },
        "version": {"introduced_in": None, "valid_from": None, "valid_to": None, "verified_in": []},
        "status": "candidate",
        "evidence": [{"source_span_id": "span:test", "basis": "explicit_text", "authority": "non_authoritative"}],
        "conflict_ids": [],
    }


def _validate(record):
    ingest.validate_json(record, DEFS["AttributeRecord"], DEFS)


def test_attribute_record_accepts_key_value_binding():
    _validate(_record("element", "anemo"))
    _validate(_record("weapon", "catalyst"))
    _validate(_record("nation", "loc:nodkrai"))


def test_attribute_record_rejects_key_value_mismatch():
    for key, value in [("element", True), ("nation", "hydro"), ("weapon", 123), ("element", "sword")]:
        with pytest.raises(ingest.IngestError):
            _validate(_record(key, value))


def _page(tmp_path, records):
    data = {
        "entity_id": "char:a",
        "canonical_name": "甲",
        "aliases": ["甲"],
        "data_mode": "production",
        "identity_versions": [],
        "events": [],
        "relations": [],
        "memory_records": [],
        "affiliation_states": [],
        "attribute_records": records,
        "conflicts": [],
    }
    path = tmp_path / "a.md"
    path.write_text("---\n" + json.dumps(data, ensure_ascii=False) + "\n---\n# 甲\n", encoding="utf-8")
    from app.canonical import CanonicalPage

    return CanonicalPage.from_path(path)


def test_runtime_attribute_fact_keeps_structured_fields(tmp_path):
    record = _record("element", "anemo")
    record["attribute_id"] = "attr:a-element"
    page = _page(tmp_path, [record])
    facts = [fact for fact in page.facts if fact.kind == "attribute"]
    assert len(facts) == 1
    assert facts[0].attribute_id == "attr:a-element"
    assert facts[0].attribute_key == "element"
    assert facts[0].attribute_value == "anemo"


def test_runtime_attribute_ambiguity_warning(tmp_path):
    first = _record("element", "anemo")
    first["attribute_id"] = "attr:a-element-anemo"
    second = _record("element", "geo")
    second["attribute_id"] = "attr:a-element-geo"
    page = _page(tmp_path, [first, second])
    from app.canonical import CanonicalIndex

    text = CanonicalIndex([page]).answer("甲的元素是什么？", snapshot={})
    assert "属性歧义" in text
    assert "anemo" in text and "geo" in text


def _data(records, **overrides):
    data = {
        "entity_id": "char:a",
        "identity_versions": [{"identity_version_id": "identity:a"}],
        "events": [{"event_id": "event:x"}],
        "conflicts": [{"conflict_id": "conflict:c1"}],
        "element": None,
        "weapon": None,
        "nation": None,
        "attribute_records": records,
    }
    data.update(overrides)
    return data


def _ingest_validate(data, paragraphs=None):
    from app import versioning

    ingest._validate_attribute_records(
        data,
        paragraphs if paragraphs is not None else {"span:test": {"quote": "风元素测试"}},
        {"conflict:c1"},
        versioning.load_registry(),
    )


def test_ingest_attribute_rejects_generator_review_status():
    for status in ["accepted", "superseded", "rejected"]:
        record = _record("element", "anemo")
        record["status"] = status
        with pytest.raises(ingest.IngestError):
            _ingest_validate(_data([record]))


def test_ingest_attribute_projection_bidirectional():
    record = _record("element", "anemo")
    _ingest_validate(_data([record], element="anemo"))
    with pytest.raises(ingest.IngestError):
        _ingest_validate(_data([record], element="geo"))
    unknown = _record("element", None)
    _ingest_validate(_data([unknown], element=None))


def test_ingest_attribute_rejects_bad_references():
    record = _record("element", "anemo")
    record["identity_version_ids"] = ["identity:missing"]
    with pytest.raises(ingest.IngestError):
        _ingest_validate(_data([record]))
    record = _record("element", "anemo")
    record["conflict_ids"] = ["conflict:missing"]
    with pytest.raises(ingest.IngestError):
        _ingest_validate(_data([record]))
    record = _record("element", "anemo")
    record["evidence"] = [
        {"source_span_id": "span:missing", "basis": "explicit_text", "authority": "non_authoritative"}
    ]
    with pytest.raises(ingest.IngestError):
        _ingest_validate(_data([record]))


def test_runtime_attribute_projection_identity_and_status(tmp_path):
    first = _record("element", "anemo")
    first["attribute_id"] = "attr:a-element-anemo"
    first["identity_version_ids"] = ["identity:a"]
    second = _record("element", "geo")
    second["attribute_id"] = "attr:a-element-geo"
    second["identity_version_ids"] = ["identity:b"]
    page = _page(tmp_path, [first, second])
    from app.canonical import CanonicalIndex

    index = CanonicalIndex([page])
    projection = index.attribute_projection({"identity_version_id": "identity:a"})
    assert projection["char:a:element"]["status"] == "applicable"
    assert projection["char:a:element"]["value"] == "anemo"
    projection_b = index.attribute_projection({"identity_version_id": "identity:b"})
    assert projection_b["char:a:element"]["value"] == "geo"


def test_runtime_attribute_projection_ambiguous_and_unresolved(tmp_path):
    first = _record("element", "anemo")
    first["attribute_id"] = "attr:a-element-anemo"
    second = _record("element", "geo")
    second["attribute_id"] = "attr:a-element-geo"
    page = _page(tmp_path, [first, second])
    from app.canonical import CanonicalIndex

    assert CanonicalIndex([page]).attribute_projection({})["char:a:element"]["status"] == "ambiguous"

    unresolved = _record("element", "anemo")
    unresolved["attribute_id"] = "attr:a-element-unresolved"
    unresolved["version"] = {"introduced_in": None, "valid_from_label": "月之七", "valid_to": None, "verified_in": []}
    page2 = _page(tmp_path, [unresolved])
    assert (
        CanonicalIndex([page2]).attribute_projection({"game_version_label": "月之三"})["char:a:element"]["status"]
        == "unresolved"
    )


def test_attribute_overlap_adjacent_version_ranges(tmp_path):
    anemo = _record("element", "anemo")
    anemo["attribute_id"] = "attr:a-element-anemo"
    anemo["version"] = {"introduced_in": None, "valid_from": None, "valid_to": "3.3", "verified_in": []}
    geo = _record("element", "geo")
    geo["attribute_id"] = "attr:a-element-geo"
    geo["version"] = {"introduced_in": None, "valid_from": "3.3", "valid_to": None, "verified_in": []}
    page = _page(tmp_path, [anemo, geo])
    from app.canonical import CanonicalIndex

    index = CanonicalIndex([page])
    assert index.attribute_projection({"game_version": "3.2"})["char:a:element"]["value"] == "anemo"
    assert index.attribute_projection({"game_version": "3.3"})["char:a:element"]["value"] == "geo"
    assert index.attribute_projection({"game_version": "3.4"})["char:a:element"]["value"] == "geo"


def test_attribute_same_value_multi_evidence_is_not_ambiguous(tmp_path):
    first = _record("element", "anemo")
    first["attribute_id"] = "attr:a-element-1"
    second = _record("element", "anemo")
    second["attribute_id"] = "attr:a-element-2"
    page = _page(tmp_path, [first, second])
    from app.canonical import CanonicalIndex

    projection = CanonicalIndex([page]).attribute_projection({})
    assert projection["char:a:element"]["status"] == "applicable"
    assert projection["char:a:element"]["value"] == "anemo"


def test_attribute_answer_reports_ambiguity_before_top_k(tmp_path):
    first = _record("element", "anemo")
    first["attribute_id"] = "attr:a-element-anemo"
    second = _record("element", "geo")
    second["attribute_id"] = "attr:a-element-geo"
    page = _page(tmp_path, [first, second])
    from app.canonical import CanonicalIndex

    text = CanonicalIndex([page]).answer("甲的元素是什么？", top_k=1, snapshot={})
    assert "属性歧义" in text


def test_attribute_quote_must_support_value():
    record = _record("element", "anemo")
    record["attribute_id"] = "attr:a-element"
    with pytest.raises(ingest.IngestError):
        ingest._validate_attribute_records(
            _data([record], element="anemo"),
            {"span:test": {"quote": "测试"}},
            {"conflict:c1"},
            __import__("app.versioning", fromlist=["load_registry"]).load_registry(),
        )


def test_attribute_conflict_target_ref_requires_same_key():
    record = _record("element", "anemo")
    record["attribute_id"] = "attr:a-element"
    data = {"attribute_records": [record]}
    alternative = {"proposed_value": record, "version": record["version"], "evidence": record["evidence"]}
    ingest._validate_proposed_value(data, {"target_ref": "attribute:attr:a-element"}, alternative, SCHEMA)
    bad = dict(record)
    bad["key"] = "weapon"
    bad["value"] = "sword"
    with pytest.raises(ingest.IngestError):
        ingest._validate_proposed_value(
            {"attribute_records": [record]},
            {"target_ref": "attribute:attr:a-element"},
            {"proposed_value": bad, "version": bad["version"], "evidence": bad["evidence"]},
            SCHEMA,
        )

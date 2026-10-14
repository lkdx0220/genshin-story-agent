# -*- coding: utf-8 -*-
"""B3 玩家选择绑定回归。"""

import json
from pathlib import Path

import pytest

import ingest

SCHEMA = json.loads(Path("wiki/__schema__/entity.schema.json").read_text(encoding="utf-8"))
DEFS = SCHEMA["$defs"]

PROFILE = {
    "player_choice_options": [
        {"choice_id": "aether", "label": "空", "aliases": ["空", "哥哥"]},
        {"choice_id": "lumine", "label": "荧", "aliases": ["荧", "妹妹"]},
    ]
}


def _page(tmp_path, data):
    path = tmp_path / "traveler.md"
    path.write_text("---\n" + json.dumps(data, ensure_ascii=False) + "\n---\n# 旅行者\n", encoding="utf-8")
    from app.canonical import CanonicalPage

    return CanonicalPage.from_path(path)


def _base_data(**overrides):
    data = {
        "entity_id": "char:traveler",
        "canonical_name": "旅行者",
        "aliases": ["旅行者"],
        "data_mode": "production",
        "player_choice_options": PROFILE["player_choice_options"],
        "identity_versions": [],
        "events": [],
        "relations": [],
        "memory_records": [],
        "affiliation_states": [],
        "attribute_records": [],
        "conflicts": [],
    }
    data.update(overrides)
    return data


def test_player_choice_schema_accepts_choice_and_scope():
    base = {"game_version": None, "story_event_id": None, "position": "unknown", "identity_version_id": None}
    ingest.validate_json({**base, "player_choice": "aether"}, DEFS["QuerySnapshot"], DEFS)
    ingest.validate_json({**base, "player_choice": None}, DEFS["QuerySnapshot"], DEFS)
    with pytest.raises(ingest.IngestError):
        ingest.validate_json({**base, "player_choice": "invalid"}, DEFS["QuerySnapshot"], DEFS)


def test_player_choice_ingest_alias_and_non_choice_entity():
    data = _base_data(aliases=["旅行者", "空"])
    with pytest.raises(ingest.IngestError):
        ingest._validate_player_choice(data, {}, PROFILE)
    non_choice = _base_data(
        player_choice_options=[], events=[{"event_id": "event:x", "player_choice_scope": ["aether"]}]
    )
    with pytest.raises(ingest.IngestError):
        ingest._validate_player_choice(non_choice, {}, {})


def test_player_choice_ingest_requires_scope_on_choice_page():
    data = _base_data(events=[{"event_id": "event:x"}])
    with pytest.raises(ingest.IngestError):
        ingest._validate_player_choice(data, {}, PROFILE)
    data = _base_data(events=[{"event_id": "event:x", "player_choice_scope": []}])
    ingest._validate_player_choice(data, {}, PROFILE)


def test_runtime_choice_scope_filters_and_warns(tmp_path):
    data = _base_data(
        events=[
            {"event_id": "event:x", "title": "空分支事件", "player_choice_scope": ["aether"]},
            {"event_id": "event:y", "title": "共同事件", "player_choice_scope": []},
        ]
    )
    page = _page(tmp_path, data)
    from app.canonical import CanonicalIndex

    index = CanonicalIndex([page])
    aether_fact = [f for f in page.facts if "空分支事件" in f.text][0]
    common_fact = [f for f in page.facts if "共同事件" in f.text][0]
    assert index._fact_applies(aether_fact, {"player_choice": "aether"}) is True
    assert index._fact_applies(aether_fact, {"player_choice": "lumine"}) is False
    assert index._fact_applies(aether_fact, {}) is False
    assert index.player_choice_gaps({})
    text = index.answer("事件", snapshot={})
    assert "选择缺口" in text
    assert index._fact_applies(common_fact, {}) is True


def test_runtime_bare_name_does_not_bind_choice(tmp_path):
    data = _base_data(events=[{"event_id": "event:x", "title": "空分支事件", "player_choice_scope": ["aether"]}])
    page = _page(tmp_path, data)
    from app.canonical import CanonicalIndex

    index = CanonicalIndex([page])
    text = index.answer("空是谁？", snapshot={})
    assert "选择缺口" in text
    assert index.attribute_projection({}) == {}


def test_player_choice_branch_evidence_required():
    data = _base_data(
        events=[
            {
                "event_id": "event:x",
                "player_choice_scope": ["aether"],
                "evidence": [
                    {"source_span_id": "span:test", "basis": "explicit_text", "authority": "non_authoritative"}
                ],
            }
        ]
    )
    with pytest.raises(ingest.IngestError):
        ingest._validate_player_choice(data, {"span:test": {"quote": "测试"}}, PROFILE)
    ingest._validate_player_choice(data, {"span:test": {"quote": "空的选择"}}, PROFILE)


def test_player_choice_observed_at_mismatch(tmp_path):
    record = {
        "attribute_id": "attr:traveler-element",
        "key": "element",
        "value": "anemo",
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
        "player_choice_scope": ["aether"],
        "observed_at": [
            {"identity_version_id": None, "story_event_id": None, "position": "unknown", "player_choice": "aether"}
        ],
    }
    data = _base_data(attribute_records=[record])
    page = _page(tmp_path, data)
    from app.canonical import CanonicalIndex

    index = CanonicalIndex([page])
    assert (
        index.attribute_projection({"player_choice": "aether", "position": "unknown"})["char:traveler:element"][
            "status"
        ]
        == "applicable"
    )
    assert (
        index.attribute_projection({"player_choice": "lumine", "position": "unknown"})["char:traveler:element"][
            "status"
        ]
        == "not_applicable"
    )


def test_player_choice_gap_not_hidden_by_topk(tmp_path):
    data = _base_data(
        events=[
            {"event_id": "event:common", "title": "共同事件", "player_choice_scope": []},
            {"event_id": "event:aether", "title": "空分支事件", "player_choice_scope": ["aether"]},
        ]
    )
    page = _page(tmp_path, data)
    from app.canonical import CanonicalIndex

    text = CanonicalIndex([page]).answer("事件", top_k=1, snapshot={})
    assert "选择缺口" in text


def test_non_choice_entity_choice_parameter_has_no_effect(tmp_path):
    data = _base_data(player_choice_options=[], events=[{"event_id": "event:x", "title": "普通事件"}])
    page = _page(tmp_path, data)
    from app.canonical import CanonicalIndex

    fact = [item for item in page.facts if "普通事件" in item.text][0]
    index = CanonicalIndex([page])
    assert index._fact_applies(fact, {}) is True
    assert index._fact_applies(fact, {"player_choice": "aether"}) is True
    assert index._fact_applies(fact, {"player_choice": "lumine"}) is True

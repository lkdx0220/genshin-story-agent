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
                "player_choice_branch": {
                    "choice_id": "aether",
                    "kind": "explicit_branch",
                    "source_span_id": "span:test",
                },
                "evidence": [
                    {"source_span_id": "span:test", "basis": "explicit_text", "authority": "non_authoritative"}
                ],
            }
        ]
    )
    with pytest.raises(ingest.IngestError):
        ingest._validate_player_choice(data, {}, PROFILE)
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
    data = _base_data(
        entity_id="char:other",
        canonical_name="其他",
        player_choice_options=[],
        events=[{"event_id": "event:x", "title": "普通事件"}],
    )
    page = _page(tmp_path, data)
    from app.canonical import CanonicalIndex

    fact = [item for item in page.facts if "普通事件" in item.text][0]
    index = CanonicalIndex([page])
    assert index._fact_applies(fact, {}) is True
    assert index._fact_applies(fact, {"player_choice": "aether"}) is True
    assert index._fact_applies(fact, {"player_choice": "lumine"}) is True


def test_player_choice_tool_reports_binding_conflict():
    from app.tools.canonical import canonical_query_tool

    conflict = canonical_query_tool.invoke(
        {"question": "旅行者是谁", "snapshot": {"player_choice": "lumine"}, "player_choice": "aether"}
    )
    assert "选择冲突" in conflict
    invalid = canonical_query_tool.invoke({"question": "旅行者是谁", "player_choice": "invalid"})
    assert "非法 player_choice" in invalid


def test_identity_summary_does_not_leak_other_choice(tmp_path):
    data = _base_data(
        identity_versions=[
            {
                "identity_version_id": "identity:traveler",
                "label": "旅行者",
                "status": "candidate",
                "evidence": [
                    {"source_span_id": "span:test", "basis": "explicit_text", "authority": "non_authoritative"}
                ],
                "names": [
                    {
                        "name_id": "name:common",
                        "value": "旅行者",
                        "kind": "primary",
                        "player_choice_scope": [],
                        "evidence": [],
                    },
                    {
                        "name_id": "name:aether",
                        "value": "空",
                        "kind": "alias",
                        "player_choice_scope": ["aether"],
                        "evidence": [],
                    },
                    {
                        "name_id": "name:lumine",
                        "value": "荧",
                        "kind": "alias",
                        "player_choice_scope": ["lumine"],
                        "evidence": [],
                    },
                ],
            }
        ]
    )
    page = _page(tmp_path, data)
    name_facts = [f for f in page.facts if f.kind == "name"]
    aether_name = [f for f in name_facts if f.player_choice_scope == ["aether"]][0]
    lumine_name = [f for f in name_facts if f.player_choice_scope == ["lumine"]][0]
    assert aether_name.text == "名称：空（旅行者）"
    assert lumine_name.text == "名称：荧（旅行者）"
    assert all("空" not in f.text and "荧" not in f.text for f in page.facts if f.kind == "identity")


def test_conflict_alternative_inherits_choice_scope(tmp_path):
    data = _base_data(
        conflicts=[
            {
                "conflict_id": "conflict:c1",
                "status": "open",
                "summary": "测试冲突",
                "target_ref": "attribute:attr:test",
                "alternatives": [
                    {
                        "alternative_id": "alt:a",
                        "statement": "空分支说法",
                        "proposed_value": {"player_choice_scope": ["aether"]},
                        "evidence": [],
                    }
                ],
            }
        ]
    )
    page = _page(tmp_path, data)
    alternatives = [f for f in page.facts if f.kind == "conflict_alternative"]
    assert alternatives[0].player_choice_scope == ["aether"]


def test_search_none_snapshot_does_not_return_branch_hits(tmp_path):
    data = _base_data(events=[{"event_id": "event:x", "title": "空分支事件", "player_choice_scope": ["aether"]}])
    page = _page(tmp_path, data)
    from app.canonical import CanonicalIndex

    index = CanonicalIndex([page])
    assert index.search("空分支事件", snapshot=None) == []
    assert index.search("空分支事件", snapshot={}) == []


def test_runtime_missing_scope_is_unclassified_gap(tmp_path):
    data = _base_data(events=[{"event_id": "event:x", "title": "未分类事件"}])
    page = _page(tmp_path, data)
    from app.canonical import CanonicalIndex

    fact = [item for item in page.facts if "未分类事件" in item.text][0]
    index = CanonicalIndex([page])
    assert fact.player_choice_missing is True
    assert index._fact_applies(fact, {}) is False
    assert any("未分类" in gap for gap in index.player_choice_gaps({}, question="事件"))


def test_attribute_observed_search_respects_point_observation(tmp_path):
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
        "evidence": [],
        "conflict_ids": [],
        "player_choice_scope": [],
        "observed_at": [
            {"identity_version_id": None, "story_event_id": None, "position": "unknown", "game_version": "3.2"}
        ],
    }
    data = _base_data(attribute_records=[record])
    page = _page(tmp_path, data)
    from app.canonical import CanonicalIndex

    index = CanonicalIndex([page])
    assert index.search("元素", snapshot={"game_version": "3.2", "position": "unknown"})
    assert not index.search("元素", snapshot={"game_version": "5.1", "position": "unknown"})


def test_attribute_version_excluded_beats_choice_unresolved(tmp_path):
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
        "version": {"introduced_in": None, "valid_from": None, "valid_to": "3.2", "verified_in": []},
        "status": "candidate",
        "evidence": [],
        "conflict_ids": [],
        "player_choice_scope": ["aether"],
        "observed_at": [],
    }
    data = _base_data(attribute_records=[record])
    page = _page(tmp_path, data)
    from app.canonical import CanonicalIndex

    index = CanonicalIndex([page])
    assert index.attribute_projection({"game_version": "5.1"})["char:traveler:element"]["status"] == "not_applicable"
    gaps = index.player_choice_gaps({"game_version": "5.1"}, question="元素")
    assert not any("attr:traveler-element" in gap for gap in gaps)


def test_profile_detection_marks_missing_scope(tmp_path):
    data = _base_data(events=[{"event_id": "event:x", "title": "旧页事件"}])
    data.pop("player_choice_options", None)
    page = _page(tmp_path, data)
    fact = [item for item in page.facts if "旧页事件" in item.text][0]
    assert fact.player_choice_missing is True


def test_identity_summary_excludes_missing_names(tmp_path):
    data = _base_data(
        identity_versions=[
            {
                "identity_version_id": "identity:traveler",
                "label": "旅行者",
                "status": "candidate",
                "evidence": [],
                "names": [{"name_id": "name:x", "value": "未分类名", "kind": "primary", "evidence": []}],
            }
        ]
    )
    page = _page(tmp_path, data)
    assert all("未分类名" not in fact.text for fact in page.facts if fact.kind == "identity")


def test_choice_specified_still_reports_missing_scope_gaps(tmp_path):
    data = _base_data(events=[{"event_id": "event:x", "title": "旧页事件"}])
    data.pop("player_choice_options", None)
    page = _page(tmp_path, data)
    from app.canonical import CanonicalIndex

    gaps = CanonicalIndex([page]).player_choice_gaps({"player_choice": "lumine"}, question="事件")
    assert any("未分类" in gap for gap in gaps)


def test_axis_excluded_beats_version_unresolved(tmp_path):
    record = _record_for_choice("element", "anemo", ["aether"], {"valid_from_label": "月之七", "valid_to": None})
    data = _base_data(attribute_records=[record])
    page = _page(tmp_path, data)
    from app.canonical import CanonicalIndex

    status = CanonicalIndex([page]).attribute_projection({"player_choice": "lumine"})["char:traveler:element"]["status"]
    assert status == "not_applicable"


def _record_for_choice(key, value, scope, version):
    return {
        "attribute_id": "attr:traveler-element",
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
        "version": {"introduced_in": None, "valid_from": None, "valid_to": None, "verified_in": [], **version},
        "status": "candidate",
        "evidence": [],
        "conflict_ids": [],
        "player_choice_scope": scope,
        "observed_at": [],
    }


def test_ingest_attribute_projection_respects_observed_at():
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
        "observed_at": [
            {"identity_version_id": None, "story_event_id": None, "position": "unknown", "game_version": "3.2"}
        ],
        "player_choice_scope": [],
    }
    data = _base_data(attribute_records=[record])
    data["snapshot"] = {
        "game_version": "5.1",
        "story_event_id": None,
        "position": "unknown",
        "identity_version_id": None,
    }
    ingest._validate_attribute_records(
        data,
        {"span:test": {"quote": "风元素"}},
        set(),
        __import__("app.versioning", fromlist=["load_registry"]).load_registry(),
    )


def test_observed_affiliation_choice_mismatch():
    state = {"observed_at": [{"identity_version_id": "identity:traveler", "player_choice": "aether"}]}
    assert (
        ingest._observed_applies(
            state, {"identity_version_id": "identity:traveler", "player_choice": "lumine"}, "identity:traveler"
        )
        is False
    )


def test_conflict_choice_scope_needs_branch():
    conflict = {
        "conflict_id": "conflict:c1",
        "status": "open",
        "summary": "冲突",
        "target_ref": "attribute:attr:test",
        "player_choice_scope": ["aether"],
        "alternatives": [],
    }
    with pytest.raises(ingest.IngestError):
        ingest._validate_player_choice(_base_data(conflicts=[conflict]), {}, PROFILE)

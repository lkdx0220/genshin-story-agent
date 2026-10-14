# -*- coding: utf-8 -*-
"""Wave1 运行时接入回归：canonical 页 -> loader -> 检索 -> 带 citation 回答。"""

import json

import pytest

from app.canonical import CANONICAL_DIR, CanonicalIndex, CanonicalPage, canonical_query

PAGE = CANONICAL_DIR / "wanderer.md"
pytestmark = pytest.mark.skipif(not PAGE.exists(), reason="canonical Wanderer page not present locally")


def test_canonical_page_loads_identity_aliases_and_citations():
    page = CanonicalPage.from_path(PAGE)
    assert page.entity_id == "char:wanderer"
    assert page.canonical_name == "流浪者"
    assert {"散兵", "流浪者", "国崩", "斯卡拉姆齐"}.issubset(set(page.aliases))
    assert page.span_url, "page should expose span->URL footnote mapping"
    assert any(fact.kind == "identity" for fact in page.facts)
    assert any(fact.kind == "relation" for fact in page.facts)
    assert any(fact.kind == "event" for fact in page.facts)


def test_canonical_index_answers_relation_with_citation():
    index = CanonicalIndex.from_dir(CANONICAL_DIR)
    text = index.answer("散兵在愚人众里是什么身份？")
    assert "第六席" in text
    assert "愚人众" in text
    assert "[^source:https://wiki.biligame.com" in text
    assert "span:" in text


def test_canonical_index_answers_event_and_memory_with_citation():
    index = CanonicalIndex.from_dir(CANONICAL_DIR)
    event_text = index.answer("世界树事件后发生了什么？")
    assert "世界树" in event_text
    assert "[^source:https://wiki.biligame.com" in event_text
    memory_text = index.answer("流浪者后来为什么再次登场？")
    assert ("挪得卡莱" in memory_text) or ("博士" in memory_text)
    assert "[^source:https://wiki.biligame.com" in memory_text


def test_canonical_query_returns_explicit_gap_when_no_hit():
    text = canonical_query("完全不存在的角色问题ZZZ")
    assert "没有可引用的记录" in text


def test_canonical_tool_wrapper_exposes_schema_and_output():
    from app.tools.canonical import canonical_query_tool

    assert canonical_query_tool.name == "canonical_query"
    assert canonical_query_tool.description
    out = canonical_query_tool.invoke({"question": "散兵和流浪者是什么关系？"})
    assert "流浪者" in out
    assert "[^source:https://wiki.biligame.com" in out


def test_canonical_alias_ambiguity_is_flagged(tmp_path):
    from app.canonical import CanonicalIndex, CanonicalPage
    import json as _json

    pages = []
    for entity_id in ("char:a", "char:b"):
        path = tmp_path / (entity_id.split(":")[-1] + ".md")
        data = {
            "entity_id": entity_id,
            "canonical_name": "同名",
            "aliases": ["同名"],
            "identity_versions": [],
            "events": [],
            "relations": [],
            "memory_records": [],
            "affiliation_states": [],
            "conflicts": [],
        }
        path.write_text("---\n" + _json.dumps(data, ensure_ascii=False) + "\n---\n# 同名\n", encoding="utf-8")
        pages.append(CanonicalPage.from_path(path))
    index = CanonicalIndex(pages)
    assert index.alias_ambiguities()["同名"] == ["char:a", "char:b"]
    out = index.answer("同名是谁？")
    assert "[歧义]" in out


def _make_page(tmp_path, name, data):
    path = tmp_path / (name + ".md")
    path.write_text("---\n" + json.dumps(data, ensure_ascii=False) + "\n---\n# " + name + "\n", encoding="utf-8")
    from app.canonical import CanonicalPage

    return CanonicalPage.from_path(path)


def test_rejected_name_does_not_flow_into_identity_summary(tmp_path):
    data = {
        "entity_id": "char:a",
        "canonical_name": "当前名",
        "aliases": ["当前名"],
        "data_mode": "production",
        "identity_versions": [
            {
                "identity_version_id": "identity:a",
                "label": "阶段",
                "status": "candidate",
                "evidence": [],
                "names": [
                    {"name_id": "name:a", "value": "当前名", "kind": "primary", "status": "candidate", "evidence": []},
                    {"name_id": "name:old", "value": "旧名", "kind": "alias", "status": "rejected", "evidence": []},
                ],
            }
        ],
        "events": [],
        "relations": [],
        "memory_records": [],
        "affiliation_states": [],
        "conflicts": [],
    }
    page = _make_page(tmp_path, "a", data)
    texts = [fact.text for fact in page.facts]
    assert all("旧名" not in text for text in texts)
    assert any("当前名" in text for text in texts)


def test_superseded_relation_is_excluded_by_default_and_available_historically(tmp_path):
    data = {
        "entity_id": "char:a",
        "canonical_name": "甲",
        "aliases": ["甲"],
        "data_mode": "production",
        "identity_versions": [],
        "events": [],
        "relations": [{"relation_id": "rel:old", "status": "superseded", "statement": "旧关系", "evidence": []}],
        "memory_records": [],
        "affiliation_states": [],
        "conflicts": [],
    }
    page = _make_page(tmp_path, "a", data)
    from app.canonical import CanonicalIndex

    index = CanonicalIndex([page])
    assert not index.search("关系")
    assert index.search("关系", include_superseded=True)
    assert index.search("历史上旧关系")


def test_conflict_alternative_carries_conflict_metadata(tmp_path):
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
        "conflicts": [
            {
                "conflict_id": "conflict:c1",
                "target_ref": "relation:rel:x",
                "summary": "候选冲突",
                "status": "open",
                "resolution": None,
                "alternatives": [{"alternative_id": "alt-a", "statement": "候选说法", "evidence": []}],
            }
        ],
    }
    page = _make_page(tmp_path, "a", data)
    alternatives = [fact for fact in page.facts if fact.kind == "conflict_alternative"]
    assert alternatives
    assert alternatives[0].conflict_id == "conflict:c1"
    assert alternatives[0].target_ref == "relation:rel:x"
    assert alternatives[0].status == "open"


def test_fact_applicability_uses_identity_and_scope():
    from app.canonical import Fact, CanonicalIndex

    fact = Fact(
        kind="relation",
        text="关系",
        identity_version_ids=["identity:wanderer"],
        story_scope={"from_event_id": "event:irminsul-erasure", "to_event_id": None},
    )
    assert (
        CanonicalIndex._fact_applies(
            fact,
            {
                "identity_version_id": "identity:wanderer",
                "story_event_id": "event:irminsul-erasure",
                "position": "after",
            },
        )
        is True
    )
    assert (
        CanonicalIndex._fact_applies(
            fact,
            {
                "identity_version_id": "identity:scaramouche",
                "story_event_id": "event:irminsul-erasure",
                "position": "after",
            },
        )
        is False
    )
    assert (
        CanonicalIndex._fact_applies(
            fact,
            {
                "identity_version_id": "identity:wanderer",
                "story_event_id": "event:irminsul-erasure",
                "position": "before",
            },
        )
        is False
    )


def test_canonical_tool_accepts_snapshot_and_is_routed():
    from app.tools.canonical import canonical_query_tool
    from app.tools import tools_by_name
    from intent_router import get_tools_for_intent
    from app.schema import AGENT_SYSTEM_PROMPT_ANSWER

    out = canonical_query_tool.invoke(
        {
            "question": "当前组织归属是什么？",
            "snapshot": {
                "identity_version_id": "identity:wanderer",
                "story_event_id": "event:irminsul-erasure",
                "position": "after",
                "game_version": "3.3",
            },
        }
    )
    assert "组织归属未确认" in out
    event_out = canonical_query_tool.invoke(
        {
            "question": "世界树事件后发生了什么？",
            "snapshot": {
                "identity_version_id": "identity:wanderer",
                "story_event_id": "event:irminsul-erasure",
                "position": "after",
                "game_version": "3.3",
            },
        }
    )
    assert "[^source:https://wiki.biligame.com" in event_out
    routed = {tool.name for tool in get_tools_for_intent(["B"], tools_by_name)}
    assert "canonical_query" in routed
    assert "canonical" in AGENT_SYSTEM_PROMPT_ANSWER
    assert "候选证据" in AGENT_SYSTEM_PROMPT_ANSWER


def test_runtime_version_semantics_publication_vs_range():
    from app.canonical import CanonicalIndex, Fact

    publication_only = Fact(kind="event", text="3.2 事件", version={"introduced_in": "3.2"})
    assert CanonicalIndex._fact_applies(publication_only, {"game_version": "5.1"}) is True
    ranged = Fact(
        kind="relation",
        text="3.2-3.3 限定关系",
        version={"valid_from": "3.2", "valid_to": "3.3"},
    )
    assert CanonicalIndex._fact_applies(ranged, {"game_version": "3.2"}) is True
    assert CanonicalIndex._fact_applies(ranged, {"game_version": "3.2.1"}) is True
    assert CanonicalIndex._fact_applies(ranged, {"game_version": "3.3"}) is False
    assert CanonicalIndex._fact_applies(ranged, {"game_version": "5.1"}) is False
    assert CanonicalIndex._fact_applies(ranged, {}) is True


def test_runtime_unknown_label_order_does_not_widen_range():
    from app.canonical import CanonicalIndex, Fact

    ranged = Fact(
        kind="relation",
        text="月之三到月之七",
        version={"valid_from_label": "月之三", "valid_to_label": "月之七"},
    )
    assert CanonicalIndex._fact_applies(ranged, {"game_version_label": "月之三"}) is False


def test_registry_fingerprint_and_projected_version_id():
    from app import versioning

    index = CanonicalIndex.from_dir()
    assert index.registry_fingerprint
    assert index.registry_fingerprint == versioning.registry_fingerprint(versioning.load_registry())
    month_3 = [fact for fact in index.pages[0].facts if fact.version_id == "version:month-3"]
    assert month_3
    assert month_3[0].kind == "event"


def test_answer_reports_registry_gap_for_unknown_label_order(tmp_path):
    data = {
        "entity_id": "char:a",
        "canonical_name": "甲",
        "aliases": ["甲"],
        "data_mode": "production",
        "identity_versions": [],
        "events": [],
        "relations": [
            {
                "relation_id": "rel:x",
                "status": "candidate",
                "statement": "标签范围关系",
                "version": {"valid_from_label": "月之三", "valid_to_label": "月之七"},
                "evidence": [],
            }
        ],
        "memory_records": [],
        "affiliation_states": [],
        "conflicts": [],
    }
    page = _make_page(tmp_path, "a", data)
    from app.canonical import CanonicalIndex

    text = CanonicalIndex([page]).answer("标签范围关系", snapshot={"game_version_label": "月之四"})
    assert "版本注册表缺口" in text

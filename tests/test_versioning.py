# -*- coding: utf-8 -*-
"""B1 版本契约统一解析回归。"""

import copy
import json
from pathlib import Path

import pytest

from app import versioning


def test_default_registry_loads_and_knows_month_labels():
    registry = versioning.load_registry()
    assert versioning.registry_fingerprint(registry)
    resolved = versioning.resolve_version(label="月之三", registry=registry)
    assert resolved.version_id == "version:month-3"
    assert resolved.canonical_label == "月之三"
    assert resolved.numeric is None
    assert resolved.numeric_status == "unknown"
    assert resolved.order_status == "unknown"


def test_registry_rejects_duplicate_ids_labels_numeric_order_and_alias():
    base = versioning.load_registry()
    cases = []
    dup_id = copy.deepcopy(base)
    dup_id["versions"][1]["version_id"] = dup_id["versions"][0]["version_id"]
    cases.append(dup_id)
    dup_label = copy.deepcopy(base)
    dup_label["versions"][1]["label"]["value"] = dup_label["versions"][0]["label"]["value"]
    cases.append(dup_label)
    dup_alias = copy.deepcopy(base)
    dup_alias["versions"][1]["aliases"] = [{"value": "月之三", "status": "candidate", "evidence": []}]
    cases.append(dup_alias)
    for registry in cases:
        with pytest.raises(versioning.VersionRegistryError):
            versioning.validate_registry(registry)


def test_confirmed_numeric_and_order_require_evidence_and_unique():
    registry = {
        "registry_revision": "test",
        "versions": [
            {
                "version_id": "version:a",
                "label": {"value": "甲", "status": "confirmed", "evidence": [{"source_span_id": "span:a"}]},
                "numeric": {"value": "3.3", "status": "confirmed", "evidence": []},
                "order": {"value": None, "status": "unknown", "evidence": []},
                "aliases": [],
            }
        ],
    }
    with pytest.raises(versioning.VersionRegistryError):
        versioning.validate_registry(registry)


def test_resolve_alias_and_numeric_label_conflict():
    registry = {
        "registry_revision": "test",
        "versions": [
            {
                "version_id": "version:a",
                "label": {"value": "甲", "status": "confirmed", "evidence": [{"source_span_id": "span:a"}]},
                "numeric": {"value": "3.3", "status": "confirmed", "evidence": [{"source_span_id": "span:a"}]},
                "order": {"value": 33, "status": "confirmed", "evidence": [{"source_span_id": "span:a"}]},
                "aliases": [{"value": "甲别名", "status": "confirmed", "evidence": [{"source_span_id": "span:a"}]}],
            },
            {
                "version_id": "version:b",
                "label": {"value": "乙", "status": "confirmed", "evidence": [{"source_span_id": "span:b"}]},
                "numeric": {"value": "3.4", "status": "candidate", "evidence": [{"source_span_id": "span:b"}]},
                "order": {"value": None, "status": "unknown", "evidence": []},
                "aliases": [],
            },
        ],
    }
    versioning.validate_registry(registry)
    assert versioning.resolve_version(label="甲别名", registry=registry).version_id == "version:a"
    with pytest.raises(versioning.VersionRegistryError):
        versioning.resolve_version(numeric="3.4", label="甲", registry=registry)


def test_numeric_comparison_ignores_trailing_zero_and_handles_two_digit():
    a = versioning.ResolvedVersion(numeric="3.3", matched_status="confirmed", input_kind="numeric")
    b = versioning.ResolvedVersion(numeric="3.3.0", matched_status="confirmed", input_kind="numeric")
    c = versioning.ResolvedVersion(numeric="3.10", matched_status="confirmed", input_kind="numeric")
    d = versioning.ResolvedVersion(numeric="3.9", matched_status="confirmed", input_kind="numeric")
    assert versioning.compare_versions(a, b) == 0
    assert versioning.compare_versions(c, d) == 1
    assert versioning.compare_versions(d, c) == -1


def test_candidate_mapping_and_unknown_order_are_not_deterministic():
    candidate = versioning.ResolvedVersion(
        version_id="version:x",
        numeric="3.3",
        numeric_status="candidate",
        matched_status="candidate",
        input_kind="numeric",
    )
    raw = versioning.ResolvedVersion(numeric="3.3", input_kind="numeric")
    assert versioning.compare_versions(candidate, raw) is None
    a = versioning.ResolvedVersion(
        version_id="version:a", order=1, order_status="candidate", matched_status="candidate", input_kind="label"
    )
    b = versioning.ResolvedVersion(
        version_id="version:b", order=2, order_status="confirmed", matched_status="confirmed", input_kind="label"
    )
    assert versioning.compare_versions(a, b) is None
    assert (
        versioning.compare_versions(
            versioning.ResolvedVersion(
                version_id="version:a",
                order=1,
                order_status="confirmed",
                matched_status="confirmed",
                input_kind="label",
            ),
            versioning.ResolvedVersion(
                version_id="version:b",
                order=2,
                order_status="confirmed",
                matched_status="confirmed",
                input_kind="label",
            ),
        )
        == -1
    )


def test_range_contains_lower_inclusive_upper_exclusive_and_unknown():
    lower = versioning.ResolvedVersion(numeric="3.2", matched_status="confirmed", input_kind="numeric")
    upper = versioning.ResolvedVersion(numeric="3.3", matched_status="confirmed", input_kind="numeric")
    assert (
        versioning.range_contains(
            versioning.ResolvedVersion(numeric="3.2", matched_status="confirmed", input_kind="numeric"), lower, upper
        )
        is True
    )
    assert (
        versioning.range_contains(
            versioning.ResolvedVersion(numeric="3.3", matched_status="confirmed", input_kind="numeric"), lower, upper
        )
        is False
    )
    assert versioning.range_contains(versioning.ResolvedVersion(raw_label="月之三"), lower, upper) is None
    assert (
        versioning.range_contains(
            versioning.ResolvedVersion(numeric="3.2.1", matched_status="confirmed", input_kind="numeric"), lower, upper
        )
        is True
    )


def test_missing_registry_raises():
    with pytest.raises(versioning.VersionRegistryError):
        versioning.load_registry(Path("/definitely/missing/version-registry.json"))


def test_ingest_version_scope_same_span_pair_allows_direct_pairing():
    paragraphs = {
        "span:a": {"game_version": "3.3", "game_version_label": "月之三"},
    }
    evidence = [{"source_span_id": "span:a"}]
    from ingest import _validate_version_scope

    _validate_version_scope(
        {"introduced_in": "3.3", "label": "月之三", "valid_from": None, "valid_to": None},
        paragraphs,
        evidence,
        "test",
    )


def test_ingest_version_scope_split_span_pair_rejected():
    paragraphs = {
        "span:a": {"game_version": "9.9", "game_version_label": None},
        "span:b": {"game_version": None, "game_version_label": "测试标签"},
    }
    evidence = [{"source_span_id": "span:a"}, {"source_span_id": "span:b"}]
    from ingest import IngestError, _validate_version_scope

    with pytest.raises(IngestError):
        _validate_version_scope(
            {"introduced_in": "9.9", "label": "测试标签", "valid_from": None, "valid_to": None},
            paragraphs,
            evidence,
            "test",
        )


def test_ingest_version_scope_unregistered_pair_supported_by_evidence_passes():
    paragraphs = {
        "span:a": {"game_version": "9.9", "game_version_label": "测试标签"},
    }
    evidence = [{"source_span_id": "span:a"}]
    from ingest import _validate_version_scope

    _validate_version_scope(
        {"introduced_in": "9.9", "label": "测试标签", "valid_from": None, "valid_to": None},
        paragraphs,
        evidence,
        "test",
    )


def test_ingest_version_scope_inverted_range_rejected():
    from ingest import IngestError, _validate_version_scope

    with pytest.raises(IngestError):
        _validate_version_scope(
            {"introduced_in": None, "label": None, "valid_from": "3.3", "valid_to": "3.2"},
            {},
            [],
            "test",
        )


def test_ingest_version_scope_generator_cannot_fill_verified_labels():
    from ingest import IngestError, _validate_version_scope

    with pytest.raises(IngestError):
        _validate_version_scope(
            {
                "introduced_in": None,
                "label": None,
                "valid_from": None,
                "valid_to": None,
                "verified_in_labels": ["月之三"],
            },
            {},
            [],
            "test",
        )


def test_l6_rejects_numeric_label_conflict():
    from wiki_lint import _check_l6

    rule = {"id": "L6", "version_path": "version", "snapshot_path": "snapshot", "scope_paths": ["events"]}
    data = {"events": [{"event_id": "event:x", "version": {"introduced_in": "3.3", "label": "月之三"}}]}
    with pytest.raises(ValueError):
        _check_l6(rule, data, "", {})


def test_l6_rejects_unregistered_verified_label():
    from wiki_lint import _check_l6

    rule = {"id": "L6", "version_path": "version", "snapshot_path": "snapshot", "scope_paths": ["events"]}
    data = {"events": [{"event_id": "event:x", "version": {"label": "月之三", "verified_in_labels": ["未注册标签"]}}]}
    with pytest.raises(ValueError):
        _check_l6(rule, data, "", {})


def _entry(version_id, label, numeric=None, order=None):
    return {
        "version_id": version_id,
        "label": {"value": label, "status": "confirmed", "evidence": [{"source_span_id": "span:test"}]},
        "numeric": {
            "value": numeric,
            "status": "confirmed" if numeric else "unknown",
            "evidence": [{"source_span_id": "span:test"}] if numeric else [],
        },
        "order": {
            "value": order,
            "status": "confirmed" if order is not None else "unknown",
            "evidence": [{"source_span_id": "span:test"}] if order is not None else [],
        },
        "aliases": [],
    }


def test_registry_rejects_duplicate_confirmed_numeric_and_order():
    registry = {
        "registry_revision": "t",
        "versions": [
            _entry("version:a", "甲", numeric="3.3", order=1),
            _entry("version:b", "乙", numeric="3.3", order=1),
        ],
    }
    with pytest.raises(versioning.VersionRegistryError):
        versioning.validate_registry(registry)


def test_runtime_missing_registry_does_not_widen(monkeypatch, tmp_path):
    from app.canonical import CanonicalIndex, Fact

    monkeypatch.setattr(versioning, "DEFAULT_REGISTRY_PATH", tmp_path / "missing.json")
    fact = Fact(kind="relation", text="限定", version={"valid_from": "3.2", "valid_to": "3.3"})
    assert CanonicalIndex._fact_applies(fact, {"game_version": "3.3"}) is False


def test_registry_cache_reloads_when_file_changes(tmp_path):
    path = tmp_path / "registry.json"
    registry = {"registry_revision": "r1", "versions": [_entry("version:a", "甲")]}
    path.write_text(json.dumps(registry, ensure_ascii=False), encoding="utf-8")
    assert versioning.load_registry(path, known_span_ids={"span:test"})["registry_revision"] == "r1"
    registry["registry_revision"] = "r2-longer"
    registry["versions"] = [_entry("version:b", "乙")]
    path.write_text(json.dumps(registry, ensure_ascii=False), encoding="utf-8")
    assert versioning.load_registry(path, known_span_ids={"span:test"})["registry_revision"] == "r2-longer"


def test_resolver_does_not_adopt_one_side_for_double_value():
    resolved = versioning.resolve_version(numeric="3.3", label="月之三")
    assert resolved.pair_status == "unresolved"
    assert resolved.version_id is None


def test_resolver_keeps_candidate_alias_non_deterministic():
    registry = {
        "registry_revision": "test",
        "versions": [
            {
                "version_id": "version:a",
                "label": {"value": "甲", "status": "confirmed", "evidence": [{"source_span_id": "span:a"}]},
                "numeric": {"value": "3.3", "status": "confirmed", "evidence": [{"source_span_id": "span:a"}]},
                "order": {"value": 33, "status": "confirmed", "evidence": [{"source_span_id": "span:a"}]},
                "aliases": [{"value": "假别名", "status": "candidate", "evidence": []}],
            }
        ],
    }
    versioning.validate_registry(registry)
    candidate = versioning.resolve_version(label="假别名", registry=registry)
    assert candidate.matched_by == "alias"
    assert candidate.matched_status == "candidate"
    confirmed = versioning.resolve_version(label="甲", registry=registry)
    assert versioning.compare_versions(candidate, confirmed) is None


def test_registry_rejects_confirmed_label_or_alias_without_evidence():
    registry = {"registry_revision": "test", "versions": [_entry("version:a", "甲")]}
    registry["versions"][0]["label"]["evidence"] = []
    with pytest.raises(versioning.VersionRegistryError):
        versioning.validate_registry(registry)
    registry = {"registry_revision": "test", "versions": [_entry("version:a", "甲")]}
    registry["versions"][0]["aliases"] = [{"value": "甲别名", "status": "confirmed", "evidence": []}]
    with pytest.raises(versioning.VersionRegistryError):
        versioning.validate_registry(registry)


def test_registry_normalizes_numeric_and_rejects_confirmed_numeric_order_conflict():
    duplicate = {
        "registry_revision": "t",
        "versions": [_entry("version:a", "甲", numeric="3.3"), _entry("version:b", "乙", numeric="3.3.0")],
    }
    with pytest.raises(versioning.VersionRegistryError):
        versioning.validate_registry(duplicate)
    conflict = {
        "registry_revision": "t",
        "versions": [
            _entry("version:a", "甲", numeric="3.3", order=20),
            _entry("version:b", "乙", numeric="3.4", order=10),
            _entry("version:c", "丙", order=15),
        ],
    }
    with pytest.raises(versioning.VersionRegistryError):
        versioning.validate_registry(conflict)


def test_l6_rejects_unpaired_or_unregistered_snapshot_labels():
    from wiki_lint import _check_l6

    rule = {"id": "L6", "version_path": "version", "snapshot_path": "snapshot", "scope_paths": []}
    with pytest.raises(ValueError):
        _check_l6(rule, {"snapshot": {"game_version": "3.3", "game_version_label": "月之三"}}, "", {})
    with pytest.raises(ValueError):
        _check_l6(rule, {"snapshot": {"game_version_label": "不存在"}}, "", {})
    _check_l6(rule, {"snapshot": {"game_version": "3.3"}}, "", {})


def test_ingest_and_runtime_agree_on_publication_and_range():
    from app.canonical import CanonicalIndex, Fact
    from ingest import _version_applies as ingest_applies

    assert ingest_applies({"label": "月之三"}, {"game_version_label": "月之七"}) is True
    assert (
        CanonicalIndex._fact_applies(
            Fact(kind="event", text="x", version={"label": "月之三"}), {"game_version_label": "月之七"}
        )
        is True
    )
    assert ingest_applies({"valid_from_label": "月之七"}, {"game_version_label": "月之三"}) is False
    assert (
        CanonicalIndex._fact_applies(
            Fact(kind="relation", text="x", version={"valid_from_label": "月之七"}), {"game_version_label": "月之三"}
        )
        is False
    )
    assert ingest_applies({"valid_from": "3.3.0"}, {"game_version": "3.3"}) is True
    assert (
        CanonicalIndex._fact_applies(
            Fact(kind="relation", text="x", version={"valid_from": "3.3.0"}), {"game_version": "3.3"}
        )
        is True
    )


def test_ingest_observed_version_equivalence():
    from ingest import _observed_applies

    state = {"observed_at": [{"identity_version_id": "identity:x", "game_version": "3.3.0"}]}
    snapshot = {"identity_version_id": "identity:x", "game_version": "3.3"}
    assert _observed_applies(state, snapshot, "identity:x") is True


def test_index_uses_fixed_registry_snapshot():
    from app.canonical import CanonicalIndex, Fact

    old = {
        "registry_revision": "old",
        "versions": [_entry("version:a", "甲", order=1), _entry("version:b", "乙", order=2)],
    }
    new = {
        "registry_revision": "new",
        "versions": [_entry("version:a", "甲", order=3), _entry("version:b", "乙", order=2)],
    }
    index = CanonicalIndex([], registry=old)
    fact = Fact(kind="relation", text="x", version={"valid_from_label": "甲"})
    snapshot = {"game_version_label": "乙"}
    assert index.registry_revision == "old"
    assert CanonicalIndex._fact_applies(fact, snapshot, registry=index.registry) is True
    assert CanonicalIndex._fact_applies(fact, snapshot, registry=new) is False


def _entry_with_alias(version_id, label, numeric, alias, alias_status="candidate"):
    entry = _entry(version_id, label, numeric=numeric)
    entry["aliases"] = [
        {
            "value": alias,
            "status": alias_status,
            "evidence": [{"source_span_id": "span:test"}] if alias_status == "confirmed" else [],
        }
    ]
    return entry


def test_r1_candidate_alias_double_does_not_upgrade_to_confirmed():
    registry = {"registry_revision": "t", "versions": [_entry_with_alias("version:a", "甲", "3.3", "假")]}
    versioning.validate_registry(registry)
    resolved = versioning.resolve_version(numeric="3.3", label="假", registry=registry)
    assert resolved.pair_status == "matched"
    assert resolved.matched_status == "candidate"
    confirmed = versioning.resolve_version(label="甲", registry=registry)
    assert versioning.compare_versions(resolved, confirmed) is None
    assert versioning.range_contains(confirmed, lower=resolved) is None


def test_r1_candidate_numeric_does_not_upgrade_to_confirmed():
    registry = {"registry_revision": "t", "versions": [_entry("version:a", "甲", numeric=None)]}
    registry["versions"][0]["numeric"] = {"value": "3.3", "status": "candidate", "evidence": []}
    # 让 numeric 映射能解析：candidate 不需要 evidence，但 label 必须 confirmed 有 evidence
    versioning.validate_registry(registry)
    resolved = versioning.resolve_version(numeric="3.3", label="甲", registry=registry)
    assert resolved.pair_status == "matched"
    assert resolved.matched_status == "candidate"
    assert versioning.compare_versions(resolved, resolved) is None


def test_r1_unknown_alias_is_not_identity_confirmed():
    registry = {
        "registry_revision": "t",
        "versions": [_entry_with_alias("version:a", "甲", "3.3", "未知别名", alias_status="unknown")],
    }
    versioning.validate_registry(registry)
    resolved = versioning.resolve_version(label="未知别名", registry=registry)
    assert resolved.version_id == "version:a"
    assert resolved.identity_confirmed is False
    assert versioning.compare_versions(resolved, versioning.resolve_version(label="甲", registry=registry)) is None


def test_r2_unresolved_double_does_not_compare_as_numeric():
    registry = versioning.load_registry()
    unresolved = versioning.resolve_version(numeric="3.3", label="月之三", registry=registry)
    raw_numeric = versioning.resolve_version(numeric="3.3", registry=registry)
    assert unresolved.pair_status == "unresolved"
    assert versioning.compare_versions(unresolved, raw_numeric) is None
    assert versioning.compare_versions(unresolved, unresolved) is None
    assert versioning.range_contains(raw_numeric, lower=unresolved) is None


def test_r3_index_uses_deep_copy_of_registry():
    from app.canonical import CanonicalIndex, Fact

    old = {
        "registry_revision": "old",
        "versions": [_entry("version:a", "甲", order=1), _entry("version:b", "乙", order=2)],
    }
    index = CanonicalIndex([], registry=old)
    fact = Fact(kind="relation", text="x", version={"valid_from_label": "甲"})
    snapshot = {"game_version_label": "乙"}
    assert CanonicalIndex._fact_applies(fact, snapshot, registry=index.registry) is True
    old["versions"][0]["order"]["value"] = 3
    assert CanonicalIndex._fact_applies(fact, snapshot, registry=index.registry) is True


def test_r3_build_failure_does_not_recover_dynamically():
    from app.canonical import CanonicalIndex, Fact

    index = CanonicalIndex([], registry=None)
    fact = Fact(kind="relation", text="x", version={"valid_from": "3.2"})
    assert index.registry is None
    assert CanonicalIndex._fact_applies(fact, {"game_version": "3.3"}, registry=index.registry) is False


def test_r4_confirmed_evidence_requires_known_span():
    registry = {"registry_revision": "t", "versions": [_entry("version:a", "甲")]}
    with pytest.raises(versioning.VersionRegistryError):
        versioning.validate_registry(registry, known_span_ids=set())


def test_r4_alias_evidence_is_projected():
    registry = {
        "registry_revision": "t",
        "versions": [
            {
                "version_id": "version:a",
                "label": {"value": "甲", "status": "confirmed", "evidence": [{"source_span_id": "span:label"}]},
                "numeric": {"value": "3.3", "status": "confirmed", "evidence": [{"source_span_id": "span:numeric"}]},
                "order": {"value": 33, "status": "confirmed", "evidence": [{"source_span_id": "span:order"}]},
                "aliases": [{"value": "甲别名", "status": "confirmed", "evidence": [{"source_span_id": "span:alias"}]}],
            }
        ],
    }
    versioning.validate_registry(registry)
    resolved = versioning.resolve_version(label="甲别名", registry=registry)
    assert {"source_span_id": "span:alias"} in resolved.evidence
    assert {"source_span_id": "span:numeric"} in resolved.evidence
    assert resolved.order_evidence == [{"source_span_id": "span:order"}]


def test_r2_runtime_unresolved_snapshot_double_is_not_applicable():
    from app.canonical import CanonicalIndex, Fact

    fact = Fact(kind="relation", text="x", version={"valid_from": "3.2"})
    snapshot = {"game_version": "3.3", "game_version_label": "月之三"}
    assert CanonicalIndex._version_status(fact.version, snapshot) == "unresolved"
    assert CanonicalIndex._fact_applies(fact, snapshot) is False


def test_r2_ingest_and_l6_reject_unpaired_range_bound():
    from ingest import IngestError, _validate_version_scope
    from wiki_lint import _check_l6

    with pytest.raises(IngestError):
        _validate_version_scope(
            {"introduced_in": None, "label": None, "valid_from": "9.9", "valid_from_label": "不存在", "valid_to": None},
            {},
            [],
            "test",
        )
    rule = {"id": "L6", "version_path": "version", "snapshot_path": "snapshot", "scope_paths": ["relations"]}
    with pytest.raises(ValueError):
        _check_l6(
            rule,
            {"relations": [{"relation_id": "rel:x", "version": {"valid_from": "9.9", "valid_from_label": "不存在"}}]},
            "",
            {"paragraphs": []},
        )


def _registry_with_candidate_alias():
    return {
        "registry_revision": "t",
        "versions": [
            _entry_with_alias("version:a", "甲", "3.3", "候选别名"),
            _entry("version:b", "乙", "3.4"),
        ],
    }


def test_s1_candidate_single_label_cross_id_is_not_comparable():
    registry = _registry_with_candidate_alias()
    candidate = versioning.resolve_version(label="候选别名", registry=registry)
    other = versioning.resolve_version(label="乙", registry=registry)
    assert candidate.matched_status == "candidate"
    assert versioning.compare_versions(candidate, other) is None
    assert versioning.range_contains(other, lower=candidate) is None


def test_s1_candidate_single_label_endpoint_rejected():
    from ingest import IngestError, _validate_version_scope
    from wiki_lint import _check_l6

    registry = _registry_with_candidate_alias()
    with pytest.raises(IngestError):
        _validate_version_scope(
            {"introduced_in": None, "label": None, "valid_from_label": "候选别名", "valid_to": None},
            {},
            [],
            "test",
            registry=registry,
        )
    rule = {"id": "L6", "version_path": "version", "snapshot_path": "snapshot", "scope_paths": ["relations"]}
    with pytest.raises(ValueError):
        _check_l6(
            rule,
            {"relations": [{"relation_id": "rel:x", "version": {"valid_from_label": "候选别名"}}]},
            "",
            {"paragraphs": []},
        )


def test_s2_default_loader_rejects_ghost_span(tmp_path, monkeypatch):
    registry = {"registry_revision": "ghost", "versions": [_entry("version:a", "甲", numeric="3.3")]}
    registry["versions"][0]["label"]["evidence"] = [{"source_span_id": "span:ghost"}]
    registry["versions"][0]["numeric"]["evidence"] = [{"source_span_id": "span:ghost"}]
    path = tmp_path / "registry.json"
    path.write_text(json.dumps(registry, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(versioning, "DEFAULT_REGISTRY_PATH", path)
    with pytest.raises(versioning.VersionRegistryError):
        versioning.load_registry()


def test_s2_strict_load_passes_known_span(tmp_path):
    registry = {"registry_revision": "known", "versions": [_entry("version:a", "甲", numeric="3.3")]}
    path = tmp_path / "registry.json"
    path.write_text(json.dumps(registry, ensure_ascii=False), encoding="utf-8")
    loaded = versioning.load_registry(path, known_span_ids={"span:test"})
    assert loaded["registry_revision"] == "known"


def test_p2_direct_version_status_loads_registry_by_default():
    from app.canonical import CanonicalIndex

    assert CanonicalIndex._version_status({"valid_from": "3.2"}, {"game_version": "3.3"}) == "applicable"
    assert CanonicalIndex._version_status({}, {"game_version": "3.3"}) == "applicable"


def _u1_registry():
    def entry(version_id, label, numeric_value, numeric_status, order):
        span = {"source_span_id": "span:test"}
        return {
            "version_id": version_id,
            "label": {"value": label, "status": "confirmed", "evidence": [span]},
            "numeric": {
                "value": numeric_value,
                "status": numeric_status,
                "evidence": [span] if numeric_status == "confirmed" and numeric_value else [],
            },
            "order": {"value": order, "status": "confirmed", "evidence": [span]},
            "aliases": [],
        }

    return {
        "registry_revision": "u1",
        "versions": [
            entry("version:a", "甲", "3.3", "unknown", 20),
            entry("version:b", "乙", "3.4", "confirmed", 10),
            entry("version:c", "丙", None, "unknown", 15),
        ],
    }


def test_u1_unknown_numeric_mapping_does_not_control_sorting():
    registry = _u1_registry()
    versioning.validate_registry(registry)
    a = versioning.resolve_version(label="甲", registry=registry)
    b = versioning.resolve_version(label="乙", registry=registry)
    c = versioning.resolve_version(label="丙", registry=registry)
    assert versioning.compare_versions(a, b) == 1
    assert versioning.compare_versions(b, c) == -1
    assert versioning.compare_versions(a, c) == 1
    assert versioning.range_contains(b, lower=a) is False
    assert versioning.range_contains(a, lower=b) is True


def test_u1_unknown_numeric_without_order_is_unresolved():
    registry = _u1_registry()
    for item in registry["versions"]:
        item["order"] = {"value": None, "status": "unknown", "evidence": []}
    versioning.validate_registry(registry)
    a = versioning.resolve_version(label="甲", registry=registry)
    b = versioning.resolve_version(label="乙", registry=registry)
    assert versioning.compare_versions(a, b) is None
    assert versioning.range_contains(b, lower=a) is None

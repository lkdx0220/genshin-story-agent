# -*- coding: utf-8 -*-
"""共享记录适用性：B1 版本解析与三态矩阵的唯一入口。"""

from typing import Any, Dict

from app.choice_contract import APPLICABLE, EXCLUDED, UNRESOLVED, combine_all, observations_status


def version_status(version: Dict[str, Any], snapshot: Dict[str, Any], registry: Any = None) -> str:
    version = version or {}
    has_range = any(version.get(key) for key in ("valid_from", "valid_from_label", "valid_to", "valid_to_label"))
    if not has_range:
        # introduced_in / label 只表示首次公开，不是有效期过滤。
        return APPLICABLE
    if not (snapshot.get("game_version") or snapshot.get("game_version_label")):
        return UNRESOLVED
    try:
        from app.versioning import VersionRegistryError, range_contains, resolve_version

        if registry is None:
            return UNRESOLVED
        value = resolve_version(
            numeric=snapshot.get("game_version"),
            label=snapshot.get("game_version_label"),
            registry=registry,
        )
        lower = None
        upper = None
        if version.get("valid_from") or version.get("valid_from_label"):
            lower = resolve_version(
                numeric=version.get("valid_from"),
                label=version.get("valid_from_label"),
                registry=registry,
            )
        if version.get("valid_to") or version.get("valid_to_label"):
            upper = resolve_version(
                numeric=version.get("valid_to"),
                label=version.get("valid_to_label"),
                registry=registry,
            )
    except VersionRegistryError:
        return UNRESOLVED
    result = range_contains(value, lower, upper)
    if result is None:
        return UNRESOLVED
    return APPLICABLE if result else EXCLUDED


def story_status(scope: Dict[str, Any], snapshot: Dict[str, Any], story_applies) -> str:
    scope = scope or {}
    if not (scope.get("from_event_id") or scope.get("to_event_id")):
        return APPLICABLE
    return APPLICABLE if story_applies(scope, snapshot) else EXCLUDED


def _node_statuses(node: Dict[str, Any], snapshot: Dict[str, Any], registry: Any, story_applies, current: bool) -> list:
    statuses = []
    identity_id = snapshot.get("identity_version_id")
    identity_ids = node.get("identity_version_ids") or []
    if identity_id and identity_ids and identity_id not in identity_ids:
        statuses.append(EXCLUDED)
    if node.get("config_error"):
        statuses.append(EXCLUDED)
    if node.get("player_choice_missing"):
        statuses.append(UNRESOLVED)
    if current and node.get("status") in ("rejected", "superseded"):
        statuses.append(EXCLUDED)
    statuses.append(story_status(node.get("story_scope") or {}, snapshot, story_applies))
    statuses.append(version_status(node.get("version") or {}, snapshot, registry))
    if "player_choice_scope" in node:
        from app.choice_contract import choice_status

        statuses.append(choice_status(node.get("player_choice_scope"), snapshot, has_choice=True, field_present=True))
    if node.get("observed_at"):
        statuses.append(observations_status(node.get("observed_at") or [], snapshot, registry=registry))
    return statuses


def record_status(
    record: Dict[str, Any],
    snapshot: Dict[str, Any],
    registry: Any,
    story_applies,
    parent: Dict[str, Any] | None = None,
    historical: bool = False,
) -> str:
    snapshot = snapshot or {}
    statuses = _node_statuses(record, snapshot, registry, story_applies, current=not historical)
    if parent:
        statuses.extend(_node_statuses(parent, snapshot, registry, story_applies, current=False))
    return combine_all(statuses) if statuses else APPLICABLE

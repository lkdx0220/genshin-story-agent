# -*- coding: utf-8 -*-
"""B3 玩家选择与观测的统一三态适用性模块。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from app.versioning import VersionRegistryError, compare_versions, resolve_version

APPLICABLE = "applicable"
UNRESOLVED = "unresolved"
EXCLUDED = "excluded"


def combine_all(states: Sequence[str]) -> str:
    states = list(states)
    if EXCLUDED in states:
        return EXCLUDED
    if UNRESOLVED in states:
        return UNRESOLVED
    return APPLICABLE


def combine_any(states: Sequence[str]) -> str:
    states = list(states)
    if APPLICABLE in states:
        return APPLICABLE
    if UNRESOLVED in states:
        return UNRESOLVED
    return EXCLUDED


def _scope_ids(scope: Any) -> Optional[List[str]]:
    if not isinstance(scope, list):
        return None
    if any(item not in ("aether", "lumine") for item in scope):
        return None
    if len(set(scope)) != len(scope) or len(scope) > 1:
        return None
    return list(scope)


def choice_status(scope: Any, snapshot: Optional[Dict[str, Any]], *, has_choice: bool, field_present: bool) -> str:
    if not field_present:
        return APPLICABLE if not has_choice else UNRESOLVED
    normalized = _scope_ids(scope)
    if normalized is None:
        return EXCLUDED
    snapshot = snapshot or {}
    query_choice = snapshot.get("player_choice")
    if query_choice not in (None, "aether", "lumine"):
        return EXCLUDED
    if not normalized:
        return APPLICABLE
    if not query_choice:
        return UNRESOLVED
    return APPLICABLE if query_choice in normalized else EXCLUDED


def _axis_equality(observation: Dict[str, Any], snapshot: Dict[str, Any], key: str) -> str:
    observed = observation.get(key)
    queried = snapshot.get(key)
    if observed is None:
        return APPLICABLE
    if queried is None:
        return UNRESOLVED
    return APPLICABLE if observed == queried else EXCLUDED


def observation_status(observation: Dict[str, Any], snapshot: Optional[Dict[str, Any]], registry: Any = None) -> str:
    snapshot = snapshot or {}
    states = [
        _axis_equality(observation, snapshot, "identity_version_id"),
        _axis_equality(observation, snapshot, "story_event_id"),
        _axis_equality(observation, snapshot, "position"),
        _axis_equality(observation, snapshot, "player_choice"),
    ]
    obs_numeric = observation.get("game_version")
    obs_label = observation.get("game_version_label")
    snap_numeric = snapshot.get("game_version")
    snap_label = snapshot.get("game_version_label")
    if obs_numeric or obs_label:
        if not (snap_numeric or snap_label):
            states.append(UNRESOLVED)
        else:
            try:
                observed = resolve_version(numeric=obs_numeric, label=obs_label, registry=registry)
                queried = resolve_version(numeric=snap_numeric, label=snap_label, registry=registry)
                cmp = compare_versions(observed, queried)
            except VersionRegistryError:
                cmp = None
            if cmp is None:
                states.append(UNRESOLVED)
            elif cmp == 0:
                states.append(APPLICABLE)
            else:
                states.append(EXCLUDED)
    return combine_all(states)


def observations_status(
    observations: List[Dict[str, Any]], snapshot: Optional[Dict[str, Any]], registry: Any = None
) -> str:
    if not observations:
        return APPLICABLE
    return combine_any([observation_status(item, snapshot, registry=registry) for item in observations])

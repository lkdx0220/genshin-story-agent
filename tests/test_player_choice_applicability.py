# -*- coding: utf-8 -*-
"""F02: 统一三态适用性回归。"""

from app.choice_contract import (
    APPLICABLE,
    EXCLUDED,
    UNRESOLVED,
    choice_status,
    combine_all,
    combine_any,
    observation_status,
)


def test_combine_all_and_any():
    assert combine_all([APPLICABLE, APPLICABLE]) == APPLICABLE
    assert combine_all([APPLICABLE, UNRESOLVED]) == UNRESOLVED
    assert combine_all([APPLICABLE, EXCLUDED]) == EXCLUDED
    assert combine_any([EXCLUDED, UNRESOLVED]) == UNRESOLVED
    assert combine_any([EXCLUDED, EXCLUDED]) == EXCLUDED


def test_choice_status_table():
    assert choice_status([], {"player_choice": None}, has_choice=True, field_present=True) == APPLICABLE
    assert choice_status(["aether"], {}, has_choice=True, field_present=True) == UNRESOLVED
    assert choice_status(["aether"], {"player_choice": "aether"}, has_choice=True, field_present=True) == APPLICABLE
    assert choice_status(["aether"], {"player_choice": "lumine"}, has_choice=True, field_present=True) == EXCLUDED
    assert choice_status(None, {}, has_choice=False, field_present=False) == APPLICABLE
    assert choice_status(None, {}, has_choice=True, field_present=False) == UNRESOLVED
    assert choice_status(["aether", "lumine"], {}, has_choice=True, field_present=True) == EXCLUDED


def test_observation_status_examples():
    obs = {"game_version": "3.2", "player_choice": "aether"}
    assert observation_status(obs, {"game_version": "3.2"}) == UNRESOLVED
    assert observation_status(obs, {"player_choice": "aether"}) == UNRESOLVED
    assert observation_status(obs, {"game_version": "5.1", "player_choice": "aether"}) == EXCLUDED
    assert observation_status(obs, {"game_version": "3.2", "player_choice": "lumine"}) == EXCLUDED
    assert observation_status(obs, {"game_version": "3.2", "player_choice": "aether"}) == APPLICABLE

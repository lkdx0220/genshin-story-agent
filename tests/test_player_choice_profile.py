# -*- coding: utf-8 -*-
"""F07: choice profile 权威与错误隔离。"""

import json


from app import canonical


def test_required_traveler_profile_loads():
    options, error = canonical._load_player_choice_profile_options("char:traveler")
    assert error is None
    assert {item["choice_id"] for item in options} == {"aether", "lumine"}


def test_non_choice_entity_without_profile_is_allowed():
    options, error = canonical._load_player_choice_profile_options("char:other")
    assert options == []
    assert error is None


def test_missing_or_broken_required_profile_is_error(tmp_path, monkeypatch):
    monkeypatch.setattr(canonical, "REPO_ROOT", tmp_path)
    options, error = canonical._load_player_choice_profile_options("char:traveler")
    assert options == []
    assert "required choice profile missing" in error
    profile_dir = tmp_path / "wiki" / "__schema__" / "entities"
    profile_dir.mkdir(parents=True)
    path = profile_dir / "char_traveler.json"
    path.write_text(json.dumps({"entity_id": "char:traveler", "player_choice_options": []}), encoding="utf-8")
    options, error = canonical._load_player_choice_profile_options("char:traveler")
    assert options == []
    assert error is None
    path.write_text("{not json", encoding="utf-8")
    options, error = canonical._load_player_choice_profile_options("char:traveler")
    assert options == []
    assert "unreadable" in error

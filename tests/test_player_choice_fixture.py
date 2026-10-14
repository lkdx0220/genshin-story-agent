# -*- coding: utf-8 -*-
"""F01/F04: traveler fixture 整链回归。"""

from pathlib import Path

import ingest

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "tests" / "fixtures" / "traveler" / "raw"
RESPONSE = ROOT / "tests" / "fixtures" / "traveler-response.md"
SCHEMA = ROOT / "wiki" / "__schema__" / "entity-template.md"
PROMPT = ROOT / "wiki" / "__schema__" / "ingest-traveler.prompt.md"
RULES = ROOT / "lint" / "wiki-lint-rules.yaml"


def test_traveler_fixture_end_to_end(tmp_path):
    output = tmp_path / "traveler.md"
    ingest.ingest(
        RAW,
        SCHEMA,
        response_file=RESPONSE,
        provider="replay",
        expected_entity_id="char:traveler",
        prompt_path=PROMPT,
        output=output,
    )
    assert output.exists()
    bundle = ingest.load_bundle(RAW)
    passed = ingest.lint(output.read_text(encoding="utf-8"), bundle)
    assert passed == ["L1", "L2", "L3", "L4", "L5", "L6", "L7", "L8"]

    from app.canonical import CanonicalIndex, CanonicalPage

    page = CanonicalPage.from_path(output)
    index = CanonicalIndex([page])
    aether = [f for f in page.facts if "空的风" in f.text][0]
    lumine = [f for f in page.facts if "荧的岩" in f.text][0]
    common = [f for f in page.facts if "共同旅行" in f.text][0]
    assert index._fact_applies(aether, {"player_choice": "aether"}) is True
    assert index._fact_applies(aether, {"player_choice": "lumine"}) is False
    assert index._fact_applies(lumine, {"player_choice": "lumine"}) is True
    assert index._fact_applies(lumine, {"player_choice": "aether"}) is False
    assert index._fact_applies(common, {"player_choice": "aether"}) is True
    assert index._fact_applies(common, {}) is True
    assert "空的风" not in index.answer("旅行者", snapshot={"player_choice": "lumine"})
    assert "荧的岩" in index.answer("旅行者", snapshot={"player_choice": "lumine"})

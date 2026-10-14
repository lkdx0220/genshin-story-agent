# -*- coding: utf-8 -*-
"""F00: traveler prompt 必须接收完整 Schema / raw / profile / proofs。"""

from pathlib import Path

import pytest

import ingest

ROOT = Path(__file__).resolve().parents[1]


def _bundle():
    return {
        "mode": "fixture",
        "sources": [{"source_id": "source:x"}],
        "paragraphs": [{"span_id": "span:x", "quote": "B3_RAW_INPUT_SENTINEL_20261014"}],
        "choice_proofs": [{"proof_id": "proof:fixture-aether-001", "scope": ["aether"]}],
    }


def test_traveler_prompt_contains_schema_raw_profile_and_proofs():
    profile = {
        "entity_id": "char:traveler",
        "player_choice_options": [{"choice_id": "aether", "label": "空", "aliases": ["空"]}],
    }
    prompt = ingest.build_prompt(
        ROOT / "wiki" / "__schema__" / "entity-template.md",
        _bundle(),
        prompt_path=ROOT / "wiki" / "__schema__" / "ingest-traveler.prompt.md",
        profile=profile,
    )
    assert "B3_RAW_INPUT_SENTINEL_20261014" in prompt
    assert "proof:fixture-aether-001" in prompt
    assert "char:traveler" in prompt
    assert "{{SCHEMA}}" not in prompt
    assert "{{SOURCE_BUNDLE}}" not in prompt


def test_prompt_template_missing_required_token_is_rejected(tmp_path):
    template = tmp_path / "bad.prompt.md"
    template.write_text("只有规则，没有占位符", encoding="utf-8")
    with pytest.raises(ingest.IngestError):
        ingest.build_prompt(
            ROOT / "wiki" / "__schema__" / "entity-template.md",
            _bundle(),
            prompt_path=template,
            profile={"player_choice_options": [{"choice_id": "aether"}]},
        )


def test_raw_literal_token_is_not_expanded(tmp_path):
    template = tmp_path / "ok.prompt.md"
    template.write_text(
        "{{SCHEMA}}\n{{SOURCE_SPAN_FORMAT}}\n{{SOURCE_BUNDLE}}\n{{ENTITY_PROFILE}}",
        encoding="utf-8",
    )
    bundle = _bundle()
    bundle["paragraphs"][0]["quote"] = "{{SCHEMA}} literal sentinel"
    prompt = ingest.build_prompt(
        ROOT / "wiki" / "__schema__" / "entity-template.md",
        bundle,
        prompt_path=template,
        profile={"player_choice_options": [{"choice_id": "aether"}]},
    )
    assert "{{SCHEMA}} literal sentinel" in prompt

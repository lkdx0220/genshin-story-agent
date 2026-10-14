# -*- coding: utf-8 -*-
"""M01/M07: traveler 合法冲突收窄 fixture 整链。"""

from pathlib import Path

import ingest

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "tests" / "fixtures" / "traveler-conflict" / "raw"
RESPONSE = ROOT / "tests" / "fixtures" / "traveler-conflict-response.md"
SCHEMA = ROOT / "wiki" / "__schema__" / "entity-template.md"
PROMPT = ROOT / "wiki" / "__schema__" / "ingest-traveler.prompt.md"


def test_traveler_conflict_fixture_end_to_end(tmp_path):
    output = tmp_path / "traveler-conflict.md"
    ingest.ingest(
        RAW,
        SCHEMA,
        response_file=RESPONSE,
        provider="replay",
        expected_entity_id="char:traveler",
        prompt_path=PROMPT,
        output=output,
    )
    text = output.read_text(encoding="utf-8")
    assert ingest.lint(text, ingest.load_bundle(RAW)) == [f"L{i}" for i in range(1, 9)]

    from app.canonical import CanonicalIndex, CanonicalPage

    page = CanonicalPage.from_path(output)
    index = CanonicalIndex([page])
    aether = index.attribute_projection({"player_choice": "aether", "game_version": "5.0"})
    unselected = index.attribute_projection({"game_version": "5.0"})
    assert aether["char:traveler:element"]["status"] == "ambiguous"
    assert aether["char:traveler:element"]["value"] is None
    assert unselected["char:traveler:element"]["status"] == "unresolved"
    assert unselected["char:traveler:element"]["value"] is None
    assert page.data["affiliation"] == ["org:traveler"]
    assert page.data["conflicts"][0]["target_ref"] == "attribute:attr:traveler-element"


def test_common_proof_cannot_narrow_ordinary_event_public_ingest(tmp_path):
    _, bundle = ingest.ingest(
        RAW,
        SCHEMA,
        response_file=RESPONSE,
        provider="replay",
        expected_entity_id="char:traveler",
        prompt_path=PROMPT,
        output=tmp_path / "ok.md",
    )
    from app import choice_proofs

    data, _ = ingest.parse_page(RESPONSE.read_text(encoding="utf-8"))
    proofs = choice_proofs.load_choice_proofs(RAW, bundle, required=True)
    common = next(proof for proof in proofs if proof["scope"] == [])
    data["events"][0].update(
        player_choice_scope=["aether"],
        player_choice_branch={
            "choice_id": "aether",
            "kind": "source_metadata",
            "source_span_id": common["anchor_span_id"],
        },
        player_choice_proof_refs=[common["proof_id"]],
    )
    bad = tmp_path / "bad.md"
    bad.write_text(ingest.page_text(data, bundle), encoding="utf-8")
    try:
        ingest.ingest(
            RAW,
            SCHEMA,
            response_file=bad,
            provider="replay",
            expected_entity_id="char:traveler",
            prompt_path=PROMPT,
            output=tmp_path / "bad-out.md",
        )
    except ingest.IngestError:
        return
    raise AssertionError("common proof must not narrow an ordinary event")

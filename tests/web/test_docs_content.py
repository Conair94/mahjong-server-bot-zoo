"""Docs content ↔ code agreement (Spec 36 § Verification 1). No browser.

`docs_content.js` is a content-only module (template-literal plain text), so
these tests read it as text and cross-check it against the things it
documents: the seat-bot registry, the live house ruleset, and the engine's
fan-name spellings. Docs that drift from the code fail here.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from mahjong.engine.scoring import lookup_x
from mahjong.server.seat_bots import SEAT_BOTS
from mahjong.web import static_root

CONTENT_PATH = Path(static_root()) / "docs_content.js"
CONTENT = CONTENT_PATH.read_text(encoding="utf-8")

SLUGS = re.findall(r'slug: "([a-z0-9-]+)"', CONTENT)
BODIES = re.findall(r"body: `(.*?)`", CONTENT, re.S)

RULESET = json.loads(
    (Path(__file__).parents[2] / "mahjong" / "engine" / "rulesets" / "mcr-house-3fan.json")
    .read_text(encoding="utf-8")
)


def _doc_body(slug: str) -> str:
    """Body of the doc declared with `slug` (source order pairs them 1:1)."""
    return BODIES[SLUGS.index(slug)]


def test_structure_slugs_unique_and_bodies_substantial() -> None:
    assert len(SLUGS) == len(set(SLUGS)), "duplicate doc slugs"
    assert len(SLUGS) == len(BODIES), "every doc needs a body"
    assert len(SLUGS) >= 9
    for slug, body in zip(SLUGS, BODIES, strict=True):
        assert len(body) > 400, f"doc {slug!r} is a stub ({len(body)} chars)"


def test_every_selectable_bot_is_documented() -> None:
    # Docs-as-contract: adding a bot to the picker without documenting it
    # fails here (Spec 36 § Open questions — intentional).
    for bot_id in SEAT_BOTS:
        assert f'slug: "bot-{bot_id}"' in CONTENT, f"no doc for seat bot {bot_id!r}"


def test_fan_chart_covers_every_tier_in_increasing_order() -> None:
    chart = _doc_body("fan-chart")
    tiers = [1, 2, 4, 6, 8, 12, 16, 24, 32, 48, 64, 88]
    positions = []
    for value in tiers:
        heading = f"{value} FAN"
        assert heading in chart, f"fan chart missing the {heading} tier"
        positions.append(chart.index(heading))
    assert positions == sorted(positions), "fan tiers must appear in increasing value"


def test_fan_chart_uses_engine_exact_names() -> None:
    # Sentinel spellings confirmed against pymj.calculate_fan output — the
    # chart must match what the win screen prints.
    chart = _doc_body("fan-chart")
    for name in (
        "Fully Concealed Hand",
        "Concealed Hand",
        "All Chows",
        "Single Wait",
        "Self-Drawn",
        "Flower Tiles",
        "Mixed Straight",
        "Seven Pairs",
        "Thirteen Orphans",
    ):
        assert name in chart, f"fan chart missing engine fan name {name!r}"


def test_house_scoring_quotes_the_live_ruleset() -> None:
    body = _doc_body("house-scoring")
    tiers = RULESET["conversion"]["tiers"]
    # The X values for the tiers the doc's worked examples lean on.
    for fan_total in (3, 6, 9, 15):
        assert str(lookup_x(fan_total, tiers)) in body
    assert RULESET["fan_cliff"] == 3
    assert "3 FAN" in body or "3 fan" in body
    # Payout multipliers (discard 2X/X -> 4X; self-draw 2X each -> 6X).
    assert "receives 4X" in body
    assert "receives 6X" in body
    assert "pay 2X each" in body
    # False-mahjong table rule quotes the config penalty.
    assert str(RULESET["false_mahjong"]["penalty_each"]) in body


def test_house_comparison_docs_state_both_floors() -> None:
    vs_mcr = _doc_body("house-vs-mcr")
    assert "8 fan" in vs_mcr and "3 fan" in vs_mcr
    vs_riichi = _doc_body("house-vs-riichi")
    assert "furiten" in vs_riichi.lower()
    assert "3 fan" in vs_riichi


def test_bot_docs_describe_the_actual_policies() -> None:
    v0 = _doc_body("bot-v0")
    # v0's load-bearing traits: fan-awareness, always-win, always-kong, no defense.
    assert "fan-aware" in v0
    assert "kong" in v0.lower()
    assert "No defense" in v0 or "no defense" in v0
    v1 = _doc_body("bot-v1")
    # v1's load-bearing traits: counting, dead waits, push/careful/fold.
    for needle in ("live", "dead", "PUSH", "CAREFUL", "FOLD", "meld"):
        assert needle in v1, f"v1 doc missing {needle!r}"

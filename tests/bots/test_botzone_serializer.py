"""BotzoneCsmSerializer unit tests.

Spec: docs/specs/bot-runner-protocol.md § Per-turn request +
      mahjong/bots/botzone_serializer.py docstring.

Pure unit tests (no subprocess, no asyncio). The four-bot integration test
lives in `tests/adapters/test_layer5_e2e.py`.
"""

from __future__ import annotations

import json
from typing import cast

from mahjong.adapters.base import Prompt
from mahjong.bots.botzone_serializer import BotzoneCsmSerializer, action_to_botzone_string


def _claim_prompt() -> Prompt:
    return cast(
        Prompt,
        {
            "kind": "CLAIM",
            "view": {},
            "legal_actions": [{"type": "PASS"}],
            "default_action": {"type": "PASS"},
            "deadline": 0.0,
            "issued_at": 0.0,
            "context": {},
        },
    )


def _discard_prompt() -> Prompt:
    return cast(
        Prompt,
        {
            "kind": "DISCARD",
            "view": {},
            "legal_actions": [{"type": "PLAY", "tile": "W5"}],
            "default_action": {"type": "PLAY", "tile": "W5"},
            "deadline": 0.0,
            "issued_at": 0.0,
            "context": {},
        },
    )


def test_init_request_format() -> None:
    s = BotzoneCsmSerializer(seat=2, round_index=0)
    s.on_observe({"event": "HEADER"}, {})
    envelope = json.loads(s.on_decide(_claim_prompt()))
    assert envelope["requests"][0] == "0 2 0"


def test_deal_request_carries_only_my_concealed() -> None:
    s = BotzoneCsmSerializer(seat=1)
    deal_event = {
        "event": "DEAL",
        "concealed": [
            ["W1"] * 13,
            ["B" + str(i + 1) for i in range(9)] + ["T1", "T2", "T3", "T4"],
            ["W2"] * 13,
            ["W3"] * 13,
        ],
    }
    s.on_observe({"event": "HEADER"}, {})
    s.on_observe(deal_event, {})
    envelope = json.loads(s.on_decide(_claim_prompt()))
    deal_line = envelope["requests"][1]
    assert deal_line.startswith("1 0 0 0 0 ")
    tokens = deal_line.split()
    assert tokens[5:] == [
        "B1",
        "B2",
        "B3",
        "B4",
        "B5",
        "B6",
        "B7",
        "B8",
        "B9",
        "T1",
        "T2",
        "T3",
        "T4",
    ]


def test_my_draw_becomes_typed_line_2() -> None:
    s = BotzoneCsmSerializer(seat=0)
    s.on_observe({"event": "HEADER"}, {})
    s.on_observe(
        {"event": "DEAL", "concealed": [["W1"] * 13 for _ in range(4)]},
        {},
    )
    s.on_observe({"event": "DRAW", "seat": 0, "tile": "B7"}, {})
    envelope = json.loads(s.on_decide(_discard_prompt()))
    assert envelope["requests"][2] == "2 B7"


def test_others_draw_filtered() -> None:
    s = BotzoneCsmSerializer(seat=0)
    s.on_observe({"event": "HEADER"}, {})
    s.on_observe(
        {"event": "DEAL", "concealed": [["W1"] * 13 for _ in range(4)]},
        {},
    )
    s.on_observe({"event": "DRAW", "seat": 1, "tile": "B7"}, {})
    envelope = json.loads(s.on_decide(_claim_prompt()))
    # Just init + deal — no draw line for someone else's draw.
    assert len(envelope["requests"]) == 2


def test_discard_visible_to_all_seats() -> None:
    s = BotzoneCsmSerializer(seat=2)
    s.on_observe({"event": "HEADER"}, {})
    s.on_observe(
        {"event": "DEAL", "concealed": [["W1"] * 13 for _ in range(4)]},
        {},
    )
    s.on_observe({"event": "DISCARD", "seat": 0, "tile": "B7"}, {})
    envelope = json.loads(s.on_decide(_claim_prompt()))
    assert envelope["requests"][2] == "3 0 PLAY B7"


def test_claim_decision_from_other_seat() -> None:
    s = BotzoneCsmSerializer(seat=3)
    s.on_observe({"event": "HEADER"}, {})
    s.on_observe(
        {"event": "DEAL", "concealed": [["W1"] * 13 for _ in range(4)]},
        {},
    )
    s.on_observe(
        {"event": "CLAIM_DECISION", "seat": 1, "decision": "PENG", "tile": "B7"},
        {},
    )
    envelope = json.loads(s.on_decide(_claim_prompt()))
    assert envelope["requests"][-1] == "3 1 PENG B7"


def test_chi_decision_uses_middle_tile_format() -> None:
    s = BotzoneCsmSerializer(seat=2)
    s.on_observe({"event": "HEADER"}, {})
    s.on_observe(
        {"event": "DEAL", "concealed": [["W1"] * 13 for _ in range(4)]},
        {},
    )
    s.on_observe(
        {
            "event": "CLAIM_DECISION",
            "seat": 1,
            "decision": "CHI",
            "chi_tiles": ["W3", "W4", "W5"],
            "tile": "W3",
        },
        {},
    )
    envelope = json.loads(s.on_decide(_claim_prompt()))
    # Botzone CHI emits middle twice; matches botzone_export.py convention.
    assert envelope["requests"][-1] == "3 1 CHI W4 W4"


def test_response_invariant_holds_after_decide() -> None:
    s = BotzoneCsmSerializer(seat=0)
    s.on_observe({"event": "HEADER"}, {})
    s.on_observe(
        {"event": "DEAL", "concealed": [["W1"] * 13 for _ in range(4)]},
        {},
    )
    s.on_observe({"event": "DRAW", "seat": 0, "tile": "W7"}, {})
    envelope_str = s.on_decide(_discard_prompt())
    env = json.loads(envelope_str)
    assert len(env["requests"]) == len(env["responses"]) + 1


def test_record_response_lands_in_next_envelope() -> None:
    s = BotzoneCsmSerializer(seat=0)
    s.on_observe({"event": "HEADER"}, {})
    s.on_observe(
        {"event": "DEAL", "concealed": [["W1"] * 13 for _ in range(4)]},
        {},
    )
    s.on_observe({"event": "DRAW", "seat": 0, "tile": "W7"}, {})
    s.on_decide(_discard_prompt())
    s.record_response({"type": "PLAY", "tile": "W7"})
    # Pretend another draw came in.
    s.on_observe({"event": "DRAW", "seat": 0, "tile": "W8"}, {})
    env = json.loads(s.on_decide(_discard_prompt()))
    assert env["responses"][-1] == "PLAY W7"


# --- action_to_botzone_string ---


def test_action_to_string_pass() -> None:
    assert action_to_botzone_string({"type": "PASS"}) == "PASS"


def test_action_to_string_play() -> None:
    assert action_to_botzone_string({"type": "PLAY", "tile": "W3"}) == "PLAY W3"


def test_action_to_string_peng() -> None:
    assert action_to_botzone_string({"type": "PENG", "tile": "B5"}) == "PENG B5"


def test_action_to_string_chi() -> None:
    assert action_to_botzone_string({"type": "CHI", "tiles": ["W3", "W4", "W5"]}) == "CHI W3 W4"


def test_action_to_string_gang_exposed() -> None:
    assert action_to_botzone_string({"type": "GANG", "tile": "B7", "kind": "EXPOSED"}) == "GANG B7"


def test_action_to_string_gang_added_is_bugang() -> None:
    assert action_to_botzone_string({"type": "GANG", "tile": "W2", "kind": "ADDED"}) == "BUGANG W2"


def test_action_to_string_hu() -> None:
    assert action_to_botzone_string({"type": "HU"}) == "HU"

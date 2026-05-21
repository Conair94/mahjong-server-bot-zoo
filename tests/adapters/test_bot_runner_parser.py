"""Action-string grammar tests for BotRunnerAdapter.

Pure parser unit tests (no subprocess, no asyncio). Split from
`test_bot_runner.py` so the asyncio file-level marker doesn't warn on these.
"""

from __future__ import annotations

from typing import Any, cast

import pytest

from mahjong.adapters.base import Prompt
from mahjong.adapters.bot_runner import _ParseError, parse_action_string


def _prompt(kind: str = "DISCARD") -> Prompt:
    pass_action: dict[str, Any] = {"type": "PASS"}
    return cast(
        Prompt,
        {
            "kind": kind,
            "view": {},
            "legal_actions": [pass_action],
            "default_action": pass_action,
            "deadline": 0.0,
            "issued_at": 0.0,
            "context": {},
        },
    )


def test_parse_pass() -> None:
    assert parse_action_string("PASS", _prompt()) == {"type": "PASS"}


def test_parse_play() -> None:
    assert parse_action_string("PLAY W3", _prompt()) == {"type": "PLAY", "tile": "W3"}


def test_parse_peng() -> None:
    assert parse_action_string("PENG B5", _prompt(kind="CLAIM")) == {
        "type": "PENG",
        "tile": "B5",
    }


def test_parse_chi_reconstructs_three_tiles() -> None:
    # Botzone CHI: <claimed> <middle>. Middle=W4 → [W3, W4, W5].
    result = parse_action_string("CHI W3 W4", _prompt(kind="CLAIM"))
    assert result == {"type": "CHI", "tiles": ["W3", "W4", "W5"]}


def test_parse_gang_in_claim_is_exposed() -> None:
    assert parse_action_string("GANG B7", _prompt(kind="CLAIM")) == {
        "type": "GANG",
        "tile": "B7",
        "kind": "EXPOSED",
    }


def test_parse_gang_in_discard_is_concealed() -> None:
    assert parse_action_string("GANG B7", _prompt(kind="DISCARD")) == {
        "type": "GANG",
        "tile": "B7",
        "kind": "CONCEALED",
    }


def test_parse_bugang_is_added() -> None:
    assert parse_action_string("BUGANG W2", _prompt(kind="DISCARD")) == {
        "type": "GANG",
        "tile": "W2",
        "kind": "ADDED",
    }


def test_parse_hu() -> None:
    assert parse_action_string("HU", _prompt(kind="CLAIM")) == {"type": "HU"}


def test_parse_unknown_tag_raises() -> None:
    with pytest.raises(_ParseError):
        parse_action_string("NOPE W1", _prompt())


def test_parse_empty_raises() -> None:
    with pytest.raises(_ParseError):
        parse_action_string("", _prompt())


def test_parse_chi_out_of_range_raises() -> None:
    # Middle=W1 → W0 doesn't exist.
    with pytest.raises(_ParseError):
        parse_action_string("CHI W1 W1", _prompt(kind="CLAIM"))


def test_parse_chi_non_numbered_middle_raises() -> None:
    with pytest.raises(_ParseError):
        parse_action_string("CHI J1 J2", _prompt(kind="CLAIM"))

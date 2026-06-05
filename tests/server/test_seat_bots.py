"""Unit tests for the in-process seat-bot registry (mahjong.server.seat_bots).

The registry is the single source of truth for the create-table bot picker:
which bots exist, how to build their adapters, and the metadata advertised in
``HELLO.bots``.
"""

from __future__ import annotations

import pytest

from mahjong.server.seat_bots import (
    DEFAULT_BOT_ID,
    SEAT_BOTS,
    available_bots_wire,
    build_bot_adapter,
    is_known_bot,
)


def test_v0_is_registered_and_default() -> None:
    assert "v0" in SEAT_BOTS
    assert is_known_bot("v0")
    assert DEFAULT_BOT_ID == "v0"


def test_unknown_bot_is_not_known() -> None:
    assert not is_known_bot("nope")
    assert not is_known_bot("")


def test_build_bot_adapter_returns_fresh_bot_seat_adapter() -> None:
    a = build_bot_adapter("v0")
    b = build_bot_adapter("v0")
    assert a is not b  # fresh instance per hand
    assert a.kind == "bot"
    assert a.identity["bot_id"] == "v0"


def test_build_unknown_bot_raises() -> None:
    with pytest.raises(KeyError):
        build_bot_adapter("nope")


def test_available_bots_wire_shape() -> None:
    bots = available_bots_wire()
    assert isinstance(bots, list)
    assert len(bots) == len(SEAT_BOTS)
    v0 = next(b for b in bots if b["bot_id"] == "v0")
    assert set(v0.keys()) == {"bot_id", "label", "description"}
    assert v0["label"]  # non-empty display text
    assert v0["description"]

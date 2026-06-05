"""Step 8.7.a — Schema parsing for ``CREATE_TABLE.seats[]``.

Verification fixtures 1-7 from
``docs/specs/multi-human-seats.md § Verification fixtures``:

1. Default composition (no ``seats`` field) → ``(human, bot, bot, bot)``.
2. All-human composition → 4 humans.
3. 2H+2B composition stored in seat-index order.
4. All-bot rejected (at least one human required).
5. Wrong-length rejected.
6. Unknown ``kind`` rejected.
7. Reserved-field rejected (``user_id`` / ``bot_id`` forbidden in v1).

The first block tests the pure parser; the second block exercises the
parser through the real WebSocket handler in ``MultiTableOrchestrator``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest
import websockets

from mahjong.engine.rulesets import MANIFEST
from mahjong.server.orchestrator import MultiTableOrchestrator
from mahjong.server.seats import (
    DEFAULT_COMPOSITION,
    SeatComposition,
    SeatsParseError,
    parse_seats_from_wire,
)

MCR_REF: dict[str, Any] = {
    "id": "mcr-2006",
    "version": 1,
    "config_hash": MANIFEST["mcr-2006"],
}
_SC_SEED = 88_888
_SC_SERVER_INFO: dict[str, Any] = {"version": "sc-test", "git_sha": "test", "host": "test"}


# ---------------------------------------------------------------------------
# Fixture 1 — Default composition (no ``seats`` field)
# ---------------------------------------------------------------------------


def test_fixture_1_default_composition_when_seats_omitted() -> None:
    result = parse_seats_from_wire(None)
    assert result == DEFAULT_COMPOSITION
    assert result == (
        SeatComposition("human"),
        SeatComposition("bot"),
        SeatComposition("bot"),
        SeatComposition("bot"),
    )


# ---------------------------------------------------------------------------
# Fixture 2 — All-human composition
# ---------------------------------------------------------------------------


def test_fixture_2_all_human_composition() -> None:
    result = parse_seats_from_wire([{"kind": "human"}] * 4)
    assert result == tuple(SeatComposition("human") for _ in range(4))


# ---------------------------------------------------------------------------
# Fixture 3 — 2H + 2B composition, order-preserving
# ---------------------------------------------------------------------------


def test_fixture_3_two_humans_two_bots_preserves_seat_index_order() -> None:
    result = parse_seats_from_wire(
        [
            {"kind": "human"},
            {"kind": "human"},
            {"kind": "bot"},
            {"kind": "bot"},
        ]
    )
    assert result == (
        SeatComposition("human"),
        SeatComposition("human"),
        SeatComposition("bot"),
        SeatComposition("bot"),
    )


def test_fixture_3_interleaved_humans_and_bots() -> None:
    """Composition is order-sensitive: human-at-2 differs from human-at-0."""
    result = parse_seats_from_wire(
        [
            {"kind": "bot"},
            {"kind": "human"},
            {"kind": "bot"},
            {"kind": "human"},
        ]
    )
    assert result == (
        SeatComposition("bot"),
        SeatComposition("human"),
        SeatComposition("bot"),
        SeatComposition("human"),
    )


# ---------------------------------------------------------------------------
# Fixture 4 — All-bot rejection
# ---------------------------------------------------------------------------


def test_fixture_4_all_bot_composition_rejected() -> None:
    with pytest.raises(SeatsParseError, match="at least one 'human'"):
        parse_seats_from_wire([{"kind": "bot"}] * 4)


# ---------------------------------------------------------------------------
# Fixture 5 — Wrong-length rejection
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("length", [0, 1, 2, 3, 5, 7])
def test_fixture_5_wrong_length_rejected(length: int) -> None:
    with pytest.raises(SeatsParseError, match="exactly 4 entries"):
        parse_seats_from_wire([{"kind": "human"}] * length)


# ---------------------------------------------------------------------------
# Fixture 6 — Unknown-kind rejection
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_kind",
    ["alien", "canned", "spectator", "Human", "BOT", "", None, 0, 42],
)
def test_fixture_6_unknown_kind_rejected(bad_kind: object) -> None:
    seats = [
        {"kind": "human"},
        {"kind": bad_kind},
        {"kind": "bot"},
        {"kind": "bot"},
    ]
    with pytest.raises(SeatsParseError, match="must be 'human' or 'bot'"):
        parse_seats_from_wire(seats)


def test_fixture_6_missing_kind_rejected() -> None:
    """A seat entry with no ``kind`` field at all."""
    seats = [
        {"kind": "human"},
        {},
        {"kind": "bot"},
        {"kind": "bot"},
    ]
    with pytest.raises(SeatsParseError, match="must be 'human' or 'bot'"):
        parse_seats_from_wire(seats)


# ---------------------------------------------------------------------------
# Fixture 7 — Reserved-field rejection
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "extra_field", ["user_id", "bot_id", "display", "name", "ready", "kind_extra"]
)
def test_fixture_7_reserved_field_rejected(extra_field: str) -> None:
    seats = [
        {"kind": "human", extra_field: "anything"},
        {"kind": "human"},
        {"kind": "bot"},
        {"kind": "bot"},
    ]
    with pytest.raises(SeatsParseError, match="forbidden field"):
        parse_seats_from_wire(seats)


# ---------------------------------------------------------------------------
# bot_id selection — a bot seat may name which in-process bot fills it.
# ---------------------------------------------------------------------------


def test_bot_seat_accepts_known_bot_id() -> None:
    result = parse_seats_from_wire(
        [
            {"kind": "human"},
            {"kind": "bot", "bot_id": "v0"},
            {"kind": "bot"},
            {"kind": "bot"},
        ]
    )
    assert result[1] == SeatComposition("bot", bot_id="v0")


def test_bot_seat_without_bot_id_defaults_to_none() -> None:
    """Omitted bot_id stays None at parse time; resolved to the default bot
    only at adapter-build time."""
    result = parse_seats_from_wire([{"kind": "human"}] + [{"kind": "bot"}] * 3)
    assert all(sc.bot_id is None for sc in result[1:])


@pytest.mark.parametrize("bad_bot_id", ["nope", "b_rule_v1", "", 42, True, []])
def test_bot_seat_unknown_bot_id_rejected(bad_bot_id: object) -> None:
    seats = [
        {"kind": "human"},
        {"kind": "bot", "bot_id": bad_bot_id},
        {"kind": "bot"},
        {"kind": "bot"},
    ]
    with pytest.raises(SeatsParseError, match="not a known bot"):
        parse_seats_from_wire(seats)


def test_human_seat_with_bot_id_rejected() -> None:
    """bot_id is forbidden on human seats (still open-lobby)."""
    seats = [
        {"kind": "human", "bot_id": "v0"},
        {"kind": "human"},
        {"kind": "bot"},
        {"kind": "bot"},
    ]
    with pytest.raises(SeatsParseError, match="forbidden field"):
        parse_seats_from_wire(seats)


# ---------------------------------------------------------------------------
# Edge cases (not numbered fixtures but worth pinning)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("non_list", [{"a": 1}, "string", 42, 3.14, True])
def test_seats_must_be_array(non_list: object) -> None:
    with pytest.raises(SeatsParseError, match="must be an array"):
        parse_seats_from_wire(non_list)


def test_seat_entry_must_be_object() -> None:
    seats = [{"kind": "human"}, "not-an-object", {"kind": "bot"}, {"kind": "bot"}]
    with pytest.raises(SeatsParseError, match=r"seats\[1\] must be an object"):
        parse_seats_from_wire(seats)


def test_default_composition_is_tuple_of_four() -> None:
    assert isinstance(DEFAULT_COMPOSITION, tuple)
    assert len(DEFAULT_COMPOSITION) == 4
    assert DEFAULT_COMPOSITION[0].kind == "human"
    assert all(sc.kind == "bot" for sc in DEFAULT_COMPOSITION[1:])


# ---------------------------------------------------------------------------
# Wire-handler integration — the parser hooked into _handle_create_table.
# These exercise the real ``MultiTableOrchestrator`` over a loopback socket.
# ---------------------------------------------------------------------------


def _make_orch(tmp_path: Path) -> MultiTableOrchestrator:
    return MultiTableOrchestrator(
        host="127.0.0.1",
        port=0,
        data_dir=tmp_path,
        ruleset=MCR_REF,
        seed=_SC_SEED,
        server_info=_SC_SERVER_INFO,
        between_hand_pause_seconds=0.05,
    )


async def _connect(url: str) -> Any:
    ws = await websockets.connect(url, subprotocols=["mahjong-v1"])
    hello = json.loads(cast(str, await ws.recv()))
    assert hello["kind"] == "HELLO", hello
    return ws


async def _send_create_table(ws: Any, **extra: Any) -> dict[str, Any]:
    """Send a CREATE_TABLE; return the next received frame (TABLE_CREATED or ERROR)."""
    msg: dict[str, Any] = {"kind": "CREATE_TABLE", "ruleset": "mcr-2006", **extra}
    await ws.send(json.dumps(msg))
    return cast(dict[str, Any], json.loads(cast(str, await ws.recv())))


@pytest.mark.asyncio
async def test_wire_default_composition_accepted(tmp_path: Path) -> None:
    """CREATE_TABLE with no ``seats`` field → table created; handle stores DEFAULT_COMPOSITION."""
    orch = _make_orch(tmp_path)
    await orch.start()
    try:
        url = f"ws://127.0.0.1:{orch.port}"
        async with await _connect(url) as ws:
            resp = await _send_create_table(ws)
            assert resp["kind"] == "TABLE_CREATED", resp
            table_id = str(resp["table_id"])
            handle = orch.registry.get_table(table_id)
            assert handle.seats == DEFAULT_COMPOSITION
    finally:
        await orch.close()


@pytest.mark.asyncio
async def test_hello_advertises_available_bots(tmp_path: Path) -> None:
    """HELLO carries the selectable-bot menu for the create-table picker."""
    orch = _make_orch(tmp_path)
    await orch.start()
    try:
        ws = await websockets.connect(
            f"ws://127.0.0.1:{orch.port}", subprotocols=["mahjong-v1"]
        )
        try:
            hello = json.loads(cast(str, await ws.recv()))
            assert hello["kind"] == "HELLO", hello
            ids = {b["bot_id"] for b in hello["bots"]}
            assert "v0" in ids
        finally:
            await ws.close()
    finally:
        await orch.close()


@pytest.mark.asyncio
async def test_wire_bot_seat_with_bot_id_stored_on_handle(tmp_path: Path) -> None:
    """A bot seat naming a known bot_id is parsed and stored, and surfaces in
    the seat summary."""
    orch = _make_orch(tmp_path)
    await orch.start()
    try:
        url = f"ws://127.0.0.1:{orch.port}"
        async with await _connect(url) as ws:
            resp = await _send_create_table(
                ws,
                seats=[
                    {"kind": "human"},
                    {"kind": "bot", "bot_id": "v0"},
                    {"kind": "bot"},
                    {"kind": "bot"},
                ],
            )
            assert resp["kind"] == "TABLE_CREATED", resp
            handle = orch.registry.get_table(str(resp["table_id"]))
            assert handle.seats[1] == SeatComposition("bot", bot_id="v0")
            # Summary resolves the unset bot seats to the default bot.
            summary = handle.summary()
            assert summary.seats[1].bot_id == "v0"
            assert summary.seats[2].bot_id == "v0"  # unset → default
    finally:
        await orch.close()


@pytest.mark.asyncio
async def test_wire_two_humans_two_bots_accepted(tmp_path: Path) -> None:
    """2H+2B composition is parsed and stored on the table handle."""
    orch = _make_orch(tmp_path)
    await orch.start()
    try:
        url = f"ws://127.0.0.1:{orch.port}"
        async with await _connect(url) as ws:
            resp = await _send_create_table(
                ws,
                seats=[
                    {"kind": "human"},
                    {"kind": "human"},
                    {"kind": "bot"},
                    {"kind": "bot"},
                ],
            )
            assert resp["kind"] == "TABLE_CREATED", resp
            handle = orch.registry.get_table(str(resp["table_id"]))
            assert handle.seats == (
                SeatComposition("human"),
                SeatComposition("human"),
                SeatComposition("bot"),
                SeatComposition("bot"),
            )
    finally:
        await orch.close()


@pytest.mark.parametrize(
    "bad_seats",
    [
        [{"kind": "bot"}] * 4,  # fixture 4: all-bot
        [{"kind": "human"}] * 3,  # fixture 5: wrong length
        [{"kind": "human"}] * 5,  # fixture 5: wrong length
        [{"kind": "alien"}, {"kind": "human"}, {"kind": "bot"}, {"kind": "bot"}],  # 6
        [
            {"kind": "human", "user_id": "u_alice"},
            {"kind": "human"},
            {"kind": "bot"},
            {"kind": "bot"},
        ],  # fixture 7
        [
            {"kind": "human"},
            {"kind": "bot", "bot_id": "b_rule_v1"},
            {"kind": "bot"},
            {"kind": "bot"},
        ],  # fixture 7 — bot_id forbidden
        "not-a-list",  # malformed top-level
    ],
)
@pytest.mark.asyncio
async def test_wire_malformed_seats_returns_framing_error(tmp_path: Path, bad_seats: Any) -> None:
    """Every shape rejected by parse_seats_from_wire returns ERROR {code:'framing'}."""
    orch = _make_orch(tmp_path)
    await orch.start()
    try:
        url = f"ws://127.0.0.1:{orch.port}"
        async with await _connect(url) as ws:
            resp = await _send_create_table(ws, seats=bad_seats)
            assert resp["kind"] == "ERROR", resp
            assert resp["code"] == "framing", resp
            assert "message" in resp, resp
            # Registry must not have allocated a table.
            assert orch.registry.list_tables() == []
    finally:
        await orch.close()

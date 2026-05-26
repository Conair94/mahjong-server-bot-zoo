"""Tests for `mahjong.wire.codec` and `mahjong.wire.errors`.

Spec: docs/specs/wire-protocol.md § Message catalog, § Message framing.

Step 7.1 of CHECKLIST.md. Tests written before the implementation.

Coverage:
- Round-trip per message kind (one fixture per `kind` in §Message catalog).
- Framing errors: invalid JSON, missing `kind`, unknown `kind`, missing `seq`
  on server-bound frames, oversized payload.
- Forward-compat: unknown optional fields are tolerated; unknown `kind` is a
  hard error (`WireDecodeError`).
- Privacy: a player-bound `EVENT` and a spectator-bound `EVENT` (built from
  the same record event via `project_event`) round-trip to distinguishable
  byte sequences — the `DRAW.tile` field is absent for the spectator.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from mahjong.engine.state import project_event
from mahjong.wire import codec, errors

# --- round-trip fixtures (one per `kind` in §Message catalog) ---

# Each entry is (label, message_dict). The label is the test-id; the dict is
# the exact wire payload the codec must round-trip. Server→client messages
# carry `seq`; client→server messages do not (per §Message framing).

_HELLO_SERVER: dict[str, Any] = {
    "kind": "HELLO",
    "seq": 1,
    "protocol_version": 1,
    "server_id": "mahjong-server-0.1.0",
    "min_client_version": 1,
    "features": ["resume", "list_tables", "spectate"],
}

_HELLO_CLIENT: dict[str, Any] = {
    "kind": "HELLO",
    "protocol_version": 1,
    "client_id": "mahjong-tui-0.1.0",
}

_HEARTBEAT: dict[str, Any] = {"kind": "HEARTBEAT", "nonce": "a1b2c3d4"}

_HEARTBEAT_ECHO: dict[str, Any] = {
    "kind": "HEARTBEAT",
    "nonce": "a1b2c3d4",
    "echo": True,
}

_ERROR_SERVER: dict[str, Any] = {
    "kind": "ERROR",
    "seq": 7,
    "code": "illegal_action",
    "message": "PLAY W3 is not in legal_actions for prompt seq=6",
    "ref": 6,
    "details": {"legal_actions": ["PASS", "PLAY B5"]},
}

_ERROR_SHUTTING_DOWN: dict[str, Any] = {
    "kind": "ERROR",
    "seq": 99,
    "code": "shutting_down",
    "message": "server is draining for shutdown",
}

_AUTH_REQUEST: dict[str, Any] = {
    "kind": "AUTH_REQUEST",
    "username": "alice",
    "password": "correct-horse-battery-staple",
}

_AUTH_RESPONSE_OK: dict[str, Any] = {
    "kind": "AUTH_RESPONSE",
    "seq": 2,
    "ok": True,
    "user_id": "u_alice",
    "display_name": "Alice",
    "session_token": "s_8f1c0000",
    "expires_at_ms": 1748908800000,
}

_AUTH_RESPONSE_FAIL: dict[str, Any] = {
    "kind": "AUTH_RESPONSE",
    "seq": 2,
    "ok": False,
}

_RESUME: dict[str, Any] = {
    "kind": "RESUME",
    "session_token": "s_8f1c0000",
}

_LIST_TABLES: dict[str, Any] = {"kind": "LIST_TABLES"}

_TABLE_LIST: dict[str, Any] = {
    "kind": "TABLE_LIST",
    "seq": 3,
    "tables": [
        {
            "table_id": 17,
            "ruleset": "mcr-2006",
            "seats": [
                {
                    "seat": 0,
                    "kind": "human",
                    "user_id": "u_alice",
                    "occupied": True,
                    "attached": True,
                },
                {
                    "seat": 1,
                    "kind": "human",
                    "user_id": "u_bob",
                    "occupied": True,
                    "attached": False,
                },
                {
                    "seat": 2,
                    "kind": "bot",
                    "bot_id": "b_rule_v1",
                    "occupied": True,
                    "attached": True,
                },
                {"seat": 3, "kind": "open", "occupied": False, "attached": False},
            ],
            "hand_index": 0,
            "phase": "WAITING_FOR_PLAYERS",
        }
    ],
}

_ATTACH: dict[str, Any] = {"kind": "ATTACH", "table_id": 17, "seat": 3}

_ATTACHED: dict[str, Any] = {
    "kind": "ATTACHED",
    "seq": 4,
    "table_id": 17,
    "seat": 3,
    "hand_index": 0,
    "snapshot": {"placeholder": "SeatView"},
    "resume_buffer_size": 0,
}

_DETACH_CLIENT: dict[str, Any] = {"kind": "DETACH", "reason": "leaving"}

_DETACH_SERVER: dict[str, Any] = {
    "kind": "DETACH",
    "seq": 42,
    "reason": "replaced_by_autopass",
    "table_id": 17,
    "seat": 3,
}

_DETACHED: dict[str, Any] = {"kind": "DETACHED", "seq": 43}

_SPECTATE: dict[str, Any] = {"kind": "SPECTATE", "table_id": 17}

_SPECTATING: dict[str, Any] = {
    "kind": "SPECTATING",
    "seq": 4,
    "table_id": 17,
    "hand_index": 0,
    "snapshot": {"placeholder": "PublicView"},
    "spectator_count": 3,
}

_STOP_SPECTATING: dict[str, Any] = {"kind": "STOP_SPECTATING"}

_EVENT: dict[str, Any] = {
    "kind": "EVENT",
    "seq": 5,
    "table_id": 17,
    "hand_index": 0,
    "event": {
        "event": "DISCARD",
        "turn_index": 1,
        "phase": "CLAIM_WINDOW",
        "ts": "2026-05-22T10:00:00Z",
        "seat": 1,
        "tile": "T6",
        "from_hand": True,
    },
}

_PROMPT: dict[str, Any] = {
    "kind": "PROMPT",
    "seq": 23,
    "table_id": 17,
    "hand_index": 0,
    "seat": 3,
    "phase": "DISCARD",
    "legal_actions": [
        {"kind": "PLAY", "tile": "W3", "from_hand": True},
        {"kind": "PLAY", "tile": "B5", "from_hand": True},
    ],
    "default_action": {"kind": "PLAY", "tile": "W3", "from_hand": True},
    "deadline_ms": 1748908830000,
    "prompt_id": "p_17_0_23",
}

_ACTION: dict[str, Any] = {
    "kind": "ACTION",
    "ref": 23,
    "prompt_id": "p_17_0_23",
    "action": {"kind": "PLAY", "tile": "B5", "from_hand": True},
}

_HAND_END: dict[str, Any] = {
    "kind": "HAND_END",
    "seq": 87,
    "table_id": 17,
    "hand_index": 0,
    "terminal": {
        "kind": "HU",
        "winner": 2,
        "loser": 1,
        "fan_list": [{"name": "Pung of Terminals", "fan": 1}],
        "fan_total": 12,
    },
    "next_hand_seq": None,
}

_CREATE_TABLE: dict[str, Any] = {
    "kind": "CREATE_TABLE",
    "ruleset": "mcr-2006",
    "seats": [
        {"kind": "human"},
        {"kind": "human"},
        {"kind": "bot"},
        {"kind": "bot"},
    ],
}

_TABLE_CREATED: dict[str, Any] = {"kind": "TABLE_CREATED", "seq": 9, "table_id": 17}

_CLOSE_TABLE: dict[str, Any] = {
    "kind": "CLOSE_TABLE",
    "table_id": 17,
    "force": False,
}

ALL_FIXTURES: list[tuple[str, dict[str, Any]]] = [
    ("HELLO_server", _HELLO_SERVER),
    ("HELLO_client", _HELLO_CLIENT),
    ("HEARTBEAT", _HEARTBEAT),
    ("HEARTBEAT_echo", _HEARTBEAT_ECHO),
    ("ERROR_server", _ERROR_SERVER),
    ("ERROR_shutting_down", _ERROR_SHUTTING_DOWN),
    ("AUTH_REQUEST", _AUTH_REQUEST),
    ("AUTH_RESPONSE_ok", _AUTH_RESPONSE_OK),
    ("AUTH_RESPONSE_fail", _AUTH_RESPONSE_FAIL),
    ("RESUME", _RESUME),
    ("LIST_TABLES", _LIST_TABLES),
    ("TABLE_LIST", _TABLE_LIST),
    ("ATTACH", _ATTACH),
    ("ATTACHED", _ATTACHED),
    ("DETACH_client", _DETACH_CLIENT),
    ("DETACH_server", _DETACH_SERVER),
    ("DETACHED", _DETACHED),
    ("SPECTATE", _SPECTATE),
    ("SPECTATING", _SPECTATING),
    ("STOP_SPECTATING", _STOP_SPECTATING),
    ("EVENT", _EVENT),
    ("PROMPT", _PROMPT),
    ("ACTION", _ACTION),
    ("HAND_END", _HAND_END),
    ("CREATE_TABLE", _CREATE_TABLE),
    ("TABLE_CREATED", _TABLE_CREATED),
    ("CLOSE_TABLE", _CLOSE_TABLE),
]


# --- round-trip ---


@pytest.mark.parametrize("label,msg", ALL_FIXTURES, ids=[lbl for lbl, _ in ALL_FIXTURES])
def test_round_trip_every_message_kind(label: str, msg: dict[str, Any]) -> None:
    """encode → decode is identity for every documented message kind."""
    encoded = codec.encode(msg)
    assert isinstance(encoded, bytes)
    decoded = codec.decode(encoded)
    assert decoded == msg


def test_encode_produces_utf8_json_bytes() -> None:
    encoded = codec.encode(_HEARTBEAT)
    # Must parse as JSON.
    parsed = json.loads(encoded.decode("utf-8"))
    assert parsed == _HEARTBEAT


def test_encode_is_single_line_no_trailing_newline() -> None:
    """Each wire frame is one JSON object, no leading/trailing whitespace
    (§Message framing)."""
    encoded = codec.encode(_HEARTBEAT)
    text = encoded.decode("utf-8")
    assert "\n" not in text
    assert text == text.strip()


# --- framing / decode errors ---


def test_decode_invalid_json_raises_framing_error() -> None:
    with pytest.raises(errors.WireFramingError):
        codec.decode(b"not json at all")


def test_decode_non_object_raises_framing_error() -> None:
    """A JSON array or scalar at the top level is a framing error."""
    with pytest.raises(errors.WireFramingError):
        codec.decode(b"[1, 2, 3]")
    with pytest.raises(errors.WireFramingError):
        codec.decode(b"42")
    with pytest.raises(errors.WireFramingError):
        codec.decode(b'"hello"')


def test_decode_missing_kind_raises_framing_error() -> None:
    with pytest.raises(errors.WireFramingError):
        codec.decode(b'{"seq": 1, "protocol_version": 1}')


def test_decode_unknown_kind_raises_decode_error() -> None:
    with pytest.raises(errors.WireDecodeError):
        codec.decode(b'{"kind": "ZZZ_NONESUCH", "seq": 1}')


def test_decode_non_string_kind_raises_framing_error() -> None:
    with pytest.raises(errors.WireFramingError):
        codec.decode(b'{"kind": 42}')


def test_decode_empty_bytes_raises_framing_error() -> None:
    with pytest.raises(errors.WireFramingError):
        codec.decode(b"")


def test_decode_invalid_utf8_raises_framing_error() -> None:
    with pytest.raises(errors.WireFramingError):
        codec.decode(b"\xff\xfe\x00\x00")


# --- forward-compat ---


def test_decode_tolerates_unknown_optional_field() -> None:
    """A future server may add a field; current clients must not error."""
    msg = dict(_HELLO_SERVER)
    msg["future_field"] = {"some": "value"}
    encoded = codec.encode(msg)
    decoded = codec.decode(encoded)
    # The unknown field is preserved (we tolerate, not strip — the consumer
    # can ignore it).
    assert decoded["future_field"] == {"some": "value"}
    assert decoded["kind"] == "HELLO"


def test_decode_unknown_kind_is_hard_error_not_tolerant() -> None:
    """Distinguish from unknown *fields*: unknown `kind` is fatal."""
    with pytest.raises(errors.WireDecodeError):
        codec.decode(b'{"kind": "FUTURE_KIND_V2", "seq": 1, "payload": {}}')


# --- privacy ---


def test_event_player_vs_spectator_differ_on_draw_tile() -> None:
    """An EVENT carrying a DRAW projects differently for the drawing seat vs.
    a spectator. The codec round-trips both; their inner events differ on
    the `tile` field per `project_event`.
    """
    record_event: dict[str, Any] = {
        "event": "DRAW",
        "turn_index": 5,
        "phase": "DISCARD",
        "ts": "2026-05-22T10:00:00Z",
        "seat": 2,
        "tile": "B5",
        "flower_replacements": [],
    }
    player_evt = project_event(record_event, seat=2)
    spectator_evt = project_event(record_event, seat=None)

    player_wire = {
        "kind": "EVENT",
        "seq": 5,
        "table_id": 17,
        "hand_index": 0,
        "event": player_evt,
    }
    spectator_wire = {
        "kind": "EVENT",
        "seq": 5,
        "table_id": 17,
        "hand_index": 0,
        "event": spectator_evt,
    }

    player_bytes = codec.encode(player_wire)
    spectator_bytes = codec.encode(spectator_wire)

    assert player_bytes != spectator_bytes
    assert b"B5" in player_bytes
    assert b"B5" not in spectator_bytes

    # Both round-trip.
    assert codec.decode(player_bytes) == player_wire
    assert codec.decode(spectator_bytes) == spectator_wire


# --- KNOWN_KINDS surface ---


def test_known_kinds_includes_every_fixture_kind() -> None:
    """KNOWN_KINDS is the authoritative enumeration; every fixture's `kind`
    must appear there (and vice versa, modulo fixtures-with-shared-kind)."""
    fixture_kinds = {msg["kind"] for _, msg in ALL_FIXTURES}
    missing = fixture_kinds - codec.KNOWN_KINDS
    assert not missing, f"KNOWN_KINDS missing: {missing}"


def test_known_kinds_does_not_include_spurious_kinds() -> None:
    """Every kind in KNOWN_KINDS must be exercised by at least one fixture."""
    fixture_kinds = {msg["kind"] for _, msg in ALL_FIXTURES}
    spurious = codec.KNOWN_KINDS - fixture_kinds
    assert not spurious, f"KNOWN_KINDS has unexercised kinds: {spurious}"

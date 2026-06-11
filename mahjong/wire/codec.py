"""WebSocket wire-protocol codec: JSON encode/decode with `kind` validation.

Spec: docs/specs/wire-protocol.md § Message framing, § Message catalog.

`encode(msg)` produces a single-line UTF-8 JSON document; `decode(data)`
parses one back. The codec validates the envelope (must be a JSON object
with a string `kind` in `KNOWN_KINDS`) but does not enforce per-message
field schemas — that lives in the typed `WireMessage` callers and downstream
consumers (session-mux, HumanAdapter).

Privacy: projection of EVENT payloads is **the caller's responsibility**.
The codec round-trips whatever it is given. Session-mux applies
`project_event` before handing an EVENT here.
"""

from __future__ import annotations

import json
from typing import Any, Literal, NotRequired, TypedDict

from mahjong.wire.errors import WireDecodeError, WireFramingError

# --- known message kinds (§Message catalog) ---

KNOWN_KINDS: frozenset[str] = frozenset(
    {
        "HELLO",
        "HEARTBEAT",
        "ERROR",
        "AUTH_REQUEST",
        "AUTH_RESPONSE",
        "RESUME",
        "REGISTER",
        "LIST_TABLES",
        "TABLE_LIST",
        "ATTACH",
        "ATTACHED",
        "DETACH",
        "DETACHED",
        "SPECTATE",
        "SPECTATING",
        "STOP_SPECTATING",
        "EVENT",
        "PROMPT",
        "ACTION",
        "HAND_END",
        "CREATE_TABLE",
        "TABLE_CREATED",
        "CLOSE_TABLE",
        "START_HAND",
        "READY",
        "READY_STATE",
        "FEEDBACK",
        "FEEDBACK_ACK",
        "GET_PROFILE",
        "PROFILE",
        "GET_HISTORY",
        "HISTORY",
        "GET_REPLAY",
        "REPLAY",
    }
)


# --- typed message shapes ---
#
# These are the contract for code that constructs wire messages in Python.
# Runtime `decode()` returns a plain `dict[str, Any]`; consumers narrow by
# `kind` and cast (or use `typing.cast`) where they need the typed form.


class HelloServer(TypedDict):
    kind: Literal["HELLO"]
    seq: int
    protocol_version: int
    server_id: str
    min_client_version: NotRequired[int]
    features: NotRequired[list[str]]
    # Selectable in-process bots for the create-table picker (seat_bots.py).
    bots: NotRequired[list[dict[str, Any]]]


class HelloClient(TypedDict):
    kind: Literal["HELLO"]
    protocol_version: int
    client_id: NotRequired[str]


class Heartbeat(TypedDict):
    kind: Literal["HEARTBEAT"]
    nonce: str
    echo: NotRequired[bool]
    seq: NotRequired[int]


class ErrorMsg(TypedDict):
    kind: Literal["ERROR"]
    code: str
    message: NotRequired[str]
    seq: NotRequired[int]
    ref: NotRequired[int]
    details: NotRequired[dict[str, Any]]


class AuthRequest(TypedDict):
    kind: Literal["AUTH_REQUEST"]
    username: str
    password: str


class AuthResponseOk(TypedDict):
    kind: Literal["AUTH_RESPONSE"]
    seq: int
    ok: Literal[True]
    user_id: str
    display_name: str
    session_token: str
    expires_at_ms: int
    # Seats this account currently holds, for FB-03 rejoin discovery
    # (reconnect-rejoin.md). Each entry: {table_id, seat, state, hand_index,
    # rejoin_deadline_ms?}. Omitted when the account holds no seats.
    seat_holds: NotRequired[list[dict[str, Any]]]


class AuthResponseFail(TypedDict):
    kind: Literal["AUTH_RESPONSE"]
    seq: int
    ok: Literal[False]


class Resume(TypedDict):
    kind: Literal["RESUME"]
    session_token: str


class Register(TypedDict):
    kind: Literal["REGISTER"]
    username: str
    password: str
    display_name: str
    invite_code: str


class ListTables(TypedDict):
    kind: Literal["LIST_TABLES"]


class TableList(TypedDict):
    kind: Literal["TABLE_LIST"]
    seq: int
    tables: list[dict[str, Any]]


class GetProfile(TypedDict):
    kind: Literal["GET_PROFILE"]


class Profile(TypedDict):
    kind: Literal["PROFILE"]
    seq: int
    account: dict[str, Any]
    stats: dict[str, Any]
    recent: list[dict[str, Any]]
    series: list[dict[str, Any]]
    # Spec 39: full achievement catalog with earned/progress, derive-at-read.
    achievements: NotRequired[list[dict[str, Any]]]


class GetHistory(TypedDict):
    kind: Literal["GET_HISTORY"]
    before_hand_id: NotRequired[str]
    limit: NotRequired[int]


class History(TypedDict):
    kind: Literal["HISTORY"]
    seq: int
    hands: list[dict[str, Any]]
    next_before_hand_id: str | None


class GetReplay(TypedDict):
    kind: Literal["GET_REPLAY"]
    hand_id: str


class Replay(TypedDict):
    kind: Literal["REPLAY"]
    seq: int
    hand_id: str
    seat: int  # viewing seat; -1 for the public (admin / non-participant) view
    snapshot: dict[str, Any]
    events: list[dict[str, Any]]
    meta: dict[str, Any]


class Attach(TypedDict):
    kind: Literal["ATTACH"]
    table_id: int
    seat: int


class Attached(TypedDict):
    kind: Literal["ATTACHED"]
    seq: int
    table_id: int
    seat: int
    hand_index: int
    snapshot: dict[str, Any]
    resume_buffer_size: int


class DetachClient(TypedDict):
    kind: Literal["DETACH"]
    reason: str


class DetachServer(TypedDict):
    kind: Literal["DETACH"]
    seq: int
    reason: str
    table_id: int
    seat: int


class Detached(TypedDict):
    kind: Literal["DETACHED"]
    seq: int


class Spectate(TypedDict):
    kind: Literal["SPECTATE"]
    table_id: int


class Spectating(TypedDict):
    kind: Literal["SPECTATING"]
    seq: int
    table_id: int
    hand_index: int
    snapshot: dict[str, Any]
    spectator_count: NotRequired[int]


class StopSpectating(TypedDict):
    kind: Literal["STOP_SPECTATING"]


class Event(TypedDict):
    kind: Literal["EVENT"]
    seq: int
    table_id: int
    hand_index: int
    event: dict[str, Any]


class Prompt(TypedDict):
    kind: Literal["PROMPT"]
    seq: int
    table_id: int
    hand_index: int
    seat: int
    phase: str
    legal_actions: list[dict[str, Any]]
    default_action: dict[str, Any]
    deadline_ms: int
    prompt_id: str


class Action(TypedDict):
    kind: Literal["ACTION"]
    prompt_id: str
    action: dict[str, Any]
    ref: NotRequired[int]


class HandEnd(TypedDict):
    kind: Literal["HAND_END"]
    seq: int
    table_id: int
    hand_index: int
    terminal: dict[str, Any]
    next_hand_seq: int | None


class CreateTable(TypedDict):
    kind: Literal["CREATE_TABLE"]
    ruleset: str
    seats: list[dict[str, Any]]
    # Optional per-table creation knobs (§22.6 Part A): bot_pacing (preset
    # name or {min_s,max_s}), decide_timeout_seconds, timeouts_enabled.
    # Parsed by mahjong.server.table_options.parse_table_options.
    options: NotRequired[dict[str, Any]]


class TableCreated(TypedDict):
    kind: Literal["TABLE_CREATED"]
    seq: int
    table_id: int


class CloseTable(TypedDict):
    kind: Literal["CLOSE_TABLE"]
    table_id: int
    force: NotRequired[bool]


class Feedback(TypedDict):
    kind: Literal["FEEDBACK"]
    type: Literal["bug", "feature"]
    text: str


class FeedbackAck(TypedDict):
    kind: Literal["FEEDBACK_ACK"]


WireMessage = (
    HelloServer
    | HelloClient
    | Heartbeat
    | ErrorMsg
    | AuthRequest
    | AuthResponseOk
    | AuthResponseFail
    | Resume
    | Register
    | ListTables
    | TableList
    | Attach
    | Attached
    | DetachClient
    | DetachServer
    | Detached
    | Spectate
    | Spectating
    | StopSpectating
    | Event
    | Prompt
    | Action
    | HandEnd
    | CreateTable
    | TableCreated
    | CloseTable
    | Feedback
    | FeedbackAck
)


# --- encode / decode ---


def encode(msg: WireMessage | dict[str, Any]) -> bytes:
    """Encode a wire message to a single-line UTF-8 JSON document.

    No leading/trailing whitespace; separators are tight (`","` / `":"`).
    Keys are sorted lexicographically so that wire-level byte equality
    matches semantic equality (useful for record-vs-wire fixture diffs).
    """
    return json.dumps(
        msg,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def decode(data: bytes) -> dict[str, Any]:
    """Parse a UTF-8 JSON wire frame into a dict and validate the envelope.

    Raises `WireFramingError` if the bytes are not a JSON object or the
    `kind` field is missing / non-string. Raises `WireDecodeError` if the
    `kind` is not in `KNOWN_KINDS`. Unknown optional fields are preserved
    (forward-compat per §Versioning).
    """
    if not data:
        raise WireFramingError("empty wire frame")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise WireFramingError(f"frame is not valid UTF-8: {exc}") from exc
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise WireFramingError(f"frame is not valid JSON: {exc.msg}") from exc
    if not isinstance(parsed, dict):
        raise WireFramingError(f"wire frame must be a JSON object, got {type(parsed).__name__}")
    kind = parsed.get("kind")
    if not isinstance(kind, str):
        raise WireFramingError(f"wire frame missing string 'kind' field, got {kind!r}")
    if kind not in KNOWN_KINDS:
        raise WireDecodeError(f"unknown wire message kind: {kind!r}")
    return parsed


__all__ = [
    "KNOWN_KINDS",
    "Action",
    "Attach",
    "Attached",
    "AuthRequest",
    "AuthResponseFail",
    "AuthResponseOk",
    "CloseTable",
    "CreateTable",
    "DetachClient",
    "DetachServer",
    "Detached",
    "ErrorMsg",
    "Event",
    "HandEnd",
    "Heartbeat",
    "HelloClient",
    "HelloServer",
    "ListTables",
    "Prompt",
    "Resume",
    "Spectate",
    "Spectating",
    "StopSpectating",
    "TableCreated",
    "TableList",
    "WireMessage",
    "decode",
    "encode",
]

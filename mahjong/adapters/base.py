"""SeatAdapter Protocol and supporting data shapes.

Spec: docs/specs/seat-port.md § The interface, § Data shapes.

The port is structural (Protocol, not ABC) — adapters share no implementation,
so inheritance would obscure the seam. Conforming classes are checked by mypy.
"""

from __future__ import annotations

from typing import Any, Literal, NotRequired, Protocol, TypedDict, runtime_checkable

from mahjong.engine.types import Action, RuleSetRef, SeatView

# --- SeatIdentity (tagged union; one variant per adapter kind) ---


class HumanIdentity(TypedDict):
    kind: Literal["human"]
    user_id: str
    display: str


class BotIdentity(TypedDict):
    kind: Literal["bot"]
    bot_id: str
    version: str
    runtime: Literal["subprocess", "in_process"]


class CannedIdentity(TypedDict):
    kind: Literal["canned"]
    script: str


class SpectatorIdentity(TypedDict):
    kind: Literal["spectator"]
    viewer_id: str


class SelfPlayDriverIdentity(TypedDict):
    kind: Literal["self_play_driver"]
    driver_id: str


SeatIdentity = (
    HumanIdentity | BotIdentity | CannedIdentity | SpectatorIdentity | SelfPlayDriverIdentity
)


# --- Lifecycle context types ---


class SeatContext(TypedDict):
    seat: int
    hand_id: str
    ruleset: RuleSetRef
    seat_deadline_ms: int
    initial_view: SeatView
    # When True, the table manager will route canonical GameState to this
    # adapter's observe instead of SeatView. Only ever true for
    # SelfPlayDriverAdapter (see seat-port.md privacy boundary).
    allow_god_view: NotRequired[bool]


PromptKind = Literal["DISCARD", "CLAIM"]


class Prompt(TypedDict):
    kind: PromptKind
    view: SeatView
    legal_actions: list[Action]
    default_action: Action
    # Monotonic deadline (`asyncio.get_event_loop().time()`-based), not wall-clock.
    deadline: float
    issued_at: float
    context: dict[str, Any]


LeaveReason = Literal["HAND_ENDED", "TABLE_CLOSED", "REPLACED", "ERROR"]


# --- Sentinel exceptions adapters may raise to signal explicit failure ---


class SeatTimeout(Exception):
    """An adapter knows it cannot meet the deadline; signal early instead of
    making the table manager wait for cancellation."""


class SeatError(Exception):
    """An adapter has hit an unrecoverable error (subprocess died, parse
    error, etc.). Treated as a strike, like a crash."""


# --- The Protocol ---


@runtime_checkable
class SeatAdapter(Protocol):
    """Five-method async interface every seat implements.

    See seat-port.md for the full contract; in summary: `seated`/`observe`/`left`
    are lifecycle, `decide` is the decision call, `identity` is data.
    """

    identity: SeatIdentity

    async def seated(self, ctx: SeatContext) -> None: ...

    async def observe(self, event: dict[str, Any], view: SeatView) -> None: ...

    async def decide(self, prompt: Prompt) -> Action: ...

    async def left(self, reason: LeaveReason) -> None: ...


__all__ = [
    "Action",
    "BotIdentity",
    "CannedIdentity",
    "HumanIdentity",
    "LeaveReason",
    "Prompt",
    "PromptKind",
    "SeatAdapter",
    "SeatContext",
    "SeatError",
    "SeatIdentity",
    "SeatTimeout",
    "SelfPlayDriverIdentity",
    "SpectatorIdentity",
]

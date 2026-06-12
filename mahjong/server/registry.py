"""Table registry — in-memory map of live TableManager instances.

Spec: docs/specs/server-lifecycle.md § Table registry.
      docs/specs/multi-human-seats.md (Step 8.7).

``TableHandle``
    Bundles one table's ``TableSessions`` + hand-orchestration task.  Seat
    composition (which seats are ``human`` vs. ``bot``) is supplied at
    construction; the hand loop builds one adapter per seat from that.

``TableRegistry``
    The dict of live ``TableHandle``s.  Supports ``create_table``,
    ``list_tables``, ``get_table``, ``close_table``, ``drain_all``.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import hashlib
import json
import logging
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

from mahjong.adapters.autopass import AutoPassAdapter
from mahjong.adapters.base import HumanIdentity, SeatAdapter
from mahjong.adapters.canned import CannedAdapter
from mahjong.adapters.human import HumanAdapter
from mahjong.adapters.paced import PacedAdapter
from mahjong.analysis import stats_for_prompt
from mahjong.engine import initial_state
from mahjong.engine.rulesets import resolve_config
from mahjong.engine.state import project as project_state
from mahjong.engine.types import Action, GameState, RuleSetRef
from mahjong.persistence import Participant, Persistence
from mahjong.server.seat_bots import DEFAULT_BOT_ID, build_bot_adapter
from mahjong.server.seats import DEFAULT_COMPOSITION, SeatsTuple
from mahjong.sessions import TableSessions
from mahjong.sessions.mux import DEFAULT_HOLD_SECONDS, SeatState
from mahjong.table import manager as mgr
from mahjong.table.manager import DecideTimeouts
from mahjong.table.match_score import MatchScore
from mahjong.table.rotation import next_dealer

_logger = logging.getLogger(__name__)


def _new_boot_id() -> str:
    """A unique, sortable namespace for one server process's records + ids.

    Sortable UTC timestamp (find a run's records by start time) plus a short
    random suffix so two boots in the same second can't collide. See
    ``TableRegistry.__init__`` for the DEF-13 rationale.
    """
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:6]


def _ruleset_config_hash(ruleset: RuleSetRef) -> str:
    """Stable hash of a ruleset dict for the persistence ``hand_index`` row."""
    canonical = json.dumps(dict(ruleset), sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _account_id_from_user_id(user_id: str) -> int | None:
    """Parse account_id from a ``u_{int}`` user_id.  Returns None on any failure
    (e.g. unauthenticated identity like ``u_42`` from the demo factory)."""
    if not user_id.startswith("u_"):
        return None
    try:
        return int(user_id[2:])
    except ValueError:
        return None


def _read_footer_checksum(record_path: Path) -> str | None:
    """Read the last line of *record_path* and return its ``checksum`` field.

    Returns None if the file is missing or the last line isn't a parseable
    FOOTER (e.g. crash mid-write).
    """
    try:
        with record_path.open("rb") as fh:
            # Records are line-oriented JSONL; tail-read by scanning from end.
            fh.seek(0, 2)
            size = fh.tell()
            if size == 0:
                return None
            chunk_size = min(size, 4096)
            fh.seek(size - chunk_size, 0)
            tail = fh.read().splitlines()
            if not tail:
                return None
            last = json.loads(tail[-1].decode("utf-8"))
            if last.get("event") != "FOOTER":
                return None
            checksum = last.get("checksum")
            return str(checksum) if checksum else None
    except (OSError, json.JSONDecodeError):
        return None


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class ShuttingDown(Exception):
    """Raised by TableRegistry.create_table when the registry is draining."""


class TableNotFound(Exception):
    """Raised by TableRegistry.get_table for an unknown table_id."""


# ---------------------------------------------------------------------------
# TableSummary
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class SeatSummary:
    """One seat's snapshot for ``TABLE_LIST.seats[i]``.

    Wire shape per ``docs/specs/multi-human-seats.md § TABLE_LIST``:
      - ``user_id`` present iff ``kind == "human" and occupied``.
      - ``bot_id`` present iff ``kind == "bot"``.
    """

    seat: int
    kind: str  # "human" | "bot"
    occupied: bool
    user_id: str | None = None
    bot_id: str | None = None
    # FB-05 (table-management.md): human-readable name + LIVE/HELD so the lobby
    # can show "who" (not just a count) and mark away players. Present only on
    # occupied human seats.
    display_name: str | None = None
    state: str | None = None  # "LIVE" | "HELD"

    def to_wire(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "seat": self.seat,
            "kind": self.kind,
            "occupied": self.occupied,
        }
        if self.kind == "human" and self.occupied and self.user_id is not None:
            out["user_id"] = self.user_id
            if self.display_name is not None:
                out["display_name"] = self.display_name
            if self.state is not None:
                out["state"] = self.state
        if self.kind == "bot" and self.bot_id is not None:
            out["bot_id"] = self.bot_id
        return out


@dataclasses.dataclass(frozen=True)
class SeatHold:
    """One seat an authenticated account currently holds, for the post-auth
    ``AUTH_RESPONSE.seat_holds[]`` rejoin-discovery list (reconnect-rejoin.md,
    FB-03).

    ``state`` is ``"LIVE"`` (socket still attached elsewhere — a takeover
    candidate) or ``"HELD"`` (dropped, within the hold window — rejoinable).
    ``rejoin_deadline_ms`` is the wall-clock hold expiry, present only for
    ``HELD``.
    """

    table_id: int
    seat: int
    state: str  # "LIVE" | "HELD"
    hand_index: int
    rejoin_deadline_ms: int | None = None

    def to_wire(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "table_id": self.table_id,
            "seat": self.seat,
            "state": self.state,
            "hand_index": self.hand_index,
        }
        if self.rejoin_deadline_ms is not None:
            out["rejoin_deadline_ms"] = self.rejoin_deadline_ms
        return out


@dataclasses.dataclass(frozen=True)
class StartHandOutcome:
    """Result of ``TableHandle.start_hand`` — see multi-human-seats.md § START_HAND."""

    ok: bool
    error_code: str | None = None  # "not_authorized" | "humans_not_ready" | "hand_already_started"
    error_message: str | None = None


@dataclasses.dataclass(frozen=True)
class TableSummary:
    """Snapshot of one table for LIST_TABLES wire response."""

    table_id: str
    ruleset: str
    hand_index: int
    phase: str  # "WAITING_FOR_PLAYERS" | "IN_PROGRESS"
    seats: tuple[SeatSummary, ...]

    def to_wire(self) -> dict[str, Any]:
        return {
            "table_id": int(self.table_id),
            "ruleset": self.ruleset,
            "hand_index": self.hand_index,
            "phase": self.phase,
            "seats": [s.to_wire() for s in self.seats],
        }


# ---------------------------------------------------------------------------
# TableHandle
# ---------------------------------------------------------------------------


class TableHandle:
    """Single-table state: TableSessions + hand-orchestration task.

    The hand loop mirrors ``WebOrchestrator._run_hand_loop``; the two share
    no code to avoid coupling until the architecture stabilises (intentional
    duplication, see project-multi-table-architecture memory).

    Seat composition is declared at construction time via ``seats`` (a
    ``SeatsTuple`` from ``mahjong.server.seats``); each ``kind: "human"``
    seat may be claimed by any authenticated user via ``attach``, each
    ``kind: "bot"`` seat is backed by a ``CannedAdapter``-PASS placeholder.

    The hand loop is ignited by an explicit ``START_HAND`` wire message
    from any LIVE human at the table (see ``start_hand``); ``attach`` no
    longer auto-starts.
    """

    def __init__(
        self,
        *,
        table_id: str,
        ruleset: RuleSetRef,
        seed: int,
        hand_id: str,
        record_path: Path,
        boot_id: str = "",
        server_info: dict[str, Any],
        canned_seat_actions: dict[int, list[Action]] | None = None,
        decide_timeout_seconds: float = 30.0,
        decide_timeouts: DecideTimeouts | None = None,
        hold_seconds: float = DEFAULT_HOLD_SECONDS,
        strike_limit: int = 3,
        max_hands: int | None = 1,
        between_hand_pause_seconds: float = 2.0,
        ready_timeout_seconds: float = 120.0,
        persistence: Persistence | None = None,
        data_dir: Path | None = None,
        seats: SeatsTuple | None = None,
        bot_pacing_enabled: bool = False,
        bot_min_delay_s: float = 5.0,
        bot_max_delay_s: float = 10.0,
    ) -> None:
        self._table_id = table_id
        self._ruleset = ruleset
        self._seed = seed
        self._hand_id = hand_id
        self._record_path = record_path
        self._server_info = server_info
        self._decide_timeout_seconds = decide_timeout_seconds
        self._decide_timeouts = decide_timeouts
        self._hold_seconds = hold_seconds
        self._strike_limit = strike_limit
        self._max_hands = max_hands
        self._between_hand_pause_seconds = between_hand_pause_seconds
        self._ready_timeout_seconds = ready_timeout_seconds
        self._persistence = persistence
        self._data_dir = data_dir
        # Bot pacing (Layer-8 §2 — humanize bot turn speed at multi-human
        # tables).  Default off so unit tests don't pay the wall-clock cost;
        # cli/serve.py turns it on via env vars for live deployments.
        self._bot_pacing_enabled = bot_pacing_enabled
        self._bot_min_delay_s = bot_min_delay_s
        self._bot_max_delay_s = bot_max_delay_s
        # Boot-scope the match id too (DEF-13) so cross-restart hands with the
        # same table id don't group together in find_hands_by_match. Empty
        # boot_id keeps the legacy format for direct-construction tests.
        self._match_id = f"match-{boot_id}-t{table_id}" if boot_id else f"match_t{table_id}"
        # Step 8.7: composition drives per-seat adapter construction and the
        # ATTACH-permission check.  ``None`` falls back to single-human legacy.
        self._seats: SeatsTuple = seats if seats is not None else DEFAULT_COMPOSITION

        # Between-hand mutable state
        self._hand_index: int = 0
        self._dealer_seat: int = 0
        self._initial_state: GameState = initial_state(
            ruleset, seed=seed, dealer_seat=0, hand_index=0
        )
        # FB-17: the *current* engine state of the running (or just-finished)
        # hand, updated by run_hand's state_callback on every transition.
        # None between hands / before the first hand — the snapshot provider
        # then falls back to `_initial_state` (the next deal). Serving this
        # instead of the deal is what makes reconnect snapshots honest
        # (session-mux.md fixture 3: snapshot == current project(state, seat)).
        self._live_state: GameState | None = None
        # Running cumulative match score across the hands played at this table
        # (Spec 40). Display-only, not persisted; rides each per-seat snapshot.
        self._match_score = MatchScore()

        # ``kind: "bot"`` seats are backed by the v0 offense bot (Spec 27).
        # The ``canned_seat_actions`` injection seam is retained for tests that
        # need a scripted seat: any seat given a non-empty script uses a
        # ``CannedAdapter`` instead of v0 (see ``_build_adapters_for_hand``).
        # A seat present in ``canned_seat_actions`` (even with an empty script,
        # which falls back to ``default_action`` = PASS) is backed by a
        # ``CannedAdapter``; absent bot seats get the v0 bot. This lets wire /
        # session tests pin deterministic PASS bots, decoupled from bot logic.
        actions_by_seat = canned_seat_actions or {}
        self._scripted_seats: set[int] = set(actions_by_seat)
        self._canned_adapters: dict[int, CannedAdapter] = {
            seat: CannedAdapter(
                identity={"kind": "canned", "script": "pass"},
                actions=list(actions_by_seat.get(seat, [])),
            )
            for seat in range(4)
            if self._seats[seat].kind == "bot"
        }

        # Identity bound to each human seat by the most-recent successful
        # ATTACH.  Used by ``_run_hand_loop`` to construct HumanAdapters and
        # by ``_reserve_hand_row`` to fill ``participants[seat].account_id``.
        self._human_identities: dict[int, HumanIdentity] = {}

        self._sessions: TableSessions = TableSessions(
            table_id=int(table_id),
            snapshot_provider=self._snapshot_provider,
            hand_index_provider=lambda: self._hand_index,
            hold_seconds=self._hold_seconds,
        )
        self._hand_task: asyncio.Task[None] | None = None
        self._match_done: asyncio.Event = asyncio.Event()
        self._start_hand_lock: asyncio.Lock = asyncio.Lock()
        # Graceful-drain signal (server-lifecycle.md § Graceful shutdown step 3):
        # ``request_stop`` sets it; the hand loop finishes the *current* hand,
        # then breaks instead of starting another.  An Event (not a bool) so the
        # between-hand pause can wait on it interruptibly — a SIGTERM during the
        # pause stops us immediately rather than after the full pause.
        self._stop_event: asyncio.Event = asyncio.Event()

        # FB-02 (end-game ready-up gate): between hands, the loop waits for each
        # LIVE human seat to send READY (acknowledging the HAND_END summary)
        # before starting the next hand, instead of a fixed pause that flashed
        # the summary for ~1s. ``_ready_seats`` accumulates per between-hand
        # window; ``_ready_changed`` wakes the gate on each READY / disconnect.
        self._ready_seats: set[int] = set()
        self._ready_changed: asyncio.Event = asyncio.Event()

    # --- public read-only properties ---

    @property
    def table_id(self) -> str:
        return self._table_id

    @property
    def sessions(self) -> TableSessions:
        return self._sessions

    @property
    def match_done(self) -> asyncio.Event:
        return self._match_done

    @property
    def record_path(self) -> Path:
        """Record path for hand 0 (used for isolation assertions in tests)."""
        return self._record_path

    @property
    def hand_id(self) -> str:
        """hand_id for hand 0 (used for isolation assertions in tests)."""
        return self._hand_id

    @property
    def seats(self) -> SeatsTuple:
        """The declared seat composition (see Step 8.7 spec)."""
        return self._seats

    def is_human_seat(self, seat: int) -> bool:
        """True if *seat* is a ``kind: "human"`` seat in this table's composition."""
        if seat < 0 or seat >= 4:
            return False
        return self._seats[seat].kind == "human"

    def seat_holds_for(self, user_id: str) -> list[SeatHold]:
        """Seats this table currently binds to *user_id* (LIVE or HELD).

        Drives FB-03 rejoin discovery: a returning client learns which seat it
        held without scanning the whole table list.
        """
        holds: list[SeatHold] = []
        for seat in range(4):
            session = self._sessions.seat(seat)
            if session.user_id != user_id:
                continue
            if session.state is SeatState.LIVE:
                holds.append(
                    SeatHold(
                        table_id=int(self._table_id),
                        seat=seat,
                        state="LIVE",
                        hand_index=self._hand_index,
                    )
                )
            elif session.state is SeatState.HELD:
                holds.append(
                    SeatHold(
                        table_id=int(self._table_id),
                        seat=seat,
                        state="HELD",
                        hand_index=self._hand_index,
                        rejoin_deadline_ms=session.hold_deadline_ms,
                    )
                )
        return holds

    # --- summary ---

    def summary(self) -> TableSummary:
        phase = "IN_PROGRESS" if self._is_in_progress() else "WAITING_FOR_PLAYERS"
        return TableSummary(
            table_id=self._table_id,
            ruleset=self._ruleset.get("id", "mcr-2006"),
            hand_index=self._hand_index,
            phase=phase,
            seats=self._build_seat_summaries(),
        )

    def _build_seat_summaries(self) -> tuple[SeatSummary, ...]:
        """One ``SeatSummary`` per seat, reflecting current session-mux state.

        - Bot seats: always ``occupied=True``, ``bot_id`` is the selected bot
          (resolved to the default when the composition left it unset).
        - Human seats: ``occupied`` reflects whether session-mux holds a
          bound user (``LIVE`` or ``HELD``); when occupied, ``user_id`` is
          the bound id.
        """
        out: list[SeatSummary] = []
        for seat in range(4):
            comp = self._seats[seat]
            if comp.kind == "human":
                session = self._sessions.seat(seat)
                user_id = session.user_id
                occupied = user_id is not None
                display_name: str | None = None
                seat_state: str | None = None
                if occupied:
                    identity = self._human_identities.get(seat)
                    display_name = identity.get("display") if identity else None
                    seat_state = session.state.value  # "LIVE" | "HELD"
                out.append(
                    SeatSummary(
                        seat=seat,
                        kind="human",
                        occupied=occupied,
                        user_id=user_id,
                        display_name=display_name,
                        state=seat_state,
                    )
                )
            else:
                out.append(
                    SeatSummary(
                        seat=seat,
                        kind="bot",
                        occupied=True,
                        bot_id=comp.bot_id or DEFAULT_BOT_ID,
                    )
                )
        return tuple(out)

    # --- snapshot provider (for TableSessions) ---

    def _snapshot_provider(self, seat: int | None) -> dict[str, Any]:
        source = self._live_state if self._live_state is not None else self._initial_state
        snapshot = cast(dict[str, Any], project_state(source, seat))
        self._annotate_seat_names(snapshot)
        self._annotate_match_scores(snapshot)
        return snapshot

    def _on_hand_state(self, state: GameState) -> None:
        """run_hand's state_callback — keep the snapshot source current."""
        self._live_state = state

    def _seat_name_map(self) -> dict[int, dict[str, Any]]:
        """seat index -> ``{"name": str | None, "is_bot": bool}`` from the roster.

        ``name`` is ``None`` for an unoccupied human seat (the client falls
        back to wind+seat). Bots are named by their ``bot_id``.
        """
        out: dict[int, dict[str, Any]] = {}
        for seat in range(4):
            comp = self._seats[seat]
            if comp.kind == "human":
                session = self._sessions.seat(seat)
                if session.user_id is not None:
                    identity = self._human_identities.get(seat)
                    display = (identity.get("display") if identity else None) or session.user_id
                    out[seat] = {"name": display, "is_bot": False}
                else:
                    out[seat] = {"name": None, "is_bot": False}
            else:
                out[seat] = {"name": comp.bot_id or DEFAULT_BOT_ID, "is_bot": True}
        return out

    def _annotate_seat_names(self, snapshot: dict[str, Any]) -> None:
        """Splice the table-roster display name onto each projected seat.

        Player names are a server/registry concept, not engine state, so the
        pure projection (``project_state``) never carries them. We decorate the
        snapshot here — the one boundary that holds both the projection and the
        seat composition — so the web client can label seats by player rather
        than only by wind+seat. The client reducer (``apply_event.js``
        ``cloneSeatView``) preserves these extra fields across events, so the
        single ATTACHED enrichment is enough for the whole hand.
        """
        name_map = self._seat_name_map()
        for seat_view in snapshot.get("seats", []):
            info = name_map.get(seat_view.get("seat"))
            if info is not None:
                seat_view["name"] = info["name"]
                seat_view["is_bot"] = info["is_bot"]

    def _annotate_match_scores(self, snapshot: dict[str, Any]) -> None:
        """Splice the running cumulative match score onto the snapshot (Spec 40).

        Like the seat-name annotation, this is a table/match concept the pure
        projection never carries. ``match_scores`` (top-level) feeds the score
        widget's per-seat line graph; per-seat ``match_score`` feeds the inline
        total beside each player's name. The client reducer preserves both
        across in-hand events, so this between-hand snapshot enrichment holds
        for the whole hand (it only changes when the next hand begins).
        """
        snapshot["match_scores"] = self._match_score.to_wire()
        cumulative = self._match_score.cumulative
        for seat_view in snapshot.get("seats", []):
            seat = seat_view.get("seat")
            if isinstance(seat, int) and 0 <= seat < len(cumulative):
                seat_view["match_score"] = cumulative[seat]

    # --- per-hand path helpers ---

    def _record_path_for_hand(self, hand_index: int) -> Path:
        if hand_index == 0:
            return self._record_path
        return self._record_path.parent / (
            f"{self._record_path.stem}_{hand_index}{self._record_path.suffix}"
        )

    def _hand_id_for_hand(self, hand_index: int) -> str:
        if hand_index == 0:
            return self._hand_id
        return f"{self._hand_id}_{hand_index}"

    # --- attach / spectate / inbound ---

    async def attach(
        self,
        conn: Any,
        *,
        identity: HumanIdentity,
        seat: int,
    ) -> bool:
        """Attach *conn* to *seat*.  Rejects bot seats and out-of-range seats
        with ``seat_not_yours``.  Returns True if the attach succeeded.

        Late-join refusal (Layer-8 §4, spec: ``docs/specs/late-join-replay.md``
        Alternative A): if the table's hand is ``IN_PROGRESS`` and the seat
        has never been bound (``SeatState.UNBOUND``), reject with
        ``hand_in_progress``.  Same-user resume (HELD → LIVE) is unaffected —
        the seat session is HELD, not UNBOUND, on a reconnect inside the
        hold window.

        Side effects on success: records the identity on
        ``self._human_identities[seat]``.  The hand loop is no longer ignited
        here — clients must follow up with ``START_HAND`` once every human
        seat is LIVE (see ``start_hand``).
        """
        if not self.is_human_seat(seat):
            with contextlib.suppress(Exception):
                await conn.send({"kind": "ERROR", "code": "seat_not_yours"})
            return False
        if self._is_in_progress() and self._sessions.seat(seat).state is SeatState.UNBOUND:
            with contextlib.suppress(Exception):
                await conn.send(
                    {
                        "kind": "ERROR",
                        "code": "hand_in_progress",
                        "message": (
                            f"table {self._table_id} is already running this hand; "
                            f"wait for the next hand to join seat {seat}"
                        ),
                    }
                )
            return False
        outcome = await self._sessions.attach(conn, user_id=identity["user_id"], seat=seat)
        if not outcome.ok:
            return False
        # Most-recent successful attach wins for identity-tracking; this
        # is consistent with same-user-takeover (session-mux fixture 8).
        self._human_identities[seat] = identity
        return True

    def _is_in_progress(self) -> bool:
        """True when the hand loop task is running.  Mirrors ``summary``'s
        phase computation so attach and ``LIST_TABLES`` agree on what
        IN_PROGRESS means."""
        return self._hand_task is not None and not self._hand_task.done()

    async def spectate(self, conn: Any, *, user_id: str) -> bool:
        outcome = await self._sessions.spectate(conn, user_id=user_id)
        return outcome.ok

    # --- hand-start trigger (8.7.d) ---

    def _seat_for_conn(self, conn: Any) -> int | None:
        """Return the seat index whose session-mux sink is *conn*, else None.

        Walks the four seat sessions; the session-mux maintains the canonical
        sink → seat binding, so we don't track a separate map.
        """
        for i in range(4):
            if self._sessions.seat(i).sink is conn:
                return i
        return None

    def _humans_not_live_count(self) -> int:
        """Count of ``kind: "human"`` seats that are not currently LIVE.

        HELD seats count as not-LIVE per spec: ``START_HAND`` requires every
        human to be actively connected, not just holding a reconnect window.
        """
        return sum(
            1
            for i in range(4)
            if self._seats[i].kind == "human" and self._sessions.seat(i).state is not SeatState.LIVE
        )

    async def start_hand(self, conn: Any) -> StartHandOutcome:
        """Validate and ignite the hand loop on behalf of *conn*.

        Three failure modes per ``docs/specs/multi-human-seats.md § START_HAND``:
          - ``not_authorized``: *conn* doesn't own a human seat at this table.
          - ``hand_already_started``: a hand is already in progress (idempotent
            against concurrent ``START_HAND``s from multiple humans).
          - ``humans_not_ready``: one or more human seats aren't LIVE yet.

        Order of checks: authorization first (don't leak occupancy info), then
        already-running (idempotent fast path), then readiness.  The
        ``_start_hand_lock`` serialises the race between simultaneous starts.
        """
        seat = self._seat_for_conn(conn)
        if seat is None or not self.is_human_seat(seat):
            return StartHandOutcome(ok=False, error_code="not_authorized")

        async with self._start_hand_lock:
            if self._hand_task is not None:
                return StartHandOutcome(ok=False, error_code="hand_already_started")

            missing = self._humans_not_live_count()
            if missing > 0:
                return StartHandOutcome(
                    ok=False,
                    error_code="humans_not_ready",
                    error_message=f"{missing} human seat(s) still unoccupied",
                )

            self._hand_task = asyncio.create_task(self._run_hand_loop())
            return StartHandOutcome(ok=True)

    async def _dispatch_start_hand(self, conn: Any, msg: dict[str, Any]) -> None:
        """Translate a START_HAND wire frame into ``start_hand`` + ERROR reply.

        Success path is silent: the originator's next visible signal is the
        first ``EVENT`` from the hand loop.  ``msg`` carries an advisory
        ``table_id`` we ignore — the connection already routes us to the
        correct table.
        """
        del msg  # table_id is advisory; routing is by connection-→-table.
        outcome = await self.start_hand(conn)
        if outcome.ok:
            return
        err: dict[str, Any] = {"kind": "ERROR", "code": outcome.error_code}
        if outcome.error_message is not None:
            err["message"] = outcome.error_message
        with contextlib.suppress(Exception):
            await conn.send(err)

    async def handle_inbound(self, conn: Any, msg: dict[str, Any]) -> None:
        kind = msg.get("kind")
        if kind == "START_HAND":
            await self._dispatch_start_hand(conn, msg)
            return
        if kind == "READY":
            self._mark_ready(conn)
            return
        await self._sessions.handle_inbound(conn, msg)

    # --- FB-02: end-game ready-up gate ---

    def _mark_ready(self, conn: Any) -> None:
        """Record a READY from *conn*'s human seat and wake the between-hand gate.

        Ignored (silently) from a non-human seat or an unrecognised connection —
        READY is advisory; a bad frame must not drop the socket."""
        seat = self._seat_for_conn(conn)
        if seat is None or not self.is_human_seat(seat):
            return
        self._ready_seats.add(seat)
        self._ready_changed.set()

    def _live_human_seats(self) -> set[int]:
        return {
            i
            for i in range(4)
            if self.is_human_seat(i) and self._sessions.seat(i).state is SeatState.LIVE
        }

    def _all_live_humans_ready(self) -> bool:
        # Vacuously true when no human is LIVE (pure-bot table, or all humans
        # dropped during the gate) → advance without waiting.
        return self._live_human_seats() <= self._ready_seats

    async def _await_humans_ready(self) -> None:
        """Block until every LIVE human seat has sent READY, the drain stop fires,
        or ``ready_timeout_seconds`` elapses (safety net for a human who left).

        The timeout means a disconnected / walked-away human can never stall the
        table forever; an explicit READY from everyone advances immediately."""
        self._ready_seats.clear()
        self._ready_changed.clear()
        loop = asyncio.get_event_loop()
        deadline = loop.time() + self._ready_timeout_seconds
        while not (self._stop_event.is_set() or self._all_live_humans_ready()):
            remaining = deadline - loop.time()
            if remaining <= 0:
                return
            # Clear-then-recheck so a READY that arrives between the loop guard
            # and this wait isn't lost.
            self._ready_changed.clear()
            if self._stop_event.is_set() or self._all_live_humans_ready():
                return
            stop_wait = asyncio.ensure_future(self._stop_event.wait())
            ready_wait = asyncio.ensure_future(self._ready_changed.wait())
            try:
                await asyncio.wait(
                    {stop_wait, ready_wait},
                    timeout=remaining,
                    return_when=asyncio.FIRST_COMPLETED,
                )
            finally:
                stop_wait.cancel()
                ready_wait.cancel()

    async def on_socket_dropped(self, conn: Any) -> None:
        await self._sessions.on_socket_dropped(conn)

    # --- hand loop ---

    def _build_adapters_for_hand(self) -> list[SeatAdapter]:
        """Build the per-seat adapter list from this table's composition.

        - ``kind: "bot"`` seat → the adapter for its selected ``bot_id`` (via
          ``seat_bots.build_bot_adapter``; ``None`` → the default bot).  A seat
          with a non-empty ``canned_seat_actions`` script overrides this with a
          ``CannedAdapter`` (the test seam).
        - ``kind: "human"`` seat with a bound identity → ``HumanAdapter``.
        - ``kind: "human"`` seat with no bound identity → ``AutoPassAdapter``
          (interim safety net; 8.7.d gates the hand on all-humans-LIVE so
          this branch becomes unreachable on the production path).

        Bot pacing (Layer-8 §2): when ``self._bot_pacing_enabled`` is True,
        every non-human adapter is wrapped in ``PacedAdapter`` so its
        ``decide`` calls sleep a per-prompt uniform-random delay.  Human
        adapters are never wrapped — humans pace themselves.
        """
        adapters: list[SeatAdapter] = []
        for seat in range(4):
            if self.is_human_seat(seat):
                identity = self._human_identities.get(seat)
                if identity is None:
                    adapters.append(cast(SeatAdapter, AutoPassAdapter()))
                else:
                    session = self._sessions.seat(seat)
                    adapters.append(
                        cast(
                            SeatAdapter,
                            HumanAdapter(
                                session=session,
                                identity=identity,
                                stats_provider=stats_for_prompt,
                            ),
                        )
                    )
            elif seat in self._scripted_seats:
                adapters.append(cast(SeatAdapter, self._canned_adapters[seat]))
            else:
                bot_id = self._seats[seat].bot_id or DEFAULT_BOT_ID
                adapters.append(build_bot_adapter(bot_id))

        if self._bot_pacing_enabled:
            for i, a in enumerate(adapters):
                if a.kind in ("bot", "canned"):
                    adapters[i] = cast(
                        SeatAdapter,
                        PacedAdapter(
                            a,
                            min_s=self._bot_min_delay_s,
                            max_s=self._bot_max_delay_s,
                        ),
                    )
        return adapters

    async def _run_hand_loop(self) -> None:
        """Background task: run hands sequentially until max_hands reached."""
        try:
            while True:
                hand_seed = self._seed + self._hand_index
                adapters = self._build_adapters_for_hand()

                current_hand_id = self._hand_id_for_hand(self._hand_index)
                current_record_path = self._record_path_for_hand(self._hand_index)
                self._reserve_hand_row(
                    hand_id=current_hand_id,
                    record_path=current_record_path,
                    hand_seed=hand_seed,
                )

                final_state: GameState | None = None
                try:
                    final_state = await mgr.run_hand(
                        adapters=adapters,
                        ruleset=self._ruleset,
                        seed=hand_seed,
                        hand_id=current_hand_id,
                        record_path=current_record_path,
                        server_info=self._server_info,
                        decide_timeout_seconds=self._decide_timeout_seconds,
                        decide_timeouts=self._decide_timeouts,
                        strike_limit=self._strike_limit,
                        event_callback=self._sessions.fanout_event_to_spectators,
                        state_callback=self._on_hand_state,
                        dealer_seat=self._dealer_seat,
                        hand_index_in_match=self._hand_index,
                    )
                finally:
                    self._finalize_hand_row(
                        hand_id=current_hand_id,
                        record_path=current_record_path,
                        final_state=final_state,
                    )

                # Spec 40: fold this hand's zero-sum settlement into the running
                # match total *before* the next snapshot is built, so the new
                # hand's board shows up-to-date standings. A draw / aborted hand
                # (final_state is None) moves no points but still counts.
                self._match_score.record_hand(
                    final_state["terminal"] if final_state is not None else None
                )

                next_hand_index = self._hand_index + 1
                if self._max_hands is not None and next_hand_index >= self._max_hands:
                    break
                # Graceful drain: finish the hand we just completed, then stop —
                # the stop may already be requested, or fire during the
                # between-hand pause, which we wait on interruptibly so a SIGTERM
                # never starts a fresh hand.
                if self._stop_event.is_set():
                    break
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(
                        self._stop_event.wait(),
                        timeout=self._between_hand_pause_seconds,
                    )
                if self._stop_event.is_set():
                    break

                # FB-02: hold the HAND_END summary until every LIVE human seat
                # acknowledges it (READY), with a timeout safety net — instead of
                # auto-advancing in ~1s. Pure-bot tables aren't gated (no LIVE
                # humans → returns immediately).
                await self._await_humans_ready()
                if self._stop_event.is_set():
                    break

                self._dealer_seat = next_dealer(
                    self._dealer_seat,
                    final_state["terminal"] if final_state is not None else None,
                    resolve_config(self._ruleset),
                )
                self._hand_index = next_hand_index
                self._initial_state = initial_state(
                    self._ruleset,
                    seed=self._seed + self._hand_index,
                    dealer_seat=self._dealer_seat,
                    hand_index=self._hand_index,
                )
                # FB-17: between hands the snapshot must show the *next* deal,
                # not the finished hand — drop the live ref before the
                # begin_next_hand fanout re-snapshots every seat. (After the
                # final hand the loop breaks above instead, so the terminal
                # state stays visible to post-game reconnects.)
                self._live_state = None
                await self._sessions.begin_next_hand()
        except asyncio.CancelledError:
            # Normal shutdown / drain — never swallow cancellation. But DO log
            # it: an unrequested cancel looks exactly like FB-13's silent
            # mid-hand dead-stop (no HAND_END, no FOOTER, nothing in the log).
            _logger.info(
                "hand_loop_cancelled table=%s hand_id=%s hand_index=%s",
                self._table_id,
                self._hand_id_for_hand(self._hand_index),
                self._hand_index,
            )
            raise
        except Exception:
            # FB-01: same silent-hang guard as WebOrchestrator._run_hand_loop, on
            # the *live* multi-table path. An unhandled exception here used to kill
            # the task silently — clients frozen, no HAND_END, record truncated
            # mid-hand. Log with full context, then tear the table down gracefully.
            # [DEF-01] parked investigation: see docs/specs/feedback-backlog.md.
            # This stack trace is exactly what that ledger row is waiting for.
            _logger.exception(
                "hand_loop_crashed [DEF-01] table=%s hand_id=%s seed=%s hand_index=%s",
                self._table_id,
                self._hand_id_for_hand(self._hand_index),
                self._seed + self._hand_index,
                self._hand_index,
            )
            with contextlib.suppress(Exception):
                await self._sessions.shutdown(reason="hand_aborted")
        finally:
            self._match_done.set()

    # --- persistence hooks ---

    def _record_path_relative(self, record_path: Path) -> str:
        """The persistence row stores the path relative to ``data_dir``.

        Falls back to the absolute path string if we can't compute a relative
        one (tests sometimes pass an unrelated tmp path).
        """
        if self._data_dir is not None:
            try:
                return str(record_path.relative_to(self._data_dir))
            except ValueError:
                pass
        return str(record_path)

    def _reserve_hand_row(
        self,
        *,
        hand_id: str,
        record_path: Path,
        hand_seed: int,
    ) -> None:
        if self._persistence is None:
            return
        participants: list[Participant] = []
        for seat in range(4):
            if self.is_human_seat(seat):
                identity = self._human_identities.get(seat)
                if identity is not None:
                    account_id = _account_id_from_user_id(identity["user_id"])
                    seat_kind: str = "human"
                else:
                    # Unattached human seat → AutoPassAdapter is in play.
                    account_id = None
                    seat_kind = "canned"
            else:
                account_id = None
                seat_kind = "canned"
            participants.append(
                Participant(
                    seat=seat,
                    account_id=account_id,
                    seat_kind=seat_kind,  # type: ignore[arg-type]
                    wind=f"F{(seat - self._dealer_seat) % 4 + 1}",
                    final_score_delta=None,
                )
            )
        try:
            self._persistence.reserve_hand(
                hand_id=hand_id,
                match_id=self._match_id,
                hand_index_in_match=self._hand_index,
                ruleset_id=self._ruleset.get("id", "mcr-2006"),
                ruleset_config_hash=_ruleset_config_hash(self._ruleset),
                started_at_ms=int(time.time() * 1000),
                master_seed=str(hand_seed),
                record_path=self._record_path_relative(record_path),
                server_version=str(self._server_info.get("version", "0.1.0")),
                source="live",
                participants=participants,
            )
        except Exception:
            _logger.exception(
                "persistence.reserve_hand_failed",
                extra={"hand_id": hand_id, "table_id": self._table_id},
            )

    def _finalize_hand_row(
        self,
        *,
        hand_id: str,
        record_path: Path,
        final_state: GameState | None,
    ) -> None:
        if self._persistence is None:
            return
        terminal = final_state["terminal"] if final_state is not None else None
        if terminal is None:
            terminal_kind = "ABORTED"
            winner_seat: int | None = None
            fan_total: int | None = None
            scores = {seat: 0 for seat in range(4)}
        else:
            engine_kind = terminal["kind"]
            terminal_kind = "EXHAUSTIVE_DRAW" if engine_kind == "DRAW" else engine_kind
            winner_seat = terminal["winner"]
            fan_total = terminal["fan_total"] if terminal["fan_total"] is not None else None
            scores = {seat: int(terminal["score_delta"][seat]) for seat in range(4)}
        checksum = _read_footer_checksum(record_path) or ""
        try:
            self._persistence.finalize_hand(
                hand_id,
                ended_at_ms=int(time.time() * 1000),
                terminal_kind=terminal_kind,
                winner_seat=winner_seat,
                fan_total=fan_total,
                record_checksum=checksum,
                participants_scores=scores,
            )
        except Exception:
            _logger.exception(
                "persistence.finalize_hand_failed",
                extra={"hand_id": hand_id, "table_id": self._table_id},
            )

    # --- drain ---

    @property
    def hand_task(self) -> asyncio.Task[None] | None:
        """The background hand-loop task, if a hand has been started."""
        return self._hand_task

    def request_stop(self) -> None:
        """Graceful-drain signal: finish the current hand, then stop the loop.

        Idempotent.  Does *not* cancel — the in-flight hand runs to its FOOTER.
        The lifecycle layer escalates to ``close`` (cancel) if the loop hasn't
        exited within the drain timeout (server-lifecycle.md § Drain timeout
        escalation).
        """
        self._stop_event.set()

    # --- close ---

    async def close(self, *, reason: str = "table_closed") -> None:
        """Cancel the hand task and detach all sessions with *reason*."""
        if self._hand_task is not None and not self._hand_task.done():
            self._hand_task.cancel()
            with contextlib.suppress(BaseException):
                await self._hand_task
        await self._sessions.shutdown(reason=reason)


# ---------------------------------------------------------------------------
# TableRegistry
# ---------------------------------------------------------------------------


class TableRegistry:
    """In-memory map of live ``TableHandle``s.

    ``create_table`` allocates a table_id and starts the handle.
    ``create_table_direct`` is the same but callable without a WebSocket
    connection — used in tests and the admin CLI (Step 8.5).

    Spec: docs/specs/server-lifecycle.md § Table registry.
    """

    def __init__(
        self, persistence: Persistence | None = None, *, boot_id: str | None = None
    ) -> None:
        self._tables: dict[str, TableHandle] = {}
        self._next_id: int = 1
        self._accepting_new: bool = True
        self._persistence = persistence
        # Per-process namespace for record paths + hand/match ids. Table ids
        # restart at 1 each boot, so without this the second boot's first table
        # reused records/t1/hand_0000.jsonl — overwriting the prior record file
        # and tripping the hand_index PK(hand_id)/UNIQUE(record_path), which hid
        # those hands from history/replay (DEF-13). A fresh id per process makes
        # every derived identifier boot-unique. Injectable for deterministic
        # restart tests.
        self._boot_id: str = boot_id or _new_boot_id()
        # Monotonic timestamp set when drain begins; read by /health to report
        # drain_remaining_s.  None until drain_all() is called.
        self.drain_started_monotonic: float | None = None

    @property
    def accepting_new(self) -> bool:
        return self._accepting_new

    # --- create ---

    def create_table_direct(
        self,
        *,
        ruleset: RuleSetRef,
        seed: int,
        server_info: dict[str, Any],
        data_dir: Path,
        canned_seat_actions: dict[int, list[Action]] | None = None,
        decide_timeout_seconds: float = 30.0,
        decide_timeouts: DecideTimeouts | None = None,
        hold_seconds: float = DEFAULT_HOLD_SECONDS,
        strike_limit: int = 3,
        max_hands: int | None = 1,
        between_hand_pause_seconds: float = 2.0,
        seats: SeatsTuple | None = None,
        bot_pacing_enabled: bool = False,
        bot_min_delay_s: float = 5.0,
        bot_max_delay_s: float = 10.0,
    ) -> str:
        """Allocate and register a new ``TableHandle``.  Returns the table_id.

        ``seats`` is the parsed composition from ``CREATE_TABLE.seats[]``
        (see ``mahjong.server.seats``).  ``None`` falls back to
        ``DEFAULT_COMPOSITION`` (legacy single-human shape).

        Raises ``ShuttingDown`` if the registry is draining.
        """
        if not self._accepting_new:
            raise ShuttingDown("Registry is draining; no new tables")

        table_id = str(self._next_id)
        self._next_id += 1

        ruleset_id = ruleset.get("id", "mcr-2006")
        # Boot-scope the record path + hand id so a restart (table ids reset to
        # 1) can't overwrite a prior run's record or collide on the hand_index
        # PK/UNIQUE (DEF-13).
        hand_id = f"{self._boot_id}-t{table_id}-h0"
        records_dir = data_dir / "records" / self._boot_id / f"t{table_id}"
        records_dir.mkdir(parents=True, exist_ok=True)
        record_path = records_dir / "hand_0000.jsonl"

        handle = TableHandle(
            table_id=table_id,
            ruleset=ruleset,
            seed=seed + int(table_id),
            hand_id=hand_id,
            record_path=record_path,
            boot_id=self._boot_id,
            server_info=server_info,
            canned_seat_actions=canned_seat_actions,
            decide_timeout_seconds=decide_timeout_seconds,
            decide_timeouts=decide_timeouts,
            hold_seconds=hold_seconds,
            strike_limit=strike_limit,
            max_hands=max_hands,
            between_hand_pause_seconds=between_hand_pause_seconds,
            persistence=self._persistence,
            data_dir=data_dir,
            seats=seats,
            bot_pacing_enabled=bot_pacing_enabled,
            bot_min_delay_s=bot_min_delay_s,
            bot_max_delay_s=bot_max_delay_s,
        )
        self._tables[table_id] = handle
        _logger.info("table.created", extra={"table_id": table_id, "ruleset": ruleset_id})
        return table_id

    # --- read ---

    def list_tables(self) -> list[TableSummary]:
        """Snapshot of all live tables for a LIST_TABLES wire response."""
        return [h.summary() for h in self._tables.values()]

    def seat_holds_for(self, user_id: str) -> list[SeatHold]:
        """All seats *user_id* currently holds across every live table, for the
        post-auth rejoin-discovery list (reconnect-rejoin.md, FB-03)."""
        holds: list[SeatHold] = []
        for handle in self._tables.values():
            holds.extend(handle.seat_holds_for(user_id))
        return holds

    def get_table(self, table_id: str) -> TableHandle:
        """Return the ``TableHandle`` for *table_id*.

        Raises ``TableNotFound`` if it doesn't exist.
        """
        try:
            return self._tables[table_id]
        except KeyError:
            raise TableNotFound(table_id) from None

    # --- close ---

    async def close_table(self, table_id: str, *, reason: str = "table_closed") -> None:
        """Close one table: cancel its hand task, detach everyone, remove from
        the registry.  Raises ``TableNotFound`` if unknown."""
        handle = self.get_table(table_id)
        await handle.close(reason=reason)
        del self._tables[table_id]
        _logger.info("table.closed", extra={"table_id": table_id, "reason": reason})

    # --- drain ---

    async def drain_all(self) -> None:
        """Phase 1 of graceful shutdown (server-lifecycle.md § Graceful shutdown).

        Refuse new ``CREATE_TABLE`` (``_accepting_new = False``) and signal every
        live table to finish its *current* hand and stop.  Open hands are *not*
        cancelled here — that is the escalation step, owned by the lifecycle
        layer via ``await_tables_drained`` + ``close``.
        """
        self._accepting_new = False
        self.drain_started_monotonic = time.monotonic()
        for handle in self._tables.values():
            handle.request_stop()
        _logger.info("registry.drain_started", extra={"tables": len(self._tables)})

    async def await_tables_drained(self, timeout_s: float) -> list[str]:
        """Phase 2: wait up to ``timeout_s`` for every table's hand loop to exit
        naturally.  Returns the table_ids whose hand task is *still running* at
        the deadline (the escalation set).  Does not cancel — the caller does
        that via ``close`` after logging ``shutdown.timeout``.
        """
        pending = {
            tid: h.hand_task
            for tid, h in self._tables.items()
            if h.hand_task is not None and not h.hand_task.done()
        }
        if not pending:
            return []
        await asyncio.wait(pending.values(), timeout=timeout_s)
        return [tid for tid, task in pending.items() if not task.done()]


__all__ = [
    "ShuttingDown",
    "TableHandle",
    "TableNotFound",
    "TableRegistry",
    "TableSummary",
]

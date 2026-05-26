"""Table registry — in-memory map of live TableManager instances.

Spec: docs/specs/server-lifecycle.md § Table registry.

``TableHandle``
    Bundles one table's ``TableSessions`` + hand-orchestration task.  The
    hand loop starts when the first client ATTACHes to seat 0.

``TableRegistry``
    The dict of live ``TableHandle``s.  Supports ``create_table``,
    ``list_tables``, ``get_table``, ``close_table``, ``drain_all``.

Decisions:
  - table_id is an auto-incrementing integer (converted to str at API boundary).
  - One seat (seat 0) is human; seats 1-3 are CannedAdapters.
  - Persistence wiring (reserve_hand / finalize_hand) lands in Step 8.5.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Any, cast

from mahjong.adapters.base import HumanIdentity, SeatAdapter
from mahjong.adapters.canned import CannedAdapter
from mahjong.adapters.human import HumanAdapter
from mahjong.engine import initial_state
from mahjong.engine.state import project as project_state
from mahjong.engine.types import Action, GameState, RuleSetRef
from mahjong.persistence import Participant, Persistence
from mahjong.sessions import TableSessions
from mahjong.sessions.mux import DEFAULT_HOLD_SECONDS
from mahjong.table import manager as mgr

_logger = logging.getLogger(__name__)

HUMAN_SEAT: int = 0


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
class TableSummary:
    """Snapshot of one table for LIST_TABLES wire response."""

    table_id: str
    ruleset: str
    hand_index: int
    phase: str  # "WAITING_FOR_PLAYERS" | "IN_PROGRESS"

    def to_wire(self) -> dict[str, Any]:
        return {
            "table_id": int(self.table_id),
            "ruleset": self.ruleset,
            "hand_index": self.hand_index,
            "phase": self.phase,
            "seats": [],  # full seat detail added in Step 8.5 when auth is wired
        }


# ---------------------------------------------------------------------------
# TableHandle
# ---------------------------------------------------------------------------


class TableHandle:
    """Single-table state: TableSessions + hand-orchestration task.

    The hand loop mirrors ``WebOrchestrator._run_hand_loop``; the two share
    no code to avoid coupling until the architecture stabilises in Step 8.5
    (YAGNI — keep WebOrchestrator's tests independent).

    The *first* client that ATTACHes to seat 0 kicks off the hand loop.
    Subsequent clients on the same seat reconnect via ``TableSessions.attach``
    (seat-hold + resume buffer mechanics from session-mux.md).
    """

    def __init__(
        self,
        *,
        table_id: str,
        ruleset: RuleSetRef,
        seed: int,
        hand_id: str,
        record_path: Path,
        server_info: dict[str, Any],
        canned_seat_actions: dict[int, list[Action]] | None = None,
        decide_timeout_seconds: float = 30.0,
        hold_seconds: float = DEFAULT_HOLD_SECONDS,
        strike_limit: int = 3,
        max_hands: int | None = 1,
        between_hand_pause_seconds: float = 2.0,
        persistence: Persistence | None = None,
        data_dir: Path | None = None,
    ) -> None:
        self._table_id = table_id
        self._ruleset = ruleset
        self._seed = seed
        self._hand_id = hand_id
        self._record_path = record_path
        self._server_info = server_info
        self._decide_timeout_seconds = decide_timeout_seconds
        self._hold_seconds = hold_seconds
        self._strike_limit = strike_limit
        self._max_hands = max_hands
        self._between_hand_pause_seconds = between_hand_pause_seconds
        self._persistence = persistence
        self._data_dir = data_dir
        self._match_id = f"match_t{table_id}"

        # Between-hand mutable state
        self._hand_index: int = 0
        self._dealer_seat: int = 0
        self._initial_state: GameState = initial_state(
            ruleset, seed=seed, dealer_seat=0, hand_index=0
        )

        # Seats 1-3 are CannedAdapters
        actions_by_seat = canned_seat_actions or {}
        self._canned_adapters: dict[int, CannedAdapter] = {
            seat: CannedAdapter(
                identity={"kind": "canned", "script": "pass"},
                actions=list(actions_by_seat.get(seat, [])),
            )
            for seat in range(4)
            if seat != HUMAN_SEAT
        }

        self._sessions: TableSessions = TableSessions(
            table_id=int(table_id),
            snapshot_provider=self._snapshot_provider,
            hand_index_provider=lambda: self._hand_index,
            hold_seconds=self._hold_seconds,
        )
        self._hand_task: asyncio.Task[None] | None = None
        self._match_done: asyncio.Event = asyncio.Event()
        self._start_hand_lock: asyncio.Lock = asyncio.Lock()

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

    # --- summary ---

    def summary(self) -> TableSummary:
        in_progress = (
            self._hand_task is not None and not self._hand_task.done()
        )
        phase = "IN_PROGRESS" if in_progress else "WAITING_FOR_PLAYERS"
        return TableSummary(
            table_id=self._table_id,
            ruleset=self._ruleset.get("id", "mcr-2006"),
            hand_index=self._hand_index,
            phase=phase,
        )

    # --- snapshot provider (for TableSessions) ---

    def _snapshot_provider(self, seat: int | None) -> dict[str, Any]:
        return cast(dict[str, Any], project_state(self._initial_state, seat))

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
        """Attach *conn* to *seat*.  Kick off the hand loop on first attach to
        seat 0.  Returns True if the attach succeeded."""
        outcome = await self._sessions.attach(
            conn, user_id=identity["user_id"], seat=seat
        )
        if not outcome.ok:
            return False
        if seat == HUMAN_SEAT:
            async with self._start_hand_lock:
                if self._hand_task is None:
                    self._hand_task = asyncio.create_task(
                        self._run_hand_loop(identity)
                    )
        return True

    async def spectate(self, conn: Any, *, user_id: str) -> bool:
        outcome = await self._sessions.spectate(conn, user_id=user_id)
        return outcome.ok

    async def handle_inbound(self, conn: Any, msg: dict[str, Any]) -> None:
        await self._sessions.handle_inbound(conn, msg)

    async def on_socket_dropped(self, conn: Any) -> None:
        await self._sessions.on_socket_dropped(conn)

    # --- hand loop ---

    async def _run_hand_loop(self, human_identity: HumanIdentity) -> None:
        """Background task: run hands sequentially until max_hands reached."""
        try:
            while True:
                hand_seed = self._seed + self._hand_index
                human_session = self._sessions.seat(HUMAN_SEAT)
                human = HumanAdapter(session=human_session, identity=human_identity)
                adapters: list[SeatAdapter] = [
                    cast(SeatAdapter, human),
                    cast(SeatAdapter, self._canned_adapters[1]),
                    cast(SeatAdapter, self._canned_adapters[2]),
                    cast(SeatAdapter, self._canned_adapters[3]),
                ]

                current_hand_id = self._hand_id_for_hand(self._hand_index)
                current_record_path = self._record_path_for_hand(self._hand_index)
                self._reserve_hand_row(
                    hand_id=current_hand_id,
                    record_path=current_record_path,
                    human_identity=human_identity,
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
                        strike_limit=self._strike_limit,
                        event_callback=self._sessions.fanout_event_to_spectators,
                        dealer_seat=self._dealer_seat,
                        hand_index_in_match=self._hand_index,
                    )
                finally:
                    self._finalize_hand_row(
                        hand_id=current_hand_id,
                        record_path=current_record_path,
                        final_state=final_state,
                    )

                next_hand_index = self._hand_index + 1
                if self._max_hands is not None and next_hand_index >= self._max_hands:
                    break

                await asyncio.sleep(self._between_hand_pause_seconds)

                self._dealer_seat = (self._dealer_seat + 1) % 4
                self._hand_index = next_hand_index
                self._initial_state = initial_state(
                    self._ruleset,
                    seed=self._seed + self._hand_index,
                    dealer_seat=self._dealer_seat,
                    hand_index=self._hand_index,
                )
                await self._sessions.begin_next_hand()
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
        human_identity: HumanIdentity,
        hand_seed: int,
    ) -> None:
        if self._persistence is None:
            return
        human_account_id = _account_id_from_user_id(human_identity["user_id"])
        participants = [
            Participant(
                seat=seat,
                account_id=(human_account_id if seat == HUMAN_SEAT else None),
                seat_kind=("human" if seat == HUMAN_SEAT else "canned"),
                wind=f"F{(seat - self._dealer_seat) % 4 + 1}",
                final_score_delta=None,
            )
            for seat in range(4)
        ]
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
            scores = {
                seat: int(terminal["score_delta"][seat]) for seat in range(4)
            }
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

    def __init__(self, persistence: Persistence | None = None) -> None:
        self._tables: dict[str, TableHandle] = {}
        self._next_id: int = 1
        self._accepting_new: bool = True
        self._persistence = persistence

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
        hold_seconds: float = DEFAULT_HOLD_SECONDS,
        strike_limit: int = 3,
        max_hands: int | None = 1,
        between_hand_pause_seconds: float = 2.0,
    ) -> str:
        """Allocate and register a new ``TableHandle``.  Returns the table_id.

        Raises ``ShuttingDown`` if the registry is draining.
        """
        if not self._accepting_new:
            raise ShuttingDown("Registry is draining; no new tables")

        table_id = str(self._next_id)
        self._next_id += 1

        ruleset_id = ruleset.get("id", "mcr-2006")
        hand_id = f"t{table_id}-h0"
        records_dir = data_dir / "records" / f"t{table_id}"
        records_dir.mkdir(parents=True, exist_ok=True)
        record_path = records_dir / "hand_0000.jsonl"

        handle = TableHandle(
            table_id=table_id,
            ruleset=ruleset,
            seed=seed + int(table_id),
            hand_id=hand_id,
            record_path=record_path,
            server_info=server_info,
            canned_seat_actions=canned_seat_actions,
            decide_timeout_seconds=decide_timeout_seconds,
            hold_seconds=hold_seconds,
            strike_limit=strike_limit,
            max_hands=max_hands,
            between_hand_pause_seconds=between_hand_pause_seconds,
            persistence=self._persistence,
            data_dir=data_dir,
        )
        self._tables[table_id] = handle
        _logger.info("table.created", extra={"table_id": table_id, "ruleset": ruleset_id})
        return table_id

    # --- read ---

    def list_tables(self) -> list[TableSummary]:
        """Snapshot of all live tables for a LIST_TABLES wire response."""
        return [h.summary() for h in self._tables.values()]

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
        """Shutdown path: refuse new CREATE_TABLE; wait for all hands to finish.

        This sets ``_accepting_new = False`` immediately.  Open hands are *not*
        cancelled here — ``drain_all`` is called at the start of a graceful
        shutdown, and the lifecycle layer waits on each table's match_done.
        Full graceful-drain logic (wait + timeout + cancel) lands in Step 8.5.
        """
        self._accepting_new = False
        _logger.info("registry.drain_started")


__all__ = [
    "ShuttingDown",
    "TableHandle",
    "TableNotFound",
    "TableRegistry",
    "TableSummary",
]

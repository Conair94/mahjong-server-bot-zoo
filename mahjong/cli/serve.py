"""``python -m mahjong serve`` — run the WebSocket mahjong server.

Spec: docs/specs/server-lifecycle.md § Process entry point.

Pragmatic-cut subset of the full spec for the friends-and-family deploy:

- Loads ``ServerConfig`` from env vars.
- Opens (or creates) the SQLite DB; applies migrations; runs an integrity
  check; marks any in-progress hands from a prior crash as ABORTED.
- Constructs ``MultiTableOrchestrator`` with auth required.
- Serves the bundled web client at ``/`` (static).
- Drains gracefully on SIGTERM / SIGINT: stops accepting, closes the registry
  (cancels each table's hand task), closes the DB.

Deferred (vs. full spec): ``/health`` endpoint, drain-timeout escalation,
periodic WAL checkpoint task, structured JSON logging.  These can land
additively when first needed.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import signal
import sys
import time
from pathlib import Path
from typing import cast

from mahjong.engine.rulesets import MANIFEST
from mahjong.engine.types import RuleSetRef
from mahjong.persistence import Persistence
from mahjong.server.config import ServerConfig, load_config_from_env
from mahjong.server.orchestrator import MultiTableOrchestrator
from mahjong.table.manager import DecideTimeouts
from mahjong.web import static_root

_logger = logging.getLogger("mahjong.serve")


def _setup_logging(cfg: ServerConfig) -> None:
    """Plain ``%(levelname)s %(name)s %(message)s`` for the pragmatic cut.

    JSON logging is in the spec but is additive; the structured event names
    map cleanly to a JSON formatter later.
    """
    level = getattr(logging, cfg.log_level.upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stdout,
    )


def _ruleset_ref(cfg: ServerConfig) -> RuleSetRef:
    """Construct the canonical RuleSetRef for the configured default ruleset."""
    rid = cfg.default_ruleset
    if rid not in MANIFEST:
        raise SystemExit(f"unknown ruleset: {rid!r}")
    return cast(RuleSetRef, {"id": rid, "version": 1, "config_hash": MANIFEST[rid]})


def _open_persistence(cfg: ServerConfig) -> Persistence:
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    cfg.records_dir.mkdir(parents=True, exist_ok=True)
    p = Persistence(cfg.db_path, cfg.data_dir)
    return p


def _mark_in_progress_aborted(p: Persistence) -> int:
    """Server-lifecycle.md § In-flight at crash. Returns count finalised."""
    rows = p.find_in_progress_hands()
    if not rows:
        return 0
    now_ms = int(time.time() * 1000)
    for row in rows:
        try:
            p.finalize_hand(
                row.hand_id,
                ended_at_ms=now_ms,
                terminal_kind="ABORTED",
                winner_seat=None,
                fan_total=None,
                record_checksum="",
                participants_scores={part.seat: 0 for part in row.participants},
            )
        except Exception:
            _logger.exception(
                "startup.abort_in_progress_failed",
                extra={"hand_id": row.hand_id},
            )
    return len(rows)


async def _serve(cfg: ServerConfig, static_dir: Path | None) -> int:
    persistence = _open_persistence(cfg)

    report = persistence.integrity_check()
    if not report.pragma_ok:
        _logger.error("db.corrupt pragma_result=not_ok")
        persistence.close()
        return 1
    if report.missing_files:
        _logger.warning(
            "startup.missing_record_files count=%d", report.missing_files
        )
    if report.orphaned_files:
        _logger.warning(
            "startup.orphaned_record_files count=%d "
            "(run `python -m mahjong rebuild-index` to reclaim)",
            report.orphaned_files,
        )

    aborted = _mark_in_progress_aborted(persistence)
    if aborted:
        _logger.warning("startup.in_progress_aborted count=%d", aborted)

    orch = MultiTableOrchestrator(
        host=cfg.listen_host,
        port=cfg.listen_port,
        data_dir=cfg.data_dir,
        ruleset=_ruleset_ref(cfg),
        seed=int(time.time()),  # nondeterministic for live play; deterministic seeds are for self-play
        server_info={
            "version": cfg.server_version,
            "server_id": cfg.server_id,
            "git_sha": "unknown",
            "host": cfg.listen_host,
        },
        static_dir=static_dir,
        hold_seconds=float(cfg.seat_hold_seconds),
        max_hands=None,  # play indefinitely
        between_hand_pause_seconds=2.0,
        persistence=persistence,
        decide_timeouts=DecideTimeouts(
            human_discard_s=float(cfg.decide_timeout_human_discard_s),
            human_claim_s=float(cfg.decide_timeout_human_claim_s),
            bot_s=float(cfg.decide_timeout_bot_s),
        ),
        bot_pacing_enabled=cfg.bot_pacing_enabled,
        bot_min_delay_s=cfg.bot_min_delay_s,
        bot_max_delay_s=cfg.bot_max_delay_s,
    )
    await orch.start()
    _logger.info(
        "server.ready listen=%s data_dir=%s",
        f"{cfg.listen_host}:{orch.port}",
        cfg.data_dir,
    )
    print(
        f"mahjong server listening on ws://{cfg.listen_host}:{orch.port}",
        file=sys.stderr,
    )
    print(
        f"web client:           http://{cfg.listen_host}:{orch.port}/",
        file=sys.stderr,
    )
    print("Press Ctrl-C to stop.", file=sys.stderr)

    # Wait for SIGTERM / SIGINT
    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    def _signal_handler() -> None:
        if not stop_event.is_set():
            _logger.info("server.shutdown_signal")
            stop_event.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        # Windows lacks add_signal_handler — falls back to default. We deploy
        # on Linux, so this is purely for dev ergonomics.
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, _signal_handler)

    try:
        await stop_event.wait()
    finally:
        _logger.info("server.draining")
        # Pragmatic drain: refuse new tables, close all live tables, close DB.
        await orch.registry.drain_all()
        try:
            await asyncio.wait_for(
                orch.close(), timeout=float(cfg.shutdown_timeout_s)
            )
        except TimeoutError:
            _logger.error("shutdown.timeout shutdown_timeout_s=%d", cfg.shutdown_timeout_s)
        persistence.close()
        _logger.info("server.exited")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="mahjong serve",
        description="Run the WebSocket mahjong server.",
    )
    parser.add_argument(
        "--no-static",
        action="store_true",
        help="don't serve the bundled web client at /",
    )
    args = parser.parse_args(argv)

    try:
        cfg, unknown = load_config_from_env()
    except Exception as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 1

    _setup_logging(cfg)

    for var in unknown:
        _logger.warning("config.unknown_var %s (typo?)", var)

    static_dir: Path | None = None if args.no_static else static_root()

    try:
        return asyncio.run(_serve(cfg, static_dir))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

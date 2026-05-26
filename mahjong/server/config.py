"""Server configuration loaded from environment variables.

Spec: docs/specs/server-lifecycle.md § Configuration.

12-factor: every runtime knob is a ``MAHJONG_*`` env var with a documented
default.  ``load_config_from_env()`` returns a frozen ``ServerConfig`` or
raises ``ConfigError`` on a malformed value.  Unknown ``MAHJONG_*`` vars are
collected and returned for the caller to log as warnings (catches typos).
"""

from __future__ import annotations

import dataclasses
import os
from collections.abc import Mapping
from pathlib import Path


class ConfigError(ValueError):
    """Raised on a malformed MAHJONG_* env var.  Message names the var."""


# Frozen so tests can compare instances structurally.
@dataclasses.dataclass(frozen=True)
class ServerConfig:
    listen_host: str
    listen_port: int
    data_dir: Path
    seat_hold_seconds: int
    heartbeat_interval_s: int
    resume_buffer_size: int
    session_lifetime_hours: int
    max_spectators_per_table: int
    default_ruleset: str
    shutdown_timeout_s: int
    wal_checkpoint_interval_s: int
    log_level: str
    log_format: str  # "json" | "console"
    server_version: str
    server_id: str
    # Pragmatic-cut omissions vs spec: health_listen_addr, bot_manifest_dir.

    @property
    def db_path(self) -> Path:
        return self.data_dir / "mahjong.db"

    @property
    def records_dir(self) -> Path:
        return self.data_dir / "records"

    @property
    def listen_addr(self) -> str:
        return f"{self.listen_host}:{self.listen_port}"


# (var_name, attribute, parser, default)
def _parse_int(name: str, raw: str) -> int:
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name}={raw!r}: not an integer") from exc


def _parse_listen_addr(name: str, raw: str) -> tuple[str, int]:
    if ":" not in raw:
        raise ConfigError(f"{name}={raw!r}: expected host:port")
    host, _, port_s = raw.rpartition(":")
    if not host or not port_s:
        raise ConfigError(f"{name}={raw!r}: expected host:port")
    return host, _parse_int(name, port_s)


_KNOWN_VARS: frozenset[str] = frozenset(
    {
        "MAHJONG_LISTEN_ADDR",
        "MAHJONG_DATA_DIR",
        "MAHJONG_SEAT_HOLD_SECONDS",
        "MAHJONG_HEARTBEAT_INTERVAL_SECONDS",
        "MAHJONG_RESUME_BUFFER_SIZE",
        "MAHJONG_SESSION_LIFETIME_HOURS",
        "MAHJONG_MAX_SPECTATORS_PER_TABLE",
        "MAHJONG_DEFAULT_RULESET",
        "MAHJONG_SHUTDOWN_TIMEOUT_SECONDS",
        "MAHJONG_WAL_CHECKPOINT_INTERVAL_SECONDS",
        "MAHJONG_LOG_LEVEL",
        "MAHJONG_LOG_FORMAT",
    }
)


def load_config_from_env(
    env: Mapping[str, str] | None = None,
) -> tuple[ServerConfig, list[str]]:
    """Parse MAHJONG_* env vars.

    Returns ``(config, unknown_vars)`` where ``unknown_vars`` is a sorted list
    of ``MAHJONG_*`` keys the loader didn't recognise (caller should log).
    Raises ``ConfigError`` on a malformed known var.
    """
    e = os.environ if env is None else env

    addr_raw = e.get("MAHJONG_LISTEN_ADDR", "127.0.0.1:8400")
    host, port = _parse_listen_addr("MAHJONG_LISTEN_ADDR", addr_raw)

    data_dir = Path(e.get("MAHJONG_DATA_DIR", "./var/mahjong"))

    cfg = ServerConfig(
        listen_host=host,
        listen_port=port,
        data_dir=data_dir,
        seat_hold_seconds=_parse_int(
            "MAHJONG_SEAT_HOLD_SECONDS",
            e.get("MAHJONG_SEAT_HOLD_SECONDS", "60"),
        ),
        heartbeat_interval_s=_parse_int(
            "MAHJONG_HEARTBEAT_INTERVAL_SECONDS",
            e.get("MAHJONG_HEARTBEAT_INTERVAL_SECONDS", "30"),
        ),
        resume_buffer_size=_parse_int(
            "MAHJONG_RESUME_BUFFER_SIZE",
            e.get("MAHJONG_RESUME_BUFFER_SIZE", "256"),
        ),
        session_lifetime_hours=_parse_int(
            "MAHJONG_SESSION_LIFETIME_HOURS",
            e.get("MAHJONG_SESSION_LIFETIME_HOURS", "336"),
        ),
        max_spectators_per_table=_parse_int(
            "MAHJONG_MAX_SPECTATORS_PER_TABLE",
            e.get("MAHJONG_MAX_SPECTATORS_PER_TABLE", "32"),
        ),
        default_ruleset=e.get("MAHJONG_DEFAULT_RULESET", "mcr-2006"),
        shutdown_timeout_s=_parse_int(
            "MAHJONG_SHUTDOWN_TIMEOUT_SECONDS",
            e.get("MAHJONG_SHUTDOWN_TIMEOUT_SECONDS", "30"),
        ),
        wal_checkpoint_interval_s=_parse_int(
            "MAHJONG_WAL_CHECKPOINT_INTERVAL_SECONDS",
            e.get("MAHJONG_WAL_CHECKPOINT_INTERVAL_SECONDS", "300"),
        ),
        log_level=e.get("MAHJONG_LOG_LEVEL", "INFO"),
        log_format=e.get("MAHJONG_LOG_FORMAT", "json"),
        server_version="0.1.0",
        server_id="mahjong-server-0.1.0",
    )

    unknown = sorted(
        k for k in e if k.startswith("MAHJONG_") and k not in _KNOWN_VARS
    )
    return cfg, unknown


__all__ = ["ConfigError", "ServerConfig", "load_config_from_env"]

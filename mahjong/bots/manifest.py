"""Bot manifest parsing + validation.

Spec: docs/specs/bot-runner-protocol.md § Bot manifest.

Validation happens at registration (fixture 10): a malformed manifest never
enters the registry, never spawns a subprocess. The dataclass shape is
immutable so a parsed manifest is safe to share across the table manager and
the bot-runner adapter.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from mahjong.bots.errors import BotManifestError

RuntimeMode = Literal["long_running", "short_running"]
NetworkPolicy = Literal["deny", "allow"]


@dataclass(frozen=True, slots=True)
class ServerCaps:
    """Per-server upper bounds. A manifest requesting more is rejected at
    parse time, not silently capped (fixture 10)."""

    max_memory_mb: int = 2048
    max_cpu_seconds: int = 3600
    max_budget_ms_per_turn: int = 5000


DEFAULT_SERVER_CAPS = ServerCaps()


@dataclass(frozen=True, slots=True)
class BotLimits:
    memory_mb: int
    cpu_seconds: int
    max_fds: int
    max_processes: int
    network: NetworkPolicy


@dataclass(frozen=True, slots=True)
class BotManifest:
    bot_id: str
    version: str
    display_name: str
    directory: Path
    command: tuple[str, ...]
    args: tuple[str, ...]
    env: dict[str, str]
    runtime_mode: RuntimeMode
    spawn_deadline_ms: int
    handshake_deadline_ms: int
    budget_ms_per_turn: int
    teardown_grace_ms: int
    limits: BotLimits
    ruleset_supported: tuple[str, ...]
    format_supported: tuple[str, ...]
    notes: str = ""
    source_path: Path | None = field(default=None)


# --- Validation helpers ---


def _require(d: dict[str, Any], key: str, *, parent: str = "") -> Any:
    if key not in d:
        raise BotManifestError(
            field=f"{parent}{key}" if parent else key,
            detail="required field missing",
        )
    return d[key]


def _expect_type(value: Any, typ: type | tuple[type, ...], *, field_name: str) -> None:
    if not isinstance(value, typ):
        type_name = typ.__name__ if isinstance(typ, type) else "/".join(t.__name__ for t in typ)
        raise BotManifestError(
            field=field_name,
            detail=f"expected {type_name}, got {type(value).__name__}",
        )


def _expect_non_empty_str_list(value: Any, *, field_name: str) -> tuple[str, ...]:
    _expect_type(value, list, field_name=field_name)
    if not value:
        raise BotManifestError(field=field_name, detail="must be non-empty")
    for i, item in enumerate(value):
        if not isinstance(item, str):
            raise BotManifestError(
                field=f"{field_name}[{i}]",
                detail=f"expected str, got {type(item).__name__}",
            )
    return tuple(value)


def _expect_str_list(value: Any, *, field_name: str) -> tuple[str, ...]:
    _expect_type(value, list, field_name=field_name)
    for i, item in enumerate(value):
        if not isinstance(item, str):
            raise BotManifestError(
                field=f"{field_name}[{i}]",
                detail=f"expected str, got {type(item).__name__}",
            )
    return tuple(value)


def _parse_limits(raw: Any, caps: ServerCaps) -> BotLimits:
    if raw is None:
        raw = {}
    _expect_type(raw, dict, field_name="limits")
    memory_mb = raw.get("memory_mb", 512)
    _expect_type(memory_mb, int, field_name="limits.memory_mb")
    if isinstance(memory_mb, bool):  # bool is a subclass of int; reject explicitly.
        raise BotManifestError(field="limits.memory_mb", detail="expected int, got bool")
    if memory_mb <= 0:
        raise BotManifestError(field="limits.memory_mb", detail="must be positive")
    if memory_mb > caps.max_memory_mb:
        raise BotManifestError(
            field="limits.memory_mb",
            detail=f"{memory_mb} exceeds server cap {caps.max_memory_mb}",
        )

    cpu_seconds = raw.get("cpu_seconds", 300)
    _expect_type(cpu_seconds, int, field_name="limits.cpu_seconds")
    if isinstance(cpu_seconds, bool):
        raise BotManifestError(field="limits.cpu_seconds", detail="expected int, got bool")
    if cpu_seconds <= 0:
        raise BotManifestError(field="limits.cpu_seconds", detail="must be positive")
    if cpu_seconds > caps.max_cpu_seconds:
        raise BotManifestError(
            field="limits.cpu_seconds",
            detail=f"{cpu_seconds} exceeds server cap {caps.max_cpu_seconds}",
        )

    max_fds = raw.get("max_fds", 64)
    _expect_type(max_fds, int, field_name="limits.max_fds")
    if isinstance(max_fds, bool) or max_fds <= 0:
        raise BotManifestError(field="limits.max_fds", detail="must be a positive int")

    max_processes = raw.get("max_processes", 1)
    _expect_type(max_processes, int, field_name="limits.max_processes")
    if isinstance(max_processes, bool) or max_processes <= 0:
        raise BotManifestError(field="limits.max_processes", detail="must be a positive int")

    network = raw.get("network", "deny")
    if network not in ("deny", "allow"):
        raise BotManifestError(
            field="limits.network",
            detail=f"expected 'deny' or 'allow', got {network!r}",
        )

    return BotLimits(
        memory_mb=memory_mb,
        cpu_seconds=cpu_seconds,
        max_fds=max_fds,
        max_processes=max_processes,
        network=network,
    )


def _parse_env(raw: Any) -> dict[str, str]:
    if raw is None:
        return {}
    _expect_type(raw, dict, field_name="env")
    out: dict[str, str] = {}
    for k, v in raw.items():
        if not isinstance(k, str) or not isinstance(v, str):
            raise BotManifestError(
                field="env",
                detail=f"keys and values must be str; got {type(k).__name__}={type(v).__name__}",
            )
        out[k] = v
    return out


def _parse_positive_int(raw: dict[str, Any], key: str, default: int, *, cap: int | None) -> int:
    value = raw.get(key, default)
    _expect_type(value, int, field_name=key)
    if isinstance(value, bool):
        raise BotManifestError(field=key, detail="expected int, got bool")
    if value <= 0:
        raise BotManifestError(field=key, detail="must be positive")
    if cap is not None and value > cap:
        raise BotManifestError(field=key, detail=f"{value} exceeds server cap {cap}")
    assert isinstance(value, int)
    return value


# --- Public API ---


def parse_manifest(
    raw: dict[str, Any],
    *,
    server_caps: ServerCaps = DEFAULT_SERVER_CAPS,
    source_path: Path | None = None,
) -> BotManifest:
    """Parse and validate a manifest dict.

    Raises BotManifestError on the first violation, with a dotted field path
    so a user can pinpoint the offending key.
    """
    if not isinstance(raw, dict):
        raise BotManifestError(field="", detail="manifest root must be an object")

    bot_id = _require(raw, "bot_id")
    _expect_type(bot_id, str, field_name="bot_id")
    if not bot_id:
        raise BotManifestError(field="bot_id", detail="must be non-empty")

    version = _require(raw, "version")
    _expect_type(version, str, field_name="version")

    display_name = raw.get("display_name", bot_id)
    _expect_type(display_name, str, field_name="display_name")

    directory_raw = raw.get("directory", "./")
    _expect_type(directory_raw, str, field_name="directory")
    if source_path is not None:
        directory = (source_path.parent / directory_raw).resolve()
    else:
        directory = Path(directory_raw)

    command = _require(raw, "command")
    command_t = _expect_non_empty_str_list(command, field_name="command")

    args = _expect_str_list(raw.get("args", []), field_name="args")
    env = _parse_env(raw.get("env"))

    runtime_mode = raw.get("runtime_mode", "long_running")
    if runtime_mode not in ("long_running", "short_running"):
        raise BotManifestError(
            field="runtime_mode",
            detail=f"expected 'long_running' or 'short_running', got {runtime_mode!r}",
        )

    spawn_deadline_ms = _parse_positive_int(raw, "spawn_deadline_ms", 5000, cap=None)
    handshake_deadline_ms = _parse_positive_int(raw, "handshake_deadline_ms", 1000, cap=None)
    budget_ms_per_turn = _parse_positive_int(
        raw, "budget_ms_per_turn", 1000, cap=server_caps.max_budget_ms_per_turn
    )
    teardown_grace_ms = _parse_positive_int(raw, "teardown_grace_ms", 2000, cap=None)

    limits = _parse_limits(raw.get("limits"), server_caps)

    ruleset_supported = _expect_non_empty_str_list(
        _require(raw, "ruleset_supported"), field_name="ruleset_supported"
    )
    format_supported = _expect_non_empty_str_list(
        _require(raw, "format_supported"), field_name="format_supported"
    )

    notes = raw.get("notes", "")
    _expect_type(notes, str, field_name="notes")

    return BotManifest(
        bot_id=bot_id,
        version=version,
        display_name=display_name,
        directory=directory,
        command=command_t,
        args=args,
        env=env,
        runtime_mode=runtime_mode,
        spawn_deadline_ms=spawn_deadline_ms,
        handshake_deadline_ms=handshake_deadline_ms,
        budget_ms_per_turn=budget_ms_per_turn,
        teardown_grace_ms=teardown_grace_ms,
        limits=limits,
        ruleset_supported=ruleset_supported,
        format_supported=format_supported,
        notes=notes,
        source_path=source_path,
    )


def load_manifest_file(
    path: Path,
    *,
    server_caps: ServerCaps = DEFAULT_SERVER_CAPS,
) -> BotManifest:
    """Read manifest.json from disk and parse it.

    `directory` in the manifest is resolved relative to the file's parent so
    a bot can declare a relative path that works regardless of where the
    server is started from.
    """
    p = Path(path)
    if not p.exists():
        raise BotManifestError(field="", detail=f"manifest file not found: {p}")
    try:
        raw_text = p.read_text(encoding="utf-8")
    except OSError as e:
        raise BotManifestError(field="", detail=f"cannot read {p}: {e}") from e
    try:
        raw = json.loads(raw_text)
    except json.JSONDecodeError as e:
        raise BotManifestError(field="", detail=f"invalid JSON in {p}: {e}") from e
    return parse_manifest(raw, server_caps=server_caps, source_path=p.resolve())


__all__ = [
    "DEFAULT_SERVER_CAPS",
    "BotLimits",
    "BotManifest",
    "NetworkPolicy",
    "RuntimeMode",
    "ServerCaps",
    "load_manifest_file",
    "parse_manifest",
]

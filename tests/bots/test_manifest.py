"""Bot manifest parsing and validation.

Spec: docs/specs/bot-runner-protocol.md § Bot manifest.
Fixtures: bot-runner-protocol.md fixture 10 (manifest validation).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from mahjong.bots.errors import BotManifestError
from mahjong.bots.manifest import (
    DEFAULT_SERVER_CAPS,
    BotManifest,
    ServerCaps,
    load_manifest_file,
    parse_manifest,
)

# --- Canonical manifest from the spec (bot-runner-protocol.md § Bot manifest) ---


def _canonical_manifest_dict() -> dict[str, Any]:
    return {
        "bot_id": "b_rule_v1",
        "version": "0.1.0",
        "display_name": "Rule-based v1",
        "directory": "./",
        "command": ["python", "-u", "bot.py"],
        "args": [],
        "env": {"PYTHONUNBUFFERED": "1"},
        "runtime_mode": "long_running",
        "spawn_deadline_ms": 5000,
        "handshake_deadline_ms": 1000,
        "budget_ms_per_turn": 1000,
        "teardown_grace_ms": 2000,
        "limits": {
            "memory_mb": 512,
            "cpu_seconds": 300,
            "max_fds": 64,
            "max_processes": 1,
            "network": "deny",
        },
        "ruleset_supported": ["mcr-2006"],
        "format_supported": ["botzone-csm"],
        "notes": "Reference rule-based bot. Strong baseline.",
    }


# --- Parsing the canonical example ---


def test_canonical_manifest_parses() -> None:
    m = parse_manifest(_canonical_manifest_dict())
    assert isinstance(m, BotManifest)
    assert m.bot_id == "b_rule_v1"
    assert m.version == "0.1.0"
    assert m.command == ("python", "-u", "bot.py")
    assert m.env == {"PYTHONUNBUFFERED": "1"}
    assert m.runtime_mode == "long_running"
    assert m.limits.memory_mb == 512
    assert m.limits.network == "deny"
    assert m.ruleset_supported == ("mcr-2006",)


# --- Defaults applied when optional fields omitted ---


def test_defaults_applied_for_missing_optional_fields() -> None:
    minimal: dict[str, Any] = {
        "bot_id": "b_min",
        "version": "0.1.0",
        "display_name": "Minimal",
        "directory": "./",
        "command": ["python", "bot.py"],
        "ruleset_supported": ["mcr-2006"],
        "format_supported": ["botzone-csm"],
    }
    m = parse_manifest(minimal)
    assert m.args == ()
    assert m.env == {}
    assert m.runtime_mode == "long_running"
    assert m.spawn_deadline_ms == 5000
    assert m.handshake_deadline_ms == 1000
    assert m.budget_ms_per_turn == 1000
    assert m.teardown_grace_ms == 2000
    assert m.limits.memory_mb == 512
    assert m.limits.cpu_seconds == 300
    assert m.limits.max_fds == 64
    assert m.limits.max_processes == 1
    assert m.limits.network == "deny"
    assert m.notes == ""


# --- Fixture 10: required-field validation ---


def test_missing_bot_id_rejected() -> None:
    bad = _canonical_manifest_dict()
    del bad["bot_id"]
    with pytest.raises(BotManifestError) as exc:
        parse_manifest(bad)
    assert exc.value.field == "bot_id"


def test_missing_command_rejected() -> None:
    bad = _canonical_manifest_dict()
    del bad["command"]
    with pytest.raises(BotManifestError) as exc:
        parse_manifest(bad)
    assert exc.value.field == "command"


def test_empty_command_rejected() -> None:
    bad = _canonical_manifest_dict()
    bad["command"] = []
    with pytest.raises(BotManifestError) as exc:
        parse_manifest(bad)
    assert exc.value.field == "command"


def test_missing_version_rejected() -> None:
    bad = _canonical_manifest_dict()
    del bad["version"]
    with pytest.raises(BotManifestError) as exc:
        parse_manifest(bad)
    assert exc.value.field == "version"


# --- Fixture 10: memory_mb over server max ---


def test_memory_mb_over_server_cap_rejected() -> None:
    bad = _canonical_manifest_dict()
    bad["limits"]["memory_mb"] = DEFAULT_SERVER_CAPS.max_memory_mb + 1
    with pytest.raises(BotManifestError) as exc:
        parse_manifest(bad)
    assert exc.value.field == "limits.memory_mb"


def test_memory_mb_at_server_cap_accepted() -> None:
    ok = _canonical_manifest_dict()
    ok["limits"]["memory_mb"] = DEFAULT_SERVER_CAPS.max_memory_mb
    m = parse_manifest(ok)
    assert m.limits.memory_mb == DEFAULT_SERVER_CAPS.max_memory_mb


def test_custom_server_caps_override_default() -> None:
    bad = _canonical_manifest_dict()
    bad["limits"]["memory_mb"] = 1024
    tight = ServerCaps(max_memory_mb=512, max_cpu_seconds=300, max_budget_ms_per_turn=5000)
    with pytest.raises(BotManifestError):
        parse_manifest(bad, server_caps=tight)


def test_budget_ms_over_server_cap_rejected() -> None:
    bad = _canonical_manifest_dict()
    bad["budget_ms_per_turn"] = DEFAULT_SERVER_CAPS.max_budget_ms_per_turn + 1
    with pytest.raises(BotManifestError) as exc:
        parse_manifest(bad)
    assert exc.value.field == "budget_ms_per_turn"


# --- Type / value errors ---


def test_command_must_be_list() -> None:
    bad = _canonical_manifest_dict()
    bad["command"] = "python bot.py"
    with pytest.raises(BotManifestError) as exc:
        parse_manifest(bad)
    assert exc.value.field == "command"


def test_memory_mb_must_be_int() -> None:
    bad = _canonical_manifest_dict()
    bad["limits"]["memory_mb"] = "512"
    with pytest.raises(BotManifestError) as exc:
        parse_manifest(bad)
    assert exc.value.field == "limits.memory_mb"


def test_network_must_be_known_value() -> None:
    bad = _canonical_manifest_dict()
    bad["limits"]["network"] = "maybe"
    with pytest.raises(BotManifestError) as exc:
        parse_manifest(bad)
    assert exc.value.field == "limits.network"


def test_runtime_mode_must_be_known_value() -> None:
    bad = _canonical_manifest_dict()
    bad["runtime_mode"] = "blazing"
    with pytest.raises(BotManifestError) as exc:
        parse_manifest(bad)
    assert exc.value.field == "runtime_mode"


def test_ruleset_supported_must_be_non_empty_list() -> None:
    bad = _canonical_manifest_dict()
    bad["ruleset_supported"] = []
    with pytest.raises(BotManifestError) as exc:
        parse_manifest(bad)
    assert exc.value.field == "ruleset_supported"


def test_env_must_be_string_string_mapping() -> None:
    bad = _canonical_manifest_dict()
    bad["env"] = {"GOOD": 1}
    with pytest.raises(BotManifestError) as exc:
        parse_manifest(bad)
    assert exc.value.field == "env"


# --- File loader ---


def test_load_manifest_file_round_trip(tmp_path: Path) -> None:
    p = tmp_path / "manifest.json"
    p.write_text(json.dumps(_canonical_manifest_dict()), encoding="utf-8")
    m = load_manifest_file(p)
    assert m.bot_id == "b_rule_v1"
    # `directory` is resolved relative to the manifest file's parent.
    assert m.directory == tmp_path.resolve()


def test_load_manifest_file_missing_path(tmp_path: Path) -> None:
    with pytest.raises(BotManifestError):
        load_manifest_file(tmp_path / "missing.json")


def test_load_manifest_file_bad_json(tmp_path: Path) -> None:
    p = tmp_path / "manifest.json"
    p.write_text("{not json", encoding="utf-8")
    with pytest.raises(BotManifestError):
        load_manifest_file(p)

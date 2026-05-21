"""Bot registry: in-memory register/lookup/list.

Spec: docs/specs/bot-runner-protocol.md (validation runs at registration);
implementation-order.md Step 5.1.
"""

from __future__ import annotations

from typing import Any

import pytest

from mahjong.bots.errors import BotManifestError
from mahjong.bots.manifest import parse_manifest
from mahjong.bots.registry import BotAlreadyRegistered, BotNotFound, BotRegistry


def _ok_manifest(bot_id: str = "b_test") -> Any:
    return parse_manifest(
        {
            "bot_id": bot_id,
            "version": "0.1.0",
            "display_name": "Test",
            "directory": "./",
            "command": ["python", "bot.py"],
            "ruleset_supported": ["mcr-2006"],
            "format_supported": ["botzone-csm"],
        }
    )


def test_register_and_lookup_round_trip() -> None:
    r = BotRegistry()
    m = _ok_manifest("b_a")
    r.register(m)
    assert r.lookup("b_a") is m


def test_lookup_unknown_raises() -> None:
    r = BotRegistry()
    with pytest.raises(BotNotFound):
        r.lookup("nope")


def test_duplicate_bot_id_rejected() -> None:
    r = BotRegistry()
    r.register(_ok_manifest("dup"))
    with pytest.raises(BotAlreadyRegistered):
        r.register(_ok_manifest("dup"))


def test_replace_true_allows_overwrite() -> None:
    r = BotRegistry()
    r.register(_ok_manifest("dup"))
    new = _ok_manifest("dup")
    r.register(new, replace=True)
    assert r.lookup("dup") is new


def test_unregister_removes_bot() -> None:
    r = BotRegistry()
    r.register(_ok_manifest("x"))
    r.unregister("x")
    with pytest.raises(BotNotFound):
        r.lookup("x")


def test_unregister_unknown_raises() -> None:
    r = BotRegistry()
    with pytest.raises(BotNotFound):
        r.unregister("ghost")


def test_list_returns_registered_ids() -> None:
    r = BotRegistry()
    r.register(_ok_manifest("a"))
    r.register(_ok_manifest("b"))
    assert set(r.list_ids()) == {"a", "b"}


def test_register_from_dict_validates() -> None:
    """register_dict triggers manifest validation before storing."""
    r = BotRegistry()
    with pytest.raises(BotManifestError):
        r.register_dict({"bot_id": "bad"})  # missing required fields
    assert list(r.list_ids()) == []

"""SeatAdapter protocol conformance + trivial-adapter behavior.

Spec: docs/specs/seat-port.md § The interface, § Adapter catalog.
"""

from __future__ import annotations

import asyncio
from typing import Any, cast

import pytest

from mahjong.adapters.autopass import AutoPassAdapter
from mahjong.adapters.base import Prompt, SeatAdapter, SeatContext
from mahjong.adapters.canned import CannedAdapter

pytestmark = pytest.mark.asyncio


def _make_prompt(
    *, default_action: dict[str, Any], legal: list[dict[str, Any]] | None = None
) -> Prompt:
    return cast(
        Prompt,
        {
            "kind": "DISCARD",
            "view": {},
            "legal_actions": legal if legal is not None else [default_action],
            "default_action": default_action,
            "deadline": asyncio.get_event_loop().time() + 5.0,
            "issued_at": asyncio.get_event_loop().time(),
            "context": {},
        },
    )


def _make_ctx(seat: int = 0) -> SeatContext:
    return cast(
        SeatContext,
        {
            "seat": seat,
            "hand_id": "01970e8a-9d3e-7c4a-9b1f-0a1b2c3d4e5f",
            "ruleset": {"id": "mcr-2006", "version": 1, "config_hash": "sha256:x"},
            "seat_deadline_ms": 1000,
            "initial_view": {},
        },
    )


# --- Protocol conformance ---


@pytest.mark.asyncio(loop_scope="function")
async def test_canned_adapter_satisfies_protocol() -> None:
    adapter: SeatAdapter = CannedAdapter(
        identity={"kind": "canned", "script": "test"},
        actions=[],
    )
    assert adapter.identity["kind"] == "canned"


@pytest.mark.asyncio(loop_scope="function")
async def test_autopass_adapter_satisfies_protocol() -> None:
    adapter: SeatAdapter = AutoPassAdapter()
    assert adapter.identity["kind"] == "canned"  # marked canned, script="autopass"


# --- CannedAdapter ---


async def test_canned_returns_scripted_actions_in_order() -> None:
    pass_action = {"type": "PASS"}
    play_action = {"type": "PLAY", "tile": "W3"}
    adapter = CannedAdapter(
        identity={"kind": "canned", "script": "two_step"},
        actions=[pass_action, play_action],
    )
    await adapter.seated(_make_ctx())
    first = await adapter.decide(_make_prompt(default_action=pass_action, legal=[pass_action]))
    second = await adapter.decide(_make_prompt(default_action=play_action, legal=[play_action]))
    assert first == pass_action
    assert second == play_action


async def test_canned_falls_back_to_default_when_script_exhausted() -> None:
    """Empty script → every decide returns the prompt's default_action."""
    adapter = CannedAdapter(identity={"kind": "canned", "script": "empty"}, actions=[])
    default = {"type": "PASS"}
    result = await adapter.decide(_make_prompt(default_action=default))
    assert result == default


async def test_canned_falls_back_to_default_when_scripted_action_illegal() -> None:
    """If the next scripted action isn't in legal_actions, the table's
    default_action fires (seat-port.md § CannedAdapter)."""
    bad = {"type": "PLAY", "tile": "Z9"}
    default = {"type": "PASS"}
    adapter = CannedAdapter(
        identity={"kind": "canned", "script": "bad"},
        actions=[bad],
    )
    result = await adapter.decide(_make_prompt(default_action=default, legal=[default]))
    assert result == default


async def test_canned_observe_and_left_are_no_ops() -> None:
    adapter = CannedAdapter(identity={"kind": "canned", "script": "noop"}, actions=[])
    await adapter.observe({"event": "HEADER"}, {})  # type: ignore[arg-type]
    await adapter.left("HAND_ENDED")
    # No exception, no state.


# --- AutoPassAdapter ---


async def test_autopass_returns_default_action() -> None:
    adapter = AutoPassAdapter()
    default = {"type": "PASS"}
    result = await adapter.decide(_make_prompt(default_action=default))
    assert result == default


async def test_autopass_never_blocks() -> None:
    """decide must return promptly (well under the deadline)."""
    adapter = AutoPassAdapter()
    default = {"type": "PASS"}
    result = await asyncio.wait_for(
        adapter.decide(_make_prompt(default_action=default)), timeout=0.5
    )
    assert result == default


async def test_autopass_lifecycle_methods_are_no_ops() -> None:
    adapter = AutoPassAdapter()
    await adapter.seated(_make_ctx())
    await adapter.observe({"event": "DISCARD"}, {})  # type: ignore[arg-type]
    await adapter.left("REPLACED")
    # No exception.

"""Exercise privacy at the public adapter boundary, not just the codec."""

from copy import deepcopy
from pathlib import Path
from typing import Any

from mahjong.adapters.canned import CannedAdapter
from mahjong.engine.rulesets import MANIFEST
from mahjong.engine.state import project_event
from mahjong.table.manager import run_hand


async def test_observe_receives_only_seat_visible_events(tmp_path: Path) -> None:
    class Spy(CannedAdapter):
        def __init__(self, seat: int) -> None:
            super().__init__(identity={"kind": "canned", "script": "privacy"}, actions=[])
            self.seat = seat
            self.events: list[dict[str, Any]] = []

        async def observe(self, event: dict[str, Any], view: Any) -> None:
            self.events.append(deepcopy(event))

    adapters = [Spy(seat) for seat in range(4)]
    await run_hand(
        adapters=adapters,
        ruleset={"id": "mcr-2006", "version": 1, "config_hash": MANIFEST["mcr-2006"]},
        seed=12345,
        hand_id="privacy",
        record_path=tmp_path / "hand.jsonl",
        server_info={"version": "test"},
    )
    for adapter in adapters:
        draws = [e for e in adapter.events if e["event"] == "DRAW"]
        assert draws
        for event in draws:
            assert ("tile" in event) == (event["seat"] == adapter.seat)
        for event in adapter.events:
            if event["event"] == "CLAIM_WINDOW":
                assert all(o["seat"] == adapter.seat for o in event["opportunities"])


def test_claim_opportunities_are_private_and_independent() -> None:
    event = {
        "event": "CLAIM_WINDOW",
        "opportunities": [
            {"seat": 0, "type": "CHI", "tiles": ["W1", "W2", "W3"]},
            {"seat": 1, "type": "HU"},
        ],
    }
    original = deepcopy(event)
    assert project_event(event, None)["opportunities"] == []
    own = project_event(event, 0)
    assert len(own["opportunities"]) == 1
    own["opportunities"][0]["tiles"].clear()
    assert event == original

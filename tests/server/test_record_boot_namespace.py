"""DEF-13: record paths + hand ids stay unique across server restarts.

Table ids restart at ``1`` every server boot, so before this fix the second
boot's first table reused ``records/t1/hand_0000.jsonl`` — overwriting the prior
boot's record file *and* tripping the ``hand_index`` PK(``hand_id``) /
UNIQUE(``record_path``), so the new hand raised ``persistence.reserve_hand_failed``
and vanished from history/replay. A per-process boot id namespaces every derived
identifier.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from mahjong.engine.rulesets import MANIFEST
from mahjong.persistence import Participant, Persistence
from mahjong.server.registry import TableHandle, TableRegistry

MCR_REF: dict[str, Any] = {"id": "mcr-2006", "version": 1, "config_hash": MANIFEST["mcr-2006"]}
SERVER = {"version": "test", "git_sha": "test", "host": "test"}
PARTICIPANTS = [
    Participant(seat=i, account_id=None, seat_kind="canned", wind=f"F{i + 1}", final_score_delta=None)
    for i in range(4)
]


def _boot_first_table(p: Persistence, data_dir: Path) -> TableHandle:
    """A fresh server process: new registry (new boot id), table ids reset to 1."""
    reg = TableRegistry(persistence=p)
    tid = reg.create_table_direct(
        ruleset=MCR_REF, seed=0, server_info=SERVER, data_dir=data_dir, max_hands=1
    )
    # The collision precondition: every boot's first table id is "1".
    assert tid == "1"
    return reg.get_table(tid)


def _reserve_like_live(p: Persistence, data_dir: Path, handle: TableHandle) -> None:
    """Write the record file and reserve its hand_index row, as the live hand
    loop's ``_reserve_hand_row`` does — this is where the collision surfaced."""
    handle.record_path.write_text('{"event":"HEADER"}\n')
    p.reserve_hand(
        hand_id=handle.hand_id,
        match_id=handle._match_id,
        hand_index_in_match=0,
        ruleset_id="mcr-2006",
        ruleset_config_hash="abc123",
        started_at_ms=1,
        master_seed="0",
        record_path=str(handle.record_path.relative_to(data_dir)),
        server_version="test",
        source="live",
        participants=PARTICIPANTS,
    )


def test_record_namespace_is_unique_across_restarts(tmp_path: Path) -> None:
    data_dir = tmp_path
    (data_dir / "records").mkdir()
    # One DB shared across both boots — exactly what a real restart sees.
    p = Persistence(":memory:", data_dir)

    # --- BOOT 1: first table reserves its hand. ---
    h1 = _boot_first_table(p, data_dir)
    _reserve_like_live(p, data_dir, h1)
    boot1_bytes = h1.record_path.read_bytes()

    # --- BOOT 2 (restart): table id resets to "1" again. ---
    h2 = _boot_first_table(p, data_dir)

    # Without a per-boot namespace these collide → overwrite + PK/UNIQUE failure.
    assert h2.record_path != h1.record_path
    assert h2.hand_id != h1.hand_id

    # The second reservation must succeed (was persistence.reserve_hand_failed).
    _reserve_like_live(p, data_dir, h2)

    # Boot 1's record file is intact — not overwritten by boot 2's first table.
    assert h1.record_path.exists()
    assert h1.record_path.read_bytes() == boot1_bytes

    # Both hands are discoverable in history.
    assert p.get_hand(h1.hand_id) is not None
    assert p.get_hand(h2.hand_id) is not None

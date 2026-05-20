"""Canonical hash: SHA-256 over sorted-key JSON.

Spec: docs/specs/determinism.md § The canonical hash.

One function (`canonical_hash`), three uses: state_hash, config_hash, record
checksum. Floats are forbidden as hash inputs (state schema bans them; this
contract is what enforces the consequence).
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


def canonical_hash(obj: Any) -> str:
    """Return `"sha256:<hex>"` over the canonical JSON form of `obj`.

    Canonicalization:
        - `sort_keys=True`: dict key order is deterministic.
        - `separators=(",", ":")`: no whitespace.
        - `ensure_ascii=False`: UTF-8 native (tokens are ASCII anyway).

    Floats in the input are a contract violation (see determinism.md);
    we reject them explicitly so a silent JSON-float round-trip can't
    drift the hash across platforms.
    """
    _reject_floats(obj)
    payload = json.dumps(
        obj,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _reject_floats(obj: Any) -> None:
    if isinstance(obj, float):
        raise TypeError("canonical_hash forbids float inputs (see determinism.md)")
    # bool is a subclass of int; allow it. Strings are iterable but already
    # handled by the isinstance check above falling through.
    if isinstance(obj, dict):
        for k, v in obj.items():
            _reject_floats(k)
            _reject_floats(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            _reject_floats(v)

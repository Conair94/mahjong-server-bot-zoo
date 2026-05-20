"""Bundled ruleset configs, versioned by config_hash.

Spec: docs/specs/determinism.md § Ruleset config_hash,
      docs/specs/engine-api.md § Internal submodule layout.

Layout:
    mcr-2006.json     - the canonical 81-fan MCR config
    MANIFEST.json     - id -> config_hash map (frozen per release)

A record's `ruleset.config_hash` is what makes replay self-describing.
The loader's contract: resolve `id -> config dict`, then verify
`canonical_hash(config)` matches both the manifest and any caller-asserted
hash. Silent acceptance of a mismatch would let an engine refactor that
changed the config quietly invalidate every prior record.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from mahjong.engine.errors import RulesetError
from mahjong.engine.hashing import canonical_hash

_RULESETS_DIR = Path(__file__).parent

with (_RULESETS_DIR / "MANIFEST.json").open("r", encoding="utf-8") as _f:
    MANIFEST: dict[str, str] = json.load(_f)


def load_ruleset(ref: dict[str, Any]) -> dict[str, Any]:
    """Resolve a `RuleSetRef` to its canonical config dict.

    Raises `RulesetError` if:
        - the `id` is not in MANIFEST,
        - the on-disk config's `canonical_hash` doesn't match MANIFEST
          (indicates a tampered file or a release-staging mistake),
        - the caller asserted a `config_hash` and it doesn't match.
    """
    rid = ref.get("id")
    if rid not in MANIFEST:
        raise RulesetError(ruleset_ref=ref, detail=f"unknown ruleset id: {rid!r}")

    config_path = _RULESETS_DIR / f"{rid}.json"
    if not config_path.exists():
        raise RulesetError(
            ruleset_ref=ref,
            detail=f"ruleset id {rid!r} is in MANIFEST but config file is missing",
        )
    with config_path.open("r", encoding="utf-8") as f:
        config: dict[str, Any] = json.load(f)

    computed = canonical_hash(config)
    expected = MANIFEST[rid]
    if computed != expected:
        raise RulesetError(
            ruleset_ref=ref,
            detail=(
                f"on-disk config_hash {computed} does not match MANIFEST {expected} - "
                f"config file may be tampered with or out of sync with MANIFEST.json"
            ),
        )

    caller_hash = ref.get("config_hash")
    if caller_hash is not None and caller_hash != computed:
        raise RulesetError(
            ruleset_ref=ref,
            detail=(
                f"caller-asserted config_hash {caller_hash} does not match "
                f"loaded config_hash {computed}"
            ),
        )

    return config

"""Step 1.3 — ruleset loader.

Spec: docs/specs/determinism.md § Ruleset config_hash,
      docs/specs/engine-api.md § Internal submodule layout.

The loader's job is small but contractual: resolve a `RuleSetRef` to a
config dict, compute its canonical hash, and refuse to silently accept a
mismatch between caller-supplied hash and computed hash. That refusal is
how records stay self-describing — a record with `config_hash` X cannot
be replayed against config Y without explicit acknowledgement.
"""

from __future__ import annotations

import pytest

from mahjong.engine.errors import RulesetError
from mahjong.engine.hashing import canonical_hash
from mahjong.engine.rulesets import MANIFEST, load_ruleset


def test_load_mcr_2006_succeeds() -> None:
    config = load_ruleset({"id": "mcr-2006"})
    assert isinstance(config, dict)
    assert config["id"] == "mcr-2006"
    # The config has a 'version' the loader stamps in.
    assert isinstance(config.get("version"), int)


def test_loaded_config_hash_matches_manifest() -> None:
    """determinism.md § Ruleset config_hash: hash of resolved dict is the contract."""
    config = load_ruleset({"id": "mcr-2006"})
    assert canonical_hash(config) == MANIFEST["mcr-2006"]


def test_unknown_ruleset_id_raises() -> None:
    with pytest.raises(RulesetError) as exc:
        load_ruleset({"id": "mcr-2099"})
    assert exc.value.ruleset_ref == {"id": "mcr-2099"}
    assert "unknown" in exc.value.detail.lower()


def test_caller_supplied_config_hash_mismatch_raises() -> None:
    """If the caller asserts a config_hash, the loader verifies it matches."""
    with pytest.raises(RulesetError) as exc:
        load_ruleset({"id": "mcr-2006", "config_hash": "sha256:not-real"})
    assert "config_hash" in exc.value.detail.lower()


def test_caller_supplied_correct_config_hash_succeeds() -> None:
    """When caller asserts the right hash, the loader returns the config."""
    expected = MANIFEST["mcr-2006"]
    config = load_ruleset({"id": "mcr-2006", "config_hash": expected})
    assert canonical_hash(config) == expected


def test_manifest_only_lists_known_rulesets() -> None:
    """Manifest is the authoritative id -> hash map; every entry must load."""
    for rid in MANIFEST:
        config = load_ruleset({"id": rid})
        assert canonical_hash(config) == MANIFEST[rid]


def test_load_ruleset_is_idempotent_in_hash() -> None:
    """Two loads of the same ref produce identical (and byte-stable) configs."""
    a = load_ruleset({"id": "mcr-2006"})
    b = load_ruleset({"id": "mcr-2006"})
    assert canonical_hash(a) == canonical_hash(b)

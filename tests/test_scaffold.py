"""Sanity tests for the project scaffold.

These don't exercise any engine behavior — they just confirm the package
imports and the layout matches what docs/specs/engine-api.md prescribes. If
these fail, the build setup itself is broken; failure on the actual
implementation tests is meaningless until these pass.
"""

from __future__ import annotations

import importlib

import pytest

EXPECTED_MODULES = [
    "mahjong",
    "mahjong.engine",
    "mahjong.engine.types",
    "mahjong.engine.tiles",
    "mahjong.engine.state",
    "mahjong.engine.pymj",
    "mahjong.engine.rng",
    "mahjong.engine.hashing",
    "mahjong.engine.errors",
    "mahjong.engine.legality",
    "mahjong.engine.legality.discard",
    "mahjong.engine.legality.claim",
    "mahjong.engine.transition",
    "mahjong.engine.transition.play",
    "mahjong.engine.transition.claim",
    "mahjong.engine.transition.gang",
    "mahjong.engine.transition.hu",
    "mahjong.engine.transition.pass_",
    "mahjong.engine.rulesets",
    "mahjong.records",
    "mahjong.adapters",
    "mahjong.bots",
    "mahjong.bots.sdk",
    "mahjong.table",
    "mahjong.selfplay",
    "mahjong.cli",
]


@pytest.mark.parametrize("modname", EXPECTED_MODULES)
def test_module_importable(modname: str) -> None:
    importlib.import_module(modname)


def test_cli_entry_point_exists() -> None:
    from mahjong.cli import main

    assert callable(main)

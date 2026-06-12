"""AST lint: enforce engine purity contracts.

Spec: docs/specs/determinism.md fixture 9 (no random imports),
      docs/specs/engine-api.md (engine is pure — no clocks, no logging).

The engine is a value-in/value-out function library. If any of these
imports appears under mahjong.engine.*, the determinism contract is at
risk: `random` / `numpy.random` would be a second RNG; `time` / `datetime`
would let wall-clock leak into state; `logging` would let the engine
produce side effects that vary with config.

The PyMahjongGB boundary is allowed only in `mahjong/engine/pymj.py`.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

ENGINE_ROOT = Path(__file__).resolve().parents[2] / "mahjong" / "engine"

FORBIDDEN_TOP_LEVEL_MODULES = {
    "random",
    "time",
    "datetime",
    "logging",
}

# `numpy.random` is checked separately so we can match `import numpy.random`
# and `from numpy import random` without banning all of numpy.
FORBIDDEN_DOTTED = {
    ("numpy", "random"),
}


def _engine_source_files() -> list[Path]:
    return [p for p in ENGINE_ROOT.rglob("*.py") if p.name != "__pycache__"]


def _imports(tree: ast.AST) -> list[tuple[str, str | None]]:
    """Return (module, alias_or_attr) pairs for every import in the tree."""
    found: list[tuple[str, str | None]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.append((alias.name, None))
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            for alias in node.names:
                found.append((mod, alias.name))
    return found


@pytest.mark.parametrize("path", _engine_source_files(), ids=lambda p: p.name)
def test_engine_module_has_no_forbidden_imports(path: Path) -> None:
    """Every engine module is purity-clean."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for module, attr in _imports(tree):
        # Bare forbidden module: `import random`, `from random import ...`
        top = module.split(".")[0]
        assert (
            top not in FORBIDDEN_TOP_LEVEL_MODULES
        ), f"{path}: forbidden import of {module!r} (see determinism.md fixture 9 — engine purity)"
        # Dotted forbidden: `import numpy.random` / `from numpy import random`
        for parent, child in FORBIDDEN_DOTTED:
            if module == f"{parent}.{child}":
                pytest.fail(f"{path}: forbidden import of {module!r}")
            if module == parent and attr == child:
                pytest.fail(f"{path}: forbidden `from {parent} import {child}`")


def test_lint_catches_synthetic_violation(tmp_path: Path) -> None:
    """The lint must fail on a known-bad file. Self-check on the rule."""
    offender = tmp_path / "bad.py"
    offender.write_text("import random\n")
    tree = ast.parse(offender.read_text(encoding="utf-8"))
    imports = _imports(tree)
    tops = {m.split(".")[0] for m, _ in imports}
    assert "random" in tops & FORBIDDEN_TOP_LEVEL_MODULES


def test_pymjgb_only_imported_from_pymj_module() -> None:
    """engine-api.md: only `mahjong/engine/pymj.py` may touch PyMahjongGB."""
    for path in _engine_source_files():
        if path.name == "pymj.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for module, _attr in _imports(tree):
            top = module.split(".")[0]
            assert top != "MahjongGB", (
                f"{path}: PyMahjongGB may only be imported from "
                f"mahjong/engine/pymj.py (engine-api.md § PyMahjongGB integration boundary)"
            )

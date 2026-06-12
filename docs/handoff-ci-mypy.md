# Handoff — make CI fully green (fix the mypy step)

**Status (2026-06-12):** `main`'s CI is **red at the `mypy mahjong/` step**. The
ruff steps (`ruff format --check .`, `ruff check .`) are now **green** (fixed in
PR #40). The remaining failure is pre-existing type debt that was masked for a
long time because CI always died at the earlier ruff step and never reached
mypy.

This doc is the resume point for a fresh session. It is the *only* outstanding
item from the 2026-06-12 session; everything else (console tunnel fix #39, ruff
#40, stats #42) is merged or in an open mergeable PR.

## Why this is its own task

It was deliberately **not** bundled into the ruff/format PR (#40):

- It needs a **mypy version decision** (see below), not just code edits.
- Several errors are in the **core rules engine** (`engine/transition/`), which
  the project's `CLAUDE.md` treats as TDD-strict / handle-with-care.
- Mixing core-engine type fixes into a whole-repo formatting PR is bad practice.

## Two-part root cause

1. **The mypy pin is stale.** `.pre-commit-config.yaml` pins
   `mirrors-mypy rev: v1.10.1`, and `pyproject.toml` declares a floating
   `mypy>=1.10`. But the code uses **PEP 695 generics** (e.g.
   `mahjong/selfplay/seeds.py:17`), which **mypy 1.10.1 cannot parse**
   (`PEP 695 generics are not yet supported` / `Name "T" is not defined`).
   PEP 695 support landed around mypy 1.11–1.12. So 1.10.1 is too old for this
   codebase, and meanwhile CI floated to mypy **2.1.0**, which *does* parse
   PEP 695 but then reports the real type errors below.

2. **~9 real type errors** that exist regardless of mypy version.

## The fix, in order

### Step 1 — pin mypy to a version that supports PEP 695

Pick a concrete version >= 1.12 (verify it parses `seeds.py`), then pin it in
**both** places so CI, pre-commit, and local agree (this is exactly the drift
that caused the ruff mess — see PR #40's commit message):

- `pyproject.toml` dev deps: change `mypy>=1.10` → `mypy==<chosen>`.
- `.pre-commit-config.yaml`: bump `mirrors-mypy rev: v1.10.1` → `v<chosen>`.

(Ruff was pinned to `==0.5.5` in PR #40 to match pre-commit; do the same shape
for mypy. The pyproject comment next to the ruff pin explains the convention.)

### Step 2 — fix the real type errors

Baseline captured on the reformatted pre-stats `main` (PR #40 branch). **Re-run
mypy on current `main` after PR #42 merges** — the stats work touches
`analysis.py` and the `stats_provider` bindings, so line numbers / a couple of
these errors will shift (the stats PR changed `stats_for_prompt` to return
`dict | None`, which partly addresses the `stats_provider` return-type half).

```
mahjong/selfplay/seeds.py:17        PEP 695 — fixed by Step 1 (version bump), not a code change
mahjong/engine/transition/__init__.py:113  Incompatible return: Literal['PASS','PLAY','PENG','CHI','HU'] vs Literal['HU','PENG','GANG','CHI']
mahjong/engine/transition/hu.py:76  Arg 3 to score_delta: str vs Literal['SELF_DRAW','DISCARD','ROBBED_KONG','LAST_TILE']
mahjong/engine/transition/hu.py:78  Unused "type: ignore" (drop it)
mahjong/analysis.py:127             Value of type "object" is not indexable
mahjong/analysis.py:172             Arg 1 to tile_sort_key: "object" vs "str"
mahjong/bots/v1.py:123              Arg 1 to _best_viable_gang: list[GangAction] vs list[PassAction|PlayAction|PengAction|ChiAction|GangAction|HuAction]
mahjong/bots/v1.py:196              Value of type "object" is not indexable
mahjong/web/server.py:324           stats_provider Callable[[dict[str,Any],int],dict] vs Callable[[Prompt,int],dict|None]|None
mahjong/server/registry.py:803      stats_provider Callable[[dict[str,Any],int],dict] vs Callable[[Prompt,int],dict|None]|None
```

Notes per cluster:

- **`engine/transition/` (113, hu.py 76/78)** — core engine, handle carefully.
  These look like annotation/`Literal` mismatches, not logic bugs, but pin the
  behavior: the full suite passes today, so any fix that changes a runtime value
  is wrong. The `score_delta` win-type arg should be a `Literal`, not bare `str`
  — likely the caller passes a `str` that needs narrowing/`cast`, or the param
  type is too narrow.
- **`analysis.py` (127, 172) "object not indexable"** — mypy lost the type of a
  value (probably a `dict.get(...)`/`view[...]` typed as `object`). Add the
  right annotation or `cast`. This file is touched by PR #42; re-check after.
- **`bots/v1.py` (123, 196)** — list-variance and an `object` index; annotation
  fixes.
- **`stats_provider` (server.py 324, registry.py 803)** — `stats_for_prompt`'s
  signature is `(prompt: dict[str, Any], seat: int)` but `StatsProvider` expects
  `(prompt: Prompt, seat)`. Align the param type to `Prompt` (the seat-port
  `Prompt` TypedDict) on `stats_for_prompt` / `prompt_stats`, or widen the
  `StatsProvider` alias. After PR #42 the return half (`dict | None`) is already
  fixed; only the param-type half remains.

### Step 3 — verify

Reproduce CI's exact mypy step locally in a throwaway venv (the repo's runtime
deps must be importable for `mypy mahjong/` to resolve imports):

```bash
python3 -m venv /tmp/mypyenv
/tmp/mypyenv/bin/pip install -e .            # runtime deps (websockets, argon2, PyMahjongGB, psutil)
/tmp/mypyenv/bin/pip install 'mypy==<chosen>'
/tmp/mypyenv/bin/mypy mahjong/               # expect: Success: no issues found
```

Then run the CI-equivalent test suite to confirm no behavior changed:

```bash
pytest -m "not integration and not linux_only"   # was 1254 passed on 2026-06-12
```

And keep ruff green (it is, via the `==0.5.5` pin; CI installs ruff from
pyproject):

```bash
ruff format --check . && ruff check .            # both clean under 0.5.5
```

## Guardrails / gotchas (learned this session)

- **Pin tool versions to match pre-commit.** CI installs lint/type tools from
  `pyproject` dev deps with no floor of its own; a floating `>=` is what drifted
  ruff (and mypy) away from the pinned pre-commit versions and reddened CI.
- **`bots/sample-botzone` is a git submodule.** CI doesn't check it out, but a
  local `ruff format .` / `mypy` would recurse into vendored code. Ruff already
  excludes it (`tool.ruff.extend-exclude`); make sure mypy does too if needed.
- **Stacked-PR merge order bit us once:** if you stack a PR, merge the *bottom*
  to `main` first, then **retarget the top to `main` before merging** — or it
  lands in the orphaned base branch and never reaches `main` (that's why the
  stats work needed re-landing as PR #42).

## Definition of done

`main` CI all green: ruff format-check ✓, ruff lint ✓, **mypy ✓**, pytest ✓,
across the matrix (Ubuntu/macOS × Py 3.12/3.13). Pin bumped in pyproject **and**
.pre-commit-config.yaml together.

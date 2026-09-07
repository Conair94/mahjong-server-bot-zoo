# mahjong-server-bot-zoo

A home-hosted Mahjong Competition Rules (MCR) server with a browser client,
scripted and heuristic bots, replayable records, and self-play tools. Python 3.12+.

Start with the [September 2026 foundation audit](docs/foundation-audit-2026-09-07.md)
for verified behavior, remaining correctness gaps, and recovered Git history.
This is a working local-play and bot-development project; competition fidelity and
learned policies are unfinished.

## What works

- Authenticated browser play: multi-table lobby, human and bot seats, spectators,
  reconnect, between-hand readiness, and account history/replays backed by SQLite.
- Two rulesets: `mcr-2006` and `mcr-house-3fan`. Live tables default to the house
  ruleset; the self-play CLI defaults to `mcr-2006`.
- In-process `v0` and `v1` bots, hand analysis, score displays, profiles,
  achievements, chat, and claim audio. These are implemented; the old roadmap
  incorrectly described many of them as future work.
- Deterministic engine/record fixtures, serial and process-parallel self-play,
  resume with record validation, and evaluation summaries.
- Local admin console, account/invite management, server supervision, graceful
  drain, health endpoint, and optional Cloudflare Tunnel integration.

Botzone subprocess support is experimental. Its initial hand delivery is fixed,
but claim translation still needs judge-backed verification. There is no trained
model, training pipeline, or demonstrated learning result. The docs-pane feature
was never merged; its branch is preserved in the Git archive.

## Development setup

```bash
python3.13 -m venv .venv
source .venv/bin/activate
python -m pip install -c requirements-dev.txt -e '.[dev,web]'
python -m playwright install chromium
pre-commit install
```

Python 3.12 is also supported. `PyMahjongGB` includes native code; building it may
require a C++ toolchain. `requirements-dev.txt` pins the reviewed development
resolution; it is a constraints file, not a hash-locked deployment specification.
For a runtime-only source install, use `python -m pip install -e .`.

```bash
# Create the first admin; the command prompts for a password.
MAHJONG_DATA_DIR=./var/mahjong python -m mahjong account create \
  --username alice --display Alice --admin

# Start local play, then open http://127.0.0.1:8400/
MAHJONG_DATA_DIR=./var/mahjong python -m mahjong serve
```

Use the lobby to create/join a table and select its seats. To bind on your LAN,
set `MAHJONG_LISTEN_ADDR=0.0.0.0:8400`. The separate admin console is loopback-only:
`python -m mahjong control` or `./scripts/mahjong-console --autostart-server`.
See [server lifecycle](docs/specs/server-lifecycle.md) and
[admin console](docs/specs/admin-console.md) for configuration.

## Verification

Run with the virtual environment activated:

```bash
ruff format --check .
ruff check .
python -m mypy mahjong/
python -m pytest --ignore=tests/web
python -m pytest tests/web
python -m build
python scripts/check_distribution.py dist/*.whl
```

Pre-commit runs formatting, lint, types, and the fast engine/record suite. CI
runs the non-browser suite on Linux/macOS and Python 3.12/3.13, plus a separate
Chromium job and distribution checks. Browser tests take roughly 14 minutes
locally. CI must actually run after pushing before cross-platform status is known.

Use `python -m mahjong play-test` for a canned-hand smoke test (its generated
`hand.jsonl` is ignored). `python -m mahjong selfplay --help` describes the corpus
runner. The bundled subprocess examples currently require this source checkout;
do not treat their termination or an eval number as proof of Botzone correctness.

## Architecture and next work

| Boundary | Code | Contract |
| --- | --- | --- |
| Pure game state, legal actions, scoring | `mahjong/engine/` | [engine API](docs/specs/engine-api.md), [state](docs/specs/state-schema.md) |
| Adapter lifecycle and hand progression | `mahjong/table/`, `mahjong/adapters/` | [seat port](docs/specs/seat-port.md) |
| Connections, reconnect, live tables | `mahjong/sessions/`, `mahjong/server/` | [session mux](docs/specs/session-mux.md) |
| Accounts, SQLite, records and replay | `mahjong/persistence/`, `mahjong/records/` | [auth](docs/specs/auth.md), [record format](docs/specs/record-format.md) |
| Bot subprocesses and self-play | `mahjong/bots/`, `mahjong/selfplay/` | [bot protocol](docs/specs/bot-runner-protocol.md), [self-play](docs/specs/selfplay-harness.md) |
| Browser and admin assets | `mahjong/web/static/`, `mahjong/control/static/` | [admin console](docs/specs/admin-console.md) |

The most valuable next work is finishing MCR event semantics and Botzone claim
translation, recording engine revision provenance, and moving blocking CPU/SQLite
work out of the shared event loop. Avoid starting RL experiments before those
correctness gates are satisfied. See the prioritized audit and the canonical
[deferred ledger](docs/specs/feedback-backlog.md#deferred-ledger-def-nn).

[CLAUDE.md](CLAUDE.md) defines the working agreement. The original
[server plan](docs/server-plan.md) and [AI plan](docs/ai-plan.md) preserve design
intent; their historical completion markers are not the current status inventory.

# Handoff — Ops loose ends & hygiene (before Layer 9)

**Created:** 2026-06-03 · **Context:** Spec 25 (admin console) and three live-play
gameplay fixes merged to `main` via PR #2 (merge `f897a5f`). Layers 0–8 of
[implementation-order.md](specs/implementation-order.md) are complete. The decision
for the next session is to **close ops loose ends before starting Layer 9 (the bot
zoo / RL north star)**.

This document is the pickup list. Do **Section A** (cheap hygiene) first to get a
clean tree, then **Section B** (the actual ops work). Section C has the context a
cold start needs.

---

## A. Cheap hygiene (do first — ~15–20 min)

### A1. Delete merged local branches
Both are merged to `main`; they only add noise to `git branch`.
```bash
git branch -d spec-25-admin-console spec-24-invite-registration
```
(Use `-D` if `-d` complains about the un-merged handoff branch; verify with
`git branch --merged main` first.)

### A2. Stop tracking runtime data under `var/`  ← real wart
`var/mahjong/` is **tracked and not gitignored**, so every local game dirties the
tree: `var/mahjong/records/t1/*.jsonl` get modified and `mahjong.db-wal` / `-shm`
appear as untracked on every run. This pollutes every future diff.

**Before removing, investigate** (per the working agreement — strange state can be
intentional): confirm nothing under `var/` is a referenced *fixture*. Test fixtures
live under `tests/_fixtures/` (e.g. `s2_e2e_record.jsonl`), **not** `var/`, so `var/`
should be pure runtime. Quick check:
```bash
grep -rn "var/mahjong" mahjong/ tests/ docs/ | grep -v "data_dir"   # expect no code depends on it
git ls-files var/                                                    # what's currently tracked
```
Then:
```bash
printf '\n# Runtime data (DB, WAL, live records)\nvar/\n' >> .gitignore
git rm -r --cached var/
git commit -m "chore: stop tracking var/ runtime data (DB + live records)"
```
Note: the default `data_dir` is `var/mahjong` (see `mahjong/server/config.py`).
The console/server will re-create it at runtime; removing it from git does not
affect a running deploy, only stops versioning the churn.

### A3. Memory consolidation
Flagged at the last several session starts and now well warranted (Spec 24, Spec 25,
and the gameplay fixes all landed in ~2 days). Run:
```
/extract-learnings   (consolidation mode)
```
to prune stale entries and capture this run's learnings.

### A4. (optional) Pre-existing lint nit
`mahjong/server/orchestrator.py:642` trips ruff `UP017` (`datetime.timezone.utc` →
`datetime.UTC`). It predates recent work and was deliberately left out of the
gameplay-fix commit for scope. Sweep it up here if you want a clean `ruff check`.

---

## B. Ops loose ends

### B1. Verification debt — confirm recent work in *real* play
The last two sessions shipped features verified by tests/headless harnesses but not
all exercised end-to-end. None are known-broken; they are **owed confirmations**.

- **B1a. Live Cloudflare tunnel.** The `TunnelSupervisor` was tested only against a
  missing binary and a fake binary — never a real `cloudflared` spawn. **cloudflared
  IS installed** (`2026.3.0`), so this is now doable:
  1. `./scripts/mahjong-console --autostart-server`
  2. In the dashboard → **Tunnel** pane → **Start tunnel**; confirm a
     `*.trycloudflare.com` URL appears and the **copy** button works.
  3. Open that URL from a device **off your home network** (phone on cellular) and
     load the login page. Then **Stop tunnel** and confirm it tears down.
  Files: [mahjong/control/tunnel.py](../mahjong/control/tunnel.py),
  Tunnel pane in [mahjong/control/static/admin.js](../mahjong/control/static/admin.js).

- **B1b. Live hand-end summary (validates the bug-2 fix in real play).** The
  HAND_END→summary dispatch was verified with a *synthetic* frame in Playwright
  ([tests/web/test_hand_end_dispatch.py](../tests/web/test_hand_end_dispatch.py)),
  not by playing a hand to terminal. Play one full hand in the browser (vs. 3 canned
  bots is fine) and confirm the §22.9 summary renders with real scores/fan/revealed
  hands when the hand ends.

- **B1c. In-game feedback (validates the bug-1 fix in real play).** From *inside* a
  table (not the lobby), submit a bug report and confirm the modal closes (ACK) and a
  file lands in `<data_dir>/reports/`. Covered by
  [tests/server/test_feedback_in_game.py](../tests/server/test_feedback_in_game.py);
  this is just the real-play confirmation.

- **B1d. Admin console pane pixels.** Data paths for Invites/Accounts/Logs/Health/
  Tunnel/Feedback/Training are verified over the live WS, but their *rendering* was
  never eyeballed. Open each pane once and sanity-check the layout.

### B2. Layer 8.8 — deferred lifecycle hardening
Step 8.5 shipped a "pragmatic cut" of [server-lifecycle.md](specs/server-lifecycle.md);
the rest was deferred with "no current user-visible failure pressure but required for
the proper S3 exit gate." Now relevant because the server is about to be used for real.
Sub-steps (from [implementation-order.md](specs/implementation-order.md) §8.8), each
test-first against the named server-lifecycle.md fixtures:

- **8.8.a — `GET /health` endpoint** on the WS listener (or `MAHJONG_HEALTH_LISTEN_ADDR`):
  200 normal / 503 during drain / 500 on DB stall. Fixtures 9, 10, 11.
  *(Note the overlap: the admin console's Health pane reads `/admin/status`; this is a
  separate, unauthenticated liveness endpoint for the tunnel/uptime checker. Decide
  whether they share a probe.)*
- **8.8.b — Drain-timeout escalation.** After `MAHJONG_DRAIN_TIMEOUT_SECONDS` (default
  30s) cancel remaining hand tasks and force-close connections. Fixture 14.
- **8.8.c — WAL checkpoint hooks.** Checkpoint on drain end (fixture 15) + periodic
  every `MAHJONG_WAL_CHECKPOINT_SECONDS` (default 300s, fixture 20).
- **8.8.d — Periodic session cleanup.** Expire `sessions` rows past `expires_at_ms`
  every `MAHJONG_SESSION_CLEANUP_SECONDS` (default 60s). Fixture 19.
- **8.8.e — Structured JSON logging.** `MAHJONG_LOG_FORMAT=json` formatter; no secrets
  in fields (log calls already use `extra=`). Fixture 21.
- **8.8.f — SIGKILL-recovery standalone fixture.** Extract the in-progress→ABORTED
  reconciliation into its own focused test. Fixture 16.

**Gate:** every server-lifecycle.md fixture (1–22) green.

### B3. Layer 8.11 — mid-hand late-join refuse (deferred defect)
A real defect, independent of 8.8: a third party can `ATTACH` to a previously-UNBOUND
human seat at a table whose hand is **already running** — the attach succeeds but the
joiner gets a pre-hand snapshot with no event replay (empty ring buffer during
UNBOUND). v1 fix: **refuse with a new `hand_in_progress` wire error code** and suppress
Join buttons on `IN_PROGRESS` tables in the lobby (Spectate stays available). Spec:
[late-join-replay.md](specs/late-join-replay.md), six fixtures. (Alternative B —
replay from the record — is parked.)

### Not in scope here (UX, not ops)
- **8.9 — cardinal-direction table renderer** ([cardinal-ui.md](specs/cardinal-ui.md)):
  a UX polish item (3×3 cardinal grid vs. the current stacked layout + pinwheel
  widgets). Pairs naturally with other client polish, not with this ops pass.

---

## C. Cold-start context

**Run the stack locally:**
```bash
./scripts/mahjong-console --autostart-server   # control console (loopback :8500) + server child
# or just the server:
MAHJONG_DATA_DIR=var/mahjong MAHJONG_LISTEN_ADDR=127.0.0.1:8400 python -m mahjong serve
```

**Tests (verification ladder — default excludes slow):**
```bash
python -m pytest -q -m "not slow"            # full fast suite (~900 tests)
python -m pytest tests/control -q            # admin console
python -m pytest tests/web -q                # browser/Playwright (Chromium auto-launch)
python -m pytest tests/server tests/sessions -q
ruff check mahjong/ tests/
```

**Key entry points:**
- Control console: [mahjong/control/](../mahjong/control/) (`app.py` wires it; `plane.py`
  is the WS brain; `tunnel.py`, `feedback.py`, `health.py`, `supervisor.py`).
- Game server: [mahjong/server/orchestrator.py](../mahjong/server/orchestrator.py)
  (two-phase loop: lobby dispatch, then per-connection in-game loop).
- Web client: [mahjong/web/static/app.js](../mahjong/web/static/app.js) (frame
  dispatch, lobby, game-pane), `render.js` (table + hand-end summary), `apply_event.js`
  (reducer).
- Lifecycle spec: [docs/specs/server-lifecycle.md](specs/server-lifecycle.md).

**Working-agreement reminders for this work:**
- Test-first for the bot↔server protocol, lifecycle invariants, and the wire
  error registry (8.11). Pragmatic-cover for UI/CLI glue.
- No learning/robustness claim without a verification artifact (a failing→passing
  test, a real run with output shown, or a real-play observation).
- The `var/` and branch cleanup are local/reversible — just do them. The live tunnel
  test is outward-facing (a public URL) — fine to run, just tear it down after.

**After Section A + B:** the path is clear to start **Layer 9 — the bot zoo**.
Suggested entry point (from the prior session's recommendation): draft a Layer 9 spec
in `docs/specs/`, then build a **real random-legal-move policy bot** wired as a genuine
seat identity (replacing the `b_random` tsumogiri placeholder), so the RL sanity
baseline "random-vs-random ≈ uniform win rates" from `CLAUDE.md` becomes runnable.

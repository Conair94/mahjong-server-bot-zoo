# Spec 39 — Achievements

Profile achievements derived **at read time** from the existing
`hand_index` + `hand_participants` tables — no new write path, no new
tables, retroactive over all recorded history. Rendered as a section on the
profile page; delivered as an additive `achievements` field on the existing
`PROFILE` frame.

Builds on:
- [profile-and-settings.md](profile-and-settings.md) § B — the profile read
  stack (`account_stats`, executor offload) this extends.
- [sqlite-schema.md](sqlite-schema.md) — everything here is computable from
  `winner_seat`, `fan_total`, `terminal_kind`, `final_score_delta`,
  `started_at_ms` on finalized live hands. Fan *names* are not persisted,
  so yaku-specific badges (e.g. "won with Seven Pairs") are out of scope
  until the fan list is (open question).

## Goals

- A fixed catalog of achievements with **earned/unearned + progress**
  (`progress`/`target`), so the profile shows both trophies and the next
  goal.
- Derive-at-read: idempotent, retroactive, zero new failure modes in the
  hand-settlement path.

## Non-goals

- **No earned-at timestamps / unlock notifications** (would need an event
  log; revisit if someone asks "when did I get this?").
- **No yaku-specific badges** (fan names unpersisted — open question).
- **No bot achievements** (account-holders only, same filter as
  `account_stats`: finalized, live-source hands).

## Catalog (v1)

Tiered tracks share a metric; singles stand alone. All metrics use the same
finalized/live filter as `account_stats`.

| id | name | target | metric |
| --- | --- | --- | --- |
| `first-win` | First Blood | 1 | hands won |
| `wins-10` | Seasoned | 10 | hands won |
| `wins-50` | Master | 50 | hands won |
| `wins-100` | Legend | 100 | hands won |
| `hands-50` | Regular | 50 | hands played |
| `hands-200` | Resident | 200 | hands played |
| `hands-500` | Lifer | 500 | hands played |
| `fan-8` | Big Hand | 8 | best winning fan |
| `fan-16` | Monster Hand | 16 | best winning fan |
| `fan-24` | Limit Break | 24 | best winning fan |
| `streak-3` | Hot Streak | 3 | longest consecutive-win run |
| `streak-5` | Unstoppable | 5 | longest consecutive-win run |
| `in-the-black` | In the Black | 20 | hands played, **and** lifetime total score > 0 |
| `draws-10` | Wall Warrior | 10 | exhaustive draws survived |

Streak semantics: over the account's finalized live hands ordered by
`started_at_ms`, the longest run of hands where the account's seat is the
winner. Draws and losses both break the run.

## Shapes

`Persistence.account_achievements(account_id) -> list[dict]`, each:

```json
{"id": "wins-10", "name": "Seasoned", "desc": "Win 10 hands",
 "earned": false, "progress": 4, "target": 10}
```

`progress` is clamped to `target` once earned. The catalog order is the wire
order (stable; the client renders verbatim).

`PROFILE` gains `achievements: NotRequired[list[dict]]` — additive, so old
clients ignore it and test servers that omit it render no section.

## Verification fixtures

1. Empty account → every achievement unearned, `progress 0`.
2. Threshold boundaries: 9 wins → `wins-10` unearned `9/10`; the 10th win
   flips it earned `10/10`.
3. Streak: W W L W W W → `streak-3` earned, `streak-5` unearned `3/5`
   (longest run, not current run); a draw breaks a run the same as a loss.
4. `fan-*` uses the **best winning** fan only (a 24-fan *loss* earns
   nothing).
5. `in-the-black` needs both legs: positive total at 19 hands → unearned;
   at 20 → earned; negative total at 20 → unearned.
6. Same exclusions as `account_stats`: unfinalized and selfplay hands count
   toward nothing.
7. Wire: PROFILE round-trips with `achievements` preserved; profile page
   renders earned vs in-progress rows from a dispatched PROFILE frame.

## Open questions

- Persist per-hand fan *names* (enables yaku badges and richer history) —
  next time the settlement write path is touched.
- Unlock toasts in-game ("Achievement unlocked!") — needs an event log +
  push; only worth it if the profile section lands well.

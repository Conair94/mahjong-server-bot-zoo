# Spec 2 — Game record format

The on-disk log of a played hand. This is the durable artifact the system produces: the input to the AI training corpus, the input to the replay system, the input to analysis overlays, and the integrity hook for "did the engine behave deterministically." A record once written is immutable.

Builds on [state-schema.md](state-schema.md) — record events carry the same tile tokens, action grammar, and seat/phase concepts. If the schema and the record format ever diverge, the record format is wrong and gets updated, not the other way around.

**Status:** draft, pre-S0.

## Goals

- **Replayable.** `replay(record) -> Iterator[GameState]` reproduces every canonical state the engine passed through, byte-identically. No other input required beyond the record itself.
- **Per-seat replayable.** `replay(record, seat=S) -> Iterator[SeatView]` is what's actually consumed by training data loaders, analysis overlays, and "review your last hand" UI. Concealed information is filtered at projection time, not stripped from the record.
- **Botzone-superset.** Every record can be losslessly exported to the Botzone judge-log format. Our format carries *more* (per-seat identity, timing, passed-on claim opportunities, integrity hashes) but never *less*. This is what makes the S1 exit criterion ("judge accepts our records") buildable.
- **One file per hand.** A hand is a self-contained unit: one deal, one outcome, one record. Multi-hand matches are sequences of records linked by `match_id`, not concatenated files. Matches the granularity of the Botzone `mjdata.zip` corpus (one "game" = one hand).
- **Append-only.** Events are written as they occur; the file is closed at `TERMINAL`. Never edited, never rewritten. A correction is a new record with a `corrects:` pointer to the original.
- **Self-describing.** The header includes the format version, ruleset reference, and seat identities. A record loaded five years from now is interpretable without consulting external state.

## Non-goals

- **Not a database.** The record is a log file. The SQLite index (S3) holds queryable metadata (winner, players, fan, timestamps); the record is the source of truth those rows point to.
- **Not a stream protocol.** Live spectators read the in-progress file from disk, not a separate event stream (decision recorded in [server-plan.md](../server-plan.md) open questions). This spec describes the file; how readers tail it is a Tier-2 concern.
- **No incremental encoding tricks.** Each event is a complete JSON object on its own line. No deltas-of-deltas, no compression at the format level. If size becomes a problem, gzip the closed files — don't complicate the schema.
- **No "redacted" records.** Per-seat views are computed at read time by the replay projection. The on-disk file always carries full information. Privacy is a read-side concern, not a storage-side one.

## File layout

```
records/
  {year}/{month}/{hand_id}.jsonl
```

- `year`, `month` are derived from the hand's start timestamp (UTC).
- `hand_id` is a UUIDv7 (timestamp-prefixed, lexicographically sortable). The timestamp prefix doubles as a sort key inside a month directory.
- Files use the `.jsonl` extension. UTF-8, LF line endings, one JSON object per line, trailing newline.

**Why UUIDv7:** sortable like a serial number, collision-free across hosts (no central counter needed when we eventually run multiple servers), embeds the creation time so the filename + directory are mutually consistent. Don't use UUIDv4 — losing the time-order property hurts log readability for no benefit.

**Why month-bucketed directories:** keeps any single directory under ~10K files even at heavy use (a home server won't hit this, but the structure scales). Easier to rsync incremental backups too.

## Top-level shape

A record is a JSONL file. The **first line is always a `HEADER` event**, the **last line is always a `FOOTER` event**, and everything in between is one event per game occurrence in chronological order.

```jsonl
{"event": "HEADER", ...}
{"event": "DEAL", ...}
{"event": "DRAW", ...}
{"event": "DISCARD", ...}
{"event": "CLAIM_WINDOW", ...}
{"event": "CLAIM_DECISION", ...}
{"event": "CLAIM_RESOLUTION", ...}
...
{"event": "HAND_END", ...}
{"event": "FOOTER", ...}
```

Every event has these common fields:

```python
{
    "event": str,         # event type — uppercase, see catalog below
    "seq": int,           # 0-indexed sequence number; strictly +1 each line
    "turn_index": int,    # mirrors GameState.turn_index at the moment of the event
    "phase": str,         # GameState.phase at the moment of the event (post-application)
    "ts": str,            # ISO-8601 UTC timestamp, millisecond precision: "2026-05-19T22:34:17.412Z"
}
```

`seq` is a strict invariant: any gap or repeat is a corrupted record. `turn_index` and `phase` are denormalized for fast filtering without replaying.

## Event catalog

### `HEADER` (always seq=0)

```json
{
  "event": "HEADER",
  "seq": 0,
  "turn_index": 0,
  "phase": "DEAL",
  "ts": "2026-05-19T22:34:17.412Z",

  "format_version": 1,
  "hand_id": "01970e8a-9d3e-7c4a-9b1f-0a1b2c3d4e5f",
  "match_id": "01970e8a-9c00-7000-8000-000000000000",
  "hand_index_in_match": 3,

  "ruleset": {
    "id": "mcr-2006",
    "version": 1,
    "config_hash": "sha256:abc123..."
  },

  "seed": "305419896",

  "seats": [
    {"seat": 0, "wind": "F1", "identity": {"kind": "human", "user_id": "u_42",  "display": "alice"}},
    {"seat": 1, "wind": "F2", "identity": {"kind": "bot",   "bot_id":  "b_rule_v1", "version": "0.1.0"}},
    {"seat": 2, "wind": "F3", "identity": {"kind": "human", "user_id": "u_17",  "display": "bob"}},
    {"seat": 3, "wind": "F4", "identity": {"kind": "bot",   "bot_id":  "b_random", "version": "0.0.1"}}
  ],

  "server": {
    "version": "0.1.0",
    "git_sha": "abc123def456",
    "host": "lockhart-mini"
  }
}
```

The header is sufficient to reconstruct `initial_state(ruleset, seed)`. Everything else in the file is action history.

- `match_id` groups related hands (one E-round, one tournament). `null` for a one-off hand.
- `hand_index_in_match` is 0 for standalone hands. The dealer rotation across hands is encoded by `seats[i].wind` — no need to derive it.
- `identity.kind` is `"human" | "bot" | "canned" | "spectator-driver"` etc.; full enum in [seat-port.md](seat-port.md).
- `seed` is the value passed to `initial_state`. Combined with `ruleset.config_hash`, this *fully determines* the initial deal — no need to record the wall contents.
- `server.version` and `server.git_sha` are post-mortem hooks. "Which build of the server produced this record?"

### `DEAL` (seq=1, exactly one per hand)

```json
{
  "event": "DEAL",
  "seq": 1,
  "turn_index": 0,
  "phase": "DISCARD",
  "ts": "2026-05-19T22:34:17.450Z",

  "concealed": [
    ["W1","W3","W5","B2","B7","T1","T1","T6","T9","F1","F1","J2","J3","W7"],
    ["W2","W4","B3","B5","B8","T2","T4","T7","F2","F3","F4","J1","J1"],
    ["W6","W8","W9","B1","B4","B6","B9","T3","T5","T8","F3","J2","J3"],
    ["W2","W5","B2","B5","B8","T1","T4","T7","F2","F4","J1","J2","J3"]
  ],
  "flowers_drawn": [
    {"seat": 0, "flowers": []},
    {"seat": 1, "flowers": ["H5"], "replacements": ["W7"]},
    {"seat": 2, "flowers": [], "replacements": []},
    {"seat": 3, "flowers": [], "replacements": []}
  ],
  "wall_remaining_after_deal": 84,

  "state_hash": "sha256:def456..."
}
```

- `concealed[i]` is seat `i`'s 13- or 14-tile starting hand (dealer gets 14: 13 + the first draw, conceptually). The dealer's 14th tile is the one they will discard or use to declare on turn 1.
- `flowers_drawn[i].flowers` are bonus tiles seat `i` drew during the initial deal; `replacements` are the tiles drawn from the back of the wall to replace them. Recorded explicitly because flower replacement is RNG-consuming and we want the replay to verify it matched.
- `wall_remaining_after_deal` is for sanity-checking against the determinism contract: replaying the seed should produce the same number.
- `state_hash` is the hash of the canonical `GameState` after dealing. See [determinism.md](determinism.md) for the canonical hash function. Mid-record `state_hash` entries are *optional* per event in normal records; the `DEAL`, `HAND_END`, and `FOOTER` events always carry them.

**Why include `concealed` when it's derivable from the seed?** Two reasons:
1. **Defense in depth.** If a future engine refactor silently changes how the seed maps to a deal, the recorded `concealed` lets us detect that with an existing record, not just with the seed.
2. **Reader independence.** A training-data loader can read the deal without instantiating the engine or its RNG implementation. The seed is for re-running; the explicit hands are for reading.

### `DRAW`

```json
{
  "event": "DRAW",
  "seq": 5,
  "turn_index": 2,
  "phase": "DISCARD",
  "ts": "2026-05-19T22:34:18.901Z",

  "seat": 1,
  "tile": "T5",
  "flower_replacements": []
}
```

- `tile` is what was drawn. Recorded for the same defense-in-depth + reader-independence reasons as `concealed`.
- `flower_replacements` is non-empty if the draw was a flower and triggered replacement(s). Each replacement is a `{"flower": "H2", "tile": "B7"}` object. If a replacement *itself* is a flower, the chain continues — record every step.

### `DISCARD`

```json
{
  "event": "DISCARD",
  "seq": 6,
  "turn_index": 2,
  "phase": "CLAIM_WINDOW",
  "ts": "2026-05-19T22:34:19.310Z",

  "seat": 1,
  "tile": "F4",
  "decision_ms": 408,
  "from_hand": true
}
```

- `decision_ms` is wall-clock elapsed time from the moment the seat received the prompt to the moment it submitted the action. Training cares about this (was the bot near its time budget?). Ops cares about this (slow human, timeout).
- `from_hand: true` means the discarded tile was already in concealed hand (normal discard). `from_hand: false` means the seat discarded the tile they just drew without taking it into hand ("tsumogiri" in Riichi parlance — captured because some MCR yaku gate on it).

### `CLAIM_WINDOW`

Emitted once after each `DISCARD` that has *any* legal claim opportunity. If no seat could claim, no `CLAIM_WINDOW` event fires — the engine advances directly to the next `DRAW`.

```json
{
  "event": "CLAIM_WINDOW",
  "seq": 7,
  "turn_index": 2,
  "phase": "CLAIM_WINDOW",
  "ts": "2026-05-19T22:34:19.311Z",

  "discard_seq": 6,
  "opportunities": [
    {"seat": 2, "claim": "CHI", "chi_tiles": ["F2","F3","F4"]},
    {"seat": 3, "claim": "PENG", "tile": "F4"}
  ],
  "deadline_ms": 1000
}
```

- `discard_seq` references the `DISCARD` event the claim is on.
- `opportunities` is the full set of *possible* claims, not just the ones eventually taken. This is what makes the record a **defense training signal**: "seat 2 had a CHI here and chose not to take it" is a labeled training example that bare action logs throw away.
- `deadline_ms` is the per-window timeout (configurable per table).

### `CLAIM_DECISION` (zero or more per `CLAIM_WINDOW`)

```json
{
  "event": "CLAIM_DECISION",
  "seq": 8,
  "turn_index": 2,
  "phase": "CLAIM_WINDOW",
  "ts": "2026-05-19T22:34:19.520Z",

  "window_seq": 7,
  "seat": 3,
  "decision": "PASS",
  "decision_ms": 209
}
```

For non-PASS decisions, the same action fields as the action grammar:

```json
{
  "event": "CLAIM_DECISION",
  "seq": 9,
  "turn_index": 2,
  "phase": "CLAIM_WINDOW",
  "ts": "2026-05-19T22:34:19.602Z",

  "window_seq": 7,
  "seat": 2,
  "decision": "CHI",
  "chi_tiles": ["F2","F3","F4"],
  "decision_ms": 291
}
```

One `CLAIM_DECISION` per seat that had an opportunity. Order in the file is submission order (useful for "did the slow bot get its decision in before the fast one?" analysis), not seat order.

### `CLAIM_RESOLUTION` (exactly one per `CLAIM_WINDOW`)

```json
{
  "event": "CLAIM_RESOLUTION",
  "seq": 10,
  "turn_index": 2,
  "phase": "DISCARD",
  "ts": "2026-05-19T22:34:19.605Z",

  "window_seq": 7,
  "outcome": "CLAIMED",
  "winning_seat": 2,
  "winning_claim": "CHI",
  "winning_chi_tiles": ["F2","F3","F4"]
}
```

- `outcome` is `"PASSED"` (everyone passed; turn advances normally) or `"CLAIMED"` (highest-priority claim took the tile).
- `winning_*` fields are absent for `"PASSED"`.
- The next event after `CLAIM_RESOLUTION` is either a `DRAW` (passed; next seat in turn draws) or a `DISCARD` (claimed; claiming seat now discards from their new hand).

### `HAND_END` (exactly one per hand)

```json
{
  "event": "HAND_END",
  "seq": 42,
  "turn_index": 18,
  "phase": "TERMINAL",
  "ts": "2026-05-19T22:36:02.118Z",

  "kind": "HU",
  "winner": [2],
  "win_tile": "T8",
  "win_type": "DISCARD",
  "deal_in_seat": 1,
  "fan": [
    {"name": "Mixed Shifted Chows", "value": 6},
    {"name": "Half Flush",          "value": 6},
    {"name": "Concealed Hand",      "value": 2}
  ],
  "fan_total": 14,
  "score_delta": [-8, -22, +38, -8],
  "final_hands": [
    {"seat": 0, "concealed": ["..."], "melds": [...], "flowers": [...]},
    {"seat": 1, "concealed": ["..."], "melds": [...], "flowers": [...]},
    {"seat": 2, "concealed": ["..."], "melds": [...], "flowers": [...]},
    {"seat": 3, "concealed": ["..."], "melds": [...], "flowers": [...]}
  ],

  "state_hash": "sha256:fed987..."
}
```

- `winner` is a list to accommodate the multi-winner-on-one-discard case (open question in state-schema). For single winners, length 1.
- `final_hands` is the canonical state's seat structure at terminal time. Lets the replay system render the reveal without re-running the engine.

### `FOOTER` (always last line)

```json
{
  "event": "FOOTER",
  "seq": 43,
  "turn_index": 18,
  "phase": "TERMINAL",
  "ts": "2026-05-19T22:36:02.120Z",

  "event_count": 44,
  "rng_cursor_final": 1284,
  "state_hash_final": "sha256:fed987...",
  "checksum": "sha256:0011...",
  "corrects": null
}
```

- `event_count` must equal `seq + 1` (sanity check on the file).
- `rng_cursor_final` is the RNG cursor at terminal. Mismatched replay → divergence detected.
- `state_hash_final` mirrors `HAND_END.state_hash`; including it in the footer means a reader that streams only the first and last lines (a cheap integrity check) can validate without parsing the middle.
- `checksum` is `sha256` over every line *except* the footer itself, with LF separators. If the checksum doesn't match on read, the record is corrupt. (Cheap to compute incrementally as events are appended.)
- `corrects` points to a prior `hand_id` if this record is a corrected version (data error discovered post-hoc); `null` for original recordings.

## Worked example: minimal record

A two-event hand: dealer draws and immediately self-draws HU (toy case for testing the format). Trimmed for readability.

```jsonl
{"event":"HEADER","seq":0,"turn_index":0,"phase":"DEAL","ts":"2026-05-19T22:34:17.412Z","format_version":1,"hand_id":"01970e8a-9d3e-7c4a-9b1f-0a1b2c3d4e5f","match_id":null,"hand_index_in_match":0,"ruleset":{"id":"mcr-2006","version":1,"config_hash":"sha256:abc"},"seed":12345,"seats":[{"seat":0,"wind":"F1","identity":{"kind":"canned","script":"toy_hu_on_deal"}},{"seat":1,"wind":"F2","identity":{"kind":"canned","script":"pass"}},{"seat":2,"wind":"F3","identity":{"kind":"canned","script":"pass"}},{"seat":3,"wind":"F4","identity":{"kind":"canned","script":"pass"}}],"server":{"version":"0.0.1","git_sha":"dev","host":"laptop"}}
{"event":"DEAL","seq":1,"turn_index":0,"phase":"DISCARD","ts":"2026-05-19T22:34:17.450Z","concealed":[["W1","W1","W1","B5","B5","B5","T9","T9","T9","F1","F1","F1","J1","J1"],["..."],["..."],["..."]],"flowers_drawn":[{"seat":0,"flowers":[],"replacements":[]},{"seat":1,"flowers":[],"replacements":[]},{"seat":2,"flowers":[],"replacements":[]},{"seat":3,"flowers":[],"replacements":[]}],"wall_remaining_after_deal":84,"state_hash":"sha256:111"}
{"event":"HAND_END","seq":2,"turn_index":0,"phase":"TERMINAL","ts":"2026-05-19T22:34:17.500Z","kind":"HU","winner":[0],"win_tile":"J1","win_type":"SELF_DRAW","deal_in_seat":null,"fan":[{"name":"Big Four Winds","value":88}],"fan_total":88,"score_delta":[+264,-88,-88,-88],"final_hands":[],"state_hash":"sha256:222"}
{"event":"FOOTER","seq":3,"turn_index":0,"phase":"TERMINAL","ts":"2026-05-19T22:34:17.501Z","event_count":4,"rng_cursor_final":56,"state_hash_final":"sha256:222","checksum":"sha256:abcd","corrects":null}
```

(Yes, the dealer hand here is impossible from a 144-tile deal — it's a contrived fixture for spec illustration, not a real game.)

## Botzone export

For S1's judge-acceptance fixture and for ongoing Botzone-compat work, every record must export to the Botzone judge-log format. The mapping is mechanical because the action grammar is shared:

- `HEADER` → Botzone match metadata (player names from `seats[].identity.display`, ruleset name).
- `DEAL` → Botzone's "0" / "1" setup + deal messages, one per seat.
- `DRAW` → Botzone "2" message to the drawing seat.
- `DISCARD` → Botzone "3" message broadcast.
- `CLAIM_WINDOW` → no direct equivalent; consumed/dropped.
- `CLAIM_DECISION` → Botzone "3" response messages (the claim or PASS).
- `CLAIM_RESOLUTION` → no direct equivalent; consumed.
- `HAND_END` → Botzone game-end message with fan and score.

The exporter is implemented once, lives in the record store module, and is validated against the official judge in S1.

**The reverse direction (Botzone log → our record) is not symmetric.** A Botzone log lacks `CLAIM_WINDOW` opportunities that everyone passed on, lacks `decision_ms`, lacks `state_hash`es, and lacks `seats[].identity` beyond display name. Importing the `mjdata` corpus produces records with those fields as `null` / `unknown`. Training pipelines that depend on the missing fields must explicitly filter for "native records only."

## Alternatives considered

**One record per match vs. one record per hand.**

- Considered: one file holds the entire E/S/W/N round, with hand boundaries marked by an event.
- Chose per-hand because (a) it matches the Botzone `mjdata` corpus granularity (no impedance for ingest), (b) a crash mid-match doesn't risk corrupting the prior hand's record, (c) hand records are bounded in size (~hundreds of lines, ~tens of KB) which makes them friendly to grep and to incremental backup. Matches are reassembled by joining on `match_id`.

**JSONL vs. Protobuf vs. a Botzone-native log.**

- Considered: Protobuf for size and schema enforcement; Botzone log format directly.
- Chose JSONL because (a) human-readable diff and grep are load-bearing for debugging RL bugs ("why did this discard get accepted?"), (b) schema enforcement comes from typed loaders and the verification fixtures, not the wire format, (c) Botzone format is informationally lossy (no claim-window opportunities), so adopting it natively would forfeit the defense training signal. We export *to* Botzone, not *from* it.

**Storing the wall contents in the record.**

- Considered: writing the full shuffled wall as part of `DEAL` for trivial replay.
- Chose not to because (a) the seed + ruleset config_hash already determine the wall (see [determinism.md](determinism.md)), (b) including it would more-than-double record size, (c) the `wall_remaining_after_deal` count + draw events' explicit `tile` fields already provide defense-in-depth without the full wall payload. If determinism somehow breaks, the seed-replay test catches it; the recorded draws catch it again at a different layer.

**Per-event `state_hash` vs. only at deal/end.**

- Considered: hashing the canonical state after every event for maximum integrity coverage.
- Chose only at `DEAL`, `HAND_END`, and `FOOTER` because (a) those three checkpoints catch every silent divergence at hand granularity, (b) per-event hashing inflates record size by ~30% for diminishing return, (c) a debug mode that enables per-event hashing is a one-line config flag if we ever need to localize a divergence within a hand.

**Recording `decision_ms` separately from `ts`.**

- Considered: deriving decision time from the gap between event timestamps.
- Chose explicit `decision_ms` because (a) event `ts` is when the *event was written*, which differs from when the seat *decided* (network latency, queueing), (b) bot time-budget analysis needs the decision time directly, (c) two separate numbers cost ~16 bytes per event and remove an entire class of "did you mean wall-clock or budget-clock?" bugs.

**`final_hands` in `HAND_END`.**

- Considered: omitting `final_hands` since it's recoverable by replay.
- Chose to include because the reveal is a heavily-used read path (replay UI, training-target extraction, "what did the winner have?"). Paying ~1KB per record to avoid running the engine on every read is a fair trade.

## Verification fixtures this spec implies

1. **Round-trip identity.** For every record in the fixture suite: `record == write(read(record))`. Reading and re-writing produces a byte-identical file. Catches accidental field reordering, whitespace drift, lossy parsers.
2. **Replay reproduces canonical state.** For every record: replaying produces a sequence of `GameState`s whose final `state_hash` matches `FOOTER.state_hash_final`. Per-line `state_hash` entries (when present) match.
3. **Per-seat projection consistency.** For every record and every seat: `replay(record, seat=S)` produces `SeatView`s identical to `project(state_t, S)` for each `t`. Closes the loop with [state-schema.md](state-schema.md) fixture 8.
4. **Privacy on replay.** For every record and every seat `S`: the sequence of `SeatView`s contains zero concealed tokens from seats `!= S` at any timestep before they were revealed (call, hand-end). Automated check.
5. **Sequence integrity.** `seq` is 0..N strictly monotonic with no gaps; `event_count == N+1`; `checksum` matches recomputed value. Any failure → corrupt record, refuse to load.
6. **Botzone export validity.** Pick a fixture record; export to Botzone log; feed the log to the official Botzone judge; judge accepts. This is the S1 exit artifact.
7. **`mjdata` import.** Pick a hand from the Botzone `mjdata` corpus; import to our record format; replay; the imported record's `HAND_END.fan_total` matches the source. Tests the import path's correctness end-to-end.
8. **Claim-window completeness.** For every `CLAIM_WINDOW` in the fixture suite, the `opportunities` list equals `legal_actions(state_at_window_open, seat)` for every seat. Catches the "we forgot to record an opportunity that everyone passed on" regression.
9. **Schema version handling.** A record with `format_version: 1` loads cleanly. A record with `format_version: 2` (synthetic test) is rejected with a clear error, not silently misparsed.

## Open questions

- **Compression.** Gzip closed files? Probably yes (records are repetitive JSON, 5–10× compression typical), but defer until disk usage is measurable. The loader should handle `.jsonl` and `.jsonl.gz` transparently when this lands.
- **Live tailing semantics.** Live spectators read the in-progress file. Question: do they re-read from `seq=0` on each tick, or seek to last-known `seq`? Working answer: seek to last-known `seq`, validate `checksum` is monotonically extendable (each new event's contribution to the running checksum). Defer to the spectator-table phase (S8).
- **Chat events.** Server-plan flagged in-game chat as deferrable. When it lands: a `CHAT` event with `{seat, text, ts}`, no `turn_index` advance, never affects state. Worth pinning the event name now so future records are consistent.
- **Time-out events.** What's the event when a seat fails to decide within `deadline_ms`? Working answer: a synthetic `CLAIM_DECISION` with `decision: "PASS", decision_ms: <deadline>, timeout: true`, plus a `DISCARD` with `from_hand: true, auto_discarded: true, reason: "timeout"` when the actor times out on their own turn (auto-discards the just-drawn tile, "tsumogiri"). Pin this when the seat-port spec lands.
- **Multi-winner score split.** Inherits from state-schema's open question. The `winner: list[int]` and `score_delta: list[int]` shapes already accommodate it; locking the split *math* is an S5 (rule-set) concern, not a format concern.
- **Per-seat decoded views as cached files.** A pre-projected `record_{hand_id}_seat_{S}.jsonl` for cheap training-data reading? Defer; the projection is fast and caching adds a coherence problem. Reconsider only if profiling says it's a bottleneck.

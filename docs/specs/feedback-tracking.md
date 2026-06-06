# Spec 30 — Feedback tracking (triage status for player reports)

Players submit bug reports / feature requests (Spec 23,
[feedback-reporting.md](feedback-reporting.md)); the admin console already **reads** them
([admin-console.md](admin-console.md) § Feedback inbox, via
[`FeedbackInbox`](../../mahjong/control/feedback.py)). What's missing is a way to record
**what happened to each report** — is it triaged, being worked, shipped, or declined — so
"which feedback has been implemented?" has a live answer instead of living only in someone's
head. This spec adds a lightweight **status overlay** on the existing report files plus the
admin-console UI to set it.

The dev-facing companion is [feedback-backlog.md](feedback-backlog.md), the curated source of
truth for implementation status; this feature is its live, server-side mirror.

---

## Goals

- Attach a **triage status** (+ optional backlog id and note) to each on-disk report.
- Set and view status from the **admin console** Feedback pane — no shell access needed.
- Persist status across server restarts.
- Leave the original report `.txt` files **immutable** (a user's words are an audit record;
  status is metadata *about* the report, stored separately).

## Non-goals

- No change to the player-facing feedback modal or the `FEEDBACK` submission path.
- No new datastore: status is a small JSON sidecar next to the reports, not a DB table.
  (Three concrete needs before a table — we have one. Revisit if reports outgrow a sidecar.)
- No auth/identity beyond the existing admin-console token gate.
- No automatic status inference from git/PRs (the backlog doc is curated by hand).

---

## § 30.1 Status vocabulary

Shared verbatim with [feedback-backlog.md](feedback-backlog.md):

```text
open · triaged · in-progress · implemented · verified · wontfix · duplicate
```

A report with no sidecar entry is `open` by default. Any other value is rejected
(`ERROR code=feedback_error`) so the store can't drift to free-text statuses.

## § 30.2 Status store — `reports/status.json`

A single JSON object keyed by report **filename** (the existing stable key —
`YYYYMMDD_HHMMSS_<type>[_N].txt`), so the overlay joins to reports without touching them:

```json
{
  "20260606_000643_bug.txt": {
    "status": "triaged",
    "backlog_id": "FB-01",
    "note": "concealed-gang hang; repro-first",
    "updated": "2026-06-06T01:10:00+00:00"
  }
}
```

- `status` — required, from the vocabulary.
- `backlog_id` — optional `FB-NN` cross-reference to [feedback-backlog.md](feedback-backlog.md).
- `note` — optional, sanitised with the same `sanitise_report_text`-style allow-list (no raw
  text to disk; the security boundary mirrors Spec 23 § 23.1). Empty note allowed here
  (unlike a report body, a status note may legitimately be blank).
- `updated` — server-set ISO-8601 UTC timestamp on every write.

Stored at `data_dir/reports/status.json` (same dir the reports live in). Writes are
**atomic** (write temp + `os.replace`) so a crash mid-write can't corrupt the map. The file
is runtime data — **gitignored** like the rest of `data_dir` (cf. var/-runtime discipline).

## § 30.3 Read side — merge into `FEEDBACK_LIST`

[`FeedbackInbox.list_reports()`](../../mahjong/control/feedback.py) gains a status merge: for
each parsed report row it looks up `status.json[filename]` and adds `status` (default
`"open"`), `backlog_id`, `note`, `updated`. Existing fields (`type/submitted/submitter/text/
filename`) are unchanged, so the current Feedback pane keeps working; the new fields are
additive. Sidecar read stays off the event loop (`run_in_executor`, same as today).

## § 30.4 Write side — `FEEDBACK_UPDATE` control-plane message

A new **control-plane** message (admin console only — token-gated like `FEEDBACK_LIST`,
[admin-console.md](admin-console.md) § Control plane). Handled in
[`mahjong/control/plane.py`](../../mahjong/control/plane.py) alongside `FEEDBACK_LIST`.

### Client → Server

```json
{ "kind": "FEEDBACK_UPDATE", "filename": "20260606_000643_bug.txt",
  "status": "triaged", "backlog_id": "FB-01", "note": "repro-first" }
```

`backlog_id` and `note` optional. `filename` must reference an existing report (no path
separators allowed — reuse the sanitisation boundary; the value is matched against the
`FeedbackInbox` listing, never used to construct a path directly).

### Server → Client (success)

Reply with the refreshed list so the pane re-renders from authoritative state:

```json
{ "kind": "FEEDBACK_LIST", "reports": [ ... ] }
```

### Server → Client (failure)

```json
{ "kind": "ERROR", "code": "feedback_error", "message": "<reason>" }
```

Reasons: unknown `filename`, status outside the vocabulary, malformed payload.

### Validation / handler contract

1. `filename` is a known report (present in `FeedbackInbox` listing) and contains no `/`
   or `\` (path-injection guard). → else ERROR.
2. `status ∈` vocabulary. → else ERROR.
3. `backlog_id` (if present) matches `^FB-\d{2,}$`. → else ERROR.
4. `note` (if present) is sanitised (allow-list, ≤200 chars). Empty allowed.
5. Merge into `status.json` (atomic write, `updated` = now), then reply `FEEDBACK_LIST`.

A `FeedbackStatusStore` class ([mahjong/control/feedback.py](../../mahjong/control/feedback.py),
next to `FeedbackInbox`) owns load/merge/atomic-write; the plane handler is thin.

## § 30.5 UI — Feedback pane status controls

Extend the existing Feedback pane (Lit, admin dashboard). Per report row, add:

- a `status` `<select>` (the seven vocabulary values), preselected to the row's current
  status,
- a small `backlog_id` text input (`FB-NN`),
- an optional `note` input,
- a **Save** button → dispatches `FEEDBACK_UPDATE`; on the returning `FEEDBACK_LIST` the pane
  re-renders. Disable Save while in-flight (idempotency lesson from Spec 29 Bug E).

A status filter (e.g. hide `implemented`/`wontfix`) is a nice-to-have, deferred unless the
list grows unwieldy.

---

## § 30.6 Verification fixtures

Per the working agreement, status logic is small but real (vocabulary enforcement, merge,
atomic write) — so it gets tests; the Lit pane gets a mounted-component test (CSS/computed
caveat from memory) + a browser-verify.

### Unit — `FeedbackStatusStore` ([tests/control/test_feedback_status.py](../../tests/control/test_feedback_status.py))

| Scenario | Expectation |
| --- | --- |
| missing `status.json` | every report merges as `status="open"`, no error |
| set status `triaged` on a report | sidecar has the entry; `updated` set |
| read back via `list_reports` | row shows `status="triaged"`, `backlog_id`, `note` |
| status `"banana"` | rejected (store raises / handler ERRORs); sidecar unchanged |
| `backlog_id="nope"` | rejected (regex) |
| concurrent overwrite | last write wins; file never truncated/corrupt (atomic replace) |

> **No game-codec entry needed.** Verified against the code: `FEEDBACK_LIST` and the
> other admin kinds are **not** in the game wire's `KNOWN_KINDS`
> ([mahjong/wire/codec.py](../../mahjong/wire/codec.py)). The admin console runs on its own
> `mahjong-admin-v1` socket whose server (`mahjong/control/server.py`) routes plain
> `json.loads`'d frames straight to `ControlPlane.handle_command` — so `FEEDBACK_UPDATE`
> is just another command kind there, with no codec allow-list or round-trip test. (Earlier
> spec drafts assumed the KNOWN_KINDS rule applied; it doesn't on the admin plane.)

### Integration — control plane (extend [tests/control/test_plane.py](../../tests/control/test_plane.py))

- `FEEDBACK_UPDATE` (valid) → `FEEDBACK_LIST` reply with the new status; `status.json` on disk.
- unknown `filename` → `ERROR feedback_error`; no sidecar change.
- bad `status` → `ERROR`; no sidecar change.
- (token gate already covered by the existing admin-console plane tests.)

### WS round-trip ([tests/control/test_admin_extras_e2e.py](../../tests/control/test_admin_extras_e2e.py))

The admin console has **no** mounted-component (headless-browser) harness — unlike the
player web client (`tests/web/`), its panes are exercised at the WS boundary, the same path
the browser uses. `test_feedback_update_round_trip` opens a real `mahjong-admin-v1` socket,
sends `FEEDBACK_UPDATE`, asserts the returned `FEEDBACK_LIST` carries the new status, and
confirms the sidecar persisted to disk. The `admin.js` `_saveStatus` DOM wiring (reading the
row's `<select>`/inputs) is the only slice past that boundary.

### Browser-verify (owed — manual)

On the deployed console: open Feedback pane, set a report to `implemented`, Save, reload,
confirm it persisted. **Not runnable in the dev macOS sandbox** (degraded sandbox + CDN
import-map), so this is owed against the deploy — tracked on FB-07 in
[feedback-backlog.md](feedback-backlog.md) (`implemented` → `verified` only after this check).

---

## § 30.7 Implementation order (TDD)

1. `FeedbackStatusStore` (load/merge/validate/atomic-write) + unit tests — **test-first**.
2. `FeedbackInbox.list_reports` status merge + `update_status` + its read-back test.
3. `plane.py` `FEEDBACK_UPDATE` handler + integration tests (extend `test_plane.py`).
4. Feedback-pane status controls + UI test.
5. Browser-verify; flip FB-07 to `implemented` in [feedback-backlog.md](feedback-backlog.md).

---

## Open questions

1. **Note sanitisation reuse.** Reuse `sanitise_report_text` (raises on too-short) or a
   thinner allow-list that permits empty notes? Spec assumes the latter for notes; confirm.
2. **Backlog id format.** `^FB-\d{2,}$` — fine, or do we want free-form links (PR #, spec
   path)? Start strict; widen if it chafes.
3. **Auto-archive.** Should `implemented`/`verified`/`wontfix` rows collapse by default in
   the pane? Deferred (§ 30.5).

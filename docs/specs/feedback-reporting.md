# Spec 23 — In-game feedback reporting

A simple in-page form lets players submit bug reports or feature requests. Reports are
stored as plain-text files in `var/reports/` where they can be reviewed and triaged into
the development pipeline.

---

## Goals

- Players can submit a bug report or feature request without leaving the game page.
- Reports are stored locally, one file per submission, in `var/reports/`.
- No special characters reach the filesystem; the sanitisation step is the security
  boundary (prevents path injection, shell metacharacters, HTML).
- The form is unobtrusive — it does not interrupt gameplay or occupy persistent screen
  real estate.
- Only logged-in players can submit; reports are tied to the submitter's `display_name`.

## Non-goals

- No email delivery, webhook, or external service integration.
- No admin UI for reading reports (plain filesystem; just `cat var/reports/*.txt`).
- No markdown, formatting, or file attachments.

---

## § 23.1 WebSocket transport — `FEEDBACK` message

### Why WebSocket, not HTTP POST

The `websockets` library's `process_request` hook parses request headers but does not
read the body (by design — it targets WS handshakes, not HTTP).  Adding a second TCP
listener just for one endpoint is more infrastructure than the feature warrants.  A
`FEEDBACK` WS message kind reuses the already-authenticated connection: the user's
identity is known from `_auth_state`, no separate token passing is needed.

### Authentication

Implicit: the `FEEDBACK` message is only accepted in Phase 1 (post-auth,
pre-attach).  Connections that have not completed `_run_auth_phase` will not reach the
phase-1 dispatch loop.  The orchestrator resolves the submitter via
`self._auth_state.get(conn)["display_name"]`.

### Message schema

#### Client → Server

```json
{ "kind": "FEEDBACK", "type": "bug|feature", "text": "<raw text>" }
```

#### Server → Client (success)

```json
{ "kind": "FEEDBACK_ACK" }
```

#### Server → Client (failure)

Reuses the existing `ERROR` kind:

```json
{ "kind": "ERROR", "code": "feedback_error", "message": "<reason>" }
```

### Sanitisation contract

Applied by the orchestrator before writing to disk (calls `sanitise_report_text` from
`mahjong.wire.feedback`):

1. Require `type ∈ {"bug", "feature"}` (ERROR on anything else).
2. Require `text` is a string (ERROR if missing or non-string).
3. Strip every character outside `[A-Za-z0-9 .,!?'\-\n]` — replace with a space.
4. Collapse runs of whitespace; trim.
5. Enforce `len(sanitised) >= 10` (ERROR "text too short").
6. Truncate to 800 characters (silently).

### Implementation location

`MultiTableOrchestrator._handle_feedback(conn, msg)` in
[mahjong/server/orchestrator.py](../../mahjong/server/orchestrator.py).  Wired into the
phase-1 dispatch loop alongside `LIST_TABLES` / `CREATE_TABLE`:

```python
elif kind == "FEEDBACK":
    await self._handle_feedback(conn, msg)
```

FEEDBACK is intentionally **not** forwarded in phase 2 (attached/spectating).
Mid-game feedback can be added later by intercepting before `table.handle_inbound`.

---

## § 23.2 File storage

### Directory

`var/reports/` — created at server startup if absent (same pattern as `var/mahjong/`).

### Filename format

```text
YYYYMMDD_HHMMSS_<type>.txt
```

Example: `20260601_143022_bug.txt`

If two submissions arrive in the same second, append `_1`, `_2`, etc. (collision loop,
max 10 attempts, then 503).

### File content

```text
type: bug
submitted: 2026-06-01T14:30:22Z
submitter: Alice
---
<sanitised text>
```

Plain text, UTF-8.  The `---` separator makes it easy to `grep` for the body.
`submitter` is the `display_name` from the resolved session token.  No IP address
logged.

---

## § 23.3 UI — feedback button + modal

### Placement

A small `[feedback]` link in the bottom-right corner of the page, always visible,
z-index above the game pane.  Style: same monospace family as the rest of the client,
low-contrast until hovered.

### Interaction

1. Click `[feedback]` → a modal `<dialog>` opens (HTML `<dialog>` element; no JS
   framework needed).
2. Modal contains:
   - `<select>` with two options: `Bug report` / `Feature request`
   - `<textarea rows="5" maxlength="1000">` (client-side length cap mirrors server)
   - `Submit` and `Cancel` buttons
3. `Submit` → `fetch('/report', {method:'POST', headers:{'Authorization':'Bearer '+token, 'Content-Type':'application/json'}, body: JSON.stringify({type, text})})`.
4. On 200: replace modal content with "Thank you! Your feedback was saved." and a
   `Close` button.
5. On 4xx/5xx: show "Something went wrong. Please try again." inline in the modal
   (do not close it so the user can copy their text).
6. `Cancel` or backdrop click → close modal, discard draft.

### Component location

Add a `<feedback-button>` Lit component in
[mahjong/web/static/](../../mahjong/web/static/) (new file `feedback.js`), imported via
the existing import-map in `index.html`.

The component receives the session token as a Lit property (`sessionToken`), set by
the parent app once auth completes (same pattern as other token-aware components).
When `sessionToken` is null/empty the component renders nothing.  It owns the
`<dialog>` and all UI state (draft, submit in-progress, result).  It does **not** own
the WS connection: on submit it dispatches a `feedback-submit` CustomEvent
(`{type, text}`, bubbles+composed) up to `<mahjong-app>`, which sends the `FEEDBACK`
frame.  When the server replies, the app calls back into the component via
`onResult(ok, message)`.

---

## § 23.4 Verification fixtures

All landed and green (791 fast tests).

### Unit: sanitisation ([tests/wire/test_feedback.py](../../tests/wire/test_feedback.py))

| Input text                              | Expected output                                   |
|-----------------------------------------|---------------------------------------------------|
| `"Hello world!"`                        | `"Hello world!"` (unchanged)                      |
| `"bug <script>alert(1)</script>"`       | `"bug script alert 1 script"` (tags stripped)     |
| `"rm -rf /; drop table users"`          | `"rm -rf drop table users"` (`;` `/` stripped)    |
| `"   "`                                 | → `SanitiseError` "text too short"                |
| 2000-char string                        | truncated to 800 chars, no error                  |

### Codec round-trip ([tests/wire/test_codec.py](../../tests/wire/test_codec.py))

- `FEEDBACK` and `FEEDBACK_ACK` added to `ALL_FIXTURES` (KNOWN_KINDS allow-list).

### Integration: WS message ([tests/server/test_feedback_integration.py](../../tests/server/test_feedback_integration.py))

- `FEEDBACK type=bug` after auth → `FEEDBACK_ACK`, file in `data_dir/reports/`.
- `FEEDBACK type=feature` → `FEEDBACK_ACK`, file written.
- `type="complaint"` → `ERROR code=feedback_error`, no file.
- short text → `ERROR code=feedback_error`.
- `FEEDBACK` before auth → `ERROR` (phase-1 unreachable pre-auth).

### Client component ([tests/web/test_feedback_component.py](../../tests/web/test_feedback_component.py))

Real headless Chromium, `<feedback-button>` mounted in isolation:

- no launcher without `sessionToken`; `[feedback]` launcher when logged in,
- dialog shows the type `<select>` (bug/feature) + `<textarea>`,
- valid submit dispatches `feedback-submit` with `{type, text}`,
- short text rejected locally (no event, inline error),
- `onResult(true)` → thank-you; `onResult(false, msg)` → error shown.

### Browser-verify ([tests/web/test_feedback_e2e.py](../../tests/web/test_feedback_e2e.py))

Automated the manual gate: real orchestrator + auth + served client + headless
browser.  Signs in, clicks `[feedback]`, picks "feature", types a suggestion,
submits, observes "Thank you", asserts the sanitised file landed in
`data_dir/reports/` with `type: feature` + `submitter: Alice`.  Negative-control
verified (fails when the expected content is wrong).

---

## Open questions

1. **Should the `var/reports/` path be configurable** (CLI flag, env var)?  Current
   proposal: hardcoded relative to the working directory, same as `var/mahjong/`.
   Fine for a home server; revisit if the deploy path changes.

2. **Do we want a `/reports` admin endpoint** (list or view reports in-browser)?
   Out of scope for now; filesystem access is sufficient.

3. **Should the button be visible only when logged in?**  Yes — the component receives
   the session token as a property; it renders nothing when the token is absent.

---

## Implementation order (all landed)

1. ✅ Sanitisation utility (`mahjong/wire/feedback.py`) + unit tests (TDD).
2. ✅ Codec `FEEDBACK`/`FEEDBACK_ACK`, `MultiTableOrchestrator._handle_feedback`,
   and integration tests.  `data_dir/reports/` created in `orchestrator.start()`.
3. ✅ `<feedback-button>` (`feedback.js`) + `app.js` wiring (import, mount,
   `_onFeedbackSubmit`, `_feedbackResult`, FEEDBACK_ACK/error routing).
4. ✅ Component tests + full browser e2e (browser-verify gate automated).

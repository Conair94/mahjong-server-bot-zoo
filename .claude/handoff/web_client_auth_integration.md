---
title: Web Client Auth Integration (Step 8.5 Wire Protocol)
date: 2026-05-26
from: Claude Haiku 4.5
to: Higher-Context LLM Review
priority: medium
---

## Problem Statement

The mahjong server now requires client authentication before serving game state (Step 8.5 of Layer 8). The web client is stuck at the HELLO message and never sends AUTH_REQUEST, so the connection hangs at "(waiting for ATTACHED snapshot…)".

**Current behavior:**
- Client connects to `ws://127.0.0.1:8400/socket`
- Server sends: `{ kind: "HELLO", ... }`
- Client receives HELLO and logs it to wire log
- **Server then waits for AUTH_REQUEST (never arrives)**
- Client hangs forever

**Root cause:** The web client code in `mahjong/web/static/app.js` was written for Step 7.5 (before auth was wired). It has no AUTH_REQUEST or RESUME logic.

---

## Scope of Work

**Primary task:** Add client-side auth flow to complete the Step 8.5 auth wire.

**Files to modify:**
- `mahjong/web/static/app.js` (primary)
  - Add auth UI (login form with username + password)
  - Add auth message handling (AUTH_REQUEST send, AUTH_RESPONSE receive)
  - Handle auth errors (show banner, allow retry)
- Possibly `mahjong/web/static/style.css` (add form styling)
- Possibly `mahjong/web/static/prompt.js` (if auth prompt needs special rendering)

**Files NOT to modify:** Server-side code is done and tested.

---

## Wire Protocol Specification

Source: `docs/specs/wire-protocol.md` § Client-administrative (AUTH phase)

### Auth Flow (Happy Path)
```
Client connects → Server sends HELLO
Client sends AUTH_REQUEST (username, password_hash)
Server sends AUTH_RESPONSE (account_id, session_token, display_name)
Client proceeds to table discovery / ATTACH
```

### Message Schemas

**HELLO (from server):**
```json
{
  "kind": "HELLO",
  "protocol_version": 1,
  "seq": 3,
  "server_id": "mahjong-server-web"
}
```

**AUTH_REQUEST (from client):**
```json
{
  "kind": "AUTH_REQUEST",
  "username": "alice",
  "password_hash": "...",
  "seq": 4
}
```

**AUTH_RESPONSE (from server):**
```json
{
  "kind": "AUTH_RESPONSE",
  "account_id": 1,
  "session_token": "...",
  "display_name": "alice",
  "seq": 4
}
```

**ERROR (from server, on auth failure):**
```json
{
  "kind": "ERROR",
  "code": "invalid_credentials",
  "message": "username or password incorrect",
  "seq": 4
}
```

### Open Questions (Reviewer: verify these)

1. **Password hashing on the client:** 
   - Claim: Password is likely sent as plaintext over the WebSocket (TLS/Tailscale in production provides transport security).
   - Evidence: No `scrypt` or similar visible in client dependencies.
   - **Verify:** Check `docs/specs/wire-protocol.md` § Authentication for exact field name and format.

2. **Session token storage:**
   - Claim: Session token should be stored in memory for the connection; localStorage is optional for resume-on-reconnect (v2).
   - Evidence: `RESUME` message exists in protocol but client doesn't implement it yet.
   - **Verify:** Check if RESUME is scoped into Step 8.5 or deferred.

3. **Seq numbering:**
   - Claim: `seq` should increment with each message (starting from server's HELLO seq + 1).
   - Evidence: Not visible in current client code; wire log shows server sent seq=3.
   - **Verify:** Is seq managed by ConnectionManager or at a higher layer?

---

## Implementation Sketch (Reviewer: Critique This)

This is my best guess at the control flow. The reviewer should check it against the actual wire protocol and client architecture.

### Phase 0: Auth (new)
After HELLO received, client shows login form (blocking ATTACH).

1. Show login form with username + password inputs (modal or in-pane)
2. On form submit:
   - Get username + password from inputs
   - Construct AUTH_REQUEST: `{ kind: "AUTH_REQUEST", username, password_hash: password, seq: next_seq }`
   - Send via `this._conn.send()`
3. Wait for AUTH_RESPONSE or ERROR:
   - If ERROR: show red banner with message, clear form, let user retry
   - If AUTH_RESPONSE: store session_token, hide form, proceed to Phase 1

### Phase 1: Table Discovery (existing, unchanged)
Client sends LIST_TABLES, CREATE_TABLE, or ATTACH (as before).

### Changes to GamePane (or new AuthPane component)

**Option A (simpler):** Add auth UI to GamePane
- New internal state: `authState` = "waiting" | "entering" | "authed" | "error"
- New internal state: `authError` = null | error message
- New internal state: `sessionToken` = null | token string
- Render conditional: show login form if `authState !== "authed"`, else show game

**Option B (cleaner):** New AuthPane component + state lift
- Create `<auth-pane>` component
- MahjongApp manages `isAuthed` state
- Only show `<table-page>` after auth succeeds
- Cleaner separation but more refactoring

**Reviewer note:** The codebase uses Lit and single-file component definitions. Option A is faster; Option B is more maintainable.

---

## Testing Plan (Reviewer: Check Coverage)

**Manual test (against live server):**
1. Start server: `MAHJONG_LISTEN_ADDR=127.0.0.1:8400 python -m mahjong serve`
2. Create test account: `python -m mahjong account create --username alice --password-stdin <<< testpass123`
3. Open browser: `http://127.0.0.1:8400`
4. Fill login form: username=`alice`, password=`testpass123`
5. **Expected:** Game pane shows ATTACHED snapshot, game board renders
6. **Test auth error:** Try wrong password, verify error banner appears
7. **Test retry:** Fix password, re-submit, verify game loads

**Automated test:** (optional for Step 8.5)
- E2E test using Playwright: login flow → ATTACHED received
- Already in `tests/web/` (may need updates if auth changes the message flow)

---

## Confidence Assessment by Haiku 4.5

**Self-assessed difficulty:** LOW-to-MEDIUM  
**Self-assessed confidence:** 85%

### Why I think this is low-to-medium:
- Wire protocol is simple (just JSON message passing)
- Server-side auth already works (594 tests pass)
- Client is well-structured Lit code
- No new dependencies needed
- No architectural changes to the game engine

### Why I'm not 100% confident (15% uncertainty):
1. **Haven't traced the full client-server handshake end-to-end yet.** I've read fragments of the code but haven't run through the exact sequence locally.
2. **Seq number management unclear.** The ConnectionManager receives HELLO with seq=3, but I haven't verified what seq the client should use in AUTH_REQUEST or how it increments.
3. **Auth prompt placement uncertain.** The best UX (modal login form vs. in-game overlay) depends on the existing component lifecycle, which I haven't fully analyzed.
4. **Session token persistence unclear.** Whether to store it, where, and when to use it for RESUME is not spelled out in my analysis.

### Was handoff needed?
**Honest answer: Probably not.** I could likely implement this myself with ~90 minutes of work:
- 30 min: Read the wire protocol spec fully
- 30 min: Trace client-server flow and identify exact insertion points
- 30 min: Code the auth UI + message handlers
- 30 min: Test and debug

**Reasons to hand off anyway (why the user asked):**
- Get a fresh pair of eyes on the problem (I might have missed something)
- Validate my complexity assessment (am I overconfident? underconfident?)
- Ensure the implementation is idiomatic for this project (Lit patterns, code style)

---

## For the Reviewer

Please assess:

1. **Is my wire protocol understanding correct?**
   - Read `docs/specs/wire-protocol.md` and `mahjong/persistence/auth.py`
   - Do HELLO → AUTH_REQUEST → AUTH_RESPONSE actually follow the flow I described?
   - Is the JSON schema right (field names, types)?

2. **Is my complexity assessment accurate?**
   - This should be 1–2 hours of work, not 1 day, right?
   - Are there any architectural surprises I missed?
   - Is there existing client-side auth code I should reuse?

3. **Was handoff actually necessary?**
   - Could Haiku 4.5 have done this without help?
   - Or are there gotchas that require higher context?
   - (Be honest; "handoff for review" is fine; "unnecessary distraction" is also fine.)

4. **Is my implementation sketch reasonable?**
   - Option A (add auth state to GamePane) vs Option B (new AuthPane) — which fits the codebase better?
   - Any other patterns I should follow?

5. **What are the actual failure modes?**
   - If I got the seq numbering wrong, what breaks?
   - If password format is wrong, what's the server error message?
   - Can these be caught in testing before shipping?

---

## Acceptance Criteria

The fix is done when:
- [ ] Client connects, receives HELLO
- [ ] Client shows login form
- [ ] User enters `alice` / `testpass123`, submits
- [ ] Client sends AUTH_REQUEST with correct schema
- [ ] Server responds with AUTH_RESPONSE
- [ ] Client receives ATTACHED snapshot
- [ ] Game board renders (tiles visible, playable)
- [ ] Wire log shows complete frame sequence (HELLO → AUTH_REQUEST → AUTH_RESPONSE → ATTACHED)
- [ ] Wrong password shows error banner, user can retry
- [ ] No console errors

---

## Deliverable

A single commit that:
- Adds auth UI and message handling to the web client
- Passes all existing tests (no regressions)
- Enables a human to play one complete hand via the browser

No additional Layer 8 work in this commit; that's for the next pass.

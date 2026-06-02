# Spec 24 — Public-internet deployment

Opens the server to players on the public internet. This is a **threat-model change**, not
a config flag: [auth.md](auth.md) was written for a friends-and-family LAN/Tailscale
deployment and explicitly deferred the three things public exposure now requires —
TLS, auth-targeted rate limiting, and over-the-wire account creation. This spec pins all
three, plus the ingress mechanism (Cloudflare Tunnel) and the trusted-proxy client-IP
handling that ties them together.

**Status:** draft. Supersedes auth.md's "no public-internet exposure" assumption
([auth.md § Goals](auth.md), bullet 2) and resolves its deferred S7 anti-abuse item
([auth.md § Non-goals](auth.md), "Not anti-abuse").

Decisions locked with the user 2026-06-02:

- **Registration:** invite-code gated (not open signup, not admin-only).
- **Ingress / TLS:** Cloudflare Tunnel (outbound-only; origin IP hidden; TLS + DDoS at
  the edge).

---

## Goals

- A stranger with an **invite code** can create an account over the wire and start
  playing, without an admin touching the CLI.
- **All credentials travel encrypted.** The app keeps speaking plain `ws://`/`http://`
  bound to loopback; TLS terminates at Cloudflare's edge. No app-level cert handling.
- **Brute-force and credential-stuffing are throttled per real client IP**, surviving
  reconnects (today's 3-attempts-per-connection limit does not — an attacker just
  reconnects).
- **The home origin IP is never exposed.** No inbound port-forward; `cloudflared` dials
  out to Cloudflare. The router needs no changes.
- **The deploy is reproducible** from a runbook on the Linux host (per
  [project_hosting_target.md](../../.claude/projects/-Users-connorlockhart-Documents-GitHub-mahjong-server-bot-zoo/memory/project_hosting_target.md)),
  not a pile of remembered shell commands.

## Non-goals

- **No password reset / email flow.** Still no email in the system. A forgotten password
  is an admin DB edit, same as auth.md v1. (Reconsider once real users complain.)
- **No CAPTCHA.** The invite gate is the human-check; a code is required to register, so
  we don't need a bot-detection challenge in v1.
- **No MFA.** Out of scope, additive later.
- **No app-managed TLS certificates.** Cloudflare owns the cert. We do not run Caddy/nginx
  or store any private key on the box. (If we ever drop Cloudflare, that's a new spec.)
- **No per-account moderation UI.** Disabling an abusive account is `accounts.disabled = 1`
  via the existing CLI. A ban/report-review UI is future work.
- **No distributed rate-limit store.** The limiter is in-process and resets on restart.
  Fine for one home box; a Redis-backed limiter is a multi-process concern we don't have.

---

## Revised threat model

auth.md defended against "someone on the LAN trying to log in as Alice." Public exposure
adds internet-scale adversaries. What changes:

| Threat | auth.md (LAN) | Spec 24 (public) |
| --- | --- | --- |
| Online password guessing | Connection-wide 3-attempt cap | **IP-keyed sliding-window limiter** (§24.3), surviving reconnects |
| Credential stuffing (breached password lists) | Out of scope | Same IP limiter; argon2 cost already makes each attempt ~250ms on the Pi |
| Credentials sniffed in transit | "Trusted link (Tailscale/localhost)" | **TLS at Cloudflare edge** (§24.1); origin link is loopback only |
| Origin host targeted directly (DDoS, port scan) | N/A (not exposed) | Origin IP hidden behind tunnel; only `cloudflared` egress exists |
| Mass account creation / spam signups | N/A (CLI only) | **Invite-code gate** (§24.2) + IP limiter on `REGISTER` |
| Spoofed client IP to dodge the limiter | N/A | `CF-Connecting-IP` trusted **only** from a loopback peer (§24.1); a remote peer can't reach the listener at all |

Still explicitly *not* defended (unchanged from auth.md): phishing, a compromised host
(root on the box reads everything), and admin-key compromise. Adding public exposure does
not change those — it changes the network adversary, not the host adversary.

---

## § 24.1 Ingress & TLS — Cloudflare Tunnel

### Topology

```text
player browser ──HTTPS/wss──▶ Cloudflare edge ──encrypted tunnel──▶ cloudflared ──HTTP/ws──▶ 127.0.0.1:8400
                              (TLS terminates here)                  (on the box)            (mahjong serve, loopback)
```

`cloudflared` runs as a service on the Linux host, holds an outbound tunnel to Cloudflare,
and proxies requests to the loopback listener. The mahjong process is **never** bound to a
public interface — it stays on `127.0.0.1`, which is also the default
([config.py:132](../../mahjong/server/config.py)). The only thing reachable from the
internet is Cloudflare's edge, which forwards over the tunnel.

*Why a tunnel and not `0.0.0.0` + port-forward:* an outbound tunnel needs no inbound
firewall hole, never reveals the home IP (so the origin can't be DDoSed or port-scanned
directly), and gets TLS + edge DDoS mitigation for free. The trade is a dependency on
Cloudflare. Alternatives weighed in § Alternatives.

### App-side change: trust the proxy's client IP

Behind the tunnel, every TCP connection to the listener originates from `cloudflared` on
loopback. So `connection.remote_address` is always `127.0.0.1` — useless for rate-limiting,
which must key on the *real* client. Cloudflare injects the real client IP as the
`CF-Connecting-IP` request header on the WebSocket upgrade.

The handshake hook in [mahjong/wire/server.py](../../mahjong/wire/server.py) extracts it:

```python
def _client_ip(request_headers, peer_address) -> str:
    # Trust CF-Connecting-IP ONLY when the TCP peer is loopback — i.e. the
    # request actually came through our local cloudflared. A direct (non-tunnel)
    # connection from a remote peer can't set this; and it can't even reach a
    # loopback-bound listener. Defense in depth: bind loopback AND check peer.
    if peer_address[0] in ("127.0.0.1", "::1"):
        forwarded = request_headers.get("CF-Connecting-IP")
        if forwarded:
            return forwarded.strip()
    return peer_address[0]
```

The resolved IP is stored on the connection (new `Connection.client_ip` attribute, or a
side map keyed by `id(conn)` if we keep `Connection` immutable) so the auth phase and the
rate limiter can read it. **This is the load-bearing seam:** get it wrong and you either
throttle every player as one IP (`127.0.0.1`) or trust a spoofable header. The
loopback-peer check is what makes the header trustworthy — a remote attacker cannot
present a loopback peer address to a loopback-bound listener.

*Config:* a new `MAHJONG_TRUST_PROXY` bool (default `false`). When false, `_client_ip`
ignores `CF-Connecting-IP` and always returns the peer — preserves today's behavior for
local/LAN/Tailscale runs and for tests. The deploy unit sets it `true`.

---

## § 24.2 Invite-code registration

### Schema — new `invites` table

Extends [sqlite-schema.md](sqlite-schema.md) (additive migration):

```sql
CREATE TABLE invites (
    code          TEXT PRIMARY KEY,         -- the invite string, e.g. "inv_a1b2c3d4"
    created_by    INTEGER NOT NULL,         -- accounts.account_id of the admin who minted it
    created_at_ms INTEGER NOT NULL,
    expires_at_ms INTEGER,                  -- NULL = never expires
    max_uses      INTEGER NOT NULL DEFAULT 1,
    used_count    INTEGER NOT NULL DEFAULT 0,
    disabled      INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (created_by) REFERENCES accounts(account_id)
);
```

A code is **redeemable** iff `disabled = 0 AND used_count < max_uses AND
(expires_at_ms IS NULL OR expires_at_ms > now)`. Redemption increments `used_count` in the
**same transaction** as the account INSERT, so two simultaneous redemptions of a
single-use code can't both win (the second sees `used_count = max_uses` and fails). This is
the standard "guard the increment inside the write transaction" pattern — without it the
check-then-act has a race.

Code format: `inv_` + 16 hex chars (`secrets.token_hex(8)`, 64 bits). Opaque; the prefix is
debugging sugar only, same convention as session tokens.

### Wire — `REGISTER` message

A third message accepted in the auth phase, alongside `AUTH_REQUEST` / `RESUME`
([orchestrator.py:283](../../mahjong/server/orchestrator.py)). On success it auto-issues a
session, so a new user is logged in immediately — no separate login round-trip.

#### Client → Server

```json
{
  "kind": "REGISTER",
  "username": "alice",
  "password": "<plaintext over TLS>",
  "display_name": "Alice",
  "invite_code": "inv_a1b2c3d4e5f60718"
}
```

#### Server → Client (success) — reuses `AUTH_RESPONSE`

```json
{
  "kind": "AUTH_RESPONSE",
  "seq": 7,
  "ok": true,
  "user_id": "u_42",
  "display_name": "Alice",
  "session_token": "s_…",
  "expires_at_ms": 1769990400000
}
```

Reusing `AUTH_RESPONSE` (not a new `REGISTER_RESPONSE`) keeps the client's auth-handling
branch singular: register and login converge on "I now hold a session token." The client
just sends `REGISTER` instead of `AUTH_REQUEST` on the signup path.

#### Server → Client (failure) — reuses `ERROR`

```json
{ "kind": "ERROR", "code": "register_rejected", "message": "<reason>" }
```

The `message` is **deliberately generic** for the invite path ("invalid or used invite
code") — we don't distinguish "no such code" from "already used" from "expired", to avoid
turning the endpoint into an invite-code oracle. Username-taken *is* surfaced specifically
(`"username already taken"`) because the user needs to pick another and it leaks nothing an
attacker couldn't learn by trying to log in.

### Validation order (server)

In [mahjong/persistence/auth.py](../../mahjong/persistence/auth.py), new
`handle_register(conn, username, password, display_name, invite_code) -> AuthResult`:

1. Validate `username`: 3–32 chars, `^[a-zA-Z0-9_-]+$`. (Same rule as the CLI,
   [auth.md § Account creation](auth.md).) Reject → generic.
2. Validate `password`: ≥ 8 chars. Reject → generic.
3. Sanitise `display_name`: reuse `sanitise_report_text`-style allow-list, ≤ 32 chars,
   fall back to `username` if empty after sanitising. *(Prevents control chars / HTML in a
   name that later renders in the lobby and in records.)*
4. `BEGIN IMMEDIATE` transaction:
   a. Re-check the code is redeemable (`SELECT … FOR UPDATE` semantics via the immediate
      txn lock).
   b. Case-insensitive username uniqueness check.
   c. `create_account(...)` (existing helper — argon2id hash).
   d. `UPDATE invites SET used_count = used_count + 1 WHERE code = ?`.
   e. COMMIT.
5. Issue a session for the new account; return `AuthResult(ok=True, …)`.

Any failure rolls back — no partial account, no consumed invite. Runs in the executor like
the other auth calls (sync DB +
[run_in_executor](../../.claude/projects/-Users-connorlockhart-Documents-GitHub-mahjong-server-bot-zoo/memory/feedback_sync_db_run_in_executor.md)).

### CLI — minting invites

New `account invite` subcommands ([mahjong/cli/account.py](../../mahjong/cli/account.py)):

```text
$ python -m mahjong account invite create --max-uses 1 --expires-days 7
created invite code=inv_a1b2c3d4e5f60718 max_uses=1 expires=2026-06-09T…Z

$ python -m mahjong account invite list      # code, used/max, expiry, disabled
$ python -m mahjong account invite revoke inv_a1b2c3d4e5f60718   # sets disabled=1
```

Admin role required to mint (the CLI runs server-side as the admin, so this is implicit —
no role check needed in the CLI path).

### UI — registration form

The existing login component
([mahjong/web/static/](../../mahjong/web/static/)) gains a "Need an account? Register"
toggle that swaps the form to username / display-name / password / **invite code** fields
and sends `REGISTER` instead of `AUTH_REQUEST`. On `AUTH_RESPONSE { ok: true }` the client
stores the token and proceeds exactly as after login. On `ERROR { code:
"register_rejected" }` it shows `message` inline without clearing the form. No new
component file is mandatory; this is a mode toggle on the existing login view.

---

## § 24.3 Auth-targeted rate limiting

### Limiter

An in-process **sliding-window counter** keyed by client IP (from §24.1). New module
`mahjong/server/ratelimit.py`:

```python
class SlidingWindowLimiter:
    def __init__(self, *, max_events: int, window_s: float) -> None: ...
    def check(self, key: str) -> bool:
        """True if this event is allowed (and records it); False if the key is
        over budget in the trailing window."""
```

Implementation: per-key deque of event timestamps; on `check`, pop timestamps older than
`window_s`, compare `len` to `max_events`, append + return True if under. A periodic sweep
(or lazy eviction on access) drops idle keys so the dict doesn't grow without bound.

*Why in-process and not Redis:* one home box, one process. A network round-trip per login
check would be slower than the argon2 verify it's protecting. YAGNI on the distributed
store until there's a second process. (Cost of restart: the window resets — acceptable; an
attacker gains at most one fresh window per server restart, and restarts are rare.)

### Where it applies

| Surface | Budget | Key | On exceed |
| --- | --- | --- | --- |
| `AUTH_REQUEST` **failures** | 10 / IP / hour | client IP | `ERROR { code: "rate_limited" }`, no argon2 verify run |
| `REGISTER` attempts | 5 / IP / hour | client IP | `ERROR { code: "rate_limited" }` |
| `FEEDBACK` (reinstate) | 5 / IP / hour | client IP | `ERROR { code: "rate_limited" }` (see §24.4) |

Only **failed** `AUTH_REQUEST`s count against the login budget — a user legitimately
reconnecting many times (flaky mobile network) with a valid token/password isn't
penalised. A hit on the limit short-circuits *before* the argon2 verify, so the limiter
also sheds the CPU-DoS angle (each verify is ~250ms on the Pi; 10/hour caps the cost an
unauthenticated IP can impose).

The connection-wide 3-attempt cap ([orchestrator.py:282](../../mahjong/server/orchestrator.py))
**stays** — it's the per-connection backstop; the IP limiter is the cross-connection one.
They compose.

---

## § 24.4 Other abuse surfaces

- **Feedback rate-limit (reinstate).** Spec 23 removed the in-memory feedback limiter when
  the endpoint became auth-only on a private server. Public exposure brings it back, now
  keyed on client IP via the shared limiter (§24.3), 5/IP/hour. *(An authenticated but
  abusive user can still be disabled via the CLI.)*
- **Connection cap per IP.** A `max_connections_per_ip` (default 20) guard in the accept
  path prevents a single IP from exhausting the connection table. Cheap; additive.
- **Message-size cap.** The `websockets` library's `max_size` (default 1 MiB) already
  bounds frame size — confirm it's not raised. No oversized-payload memory blow-up.

These are small; they ride along with §24.3's limiter rather than warranting their own
spec sections of design.

---

## § 24.5 Deployment runbook (Linux host)

Lives at `docs/ops/cloudflare-tunnel.md` (new). **Two phases — validate free, then pay for
a stable address.** The application code and config are identical across both; only the
`cloudflared` invocation differs.

### Cost

Cloudflare account, Tunnel, edge TLS, WebSockets, and DDoS mitigation are all **free** at
our scale (Zero Trust free tier covers ≤ 50 users; we have a handful). The **only** real
cost is a **domain name (~$10/yr)**, and it is needed *only* for Phase B's stable URL.
Phase A is $0 and needs neither an account nor a domain.

### Phase A — quick tunnel (free, ephemeral; the walking-skeleton deploy)

```bash
cloudflared tunnel --url http://127.0.0.1:8400
```

Prints a public `https://<random>.trycloudflare.com` URL with full TLS + WebSockets, **no
account, no domain, no money.** Use it to prove the whole public path end-to-end —
register-with-invite, TLS, `CF-Connecting-IP` rate-limiting, a friend connecting off-network
— before committing to a domain. The URL **changes on every restart** and Cloudflare marks
it non-production, so it's a validation tool, not the final home.

Run `mahjong serve` with `MAHJONG_TRUST_PROXY=1` (quick tunnels still inject
`CF-Connecting-IP`, and the listener is still loopback, so the §24.1 peer check holds).

### Phase B — named tunnel (~$10/yr domain; stable shareable URL)

Once Phase A validates the architecture and you want a permanent address:

1. Register/transfer a domain into a Cloudflare zone (at-cost via Cloudflare, ~$10–12/yr).
2. Install `cloudflared`; `cloudflared tunnel login` (one-time browser auth).
3. `cloudflared tunnel create mahjong` → writes a credentials JSON; note the tunnel UUID.
4. DNS: `cloudflared tunnel route dns mahjong play.<yourdomain>` (CNAME to the tunnel).
5. Tunnel config `~/.cloudflared/config.yml`:
   ```yaml
   tunnel: <UUID>
   credentials-file: /home/mahjong/.cloudflared/<UUID>.json
   ingress:
     - hostname: play.<yourdomain>
       service: http://127.0.0.1:8400
     - service: http_status:404
   ```
6. `systemd` units (both `Restart=on-failure`, `After=network-online.target`):
   - `mahjong.service` — `python -m mahjong serve` with `MAHJONG_TRUST_PROXY=1`,
     `MAHJONG_LISTEN_ADDR=127.0.0.1:8400`, data-dir on persistent storage.
   - `cloudflared.service` — `cloudflared tunnel run mahjong`.
7. Verify off-network: load `https://play.<yourdomain>/`, register with an invite, play a
   hand.

Cloudflare dashboard: enable WebSockets (Network tab — on by default for Free), and
optionally a WAF rate-limit rule on `/` as an edge-side backstop to §24.3.

*Origin-hiding caveat:* Phase A's quick tunnel hides the origin just like Phase B. The
full benefit (plus a stable URL friends can bookmark) is Phase B; the only thing $10 buys
is the permanent name, not the security.

---

## Alternatives considered

- **Open self-serve signup.** Simplest "anyone can join", but the largest abuse surface
  (spam accounts, no human gate) — would force CAPTCHA + heavier moderation. Invite codes
  give 90% of the openness with a fraction of the abuse risk for a home server. Rejected
  for v1; an invite is cheap to hand out.
- **Caddy + router port-forward + DDNS.** Most self-reliant, auto Let's Encrypt TLS, no
  third-party dependency. Rejected because it exposes the home IP (DDoS / scan target) and
  needs router config; the tunnel hides the origin and needs no inbound hole. Caddy stays
  the fallback if we ever leave Cloudflare.
- **Tailscale Funnel.** Reuses the existing Tailscale plan, TLS included, no port-forward.
  Rejected: Funnel targets light/occasional sharing and carries bandwidth/availability
  caveats for a persistent public service; Cloudflare's edge is built for always-on public
  traffic.
- **`X-Forwarded-For` instead of `CF-Connecting-IP`.** XFF is a comma-list that any
  upstream can append to; `CF-Connecting-IP` is a single value Cloudflare sets and
  overwrites. With the loopback-peer check either is safe, but the single-value header is
  less error-prone. Use `CF-Connecting-IP`; it's Cloudflare-specific and we're on
  Cloudflare.
- **Persisting the rate-limiter to SQLite.** Survives restart, but adds a write per login
  attempt and a schema. The in-memory window is enough at this scale; revisit if restarts
  become an attack vector (they aren't — you'd need to crash the server every hour).
- **Token rotation / shorter session lifetime for public.** auth.md deferred rotation for
  multi-tab reasons; public exposure doesn't change that calculus (tokens are 128-bit,
  TLS-protected in transit, revocable via disable). Out of scope here.

---

## Verification fixtures

### Unit — client-IP extraction (§24.1)

1. Loopback peer + `CF-Connecting-IP: 203.0.113.7` + trust_proxy on → returns
   `"203.0.113.7"`.
2. Loopback peer + header, **trust_proxy off** → returns `"127.0.0.1"` (header ignored).
3. **Non-loopback** peer + `CF-Connecting-IP` header + trust_proxy on → returns the peer,
   *not* the header (spoof defense).
4. Loopback peer, no header → returns `"127.0.0.1"`.

### Unit — sliding-window limiter (§24.3)

5. `max_events=3, window_s=10`: 3 `check`s on one key return True, 4th returns False.
6. After advancing a fake clock past `window_s`, the key's budget refreshes (True again).
7. Distinct keys have independent budgets.
8. Idle keys are evicted (dict size returns to 0 after a sweep past the window).

### Unit / integration — invite registration (§24.2)

9. Mint a `max_uses=1` invite; `handle_register` with it + a fresh username → `ok=True`,
   account row created, session issued, `used_count = 1`.
10. Re-using the now-spent invite → `ok=False`, generic `register_rejected`, **no** second
    account.
11. Expired invite (`expires_at_ms = now-1`) → rejected, no account.
12. Disabled invite → rejected.
13. Duplicate username (case-insensitive `Alice` vs existing `alice`) → rejected with
    `"username already taken"`; the invite is **not** consumed.
14. Concurrent redemption of a single-use code (two `handle_register` in parallel against a
    shared connection/threadpool): exactly one succeeds, `used_count` ends at 1. *(The
    load-bearing race test — pins the in-transaction increment.)*
15. `display_name` with control chars / HTML is sanitised before storage.

### Codec round-trip (§24.2)

16. `REGISTER` added to `ALL_FIXTURES` /
    [KNOWN_KINDS allow-list](../../.claude/projects/-Users-connorlockhart-Documents-GitHub-mahjong-server-bot-zoo/memory/feedback_wire_codec_known_kinds.md);
    round-trips without the decoder dropping the connection. (`AUTH_RESPONSE` / `ERROR`
    already covered.)

### Integration — limiter wired to auth (§24.3)

17. 10 failed `AUTH_REQUEST`s from one client IP → 11th returns `ERROR rate_limited`
    **without** running an argon2 verify (assert via a verify-call spy / timing).
18. Successful logins do **not** consume the failure budget (20 good logins, no throttle).
19. Two different client IPs each get their own budget.
20. `REGISTER` over its 5/hour budget → `rate_limited`.

### Browser-verify (§24.2 UI) — Playwright async

21. Real orchestrator + served client: toggle to Register, submit username / display /
    password / invite, observe auto-login into the lobby; assert the account + spent invite
    landed in the DB. Negative control: a bad invite shows the inline error and does **not**
    log in. (Playwright **async** API only —
    [memory](../../.claude/projects/-Users-connorlockhart-Documents-GitHub-mahjong-server-bot-zoo/memory/feedback_playwright_async_only.md).)

### Deploy smoke (§24.5) — manual, documented not automated

22. From off-network, `https://play.<domain>/` loads over TLS, register-with-invite works,
    a full hand plays. Recorded as a checklist tick, not a CI gate (needs real DNS +
    tunnel).

---

## Open questions

1. ~~**Domain name.**~~ **Resolved 2026-06-02:** user has no domain and wants to avoid
   cost. Deploy is two-phase (§24.5) — **Phase A** uses a free, account-less, ephemeral
   `trycloudflare.com` quick tunnel to validate the public path; **Phase B** (a ~$10/yr
   Cloudflare-zone domain + named tunnel) is deferred until a stable shareable URL is
   wanted. App code is identical across both.
2. ~~**Invite default lifetime.**~~ **Resolved 2026-06-02:** single-use, 7-day expiry by
   default; overridable per-mint (`--max-uses`, `--expires-days`).
3. **Should `REGISTER` also be allowed mid-session** (a logged-in admin minting + handing
   out codes in-app)? Proposal: no — minting stays CLI-only in v1; registration is a
   pre-auth wire message only. Additive later.
4. **Public abuse logging.** Do we log throttled IPs / rejected invites to a file for
   post-mortem? Proposal: log at WARNING (no PII beyond the IP, which Cloudflare already
   sees); no separate store.

---

## Implementation order (walking skeleton first)

Build the end-to-end "stranger registers and plays" slice before hardening, so integration
bugs surface early — then layer the abuse defenses, then deploy.

1. **Schema + invite persistence** (TDD): `invites` migration; `mint_invite` /
   `redeem_invite` helpers with the in-transaction increment; fixtures 9–14.
2. **`REGISTER` wire + `handle_register`** (TDD): codec entry (16), auth-phase dispatch,
   orchestrator wiring; auto-login on success. Fixtures 9–13, 15.
3. **Registration UI**: login-view toggle + `REGISTER` send; browser-verify (21).
4. **Client-IP plumbing** (TDD): `_client_ip` + `MAHJONG_TRUST_PROXY` + thread onto the
   connection; fixtures 1–4. *(No behavior change yet — just makes the IP available.)*
5. **Rate limiter** (TDD): `SlidingWindowLimiter` (fixtures 5–8), then wire to
   `AUTH_REQUEST` failures + `REGISTER` + reinstate `FEEDBACK` (fixtures 17–20). Connection
   cap + confirm `max_size`.
6. **Deploy**: write `docs/ops/cloudflare-tunnel.md`; stand up `cloudflared` + systemd on
   the Linux box; off-network smoke (22). Flip `MAHJONG_TRUST_PROXY=1` only here.

Steps 1–5 are all runnable and testable on macOS/localhost with `trust_proxy=false`. Step
6 is the only Linux-host-specific piece and the only one that touches the public internet —
it lands last, after everything it fronts is verified.

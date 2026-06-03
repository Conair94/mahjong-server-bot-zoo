# Deploy runbook — public hosting via Cloudflare Tunnel

Spec: [public-deployment.md § 24.5](../specs/public-deployment.md). This is the operator
runbook for exposing the mahjong server to the public internet. **Two phases:** validate
the whole public path for **$0** with an ephemeral quick tunnel (Phase A), then — only if
you want a permanent shareable URL — spend ~$10/yr on a domain and switch to a named
tunnel (Phase B). The application is identical across both; only the `cloudflared`
invocation changes.

> **Cost.** Cloudflare account, Tunnel, edge TLS, WebSockets, and DDoS mitigation are all
> free at our scale. The *only* real cost is a domain name (~$10/yr), needed only for
> Phase B's stable URL. Phase A needs neither an account nor a domain.

> **Security posture.** The mahjong process always binds loopback (`127.0.0.1`); it is
> never exposed on a public interface. `cloudflared` dials *out* to Cloudflare, so there
> is no inbound firewall hole and the home origin IP stays hidden. Set
> `MAHJONG_TRUST_PROXY=1` only when behind the tunnel — it tells the server to read the
> real client IP from `CF-Connecting-IP` (trusted only from the loopback `cloudflared`
> peer; see [§24.1](../specs/public-deployment.md)).

---

## 0. Prerequisites (the operator side — run these first, locally)

These are the same on macOS dev and the Linux host, and are independent of the tunnel.
Configure the data dir and create the first admin + at least one invite to hand out.

```bash
export MAHJONG_DATA_DIR=/home/mahjong/data      # persistent storage on the host

# First admin account (password read from stdin; never in shell history):
echo 'your-strong-admin-password' | \
  python -m mahjong account create --username root --display "Root" --admin --password-stdin

# Mint an invite to give to a player (single-use, 7-day expiry by default):
python -m mahjong account invite create
#   → created invite code=inv_xxxxxxxxxxxxxxxx max_uses=1 expires=2026-06-10T...Z

# Other invite operations:
python -m mahjong account invite create --max-uses 5 --expires-days 30   # a reusable code
python -m mahjong account invite list                                    # who/used/expiry
python -m mahjong account invite revoke inv_xxxxxxxxxxxxxxxx              # disable one
```

A player registers in the browser with the code (the "Register" toggle on the login form).
Without a minted invite, nobody can sign up — that is the gate.

---

## Phase A — quick tunnel (free, ephemeral; the walking-skeleton deploy)

Validates the **entire public path** — registration, TLS, `CF-Connecting-IP`
rate-limiting, a friend connecting off-network — with **no account, no domain, no money**.
The URL changes on every restart and Cloudflare marks it non-production, so it is a
validation tool, not the final home.

1. Install `cloudflared` (`brew install cloudflared`, or the Linux package from Cloudflare).

2. Start the origin (loopback, proxy-trust on):

   ```bash
   MAHJONG_DATA_DIR=/home/mahjong/data \
   MAHJONG_LISTEN_ADDR=127.0.0.1:8400 \
   MAHJONG_TRUST_PROXY=1 \
     python -m mahjong serve
   # → mahjong server listening on ws://127.0.0.1:8400
   #   web client:           http://127.0.0.1:8400/
   ```

3. In another shell, open the quick tunnel to it:

   ```bash
   cloudflared tunnel --url http://127.0.0.1:8400
   # → prints https://<random-words>.trycloudflare.com  (full TLS + WebSockets)
   ```

4. From a device **off your home network** (phone on cellular), open the printed
   `https://…trycloudflare.com/` URL, click **Register**, enter the invite code, and play
   a hand. That round-trip validates everything Phase B will rely on.

---

## Phase B — named tunnel (~$10/yr domain; stable shareable URL)

Once Phase A proves the architecture and you want a permanent address:

1. Register/transfer a domain into a Cloudflare zone (at-cost via Cloudflare, ~$10–12/yr).
2. `cloudflared tunnel login` (one-time browser auth to the Cloudflare account).
3. `cloudflared tunnel create mahjong` → writes a credentials JSON; note the tunnel UUID.
4. `cloudflared tunnel route dns mahjong play.<yourdomain>` (CNAME to the tunnel).
5. Tunnel config `~/.cloudflared/config.yml`:

   ```yaml
   tunnel: <UUID>
   credentials-file: /home/mahjong/.cloudflared/<UUID>.json
   ingress:
     - hostname: play.<yourdomain>
       service: http://127.0.0.1:8400
     - service: http_status:404
   ```

6. Run as services (see systemd units below).
7. Verify off-network: open `https://play.<yourdomain>/`, register with an invite, play.

Cloudflare dashboard: WebSockets are on by default (Network tab); optionally add a WAF
rate-limit rule on `/` as an edge-side backstop to the app's own limiter
([§24.3](../specs/public-deployment.md)).

---

## systemd units (Linux host, Phase B)

`/etc/systemd/system/mahjong.service`:

```ini
[Unit]
Description=mahjong server
After=network-online.target
Wants=network-online.target

[Service]
User=mahjong
Environment=MAHJONG_DATA_DIR=/home/mahjong/data
Environment=MAHJONG_LISTEN_ADDR=127.0.0.1:8400
Environment=MAHJONG_TRUST_PROXY=1
ExecStart=/home/mahjong/venv/bin/python -m mahjong serve
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

`/etc/systemd/system/cloudflared.service`:

```ini
[Unit]
Description=cloudflared tunnel for mahjong
After=network-online.target
Wants=network-online.target

[Service]
User=mahjong
ExecStart=/usr/bin/cloudflared tunnel run mahjong
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now mahjong.service cloudflared.service
journalctl -u mahjong -f      # watch server logs
```

The server drains gracefully on SIGTERM (stops accepting, closes tables, closes the DB),
so `systemctl restart mahjong` is safe between hands.

---

## Verification checklist (the deploy gate)

- [ ] `python -m mahjong account create --admin …` succeeds; `account invite create` prints a code.
- [ ] `python -m mahjong serve` logs `server.ready listen=127.0.0.1:8400` and `GET /` returns the client HTML.
- [ ] Quick tunnel (Phase A) prints a `trycloudflare.com` URL that loads over HTTPS off-network.
- [ ] Register-with-invite from an off-network device lands in the lobby; a second use of a single-use code is refused.
- [ ] (Phase B) `https://play.<yourdomain>/` loads; both systemd units are `active`.

This checklist is the manual deploy gate (spec fixture 22) — it needs real DNS/tunnel and
is not a CI test.

---

## Still open (tracked, not blockers)

- **Per-IP connection cap** — deferred ([§24.4](../specs/public-deployment.md)); Cloudflare's
  edge absorbs raw connection floods in the meantime.
- **`/health` endpoint** — not wired in the pragmatic serve path (returns 503); the tunnel
  fronts `/`, which does not need it. Additive later per server-lifecycle.md.

import { LitElement, html } from "lit";

// Admin control console (Spec 25). One WebSocket (`mahjong-admin-v1`) carries
// commands + STATUS pushes + list replies. Panes are added incrementally; the
// app ignores frame kinds and fields it doesn't recognise (additive evolution).
// Reserved tabs that render a "coming soon" stub pane but are still selectable.
const FUTURE_TABS = [];

// Feedback triage status vocabulary (Spec 30 § 30.1), kept in sync with the server's
// VALID_STATUSES and feedback-backlog.md.
const STATUS_VALUES = ["open", "triaged", "in-progress", "implemented", "verified", "wontfix", "duplicate"];

class AdminApp extends LitElement {
  createRenderRoot() { return this; }

  static properties = {
    status: { state: true },
    connected: { state: true },
    tab: { state: true },
    invites: { state: true },
    accounts: { state: true },
    logs: { state: true },
    reports: { state: true },
  };

  constructor() {
    super();
    this.status = null;
    this.connected = false;
    this.tab = "Status";
    this.invites = [];
    this.accounts = [];
    this.logs = [];
    this.reports = [];
    this._ws = null;
    this._reconnectMs = 500;
  }

  connectedCallback() {
    super.connectedCallback();
    this._connect();
  }

  _connect() {
    let ws;
    try {
      ws = new WebSocket(`ws://${location.host}/`, "mahjong-admin-v1");
    } catch (e) {
      this._scheduleReconnect();
      return;
    }
    this._ws = ws;
    ws.onopen = () => { this.connected = true; this._reconnectMs = 500; };
    ws.onclose = () => { this.connected = false; this._scheduleReconnect(); };
    ws.onerror = () => { try { ws.close(); } catch (e) {} };
    ws.onmessage = (ev) => {
      let msg;
      try { msg = JSON.parse(ev.data); } catch (e) { return; }
      if (msg.kind === "STATUS") this.status = msg;
      else if (msg.kind === "INVITE_LIST") this.invites = msg.invites ?? [];
      else if (msg.kind === "ACCOUNT_LIST") this.accounts = msg.accounts ?? [];
      else if (msg.kind === "LOG_BATCH") this._appendLogs(msg.lines ?? []);
      else if (msg.kind === "FEEDBACK_LIST") this.reports = msg.reports ?? [];
      else if (msg.kind === "ERROR") this._flash(msg.message || msg.code);
    };
  }

  _scheduleReconnect() {
    setTimeout(() => this._connect(), this._reconnectMs);
    this._reconnectMs = Math.min(this._reconnectMs * 2, 5000);
  }

  _send(kind) { this._sendMsg({ kind }); }
  _sendMsg(obj) {
    if (this._ws && this._ws.readyState === WebSocket.OPEN) this._ws.send(JSON.stringify(obj));
  }
  _flash(text) { this._err = text; this.requestUpdate(); setTimeout(() => { this._err = null; this.requestUpdate(); }, 4000); }

  _selectTab(tab) {
    this.tab = tab;
    if (tab === "Invites") this._send("INVITES_LIST");
    if (tab === "Accounts") this._send("ACCOUNTS_LIST");
    if (tab === "Logs") { this.logs = []; this._sendMsg({ kind: "LOG_SUBSCRIBE" }); }
    if (tab === "Feedback") this._send("FEEDBACK_LIST");
  }

  _appendLogs(lines) {
    // Keep a bounded client-side window so the DOM doesn't grow unbounded.
    this.logs = [...this.logs, ...lines].slice(-1000);
  }

  render() {
    const s = this.status?.server ?? {};
    const state = s.state ?? "UNKNOWN";
    const running = state === "RUNNING";
    const busy = state === "STARTING" || state === "STOPPING";
    const tabs = ["Status", "Invites", "Accounts", "Logs", "Health", "Tunnel", "Feedback", "Training", ...FUTURE_TABS];
    return html`
      <div class="wrap">
        <header class="bar">
          <span class="title">mahjong <span class="zh">控制</span></span>
          <span class="badge ${state}">${state}</span>
          <span class="conn ${this.connected ? "up" : "down"}">${this.connected ? "● connected" : "○ offline"}</span>
          <span class="spacer"></span>
          ${this._metrics(s)}
        </header>

        <div class="controls">
          <button @click=${() => this._send("SERVER_START")} ?disabled=${running || busy}>▶ Start server</button>
          <button class="danger" @click=${() => this._send("SERVER_STOP")} ?disabled=${state === "STOPPED" || state === "CRASHED" || busy}>■ Stop</button>
          <button @click=${() => this._send("SERVER_RESTART")} ?disabled=${!running}>⟳ Restart</button>
          ${this._err ? html`<span style="color:var(--bad)">${this._err}</span>` : ""}
        </div>

        <div class="tabs">
          ${tabs.map((t) => {
            const future = FUTURE_TABS.includes(t);
            return html`<span class="tab ${this.tab === t ? "active" : ""} ${future ? "disabled" : ""}"
              @click=${() => (future ? null : this._selectTab(t))} title=${future ? "coming soon" : ""}>${t}</span>`;
          })}
        </div>

        ${this._pane(s)}
      </div>
    `;
  }

  _pane(s) {
    if (this.tab === "Invites") return this._invitesPane();
    if (this.tab === "Accounts") return this._accountsPane();
    if (this.tab === "Logs") return this._logsPane();
    if (this.tab === "Health") return this._healthPane();
    if (this.tab === "Tunnel") return this._tunnelPane();
    if (this.tab === "Feedback") return this._feedbackPane();
    if (this.tab === "Training") return this._trainingPane();
    return this._statusPane(s);
  }

  _trainingPane() {
    // Reserved slot (Spec 25 § Non-goals): the bot-zoo training dashboard lands
    // here once the RL harness is wired to the live server. No controls yet.
    return html`<div class="panel">
      <div class="empty">
        <p><b>AI training</b> — reserved for the bot-zoo.</p>
        <p>This pane will host training-run controls and live eval curves once the
        RL harness is wired to the running server. Nothing to manage yet.</p>
      </div>
    </div>`;
  }

  _tunnelPane() {
    const t = this.status?.tunnel ?? {};
    const running = !!t.running;
    return html`<div class="panel">
      <div class="controls">
        <button @click=${() => this._send("TUNNEL_START")} ?disabled=${running}>▲ Start tunnel</button>
        <button class="danger" @click=${() => this._send("TUNNEL_STOP")} ?disabled=${!running}>■ Stop tunnel</button>
      </div>
      ${t.error
        ? html`<div class="empty" style="color:var(--bad)">${this._tunnelError(t.error)}</div>`
        : running && t.url
        ? html`<div style="margin-top:8px">
            <span style="color:var(--muted)">public URL</span>
            &nbsp; <a class="url" href=${t.url} target="_blank" rel="noopener">${t.url}</a>
            &nbsp; <button @click=${() => this._copy(t.url)}>copy</button>
          </div>`
        : html`<div class="empty">Tunnel stopped. Start it to expose the server over a public <code>trycloudflare.com</code> URL.</div>`}
    </div>`;
  }

  _tunnelError(code) {
    if (code === "cloudflared_not_found") return "cloudflared is not installed — install it to use quick tunnels.";
    if (code === "tunnel_url_timeout") return "Timed out waiting for cloudflared to report a URL.";
    if (code === "cloudflared_exited") return "cloudflared exited before reporting a URL.";
    return `Tunnel error: ${code}`;
  }

  _copy(text) {
    try { navigator.clipboard?.writeText(text); this._flash("URL copied"); } catch (e) {}
  }

  _feedbackPane() {
    return html`<div class="panel">
      <div class="controls">
        <button @click=${() => this._send("FEEDBACK_LIST")}>⟳ Refresh</button>
        <span style="color:var(--muted)">${this.reports.length} report${this.reports.length === 1 ? "" : "s"}</span>
      </div>
      ${this.reports.length === 0
        ? html`<div class="empty">No feedback reports yet.</div>`
        : html`<div class="reports">${this.reports.map((r) => html`<div class="report">
            <div class="report-head">
              <span class="tag ${r.type}">${r.type || "?"}</span>
              <b>${r.submitter || "anonymous"}</b>
              <span style="color:var(--muted)">${r.submitted || ""}</span>
              <span class="tag status-${r.status || "open"}">${r.status || "open"}</span>
            </div>
            <pre class="report-body">${r.text || ""}</pre>
            <div class="report-status" style="display:flex;gap:6px;align-items:center;flex-wrap:wrap;margin-top:6px">
              <select class="fb-status">
                ${STATUS_VALUES.map((v) => html`<option value=${v} ?selected=${(r.status || "open") === v}>${v}</option>`)}
              </select>
              <input class="fb-backlog" placeholder="FB-NN" size="6" .value=${r.backlog_id || ""} />
              <input class="fb-note" placeholder="note (optional)" .value=${r.note || ""} />
              <button @click=${(e) => this._saveStatus(r.filename, e)}>Save</button>
              ${r.updated ? html`<span style="color:var(--muted)">updated ${r.updated}</span>` : ""}
            </div>
          </div>`)}</div>`}
    </div>`;
  }

  _saveStatus(filename, e) {
    // Read the row's controls and send one FEEDBACK_UPDATE. Disable Save in-flight
    // (idempotency lesson from Spec 29 Bug E); the FEEDBACK_LIST reply re-renders the
    // pane (recreating the button), and the timeout re-enables if no reply arrives.
    const row = e.target.closest(".report");
    if (!row || e.target.disabled) return;
    e.target.disabled = true;
    const status = row.querySelector(".fb-status").value;
    const backlog_id = row.querySelector(".fb-backlog").value.trim();
    const note = row.querySelector(".fb-note").value;
    this._sendMsg({ kind: "FEEDBACK_UPDATE", filename, status, backlog_id, note });
    setTimeout(() => { e.target.disabled = false; }, 800);
  }

  _healthPane() {
    const h = this.status?.health ?? {};
    const dot = (ok) => (ok ? html`<span style="color:var(--ok)">● ok</span>` : html`<span style="color:var(--bad)">● problem</span>`);
    return html`<div class="panel">
      <table>
        <tbody>
          <tr><th>server reachable</th><td>${dot(h.admin_status_ok)}</td></tr>
          <tr><th>DB integrity</th><td>${h.db_integrity_ok == null ? "—" : dot(h.db_integrity_ok)}</td></tr>
          <tr><th>disk free</th><td>${this._gb(h.disk_free_bytes)}</td></tr>
          <tr><th>WAL size</th><td>${this._mb(h.wal_bytes)}</td></tr>
        </tbody>
      </table>
    </div>`;
  }

  _gb(b) { return b == null ? "—" : `${(b / 1073741824).toFixed(1)} GB`; }

  _logsPane() {
    const text = this.logs
      .map((l) => `${l.stream === "stderr" ? "! " : "  "}${l.text}`)
      .join("\n");
    return html`<div class="panel">
      ${this.logs.length === 0
        ? html`<div class="empty">Waiting for server output… (start the server to see logs)</div>`
        : html`<pre class="logs">${text}</pre>`}
    </div>`;
  }

  _metrics(s) {
    if (s.cpu_pct == null && s.mem_rss_bytes == null) return html`<span class="metrics"><span>— no metrics —</span></span>`;
    return html`<span class="metrics">
      <span>CPU <b>${s.cpu_pct ?? "—"}%</b></span>
      <span>MEM <b>${this._mb(s.mem_rss_bytes)}</b></span>
      <span>up <b>${this._uptime(s.uptime_s)}</b></span>
    </span>`;
  }

  _statusPane(s) {
    const tables = s.tables ?? [];
    return html`<div class="panel">
      <div style="margin-bottom:10px">
        <span style="color:var(--muted)">listen</span> ${s.listen_url ?? "—"}
        &nbsp;·&nbsp; <span style="color:var(--muted)">players</span> <b>${s.players_connected ?? 0}</b>
      </div>
      ${tables.length === 0
        ? html`<div class="empty">No active tables.</div>`
        : html`<table>
            <thead><tr><th>table</th><th>ruleset</th><th>hand</th><th>phase</th><th>seats</th></tr></thead>
            <tbody>${tables.map((t) => html`<tr>
              <td>#${t.table_id}</td><td>${t.ruleset ?? "—"}</td><td>${t.hand_index ?? "—"}</td>
              <td>${t.phase ?? "—"}</td><td>${this._seats(t.seats)}</td></tr>`)}</tbody>
          </table>`}
    </div>`;
  }

  _invitesPane() {
    return html`<div class="panel">
      <div class="controls">
        uses <input id="iv-uses" type="number" value="1" min="1" style="width:60px" />
        expires (days) <input id="iv-exp" type="number" value="7" min="0" style="width:60px" />
        <button @click=${() => this._mintInvite()}>＋ Mint invite</button>
      </div>
      ${this.invites.length === 0
        ? html`<div class="empty">No invites yet.</div>`
        : html`<table>
            <thead><tr><th>code</th><th>uses</th><th>expires</th><th>status</th><th></th></tr></thead>
            <tbody>${this.invites.map((iv) => html`<tr>
              <td>${iv.code}</td>
              <td>${iv.used_count}/${iv.max_uses}</td>
              <td>${iv.expires_iso ?? "never"}</td>
              <td>${iv.disabled ? html`<span style="color:var(--bad)">revoked</span>` : "active"}</td>
              <td>${iv.disabled ? "" : html`<button class="danger" @click=${() => this._sendMsg({ kind: "INVITE_REVOKE", code: iv.code })}>revoke</button>`}</td>
            </tr>`)}</tbody>
          </table>`}
    </div>`;
  }

  _mintInvite() {
    const uses = parseInt(this.querySelector("#iv-uses")?.value ?? "1", 10);
    const exp = parseInt(this.querySelector("#iv-exp")?.value ?? "7", 10);
    this._sendMsg({ kind: "INVITE_CREATE", max_uses: uses, expires_days: exp });
  }

  _accountsPane() {
    return html`<div class="panel">
      <div class="controls">
        <input id="ac-user" placeholder="username" style="width:120px" />
        <input id="ac-pass" type="password" placeholder="password" style="width:120px" />
        <label><input id="ac-admin" type="checkbox" /> admin</label>
        <button @click=${() => this._createAccount()}>＋ Create account</button>
      </div>
      ${this.accounts.length === 0
        ? html`<div class="empty">No accounts.</div>`
        : html`<table>
            <thead><tr><th>id</th><th>username</th><th>role</th><th>status</th><th></th></tr></thead>
            <tbody>${this.accounts.map((a) => html`<tr>
              <td>${a.account_id}</td>
              <td>${a.username} <span style="color:var(--muted)">${a.kind === "bot" ? "(bot)" : ""}</span></td>
              <td>${a.role}</td>
              <td>${a.disabled ? html`<span style="color:var(--bad)">disabled</span>` : "active"}</td>
              <td>
                <button @click=${() => this._sendMsg({ kind: "ACCOUNT_SET_ROLE", account_id: a.account_id, role: a.role === "admin" ? "user" : "admin" })}>${a.role === "admin" ? "demote" : "promote"}</button>
                <button class="${a.disabled ? "" : "danger"}" @click=${() => this._sendMsg({ kind: "ACCOUNT_SET_DISABLED", account_id: a.account_id, disabled: !a.disabled })}>${a.disabled ? "enable" : "disable"}</button>
              </td>
            </tr>`)}</tbody>
          </table>`}
    </div>`;
  }

  _createAccount() {
    const username = this.querySelector("#ac-user")?.value ?? "";
    const password = this.querySelector("#ac-pass")?.value ?? "";
    const admin = this.querySelector("#ac-admin")?.checked ?? false;
    if (!username || !password) { this._flash("username and password required"); return; }
    this._sendMsg({ kind: "ACCOUNT_CREATE", username, password, admin });
  }

  _seats(seats) {
    if (!seats) return "—";
    return seats.map((x) => (x.kind === "bot" ? "B" : x.occupied ? (x.user_id ?? "H") : "·")).join(" ");
  }
  _mb(b) { return b == null ? "—" : `${(b / 1048576).toFixed(0)}MB`; }
  _uptime(sec) {
    if (sec == null) return "—";
    const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60);
    if (h > 0) return `${h}h${m}m`;
    if (m > 0) return `${m}m${sec % 60}s`;
    return `${sec}s`;
  }
}

customElements.define("admin-app", AdminApp);

import { LitElement, html } from "lit";

// Admin control console (Spec 25). One WebSocket (`mahjong-admin-v1`) carries
// commands + STATUS pushes + list replies. Panes are added incrementally; the
// app ignores frame kinds and fields it doesn't recognise (additive evolution).
const FUTURE_TABS = ["Logs", "Tunnel", "Feedback", "Health", "Training"];

class AdminApp extends LitElement {
  createRenderRoot() { return this; }

  static properties = {
    status: { state: true },
    connected: { state: true },
    tab: { state: true },
    invites: { state: true },
    accounts: { state: true },
  };

  constructor() {
    super();
    this.status = null;
    this.connected = false;
    this.tab = "Status";
    this.invites = [];
    this.accounts = [];
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
  }

  render() {
    const s = this.status?.server ?? {};
    const state = s.state ?? "UNKNOWN";
    const running = state === "RUNNING";
    const busy = state === "STARTING" || state === "STOPPING";
    const tabs = ["Status", "Invites", "Accounts", ...FUTURE_TABS];
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
    return this._statusPane(s);
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

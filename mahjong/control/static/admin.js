import { LitElement, html, css } from "lit";

// Admin control console (Spec 25). One WebSocket (`mahjong-admin-v1`) carries
// commands + STATUS pushes. The skeleton renders the Status tab; later steps
// (invites/accounts/logs/tunnel/feedback/health) slot in as additional tabs.
const FUTURE_TABS = ["Invites", "Accounts", "Logs", "Tunnel", "Feedback", "Health", "Training"];

class AdminApp extends LitElement {
  // Render into the light DOM so the global stylesheet applies (the game client
  // does the same; avoids re-declaring styles in the shadow root).
  createRenderRoot() { return this; }

  static properties = {
    status: { state: true },
    connected: { state: true },
    tab: { state: true },
  };

  constructor() {
    super();
    this.status = null;
    this.connected = false;
    this.tab = "Status";
    this._ws = null;
    this._reconnectMs = 500;
  }

  connectedCallback() {
    super.connectedCallback();
    this._connect();
  }

  _connect() {
    const url = `ws://${location.host}/`;
    let ws;
    try {
      ws = new WebSocket(url, "mahjong-admin-v1");
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
    };
  }

  _scheduleReconnect() {
    setTimeout(() => this._connect(), this._reconnectMs);
    this._reconnectMs = Math.min(this._reconnectMs * 2, 5000);
  }

  _send(kind) {
    if (this._ws && this._ws.readyState === WebSocket.OPEN) {
      this._ws.send(JSON.stringify({ kind }));
    }
  }

  render() {
    const s = this.status?.server ?? {};
    const state = s.state ?? "UNKNOWN";
    const running = state === "RUNNING";
    const busy = state === "STARTING" || state === "STOPPING";
    return html`
      <div class="wrap">
        <header class="bar">
          <span class="title">mahjong <span class="zh">控制</span></span>
          <span class="badge ${state}">${state}</span>
          <span class="conn ${this.connected ? "up" : "down"}">
            ${this.connected ? "● console connected" : "○ console offline"}
          </span>
          <span class="spacer"></span>
          ${this._metrics(s)}
        </header>

        <div class="controls">
          <button @click=${() => this._send("SERVER_START")} ?disabled=${running || busy}>▶ Start server</button>
          <button class="danger" @click=${() => this._send("SERVER_STOP")} ?disabled=${state === "STOPPED" || state === "CRASHED" || busy}>■ Stop</button>
          <button @click=${() => this._send("SERVER_RESTART")} ?disabled=${!running}>⟳ Restart</button>
        </div>

        <div class="tabs">
          <span class="tab ${this.tab === "Status" ? "active" : ""}" @click=${() => (this.tab = "Status")}>Status</span>
          ${FUTURE_TABS.map((t) => html`<span class="tab disabled" title="coming soon">${t}</span>`)}
        </div>

        ${this._statusPane(s)}
      </div>
    `;
  }

  _metrics(s) {
    if (s.cpu_pct == null && s.mem_rss_bytes == null) {
      return html`<span class="metrics"><span>— no metrics —</span></span>`;
    }
    return html`
      <span class="metrics">
        <span>CPU <b>${s.cpu_pct ?? "—"}%</b></span>
        <span>MEM <b>${this._mb(s.mem_rss_bytes)}</b></span>
        <span>up <b>${this._uptime(s.uptime_s)}</b></span>
      </span>`;
  }

  _statusPane(s) {
    const tables = s.tables ?? [];
    return html`
      <div class="panel">
        <div style="margin-bottom:10px">
          <span style="color:var(--muted)">listen</span>
          ${s.listen_url ? html`<a class="url" href="#">${s.listen_url}</a>` : html`<span>—</span>`}
          &nbsp;·&nbsp;
          <span style="color:var(--muted)">players</span> <b>${s.players_connected ?? 0}</b>
        </div>
        ${tables.length === 0
          ? html`<div class="empty">No active tables.</div>`
          : html`
            <table>
              <thead><tr><th>table</th><th>ruleset</th><th>hand</th><th>phase</th><th>seats</th></tr></thead>
              <tbody>
                ${tables.map((t) => html`
                  <tr>
                    <td>#${t.table_id}</td>
                    <td>${t.ruleset ?? "—"}</td>
                    <td>${t.hand_index ?? "—"}</td>
                    <td>${t.phase ?? "—"}</td>
                    <td>${this._seats(t.seats)}</td>
                  </tr>`)}
              </tbody>
            </table>`}
      </div>
    `;
  }

  _seats(seats) {
    if (!seats) return "—";
    return seats.map((x) => {
      if (x.kind === "bot") return "B";
      return x.occupied ? (x.user_id ?? "H") : "·";
    }).join(" ");
  }

  _mb(bytes) {
    if (bytes == null) return "—";
    return `${(bytes / (1024 * 1024)).toFixed(0)}MB`;
  }

  _uptime(sec) {
    if (sec == null) return "—";
    const h = Math.floor(sec / 3600);
    const m = Math.floor((sec % 3600) / 60);
    const sObj = sec % 60;
    if (h > 0) return `${h}h${m}m`;
    if (m > 0) return `${m}m${sObj}s`;
    return `${sObj}s`;
  }
}

customElements.define("admin-app", AdminApp);

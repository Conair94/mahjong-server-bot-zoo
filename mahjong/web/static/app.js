// Web client entry — Step 7.5a walking skeleton.
//
// Scope (locked by docs/specs/tui-client.md): just <mahjong-app> + <game-pane>
// + ConnectionManager. No chat, stats, or spectator panes yet. No login,
// no lobby. The page connects to the server's WebSocket on load and renders
// inbound wire messages as an ASCII frame log inside <game-pane>.

import { LitElement, html, css } from "lit";

// --- ConnectionManager --------------------------------------------------

const SUBPROTOCOL = "mahjong-v1";

class ConnectionManager extends EventTarget {
  constructor(url) {
    super();
    this.url = url;
    this.ws = null;
  }

  connect() {
    this.ws = new WebSocket(this.url, SUBPROTOCOL);
    this.ws.addEventListener("open", () => this.dispatchEvent(new Event("open")));
    this.ws.addEventListener("close", (e) =>
      this.dispatchEvent(new CustomEvent("close", { detail: { code: e.code, reason: e.reason } })),
    );
    this.ws.addEventListener("error", () => this.dispatchEvent(new Event("error")));
    this.ws.addEventListener("message", (e) => {
      let msg;
      try {
        msg = JSON.parse(e.data);
      } catch (err) {
        this.dispatchEvent(new CustomEvent("decode-error", { detail: { raw: e.data, err } }));
        return;
      }
      this.dispatchEvent(new CustomEvent("message", { detail: msg }));
    });
  }

  send(message) {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
      throw new Error("WebSocket not open");
    }
    this.ws.send(JSON.stringify(message));
  }

  close(code = 1000) {
    if (this.ws) this.ws.close(code);
  }
}

// --- <game-pane> --------------------------------------------------------

class GamePane extends LitElement {
  static properties = {
    status: { type: String },
    frames: { state: true },
  };

  static styles = css`
    :host {
      display: block;
      color: var(--fg);
    }
    .frame {
      border: 1px solid var(--border);
      padding: 0.75rem 1rem;
      margin-bottom: 0.75rem;
    }
    .title {
      color: var(--accent);
      margin-bottom: 0.5rem;
    }
    .log {
      max-height: 60vh;
      overflow-y: auto;
    }
    .log pre {
      border-bottom: 1px dashed var(--border);
      padding: 0.25rem 0;
      color: var(--fg-dim);
    }
    .log pre.kind-HELLO { color: var(--accent); }
    .log pre.kind-ERROR { color: var(--error); }
    .status {
      color: var(--fg-dim);
    }
    .status.connected { color: var(--accent); }
    .status.error { color: var(--error); }
  `;

  constructor() {
    super();
    this.status = "connecting";
    this.frames = [];
  }

  pushFrame(msg) {
    this.frames = [...this.frames, msg];
  }

  setStatus(status) {
    this.status = status;
  }

  render() {
    return html`
      <div class="frame">
        <div class="title">┌─ Game pane (walking skeleton) ──────────────────────────┐</div>
        <div class="status ${this.status}">
          Connection: ${this.status}
        </div>
      </div>
      <div class="frame log">
        <div class="title">┌─ Wire log ──────────────────────────────────────────────┐</div>
        ${this.frames.length === 0
          ? html`<pre class="empty">(no frames yet)</pre>`
          : this.frames.map(
              (f) => html`<pre class="kind-${f.kind || "?"}">${JSON.stringify(f, null, 2)}</pre>`,
            )}
      </div>
    `;
  }
}

customElements.define("game-pane", GamePane);

// --- <mahjong-app> ------------------------------------------------------

class MahjongApp extends LitElement {
  static properties = {
    route: { type: String },
  };

  static styles = css`
    :host {
      display: block;
    }
    header {
      color: var(--accent);
      margin-bottom: 1rem;
    }
    header pre { color: var(--accent); }
  `;

  constructor() {
    super();
    this.route = "table"; // walking skeleton: go straight to the table pane
    this._conn = null;
  }

  firstUpdated() {
    const wsUrl = `${location.protocol === "https:" ? "wss:" : "ws:"}//${location.host}/socket`;
    this._conn = new ConnectionManager(wsUrl);
    const pane = this.renderRoot.querySelector("game-pane");
    this._conn.addEventListener("open", () => pane.setStatus("connected"));
    this._conn.addEventListener("close", (e) => pane.setStatus(`closed (${e.detail.code})`));
    this._conn.addEventListener("error", () => pane.setStatus("error"));
    this._conn.addEventListener("message", (e) => pane.pushFrame(e.detail));
    this._conn.connect();
  }

  render() {
    return html`
      <header>
        <pre>
 ╔══════════════════════════════════════════════════════════╗
 ║   Mahjong / 麻将        — web client, step 7.5a walk     ║
 ╚══════════════════════════════════════════════════════════╝</pre>
      </header>
      <game-pane></game-pane>
    `;
  }
}

customElements.define("mahjong-app", MahjongApp);

// Web client entry — Step 7.5b pane-toggle shell.
//
// Scope: introduces <table-page> as the host element for the four-pane grid.
// <game-pane> still renders the 7.5a wire-log walking skeleton (real snapshot
// rendering lands in 7.5c). <chat-pane>, <stats-pane>, <spectator-pane> are
// stubs with placeholder content. Pane visibility is held on <mahjong-app>
// so it survives route transitions; toggle hotkeys are Alt+C / Alt+S / Alt+W
// to keep the bare-letter keys (C/P/H/G/B) reserved for player actions.
//
// Per docs/specs/tui-client.md fixture 17.

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

// --- Shared pane chrome -------------------------------------------------

const paneChromeStyles = css`
  :host {
    display: block;
    color: var(--fg);
  }
  .pane {
    border: 1px solid var(--border);
    padding: 0.5rem 1rem 0.75rem;
    height: 100%;
    box-sizing: border-box;
  }
  .pane-header {
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    color: var(--accent);
    margin-bottom: 0.5rem;
  }
  .pane-title { color: var(--accent); }
  .pane-close {
    background: none;
    border: none;
    color: var(--fg-dim);
    font-family: inherit;
    font-size: inherit;
    cursor: pointer;
    padding: 0 0.25rem;
  }
  .pane-close:hover { color: var(--accent); }
  .placeholder {
    color: var(--fg-dim);
    padding: 1rem 0;
  }
`;

function paneHeader(title, hotkey, onClose) {
  return html`
    <div class="pane-header">
      <span class="pane-title">─ ${title} ${hotkey ? html`<span style="color: var(--fg-dim);">(${hotkey})</span>` : ""} ─</span>
      ${onClose
        ? html`<button class="pane-close" @click=${onClose} title="Close pane">[ × ]</button>`
        : ""}
    </div>
  `;
}

// --- <game-pane> --------------------------------------------------------

class GamePane extends LitElement {
  static properties = {
    status: { type: String },
    frames: { state: true },
  };

  static styles = [
    paneChromeStyles,
    css`
      .log {
        max-height: 50vh;
        overflow-y: auto;
      }
      .log pre {
        border-bottom: 1px dashed var(--border);
        padding: 0.25rem 0;
        color: var(--fg-dim);
        white-space: pre-wrap;
        word-break: break-all;
      }
      .log pre.kind-HELLO { color: var(--accent); }
      .log pre.kind-ERROR { color: var(--error); }
      .status { color: var(--fg-dim); margin-bottom: 0.5rem; }
      .status.connected { color: var(--accent); }
      .status.error { color: var(--error); }
    `,
  ];

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
      <div class="pane">
        ${paneHeader("Game pane (walking skeleton)", null, null)}
        <div class="status ${this.status}">Connection: ${this.status}</div>
        <div class="log">
          ${this.frames.length === 0
            ? html`<pre class="empty">(no frames yet)</pre>`
            : this.frames.map(
                (f) => html`<pre class="kind-${f.kind || "?"}">${JSON.stringify(f, null, 2)}</pre>`,
              )}
        </div>
      </div>
    `;
  }
}

customElements.define("game-pane", GamePane);

// --- <chat-pane> (stub) -------------------------------------------------

class ChatPane extends LitElement {
  static styles = paneChromeStyles;

  render() {
    return html`
      <div class="pane">
        ${paneHeader("Chat", "Alt+C", () => this.dispatchEvent(new CustomEvent("pane-close", { bubbles: true, composed: true, detail: { pane: "chat" } })))}
        <div class="placeholder">
          (chat pane — not yet implemented)<br />
          Wire-protocol amendment for CHAT frames is required before this can do anything real.
        </div>
      </div>
    `;
  }
}

customElements.define("chat-pane", ChatPane);

// --- <stats-pane> (stub) ------------------------------------------------

class StatsPane extends LitElement {
  static styles = paneChromeStyles;

  render() {
    return html`
      <div class="pane">
        ${paneHeader("Stats", "Alt+S", () => this.dispatchEvent(new CustomEvent("pane-close", { bubbles: true, composed: true, detail: { pane: "stats" } })))}
        <div class="placeholder">
          (stats pane — not yet implemented)<br />
          Cross-game stats require Layer 8 / SQLite persistence and a STATS request/response on the wire.
        </div>
      </div>
    `;
  }
}

customElements.define("stats-pane", StatsPane);

// --- <spectator-pane> (stub) --------------------------------------------

class SpectatorPane extends LitElement {
  static styles = paneChromeStyles;

  render() {
    return html`
      <div class="pane">
        ${paneHeader("Spectator", "Alt+W", () => this.dispatchEvent(new CustomEvent("pane-close", { bubbles: true, composed: true, detail: { pane: "spectator" } })))}
        <div class="placeholder">
          (spectator pane — not yet implemented)<br />
          Will open a second WebSocket to a peer table. No multi-subscription on this connection.
        </div>
      </div>
    `;
  }
}

customElements.define("spectator-pane", SpectatorPane);

// --- <table-page> -------------------------------------------------------

const PANE_HOTKEYS = {
  // event.code form — matches a physical key regardless of layout/locale.
  KeyC: "chat",
  KeyS: "stats",
  KeyW: "spectator",
};

class TablePage extends LitElement {
  static properties = {
    panes: { type: Object },
  };

  static styles = css`
    :host { display: block; }

    .table-header {
      color: var(--accent);
      border: 1px solid var(--accent-red);
      border-left-width: 3px;
      padding: 0.5rem 1rem;
      margin-bottom: 0.75rem;
      display: flex;
      justify-content: space-between;
    }
    .table-header .panes-indicator { color: var(--fg-dim); }
    .table-header .panes-indicator .on { color: var(--accent); }
    .table-header .panes-indicator .always-on { color: var(--accent-red); }

    .grid {
      display: grid;
      gap: 0.75rem;
      grid-template-columns: minmax(0, 2fr) minmax(0, 1fr);
      grid-template-areas:
        "game side"
        "spectator spectator";
    }
    /* When the right column has no panes, the side column collapses. */
    .grid.no-side {
      grid-template-columns: minmax(0, 1fr);
      grid-template-areas:
        "game"
        "spectator";
    }
    .grid.no-spectator { grid-template-areas: "game side"; }
    .grid.no-side.no-spectator { grid-template-areas: "game"; }

    .slot-game { grid-area: game; }
    .slot-side {
      grid-area: side;
      display: flex;
      flex-direction: column;
      gap: 0.75rem;
    }
    .slot-spectator { grid-area: spectator; }
  `;

  constructor() {
    super();
    this.panes = { chat: false, stats: false, spectator: false };
    this._onKeydown = this._handleKeydown.bind(this);
    this._onPaneClose = this._handlePaneClose.bind(this);
  }

  connectedCallback() {
    super.connectedCallback();
    window.addEventListener("keydown", this._onKeydown);
    this.addEventListener("pane-close", this._onPaneClose);
  }

  disconnectedCallback() {
    window.removeEventListener("keydown", this._onKeydown);
    this.removeEventListener("pane-close", this._onPaneClose);
    super.disconnectedCallback();
  }

  _handleKeydown(e) {
    if (!e.altKey || e.ctrlKey || e.metaKey || e.shiftKey) return;
    const pane = PANE_HOTKEYS[e.code];
    if (!pane) return;
    e.preventDefault();
    this._togglePane(pane);
  }

  _handlePaneClose(e) {
    const pane = e.detail?.pane;
    if (pane && this.panes[pane]) this._togglePane(pane);
  }

  _togglePane(pane) {
    this.panes = { ...this.panes, [pane]: !this.panes[pane] };
    this.dispatchEvent(
      new CustomEvent("panes-changed", {
        bubbles: true,
        composed: true,
        detail: { panes: { ...this.panes } },
      }),
    );
  }

  _paneIndicator(label, isOn) {
    return html`<span class=${isOn ? "on" : ""}>${label}</span>`;
  }

  _alwaysOnIndicator(label) {
    return html`<span class="always-on">${label}</span>`;
  }

  render() {
    const sideEmpty = !this.panes.chat && !this.panes.stats;
    const spectatorOff = !this.panes.spectator;
    const gridClasses = ["grid"];
    if (sideEmpty) gridClasses.push("no-side");
    if (spectatorOff) gridClasses.push("no-spectator");

    return html`
      <div class="table-header">
        <span>Table — demo  ·  Hand —/—  ·  Wind —  ·  Wall —</span>
        <span class="panes-indicator">
          Panes:
          ${this._alwaysOnIndicator("[G]")}
          ${this._paneIndicator("C", this.panes.chat)}·${this._paneIndicator(
            "S",
            this.panes.stats,
          )}·${this._paneIndicator("W", this.panes.spectator)}
        </span>
      </div>
      <div class=${gridClasses.join(" ")}>
        <div class="slot-game"><game-pane></game-pane></div>
        ${!sideEmpty
          ? html`
              <div class="slot-side">
                ${this.panes.chat ? html`<chat-pane></chat-pane>` : ""}
                ${this.panes.stats ? html`<stats-pane></stats-pane>` : ""}
              </div>
            `
          : ""}
        ${this.panes.spectator
          ? html`<div class="slot-spectator"><spectator-pane></spectator-pane></div>`
          : ""}
      </div>
    `;
  }

  // Expose the game-pane to the app shell so it can wire ConnectionManager
  // events into it. Public on purpose; the alternative (bubbled events) is
  // overkill for the walking skeleton.
  get gamePane() {
    return this.renderRoot.querySelector("game-pane");
  }
}

customElements.define("table-page", TablePage);

// --- <mahjong-app> ------------------------------------------------------

const THEME_STORAGE_KEY = "mahjong-theme";
const THEMES = ["dark", "light"];

function loadInitialTheme() {
  try {
    const stored = localStorage.getItem(THEME_STORAGE_KEY);
    if (THEMES.includes(stored)) return stored;
  } catch {
    // localStorage can throw in private-mode/sandboxed contexts; fall through.
  }
  return "dark";
}

class MahjongApp extends LitElement {
  static properties = {
    route: { type: String },
    panes: { state: true },
    theme: { state: true },
  };

  static styles = css`
    :host { display: block; }
    header {
      color: var(--accent);
      margin-bottom: 1rem;
      display: flex;
      justify-content: space-between;
      align-items: flex-start;
      gap: 1rem;
    }
    header pre { color: var(--accent-red); }
    .controls {
      display: flex;
      gap: 0.5rem;
      align-items: center;
    }
    .theme-btn {
      background: transparent;
      border: 1px solid var(--border);
      color: var(--fg);
      font-family: inherit;
      font-size: inherit;
      padding: 0.25rem 0.75rem;
      cursor: pointer;
    }
    .theme-btn:hover { color: var(--accent); border-color: var(--accent); }
    .theme-btn .hint { color: var(--fg-dim); margin-left: 0.5rem; }
  `;

  constructor() {
    super();
    this.route = "table"; // walking skeleton: go straight to the table page
    // Pane visibility lives here so it survives route transitions.
    this.panes = { chat: false, stats: false, spectator: false };
    this.theme = loadInitialTheme();
    this._conn = null;
    this._onKeydown = this._handleKeydown.bind(this);
  }

  connectedCallback() {
    super.connectedCallback();
    this._applyTheme();
    window.addEventListener("keydown", this._onKeydown);
  }

  disconnectedCallback() {
    window.removeEventListener("keydown", this._onKeydown);
    super.disconnectedCallback();
  }

  updated(changed) {
    if (changed.has("theme")) this._applyTheme();
  }

  _applyTheme() {
    document.documentElement.dataset.theme = this.theme;
  }

  _handleKeydown(e) {
    // Alt+T toggles theme. Other Alt-chords belong to <table-page>; we early-
    // return on those to avoid double-handling.
    if (!e.altKey || e.ctrlKey || e.metaKey || e.shiftKey) return;
    if (e.code !== "KeyT") return;
    e.preventDefault();
    this._toggleTheme();
  }

  _toggleTheme() {
    this.theme = this.theme === "dark" ? "light" : "dark";
    try {
      localStorage.setItem(THEME_STORAGE_KEY, this.theme);
    } catch {
      // Storage unavailable — theme will simply not persist. Non-fatal.
    }
  }

  firstUpdated() {
    const wsUrl = `${location.protocol === "https:" ? "wss:" : "ws:"}//${location.host}/socket`;
    this._conn = new ConnectionManager(wsUrl);
    const tablePage = this.renderRoot.querySelector("table-page");
    // Wait one microtask for the table-page to render its <game-pane>.
    queueMicrotask(() => {
      const pane = tablePage?.gamePane;
      if (!pane) return;
      this._conn.addEventListener("open", () => pane.setStatus("connected"));
      this._conn.addEventListener("close", (e) => pane.setStatus(`closed (${e.detail.code})`));
      this._conn.addEventListener("error", () => pane.setStatus("error"));
      this._conn.addEventListener("message", (e) => pane.pushFrame(e.detail));
      this._conn.connect();
    });

    this.addEventListener("panes-changed", (e) => {
      this.panes = e.detail.panes;
    });
  }

  render() {
    const next = this.theme === "dark" ? "light" : "dark";
    return html`
      <header>
        <pre>
 ╔══════════════════════════════════════════════════════════╗
 ║   Mahjong / 麻将        — web client, step 7.5b shell    ║
 ╚══════════════════════════════════════════════════════════╝</pre>
        <div class="controls">
          <button
            class="theme-btn"
            @click=${this._toggleTheme}
            title="Toggle theme (Alt+T)"
          >
            [ ${this.theme} → ${next} ]<span class="hint">Alt+T</span>
          </button>
        </div>
      </header>
      <table-page .panes=${this.panes}></table-page>
    `;
  }
}

customElements.define("mahjong-app", MahjongApp);

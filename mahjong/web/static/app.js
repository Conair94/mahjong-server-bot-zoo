// Web client entry — Step 7.5c.i snapshot rendering.
//
// <game-pane> now renders a real SeatView (state-schema.md § Per-seat
// projection) into the ASCII table layout when an ATTACHED frame arrives.
// The wire log is kept as a collapsible debug pane below the table — useful
// while we're still smoke-testing the wire shape.
//
// applyEvent (7.5c.ii), PROMPT bar (7.5c.iii), and bilingual rendering
// (7.5c.iv) follow in subsequent commits.

import { LitElement, html, css } from "lit";
import { renderTable } from "/static/render.js";
import { applyEvent } from "/static/apply_event.js";

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
    seatView: { state: true },
    ownSeat: { state: true },
    tileStyle: { type: String },
    frames: { state: true },
    showLog: { state: true },
  };

  static styles = [
    paneChromeStyles,
    css`
      .status { color: var(--fg-dim); margin-bottom: 0.5rem; }
      .status.connected { color: var(--accent); }
      .status.error { color: var(--error); }

      .table-ascii {
        color: var(--fg);
        font-family: inherit;
        margin: 0.25rem 0 0.75rem;
        padding: 0.5rem 0;
        border-top: 1px dashed var(--border);
        border-bottom: 1px dashed var(--border);
      }
      .table-ascii .section {
        margin: 0;
        padding: 0.25rem 0;
        white-space: pre;
        font-family: inherit;
        color: inherit;
      }
      .table-ascii hr.ascii-rule {
        border: none;
        border-top: 1px solid var(--border);
        margin: 0.4rem 0;
        height: 0;
      }
      .waiting {
        color: var(--fg-dim);
        font-style: italic;
        padding: 1rem 0;
      }

      /* --- Tile colors. These rules live inside the component's shadow
       * root because document-level CSS does not pierce shadow DOM.
       * Custom properties (--suit-bamboo etc.) DO inherit through, so the
       * theme system still works.
       *
       * Tiles render slightly larger than surrounding labels — Unicode
       * mahjong glyphs are rendered small in most fonts, and even the
       * ASCII shorthand reads as the "main object" on the table. line-
       * height: 1 keeps row spacing stable as tile-size grows.
       */
      .tile {
        display: inline;
        font-size: 1.8em;
        line-height: 1;
        vertical-align: baseline;
      }
      .tile.dragon, .tile.face-down { font-size: 2.2em; }
      .tile .rank { color: var(--fg); }
      .tile .suit-bamboo,
      .tile.suit-bamboo { color: var(--suit-bamboo); }
      .tile .suit-character,
      .tile.suit-character { color: var(--suit-character); }
      .tile .suit-dots,
      .tile.suit-dots { color: var(--fg); }
      .tile.wind, .tile.flower { color: var(--fg); }
      .tile.face-down { color: var(--fg-dim); }
      .tile.dragon.dragon-red   { color: var(--dragon-red); }
      .tile.dragon.dragon-green { color: var(--dragon-green); }
      .tile.dragon.dragon-white { color: var(--dragon-white); }
      .empty { color: var(--fg-dim); }
      .seat-label { color: var(--accent); }
      .seat-position { color: var(--fg-dim); }
      .flower-tag { color: var(--warn); }
      .hdr-label { color: var(--fg-dim); }

      .log-toggle {
        background: none;
        border: none;
        color: var(--fg-dim);
        font-family: inherit;
        font-size: inherit;
        cursor: pointer;
        padding: 0.25rem 0;
      }
      .log-toggle:hover { color: var(--accent); }

      .log {
        max-height: 30vh;
        overflow-y: auto;
        margin-top: 0.5rem;
      }
      .log pre {
        border-bottom: 1px dashed var(--border);
        padding: 0.25rem 0;
        color: var(--fg-dim);
        white-space: pre-wrap;
        word-break: break-all;
        font-size: 0.85em;
      }
      .log pre.kind-HELLO { color: var(--accent); }
      .log pre.kind-ATTACHED { color: var(--accent); }
      .log pre.kind-ERROR { color: var(--error); }
    `,
  ];

  constructor() {
    super();
    this.status = "connecting";
    this.seatView = null;
    this.ownSeat = null;
    this.tileStyle = "ascii";
    this.frames = [];
    this.showLog = false;
  }

  pushFrame(msg) {
    this.frames = [...this.frames, msg];
  }

  setStatus(status) {
    this.status = status;
  }

  setSnapshot(seatView, ownSeat) {
    this.seatView = seatView;
    this.ownSeat = ownSeat;
  }

  _toggleLog() {
    this.showLog = !this.showLog;
  }

  render() {
    const tableContent = this.seatView
      ? renderTable(this.seatView, this.ownSeat, { tileStyle: this.tileStyle })
      : null;

    return html`
      <div class="pane">
        ${paneHeader("Game pane", null, null)}
        <div class="status ${this.status}">Connection: ${this.status}</div>
        ${tableContent !== null
          ? html`<div class="table-ascii">${tableContent}</div>`
          : html`<div class="waiting">(waiting for ATTACHED snapshot…)</div>`}

        <button class="log-toggle" @click=${this._toggleLog}>
          ${this.showLog ? "▼" : "▶"} wire log (${this.frames.length} frame${this.frames.length === 1 ? "" : "s"})
        </button>
        ${this.showLog
          ? html`
              <div class="log">
                ${this.frames.length === 0
                  ? html`<pre class="empty">(no frames yet)</pre>`
                  : this.frames.map(
                      (f) => html`<pre class="kind-${f.kind || "?"}">${JSON.stringify(f, null, 2)}</pre>`,
                    )}
              </div>
            `
          : ""}
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
    tileStyle: { type: String },
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
    this.tileStyle = "ascii";
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
        <div class="slot-game"><game-pane .tileStyle=${this.tileStyle}></game-pane></div>
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

const TILE_STYLE_STORAGE_KEY = "mahjong-tile-style";
const TILE_STYLES = ["ascii", "unicode"];

function loadInitialTheme() {
  try {
    const stored = localStorage.getItem(THEME_STORAGE_KEY);
    if (THEMES.includes(stored)) return stored;
  } catch {
    // localStorage can throw in private-mode/sandboxed contexts; fall through.
  }
  return "dark";
}

function loadInitialTileStyle() {
  try {
    const stored = localStorage.getItem(TILE_STYLE_STORAGE_KEY);
    if (TILE_STYLES.includes(stored)) return stored;
  } catch {
    // ignore.
  }
  return "ascii";
}

class MahjongApp extends LitElement {
  static properties = {
    route: { type: String },
    panes: { state: true },
    theme: { state: true },
    tileStyle: { state: true },
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
      flex-wrap: wrap;
      flex-shrink: 0;
    }
    .theme-btn {
      background: transparent;
      border: 1px solid var(--border);
      color: var(--fg);
      font-family: inherit;
      font-size: inherit;
      padding: 0.25rem 0.75rem;
      cursor: pointer;
      white-space: nowrap;
      flex-shrink: 0;
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
    this.tileStyle = loadInitialTileStyle();
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
    // Alt+T toggles theme; Alt+U toggles tile style. Other Alt-chords belong
    // to <table-page>; we early-return on those to avoid double-handling.
    if (!e.altKey || e.ctrlKey || e.metaKey || e.shiftKey) return;
    if (e.code === "KeyT") {
      e.preventDefault();
      this._toggleTheme();
    } else if (e.code === "KeyU") {
      e.preventDefault();
      this._toggleTileStyle();
    }
  }

  _toggleTheme() {
    this.theme = this.theme === "dark" ? "light" : "dark";
    try {
      localStorage.setItem(THEME_STORAGE_KEY, this.theme);
    } catch {
      // Storage unavailable — theme will simply not persist. Non-fatal.
    }
  }

  _toggleTileStyle() {
    this.tileStyle = this.tileStyle === "ascii" ? "unicode" : "ascii";
    try {
      localStorage.setItem(TILE_STYLE_STORAGE_KEY, this.tileStyle);
    } catch {
      // ignore — non-fatal.
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
      this._conn.addEventListener("message", (e) => {
        const frame = e.detail;
        pane.pushFrame(frame);
        if (frame.kind === "ATTACHED" && frame.snapshot) {
          pane.setSnapshot(frame.snapshot, frame.seat ?? 0);
        } else if (frame.kind === "EVENT" && frame.event && pane.seatView) {
          // The pane's seatView is mutated by the reducer per event so the
          // ASCII layout stays current without a fresh snapshot per turn.
          const next = applyEvent(pane.seatView, frame.event, pane.ownSeat);
          pane.setSnapshot(next, pane.ownSeat);
        }
      });
      this._conn.connect();
    });

    this.addEventListener("panes-changed", (e) => {
      this.panes = e.detail.panes;
    });
  }

  render() {
    const nextTheme = this.theme === "dark" ? "light" : "dark";
    const nextTile = this.tileStyle === "ascii" ? "unicode" : "ascii";
    return html`
      <header>
        <pre>
 ╔══════════════════════════════════════════════════════════╗
 ║   Mahjong / 麻将        — web client, step 7.5c.i        ║
 ╚══════════════════════════════════════════════════════════╝</pre>
        <div class="controls">
          <button
            class="theme-btn"
            @click=${this._toggleTheme}
            title="Toggle theme (Alt+T)"
          >
            [ ${this.theme} → ${nextTheme} ]<span class="hint">Alt+T</span>
          </button>
          <button
            class="theme-btn"
            @click=${this._toggleTileStyle}
            title="Toggle tile style (Alt+U)"
          >
            [ tiles: ${this.tileStyle} → ${nextTile} ]<span class="hint">Alt+U</span>
          </button>
        </div>
      </header>
      <table-page .panes=${this.panes} .tileStyle=${this.tileStyle}></table-page>
    `;
  }
}

customElements.define("mahjong-app", MahjongApp);

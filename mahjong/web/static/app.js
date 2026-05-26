// Web client entry — Step 8.5 AUTH wire + table discovery.
//
// <game-pane> renders a SeatView (state-schema.md § Per-seat projection)
// as an ASCII table, mutates it per inbound EVENT, and — when a PROMPT is
// outstanding — renders a prompt bar listing the legal actions with their
// key bindings. Keystrokes get translated to ACTION frames and sent back.
//
// Step 8.5 adds: auth form (shown when HELLO.features includes "auth"),
// AUTH_REQUEST / AUTH_RESPONSE handling, and post-auth table discovery
// (LIST_TABLES → CREATE_TABLE if needed → ATTACH seat 0).

import { LitElement, html, css } from "lit";
import { renderTable } from "/static/render.js";
import { applyEvent } from "/static/apply_event.js";
import { renderPromptBar, actionForKey, tileIndexForKeyCode } from "/static/prompt.js";

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
    currentPrompt: { state: true },
    selectedTile: { state: true },
    illegalBanner: { state: true },
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

      /* --- Prompt bar (7.5c.iii). Renders when currentPrompt is set. */
      .prompt-bar {
        margin: 0.5rem 0;
        padding: 0.5rem 0.75rem;
        border: 1px solid var(--accent);
        border-left-width: 3px;
        display: flex;
        flex-wrap: wrap;
        gap: 0.75rem 1.25rem;
        align-items: baseline;
      }
      .prompt-bar-label { color: var(--accent); }
      .prompt-action { color: var(--fg); white-space: nowrap; }
      .prompt-action.prompt-play { color: var(--fg-dim); }
      .prompt-bar kbd {
        font-family: inherit;
        color: var(--accent);
        background: transparent;
        border: none;
        padding: 0;
      }

      /* --- Illegal-action banner (transient). The prompt stays open. */
      .illegal-banner {
        margin: 0.5rem 0;
        padding: 0.4rem 0.75rem;
        border: 1px solid var(--error);
        color: var(--error);
      }
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
    this.currentPrompt = null;
    this.selectedTile = null;
    this.illegalBanner = null;
    this._illegalBannerTimer = null;
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

  setPrompt(prompt) {
    // A new prompt clears any stale selection and dismisses an illegal-action
    // banner from the previous attempt.
    this.currentPrompt = prompt;
    this.selectedTile = null;
    this._clearIllegalBanner();
  }

  clearPrompt() {
    this.currentPrompt = null;
    this.selectedTile = null;
  }

  showIllegalBanner(message) {
    this.illegalBanner = message;
    // The prompt stays open (per spec fixture 9). The banner is transient
    // so it doesn't pile up if the player retries multiple times.
    if (this._illegalBannerTimer != null) clearTimeout(this._illegalBannerTimer);
    this._illegalBannerTimer = setTimeout(() => this._clearIllegalBanner(), 4000);
  }

  _clearIllegalBanner() {
    this.illegalBanner = null;
    if (this._illegalBannerTimer != null) {
      clearTimeout(this._illegalBannerTimer);
      this._illegalBannerTimer = null;
    }
  }

  _toggleLog() {
    this.showLog = !this.showLog;
  }

  connectedCallback() {
    super.connectedCallback();
    this._onKeydown = (e) => this._handleKeydown(e);
    window.addEventListener("keydown", this._onKeydown);
  }

  disconnectedCallback() {
    if (this._onKeydown) window.removeEventListener("keydown", this._onKeydown);
    super.disconnectedCallback();
  }

  _ownConcealedTiles() {
    if (!this.seatView || this.ownSeat == null) return [];
    const seat = this.seatView.seats?.[this.ownSeat];
    return Array.isArray(seat?.concealed) ? seat.concealed : [];
  }

  _handleKeydown(e) {
    // Alt-chords belong to <table-page> (pane toggles) and <mahjong-app>
    // (theme/tile-style). Ctrl/Meta likewise reserved for browser shortcuts.
    if (e.altKey || e.ctrlKey || e.metaKey) return;
    if (!this.currentPrompt) return;

    // Tile-selection keys set the cursor; arrow keys nudge it; Enter
    // confirms PLAY. All other keys dispatch to actionForKey.
    const tileIdx = tileIndexForKeyCode(e.code);
    const concealed = this._ownConcealedTiles();
    if (tileIdx !== null) {
      if (tileIdx >= 0 && tileIdx < concealed.length) {
        e.preventDefault();
        this.selectedTile = tileIdx;
      }
      return;
    }
    if (e.code === "ArrowLeft" || e.code === "ArrowRight") {
      if (concealed.length === 0) return;
      const cur = this.selectedTile ?? concealed.length - 1;
      const next = e.code === "ArrowLeft" ? Math.max(0, cur - 1) : Math.min(concealed.length - 1, cur + 1);
      e.preventDefault();
      this.selectedTile = next;
      return;
    }

    const action = actionForKey(e.code, this.currentPrompt, this.selectedTile, concealed);
    if (!action) return; // illegal key for this prompt — no-op per spec.
    e.preventDefault();
    this._submitAction(action);
  }

  _submitAction(action) {
    const prompt = this.currentPrompt;
    if (!prompt) return;
    this.dispatchEvent(
      new CustomEvent("action-submitted", {
        bubbles: true,
        composed: true,
        detail: { prompt_id: prompt.prompt_id, action },
      }),
    );
    // Don't optimistically clear: spec fixture 9 requires the prompt to
    // remain rendered when the server replies with `ERROR illegal_action`.
    // A fresh inbound PROMPT (or phase transition via EVENT — wired later)
    // replaces or clears this one. Keep the visual feedback minimal here.
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

        ${this.illegalBanner
          ? html`<div class="illegal-banner">${this.illegalBanner}</div>`
          : ""}
        ${this.currentPrompt ? renderPromptBar(this.currentPrompt) : ""}

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
    // Auth state — driven by HELLO.features and AUTH_RESPONSE.
    _authRequired: { state: true }, // bool: server sent features: ["auth"]
    _authState: { state: true },    // "idle"|"waiting"|"submitting"|"authed"|"error"
    _authError: { state: true },    // null | error string shown under the form
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

    /* --- Auth form (Step 8.5) ------------------------------------------- */
    .auth-overlay {
      margin: 0 0 1rem;
      padding: 1rem 1.5rem 1.25rem;
      border: 1px solid var(--border);
      max-width: 380px;
    }
    .auth-title {
      color: var(--accent);
      margin-bottom: 0.75rem;
    }
    .auth-error {
      color: var(--error);
      margin-bottom: 0.75rem;
      padding: 0.4rem 0.75rem;
      border: 1px solid var(--error);
    }
    .auth-form-row {
      display: flex;
      flex-direction: column;
      gap: 0.25rem;
      margin-bottom: 0.6rem;
    }
    .auth-label { color: var(--fg-dim); font-size: 0.9em; }
    .auth-input {
      background: transparent;
      border: 1px solid var(--border);
      color: var(--fg);
      font-family: inherit;
      font-size: inherit;
      padding: 0.3rem 0.5rem;
      width: 100%;
      box-sizing: border-box;
    }
    .auth-input:focus {
      outline: none;
      border-color: var(--accent);
    }
    .auth-input:disabled { opacity: 0.5; }
    .auth-actions {
      display: flex;
      gap: 0.75rem;
      align-items: baseline;
      margin-top: 0.25rem;
    }
    .auth-submit {
      background: transparent;
      border: 1px solid var(--accent);
      color: var(--accent);
      font-family: inherit;
      font-size: inherit;
      padding: 0.25rem 1rem;
      cursor: pointer;
    }
    .auth-submit:hover:not(:disabled) {
      background: var(--accent);
      color: var(--bg);
    }
    .auth-submit:disabled { opacity: 0.5; cursor: default; }
    .auth-hint { color: var(--fg-dim); font-size: 0.85em; }
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
    // Auth state — see Step 8.5.
    this._authRequired = false;
    this._authState = "idle";
    this._authError = null;
    this._sessionToken = null; // stored in memory; RESUME is a v2 concern
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

        // --- Auth phase (Step 8.5) -----------------------------------------
        if (frame.kind === "HELLO") {
          // Server signals auth via HELLO.features = ["auth"].
          // Older/test servers that omit features skip straight to discovery.
          const feats = Array.isArray(frame.features) ? frame.features : [];
          if (feats.includes("auth")) {
            this._authRequired = true;
            this._authState = "waiting"; // triggers auth form render
          } else {
            // No auth required — go straight to table discovery.
            this._doTableDiscovery();
          }
          return;
        }

        if (frame.kind === "AUTH_RESPONSE") {
          if (frame.ok) {
            this._sessionToken = frame.session_token ?? null;
            this._authState = "authed";
            this._authError = null;
            this._doTableDiscovery(); // LIST_TABLES → ATTACH
          } else {
            // Server allows up to 3 attempts on the same connection; keep the
            // form open so the user can correct their credentials.
            this._authState = "error";
            this._authError = "Invalid credentials — please try again.";
          }
          return;
        }

        // --- Table discovery (Step 8.5) ------------------------------------
        if (frame.kind === "TABLE_LIST") {
          const tables = Array.isArray(frame.tables) ? frame.tables : [];
          if (tables.length > 0) {
            this._doAttach(tables[0].table_id);
          } else {
            this._doCreateTable();
          }
          return;
        }

        if (frame.kind === "TABLE_CREATED") {
          this._doAttach(frame.table_id);
          return;
        }

        // --- Gameplay (unchanged from Step 7.5) ----------------------------
        if (frame.kind === "ATTACHED" && frame.snapshot) {
          pane.setSnapshot(frame.snapshot, frame.seat ?? 0);
        } else if (frame.kind === "EVENT" && frame.event && pane.seatView) {
          // The pane's seatView is mutated by the reducer per event so the
          // ASCII layout stays current without a fresh snapshot per turn.
          const next = applyEvent(pane.seatView, frame.event, pane.ownSeat);
          pane.setSnapshot(next, pane.ownSeat);
        } else if (frame.kind === "PROMPT") {
          pane.setPrompt(frame);
        } else if (frame.kind === "ERROR" && frame.code === "illegal_action") {
          pane.showIllegalBanner(frame.message ?? "Server rejected that action — try again.");
        }
      });
      pane.addEventListener("action-submitted", (e) => {
        try {
          this._conn.send({ kind: "ACTION", ...e.detail });
        } catch (err) {
          console.warn("ACTION send failed:", err);
        }
      });
      this._conn.connect();
    });

    this.addEventListener("panes-changed", (e) => {
      this.panes = e.detail.panes;
    });
  }

  // --- Auth helpers (Step 8.5) --------------------------------------------

  _onAuthSubmit(e) {
    e.preventDefault();
    const form = e.target;
    const username = form.elements.username.value.trim();
    const password = form.elements.password.value;
    if (!username || !password) return;
    // "submitting" disables the form while we wait for AUTH_RESPONSE.
    this._authState = "submitting";
    this._authError = null;
    try {
      // Field is "password" (plaintext) — transport security via TLS/Tailscale.
      // wire-protocol.md § AUTH_REQUEST.
      this._conn.send({ kind: "AUTH_REQUEST", username, password });
    } catch (err) {
      console.warn("AUTH_REQUEST send failed:", err);
      this._authState = "waiting";
      this._authError = "Failed to send — is the server running?";
    }
  }

  // --- Table-discovery helpers (Step 8.5) ---------------------------------

  _doTableDiscovery() {
    try {
      this._conn.send({ kind: "LIST_TABLES" });
    } catch (err) {
      console.warn("LIST_TABLES send failed:", err);
    }
  }

  _doCreateTable() {
    try {
      // No ruleset override needed — server uses its configured default.
      this._conn.send({ kind: "CREATE_TABLE" });
    } catch (err) {
      console.warn("CREATE_TABLE send failed:", err);
    }
  }

  _doAttach(tableId) {
    try {
      this._conn.send({ kind: "ATTACH", table_id: tableId, seat: 0 });
    } catch (err) {
      console.warn("ATTACH send failed:", err);
    }
  }

  // --- Auth form renderer (Step 8.5) -------------------------------------

  _renderAuthForm() {
    const submitting = this._authState === "submitting";
    return html`
      <div class="auth-overlay">
        <div class="auth-title">── Sign in ──</div>
        ${this._authError
          ? html`<div class="auth-error">${this._authError}</div>`
          : ""}
        <form @submit=${this._onAuthSubmit.bind(this)}>
          <div class="auth-form-row">
            <label class="auth-label">Username</label>
            <input
              class="auth-input"
              type="text"
              name="username"
              ?disabled=${submitting}
              autocomplete="username"
              autofocus
            />
          </div>
          <div class="auth-form-row">
            <label class="auth-label">Password</label>
            <input
              class="auth-input"
              type="password"
              name="password"
              ?disabled=${submitting}
              autocomplete="current-password"
            />
          </div>
          <div class="auth-actions">
            <button class="auth-submit" type="submit" ?disabled=${submitting}>
              ${submitting ? "[ signing in… ]" : "[ Sign in ]"}
            </button>
            ${submitting
              ? ""
              : html`<span class="auth-hint">
                  (create accounts with <code>python -m mahjong account create</code>)
                </span>`}
          </div>
        </form>
      </div>
    `;
  }

  render() {
    const nextTheme = this.theme === "dark" ? "light" : "dark";
    const nextTile = this.tileStyle === "ascii" ? "unicode" : "ascii";
    // Show the auth form when the server requires auth and we haven't authed yet.
    const showAuth = this._authRequired && this._authState !== "authed";
    return html`
      <header>
        <pre>
 ╔══════════════════════════════════════════════════════════╗
 ║   Mahjong / 麻将        — web client, step 8.5           ║
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
      ${showAuth ? this._renderAuthForm() : ""}
      <table-page .panes=${this.panes} .tileStyle=${this.tileStyle}></table-page>
    `;
  }
}

customElements.define("mahjong-app", MahjongApp);

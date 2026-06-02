// Web client entry — Step 8.7.e multi-human lobby + START_HAND.
//
// <game-pane> renders a SeatView (state-schema.md § Per-seat projection)
// as an ASCII table, mutates it per inbound EVENT, and — when a PROMPT is
// outstanding — renders a prompt bar listing the legal actions with their
// key bindings. Keystrokes get translated to ACTION frames and sent back.
//
// Step 8.5 added: auth form, AUTH_REQUEST/AUTH_RESPONSE, and post-auth
// table discovery (LIST_TABLES → CREATE_TABLE if needed → ATTACH seat 0).
//
// Step 8.7.e adds:
// - `?humans=N` URL param (1-4, default 1) — used as the CREATE_TABLE
//   composition (N humans + 4-N CannedAdapter bots).
// - Prefer joining an existing table with an open human seat over
//   creating a fresh one (open-lobby model — multi-human-seats.md).
// - Send `START_HAND` after the local ATTACHED arrives. Hand-loop
//   ignition no longer auto-fires on ATTACH after Step 8.7.d.
// - `humans_not_ready` → poll `LIST_TABLES` every 2s; when the table's
//   `seats[]` show every `kind:"human"` seat as `occupied:true`, re-send
//   `START_HAND`. `hand_already_started` is a benign race (another
//   human at the same table got there first); treat as silent no-op.

import { LitElement, html, css } from "lit";
import { renderTable, renderPinwheel, renderHandEndSummary } from "/static/render.js";
import { applyEvent } from "/static/apply_event.js";
import { renderPromptBar, actionForKey, tileIndexForKeyCode, isClaimAvailable } from "/static/prompt.js";
import "/static/feedback.js";

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

      /* Pinwheel widget (Step 8.9 — cardinal-ui.md).  Compact 3×3 grid
       * answering "whose just discarded" + "which tile" at a glance.
       * Sits in the top-right of the game pane; the unicode tile in the
       * center is the visual anchor and is rendered large. */
      .pinwheel-wrap {
        position: relative;
      }
      .pinwheel {
        position: absolute;
        top: 0;
        right: 0;
        display: grid;
        grid-template-columns: repeat(3, auto);
        grid-gap: 0.1rem 0.5rem;
        padding: 0.35rem 0.6rem;
        border: 1px dashed var(--border);
        border-radius: 3px;
        line-height: 1.1;
        text-align: center;
        background: var(--bg);
      }
      .pinwheel .pw-cell {
        min-width: 1.5ch;
        padding: 0 0.1rem;
      }
      .pinwheel .pw-badge {
        color: var(--fg-dim);
        font-size: 0.95em;
      }
      .pinwheel .pw-badge.own {
        color: var(--accent);
        font-weight: bold;
        text-decoration: underline;
      }
      .pinwheel .pw-badge.active {
        color: var(--accent-red);
        font-weight: bold;
      }
      /* When the own seat is also the active discarder, the active
       * (accent-red) color wins so it's clearly distinct from idle. */
      .pinwheel .pw-badge.own.active {
        color: var(--accent-red);
        text-decoration: underline;
      }
      .pinwheel .pw-mid {
        display: flex;
        flex-direction: column;
        align-items: center;
        justify-content: center;
        gap: 0.15rem;
      }
      .pinwheel .pw-arrow {
        font-size: 1.6em;
        color: var(--accent);
        line-height: 1;
      }
      .pinwheel .pw-last-discard {
        line-height: 1;
        min-height: 2.6em;
        display: flex;
        align-items: center;
        justify-content: center;
      }
      .pinwheel .pw-last-discard.pw-empty {
        color: var(--fg-dim);
        font-size: 1.4em;
      }
      /* The pinwheel's last-discard tile is the main visual anchor.
       * Override the table's default 1.8em with a much larger glyph so
       * the unicode tile reads at a glance from across the room. */
      .pinwheel .tile {
        font-size: 2.6em;
      }
      .pinwheel .tile.dragon,
      .pinwheel .tile.face-down {
        font-size: 2.6em;
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
      /* Discard pile is high-frequency background info — render it smaller
       * than the hand so attention stays on the concealed tiles (Spec 22
       * § 22.4). Dragons / face-down keep a slightly larger ratio. */
      .discard-row .tile { font-size: 1.2em; }
      .discard-row .tile.dragon,
      .discard-row .tile.face-down { font-size: 1.45em; }
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

      /* --- Layer-8 §1 hand-display polish.  Per-tile modifier wrappers
       * for the local player's concealed hand: selection cursor,
       * just-drawn offset, and suit-group break. */
      .tile-mod { display: inline; }
      /* Selection cue: a translucent accent-tint box rather than an
       * underline. The underline didn't render reliably under the unicode
       * mahjong glyphs (Spec 22 § 22.3); a background box reads under both
       * ASCII and unicode and on both themes. color-mix keeps it derived
       * from --accent so it follows theme swaps without a parallel var. */
      .tile-mod.selected {
        background-color: color-mix(in srgb, var(--accent) 22%, transparent);
        border-radius: 0.15em;
        padding: 0 0.1em;
        font-weight: 600;
      }
      .tile-mod.just-drawn { margin-left: 1.2em; }
      .tile-mod.suit-break { margin-left: 0.5em; }

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

      /* --- Claim-available alert (§22.2). When a CLAIM_WINDOW prompt offers
       * a real (non-PASS) option, the bar pulses and a chip pins to the pane
       * header so the cue survives the player glancing at another tab.
       * Sound is a future enhancement (see spec). */
      .prompt-bar.claim-active {
        animation: claim-pulse 1s ease-in-out infinite alternate;
      }
      @keyframes claim-pulse {
        from { border-color: var(--accent); }
        to   { border-color: var(--accent-red); }
      }
      @media (prefers-reduced-motion: reduce) {
        .prompt-bar.claim-active { animation: none; border-color: var(--accent-red); }
      }
      .claim-chip {
        margin: 0.25rem 0 0;
        color: var(--accent-red);
        font-weight: 600;
        letter-spacing: 0.05em;
        animation: chip-pulse 1s ease-in-out infinite alternate;
      }
      @keyframes chip-pulse {
        from { opacity: 0.45; }
        to   { opacity: 1; }
      }
      @media (prefers-reduced-motion: reduce) {
        .claim-chip { animation: none; }
      }

      /* --- Hand-end summary (§22.9). Modular sections stacked vertically. */
      .hand-end-summary {
        margin: 0.5rem 0;
        padding: 0.5rem 0.75rem;
        border: 1px solid var(--accent);
        border-left-width: 3px;
        display: flex;
        flex-direction: column;
        gap: 0.5rem;
      }
      .he-headline { font-size: 1.1em; }
      .he-winner { color: var(--accent); font-weight: 600; }
      .he-section-title { color: var(--fg-dim); margin-bottom: 0.15rem; }
      .he-fan-row, .he-score-row {
        display: flex;
        justify-content: space-between;
        max-width: 22rem;
      }
      .he-fan-total { border-top: 1px dashed var(--border); font-weight: 600; }
      .he-fan-value, .he-score-delta { color: var(--accent); }
      .he-score-row.he-winner .he-score-name,
      .he-score-row.he-winner .he-score-delta { color: var(--accent); font-weight: 600; }
      .he-hand-row { margin: 0.1rem 0; }
      .he-hand-name { color: var(--fg-dim); margin-right: 0.4rem; }
      .he-hand-melds { margin-left: 0.6rem; }

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
    // selectedTile threaded into renderTable so the renderer can mark the
    // cursor tile with .selected — see §1 of layer8-closeout.md.
    const tableContent = this.seatView
      ? renderTable(this.seatView, this.ownSeat, {
          tileStyle: this.tileStyle,
          selectedTile: this.selectedTile,
        })
      : null;
    const pinwheel = this.seatView
      ? renderPinwheel(this.seatView, this.ownSeat, { tileStyle: this.tileStyle })
      : null;
    const handEndSummary = this.seatView?.terminal
      ? renderHandEndSummary(this.seatView, this.ownSeat, { tileStyle: this.tileStyle })
      : null;

    const claimAvailable = isClaimAvailable(this.currentPrompt);
    return html`
      <div class="pane">
        ${paneHeader("Game pane", null, null)}
        ${claimAvailable
          ? html`<div class="claim-chip">[ CLAIM AVAILABLE ]</div>`
          : ""}
        <div class="status ${this.status}">Connection: ${this.status}</div>
        ${tableContent !== null
          ? html`<div class="table-ascii pinwheel-wrap">
              ${pinwheel}
              ${tableContent}
            </div>`
          : html`<div class="waiting">(waiting for ATTACHED snapshot…)</div>`}

        ${handEndSummary ?? ""}

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

// --- <lobby-view> -------------------------------------------------------
//
// Post-auth landing page.  Lists active tables (one block each) with a
// per-open-seat Join button.  Below the list, a composition picker +
// Create button.  Auto-refreshes every 2s so seat occupancy updates
// without manual interaction; a manual Refresh button is also available.

const PHASE_LABEL_LOBBY = {
  WAITING_FOR_PLAYERS: "waiting for players",
  IN_PROGRESS: "in progress",
};

class LobbyView extends LitElement {
  static properties = {
    tables: { type: Array },
    desiredHumans: { type: Number },
    lastRefreshTs: { type: Number, state: true },
    busy: { type: String, state: true }, // null | "joining" | "creating"
    // §22.6 Part A — table creation options (collapsed by default).
    showAdvanced: { state: true },
    pacingPreset: { state: true },       // "fast" | "normal" | "slow" | "custom"
    customMin: { state: true },
    customMax: { state: true },
    decideTimeout: { state: true },
    timeoutsEnabled: { state: true },
  };

  static styles = css`
    :host { display: block; color: var(--fg); }
    .lobby {
      border: 1px solid var(--border);
      padding: 0.75rem 1rem 1rem;
      margin-bottom: 1rem;
    }
    .lobby-title {
      color: var(--accent);
      margin-bottom: 0.75rem;
    }
    .lobby-section-title {
      color: var(--accent);
      margin: 0.75rem 0 0.4rem;
    }
    .table-block {
      border: 1px solid var(--border);
      padding: 0.5rem 0.75rem;
      margin-bottom: 0.5rem;
    }
    .table-meta {
      color: var(--fg-dim);
      margin-bottom: 0.4rem;
    }
    .seat-row {
      display: flex;
      align-items: baseline;
      gap: 0.75rem;
      padding: 0.1rem 0;
    }
    .seat-label { color: var(--fg-dim); min-width: 8ch; }
    .seat-kind { min-width: 6ch; }
    .seat-occupied { color: var(--accent); }
    .seat-open { color: var(--fg-dim); }
    .seat-bot { color: var(--fg-dim); }
    .seat-join {
      background: transparent;
      border: 1px solid var(--accent);
      color: var(--accent);
      font-family: inherit;
      font-size: 0.9em;
      padding: 0.1rem 0.6rem;
      cursor: pointer;
      margin-left: auto;
    }
    .seat-join:hover:not(:disabled) {
      background: var(--accent);
      color: var(--bg);
    }
    .seat-join:disabled { opacity: 0.4; cursor: default; }
    .empty {
      color: var(--fg-dim);
      padding: 0.5rem 0;
    }
    .pick-row {
      display: flex;
      gap: 0.4rem;
      align-items: baseline;
      margin: 0.4rem 0;
      flex-wrap: wrap;
    }
    .pick-label { color: var(--fg-dim); }
    .pick-btn {
      background: transparent;
      border: 1px solid var(--border);
      color: var(--fg);
      font-family: inherit;
      font-size: inherit;
      padding: 0.15rem 0.7rem;
      cursor: pointer;
    }
    .pick-btn.selected {
      border-color: var(--accent);
      color: var(--accent);
    }
    .pick-btn:hover:not(.selected) { color: var(--accent); }
    /* §22.6 Part A — advanced table-creation options. */
    .adv-options { margin: 0.4rem 0; }
    .adv-toggle {
      background: transparent;
      border: none;
      color: var(--fg-dim);
      font-family: inherit;
      font-size: inherit;
      cursor: pointer;
      padding: 0;
    }
    .adv-toggle:hover:not(:disabled) { color: var(--accent); }
    .adv-body {
      margin: 0.3rem 0 0 1rem;
      display: flex;
      flex-direction: column;
      gap: 0.35rem;
    }
    .adv-row { display: flex; flex-wrap: wrap; align-items: center; gap: 0.5rem; }
    .adv-label { color: var(--fg-dim); }
    .adv-radio { color: var(--fg); display: inline-flex; align-items: center; gap: 0.2rem; }
    .adv-custom input,
    .adv-row input[type="number"] {
      width: 4rem;
      background: var(--bg);
      color: var(--fg);
      border: 1px solid var(--border);
      font-family: inherit;
    }
    .lobby-actions {
      display: flex;
      gap: 0.75rem;
      margin-top: 0.4rem;
      align-items: baseline;
    }
    .lobby-btn {
      background: transparent;
      border: 1px solid var(--accent);
      color: var(--accent);
      font-family: inherit;
      font-size: inherit;
      padding: 0.2rem 0.8rem;
      cursor: pointer;
    }
    .lobby-btn:hover:not(:disabled) {
      background: var(--accent);
      color: var(--bg);
    }
    .lobby-btn:disabled { opacity: 0.4; cursor: default; }
    .refresh-hint { color: var(--fg-dim); font-size: 0.85em; }
  `;

  constructor() {
    super();
    this.tables = [];
    this.desiredHumans = 1;
    this.lastRefreshTs = 0;
    this.busy = null;
    this.showAdvanced = false;
    this.pacingPreset = "normal";
    this.customMin = 5.0;
    this.customMax = 10.0;
    this.decideTimeout = 60;
    this.timeoutsEnabled = true;
  }

  _emit(name, detail) {
    this.dispatchEvent(new CustomEvent(name, { detail, bubbles: true, composed: true }));
  }

  // Build the CREATE_TABLE.options object from the advanced controls.
  // Returns null when every control is at its default (keeps the wire
  // message minimal and lets the server apply its own defaults).
  _buildOptions() {
    const atDefault =
      this.pacingPreset === "normal" && this.decideTimeout === 60 && this.timeoutsEnabled;
    if (atDefault) return null;
    const options = {};
    options.bot_pacing =
      this.pacingPreset === "custom"
        ? { min_s: Number(this.customMin), max_s: Number(this.customMax) }
        : this.pacingPreset;
    options.timeouts_enabled = this.timeoutsEnabled;
    if (this.timeoutsEnabled) options.decide_timeout_seconds = Number(this.decideTimeout);
    return options;
  }

  _onJoin(tableId, seat) {
    if (this.busy) return;
    this.busy = "joining";
    this._emit("lobby-join", { tableId, seat });
  }

  _onCreate() {
    if (this.busy) return;
    this.busy = "creating";
    this._emit("lobby-create", { humans: this.desiredHumans, options: this._buildOptions() });
  }

  _onRefresh() {
    this._emit("lobby-refresh", {});
  }

  _pickHumans(n) {
    this.desiredHumans = n;
  }

  _renderSeat(seat) {
    if (seat.kind === "bot") {
      return html`
        <div class="seat-row">
          <span class="seat-label">Seat ${seat.seat}</span>
          <span class="seat-kind">bot</span>
          <span class="seat-bot">${seat.bot_id ?? "canned-pass"}</span>
        </div>
      `;
    }
    // Human seat.
    if (seat.occupied) {
      return html`
        <div class="seat-row">
          <span class="seat-label">Seat ${seat.seat}</span>
          <span class="seat-kind">human</span>
          <span class="seat-occupied">${seat.user_id ?? "occupied"}</span>
        </div>
      `;
    }
    // Open human seat.  When the table's hand is IN_PROGRESS, late-join
    // is refused server-side (Layer-8 §4) — the lobby suppresses the Join
    // affordance and marks the row "in progress" so the user understands
    // why they can't sit down.
    if (seat.table_phase === "IN_PROGRESS") {
      return html`
        <div class="seat-row">
          <span class="seat-label">Seat ${seat.seat}</span>
          <span class="seat-kind">human</span>
          <span class="seat-open">open (in progress — wait for next hand)</span>
        </div>
      `;
    }
    return html`
      <div class="seat-row">
        <span class="seat-label">Seat ${seat.seat}</span>
        <span class="seat-kind">human</span>
        <span class="seat-open">open</span>
        <button
          class="seat-join"
          ?disabled=${!!this.busy}
          @click=${() => this._onJoin(seat.table_id, seat.seat)}
        >
          [ Join ]
        </button>
      </div>
    `;
  }

  _renderAdvancedOptions() {
    const presets = ["fast", "normal", "slow", "custom"];
    return html`
      <div class="adv-options">
        <button
          class="adv-toggle"
          ?disabled=${!!this.busy}
          @click=${() => (this.showAdvanced = !this.showAdvanced)}
        >
          ${this.showAdvanced ? "▼" : "▶"} Options (advanced)
        </button>
        ${this.showAdvanced
          ? html`
              <div class="adv-body">
                <div class="adv-row">
                  <span class="adv-label">Bot pacing:</span>
                  ${presets.map(
                    (p) => html`
                      <label class="adv-radio">
                        <input
                          type="radio"
                          name="pacing"
                          .checked=${this.pacingPreset === p}
                          ?disabled=${!!this.busy}
                          @change=${() => (this.pacingPreset = p)}
                        />${p}
                      </label>
                    `,
                  )}
                  ${this.pacingPreset === "custom"
                    ? html`<span class="adv-custom"
                        ><input
                          type="number"
                          min="0"
                          max="60"
                          step="0.5"
                          .value=${String(this.customMin)}
                          @input=${(e) => (this.customMin = e.target.value)}
                        />–<input
                          type="number"
                          min="0"
                          max="60"
                          step="0.5"
                          .value=${String(this.customMax)}
                          @input=${(e) => (this.customMax = e.target.value)}
                        />s</span
                      >`
                    : ""}
                </div>
                <div class="adv-row">
                  <span class="adv-label">Decide time:</span>
                  <input
                    type="number"
                    min="5"
                    max="600"
                    step="5"
                    .value=${String(this.decideTimeout)}
                    ?disabled=${!this.timeoutsEnabled || !!this.busy}
                    @input=${(e) => (this.decideTimeout = e.target.value)}
                  />
                  <span class="adv-label">seconds per discard prompt</span>
                </div>
                <div class="adv-row">
                  <label class="adv-radio">
                    <input
                      type="checkbox"
                      .checked=${this.timeoutsEnabled}
                      ?disabled=${!!this.busy}
                      @change=${(e) => (this.timeoutsEnabled = e.target.checked)}
                    />Use time limits (uncheck → no deadline on this table)
                  </label>
                </div>
              </div>
            `
          : ""}
      </div>
    `;
  }

  _renderTable(t) {
    const seats = (t.seats ?? []).map((s) => ({
      ...s,
      table_id: t.table_id,
      table_phase: t.phase,
    }));
    const phase = PHASE_LABEL_LOBBY[t.phase] ?? t.phase ?? "?";
    return html`
      <div class="table-block">
        <div class="table-meta">
          Table ${t.table_id} · ${t.ruleset ?? "mcr-2006"} · ${phase} · hand ${(t.hand_index ?? 0) + 1}
        </div>
        ${seats.map((s) => this._renderSeat(s))}
      </div>
    `;
  }

  render() {
    const tables = Array.isArray(this.tables) ? this.tables : [];
    return html`
      <div class="lobby">
        <div class="lobby-title">── Lobby ──</div>

        <div class="lobby-section-title">Active tables (${tables.length})</div>
        ${tables.length === 0
          ? html`<div class="empty">No active tables. Create one below to get started.</div>`
          : tables.map((t) => this._renderTable(t))}

        <div class="lobby-section-title">Create a new table</div>
        <div class="pick-row">
          <span class="pick-label">Humans:</span>
          ${[1, 2, 3, 4].map(
            (n) => html`
              <button
                class="pick-btn ${this.desiredHumans === n ? "selected" : ""}"
                ?disabled=${!!this.busy}
                @click=${() => this._pickHumans(n)}
              >
                ${n}
              </button>
            `,
          )}
          <span class="pick-label">+ ${4 - this.desiredHumans} canned-pass bot${4 - this.desiredHumans === 1 ? "" : "s"}</span>
        </div>
        ${this._renderAdvancedOptions()}
        <div class="lobby-actions">
          <button
            class="lobby-btn"
            ?disabled=${!!this.busy}
            @click=${this._onCreate}
          >
            ${this.busy === "creating" ? "[ creating… ]" : "[ Create table ]"}
          </button>
          <button
            class="lobby-btn"
            ?disabled=${!!this.busy}
            @click=${this._onRefresh}
          >
            [ Refresh ]
          </button>
          <span class="refresh-hint">(auto-refreshes every 2s)</span>
        </div>
      </div>
    `;
  }
}

customElements.define("lobby-view", LobbyView);

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

// Read `?humans=N` from the URL, clamped to 1..4.  Default 1 keeps the
// pre-8.7.e single-human flow when the user doesn't ask for anything else.
function _readDesiredHumans() {
  try {
    const raw = new URL(location.href).searchParams.get("humans");
    if (raw == null) return 1;
    const n = Number.parseInt(raw, 10);
    if (Number.isFinite(n) && n >= 1 && n <= 4) return n;
  } catch {
    // ignore — malformed URL or no URL API; fall through to default.
  }
  return 1;
}

// Compose a `seats: [...]` payload for CREATE_TABLE from a human count.
// 1 human → [H, B, B, B]; 2 → [H, H, B, B]; etc.
function _seatsForHumanCount(n) {
  return Array.from({ length: 4 }, (_, i) => ({ kind: i < n ? "human" : "bot" }));
}

// Pick a table from a TABLE_LIST response that we can join right now: the
// first table with any `kind:"human"` seat that isn't occupied.  Returns
// `{tableId, seat}` or `null` if no such opening exists.
function _findOpenHumanSeat(tables) {
  if (!Array.isArray(tables)) return null;
  for (const t of tables) {
    const seats = Array.isArray(t.seats) ? t.seats : [];
    for (const s of seats) {
      if (s.kind === "human" && !s.occupied) {
        return { tableId: t.table_id, seat: s.seat };
      }
    }
  }
  return null;
}

// Did the TABLE_LIST entry for *our* table show every human seat occupied?
// Used by the START_HAND retry loop after a `humans_not_ready` error.
function _allHumansOccupied(tables, tableId) {
  const t = (tables ?? []).find((row) => row.table_id === tableId);
  if (!t || !Array.isArray(t.seats)) return false;
  const humans = t.seats.filter((s) => s.kind === "human");
  return humans.length > 0 && humans.every((s) => s.occupied);
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
    // Lobby vs. in-game view.
    _view: { state: true },         // "lobby" | "table"
    _lobbyTables: { state: true },  // array of TABLE_LIST.tables entries
    _lobbyHumans: { state: true },  // current composition pick (1..4)
    _lobbyError: { state: true },   // null | error string above the table list
    _sessionToken: { state: true }, // drives <feedback-button> visibility
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
    // Lobby state — see Step 8.7.e.
    this._desiredHumans = _readDesiredHumans(); // 1..4 from ?humans=N
    this._attachedTableId = null;               // populated on ATTACHED
    this._attachedSeat = null;
    this._lobbyPollHandle = null;               // setTimeout id while waiting (humans_not_ready)
    this._handStarted = false;                  // first EVENT clears the lobby state
    // Lobby view (post-auth landing).
    this._view = "lobby";
    this._lobbyTables = [];
    this._lobbyHumans = this._desiredHumans;
    this._lobbyError = null;
    this._lobbyAutoRefresh = null;              // setInterval id while in lobby view
    this._lobbyTargetSeat = null;               // seat we're attempting to join (debug/diagnostic)
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
          // Older/test servers that omit features skip straight to lobby.
          const feats = Array.isArray(frame.features) ? frame.features : [];
          if (feats.includes("auth")) {
            this._authRequired = true;
            this._authState = "waiting"; // triggers auth form render
          } else {
            // No auth required — go straight to lobby.
            this._enterLobby();
          }
          return;
        }

        if (frame.kind === "AUTH_RESPONSE") {
          if (frame.ok) {
            this._sessionToken = frame.session_token ?? null;
            this._authState = "authed";
            this._authError = null;
            this._enterLobby();
          } else {
            // Server allows up to 3 attempts on the same connection; keep the
            // form open so the user can correct their credentials.
            this._authState = "error";
            this._authError = "Invalid credentials — please try again.";
          }
          return;
        }

        // --- Feedback (Spec 23) --------------------------------------------
        if (frame.kind === "FEEDBACK_ACK") {
          this._feedbackResult(true);
          return;
        }
        if (frame.kind === "ERROR" && frame.code === "feedback_error") {
          this._feedbackResult(false, frame.message);
          return;
        }

        // --- TABLE_LIST routing --------------------------------------------
        if (frame.kind === "TABLE_LIST") {
          const tables = Array.isArray(frame.tables) ? frame.tables : [];
          // In lobby view: refresh the displayed table list.  No auto-join.
          if (this._view === "lobby") {
            this._lobbyTables = tables;
            return;
          }
          // In table view: this TABLE_LIST is the humans_not_ready poll
          // response.  If every human seat is now occupied, retry
          // START_HAND; otherwise schedule another poll in 2s.
          if (this._attachedTableId !== null && !this._handStarted) {
            if (_allHumansOccupied(tables, this._attachedTableId)) {
              this._lobbyPollHandle = null;
              this._doStartHand();
            } else if (this._lobbyPollHandle === null) {
              this._lobbyPollHandle = setTimeout(() => this._doTableDiscovery(), 2000);
            }
          }
          return;
        }

        if (frame.kind === "TABLE_CREATED") {
          // The user just clicked Create in the lobby — auto-attach to
          // seat 0 (the first human slot in any composition we emit) and
          // transition to the table view.
          this._enterTableView();
          this._doAttach(frame.table_id, 0);
          return;
        }

        // --- Gameplay (unchanged from Step 7.5) ----------------------------
        if (frame.kind === "ATTACHED" && frame.snapshot) {
          pane.setSnapshot(frame.snapshot, frame.seat ?? 0);
          this._attachedTableId = frame.table_id ?? this._attachedTableId;
          this._attachedSeat = frame.seat ?? this._attachedSeat;
          // Transition out of the lobby (idempotent — repeated ATTACHED on
          // a between-hand transition keeps us in table view).
          this._enterTableView();
          // Step 8.7.d: ignition no longer rides ATTACH; we must ask.
          this._doStartHand();
        } else if (frame.kind === "EVENT" && frame.event && pane.seatView) {
          // The pane's seatView is mutated by the reducer per event so the
          // ASCII layout stays current without a fresh snapshot per turn.
          const next = applyEvent(pane.seatView, frame.event, pane.ownSeat);
          pane.setSnapshot(next, pane.ownSeat);
          // First EVENT means the hand is actually running; cancel any
          // lobby polling that was still in flight.
          this._handStarted = true;
          if (this._lobbyPollHandle !== null) {
            clearTimeout(this._lobbyPollHandle);
            this._lobbyPollHandle = null;
          }
        } else if (frame.kind === "PROMPT") {
          pane.setPrompt(frame);
        } else if (frame.kind === "ERROR" && frame.code === "illegal_action") {
          pane.showIllegalBanner(frame.message ?? "Server rejected that action — try again.");
        } else if (frame.kind === "ERROR" && frame.code === "humans_not_ready") {
          // Not everyone is seated yet — poll the lobby and retry when the
          // human-seat occupancy is complete.  We don't show this to the
          // user; the existing "waiting for ATTACHED snapshot…" placeholder
          // already conveys "we're not in a hand yet."
          if (this._lobbyPollHandle === null && !this._handStarted) {
            this._lobbyPollHandle = setTimeout(() => this._doTableDiscovery(), 2000);
          }
        } else if (frame.kind === "ERROR" && frame.code === "hand_already_started") {
          // Another LIVE human at this table won the START_HAND race; the
          // server is already feeding us the hand events.  No-op.
        } else if (
          frame.kind === "ERROR" &&
          this._view === "lobby" &&
          (frame.code === "table_unknown" ||
            frame.code === "seat_occupied" ||
            frame.code === "seat_not_yours" ||
            frame.code === "shutting_down" ||
            frame.code === "framing")
        ) {
          // The lobby's join/create attempt failed.  Surface the reason
          // and let the auto-refresh repopulate so the user can pick again.
          const lobby = this.renderRoot.querySelector("lobby-view");
          if (lobby) lobby.busy = null;
          this._lobbyError = `${frame.code}${frame.message ? `: ${frame.message}` : ""}`;
          this._doTableDiscovery();
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

  _doCreateTable(humans, options = null) {
    // The lobby panel passes the chosen composition; falls back to the
    // URL-param default when called from auto-flows.
    const n = Number.isFinite(humans) ? humans : this._desiredHumans;
    const msg = { kind: "CREATE_TABLE", seats: _seatsForHumanCount(n) };
    if (options) msg.options = options; // §22.6 Part A; omit → server defaults
    try {
      this._conn.send(msg);
    } catch (err) {
      console.warn("CREATE_TABLE send failed:", err);
    }
  }

  // --- Lobby / table-view transitions (Step 8.7.e+ lobby UI) -------------

  _enterLobby() {
    this._view = "lobby";
    this._lobbyError = null;
    // Reset any stale attach state — lobby is the "between-tables" view.
    this._attachedTableId = null;
    this._attachedSeat = null;
    this._handStarted = false;
    if (this._lobbyPollHandle !== null) {
      clearTimeout(this._lobbyPollHandle);
      this._lobbyPollHandle = null;
    }
    // Kick off auto-refresh: send LIST_TABLES immediately and every 2s.
    this._doTableDiscovery();
    if (this._lobbyAutoRefresh === null) {
      this._lobbyAutoRefresh = setInterval(() => {
        if (this._view === "lobby") this._doTableDiscovery();
      }, 2000);
    }
  }

  _enterTableView() {
    this._view = "table";
    if (this._lobbyAutoRefresh !== null) {
      clearInterval(this._lobbyAutoRefresh);
      this._lobbyAutoRefresh = null;
    }
  }

  _onLobbyJoin(e) {
    const { tableId, seat } = e.detail;
    if (tableId == null || seat == null) return;
    this._lobbyError = null;
    this._lobbyTargetSeat = { tableId, seat };
    // We send ATTACH now; on success the ATTACHED handler will transition
    // to table view.  On error (seat_occupied etc.), the ERROR handler
    // resets the lobby and surfaces the message.
    this._doAttach(tableId, seat);
  }

  _onLobbyCreate(e) {
    const { humans, options } = e.detail;
    if (Number.isFinite(humans) && humans >= 1 && humans <= 4) {
      this._lobbyHumans = humans;
    }
    this._lobbyError = null;
    this._doCreateTable(this._lobbyHumans, options ?? null);
  }

  _onLobbyRefresh() {
    this._lobbyError = null;
    this._doTableDiscovery();
  }

  _doAttach(tableId, seat) {
    try {
      this._conn.send({ kind: "ATTACH", table_id: tableId, seat });
    } catch (err) {
      console.warn("ATTACH send failed:", err);
    }
  }

  _doStartHand() {
    // Idempotent on the server side: extra sends return `hand_already_started`
    // which we treat as a no-op in the message handler.
    if (this._attachedTableId == null) return;
    try {
      this._conn.send({ kind: "START_HAND", table_id: this._attachedTableId });
    } catch (err) {
      console.warn("START_HAND send failed:", err);
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
    const showLobby = !showAuth && this._view === "lobby";
    return html`
      <header>
        <pre>
 ╔══════════════════════════════════════════════════════════╗
 ║   Mahjong / 麻将        — web client                     ║
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
      ${showLobby
        ? html`
            ${this._lobbyError
              ? html`<div class="auth-error">${this._lobbyError}</div>`
              : ""}
            <lobby-view
              .tables=${this._lobbyTables}
              .desiredHumans=${this._lobbyHumans}
              @lobby-join=${this._onLobbyJoin.bind(this)}
              @lobby-create=${this._onLobbyCreate.bind(this)}
              @lobby-refresh=${this._onLobbyRefresh.bind(this)}
            ></lobby-view>
          `
        : ""}
      <table-page
        .panes=${this.panes}
        .tileStyle=${this.tileStyle}
        ?hidden=${showLobby || showAuth}
      ></table-page>
      <feedback-button
        .sessionToken=${this._sessionToken}
        @feedback-submit=${this._onFeedbackSubmit}
      ></feedback-button>
    `;
  }

  _onFeedbackSubmit(e) {
    // Child <feedback-button> validated locally; relay over the WS connection.
    const { type, text } = e.detail;
    try {
      this._conn.send({ kind: "FEEDBACK", type, text });
    } catch {
      this._feedbackResult(false, "Not connected. Please try again.");
    }
  }

  _feedbackResult(ok, message) {
    const btn = this.renderRoot.querySelector("feedback-button");
    if (btn) btn.onResult(ok, message);
  }
}

customElements.define("mahjong-app", MahjongApp);

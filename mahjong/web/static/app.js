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
import {
  renderTable,
  renderMinimal,
  renderPinwheel,
  renderHandEndSummary,
  renderScoreGraph,
  concealedDisplayOrder,
} from "/static/render.js";
import { applyEvent } from "/static/apply_event.js";
import { audioCues, cueForEvent, cueForPrompt, cueForTerminal } from "/static/audio.js";
import {
  renderPromptBar,
  actionForKey,
  chiOptions,
  tileIndexForKeyCode,
  isClaimAvailable,
} from "/static/prompt.js";
import { renderStatsDetail } from "/static/stats.js";
import { SETTINGS } from "/static/settings.js";
import "/static/feedback.js";

// True when the keydown originated in an editable element (text input,
// textarea, contentEditable) — so global game shortcuts can stand down and let
// the user type. composedPath()[0] pierces shadow DOM: at window level e.target
// is retargeted to the host, but the real focused node is first in the path.
function isEditableTarget(e) {
  const node = e.composedPath?.()[0] ?? e.target;
  if (!node || node.nodeType !== 1) return false;
  const tag = node.tagName;
  return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || node.isContentEditable === true;
}

// --- ConnectionManager --------------------------------------------------

const SUBPROTOCOL = "mahjong-v1";

// Auto-reconnect tuning: exponential backoff, capped. The server sends HELLO
// on (re)connect; the app re-authenticates with the stored session token via
// RESUME, so a dropped socket (tunnel warm-up, Wi-Fi↔cellular handoff, sleep)
// recovers without a manual reload.
const RECONNECT_BASE_MS = 500;
const RECONNECT_MAX_MS = 10000;

class ConnectionManager extends EventTarget {
  constructor(url) {
    super();
    this.url = url;
    this.ws = null;
    this._shouldReconnect = true; // false after a deliberate close()
    this._reconnectDelay = RECONNECT_BASE_MS;
    this._reconnectTimer = null;
  }

  connect() {
    this.ws = new WebSocket(this.url, SUBPROTOCOL);
    this.ws.addEventListener("open", () => {
      this._reconnectDelay = RECONNECT_BASE_MS; // reset backoff on a good connect
      this.dispatchEvent(new Event("open"));
    });
    this.ws.addEventListener("close", (e) => {
      this.dispatchEvent(new CustomEvent("close", { detail: { code: e.code, reason: e.reason } }));
      this._scheduleReconnect();
    });
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

  _scheduleReconnect() {
    // Don't reconnect after a deliberate close, and never stack timers.
    if (!this._shouldReconnect || this._reconnectTimer !== null) return;
    const delay = this._reconnectDelay;
    this.dispatchEvent(new CustomEvent("reconnecting", { detail: { delay } }));
    this._reconnectTimer = setTimeout(() => {
      this._reconnectTimer = null;
      this.connect();
    }, delay);
    this._reconnectDelay = Math.min(this._reconnectDelay * 2, RECONNECT_MAX_MS);
  }

  send(message) {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
      throw new Error("WebSocket not open");
    }
    this.ws.send(JSON.stringify(message));
  }

  close(code = 1000) {
    // Deliberate teardown: stop auto-reconnecting and cancel any pending retry.
    this._shouldReconnect = false;
    if (this._reconnectTimer !== null) {
      clearTimeout(this._reconnectTimer);
      this._reconnectTimer = null;
    }
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
    viewMode: { type: String },
    discardLayout: { type: String },
    frames: { state: true },
    showLog: { state: true },
    currentPrompt: { state: true },
    selectedTile: { state: true },
    chiChoosing: { state: true },
    illegalBanner: { state: true },
    readySent: { state: true },
    readyState: { state: true },
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
      .he-tenpai-row {
        margin: 0.1rem 0;
        display: flex;
        flex-wrap: wrap;
        gap: 0.4rem;
        align-items: baseline;
      }
      .he-tenpai-name { color: var(--fg-dim); }
      .he-tenpai-state { font-weight: 600; }
      .he-wait { white-space: nowrap; }
      .he-subfloor { opacity: 0.55; }

      /* --- Between-hand ready area (FB-02 button + FB-19 live roster). */
      .ready-area {
        margin-top: 0.5rem;
        display: flex;
        flex-direction: column;
        gap: 0.35rem;
      }
      .ready-waiting { color: var(--fg-dim); }
      .ready-btn {
        font-family: inherit;
        cursor: pointer;
        color: var(--accent);
        background: transparent;
        border: 1px solid var(--accent);
        padding: 0.25rem 0.75rem;
        align-self: flex-start;
      }
      .ready-roster { display: flex; flex-wrap: wrap; gap: 0.6rem; }
      .ready-seat { white-space: nowrap; }
      .ready-yes { color: var(--accent); }
      .ready-no { color: var(--fg-dim); }

      /* --- Illegal-action banner (transient). The prompt stays open. */
      .illegal-banner {
        margin: 0.5rem 0;
        padding: 0.4rem 0.75rem;
        border: 1px solid var(--error);
        color: var(--error);
      }

      /* --- Minimal view (minimal-play-view.md). Large print, decluttered:
       * only whose-turn, the large last discard, each player's melds +
       * flowers + score, a combined discard pond, and your own hand. */
      .minimal-wrap {
        margin: 0.25rem 0 0.75rem;
        font-size: 1.1rem; /* large print baseline; tiles scale via em */
      }
      .mv {
        display: flex;
        flex-direction: column;
        gap: 0.6rem;
      }
      /* Whose-turn banner — the headline cue. */
      .mv-turn {
        font-size: 1.5em;
        font-weight: 600;
        text-align: center;
        padding: 0.3rem 0;
        letter-spacing: 0.04em;
        color: var(--fg-dim);
      }
      .mv-turn-you {
        color: var(--accent);
        border: 1px solid var(--accent);
      }
      /* Most-recent discard, shown large. */
      .mv-lastdiscard {
        text-align: center;
        padding: 0.25rem 0;
        border-top: 1px dashed var(--border);
        border-bottom: 1px dashed var(--border);
      }
      .mv-lastdiscard.mv-ld-empty {
        color: var(--fg-dim);
        font-style: italic;
      }
      .mv-ld-label {
        color: var(--fg-dim);
        font-size: 0.85em;
        margin-bottom: 0.15rem;
      }
      .mv-ld-tile .tile {
        font-size: 3.4em;
      }
      .mv-ld-tile .tile.dragon,
      .mv-ld-tile .tile.face-down {
        font-size: 3.4em;
      }
      /* Per-player roster rows: name (wind) · score · melds · flowers. */
      .mv-roster {
        display: flex;
        flex-direction: column;
      }
      .mv-row,
      .mv-own-head {
        display: flex;
        flex-wrap: wrap;
        align-items: baseline;
        gap: 0.15rem 1.1rem;
        padding: 0.25rem 0;
        border-bottom: 1px dashed var(--border);
      }
      .mv-name {
        color: var(--accent);
        min-width: 9ch;
        font-weight: 600;
      }
      .mv-name.mv-you {
        color: var(--accent-red);
      }
      .mv-wind {
        color: var(--fg-dim);
        font-weight: 400;
      }
      .mv-bot {
        color: var(--fg-dim);
        font-size: 0.8em;
        margin-left: 0.25em;
      }
      .mv-score {
        color: var(--fg-dim);
        min-width: 4ch;
      }
      .mv-score::before {
        content: "♦ ";
      }
      .mv-melds .tile,
      .mv-flowers .tile {
        font-size: 1.3em;
      }
      .mv-noflower {
        color: var(--fg-dim);
      }
      /* Combined discard pond (chronological). High-frequency background
       * info, so smaller than the hand; the latest tile is highlighted to
       * tie back to the large last-discard. */
      .mv-pond {
        padding: 0.25rem 0;
      }
      .mv-pond-label {
        color: var(--fg-dim);
        font-size: 0.85em;
        margin-bottom: 0.15rem;
      }
      .mv-pond-tiles {
        line-height: 1.7;
      }
      .mv-pond-tiles .tile {
        font-size: 1.3em;
      }
      .pond-latest {
        background-color: color-mix(in srgb, var(--accent) 20%, transparent);
        border-radius: 0.15em;
        padding: 0 0.1em;
      }
      /* Per-player discard rows (Spec 40, the default): one row per seat in
       * seat order, each that player's discards. */
      .mv-drows { padding: 0.25rem 0; }
      .mv-drow {
        display: flex;
        flex-wrap: wrap;
        align-items: baseline;
        gap: 0.15rem 0.6rem;
        padding: 0.1rem 0;
      }
      .mv-drow-label {
        color: var(--fg-dim);
        min-width: 11ch;
        font-size: 0.85em;
      }
      .mv-drow-tiles { line-height: 1.6; }
      .mv-drow-tiles .tile { font-size: 1.2em; }
      /* Your own block: roster head + the large concealed hand. */
      .mv-own {
        border-top: 1px solid var(--border);
        padding-top: 0.4rem;
      }
      .mv-own-hand {
        margin-top: 0.4rem;
        line-height: 2;
      }
      .mv-own-hand .tile {
        font-size: 2.4em;
      }
      .mv-own-hand .tile.dragon,
      .mv-own-hand .tile.face-down {
        font-size: 2.6em;
      }

      /* Prominent claim banner in minimal mode (the small chip becomes a
       * full-width alert so a claim window can't be missed). */
      .claim-chip.mv-claim {
        margin: 0.25rem 0 0.5rem;
        padding: 0.5rem 0.75rem;
        font-size: 1.4em;
        text-align: center;
        border: 2px solid var(--accent-red);
        border-radius: 3px;
        letter-spacing: 0.08em;
      }
    `,
  ];

  constructor() {
    super();
    this.status = "connecting";
    this.seatView = null;
    this.ownSeat = null;
    this.tileStyle = "ascii";
    this.viewMode = "minimal";
    this.frames = [];
    this.showLog = false;
    this.currentPrompt = null;
    this.selectedTile = null;
    this.chiChoosing = null; // CHI sequences when picking which run to take
    this.illegalBanner = null;
    this._illegalBannerTimer = null;
    this.readySent = false; // FB-02: has the local human acked this HAND_END?
    this.readyState = null; // FB-19: live { ready[], waiting_on[] } roster, or null
  }

  pushFrame(msg) {
    this.frames = [...this.frames, msg];
  }

  setStatus(status) {
    this.status = status;
  }

  // FB-19: the live between-hand readiness roster (READY_STATE frame). Drives
  // the "waiting on …" display next to the Ready button.
  setReadyState(frame) {
    this.readyState = { ready: frame.ready ?? [], waiting_on: frame.waiting_on ?? [] };
  }

  setSnapshot(seatView, ownSeat) {
    // FB-02: a snapshot without a terminal means a fresh hand started — re-arm
    // the ready button for the next HAND_END, and drop the stale roster.
    if (!seatView?.terminal) {
      this.readySent = false;
      this.readyState = null;
    }
    this.seatView = seatView;
    this.ownSeat = ownSeat;
    // The live table header (round/wall/hand) lives in <table-page>, which
    // doesn't hold the view. Every view update funnels through here (ATTACHED,
    // EVENT, HAND_END), so this is the single seam to keep the header current.
    this.dispatchEvent(
      new CustomEvent("view-changed", {
        bubbles: true,
        composed: true,
        detail: { view: seatView },
      }),
    );
  }

  _submitReady() {
    // FB-02: ack the HAND_END summary so the server starts the next hand.
    if (this.readySent) return;
    this.readySent = true;
    this.dispatchEvent(
      new CustomEvent("ready-submitted", { bubbles: true, composed: true, detail: {} }),
    );
  }

  // FB-19: short seat label for the readiness roster (wind name, plus player
  // name when the view carries one). Wind is always present, so this never
  // renders an empty cell.
  _seatRosterName(seat) {
    const sv = this.seatView?.seats?.find((s) => s.seat === seat);
    const wind = sv ? (ROUND_WIND_NAME[sv.seat_wind] ?? sv.seat_wind) : null;
    const name = sv?.player?.name ?? sv?.name ?? null;
    if (name) return `${name} (${wind})`;
    return wind ?? `Seat ${seat + 1}`;
  }

  // FB-02 + FB-19: the between-hand control. The local Ready button (until this
  // client acks), plus the live readiness roster (READY_STATE) showing who the
  // next hand is still waiting on — no timeout, so this is how players know.
  _renderReadyArea() {
    if (!this.seatView?.terminal) return "";
    const control = this.readySent
      ? html`<div class="ready-waiting">✓ You're ready — waiting for the others…</div>`
      : html`<button class="ready-btn" @click=${() => this._submitReady()}>
          Ready ▶ Next hand
        </button>`;
    const rs = this.readyState;
    const roster = rs
      ? html`<div class="ready-roster">
          ${[...rs.ready]
            .sort((a, b) => a - b)
            .map(
              (s) =>
                html`<span class="ready-seat ready-yes">${this._seatRosterName(s)} ✓</span>`,
            )}
          ${[...rs.waiting_on]
            .sort((a, b) => a - b)
            .map(
              (s) =>
                html`<span class="ready-seat ready-no">${this._seatRosterName(s)} …</span>`,
            )}
        </div>`
      : "";
    return html`<div class="ready-area">${control}${roster}</div>`;
  }

  setPrompt(prompt) {
    // A new prompt clears any stale selection and dismisses an illegal-action
    // banner from the previous attempt.
    this.currentPrompt = prompt;
    this.selectedTile = null;
    this.chiChoosing = null;
    this._clearIllegalBanner();
    this._notifyPromptChanged();
  }

  clearPrompt() {
    this.currentPrompt = null;
    this.selectedTile = null;
    this.chiChoosing = null;
    this._notifyPromptChanged();
  }

  _notifyPromptChanged() {
    // Spec 37: <table-page> mirrors the prompt into the Alt+S stats pane.
    this.dispatchEvent(
      new CustomEvent("prompt-changed", {
        bubbles: true,
        composed: true,
        detail: { prompt: this.currentPrompt },
      }),
    );
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
    // FB-06: browsers keep an AudioContext suspended until a user gesture, so
    // cues created off the websocket are silent. Warm/resume it on the first
    // interaction (`once` auto-removes); `unlock` is idempotent if both fire.
    this._unlockAudio = () => audioCues.unlock();
    window.addEventListener("pointerdown", this._unlockAudio, { once: true });
    window.addEventListener("keydown", this._unlockAudio, { once: true });
  }

  disconnectedCallback() {
    if (this._onKeydown) window.removeEventListener("keydown", this._onKeydown);
    if (this._unlockAudio) {
      window.removeEventListener("pointerdown", this._unlockAudio);
      window.removeEventListener("keydown", this._unlockAudio);
    }
    super.disconnectedCallback();
  }

  _ownConcealedTiles() {
    if (!this.seatView || this.ownSeat == null) return [];
    const seat = this.seatView.seats?.[this.ownSeat];
    return Array.isArray(seat?.concealed) ? seat.concealed : [];
  }

  _handleKeydown(e) {
    // Never hijack keystrokes meant for a text field (bug-report box, chat,
    // any input) — Space/H/Enter/letters are game shortcuts, so typing in one
    // would pass, declare HU, or discard a tile (player report 025210). The
    // bug-report textarea lives in a shadow root, so at window level e.target
    // is the retargeted host; composedPath()[0] is the real focused element.
    if (isEditableTarget(e)) return;
    // Alt-chords belong to <table-page> (pane toggles) and <mahjong-app>
    // (theme/tile-style). Ctrl/Meta likewise reserved for browser shortcuts.
    if (e.altKey || e.ctrlKey || e.metaKey) return;
    if (!this.currentPrompt) return;

    // Staged CHI chooser: after pressing C with multiple sequences, a digit
    // key picks which run; Esc backs out. Any other key cancels the chooser
    // and is then handled normally (so Space still passes, etc.).
    if (this.chiChoosing) {
      const pick = tileIndexForKeyCode(e.code);
      if (pick !== null) {
        if (pick >= 0 && pick < this.chiChoosing.length) {
          e.preventDefault();
          const action = this.chiChoosing[pick];
          this.chiChoosing = null;
          this._submitAction(action);
        }
        return; // a digit out of range is ignored; stay in the chooser
      }
      if (e.code === "Escape") {
        e.preventDefault();
        this.chiChoosing = null;
        return;
      }
      this.chiChoosing = null; // fall through to normal handling for other keys
    }

    // C enters the chooser when there are 2+ chi sequences, or submits the
    // sole option directly. (Routed here so multiple CHI options are reachable
    // — the old actionForKey path always took the first.)
    if (e.code === "KeyC") {
      const chis = chiOptions(this.currentPrompt);
      if (chis.length === 0) return; // no chi available — no-op per spec
      e.preventDefault();
      if (chis.length === 1) {
        this._submitAction(chis[0]);
      } else {
        this.chiChoosing = chis;
      }
      return;
    }

    // Tile-selection keys set the cursor; arrow keys nudge it; Enter
    // confirms PLAY. All other keys dispatch to actionForKey.
    //
    // Selection runs in DISPLAY order, not raw concealed order: the just-drawn
    // tile is rendered out of sort order (pulled to the end), so a position
    // key / arrow must address the on-screen slot the player sees. `order`
    // maps each screen slot to its raw concealed index (origIdx), the value
    // stored in selectedTile and read back by the renderer and actionForKey —
    // FB-18 defect 2.
    const tileIdx = tileIndexForKeyCode(e.code);
    const concealed = this._ownConcealedTiles();
    const order = concealedDisplayOrder(concealed, this.seatView, this.ownSeat);
    if (tileIdx !== null) {
      if (tileIdx >= 0 && tileIdx < order.length) {
        e.preventDefault();
        this.selectedTile = order[tileIdx].origIdx;
      }
      return;
    }
    if (e.code === "ArrowLeft" || e.code === "ArrowRight") {
      if (order.length === 0) return;
      // Nudge the cursor in display order. With no selection yet, start from
      // the last on-screen tile (the just-drawn one), matching Enter's default.
      let pos =
        this.selectedTile == null
          ? order.length - 1
          : order.findIndex((o) => o.origIdx === this.selectedTile);
      if (pos < 0) pos = order.length - 1;
      const nextPos =
        e.code === "ArrowLeft" ? Math.max(0, pos - 1) : Math.min(order.length - 1, pos + 1);
      e.preventDefault();
      this.selectedTile = order[nextPos].origIdx;
      return;
    }

    // Enter's no-selection fallback is the just-drawn tile (tsumogiri).
    const justDrawn = order.find((o) => o.isJustDrawn);
    const lastDrawnTile = justDrawn ? justDrawn.token : null;
    const action = actionForKey(
      e.code,
      this.currentPrompt,
      this.selectedTile,
      concealed,
      lastDrawnTile,
    );
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
    // Minimal is the default; classic is the original stat-rich table. The
    // pinwheel is classic-only — the minimal view carries its own whose-turn
    // banner and large last-discard, so the pinwheel would just be clutter.
    const isMinimal = this.viewMode !== "classic";
    const renderFn = isMinimal ? renderMinimal : renderTable;
    const tableContent = this.seatView
      ? renderFn(this.seatView, this.ownSeat, {
          tileStyle: this.tileStyle,
          selectedTile: this.selectedTile,
          discardLayout: this.discardLayout,
        })
      : null;
    const pinwheel =
      !isMinimal && this.seatView
        ? renderPinwheel(this.seatView, this.ownSeat, { tileStyle: this.tileStyle })
        : null;
    const handEndSummary = this.seatView?.terminal
      ? renderHandEndSummary(this.seatView, this.ownSeat, { tileStyle: this.tileStyle })
      : null;

    const claimAvailable = isClaimAvailable(this.currentPrompt);
    return html`
      <div class="pane ${isMinimal ? "minimal" : "classic"}">
        ${paneHeader("Game pane", null, null)}
        ${claimAvailable
          ? html`<div class="claim-chip ${isMinimal ? "mv-claim" : ""}">
              ${isMinimal ? "⚠ CLAIM AVAILABLE" : "[ CLAIM AVAILABLE ]"}
            </div>`
          : ""}
        <div class="status ${this.status}">Connection: ${this.status}</div>
        ${tableContent !== null
          ? isMinimal
            ? html`<div class="minimal-wrap">${tableContent}</div>`
            : html`<div class="table-ascii pinwheel-wrap">
                ${pinwheel}
                ${tableContent}
              </div>`
          : html`<div class="waiting">(waiting for ATTACHED snapshot…)</div>`}

        ${handEndSummary ?? ""}
        ${this._renderReadyArea()}

        ${this.illegalBanner
          ? html`<div class="illegal-banner">${this.illegalBanner}</div>`
          : ""}
        ${this.currentPrompt ? renderPromptBar(this.currentPrompt, this.chiChoosing) : ""}

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

// --- <chat-pane> (Spec 38: table chat) ------------------------------------
//
// Scrollback + input over the CHAT/CHAT_MESSAGE frames. Messages arrive
// denormalized ({seat, name, text, ts}) from <table-page>.addChatMessage —
// the pane renders verbatim (Lit text interpolation; never HTML). The
// window-level game keymap ignores keys typed here via the FB-16
// isEditableTarget guard; Enter-to-send is handled on the input itself.

class ChatPane extends LitElement {
  static properties = {
    messages: { attribute: false },
  };

  static styles = [
    paneChromeStyles,
    css`
      .chat-log {
        max-height: 16rem;
        overflow-y: auto;
        margin: 0.25rem 0 0.5rem;
        display: flex;
        flex-direction: column;
        gap: 0.15rem;
      }
      .chat-line { word-break: break-word; }
      .chat-line .who { color: var(--accent); }
      .chat-empty { color: var(--fg-dim); }
      .chat-input-row { display: flex; gap: 0.5rem; }
      .chat-input-row input {
        flex: 1;
        background: transparent;
        color: var(--fg);
        border: 1px solid var(--fg-dim);
        font-family: inherit;
        font-size: inherit;
        padding: 0.25rem 0.5rem;
      }
      .chat-input-row input:focus { outline: none; border-color: var(--accent); }
      .chat-input-row button {
        background: transparent;
        color: var(--accent);
        border: 1px solid var(--accent);
        font-family: inherit;
        cursor: pointer;
        padding: 0.25rem 0.75rem;
      }
    `,
  ];

  constructor() {
    super();
    this.messages = [];
  }

  updated() {
    const log = this.renderRoot.querySelector(".chat-log");
    if (log) log.scrollTop = log.scrollHeight;
  }

  _send() {
    const input = this.renderRoot.querySelector("input");
    const text = (input?.value ?? "").trim();
    if (!text) return;
    input.value = "";
    this.dispatchEvent(
      new CustomEvent("chat-send", { bubbles: true, composed: true, detail: { text } }),
    );
  }

  _onInputKeydown(e) {
    if (e.key === "Enter") {
      e.preventDefault();
      this._send();
    }
  }

  render() {
    return html`
      <div class="pane">
        ${paneHeader("Chat", "Alt+C", () => this.dispatchEvent(new CustomEvent("pane-close", { bubbles: true, composed: true, detail: { pane: "chat" } })))}
        <div class="chat-log">
          ${this.messages.length
            ? this.messages.map(
                (m) => html`<div class="chat-line"><span class="who">${m.name}:</span> ${m.text}</div>`,
              )
            : html`<div class="chat-empty">(no messages yet — say hi)</div>`}
        </div>
        <div class="chat-input-row">
          <input
            type="text"
            maxlength="500"
            placeholder="message the table…"
            @keydown=${this._onInputKeydown}
          />
          <button @click=${this._send}>Send</button>
        </div>
      </div>
    `;
  }
}

customElements.define("chat-pane", ChatPane);

// --- <stats-pane> (Spec 37: hand analysis) --------------------------------
//
// Renders the live PROMPT.stats payload as a full per-candidate table:
// every legal discard with the shanten it leaves, the advancing tiles with
// remaining counts, and per-wait fan at tenpai. Fed by <table-page> via the
// game-pane's `prompt-changed` event; shows a hint between prompts.
// (Career / cross-game stats live on the profile page, not here.)

class StatsPane extends LitElement {
  static properties = {
    prompt: { attribute: false },
    tileStyle: { type: String },
    // Per-table opt-out (snapshot `stats_enabled`); false => "stats disabled".
    statsEnabled: { type: Boolean },
  };

  static styles = [
    paneChromeStyles,
    css`
      .stats-meta { color: var(--fg-dim); margin: 0.25rem 0 0.5rem; }
      table.stats-table { border-collapse: collapse; width: 100%; }
      table.stats-table th {
        text-align: left;
        color: var(--accent);
        font-weight: normal;
        padding: 0.15rem 0.6rem 0.15rem 0;
      }
      table.stats-table td {
        padding: 0.15rem 0.6rem 0.15rem 0;
        vertical-align: baseline;
      }
      table.stats-table tbody tr:first-child td { color: var(--accent); }
      .stat-tile { white-space: nowrap; margin-right: 0.45rem; }
      .stat-tile .fan { color: var(--fg-dim); }
      .stat-tile.sub-floor { opacity: 0.55; }
      .stat-tile.sub-floor .floor-mark { color: var(--error); margin-left: 0.15rem; }
      .stat-tile.dead { text-decoration: line-through; opacity: 0.55; }
      .total { color: var(--fg-dim); }
      .more { color: var(--fg-dim); }
    `,
  ];

  constructor() {
    super();
    this.prompt = null;
    this.tileStyle = "ascii";
    this.statsEnabled = true;
  }

  render() {
    return html`
      <div class="pane">
        ${paneHeader("Hand stats", "Alt+S", () => this.dispatchEvent(new CustomEvent("pane-close", { bubbles: true, composed: true, detail: { pane: "stats" } })))}
        ${renderStatsDetail(this.prompt, { tileStyle: this.tileStyle }, this.statsEnabled)}
      </div>
    `;
  }
}

customElements.define("stats-pane", StatsPane);

// --- <score-pane> (Spec 40: running match scoreboard) ---------------------
//
// One cumulative line graph per seat across the match's completed hands, plus
// each seat's current running total. Fed the live view by <table-page>; reads
// the server-authoritative `match_scores` block (cumulative + per-hand series)
// and reuses the profile page's renderScoreGraph. Empty until the first hand
// completes (no series points yet).
const _WIND_INITIAL = { F1: "E", F2: "S", F3: "W", F4: "N" };

class ScorePane extends LitElement {
  static properties = {
    view: { attribute: false },
  };

  static styles = [
    paneChromeStyles,
    css`
      .sp-empty { color: var(--fg-dim); padding: 0.5rem 0; }
      .sp-player { margin-bottom: 0.6rem; }
      .sp-head { display: flex; justify-content: space-between; color: var(--accent); }
      .sp-total.pos { color: var(--accent); }
      .sp-total.neg { color: var(--accent-red); }
      .sp-graph {
        white-space: pre;
        line-height: 1.05;
        color: var(--fg-dim);
        margin-top: 0.1rem;
      }
    `,
  ];

  constructor() {
    super();
    this.view = null;
  }

  _label(seat) {
    const sv = (this.view?.seats ?? []).find((s) => s.seat === seat);
    const name = sv?.name ?? `Seat ${seat + 1}`;
    const wind = _WIND_INITIAL[sv?.seat_wind] ?? "";
    const bot = sv?.is_bot ? " ·bot" : "";
    return wind ? `${name}${bot} (${wind})` : `${name}${bot}`;
  }

  render() {
    const ms = this.view?.match_scores;
    const hasData = ms && Array.isArray(ms.series) && ms.series.length > 0;
    const close = () =>
      this.dispatchEvent(
        new CustomEvent("pane-close", {
          bubbles: true,
          composed: true,
          detail: { pane: "score" },
        }),
      );
    return html`
      <div class="pane">
        ${paneHeader("Scores", "Alt+P", close)}
        ${!hasData
          ? html`<div class="sp-empty">(no completed hands yet — standings appear after the first hand)</div>`
          : [0, 1, 2, 3].map((seat) => {
              const total = ms.cumulative?.[seat] ?? 0;
              const series = ms.series.map((row) => ({ cumulative: row[seat] ?? 0 }));
              const cls = total > 0 ? "pos" : total < 0 ? "neg" : "";
              return html`<div class="sp-player">
                <div class="sp-head">
                  <span>${this._label(seat)}</span>
                  <span class="sp-total ${cls}">${total > 0 ? `+${total}` : total}</span>
                </div>
                <div class="sp-graph">${renderScoreGraph(series, { width: 34, height: 6 })}</div>
              </div>`;
            })}
      </div>
    `;
  }
}

customElements.define("score-pane", ScorePane);

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
    seatHolds: { type: Array },          // FB-03 — seats this account can rejoin
    desiredHumans: { type: Number },
    availableBots: { type: Array },      // HELLO.bots — [{bot_id,label,description}]
    botSelections: { state: true },      // per-seat bot_id (length 4; bot seats only)
    lastRefreshTs: { type: Number, state: true },
    busy: { type: String, state: true }, // null | "joining" | "creating"
    // §22.6 Part A — table creation options (collapsed by default).
    showAdvanced: { state: true },
    pacingPreset: { state: true },       // "fast" | "normal" | "slow" | "custom"
    customMin: { state: true },
    customMax: { state: true },
    decideTimeout: { state: true },
    timeoutsEnabled: { state: true },
    statsEnabled: { state: true },       // Spec 37 per-table stats opt-out
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
    .rejoin-block {
      border: 1px solid var(--accent);
      padding: 0.4rem 0.75rem 0.6rem;
      margin-bottom: 0.75rem;
    }
    .rejoin-row {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 0.75rem;
      margin: 0.3rem 0;
    }
    .rejoin-label em { color: var(--fg-dim); font-style: normal; }
    .rejoin-btn { white-space: nowrap; }
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
    .seat-away { color: var(--fg-dim); font-style: italic; }
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
    .bot-picker {
      margin: 0.2rem 0 0.4rem 1rem;
      display: flex;
      flex-direction: column;
      gap: 0.25rem;
    }
    .bot-pick-row {
      display: flex;
      gap: 0.5rem;
      align-items: baseline;
    }
    .bot-select {
      background: var(--bg);
      border: 1px solid var(--border);
      color: var(--fg);
      font: inherit;
      padding: 0.05rem 0.3rem;
      cursor: pointer;
    }
    .bot-select:disabled { opacity: 0.4; cursor: default; }
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
    this.availableBots = [];
    // One bot_id per seat; only bot seats (index >= desiredHumans) are used.
    // null entries resolve to the default bot at payload-build time.
    this.botSelections = [null, null, null, null];
    this.lastRefreshTs = 0;
    this.busy = null;
    this.showAdvanced = false;
    this.pacingPreset = "normal";
    this.customMin = 5.0;
    this.customMax = 10.0;
    this.decideTimeout = 60;
    // Untimed by default: casual home play should wait for a human as long as
    // they like.  The creator opts into a turn timer via the prominent toggle.
    this.timeoutsEnabled = false;
    // Decision-time stats (Spec 37) are on by default; the creator can disable
    // the Alt+S analysis pane for the whole table ("no aids" games).
    this.statsEnabled = true;
  }

  _emit(name, detail) {
    this.dispatchEvent(new CustomEvent(name, { detail, bubbles: true, composed: true }));
  }

  // Build the CREATE_TABLE.options object from the advanced controls.
  // Returns null when every control is at its default (keeps the wire
  // message minimal and lets the server apply its own defaults).
  _buildOptions() {
    const atDefault =
      this.pacingPreset === "normal" &&
      this.decideTimeout === 60 &&
      this.timeoutsEnabled &&
      this.statsEnabled;
    if (atDefault) return null;
    const options = {};
    options.bot_pacing =
      this.pacingPreset === "custom"
        ? { min_s: Number(this.customMin), max_s: Number(this.customMax) }
        : this.pacingPreset;
    options.timeouts_enabled = this.timeoutsEnabled;
    if (this.timeoutsEnabled) options.decide_timeout_seconds = Number(this.decideTimeout);
    // Only send stats_enabled when opting out — absent means "on" server-side.
    if (!this.statsEnabled) options.stats_enabled = false;
    return options;
  }

  _onJoin(tableId, seat) {
    if (this.busy) return;
    this.busy = "joining";
    this._emit("lobby-join", { tableId, seat });
  }

  // Default bot_id when a seat hasn't been explicitly picked: first advertised
  // bot, or "v0" if the server didn't send a menu (old server).
  _defaultBotId() {
    return this.availableBots[0]?.bot_id ?? "v0";
  }

  // Build the CREATE_TABLE.seats[] payload: first N seats human, the rest bots
  // carrying their selected bot_id.
  _seatsPayload() {
    return Array.from({ length: 4 }, (_, i) => {
      if (i < this.desiredHumans) return { kind: "human" };
      return { kind: "bot", bot_id: this.botSelections[i] ?? this._defaultBotId() };
    });
  }

  _onCreate() {
    if (this.busy) return;
    this.busy = "creating";
    this._emit("lobby-create", {
      humans: this.desiredHumans,
      options: this._buildOptions(),
      seats: this._seatsPayload(),
    });
  }

  _pickBot(seat, botId) {
    const next = [...this.botSelections];
    next[seat] = botId;
    this.botSelections = next;
  }

  // Per-bot-seat agent picker. One row per seat that will be filled by a bot
  // (seats at index >= desiredHumans). Lets the creator choose which agent
  // sits where. With a single registered bot the <select> still renders so the
  // choice is explicit and the UI is ready for more agents.
  _renderBotPicker() {
    const bots = Array.isArray(this.availableBots) ? this.availableBots : [];
    const botSeats = [];
    for (let i = this.desiredHumans; i < 4; i++) botSeats.push(i);
    if (botSeats.length === 0) return "";
    const defaultId = this._defaultBotId();
    return html`
      <div class="bot-picker">
        ${botSeats.map((i) => {
          const selected = this.botSelections[i] ?? defaultId;
          return html`
            <div class="bot-pick-row">
              <span class="pick-label">Seat ${i} bot:</span>
              ${bots.length > 0
                ? html`
                    <select
                      class="bot-select"
                      ?disabled=${!!this.busy}
                      .value=${selected}
                      @change=${(e) => this._pickBot(i, e.target.value)}
                    >
                      ${bots.map(
                        (b) => html`
                          <option value=${b.bot_id} ?selected=${b.bot_id === selected} title=${b.description ?? ""}>
                            ${b.label ?? b.bot_id}
                          </option>
                        `,
                      )}
                    </select>
                  `
                : html`<span class="seat-bot">${selected}</span>`}
            </div>
          `;
        })}
      </div>
    `;
  }

  _onRefresh() {
    this._emit("lobby-refresh", {});
  }

  _onRejoin(hold) {
    // Emit a rejoin intent; the app turns it into an ATTACH on the held seat.
    this._emit("lobby-rejoin", { tableId: hold.table_id, seat: hold.seat });
  }

  // FB-03 — render a prominent "rejoin / take over" block when this account
  // holds seats it can return to.
  _renderRejoin() {
    const holds = Array.isArray(this.seatHolds) ? this.seatHolds : [];
    if (holds.length === 0) return html``;
    return html`
      <div class="rejoin-block">
        <div class="lobby-section-title">Rejoin a game in progress</div>
        ${holds.map((h) => {
          const live = h.state === "LIVE";
          return html`
            <div class="rejoin-row">
              <span class="rejoin-label">
                Table ${h.table_id} · seat ${h.seat}
                ${live ? html`<em>(open elsewhere — take over)</em>` : html`<em>(you're away)</em>`}
              </span>
              <button class="lobby-btn rejoin-btn" @click=${() => this._onRejoin(h)}>
                ${live ? "[ ▶ Take over ]" : "[ ▶ Rejoin ]"}
              </button>
            </div>
          `;
        })}
      </div>
    `;
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
          <span class="seat-bot">${seat.bot_id ?? "v0"}</span>
        </div>
      `;
    }
    // Human seat. FB-05: show who's seated (display name), and mark a player
    // who dropped (HELD) as "away" so others know the seat isn't free to take.
    if (seat.occupied) {
      const who = seat.display_name ?? seat.user_id ?? "occupied";
      const away = seat.state === "HELD";
      return html`
        <div class="seat-row">
          <span class="seat-label">Seat ${seat.seat}</span>
          <span class="seat-kind">human</span>
          <span class="seat-occupied">
            ${who}${away ? html` <em class="seat-away">(away)</em>` : ""}
          </span>
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
                  <span class="adv-label">seconds per discard prompt
                    ${this.timeoutsEnabled ? "" : "(turn timer is off)"}</span>
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

        ${this._renderRejoin()}

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
          <span class="pick-label">+ ${4 - this.desiredHumans} bot${4 - this.desiredHumans === 1 ? "" : "s"}</span>
        </div>
        ${this._renderBotPicker()}
        <div class="pick-row">
          <label class="timer-toggle">
            <input
              type="checkbox"
              .checked=${this.timeoutsEnabled}
              ?disabled=${!!this.busy}
              @change=${(e) => (this.timeoutsEnabled = e.target.checked)}
            />
            Turn timer
          </label>
          <span class="pick-label">
            ${this.timeoutsEnabled
              ? html`on — ${this.decideTimeout}s per turn (tune in Options)`
              : "off — players take as long as they like"}
          </span>
        </div>
        <div class="pick-row">
          <label class="timer-toggle">
            <input
              type="checkbox"
              .checked=${!this.statsEnabled}
              ?disabled=${!!this.busy}
              @change=${(e) => (this.statsEnabled = !e.target.checked)}
            />
            Disable stats panel
          </label>
          <span class="pick-label">
            ${this.statsEnabled
              ? "Alt+S shanten/waits analysis available to players"
              : "no hand-analysis aids in this game"}
          </span>
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
  KeyP: "score", // Spec 40: running scoreboard widget
};

// Round (prevailing) wind label for the table header. Engine encodes winds as
// F1..F4; mirror render.js's WIND_NAME without exporting it across modules.
const ROUND_WIND_NAME = { F1: "East", F2: "South", F3: "West", F4: "North" };

class TablePage extends LitElement {
  static properties = {
    panes: { type: Object },
    tileStyle: { type: String },
    viewMode: { type: String },
    discardLayout: { type: String },
    tableId: {},                  // attached table id, for the header label
    _chatLog: { state: true },
    _chatUnread: { state: true },
    _prompt: { state: true },
    _headerView: { state: true }, // latest seatView, for the live header
  };

  static styles = css`
    :host { display: block; }
    /* A custom element with an explicit :host display ignores the UA [hidden]
       rule, so the lobby's ?hidden never took effect — the table header bled
       through. Restore it. */
    :host([hidden]) { display: none; }

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
    .table-header .panes-indicator .unread { color: var(--accent-red); }

    /* Spec 40 layout: chat lives in a narrow side column (no longer a third of
       the board); stats + score panes stack *under* the game pane full-width.
       Flex (not grid) so empty regions collapse without area juggling. */
    .table-body { display: flex; flex-direction: column; gap: 0.75rem; }
    .main-row { display: flex; gap: 0.75rem; align-items: flex-start; }
    .slot-game { flex: 1 1 auto; min-width: 0; }
    .slot-side {
      flex: 0 0 28ch;
      min-width: 0;
      display: flex;
      flex-direction: column;
      gap: 0.75rem;
    }
    .under-row {
      display: flex;
      flex-wrap: wrap;
      gap: 0.75rem;
      align-items: flex-start;
    }
    .under-row > * { flex: 1 1 320px; min-width: 0; }
    .slot-spectator { width: 100%; }
  `;

  constructor() {
    super();
    this.panes = { chat: false, stats: false, score: false, spectator: false };
    this.tileStyle = "ascii";
    this._chatLog = [];
    this._chatUnread = false;
    this._prompt = null;
    this._headerView = null;
    this._onKeydown = this._handleKeydown.bind(this);
    this._onPaneClose = this._handlePaneClose.bind(this);
    this._onPromptChanged = (e) => {
      this._prompt = e.detail?.prompt ?? null;
    };
    this._onViewChanged = (e) => {
      this._headerView = e.detail?.view ?? null;
    };
  }

  // Spec 38: append one CHAT_MESSAGE frame, denormalizing the sender label
  // from the live view (names are hand-stable, so append-time is correct).
  // Capped scrollback; a line arriving while the pane is closed lights the
  // header's [C] indicator.
  addChatMessage(frame) {
    const name =
      this.gamePane?.seatView?.seats?.[frame.seat]?.name ?? `Seat ${frame.seat}`;
    this._chatLog = [
      ...this._chatLog.slice(-199),
      { seat: frame.seat, name, text: frame.text, ts: frame.ts },
    ];
    if (!this.panes.chat) this._chatUnread = true;
  }

  connectedCallback() {
    super.connectedCallback();
    window.addEventListener("keydown", this._onKeydown);
    this.addEventListener("pane-close", this._onPaneClose);
    // Spec 37: mirror the game-pane's live prompt into the stats pane.
    this.addEventListener("prompt-changed", this._onPromptChanged);
    // Spec 40: keep the table header (round/wall/hand) current.
    this.addEventListener("view-changed", this._onViewChanged);
  }

  disconnectedCallback() {
    window.removeEventListener("keydown", this._onKeydown);
    this.removeEventListener("pane-close", this._onPaneClose);
    this.removeEventListener("prompt-changed", this._onPromptChanged);
    this.removeEventListener("view-changed", this._onViewChanged);
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
    if (pane === "chat" && this.panes.chat) this._chatUnread = false;
    this.dispatchEvent(
      new CustomEvent("panes-changed", {
        bubbles: true,
        composed: true,
        detail: { panes: { ...this.panes } },
      }),
    );
  }

  // Live table status line (Spec 40). Reads the latest seatView; before the
  // first snapshot arrives it shows the table id with placeholders so the
  // header never reads as a stale "demo" row.
  _renderHeaderInfo() {
    const v = this._headerView;
    const table = this.tableId != null && this.tableId !== "" ? this.tableId : "—";
    const round = v ? (ROUND_WIND_NAME[v.round_wind] ?? v.round_wind ?? "—") : "—";
    const hand = v ? (v.hand_index ?? 0) + 1 : "—";
    const wall = v?.wall?.remaining_count ?? "—";
    return html`<span
      >Table ${table}  ·  Hand ${hand}  ·  Round ${round}  ·  Wall ${wall}</span
    >`;
  }

  _paneIndicator(label, isOn) {
    return html`<span class=${isOn ? "on" : ""}>${label}</span>`;
  }

  _alwaysOnIndicator(label) {
    return html`<span class="always-on">${label}</span>`;
  }

  render() {
    const chatOn = this.panes.chat;
    const underOn = this.panes.stats || this.panes.score;

    return html`
      <div class="table-header">
        ${this._renderHeaderInfo()}
        <span class="panes-indicator">
          Panes:
          ${this._alwaysOnIndicator("[G]")}
          <span class=${this.panes.chat ? "on" : this._chatUnread ? "unread" : ""}>C</span
          >·${this._paneIndicator("S", this.panes.stats)}·${this._paneIndicator(
            "P",
            this.panes.score,
          )}·${this._paneIndicator("W", this.panes.spectator)}
        </span>
      </div>
      <div class="table-body">
        <div class="main-row">
          <div class="slot-game"><game-pane .tileStyle=${this.tileStyle} .viewMode=${this.viewMode} .discardLayout=${this.discardLayout}></game-pane></div>
          ${chatOn
            ? html`<div class="slot-side"><chat-pane .messages=${this._chatLog}></chat-pane></div>`
            : ""}
        </div>
        ${underOn
          ? html`<div class="under-row">
              ${this.panes.stats
                ? html`<stats-pane
                    .prompt=${this._prompt}
                    .tileStyle=${this.tileStyle}
                    .statsEnabled=${this._headerView?.stats_enabled !== false}
                  ></stats-pane>`
                : ""}
              ${this.panes.score ? html`<score-pane .view=${this._headerView}></score-pane>` : ""}
            </div>`
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

// --- <settings-menu> ----------------------------------------------------
//
// Modal overlay listing every client toggle (settings.js § SETTINGS).  Purely
// presentational: reads current values via `.values`, emits `setting-cycle`
// {key} when a row is activated and `settings-close` on dismiss.  The existing
// keyboard chords keep working — this is a discoverable surface over the same
// state, not a replacement.
class SettingsMenu extends LitElement {
  static properties = {
    values: { type: Object },     // { [key]: currentValue }
    tableActive: { type: Boolean }, // table-scoped rows enabled?
  };

  static styles = css`
    :host { display: block; }
    .backdrop {
      position: fixed;
      inset: 0;
      background: rgba(0, 0, 0, 0.55);
      display: flex;
      align-items: center;
      justify-content: center;
      z-index: 50;
    }
    .modal {
      background: var(--bg);
      border: 1px solid var(--accent);
      padding: 1rem 1.25rem 1.25rem;
      min-width: 340px;
      max-width: 90vw;
      font-family: inherit;
    }
    .title {
      color: var(--accent);
      margin-bottom: 0.75rem;
      display: flex;
      justify-content: space-between;
      gap: 1rem;
    }
    .close {
      background: transparent;
      border: none;
      color: var(--fg-dim);
      font-family: inherit;
      font-size: inherit;
      cursor: pointer;
    }
    .close:hover { color: var(--accent); }
    .row {
      display: grid;
      grid-template-columns: 1fr auto auto;
      gap: 0.75rem;
      align-items: center;
      padding: 0.3rem 0;
    }
    .row .name { color: var(--fg); }
    .row.disabled .name { color: var(--fg-dim); }
    .val {
      background: transparent;
      border: 1px solid var(--border);
      color: var(--fg);
      font-family: inherit;
      font-size: inherit;
      padding: 0.15rem 0.7rem;
      cursor: pointer;
      min-width: 96px;
      text-align: center;
    }
    .val:hover:not(:disabled) { color: var(--accent); border-color: var(--accent); }
    .val:disabled { opacity: 0.45; cursor: default; }
    .hk { color: var(--fg-dim); font-size: 0.85em; min-width: 3.5em; text-align: right; }
    .hint { color: var(--fg-dim); font-size: 0.8em; margin-top: 0.6rem; }
  `;

  render() {
    const values = this.values ?? {};
    return html`
      <div class="backdrop" @click=${this._onBackdrop}>
        <div class="modal" @click=${(e) => e.stopPropagation()}>
          <div class="title">
            <span>── Settings ──</span>
            <button class="close" @click=${this._close} title="Close (Esc)">[ × ]</button>
          </div>
          ${SETTINGS.map((s) => {
            const disabled = s.scope === "table" && !this.tableActive;
            const current = values[s.key] ?? s.values[0];
            return html`
              <div class="row ${disabled ? "disabled" : ""}">
                <span class="name">${s.label}</span>
                <button
                  class="val"
                  ?disabled=${disabled}
                  @click=${() => this._cycle(s.key)}
                  title=${disabled ? "Available at a table" : `Cycle (${s.hotkey})`}
                >
                  ${current}${disabled ? "" : " ▸"}
                </button>
                <span class="hk">${s.hotkey}</span>
              </div>
            `;
          })}
          ${this.tableActive ? "" : html`<div class="hint">Pane toggles are available once you're at a table.</div>`}
        </div>
      </div>
    `;
  }

  _cycle(key) {
    this.dispatchEvent(new CustomEvent("setting-cycle", { detail: { key }, bubbles: true, composed: true }));
  }

  _onBackdrop() {
    this._close();
  }

  _close() {
    this.dispatchEvent(new CustomEvent("settings-close", { bubbles: true, composed: true }));
  }
}

customElements.define("settings-menu", SettingsMenu);

// --- <profile-page> -----------------------------------------------------
//
// Top-level profile home screen (profile-and-settings.md § B.5).  Renders the
// PROFILE wire payload: a stats grid, a cumulative-points ASCII line graph,
// and a recent-games list.  Stays dumb — derives win-rate / avg-win from raw
// counts and emits `profile-back` to return to the lobby.
function _relTime(ms) {
  if (ms == null) return "—";
  const secs = Math.max(0, Math.round((Date.now() - ms) / 1000));
  if (secs < 60) return "just now";
  const mins = Math.round(secs / 60);
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.round(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.round(hrs / 24);
  return `${days}d ago`;
}

function _signed(n) {
  if (n == null) return "—";
  return n > 0 ? `+${n}` : String(n);
}

// Spec 39: 8-cell ASCII progress bar for unearned achievements.
function _achBar(progress, target) {
  const cells = 8;
  const filled = target > 0 ? Math.min(cells, Math.floor((progress / target) * cells)) : 0;
  return `[${"█".repeat(filled)}${"░".repeat(cells - filled)}]`;
}

class ProfilePage extends LitElement {
  static properties = {
    profile: { type: Object }, // PROFILE payload, or null while loading
  };

  static styles = css`
    :host { display: block; }
    .wrap { border: 1px solid var(--border); padding: 1rem 1.25rem 1.25rem; }
    .head {
      display: flex;
      justify-content: space-between;
      align-items: baseline;
      gap: 1rem;
      margin-bottom: 1rem;
    }
    .who { color: var(--accent); font-size: 1.1em; }
    .back {
      background: transparent;
      border: 1px solid var(--border);
      color: var(--fg);
      font-family: inherit;
      font-size: inherit;
      padding: 0.25rem 0.75rem;
      cursor: pointer;
    }
    .back:hover { color: var(--accent); border-color: var(--accent); }
    .grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
      gap: 0.6rem 1.5rem;
      margin-bottom: 1.25rem;
    }
    .stat .label { color: var(--fg-dim); font-size: 0.85em; }
    .stat .value { color: var(--fg); font-size: 1.15em; }
    .section-title { color: var(--accent); margin: 0.5rem 0; }
    pre.graph {
      color: var(--accent);
      line-height: 1.05;
      margin: 0 0 1.25rem;
      overflow-x: auto;
    }
    table.recent { width: 100%; border-collapse: collapse; }
    table.recent th, table.recent td {
      text-align: left;
      padding: 0.2rem 0.6rem 0.2rem 0;
      border-bottom: 1px solid var(--border);
      font-weight: normal;
    }
    table.recent th { color: var(--fg-dim); font-size: 0.85em; }
    .win { color: var(--accent); }
    .loss { color: var(--fg-dim); }
    .empty { color: var(--fg-dim); padding: 1.5rem 0; }
    .watch-btn {
      background: none; border: none; color: var(--accent);
      cursor: pointer; font-family: inherit; font-size: inherit; padding: 0;
    }
    .watch-btn:hover { text-decoration: underline; }
    /* Spec 39: achievements — earned bright, in-progress dimmed with an
     * ASCII progress bar (terminal aesthetic; no images). */
    .ach-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(230px, 1fr));
      gap: 0.5rem 1.5rem;
      margin-bottom: 1.25rem;
    }
    .ach { color: var(--fg-dim); }
    .ach .ach-name { margin-left: 0.35rem; }
    .ach .ach-progress { margin-left: 0.5rem; font-size: 0.85em; }
    .ach .ach-desc { display: block; font-size: 0.8em; margin-left: 1.4rem; }
    .ach.earned, .ach.earned .ach-name { color: var(--accent); }
    .ach.earned .ach-badge { color: var(--accent-red); }
  `;

  render() {
    const p = this.profile;
    if (p == null) {
      return html`<div class="wrap"><div class="empty">Loading profile…</div></div>`;
    }
    const s = p.stats ?? {};
    const played = s.hands_played ?? 0;
    const won = s.hands_won ?? 0;
    const winRate = played > 0 ? `${((won / played) * 100).toFixed(1)}%` : "—";
    const avgWin = won > 0 ? `+${Math.round((s.total_win_points ?? 0) / won)}` : "—";

    return html`
      <div class="wrap">
        <div class="head">
          <span class="who">${p.account?.display_name ?? "—"} · profile</span>
          <button class="back" @click=${this._back} title="Back to lobby (Esc)">[ back ]</button>
        </div>

        ${played === 0
          ? html`<div class="empty">No games yet — play a hand and your stats will appear here.</div>`
          : html`
              <div class="grid">
                ${this._stat("Hands played", played)}
                ${this._stat("Win rate", winRate)}
                ${this._stat("Wins", won)}
                ${this._stat("Draws", s.draws ?? 0)}
                ${this._stat("Avg win size", avgWin)}
                ${this._stat("Best win (fan)", s.best_win_fan ?? "—")}
                ${this._stat("Total standing", _signed(s.total_score ?? 0))}
                ${this._stat("Last played", _relTime(s.last_played_ms))}
              </div>

              <div class="section-title">─ Point performance (cumulative) ─</div>
              <pre class="graph">${renderScoreGraph(p.series ?? [])}</pre>

              <div class="section-title">─ Recent games ─</div>
              ${this._recentTable(p.recent ?? [])}

              ${Array.isArray(p.achievements) && p.achievements.length
                ? html`
                    <div class="section-title">─ Achievements ─</div>
                    ${this._achievements(p.achievements)}
                  `
                : ""}
            `}
      </div>
    `;
  }

  // Spec 39: one row per catalog entry, in wire order — earned rows bright
  // with a filled star, in-progress rows dimmed with an ASCII bar.
  _achievements(list) {
    return html`<div class="ach-grid">
      ${list.map(
        (a) => html`<div class="ach ${a.earned ? "earned" : ""}">
          <span class="ach-badge">${a.earned ? "★" : "☆"}</span><span class="ach-name">${a.name}</span>
          <span class="ach-progress">${a.earned ? "" : `${_achBar(a.progress, a.target)} ${a.progress}/${a.target}`}</span>
          <span class="ach-desc">${a.desc}</span>
        </div>`,
      )}
    </div>`;
  }

  _stat(label, value) {
    return html`<div class="stat">
      <div class="label">${label}</div>
      <div class="value">${value}</div>
    </div>`;
  }

  _recentTable(recent) {
    if (recent.length === 0) {
      return html`<div class="empty">No recent games.</div>`;
    }
    return html`
      <table class="recent">
        <tr><th>When</th><th>Result</th><th>Points</th><th>Fan</th><th></th></tr>
        ${recent.map((h) => {
          const result = h.won
            ? html`<span class="win">WIN</span>`
            : h.terminal_kind === "EXHAUSTIVE_DRAW"
              ? html`<span class="loss">draw</span>`
              : html`<span class="loss">loss</span>`;
          return html`<tr>
            <td>${_relTime(h.started_at_ms)}</td>
            <td>${result}</td>
            <td class=${h.score_delta > 0 ? "win" : "loss"}>${_signed(h.score_delta)}</td>
            <td>${h.fan_total ?? "—"}</td>
            <td>
              ${h.hand_id
                ? html`<button class="watch-btn" @click=${() => this._watch(h.hand_id)}
                    title="Watch this hand">[ ▶ watch ]</button>`
                : ""}
            </td>
          </tr>`;
        })}
      </table>
    `;
  }

  _watch(handId) {
    // FB-04: ask the app shell to fetch + open a replay of this hand.
    this.dispatchEvent(
      new CustomEvent("profile-replay", { detail: { handId }, bubbles: true, composed: true }),
    );
  }

  _back() {
    this.dispatchEvent(new CustomEvent("profile-back", { bubbles: true, composed: true }));
  }
}

customElements.define("profile-page", ProfilePage);

// --- <replay-view> ------------------------------------------------------
// FB-04 (account-records-replay.md). Folds a REPLAY frame's projected event
// stream through the *live* reducer (applyEvent) and renderer (renderTable) at
// the user's pace — no replay-specific board code. Read-only: no prompts, no
// actions. `cursor` is the index of the next event to apply; the board shows
// events[0..cursor).
class ReplayView extends LitElement {
  static properties = {
    replay: { type: Object },     // the REPLAY frame {seat, snapshot, events, meta}
    cursor: { state: true },      // how many events have been applied
    playing: { state: true },
  };

  static styles = css`
    :host { display: block; color: var(--fg); font-family: inherit; }
    .wrap { border: 1px solid var(--border); padding: 0.75rem 1rem 1rem; }
    .head {
      display: flex; justify-content: space-between; align-items: center;
      margin-bottom: 0.5rem;
    }
    .who { color: var(--accent); }
    .back { background: none; border: 1px solid var(--border); color: var(--fg);
            cursor: pointer; font-family: inherit; padding: 0.1rem 0.5rem; }
    pre.board { margin: 0.5rem 0; line-height: 1.15; white-space: pre; overflow-x: auto; }
    .transport {
      display: flex; align-items: center; gap: 0.5rem; margin-top: 0.5rem;
      flex-wrap: wrap;
    }
    .transport button {
      background: none; border: 1px solid var(--border); color: var(--fg);
      cursor: pointer; font-family: inherit; padding: 0.15rem 0.55rem;
    }
    .transport button:disabled { opacity: 0.4; cursor: default; }
    .scrub { flex: 1; min-width: 8rem; }
    .pos { color: var(--fg-dim); font-size: 0.85em; white-space: nowrap; }
  `;

  constructor() {
    super();
    this.replay = null;
    this.cursor = 0;
    this.playing = false;
    this._timer = null;
  }

  disconnectedCallback() {
    super.disconnectedCallback();
    this._stop();
  }

  updated(changed) {
    // A fresh replay resets the cursor to the start.
    if (changed.has("replay") && this.replay) {
      this._stop();
      this.cursor = 0;
    }
  }

  // Fold events[0..cursor) onto a deep copy of the initial snapshot.
  _currentView() {
    const r = this.replay;
    if (!r) return null;
    let view = JSON.parse(JSON.stringify(r.snapshot ?? {}));
    const seat = r.seat;
    const events = Array.isArray(r.events) ? r.events : [];
    for (let i = 0; i < this.cursor && i < events.length; i++) {
      view = applyEvent(view, events[i], seat);
    }
    return view;
  }

  _count() {
    return Array.isArray(this.replay?.events) ? this.replay.events.length : 0;
  }

  _step(delta) {
    const n = this._count();
    this.cursor = Math.max(0, Math.min(n, this.cursor + delta));
    if (this.cursor >= n) this._stop();
  }

  _scrub(e) {
    this.cursor = Number(e.target.value);
    this._stop();
  }

  _togglePlay() {
    if (this.playing) {
      this._stop();
    } else {
      if (this.cursor >= this._count()) this.cursor = 0; // replay from start
      this.playing = true;
      this._timer = setInterval(() => {
        if (this.cursor >= this._count()) {
          this._stop();
          return;
        }
        this.cursor += 1;
      }, 700);
    }
  }

  _stop() {
    this.playing = false;
    if (this._timer !== null) {
      clearInterval(this._timer);
      this._timer = null;
    }
  }

  _close() {
    this._stop();
    this.dispatchEvent(new CustomEvent("replay-close", { bubbles: true, composed: true }));
  }

  render() {
    const r = this.replay;
    if (!r) return html`<div class="wrap">No replay loaded.</div>`;
    const view = this._currentView();
    const seat = r.seat;
    const n = this._count();
    const atEnd = this.cursor >= n;
    const board =
      atEnd && view && view.phase === "TERMINAL"
        ? renderHandEndSummary(view, seat)
        : renderTable(view, seat);
    const seatLabel = seat === -1 || seat == null ? "spectator view" : `seat ${seat}`;
    return html`
      <div class="wrap">
        <div class="head">
          <span class="who">▶ Replay · ${seatLabel} · ${r.meta?.ruleset_id ?? ""}</span>
          <button class="back" @click=${this._close} title="Back (Esc)">[ back ]</button>
        </div>
        <pre class="board">${board}</pre>
        <div class="transport">
          <button @click=${() => this._step(-1)} ?disabled=${this.cursor === 0}>[ ◀ ]</button>
          <button @click=${this._togglePlay}>${this.playing ? "[ ⏸ ]" : "[ ▶ ]"}</button>
          <button @click=${() => this._step(1)} ?disabled=${atEnd}>[ ▶| ]</button>
          <input
            class="scrub" type="range" min="0" max=${n} .value=${String(this.cursor)}
            @input=${this._scrub}
          />
          <span class="pos">${this.cursor} / ${n}</span>
        </div>
      </div>
    `;
  }
}

customElements.define("replay-view", ReplayView);

// --- <mahjong-app> ------------------------------------------------------

const THEME_STORAGE_KEY = "mahjong-theme";
const THEMES = ["dark", "light"];

const TILE_STYLE_STORAGE_KEY = "mahjong-tile-style";
const TILE_STYLES = ["ascii", "unicode"];

// Play-view layout: "minimal" (decluttered, large-print — the default while
// it's the focus of iteration) vs "classic" (the original stat-rich table,
// slated to grow into the stats-heavy view). Persisted like theme/tile-style.
const VIEW_MODE_STORAGE_KEY = "mahjong-view-mode";
const VIEW_MODES = ["minimal", "classic"];

const SOUND_STORAGE_KEY = "mahjong-sound"; // "on" | "off" (FB-06)

// Spec 29 Bug A: the session token is persisted so a full page reload restores
// the session via RESUME instead of bouncing the user back to the login form
// (which also made the profile page unreachable). localStorage (chosen over
// sessionStorage) keeps the user signed in across tab reopen, not just reload.
// Same private-mode/sandbox try/catch fallback as the theme/tile-style keys.
const SESSION_TOKEN_STORAGE_KEY = "mahjong.session_token";

function loadStoredSessionToken() {
  try {
    return localStorage.getItem(SESSION_TOKEN_STORAGE_KEY) || null;
  } catch {
    return null;
  }
}

function storeSessionToken(token) {
  try {
    if (token) localStorage.setItem(SESSION_TOKEN_STORAGE_KEY, token);
    else localStorage.removeItem(SESSION_TOKEN_STORAGE_KEY);
  } catch {
    // Storage unavailable — the token simply won't survive a reload. Non-fatal.
  }
}

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

function loadInitialViewMode() {
  try {
    const stored = localStorage.getItem(VIEW_MODE_STORAGE_KEY);
    if (VIEW_MODES.includes(stored)) return stored;
  } catch {
    // ignore.
  }
  return "minimal"; // minimal is the default play view
}

// Spec 40: discard layout — per-player rows (default) vs one combined pond.
const DISCARD_LAYOUT_STORAGE_KEY = "mahjong-discard-layout";
function loadInitialDiscardLayout() {
  try {
    const stored = localStorage.getItem(DISCARD_LAYOUT_STORAGE_KEY);
    if (stored === "rows" || stored === "pond") return stored;
  } catch {
    // ignore.
  }
  return "rows"; // per-player rows in seat order is the default
}

// FB-06: sound on by default; persisted as "off" when the user mutes.
function loadInitialMuted() {
  try {
    return localStorage.getItem(SOUND_STORAGE_KEY) === "off";
  } catch {
    return false;
  }
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
    viewMode: { state: true },
    discardLayout: { state: true },
    muted: { state: true },
    // Auth state — driven by HELLO.features and AUTH_RESPONSE.
    _authRequired: { state: true }, // bool: server sent features: ["auth"]
    _authState: { state: true },    // "idle"|"waiting"|"submitting"|"authed"|"error"
    _authError: { state: true },    // null | error string shown under the form
    _authMode: { state: true },     // "login" | "register" (invite-gated signup)
    // Lobby vs. in-game view.
    _view: { state: true },         // "lobby" | "table" | "profile"
    _lobbyTables: { state: true },  // array of TABLE_LIST.tables entries
    _lobbyHumans: { state: true },  // current composition pick (1..4)
    _lobbyError: { state: true },   // null | error string above the table list
    _sessionToken: { state: true }, // drives <feedback-button> visibility
    _availableBots: { state: true },// HELLO.bots — the create-table bot menu
    // Spec 29: these were missing from the reactive set, so mutating them did
    // NOT schedule a re-render — the UI only updated when some *other* reactive
    // change (e.g. a theme/colour toggle) flushed a render. That's why the
    // settings menu "sometimes" failed to open/close depending on the UI colour,
    // and why re-opening the profile while already on it could hang on "Loading".
    _settingsOpen: { state: true }, // settings overlay visibility
    _profile: { state: true },      // PROFILE payload (null while loading)
    _serverFeatures: { state: true },// HELLO.features — gates profile button, etc.
    _leaveArmed: { state: true },   // FB-14 leave button two-step confirm
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
    /* FB-14: armed "leave?" state reads as a warning. */
    .leave-btn.armed { color: var(--accent-red); border-color: var(--accent-red); }

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
    .resuming {
      color: var(--fg-dim);
      text-align: center;
      padding: 0.5rem;
    }
    .rejoin-notice {
      color: var(--accent);
      margin-bottom: 0.5rem;
      padding: 0.4rem 0.75rem;
      border: 1px solid var(--accent);
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
    .auth-toggle {
      margin-top: 0.9rem;
      color: var(--fg-dim);
      font-size: 0.9em;
    }
    .auth-toggle-link {
      color: var(--accent);
      text-decoration: none;
      border-bottom: 1px dotted var(--accent);
      cursor: pointer;
    }
    .auth-toggle-link:hover { color: var(--fg); }
  `;

  constructor() {
    super();
    this.route = "table"; // walking skeleton: go straight to the table page
    // Pane visibility lives here so it survives route transitions.
    this.panes = { chat: false, stats: false, score: false, spectator: false };
    this.theme = loadInitialTheme();
    this.tileStyle = loadInitialTileStyle();
    this.viewMode = loadInitialViewMode();
    this.discardLayout = loadInitialDiscardLayout();
    this.muted = loadInitialMuted();
    audioCues.setMuted(this.muted);
    this._conn = null;
    this._onKeydown = this._handleKeydown.bind(this);
    // Auth state — see Step 8.5.
    this._authRequired = false;
    this._authState = "idle";
    this._authError = null;
    this._authMode = "login";
    // Spec 29 Bug A: hydrate from storage so the HELLO handler can RESUME on a
    // fresh page load (not just an in-session websocket reconnect).
    this._sessionToken = loadStoredSessionToken();
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
    // Selectable bots advertised by HELLO.bots; empty until the server greets.
    this._availableBots = [];
    this._lobbyAutoRefresh = null;              // setInterval id while in lobby view
    this._lobbyTargetSeat = null;               // seat we're attempting to join (debug/diagnostic)
    // Settings + profile (Spec 28).
    this._settingsOpen = false;
    this._profile = null;
    this._serverFeatures = [];
    // Replay viewer (FB-04). Set from a REPLAY frame; cleared on close.
    this._replay = null;
    this._replayReturnView = "profile";  // where [back] returns to
    // FB-03 rejoin (reconnect-rejoin.md): seats this account holds, learned
    // from AUTH_RESPONSE.seat_holds[]. A single HELD hold auto-rejoins; more
    // than one (or a LIVE takeover candidate) renders rows in the lobby.
    this._seatHolds = [];
    this._rejoinNotice = null;                  // transient "Reconnected…" toast text
    // FB-14: in-game "leave table" escape hatch (two-step confirm).
    this._leaveArmed = false;
    this._leaveArmTimer = null;
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
    // Esc closes whichever overlay/screen is open (settings first, then profile).
    if (e.key === "Escape") {
      if (this._settingsOpen) {
        e.preventDefault();
        this._settingsOpen = false;
        return;
      }
      if (this._view === "profile") {
        e.preventDefault();
        this._closeProfile();
        return;
      }
    }
    // Alt+T toggles theme; Alt+M toggles view (minimal/classic); Alt+U toggles
    // tile style; Alt+, opens settings.
    // Other Alt-chords belong to <table-page>; we early-return to avoid
    // double-handling.
    if (!e.altKey || e.ctrlKey || e.metaKey || e.shiftKey) return;
    if (e.code === "KeyT") {
      e.preventDefault();
      this._toggleTheme();
    } else if (e.code === "KeyM") {
      e.preventDefault();
      this._toggleViewMode();
    } else if (e.code === "KeyU") {
      e.preventDefault();
      this._toggleTileStyle();
    } else if (e.code === "KeyD") {
      e.preventDefault();
      this._toggleDiscardLayout();
    } else if (e.code === "Comma") {
      e.preventDefault();
      this._settingsOpen = !this._settingsOpen;
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

  _toggleDiscardLayout() {
    this.discardLayout = this.discardLayout === "rows" ? "pond" : "rows";
    try {
      localStorage.setItem(DISCARD_LAYOUT_STORAGE_KEY, this.discardLayout);
    } catch {
      // ignore — non-fatal.
    }
  }

  _toggleViewMode() {
    this.viewMode = this.viewMode === "minimal" ? "classic" : "minimal";
    try {
      localStorage.setItem(VIEW_MODE_STORAGE_KEY, this.viewMode);
    } catch {
      // ignore — non-fatal.
    }
  }

  _toggleSound() {
    this.muted = !this.muted;
    audioCues.setMuted(this.muted);
    try {
      localStorage.setItem(SOUND_STORAGE_KEY, this.muted ? "off" : "on");
    } catch {
      // ignore — non-fatal.
    }
  }

  // --- Settings menu (Spec 28 Part A) ------------------------------------

  // Current value per settings.js descriptor key, for the menu to display.
  _settingsValues() {
    return {
      theme: this.theme,
      "view-mode": this.viewMode,
      "tile-style": this.tileStyle,
      "pane-chat": this.panes.chat ? "on" : "off",
      "pane-stats": this.panes.stats ? "on" : "off",
      "pane-score": this.panes.score ? "on" : "off",
      "pane-spectator": this.panes.spectator ? "on" : "off",
      "discard-layout": this.discardLayout,
      sound: this.muted ? "off" : "on",
    };
  }

  _onSettingCycle(e) {
    const key = e.detail?.key;
    if (key === "theme") {
      this._toggleTheme();
    } else if (key === "view-mode") {
      this._toggleViewMode();
    } else if (key === "tile-style") {
      this._toggleTileStyle();
    } else if (key === "sound") {
      this._toggleSound();
    } else if (key === "discard-layout") {
      this._toggleDiscardLayout();
    } else if (key.startsWith("pane-")) {
      // Route through table-page's _togglePane so its `panes` copy and ours
      // stay in sync via the existing `panes-changed` event (single source).
      const pane = key.slice("pane-".length);
      const tablePage = this.renderRoot.querySelector("table-page");
      if (tablePage && typeof tablePage._togglePane === "function") {
        tablePage._togglePane(pane);
      }
    }
  }

  // --- Profile home page (Spec 28 Part B) --------------------------------

  _openProfile() {
    this._profile = null; // loading state until PROFILE arrives
    this._view = "profile";
    this._settingsOpen = false;
    try {
      this._conn.send({ kind: "GET_PROFILE" });
    } catch (err) {
      console.warn("GET_PROFILE send failed:", err);
    }
  }

  _closeProfile() {
    this._profile = null;
    this._view = "lobby";
  }

  _onProfileReplay(e) {
    // FB-04: a "watch" click on a profile recent-games row. Fetch the replay;
    // the REPLAY frame handler flips the view to the viewer. [back] returns
    // here to the profile.
    const handId = e.detail?.handId;
    if (!handId) return;
    this._replayReturnView = "profile";
    this._lobbyError = null;
    try {
      this._conn.send({ kind: "GET_REPLAY", hand_id: handId });
    } catch (err) {
      console.warn("GET_REPLAY send failed:", err);
    }
  }

  _closeReplay() {
    this._replay = null;
    this._view = this._replayReturnView || "lobby";
    if (this._view === "profile") this._openProfile();  // refresh profile data
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
          // HELLO.bots is the create-table picker menu; absent on old servers.
          this._availableBots = Array.isArray(frame.bots) ? frame.bots : [];
          const feats = Array.isArray(frame.features) ? frame.features : [];
          this._serverFeatures = feats;
          if (feats.includes("auth")) {
            this._authRequired = true;
            if (this._sessionToken) {
              // Reconnect / reload path: we already hold a token (in memory
              // from this session, or rehydrated from localStorage on a fresh
              // load) — re-authenticate silently with RESUME instead of forcing
              // the user to log in again. The AUTH_RESPONSE handler returns us
              // to the lobby. The "resuming" state suppresses the login-form
              // flash while RESUME is in flight (Spec 29 Bug A).
              this._authState = "resuming";
              try {
                this._conn.send({ kind: "RESUME", session_token: this._sessionToken });
              } catch (err) {
                console.warn("RESUME failed to send:", err);
                this._authState = "waiting";
              }
            } else {
              this._authState = "waiting"; // triggers auth form render
            }
          } else {
            // No auth required — go straight to lobby.
            this._enterLobby();
          }
          return;
        }

        if (frame.kind === "AUTH_RESPONSE") {
          if (frame.ok) {
            this._sessionToken = frame.session_token ?? null;
            storeSessionToken(this._sessionToken); // Spec 29 Bug A: survive reload
            this._authState = "authed";
            this._authError = null;
            // FB-03: seats this account still holds (rejoinable). Captured
            // before _enterLobby so it can auto-rejoin a lone HELD seat.
            this._seatHolds = Array.isArray(frame.seat_holds) ? frame.seat_holds : [];
            this._enterLobby();
          } else {
            // Server allows up to 3 attempts on the same connection; keep the
            // form open so the user can correct their credentials. A failure
            // here also covers a stale/expired RESUME token — clear it so we
            // don't retry the dead token on every reconnect, and don't show a
            // scary "invalid credentials" message for an auto-resume.
            const wasResuming = this._authState === "resuming";
            if (this._sessionToken) {
              this._sessionToken = null;
              storeSessionToken(null);
            }
            this._authState = wasResuming ? "waiting" : "error";
            this._authError = wasResuming
              ? null
              : "Invalid credentials — please try again.";
          }
          return;
        }

        // --- Profile (Spec 28) ---------------------------------------------
        if (frame.kind === "PROFILE") {
          this._profile = frame;
          if (this._view !== "profile") this._view = "profile";
          return;
        }

        // --- Replay (FB-04) ------------------------------------------------
        if (frame.kind === "REPLAY") {
          this._replay = frame;
          this._view = "replay";
          return;
        }
        if (frame.kind === "ERROR" &&
            ["hand_not_found", "not_authorized", "replay_unavailable", "history_error"]
              .includes(frame.code)) {
          // Replay/history fetch failed — stay where we are, surface a hint.
          this._lobbyError = frame.message || `Replay unavailable (${frame.code}).`;
          return;
        }

        // --- Registration rejection (Spec 24 § 24.2) -----------------------
        // Success arrives as AUTH_RESPONSE { ok: true } (auto-login, handled
        // above); only the failure path is register-specific.
        if (frame.kind === "ERROR" && frame.code === "register_rejected") {
          this._authState = "error";
          this._authError = frame.message || "Registration failed — please try again.";
          return;
        }

        // --- Rate-limited login / register (Spec 24 § 24.3) ----------------
        // Both sign-in and register share the auth form, so one handler covers
        // both. Without this the form would hang in the "submitting" state.
        if (frame.kind === "ERROR" && frame.code === "rate_limited") {
          this._authState = "error";
          this._authError = frame.message || "Too many attempts — please wait and try again.";
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
          audioCues.play(cueForEvent(frame.event, pane.ownSeat)); // FB-06
          // First EVENT means the hand is actually running; cancel any
          // lobby polling that was still in flight.
          this._handStarted = true;
          if (this._lobbyPollHandle !== null) {
            clearTimeout(this._lobbyPollHandle);
            this._lobbyPollHandle = null;
          }
        } else if (frame.kind === "HAND_END" && frame.terminal && pane.seatView) {
          // The server sends end-of-hand as its own frame whose `terminal`
          // payload is the record HAND_END event minus its wrapper fields
          // (incl. the `event` discriminator). Re-add `event: "HAND_END"` so the
          // reducer routes it to applyHandEnd, which sets seatView.terminal and
          // the §22.9 summary (scores + fan + revealed hands) renders.
          const next = applyEvent(
            pane.seatView,
            { event: "HAND_END", ...frame.terminal },
            pane.ownSeat,
          );
          pane.setSnapshot(next, pane.ownSeat);
          audioCues.play(cueForTerminal(frame.terminal)); // FB-06: win flourish for all
        } else if (frame.kind === "PROMPT") {
          pane.setPrompt(frame);
          audioCues.play(cueForPrompt(frame)); // FB-06: escalating claim cue
        } else if (frame.kind === "CHAT_MESSAGE") {
          tablePage?.addChatMessage(frame); // Spec 38: table chat
        } else if (frame.kind === "READY_STATE") {
          pane.setReadyState(frame); // FB-19: live between-hand readiness roster
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
      pane.addEventListener("ready-submitted", () => {
        // FB-02: ack the end-of-hand summary so the server advances.
        try {
          this._conn.send({ kind: "READY", table_id: this._attachedTableId });
        } catch (err) {
          console.warn("READY send failed:", err);
        }
      });
      // Spec 38: chat-send bubbles composed from <chat-pane> through
      // <table-page>; listen at the shell, where the connection lives.
      this.addEventListener("chat-send", (e) => {
        try {
          this._conn.send({ kind: "CHAT", text: e.detail.text });
        } catch (err) {
          console.warn("CHAT send failed:", err);
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

    const register = this._authMode === "register";
    let msg;
    if (register) {
      const inviteCode = form.elements.invite_code.value.trim();
      if (!inviteCode) {
        this._authError = "An invite code is required to register.";
        return;
      }
      const displayName = form.elements.display_name.value.trim();
      // Field is "password" (plaintext) — transport security via TLS.
      // public-deployment.md § 24.2; server reuses AUTH_RESPONSE on success.
      msg = {
        kind: "REGISTER",
        username,
        password,
        display_name: displayName || username,
        invite_code: inviteCode,
      };
    } else {
      // wire-protocol.md § AUTH_REQUEST.
      msg = { kind: "AUTH_REQUEST", username, password };
    }

    // "submitting" disables the form while we wait for the server's reply.
    this._authState = "submitting";
    this._authError = null;
    try {
      this._conn.send(msg);
    } catch (err) {
      console.warn("auth send failed:", err);
      this._authState = "waiting";
      this._authError = "Failed to send — is the server running?";
    }
  }

  _onToggleAuthMode(e) {
    e.preventDefault();
    this._authMode = this._authMode === "register" ? "login" : "register";
    this._authState = "waiting";
    this._authError = null;
  }

  // --- Table-discovery helpers (Step 8.5) ---------------------------------

  _doTableDiscovery() {
    try {
      this._conn.send({ kind: "LIST_TABLES" });
    } catch (err) {
      console.warn("LIST_TABLES send failed:", err);
    }
  }

  _doCreateTable(humans, options = null, seats = null) {
    // The lobby panel passes the chosen composition (incl. per-seat bot
    // selections); falls back to the URL-param human count for auto-flows.
    const n = Number.isFinite(humans) ? humans : this._desiredHumans;
    const msg = {
      kind: "CREATE_TABLE",
      seats: Array.isArray(seats) ? seats : _seatsForHumanCount(n),
    };
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
    this._rejoinNotice = null;
    this._disarmLeave();
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
    // FB-03: if the account holds a seat, either auto-rejoin (unambiguous) or
    // leave the holds for the lobby to render as "▶ Rejoin" rows.
    this._maybeAutoRejoin();
  }

  _maybeAutoRejoin() {
    const holds = Array.isArray(this._seatHolds) ? this._seatHolds : [];
    const held = holds.filter((h) => h && h.state === "HELD");
    // Exactly one hold, and it's HELD → rejoin without asking. Anything
    // ambiguous (>1 hold, or a LIVE takeover candidate) goes to the lobby
    // rows so the player chooses.
    if (holds.length === 1 && held.length === 1) {
      const h = held[0];
      this._seatHolds = [];
      this._rejoinNotice = `Reconnecting to your game (table ${h.table_id})…`;
      this._lobbyTargetSeat = { tableId: h.table_id, seat: h.seat };
      this._doAttach(h.table_id, h.seat);
    }
  }

  _onLobbyRejoin(e) {
    // A rejoin row was clicked — it's just an ATTACH to a held seat (the
    // server routes a same-user ATTACH on a HELD seat to its resume path).
    const { tableId, seat } = e.detail || {};
    if (tableId == null || seat == null) return;
    this._seatHolds = [];
    this._lobbyError = null;
    this._lobbyTargetSeat = { tableId, seat };
    this._doAttach(tableId, seat);
  }

  _enterTableView() {
    this._view = "table";
    this._rejoinNotice = null;  // landed in the game — drop the reconnect toast
    this._disarmLeave();
    if (this._lobbyAutoRefresh !== null) {
      clearInterval(this._lobbyAutoRefresh);
      this._lobbyAutoRefresh = null;
    }
  }

  // --- FB-14: leave table → back to the main menu -------------------------

  _disarmLeave() {
    this._leaveArmed = false;
    if (this._leaveArmTimer !== null) {
      clearTimeout(this._leaveArmTimer);
      this._leaveArmTimer = null;
    }
  }

  _onLeaveTable() {
    // Two-step confirm: first click arms ("[ leave? ]"), second click within
    // 4s actually leaves. Avoids forfeiting a seat on a stray click.
    if (!this._leaveArmed) {
      this._leaveArmed = true;
      this._leaveArmTimer = setTimeout(() => this._disarmLeave(), 4000);
      return;
    }
    this._disarmLeave();
    // Tell the server we're leaving (releases the seat; the orchestrator
    // returns this connection to its lobby loop). Sent before _enterLobby's
    // LIST_TABLES so the server processes them in that order.
    try {
      this._conn.send({ kind: "DETACH", reason: "leaving" });
    } catch (err) {
      console.warn("DETACH send failed:", err);
    }
    // Switch views optimistically — the escape hatch must work even if the
    // server never acks (that's the hung-table case this exists for).
    const pane = this.renderRoot.querySelector("table-page")?.gamePane;
    if (pane) pane.clearPrompt();
    this._enterLobby();
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
    const { humans, options, seats } = e.detail;
    if (Number.isFinite(humans) && humans >= 1 && humans <= 4) {
      this._lobbyHumans = humans;
    }
    this._lobbyError = null;
    this._doCreateTable(this._lobbyHumans, options ?? null, seats ?? null);
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
    const register = this._authMode === "register";
    return html`
      <div class="auth-overlay">
        <div class="auth-title">
          ${register ? "── Create account ──" : "── Sign in ──"}
        </div>
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
          ${register
            ? html`<div class="auth-form-row">
                <label class="auth-label">Display name</label>
                <input
                  class="auth-input"
                  type="text"
                  name="display_name"
                  ?disabled=${submitting}
                  autocomplete="nickname"
                />
              </div>`
            : ""}
          <div class="auth-form-row">
            <label class="auth-label">Password</label>
            <input
              class="auth-input"
              type="password"
              name="password"
              ?disabled=${submitting}
              autocomplete=${register ? "new-password" : "current-password"}
            />
          </div>
          ${register
            ? html`<div class="auth-form-row">
                <label class="auth-label">Invite code</label>
                <input
                  class="auth-input"
                  type="text"
                  name="invite_code"
                  placeholder="inv_…"
                  ?disabled=${submitting}
                />
              </div>`
            : ""}
          <div class="auth-actions">
            <button class="auth-submit" type="submit" ?disabled=${submitting}>
              ${submitting
                ? register
                  ? "[ creating… ]"
                  : "[ signing in… ]"
                : register
                  ? "[ Create account ]"
                  : "[ Sign in ]"}
            </button>
          </div>
        </form>
        <div class="auth-toggle">
          ${register
            ? html`Have an account?
                <a class="auth-toggle-link" href="#" @click=${this._onToggleAuthMode}>Sign in</a>`
            : html`Need an account?
                <a class="auth-toggle-link" href="#" @click=${this._onToggleAuthMode}>Register</a>
                <span class="auth-hint">(an invite code is required)</span>`}
        </div>
      </div>
    `;
  }

  render() {
    const nextTheme = this.theme === "dark" ? "light" : "dark";
    // Spec 29 Bug A: while a stored token is being RESUMEd on load, show a
    // brief "restoring session" splash instead of the login form, and keep the
    // lobby/table/profile hidden until auth resolves (so we don't flash the
    // form or the empty lobby on every reload).
    const showResuming = this._authRequired && this._authState === "resuming";
    // Show the auth form when the server requires auth and we haven't authed yet.
    const showAuth =
      this._authRequired && this._authState !== "authed" && !showResuming;
    const showReplay = !showAuth && !showResuming && this._view === "replay";
    const showLobby = !showAuth && !showResuming && this._view === "lobby";
    const showProfile = !showAuth && !showResuming && this._view === "profile";
    // Profile needs the server to persist history; gate the button on the
    // advertised feature (older/no-persistence servers omit it).
    const profileSupported = (this._serverFeatures ?? []).includes("profile");
    return html`
      <header>
        <pre>
 ╔══════════════════════════════════════════════════════════╗
 ║   Mahjong / 麻将        — web client                     ║
 ╚══════════════════════════════════════════════════════════╝</pre>
        <div class="controls">
          ${this._view === "table" && !showAuth
            ? html`<button
                class="theme-btn leave-btn ${this._leaveArmed ? "armed" : ""}"
                @click=${this._onLeaveTable}
                title="Leave the table and return to the main menu"
              >
                ${this._leaveArmed ? "[ leave? ]" : "[ ⌂ menu ]"}
              </button>`
            : ""}
          ${!showAuth && profileSupported
            ? html`<button
                class="theme-btn"
                @click=${this._openProfile}
                title="Your profile & stats"
              >
                [ profile ]
              </button>`
            : ""}
          <button
            class="theme-btn"
            @click=${() => { this._settingsOpen = true; }}
            title="Settings (Alt+,)"
          >
            [ ⚙ ]<span class="hint">Alt+,</span>
          </button>
          <button
            class="theme-btn"
            @click=${this._toggleTheme}
            title="Toggle theme (Alt+T)"
          >
            [ ${this.theme} → ${nextTheme} ]<span class="hint">Alt+T</span>
          </button>
          <!-- Tile-style toggle lives in the Settings menu now (Spec 29). The
               Alt+U chord still works; the header button was redundant. -->
        </div>
      </header>
      ${showAuth ? this._renderAuthForm() : ""}
      ${showResuming
        ? html`<div class="auth-overlay"><div class="resuming">Restoring session…</div></div>`
        : ""}
      ${showLobby
        ? html`
            ${this._lobbyError
              ? html`<div class="auth-error">${this._lobbyError}</div>`
              : ""}
            ${this._rejoinNotice
              ? html`<div class="rejoin-notice">${this._rejoinNotice}</div>`
              : ""}
            <lobby-view
              .tables=${this._lobbyTables}
              .seatHolds=${this._seatHolds}
              .desiredHumans=${this._lobbyHumans}
              .availableBots=${this._availableBots}
              @lobby-join=${this._onLobbyJoin.bind(this)}
              @lobby-rejoin=${this._onLobbyRejoin.bind(this)}
              @lobby-create=${this._onLobbyCreate.bind(this)}
              @lobby-refresh=${this._onLobbyRefresh.bind(this)}
            ></lobby-view>
          `
        : ""}
      ${showProfile
        ? html`<profile-page
            .profile=${this._profile}
            @profile-back=${this._closeProfile}
            @profile-replay=${this._onProfileReplay.bind(this)}
          ></profile-page>`
        : ""}
      ${showReplay
        ? html`<replay-view
            .replay=${this._replay}
            @replay-close=${this._closeReplay.bind(this)}
          ></replay-view>`
        : ""}
      <table-page
        .panes=${this.panes}
        .tileStyle=${this.tileStyle}
        .viewMode=${this.viewMode}
        .discardLayout=${this.discardLayout}
        .tableId=${this._attachedTableId}
        ?hidden=${showLobby || showAuth || showProfile || showResuming || showReplay}
      ></table-page>
      ${this._settingsOpen
        ? html`<settings-menu
            .values=${this._settingsValues()}
            .tableActive=${this._view === "table"}
            @setting-cycle=${this._onSettingCycle}
            @settings-close=${() => { this._settingsOpen = false; }}
          ></settings-menu>`
        : ""}
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

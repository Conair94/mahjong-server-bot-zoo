// Prompt-bar rendering + key-binding logic for Step 7.5c.iii.
//
// Pure functions, no DOM dependencies beyond Lit's html tag. Kept in its
// own module so it's easy to unit-test from a JS harness and so the
// keystroke-to-action mapping is the single source of truth.
//
// Key map (locked 2026-05-24):
//   PASS   → Space
//   PENG   → P
//   CHI    → C
//   GANG   → G    (EXPOSED / CONCEALED)
//   BUGANG → B    (GANG with kind=ADDED)
//   HU     → H
//   PLAY   → tile keys 1..9 0 - = [ ]  → *display* positions 0..13 (the
//            renderer pulls the just-drawn tile out of sort order to the
//            end; the keys follow what's on screen — FB-18).
//            Arrow Left/Right nudge the selection by one display slot.
//            Enter confirms PLAY for the selected tile (or, with no
//            explicit selection, the just-drawn tile from the view's
//            authoritative `last_drawn` slot; no draw → no-op).

import { html } from "lit";

// --- key tables ---

// `event.code` (physical key) → tile slot index. Using `code` rather than
// `key` so the binding works across keyboard layouts/locales.
const TILE_CODE_TO_INDEX = {
  Digit1: 0,
  Digit2: 1,
  Digit3: 2,
  Digit4: 3,
  Digit5: 4,
  Digit6: 5,
  Digit7: 6,
  Digit8: 7,
  Digit9: 8,
  Digit0: 9,
  Minus: 10,
  Equal: 11,
  BracketLeft: 12,
  BracketRight: 13,
};

const TILE_KEY_LABELS = ["1", "2", "3", "4", "5", "6", "7", "8", "9", "0", "-", "=", "[", "]"];

// --- public helpers ---

export function tileIndexForKeyCode(code) {
  const idx = TILE_CODE_TO_INDEX[code];
  return idx === undefined ? null : idx;
}

/**
 * The hand's *display* order: engine-sorted concealed with the just-drawn
 * tile pulled out and appended last (mirroring the physical-table habit of
 * keeping the draw separate). Single source of truth shared by the renderer
 * and the keystroke layer so "the Nth tile on screen" and "the Nth tile key"
 * can never disagree (FB-18).
 *
 * `lastDrawn` is the view's `{seat, tile}` slot (or null); `ownSeat` gates it
 * to the owning seat. Returns [{token, origIdx, isJustDrawn}], where
 * `origIdx` indexes the raw `concealed` array (the engine-facing identity a
 * selection is stored as).
 */
export function handDisplayOrder(concealed, lastDrawn, ownSeat) {
  const tiles = concealed ?? [];
  const hasJustDrawn =
    lastDrawn != null && lastDrawn.seat === ownSeat && lastDrawn.tile != null;
  // findIndex removes exactly one copy when the hand holds duplicates.
  const drawnIdx = hasJustDrawn ? tiles.findIndex((t) => t === lastDrawn.tile) : -1;

  const order = [];
  tiles.forEach((token, i) => {
    if (i === drawnIdx) return;
    order.push({ token, origIdx: i, isJustDrawn: false });
  });
  if (drawnIdx >= 0) {
    order.push({ token: tiles[drawnIdx], origIdx: drawnIdx, isJustDrawn: true });
  }
  return order;
}

export function tileKeyLabel(index) {
  return TILE_KEY_LABELS[index] ?? "?";
}

export function keyForAction(action) {
  switch (action?.type) {
    case "PASS":
      return "Space";
    case "PENG":
      return "P";
    case "CHI":
      return "C";
    case "GANG":
      return action.kind === "ADDED" ? "B" : "G";
    case "HU":
      return "H";
    default:
      return null;
  }
}

/**
 * Match an action from `prompt.legal_actions` for the given keystroke.
 * Returns `null` if no legal action corresponds.
 *
 * `ownConcealed` is the owning seat's concealed tile list (Tile[]), used to
 * resolve an explicit `selectedTile` (a raw index into it). `lastDrawnTile`
 * is the owning seat's just-drawn tile (or null) — the Enter fallback. The
 * engine re-sorts concealed after every draw, so `ownConcealed[length-1]` is
 * the highest-*sorting* tile, not the draw; only the view's `last_drawn`
 * slot is authoritative (FB-18 / the 8.7.e lesson).
 */
export function actionForKey(eventCode, prompt, selectedTile, ownConcealed, lastDrawnTile = null) {
  if (!prompt) return null;
  const legal = prompt.legal_actions ?? [];

  // Special-action keys map by event.code to a single action `type` filter.
  if (eventCode === "Space") {
    return findFirst(legal, (a) => a.type === "PASS");
  }
  if (eventCode === "KeyP") {
    return findFirst(legal, (a) => a.type === "PENG");
  }
  if (eventCode === "KeyC") {
    return findFirst(legal, (a) => a.type === "CHI");
  }
  if (eventCode === "KeyG") {
    return findFirst(legal, (a) => a.type === "GANG" && a.kind !== "ADDED");
  }
  if (eventCode === "KeyB") {
    return findFirst(legal, (a) => a.type === "GANG" && a.kind === "ADDED");
  }
  if (eventCode === "KeyH") {
    return findFirst(legal, (a) => a.type === "HU");
  }

  // Enter: confirm PLAY for the currently-selected tile. With no explicit
  // selection, fall back to the just-drawn tile (tsumogiri — the dominant
  // choice during DISCARD). No selection and no draw (e.g. the forced
  // discard after a claim) → no-op rather than guessing a tile.
  if (eventCode === "Enter") {
    let tile = null;
    if (selectedTile != null) {
      tile = ownConcealed?.[selectedTile] ?? null;
    } else if (lastDrawnTile != null) {
      tile = lastDrawnTile;
    }
    if (tile == null) return null;
    return findFirst(legal, (a) => a.type === "PLAY" && a.tile === tile);
  }

  return null;
}

function findFirst(arr, pred) {
  for (const item of arr) {
    if (pred(item)) return item;
  }
  return null;
}

/**
 * All CHI options in the prompt, in the server's order. A single discard can
 * admit up to three distinct sequences (e.g. B4 → B2B3, B3B5, B5B6); the
 * server emits one CHI action per sequence. Returns [] when none are legal.
 *
 * The keystroke layer uses this to drive the staged chooser: pressing C with
 * 2+ options enters a sub-mode where a digit key picks which run to take,
 * instead of silently always taking the first (the pre-fix behaviour).
 */
export function chiOptions(prompt) {
  if (!prompt) return [];
  return (prompt.legal_actions ?? []).filter((a) => a.type === "CHI");
}

// --- rendering ---

/**
 * Render the prompt bar listing each legal action and its key binding.
 *
 * PLAY actions collapse to a single tile-picker hint (otherwise a 14-tile
 * hand would generate 14 buttons and dominate the layout); the in-hand
 * cursor render (next to the concealed row) is the per-tile UI.
 */
// True when the prompt offers a real claim on another seat's discard —
// i.e. a CLAIM_WINDOW prompt with at least one non-PASS option (PENG / CHI
// / GANG / HU). PASS-only claim windows (no actual choice) don't count.
// Drives the attention cue in §22.2; shared so the prompt-bar class and the
// pane-header chip use one predicate.
export function isClaimAvailable(prompt) {
  if (!prompt || prompt.phase !== "CLAIM_WINDOW") return false;
  return (prompt.legal_actions ?? []).some((a) => a.type !== "PASS");
}

export function renderPromptBar(prompt, chiChoosing = null) {
  if (!prompt) return null;
  const legal = prompt.legal_actions ?? [];
  const plays = legal.filter((a) => a.type === "PLAY");
  const chis = legal.filter((a) => a.type === "CHI");
  // CHI is rendered as its own collapsed chip below — exclude it from `others`
  // so multiple sequences don't each emit a misleading "[C]" chip.
  const others = legal.filter((a) => a.type !== "PLAY" && a.type !== "CHI");
  const barClass = isClaimAvailable(prompt) ? "prompt-bar claim-active" : "prompt-bar";

  // Staged chooser: once the player presses C with 2+ sequences available, each
  // option is numbered so they pick which run to take (Esc backs out).
  if (chiChoosing && chiChoosing.length > 0) {
    return html`
      <div class=${barClass}>
        <span class="prompt-bar-label">Which chi? —</span>
        ${chiChoosing.map(
          (a, i) =>
            html`<span class="prompt-action">[<kbd>${i + 1}</kbd>] ${actionLabel(a)}</span>`,
        )}
        <span class="prompt-action">[<kbd>Esc</kbd>] cancel</span>
      </div>
    `;
  }

  return html`
    <div class=${barClass}>
      <span class="prompt-bar-label">Your turn —</span>
      ${others.map(
        (a) =>
          html`<span class="prompt-action">[<kbd>${keyForAction(a)}</kbd>] ${actionLabel(a)}</span>`,
      )}
      ${chis.length === 1
        ? html`<span class="prompt-action">[<kbd>C</kbd>] ${actionLabel(chis[0])}</span>`
        : chis.length > 1
          ? html`<span class="prompt-action">[<kbd>C</kbd>] Chi… (${chis.length} options)</span>`
          : ""}
      ${plays.length > 0
        ? html`<span class="prompt-action prompt-play"
            >[<kbd>1-=[]</kbd>] Play tile · [<kbd>Enter</kbd>] confirm</span
          >`
        : ""}
    </div>
  `;
}

function actionLabel(action) {
  switch (action.type) {
    case "PASS":
      return "Pass";
    case "PENG":
      return `Peng ${action.tile ?? ""}`.trim();
    case "CHI":
      return `Chi ${(action.tiles ?? []).join(" ")}`.trim();
    case "GANG":
      return action.kind === "ADDED"
        ? `Bugang ${action.tile ?? ""}`.trim()
        : `Gang ${action.tile ?? ""}`.trim();
    case "HU":
      return "Hu";
    default:
      return action.type;
  }
}

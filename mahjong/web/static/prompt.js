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
//   PLAY   → tile keys 1..9 0 - = [ ]  → concealed indices 0..13
//            Arrow Left/Right nudge the selected index by one slot.
//            Enter confirms PLAY for the selected tile (or, if no
//            explicit selection, the last tile in concealed — the
//            just-drawn one during DISCARD phase).

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
 * `ownConcealed` is the owning seat's concealed tile list (Tile[]). Used to
 * resolve tile-selection keys and the Enter-confirm shortcut for PLAY.
 */
export function actionForKey(eventCode, prompt, selectedTile, ownConcealed) {
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
  // selection, fall back to the last concealed tile — during DISCARD this is
  // the just-drawn tile, which is the dominant choice.
  if (eventCode === "Enter") {
    let idx = selectedTile;
    if (idx == null && ownConcealed && ownConcealed.length > 0) {
      idx = ownConcealed.length - 1;
    }
    if (idx == null) return null;
    const tile = ownConcealed?.[idx];
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

// --- rendering ---

/**
 * Render the prompt bar listing each legal action and its key binding.
 *
 * PLAY actions collapse to a single tile-picker hint (otherwise a 14-tile
 * hand would generate 14 buttons and dominate the layout); the in-hand
 * cursor render (next to the concealed row) is the per-tile UI.
 */
export function renderPromptBar(prompt) {
  if (!prompt) return null;
  const legal = prompt.legal_actions ?? [];
  const plays = legal.filter((a) => a.type === "PLAY");
  const others = legal.filter((a) => a.type !== "PLAY");

  return html`
    <div class="prompt-bar">
      <span class="prompt-bar-label">Your turn —</span>
      ${others.map(
        (a) =>
          html`<span class="prompt-action">[<kbd>${keyForAction(a)}</kbd>] ${actionLabel(a)}</span>`,
      )}
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

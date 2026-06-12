// Hand-stats rendering (Spec 37): the Alt+S detail panel, driven verbatim by
// `PROMPT.stats` — the client does no game math (authoritative-state rule);
// it only formats what the server sent.
//
// Revision (2026-06-12): stats are discard-only and pane-only. The old inline
// strip and the CLAIM-time `hand`/`claims` rendering are gone — the server
// now attaches `stats` (a per-candidate discard table) solely on DISCARD
// prompts, and surfaces nothing on a table that opted out (`stats_enabled`).
//
// Pure functions, no DOM dependencies beyond Lit's html tag — same shape as
// prompt.js so the formatting is unit-testable through the real frame
// dispatch.

import { html, nothing } from "lit";
import { tile } from "./render.js";

// --- pure helpers ---------------------------------------------------------

export function shantenLabel(shanten) {
  return shanten === 0 ? "TENPAI" : `${shanten}-shanten`;
}

function totalRemaining(tiles) {
  return tiles.reduce((acc, t) => acc + (t.remaining ?? 0), 0);
}

// --- shared fragments ------------------------------------------------------

// One wait/effective tile: glyph ×remaining, with fan when at tenpai.
// Sub-floor waits (fan_discard < floor) and dead waits (remaining 0) are
// visually flagged — those are exactly the traps (FB-15) the stats exist
// to surface.
function tileEntry(entry, floor, options) {
  const isWait = entry.fan_discard !== undefined;
  const dead = entry.remaining === 0;
  const subFloor = isWait && entry.fan_discard < floor;
  const cls = ["stat-tile", dead ? "dead" : "", subFloor ? "sub-floor" : ""]
    .filter(Boolean)
    .join(" ");
  return html`<span class=${cls}
    >${tile(entry.tile, options)}×${entry.remaining}${isWait
      ? html`<span class="fan"
          >&nbsp;${entry.fan_discard}f/${entry.fan_self_draw}f</span
        >${subFloor ? html`<span class="floor-mark">&lt;floor</span>` : nothing}`
      : nothing}</span
  >`;
}

function tilesInline(tiles, floor, options, limit = Infinity) {
  const shown = tiles.slice(0, limit);
  const more = tiles.length - shown.length;
  return html`${shown.map((t) => tileEntry(t, floor, options))}${more > 0
    ? html`<span class="more">+${more} more</span>`
    : nothing}`;
}

// --- the detail panel (stats-pane, Alt+S) -----------------------------------

// `statsEnabled` comes from the table snapshot (`stats_enabled`); when the
// table opted out we say so explicitly rather than showing an empty pane.
export function renderStatsDetail(prompt, options = {}, statsEnabled = true) {
  if (statsEnabled === false) {
    return html`<div class="placeholder">This game has stats disabled.</div>`;
  }

  const stats = prompt?.stats;
  if (!stats || !Array.isArray(stats.discards)) {
    return html`<div class="placeholder">
      (hand analysis appears when it's your turn to discard)
    </div>`;
  }

  const header = html`<div class="stats-meta">
    floor ${stats.floor}f · wall ${stats.wall_remaining}
  </div>`;

  return html`${header}
    <table class="stats-table">
      <thead>
        <tr><th>discard</th><th>leaves</th><th>advancing tiles</th></tr>
      </thead>
      <tbody>
        ${stats.discards.map(
          (row) => html`<tr>
            <td>${tile(row.tile, options)}</td>
            <td>${shantenLabel(row.shanten)}</td>
            <td>
              ${tilesInline(row.tiles, stats.floor, options)}
              <span class="total">(${totalRemaining(row.tiles)})</span>
            </td>
          </tr>`,
        )}
      </tbody>
    </table>`;
}

// Hand-stats rendering (Spec 37): the in-pane strip and the Alt+S detail
// panel, both driven verbatim by `PROMPT.stats` — the client does no game
// math (authoritative-state rule); it only formats what the server sent.
//
// Pure functions, no DOM dependencies beyond Lit's html tag — same shape as
// prompt.js so the formatting is unit-testable through the real frame
// dispatch.

import { html, nothing } from "lit";
import { tile } from "./render.js";

// --- pure helpers ---------------------------------------------------------

// The stats row for the player's current selection. `selectedTile` is an
// index into `ownConcealed` (game-pane convention); no explicit selection
// falls back to the server-sorted best line (`discards[0]`).
export function selectedDiscardRow(stats, selectedTile, ownConcealed) {
  const rows = stats?.discards;
  if (!Array.isArray(rows) || rows.length === 0) return null;
  if (selectedTile != null && ownConcealed && ownConcealed[selectedTile] != null) {
    const token = ownConcealed[selectedTile];
    const row = rows.find((r) => r.tile === token);
    if (row) return row;
  }
  return rows[0];
}

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

// --- the strip (inside game-pane, both view modes) --------------------------

export function renderStatsStrip(prompt, selectedTile, ownConcealed, options = {}) {
  const stats = prompt?.stats;
  if (!stats) return nothing;

  if (Array.isArray(stats.discards)) {
    const row = selectedDiscardRow(stats, selectedTile, ownConcealed);
    if (!row) return nothing;
    const best = stats.discards[0];
    const isBest = row === best;
    return html`<div class="stats-strip" data-kind="discard">
      <span class="lead">${tile(row.tile, options)} → ${shantenLabel(row.shanten)}</span>
      ${row.shanten > 0
        ? html`<span class="accept"
            >accepts ${row.tiles.length} kinds / ${totalRemaining(row.tiles)} tiles</span
          >`
        : nothing}
      <span class="tiles">${tilesInline(row.tiles, stats.floor, options, 6)}</span>
      ${!isBest
        ? html`<span class="best-hint"
            >best: ${tile(best.tile, options)} → ${shantenLabel(best.shanten)}</span
          >`
        : nothing}
    </div>`;
  }

  if (stats.hand) {
    const hand = stats.hand;
    return html`<div class="stats-strip" data-kind="claim">
      <span class="lead">now: ${shantenLabel(hand.shanten)}</span>
      <span class="tiles">${tilesInline(hand.tiles, stats.floor, options, 6)}</span>
      ${(stats.claims ?? []).map((c) => {
        const improves = c.shanten_after < hand.shanten;
        return html`<span class="claim-option ${improves ? "improves" : "neutral"}"
          >${c.action.type} → ${shantenLabel(c.shanten_after)}</span
        >`;
      })}
    </div>`;
  }
  return nothing;
}

// --- the detail panel (stats-pane, Alt+S) -----------------------------------

export function renderStatsDetail(prompt, options = {}) {
  const stats = prompt?.stats;
  if (!stats) {
    return html`<div class="placeholder">
      (hand analysis appears with your next turn or claim window)
    </div>`;
  }

  const header = html`<div class="stats-meta">
    floor ${stats.floor}f · wall ${stats.wall_remaining}
  </div>`;

  if (Array.isArray(stats.discards)) {
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

  if (stats.hand) {
    return html`${header}
      <div class="stats-hand">
        <div class="lead">hand: ${shantenLabel(stats.hand.shanten)}</div>
        <div class="tiles">${tilesInline(stats.hand.tiles, stats.floor, options)}</div>
      </div>
      ${(stats.claims ?? []).length
        ? html`<table class="stats-table">
            <thead>
              <tr><th>claim</th><th>reaches</th></tr>
            </thead>
            <tbody>
              ${stats.claims.map(
                (c) => html`<tr>
                  <td>
                    ${c.action.type}${c.action.tile
                      ? html` ${tile(c.action.tile, options)}`
                      : nothing}${Array.isArray(c.action.tiles)
                      ? c.action.tiles.map((t) => html` ${tile(t, options)}`)
                      : nothing}
                  </td>
                  <td>${shantenLabel(c.shanten_after)}</td>
                </tr>`,
              )}
            </tbody>
          </table>`
        : nothing}`;
  }
  return header;
}

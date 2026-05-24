// Snapshot rendering — step 7.5c.i.
//
// Pure functions: take a SeatView (state-schema.md § Per-seat projection)
// and return a Lit html template fragment for <game-pane> to drop inside a
// <pre>. Returns templates (not strings) so tile spans can be colored by
// suit / dragon-color via CSS.
//
// Display conventions (locked 2026-05-23 in conversation with user):
//
// - Suited tile shorthand is **rank-first**: bamboo `1B`, characters `1C`,
//   dots `1D`. Engine prefixes (W/B/T) are remapped for display so the
//   on-screen letter matches the suit's English name:
//       engine W (wan / characters)  -> display C  (in red)
//       engine B (bing / dots)       -> display D  (in foreground)
//       engine T (tiao / bamboo)     -> display B  (in green)
// - Winds: direction-letter + W → `EW SW WW NW` (engine F1..F4).
// - Flowers: rank + F → `1F..8F` (engine H1..H8).
// - Dragons: **always** colored Unicode glyphs regardless of tile_style
//   setting — `🀄` (red), `🀅` (green), `🀆` (foreground).
// - tile_style="unicode" swaps the suited / wind / flower shorthands for
//   the Unicode mahjong tile glyphs (U+1F000..U+1F029); colors carry over.
// - Face-down tiles: `▒▒` in ASCII mode, `🀫` (back-of-tile glyph) in
//   Unicode mode.
//
// Seat positioning (locked same conversation): own seat at the bottom; the
// player on your *right* (next in counterclockwise play order) renders at
// the top, then across, then your left, then you. Mahjong play order is
// counterclockwise (E → S → W → N), so right = (own + 1) % 4.

import { html } from "lit";

const WIND_NAME = { F1: "East", F2: "South", F3: "West", F4: "North" };
const SEAT_WIND_NAME = ["East", "South", "West", "North"]; // by seat index

const PHASE_LABEL = {
  DEAL: "Deal",
  DRAW: "Draw",
  DISCARD: "Discard",
  CLAIM_WINDOW: "Claim window",
  TERMINAL: "Terminal",
};

// engine-prefix -> display short letter + CSS class for the suit marker
const SUIT_DISPLAY = {
  W: { letter: "C", cls: "suit-character" }, // characters → red
  B: { letter: "D", cls: "suit-dots" }, //       dots   → fg
  T: { letter: "B", cls: "suit-bamboo" }, //     bamboo → green
};

// Unicode codepoint base by engine prefix.
const UNICODE_BASE = {
  W: 0x1f007, // 🀇..🀏 characters
  B: 0x1f019, // 🀙..🀡 dots / circles
  T: 0x1f010, // 🀐..🀘 bamboo
  F: 0x1f000, // 🀀..🀃 winds
  J: 0x1f004, // 🀄..🀆 dragons
  H: 0x1f022, // 🀢..🀩 flowers / seasons
};

const DRAGON_CSS = ["dragon-red", "dragon-green", "dragon-white"];

const WIND_LETTER = { F1: "E", F2: "S", F3: "W", F4: "N" };

const FACE_DOWN_ASCII = "▒▒";
const FACE_DOWN_UNICODE = "🀫";

function windNameFromSeat(seatIndex) {
  return SEAT_WIND_NAME[seatIndex] ?? `Seat ${seatIndex + 1}`;
}

// Combined label: "East (Seat 1)" — wind comes from the seat's current
// seat_wind (rotates between hands), seat number is the fixed 1-indexed
// table position. We look up the wind from the view so the seat number
// stays correct across hands where the dealer has rotated.
function fullSeatName(view, seatIndex) {
  const seat = view?.seats?.find((s) => s.seat === seatIndex);
  const wind = seat ? (WIND_NAME[seat.seat_wind] ?? "?") : "?";
  return `${wind} (Seat ${seatIndex + 1})`;
}

function parseTile(token) {
  if (typeof token !== "string" || token.length !== 2) return null;
  const prefix = token[0];
  const rank = parseInt(token[1], 10);
  if (!Number.isInteger(rank) || rank < 1 || rank > 9) return null;
  if (!(prefix in UNICODE_BASE)) return null;
  return { prefix, rank };
}

// Single tile as a Lit fragment. `options.tileStyle` is "ascii" | "unicode".
// Dragons always render Unicode regardless.
export function tile(token, options = {}) {
  const parsed = parseTile(token);
  if (!parsed) return html`<span class="tile tile-unknown">??</span>`;
  const { prefix, rank } = parsed;
  const tileStyle = options.tileStyle === "unicode" ? "unicode" : "ascii";

  // Dragons — always unicode, colored.
  if (prefix === "J") {
    const cls = DRAGON_CSS[rank - 1] ?? "";
    const glyph = String.fromCodePoint(0x1f004 + rank - 1);
    return html`<span class="tile dragon ${cls}">${glyph}</span>`;
  }

  if (tileStyle === "unicode") {
    const glyph = String.fromCodePoint(UNICODE_BASE[prefix] + rank - 1);
    const suitCls = SUIT_DISPLAY[prefix]?.cls ?? "";
    return html`<span class="tile ${suitCls}">${glyph}</span>`;
  }

  // ASCII shorthand path.
  if (prefix in SUIT_DISPLAY) {
    const { letter, cls } = SUIT_DISPLAY[prefix];
    return html`<span class="tile"
      ><span class="rank">${rank}</span
      ><span class="suit ${cls}">${letter}</span
    ></span>`;
  }
  if (prefix === "F") {
    const dir = WIND_LETTER[token] ?? "?";
    return html`<span class="tile wind">${dir}W</span>`;
  }
  if (prefix === "H") {
    return html`<span class="tile flower">${rank}F</span>`;
  }
  return html`<span class="tile">${token}</span>`;
}

function faceDown(options) {
  const glyph =
    options.tileStyle === "unicode" ? FACE_DOWN_UNICODE : FACE_DOWN_ASCII;
  return html`<span class="tile face-down">${glyph}</span>`;
}

function joinTiles(tiles, sep, options) {
  const out = [];
  tiles.forEach((t, i) => {
    if (i > 0) out.push(sep);
    out.push(tile(t, options));
  });
  return out;
}

function joinFaceDown(count, sep, options) {
  const out = [];
  for (let i = 0; i < count; i++) {
    if (i > 0) out.push(sep);
    out.push(faceDown(options));
  }
  return out;
}

function emptyMarker() {
  return html`<span class="empty">(none)</span>`;
}

function renderMelds(melds, options) {
  if (!melds || melds.length === 0) return emptyMarker();
  const out = [];
  melds.forEach((m, i) => {
    if (i > 0) out.push("  ");
    out.push("[");
    out.push(m.type);
    out.push(" ");
    out.push(...joinTiles(m.tiles ?? [], " ", options));
    if (m.called_from_seat !== undefined && m.called_from_seat !== null) {
      out.push(` from ${windNameFromSeat(m.called_from_seat)}`);
    }
    out.push("]");
  });
  return out;
}

function renderDiscards(discards, options) {
  if (!discards || discards.length === 0) return emptyMarker();
  const rows = [];
  for (let i = 0; i < discards.length; i += 12) {
    rows.push(discards.slice(i, i + 12));
  }
  const out = [];
  rows.forEach((row, idx) => {
    if (idx > 0) out.push("\n              ");
    out.push(...joinTiles(row, " ", options));
  });
  return out;
}

function seatHeader(seat, positionLabel) {
  const wind = WIND_NAME[seat.seat_wind] ?? "?";
  const seatNum = seat.seat + 1;
  return html`<span class="seat-label">${wind} (Seat ${seatNum})</span> <span class="seat-position">(${positionLabel})</span> Score: ${seat.score}`;
}

// Flowers are public-knowledge tiles (state-schema.md: flowers are not
// concealed — same projection rule for own seat and opponents). We show
// the actual tiles, not a count, so the on-screen string doesn't collide
// with the rank+F shorthand for individual flower tokens.
function renderFlowers(flowers, options) {
  if (!flowers || flowers.length === 0) return emptyMarker();
  return joinTiles(flowers, " ", options);
}

function renderOpponent(seat, positionLabel, options) {
  const count = seat.concealed?.count ?? 0;
  return html`${seatHeader(seat, positionLabel)}
   Hand:      ${joinFaceDown(count, " ", options)}
   Melds:     ${renderMelds(seat.melds, options)}
   Flowers:   ${renderFlowers(seat.flowers, options)}
   Discards:  ${renderDiscards(seat.discards, options)}`;
}

function renderOwn(seat, options) {
  return html`${seatHeader(seat, "— YOU")}
   Concealed: ${joinTiles(seat.concealed ?? [], " ", options)}
   Melds:     ${renderMelds(seat.melds, options)}
   Flowers:   ${renderFlowers(seat.flowers, options)}
   Discards:  ${renderDiscards(seat.discards, options)}`;
}

function renderHeader(view) {
  const round = WIND_NAME[view.round_wind] ?? "?";
  const hand = (view.hand_index ?? 0) + 1;
  const turn = view.turn_index ?? 0;
  const wall = view.wall?.remaining_count ?? "?";
  const phase = PHASE_LABEL[view.phase] ?? view.phase ?? "?";
  const actor = view.current_actor ?? 0;
  const dealer = view.dealer_seat ?? 0;
  return html`<span class="hdr-label">Round:</span> ${round}   <span class="hdr-label">Hand:</span> ${hand}   <span class="hdr-label">Turn:</span> ${turn}   <span class="hdr-label">Wall:</span> ${wall} left
<span class="hdr-label">Phase:</span> ${phase}   <span class="hdr-label">Dealer:</span> ${fullSeatName(view, dealer)}   <span class="hdr-label">Active:</span> ${fullSeatName(view, actor)}`;
}

function renderLastDiscard(view, options) {
  const ld = view.last_discard;
  if (!ld)
    return html`<span class="hdr-label">Last discard:</span> (none)`;
  return html`<span class="hdr-label">Last discard:</span> ${fullSeatName(view, ld.seat)} discarded ${tile(ld.tile, options)} (turn ${ld.turn_index})`;
}

// own seat at bottom; right opponent at top; across in the middle; left
// opponent just above you. Mahjong is counterclockwise so right is the
// *next* player to play after you, i.e. (own + 1) % 4.
function seatPositions(view, ownSeat) {
  const byId = (id) => view.seats.find((s) => s.seat === id);
  return {
    top: byId((ownSeat + 1) % 4),
    middle: byId((ownSeat + 2) % 4),
    just_above: byId((ownSeat + 3) % 4),
    bottom: byId(ownSeat),
  };
}

function seatBlock(seat, positionLabel, ownSeat, options) {
  if (!seat) return "";
  // Own seat has a list concealed; opponents have {count: N}.
  return seat.seat === ownSeat && Array.isArray(seat.concealed)
    ? renderOwn(seat, options)
    : renderOpponent(seat, positionLabel, options);
}

export function renderTable(seatView, ownSeat, options = {}) {
  if (!seatView)
    return html`<span class="empty"
      >(no snapshot — waiting for ATTACHED)</span
    >`;

  const positions = seatPositions(seatView, ownSeat);

  // Render in three sections joined by CSS hairlines (not ASCII rules), so
  // the dividers stretch to whatever width the game pane actually has —
  // important when side panes are open and the game pane narrows.
  //
  // Section order (top to bottom): the three opponents + last discard,
  // then your own hand, then the Round/Phase metadata strip. Metadata at
  // the bottom keeps the player's attention on the table; the strip stays
  // visible just above the wire-log toggle.
  return html`
    <pre class="section">
${seatBlock(positions.top, "your right", ownSeat, options)}

${seatBlock(positions.middle, "across", ownSeat, options)}

${seatBlock(positions.just_above, "your left", ownSeat, options)}

${renderLastDiscard(seatView, options)}</pre
    >
    <hr class="ascii-rule" />
    <pre class="section">
${seatBlock(positions.bottom, "you", ownSeat, options)}</pre
    >
    <hr class="ascii-rule" />
    <pre class="section">${renderHeader(seatView)}</pre>
  `;
}

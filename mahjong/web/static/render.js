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
// Seat positioning (locked 2026-05-23 in conversation with user): own seat
// at the bottom; the player on your *right* (next in counterclockwise play
// order) renders at the top, then across, then your left, then you.
// Mahjong play order is counterclockwise (E → S → W → N), so right =
// (own + 1) % 4.
//
// Pinwheel widget (Step 8.9 — cardinal-ui.md):
//   A separate compact "who's-playing + last-discard" indicator rendered
//   next to the stacked seat blocks.  The pinwheel is the cardinal map;
//   the seat blocks stay stacked so wide opponent rows don't fight a grid.

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
    if (m.hidden) {
      // An opponent's concealed kong — tile identity is private until
      // settlement (Spec 29 Bug D), so show four face-down tiles.
      out.push(...joinFaceDown(4, " ", options));
    } else {
      out.push(...joinTiles(m.tiles ?? [], " ", options));
    }
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
  // Wrap in .discard-row so CSS can render the pile smaller than the hand
  // (high-frequency, low-importance background info — Spec 22 § 22.4).
  return html`<span class="discard-row">${out}</span>`;
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

// Suit identifier for suit-break detection in the local hand.  Engine
// prefix W (characters), B (bing/dots), T (tiao/bamboo), F (winds), J
// (dragons).  Flowers are public-knowledge and never appear in concealed.
function _suitOf(token) {
  return typeof token === "string" && token.length > 0 ? token[0] : "";
}

// Layer-8 close-out §1 hand-display polish.  Render the local player's
// concealed hand with per-tile decoration:
//
//   .selected     on the tile at `options.selectedTile` (index into the
//                 engine-sorted concealed list — the cursor the player
//                 moves with digit keys / arrow keys).
//   .just-drawn   on the tile matching `view.last_drawn.tile` when the
//                 player has drawn but not yet discarded.  Pulled out of
//                 sort order and appended at the end with a wider gap,
//                 mirroring the physical-table convention of keeping the
//                 just-drawn tile separate from the rest of your hand.
//   .suit-break   on the first tile of each new suit group, so the
//                 m/p/s/F/J boundaries read at a glance instead of having
//                 to scan the suit letters.
//
// Each tile is wrapped in a <span class="tile-mod ..."> so modifier
// classes don't fight the existing .tile class set by tile().
export function renderOwnConcealedTiles(seat, view, ownSeat, options = {}) {
  const concealed = seat?.concealed ?? [];
  if (concealed.length === 0) return emptyMarker();

  const selectedIdx = options.selectedTile;
  const ld = view?.last_drawn;
  const hasJustDrawn = ld && ld.seat === ownSeat && ld.tile != null;

  // Find first concealed index whose token equals the just-drawn tile —
  // findIndex correctly removes only one when the hand contains duplicates.
  let drawnIdx = -1;
  if (hasJustDrawn) {
    drawnIdx = concealed.findIndex((t) => t === ld.tile);
  }

  // Build the rendered order: everything except just-drawn first, then
  // just-drawn at the end when present.
  const order = [];
  concealed.forEach((tok, i) => {
    if (i === drawnIdx) return;
    order.push({ token: tok, origIdx: i, isJustDrawn: false });
  });
  if (drawnIdx >= 0) {
    order.push({
      token: concealed[drawnIdx],
      origIdx: drawnIdx,
      isJustDrawn: true,
    });
  }

  // Emit per-tile spans, tracking suit transitions on the way.  No
  // suit-break on the just-drawn tile — it carries .just-drawn instead,
  // and its larger offset already signals "this one is separate."
  const out = [];
  let prevSuit = null;
  order.forEach((item, renderIdx) => {
    const curSuit = _suitOf(item.token);
    const isSuitBreak =
      renderIdx > 0 && !item.isJustDrawn && curSuit !== prevSuit;

    const classes = ["tile-mod"];
    if (item.origIdx === selectedIdx) classes.push("selected");
    if (item.isJustDrawn) classes.push("just-drawn");
    if (isSuitBreak) classes.push("suit-break");

    if (renderIdx > 0) out.push(" ");
    out.push(
      html`<span class=${classes.join(" ")}>${tile(item.token, options)}</span>`,
    );

    prevSuit = curSuit;
  });

  return out;
}

function renderOwn(seat, view, ownSeat, options) {
  return html`${seatHeader(seat, "— YOU")}
   Concealed: ${renderOwnConcealedTiles(seat, view, ownSeat, options)}
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

function seatBlock(seat, positionLabel, view, ownSeat, options) {
  if (!seat) return "";
  // Own seat has a list concealed; opponents have {count: N}.
  // `view` is threaded only for the own-seat path — renderOwn needs
  // `last_drawn` to offset the just-drawn tile (§1 hand-display polish).
  return seat.seat === ownSeat && Array.isArray(seat.concealed)
    ? renderOwn(seat, view, ownSeat, options)
    : renderOpponent(seat, positionLabel, options);
}

// --- Pinwheel widget (Step 8.9, cardinal-ui.md) ----------------------------
//
// A small compact "who's playing + last discard" indicator that sits next
// to the stacked seat blocks.  Its job is to answer two questions at a
// glance: whose turn is it (arrow), and what was the last tile played
// (center glyph).  Cardinal layout mirrors a physical mahjong table —
// you at south, next-to-act on your right (east), across (north), previous
// actor on your left (west).

// Wind-to-pinwheel-number lookup.  MCR convention: East=1, South=2,
// West=3, North=4 — numbers increase counter-clockwise (mahjong play
// order).  The badge displays this wind number per seat regardless of
// where the seat sits in the pinwheel relative to ownSeat; the spatial
// position is determined by `(seatIndex - ownSeat) % 4` (YOU at south,
// neighbours radiating CCW from there).
const WIND_TO_NUMBER = { F1: 1, F2: 2, F3: 3, F4: 4 };

// (relative seat from ownSeat) -> arrow glyph pointing at that cardinal.
const PINWHEEL_ARROW = { 0: "↓", 1: "→", 2: "↑", 3: "←" };

function _seatBadge(seat) {
  if (!seat) return "?";
  const n = WIND_TO_NUMBER[seat.seat_wind];
  return n != null ? String(n) : "?";
}

// The arrow points at whoever just discarded the tile shown in the
// center.  Falls back to a neutral marker before the first discard /
// after the hand ends.  It deliberately does NOT special-case
// CLAIM_WINDOW: a `?` there broadcast that *someone is deciding whether
// to claim*, which is a tell you'd only have by watching body language
// at a physical table (Spec 22 § 22.1).  `last_discard` is already
// public (the tile is on the table), so pointing at the discarder in
// every phase reveals nothing new.
function _pinwheelArrow(view, ownSeat) {
  if (view.phase === "TERMINAL") return "·";
  const ld = view.last_discard;
  if (!ld || ld.seat == null) return "·";
  const relative = (((ld.seat - ownSeat) % 4) + 4) % 4;
  return PINWHEEL_ARROW[relative] ?? "·";
}

// Renders a 3×3 compact grid: north badge top, west/center/east in the
// middle row, south badge at the bottom.  Center cell carries the arrow
// + (when present) a large unicode last-discard tile.  The four corner
// cells are empty padding so the badges line up on the cardinal axes.
//
// Badges show the per-hand *wind number* of each seat (1=East, 2=South,
// 3=West, 4=North) so the player can read who's the dealer at a glance
// (dealer is always East = "1").  The own seat additionally carries a
// `.own` class so CSS can mark "this is you".
export function renderPinwheel(view, ownSeat, options = {}) {
  if (!view) return "";
  const ld = view.last_discard;
  const arrow = _pinwheelArrow(view, ownSeat);
  const activeSeat = ld?.seat ?? -1;

  const seatAt = (offset) => {
    const seatIndex = ((ownSeat + offset) % 4 + 4) % 4;
    return view.seats.find((s) => s.seat === seatIndex) ?? null;
  };

  function badge(offset) {
    const seat = seatAt(offset);
    if (!seat) return "";
    const seatIndex = seat.seat;
    const label = _seatBadge(seat);
    const classes = ["pw-badge"];
    if (seatIndex === activeSeat) classes.push("active");
    if (seatIndex === ownSeat) classes.push("own");
    return html`<div class=${classes.join(" ")}>${label}</div>`;
  }

  // Force unicode tile in the pinwheel: the tile is the main visual
  // anchor and unicode mahjong glyphs read at a glance.  The surrounding
  // CSS knocks the tile up to a large size.
  const tileOptions = { ...options, tileStyle: "unicode" };

  const center = html`
    <div class="pw-center">
      <div class="pw-arrow">${arrow}</div>
      ${ld
        ? html`<div class="pw-last-discard">${tile(ld.tile, tileOptions)}</div>`
        : html`<div class="pw-last-discard pw-empty">·</div>`}
    </div>
  `;

  return html`<div class="pinwheel" title="Arrow points at the last discarder · 1=E 2=S 3=W 4=N">
    <div class="pw-cell pw-corner"></div>
    <div class="pw-cell pw-north">${badge(2)}</div>
    <div class="pw-cell pw-corner"></div>
    <div class="pw-cell pw-west">${badge(3)}</div>
    <div class="pw-cell pw-mid">${center}</div>
    <div class="pw-cell pw-east">${badge(1)}</div>
    <div class="pw-cell pw-corner"></div>
    <div class="pw-cell pw-south">${badge(0)}</div>
    <div class="pw-cell pw-corner"></div>
  </div>`;
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
  // visible just above the wire-log toggle.  The pinwheel is mounted next
  // to this stack by <game-pane> — it isn't part of the stacked content.
  return html`
    <pre class="section">
${seatBlock(positions.top, "your right", seatView, ownSeat, options)}

${seatBlock(positions.middle, "across", seatView, ownSeat, options)}

${seatBlock(positions.just_above, "your left", seatView, ownSeat, options)}

${renderLastDiscard(seatView, options)}</pre
    >
    <hr class="ascii-rule" />
    <pre class="section">
${seatBlock(positions.bottom, "you", seatView, ownSeat, options)}</pre
    >
    <hr class="ascii-rule" />
    <pre class="section">${renderHeader(seatView)}</pre>
  `;
}

// --- Hand-end summary (§22.9) ---------------------------------------------
//
// Modular by design: the summary is a list of independent *section*
// renderers, each `(terminal, view, ownSeat) -> html|""`. To add a new
// detail later (e.g. a stats widget, a win-probability bar, a "next hand"
// button), write a section function and append it to HAND_END_SECTIONS —
// no other code changes. A section returning "" is skipped, so sections can
// opt out (e.g. fan breakdown on an exhausted draw).

// Section: headline — who won and how, or "exhausted draw".
function _summaryHeadline(terminal, view, _ownSeat) {
  if (terminal.kind !== "HU" || terminal.winner == null) {
    return html`<div class="he-headline">Exhausted draw — no winner</div>`;
  }
  const who = fullSeatName(view, terminal.winner);
  let how = "";
  if (terminal.win_type === "SELF_DRAW") {
    how = "self-draw";
  } else if (terminal.win_type === "DISCARD" && terminal.deal_in_seat != null) {
    how = `on ${fullSeatName(view, terminal.deal_in_seat)}'s discard`;
  }
  return html`<div class="he-headline">
    <span class="he-winner">${who} wins</span>${how ? html` — ${how}` : ""}
    ${terminal.win_tile
      ? html` &nbsp;${tile(terminal.win_tile, { tileStyle: "unicode" })}`
      : ""}
  </div>`;
}

// Section: fan breakdown — each scored pattern + its value, then the total.
function _summaryFan(terminal, _view, _ownSeat) {
  if (terminal.kind !== "HU") return "";
  const fans = Array.isArray(terminal.fan) ? terminal.fan : [];
  return html`<div class="he-fan">
    <div class="he-section-title">Fan</div>
    ${fans.length === 0
      ? html`<div class="he-fan-row">(none recorded)</div>`
      : fans.map(
          (f) => html`<div class="he-fan-row">
            <span class="he-fan-name">${f.name}</span>
            <span class="he-fan-value">${f.value}</span>
          </div>`,
        )}
    <div class="he-fan-row he-fan-total">
      <span class="he-fan-name">Total</span>
      <span class="he-fan-value">${terminal.fan_total ?? 0}</span>
    </div>
  </div>`;
}

// Section: point swing — per-seat score delta, winner highlighted.
function _summaryScores(terminal, view, _ownSeat) {
  const deltas = Array.isArray(terminal.score_delta) ? terminal.score_delta : [];
  if (deltas.length !== 4) return "";
  return html`<div class="he-scores">
    <div class="he-section-title">Points</div>
    ${deltas.map((d, seat) => {
      const sign = d > 0 ? "+" : "";
      const cls = seat === terminal.winner ? "he-score-row he-winner" : "he-score-row";
      return html`<div class=${cls}>
        <span class="he-score-name">${fullSeatName(view, seat)}</span>
        <span class="he-score-delta">${sign}${d}</span>
      </div>`;
    })}
  </div>`;
}

// Section: everyone's revealed hands (concealed + melds). final_hands is the
// authoritative reveal from the HAND_END event; fall back to the projected
// view only for the own seat if it's absent.
function _summaryHands(terminal, view, _ownSeat, options) {
  const hands = Array.isArray(terminal.final_hands) ? terminal.final_hands : null;
  if (!hands) return "";
  return html`<div class="he-hands">
    <div class="he-section-title">Hands</div>
    ${hands
      .slice()
      .sort((a, b) => a.seat - b.seat)
      .map(
        (h) => html`<div class="he-hand-row">
          <span class="he-hand-name">${fullSeatName(view, h.seat)}:</span>
          <span class="he-hand-tiles"
            >${joinTiles(h.concealed ?? [], " ", options)}</span
          >
          ${(h.melds ?? []).length > 0
            ? html`<span class="he-hand-melds">${renderMelds(h.melds, options)}</span>`
            : ""}
        </div>`,
      )}
  </div>`;
}

// The ordered section list. Append here to add a new summary detail.
const HAND_END_SECTIONS = [_summaryHeadline, _summaryFan, _summaryScores, _summaryHands];

export function renderHandEndSummary(view, ownSeat, options = {}) {
  const terminal = view?.terminal;
  if (!terminal) return "";
  return html`<div class="hand-end-summary">
    ${HAND_END_SECTIONS.map((section) => section(terminal, view, ownSeat, options))}
  </div>`;
}

// --- Point-performance line graph (profile-and-settings.md § B.6) --------
//
// Pure function: a cumulative-score series → a multi-line ASCII chart string.
// No charting lib (the client is build-free ASCII).  Auto-scales y to the
// data with the zero baseline always shown; degenerate inputs (empty, single
// point, all-equal) render an empty-state or flat line, never NaN.
export function renderScoreGraph(series, options = {}) {
  const width = Math.max(8, options.width ?? 48);
  const height = Math.max(3, options.height ?? 9);
  if (!Array.isArray(series) || series.length === 0) {
    return "(no games yet)";
  }

  const values = series.map((p) => p.cumulative);
  // Include 0 so the baseline is always in range; pad a flat series so the
  // axis has a non-zero span (avoids divide-by-zero).
  let lo = Math.min(0, ...values);
  let hi = Math.max(0, ...values);
  if (lo === hi) {
    lo -= 1;
    hi += 1;
  }
  const span = hi - lo;
  const rowFor = (v) => Math.round(((hi - v) / span) * (height - 1));

  // Resample the series across `width` columns (nearest data index).
  const n = series.length;
  const cols = [];
  for (let x = 0; x < width; x++) {
    const idx = n === 1 ? 0 : Math.round((x / (width - 1)) * (n - 1));
    cols.push(rowFor(values[idx]));
  }

  const grid = Array.from({ length: height }, () => Array(width).fill(" "));
  const zeroRow = rowFor(0);
  for (let x = 0; x < width; x++) grid[zeroRow][x] = "·"; // baseline
  for (let x = 0; x < width; x++) {
    const r = cols[x];
    // Connect the vertical gap to the previous column for a line feel.
    if (x > 0) {
      const prev = cols[x - 1];
      const [a, b] = prev < r ? [prev, r] : [r, prev];
      for (let y = a + 1; y < b; y++) {
        if (grid[y][x] === " " || grid[y][x] === "·") grid[y][x] = "│";
      }
    }
    grid[r][x] = "●";
  }

  const labelWidth = String(Math.max(Math.abs(hi), Math.abs(lo))).length + 1;
  const fmt = (v) => (v > 0 ? `+${v}` : String(v)).padStart(labelWidth);
  return grid
    .map((row, r) => {
      let label = " ".repeat(labelWidth);
      if (r === 0) label = fmt(hi);
      else if (r === height - 1) label = fmt(lo);
      else if (r === zeroRow) label = fmt(0);
      return `${label} │${row.join("")}`;
    })
    .join("\n");
}

// Test hooks (jsdom).  The pinwheel's pure helpers are easy to pin without
// rendering, which keeps the cardinal-ui fixtures fast.
export const __test__ = {
  PINWHEEL_ARROW,
  WIND_TO_NUMBER,
  pinwheelArrow: _pinwheelArrow,
  seatBadge: _seatBadge,
  renderScoreGraph,
};

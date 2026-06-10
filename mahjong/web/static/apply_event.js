// applyEvent — step 7.5c.ii.
//
// Pure reducer: (seatView, event, ownSeat) -> newSeatView.
//
// Mirrors the engine's apply_action / diff_to_events loop but on the
// projected-view side. The two are structurally analogous; they are NOT
// the same code because (a) we operate on a SeatView, not GameState, and
// (b) opponents' concealed tiles are a count, not a list.
//
// Event vocabulary (mahjong/records/diff.py):
//   - DRAW              { seat, tile?, turn_index, phase }
//   - DISCARD           { seat, tile, from_hand, turn_index, phase }
//   - CLAIM_WINDOW      { opportunities, turn_index, phase }
//   - CLAIM_DECISION    { seat, decision, tile?, kind?, chi_tiles?, ... }
//   - CLAIM_RESOLUTION  { outcome, winning_seat?, winning_claim?, ... }
//   - HAND_END          { kind, winner[], win_tile, ..., score_delta, ... }
//
// Scope decisions:
// - Returns a *new* SeatView every call. No in-place mutation; the renderer
//   relies on identity comparison for re-render triggers.
// - The opponent-meld branch can't faithfully reconstruct WHICH tiles left
//   an opponent's concealed (we only know the count). We decrement the
//   count by the meld arity (2 / 3 / 4) and trust the meld payload on the
//   event for what to show in the meld bar.
// - We re-sort own concealed after a DRAW so the local hand mirrors the
//   engine's canonical-sort invariant (the engine sorts concealed after
//   every draw — mahjong/engine/transition/__init__.py). Earlier this was
//   deliberately skipped, which left previously-drawn-then-kept tiles
//   stranded at the tail of the hand and the suit-break logic broke (Spec
//   22 § 22.7). The renderer still pulls the *just-drawn* tile out to the
//   end via view.last_drawn (it matches by value, not array position), so
//   the "newest tile sits apart" physical-table cue survives the sort.

// ---- helpers ----------------------------------------------------------

// Canonical tile order, mirroring engine/tiles.py `tile_sort_key`
// (sections W < B < T < F < J < H, then by rank). Kept in lockstep with
// the engine so the client hand reads in the same order the server uses.
const _SUIT_ORDER = { W: 0, B: 1, T: 2, F: 3, J: 4, H: 5 };

function _tileSortKey(token) {
  const suit = _SUIT_ORDER[token?.[0]] ?? 99;
  const rank = parseInt(token?.[1], 10) || 0;
  return suit * 10 + rank;
}

function sortOwnConcealed(seatBlock) {
  if (isOwnConcealed(seatBlock)) {
    seatBlock.concealed.sort((a, b) => _tileSortKey(a) - _tileSortKey(b));
  }
}

function cloneSeatView(view) {
  // Shallow-clone the top + each seat dict (we always copy seats we touch).
  // SeatView is plain JSON-friendly data so a structuredClone-like deep
  // copy is overkill; we copy at the granularity we mutate.
  return {
    ...view,
    wall: { ...view.wall },
    seats: view.seats.map((s) => ({
      ...s,
      // concealed is a list (own) or {count} (opponent); copy both shapes.
      concealed: Array.isArray(s.concealed) ? [...s.concealed] : { ...s.concealed },
      // An opponent's hidden concealed kong (Spec 29 Bug D) carries no `tiles`
      // — its identity is masked until settlement — so guard the spread.
      melds: s.melds.map((m) => ({ ...m, tiles: Array.isArray(m.tiles) ? [...m.tiles] : m.tiles })),
      discards: [...s.discards],
      flowers: [...s.flowers],
    })),
    last_discard: view.last_discard ? { ...view.last_discard } : null,
    pending_claims: view.pending_claims ? [...view.pending_claims] : [],
    terminal: view.terminal ? { ...view.terminal } : null,
  };
}

function isOwnConcealed(seatBlock) {
  return Array.isArray(seatBlock.concealed);
}

function removeOneFromConcealed(seatBlock, tile) {
  if (isOwnConcealed(seatBlock)) {
    const idx = seatBlock.concealed.indexOf(tile);
    if (idx >= 0) seatBlock.concealed.splice(idx, 1);
  } else {
    seatBlock.concealed.count = Math.max(0, (seatBlock.concealed.count ?? 0) - 1);
  }
}

function removeNFromConcealed(seatBlock, tile, n) {
  if (isOwnConcealed(seatBlock)) {
    for (let i = 0; i < n; i++) {
      const idx = seatBlock.concealed.indexOf(tile);
      if (idx >= 0) seatBlock.concealed.splice(idx, 1);
    }
  } else {
    seatBlock.concealed.count = Math.max(0, (seatBlock.concealed.count ?? 0) - n);
  }
}

function addToConcealed(seatBlock, tile) {
  if (isOwnConcealed(seatBlock)) {
    seatBlock.concealed.push(tile);
  } else {
    seatBlock.concealed.count = (seatBlock.concealed.count ?? 0) + 1;
  }
}

// Remove a list of tiles minus the called one (CHI). The called tile came
// from the discard pile, so the claimer only had the other two in hand.
function removeChiSupportTiles(seatBlock, chiTiles, calledTile) {
  if (!isOwnConcealed(seatBlock)) {
    seatBlock.concealed.count = Math.max(0, (seatBlock.concealed.count ?? 0) - 2);
    return;
  }
  // Remove each tile from chiTiles except one instance of calledTile.
  let calledRemoved = false;
  for (const t of chiTiles) {
    if (!calledRemoved && t === calledTile) {
      calledRemoved = true;
      continue;
    }
    const idx = seatBlock.concealed.indexOf(t);
    if (idx >= 0) seatBlock.concealed.splice(idx, 1);
  }
}

// ---- event handlers ---------------------------------------------------

function applyDraw(view, event, _ownSeat) {
  const seat = view.seats.find((s) => s.seat === event.seat);
  if (!seat) return view;

  if (event.tile) {
    // The drawing seat (own perspective) sees the tile.
    addToConcealed(seat, event.tile);
    sortOwnConcealed(seat); // keep the local hand in engine-canonical order
    view.last_drawn = { seat: event.seat, tile: event.tile };
  } else {
    // Opponent draw — only the count moves.
    addToConcealed(seat, null);
    view.last_drawn = { seat: event.seat, tile: null };
  }

  view.wall.drawn_count = (view.wall.drawn_count ?? 0) + 1;
  view.wall.remaining_count = Math.max(0, (view.wall.remaining_count ?? 0) - 1);
  view.turn_index = event.turn_index ?? view.turn_index;
  view.phase = event.phase ?? view.phase;
  view.current_actor = event.seat;

  // Auto-replaced flowers — append each to the seat's flowers, the wall
  // ticks for each replacement. The replacement tile (the final non-flower)
  // is already in `event.tile`.
  if (Array.isArray(event.flower_replacements)) {
    for (const flower of event.flower_replacements) {
      seat.flowers.push(flower);
      view.wall.drawn_count += 1;
      view.wall.remaining_count = Math.max(0, view.wall.remaining_count - 1);
    }
  }

  return view;
}

function applyDiscard(view, event, _ownSeat) {
  const seat = view.seats.find((s) => s.seat === event.seat);
  if (!seat || !event.tile) return view;

  // Tsumogiri (from_hand=false) still removes from concealed — the just-
  // drawn tile is in the hand for one beat. Same removal path either way;
  // the from_hand flag only matters for the renderer's tsumogiri tag.
  removeOneFromConcealed(seat, event.tile);
  seat.discards.push(event.tile);

  view.last_discard = {
    seat: event.seat,
    tile: event.tile,
    turn_index: event.turn_index ?? view.turn_index,
  };
  view.last_drawn = null;
  view.turn_index = event.turn_index ?? view.turn_index;
  view.phase = event.phase ?? view.phase;
  return view;
}

function applyClaimWindow(view, event, _ownSeat) {
  view.turn_index = event.turn_index ?? view.turn_index;
  view.phase = event.phase ?? "CLAIM_WINDOW";
  return view;
}

function applyClaimDecision(view, event, _ownSeat) {
  view.turn_index = event.turn_index ?? view.turn_index;
  view.phase = event.phase ?? view.phase;

  // Spec 29 Bug C: a CLAIM_DECISION is now *informational* for window claims
  // (PENG / CHI / GANG-EXPOSED). The hand mutation moves to applyClaimResolution,
  // which names the authoritative winner. Previously every decision mutated the
  // view assuming it won; when a CHI lost the priority race to an overriding
  // GANG, the phantom meld was never rolled back (the 5-meld / can't-mahjong
  // bug). Deferring to the resolution means a *losing* decision touches nothing,
  // so there is nothing to undo. `last_discard` is deliberately left intact —
  // the resolution still needs it to locate the called tile and its discarder.
  //
  // The one exception is a self-initiated kong (concealed / added) declared from
  // DISCARD phase: it can't contend and carries no resolution, so it applies here.
  if (event.decision === "GANG" && (event.kind === "CONCEALED" || event.kind === "ADDED")) {
    return applySelfGang(view, event);
  }
  return view;
}

// Self-initiated kong from DISCARD phase (no claim window, no resolution event).
function applySelfGang(view, event) {
  const claimer = view.seats.find((s) => s.seat === event.seat);
  if (!claimer) return view;
  const tile = event.tile;

  if (event.kind === "CONCEALED") {
    // `removeNFromConcealed` is count-based for opponents, so it works even
    // though the tile is redacted from non-owners (Spec 29 Bug D).
    removeNFromConcealed(claimer, tile, 4);
    if (isOwnConcealed(claimer) && tile) {
      // Own kong: we know the tile, show it face-up to ourselves.
      claimer.melds.push({
        type: "GANG_CONCEALED",
        tiles: [tile, tile, tile, tile],
        called_from_seat: event.seat,
      });
    } else {
      // Opponent kong: the tile identity is hidden until settlement; render
      // four face-down tiles.
      claimer.melds.push({
        type: "GANG_CONCEALED",
        hidden: true,
        called_from_seat: event.seat,
      });
    }
  } else if (event.kind === "ADDED") {
    if (!tile) return view;
    // Upgrade an existing PENG meld of the same tile.
    removeNFromConcealed(claimer, tile, 1);
    const meldIdx = claimer.melds.findIndex(
      (m) => m.type === "PENG" && m.tiles[0] === tile,
    );
    if (meldIdx >= 0) {
      const old = claimer.melds[meldIdx];
      claimer.melds[meldIdx] = {
        type: "GANG_ADDED",
        tiles: [tile, tile, tile, tile],
        called_tile: tile,
        called_from_seat: old.called_from_seat,
      };
    }
  }
  view.current_actor = event.seat;
  return view;
}

function pullCalledTileOffDiscarder(view, tile, fromSeat) {
  if (fromSeat === undefined || fromSeat === null || !tile) return;
  const discarder = view.seats.find((s) => s.seat === fromSeat);
  if (!discarder) return;
  // Remove only the LAST occurrence so we don't accidentally erase an
  // earlier same-tile discard from the pile.
  for (let i = discarder.discards.length - 1; i >= 0; i--) {
    if (discarder.discards[i] === tile) {
      discarder.discards.splice(i, 1);
      return;
    }
  }
}

function applyClaimResolution(view, event, _ownSeat) {
  view.turn_index = event.turn_index ?? view.turn_index;
  view.phase = event.phase ?? view.phase;

  // Spec 29 Bug C: the resolution is the *authoritative* winner of a claim
  // window, so this is where the winning meld is applied (CLAIM_DECISION no
  // longer mutates for window claims). PASSED means no claim landed — nothing
  // to do. A winning HU emits HAND_END, not a resolution, so it never lands here.
  if (event.outcome !== "CLAIMED") return view;

  const claimer = view.seats.find((s) => s.seat === event.winning_seat);
  if (!claimer) return view;

  const claim = event.winning_claim;
  // The server stamps `called_tile` on PENG/GANG resolutions; fall back to the
  // still-intact last_discard for CHI (and older servers).
  const calledTile = event.called_tile ?? view.last_discard?.tile;
  const fromSeat = view.last_discard?.seat;

  if (claim === "PENG") {
    if (!calledTile) return view;
    removeNFromConcealed(claimer, calledTile, 2);
    pullCalledTileOffDiscarder(view, calledTile, fromSeat);
    claimer.melds.push({
      type: "PENG",
      tiles: [calledTile, calledTile, calledTile],
      called_tile: calledTile,
      called_from_seat: fromSeat ?? -1,
    });
  } else if (claim === "CHI") {
    const chiTiles = event.winning_chi_tiles ?? [];
    if (calledTile) removeChiSupportTiles(claimer, chiTiles, calledTile);
    pullCalledTileOffDiscarder(view, calledTile, fromSeat);
    claimer.melds.push({
      type: "CHI",
      tiles: [...chiTiles],
      called_tile: calledTile,
      called_from_seat: fromSeat ?? -1,
    });
  } else if (claim === "GANG") {
    // Only the EXPOSED kong reaches a resolution (concealed/added are
    // self-initiated and applied on the decision). Claimer had 3 in concealed
    // and takes the discarded 4th.
    if (!calledTile) return view;
    removeNFromConcealed(claimer, calledTile, 3);
    pullCalledTileOffDiscarder(view, calledTile, fromSeat);
    claimer.melds.push({
      type: "GANG_EXPOSED",
      tiles: [calledTile, calledTile, calledTile, calledTile],
      called_tile: calledTile,
      called_from_seat: fromSeat ?? -1,
    });
  } else {
    return view;
  }

  view.last_discard = null;
  view.current_actor = event.winning_seat;
  return view;
}

function applyHandEnd(view, event, _ownSeat) {
  view.turn_index = event.turn_index ?? view.turn_index;
  view.phase = "TERMINAL";
  const winner =
    Array.isArray(event.winner) && event.winner.length > 0
      ? event.winner[0]
      : null;
  view.terminal = {
    kind: event.kind,
    winner,
    win_tile: event.win_tile ?? null,
    win_type: event.win_type ?? null,
    deal_in_seat: event.deal_in_seat ?? null,
    fan: event.fan ?? [],
    fan_total: event.fan_total ?? 0,
    score_delta: event.score_delta ?? [],
    // HAND_END reveals every seat's hand (MCR shows all hands at the end);
    // project_event passes final_hands through unredacted. Captured so the
    // §22.9 summary can show everyone's concealed + melds.
    final_hands: event.final_hands ?? null,
  };
  // Apply scores; engine carries them as a per-seat delta list.
  if (Array.isArray(event.score_delta) && event.score_delta.length === 4) {
    view.seats.forEach((s, i) => {
      s.score = (s.score ?? 0) + (event.score_delta[i] ?? 0);
    });
  }
  return view;
}

// ---- entry point ------------------------------------------------------

const HANDLERS = {
  DRAW: applyDraw,
  DISCARD: applyDiscard,
  CLAIM_WINDOW: applyClaimWindow,
  CLAIM_DECISION: applyClaimDecision,
  CLAIM_RESOLUTION: applyClaimResolution,
  HAND_END: applyHandEnd,
};

export function applyEvent(seatView, event, ownSeat) {
  if (!seatView || !event) return seatView;
  const kind = event.event ?? event.kind;
  const handler = HANDLERS[kind];
  if (!handler) {
    // Unknown event — pass through; the wire log still shows it.
    return seatView;
  }
  const next = cloneSeatView(seatView);
  return handler(next, event, ownSeat);
}

// In-client documentation content (Spec 36 — docs-pane.md).
//
// Content-only module: no imports, no Lit. Each doc body is hand-wrapped
// plain text (<= 78 columns) rendered in a monospace pre-wrap block, matching
// the client's ASCII aesthetic. tests/web/test_docs_content.py cross-checks
// this file against the code it documents (seat-bot registry, house ruleset,
// engine fan-name spellings) — if the code moves, the docs fail the suite
// until they're updated. Keep it that way: docs that drift are worse than no
// docs.
//
// Tile shorthand in examples matches the client renderer (render.js):
// rank-first, C = characters, D = dots, B = bamboo; winds EW SW WW NW;
// flowers 1F-8F.

export const DOC_SECTIONS = [
  {
    title: "Getting started",
    docs: [
      {
        slug: "getting-started",
        title: "Playing on this server",
        body: `PLAYING ON THIS SERVER
══════════════════════

Welcome! This is a home-hosted mahjong server playing a Chinese-style
(MCR-family) game under house rules: you need at least 3 fan to win a hand.
If you have never played mahjong, start with "Basic rules — your first hand"
in the Rules folder, then come back here.

Logging in
──────────
You need an account. Registration uses an invite code from the host; once
registered, sign in with your username and password. Your session survives
page reloads — if you get disconnected mid-game, just log back in and the
server offers to rejoin your seat.

The lobby
─────────
The lobby lists open tables. You can:

  - join an open seat at an existing table,
  - or create your own table.

When creating a table you choose each seat: human or bot. For bot seats you
can pick WHICH bot from a dropdown — currently:

  v0 — greedy offense    a pure attacker; never defends
  v1 — offense + defense counts tiles, avoids feeding your hand

(The Bots folder in this menu explains exactly how each one thinks.)
Advanced options let you adjust bot pacing and the turn timer, or disable
timeouts entirely for a relaxed game.

Playing a hand
──────────────
Your hand renders at the bottom; play proceeds counterclockwise. When it is
your turn or you can react to a discard, a prompt bar appears listing your
legal actions with their key bindings — click an action or press its key.
When an opponent's discard is claimable you get a short claim window; if you
do nothing it passes automatically.

Useful keys (also listed in Settings):

  Alt+,   open settings          Alt+T   toggle dark/light theme
  Alt+M   minimal/classic view   Alt+U   ASCII/Unicode tiles
  Esc     back / close menus

Settings & profile
──────────────────
Settings (the gear button) holds theme, tile style, view mode, and sound.
The profile button shows your stats and recent games — every finished hand
can be replayed move by move from there.

Found a bug? The feedback button (bottom corner) sends a note straight to
the host, attached to the exact game state you were looking at.`,
      },
    ],
  },
  {
    title: "Rules",
    docs: [
      {
        slug: "basic-rules",
        title: "Basic rules — your first hand",
        body: `BASIC RULES — YOUR FIRST HAND
═════════════════════════════

The tiles
─────────
Mahjong is played with 144 tiles:

  Suits (4 copies of each):
    Characters  1C..9C   (red letters here)
    Dots        1D..9D
    Bamboo      1B..9B   (green letters here)
  Honors (4 copies of each):
    Winds       EW SW WW NW          (East, South, West, North)
    Dragons     Red, Green, White    (shown as colored glyphs)
  Flowers (1 copy of each):
    1F..8F — bonus tiles, they never sit in your hand (see below)

The goal
────────
Build a 14-tile hand of FOUR SETS plus ONE PAIR:

  set  = a CHOW  (three consecutive tiles in one suit, e.g. 4B 5B 6B)
       or a PUNG (three identical tiles, e.g. 7C 7C 7C)
       or a KONG (four identical tiles — counts as one set)
  pair = two identical tiles (the "eyes")

A few special hands break this shape (Seven Pairs, Thirteen Orphans,
knitted hands) — see the fan chart. You'll meet them later.

The turn loop
─────────────
Everyone holds 13 tiles. On your turn you DRAW one tile from the wall and
then DISCARD one face-up. Play moves counterclockwise: East, South, West,
North. That's the whole engine of the game — draw one, discard one — until
someone completes a winning hand.

You can also take other players' discards to complete sets (claims — they
have their own page in this folder).

Winning
───────
You win by completing four sets + a pair with EITHER:

  - a tile you drew yourself (a SELF-DRAW win), or
  - the tile an opponent just discarded (a DISCARD win — you "claim" it).

One catch: under our house rules your hand must be worth at least 3 FAN
(scoring points) to be a legal win. Most natural hands clear this; the
Scoring folder explains fan. If nobody wins before the wall runs out, the
hand is a draw and no points change hands.

Flowers
───────
If you draw a flower tile it is set aside immediately and you draw a
replacement — flowers never occupy hand space. Each flower you hold when
you win is worth a bonus fan.

A first-hand strategy in one line
─────────────────────────────────
Keep tiles that work together (neighbors in a suit, pairs), discard lone
honors early, and don't be afraid to lose your first few hands — watching
what the bots discard teaches the rhythm faster than any rulebook.`,
      },
      {
        slug: "claims-melds",
        title: "Claims, melds & flowers",
        body: `CLAIMS, MELDS & FLOWERS
═══════════════════════

When an opponent discards a tile you need, you can claim it. A claimed set
is placed face-up next to your hand (a MELD) — it still counts toward your
four sets, but everyone can see it, and an "open" hand gives up the fan
bonuses for staying concealed.

The claims
──────────
  CHI   (chow)  Take the discard to complete a run, e.g. claim a 5B to
                finish 4B-6B. ONLY allowed from the player to your LEFT
                (the player before you in turn order).
  PENG  (pung)  Take the discard to complete a triplet — you must already
                hold the other two. Allowed from ANY player.
  GANG  (kong)  Take the discard holding the other three (an exposed kong),
                OR on your own turn: lay down four from hand (a concealed
                kong) or add your drawn 4th tile to an existing pung meld
                (an added kong). Every kong earns a replacement draw from
                the wall — and kongs score fan on their own.
  HU    (win)   Take the discard to complete a legal winning hand.

After a CHI or PENG you discard immediately (no draw). After any GANG you
draw a replacement tile first.

Claim priority
──────────────
If several players want the same discard:

  HU  beats  PENG / GANG  beats  CHI

A win always takes precedence; ties between multiple winners go to the
seat closest in turn order after the discarder.

Concealed vs open
─────────────────
Claiming is fast but costly: a fully concealed hand (no claimed melds) that
wins by discard earns the Concealed Hand fan, and winning by self-draw
while concealed earns Fully Concealed Hand — easy fan that vanishes the
moment you claim. Good players claim when it genuinely speeds a winning
shape, not just because they can. (Our v0 bot claims greedily; v1 is
choosier. See the Bots folder.)

Kong corner cases
─────────────────
  - Robbing the kong: if a player upgrades a pung-meld to a kong with the
    tile that completes YOUR hand, you may claim it and win.
  - The replacement draw after a kong can itself win the hand (that's the
    "Out with Replacement Tile" fan).
  - A concealed kong keeps your hand concealed; exposed and added kongs
    open it.

Flowers
───────
Flowers are set aside the moment they're drawn (with a replacement draw),
are visible to everyone, and pay one fan each when you win. They are pure
bonus — no decisions attached.`,
      },
    ],
  },
  {
    title: "Scoring",
    docs: [
      {
        slug: "fan-chart",
        title: "Fan chart — every fan by value",
        body: `FAN CHART — EVERY FAN, IN INCREASING VALUE
══════════════════════════════════════════

Your winning hand's value is the SUM of every fan (scoring pattern) it
contains, with one rule of thumb: a pattern never double-counts a strictly
weaker pattern it automatically contains (you don't get No Honors on top of
All Simples, or Full Flush on top of Half Flush). The names below are
exactly what the win screen prints. House floor: a hand needs 3+ fan to win.

1 FAN ────────────────────────────────────────────────────────────────────
  Pure Double Chow         two identical chows in the same suit
  Mixed Double Chow        the same chow in two different suits
  Short Straight           two consecutive chows in one suit (123 + 456)
  Two Terminal Chows       123 and 789 of the same suit
  Pung of Terminals or Honors
                           a pung/kong of 1s, 9s, or winds that aren't
                           your seat/prevalent wind
  Melded Kong              one open kong
  One Voided Suit          using only two of the three suits
  No Honors                no winds or dragons anywhere
  Edge Wait                waiting on a 3 for 123 or a 7 for 789
  Closed Wait              waiting on the middle tile of a chow
  Single Wait              waiting to complete the pair
  Self-Drawn               winning on your own draw
  Flower Tiles             one fan per flower set aside

2 FAN ────────────────────────────────────────────────────────────────────
  Dragon Pung              a pung/kong of any dragon
  Prevalent Wind           a pung/kong of the round's wind
  Seat Wind                a pung/kong of your own seat wind
  Concealed Hand           no melds, winning on a discard
  All Chows                four chows + a non-honor pair
  Tile Hog                 all four copies of a tile, without a kong
  Double Pung              two pungs of the same number in two suits
  Two Concealed Pungs      two pungs formed without claiming
  Concealed Kong           one closed kong
  All Simples              nothing but 2-8 (no terminals, no honors)

4 FAN ────────────────────────────────────────────────────────────────────
  Outside Hand             every set (and the pair) contains a terminal
                           or honor
  Fully Concealed Hand     no melds AND winning by self-draw
  Two Melded Kongs         two open kongs
  Last Tile                winning on the 4th copy of a tile when the
                           other three are visible to all

6 FAN ────────────────────────────────────────────────────────────────────
  All Pungs                four pungs/kongs + a pair
  Half Flush               one suit + honors only
  Mixed Shifted Chows      three chows in three suits, each shifted up one
                           (123 / 234 / 345)
  All Types                all three suits, winds AND dragons represented
  Melded Hand              every set claimed, pair completed by discard
  Two Dragons Pungs        pungs/kongs of two different dragons

8 FAN ────────────────────────────────────────────────────────────────────
  Mixed Straight           123 / 456 / 789 spread across the three suits
  Reversible Tiles         only tiles that look the same upside-down
                           (1234589D, 245689B, White Dragon)
  Mixed Triple Chow        the same chow in all three suits
  Mixed Shifted Pungs      three pungs in three suits, each one number up
  Chicken Hand             a hand worth zero fan otherwise (a curiosity —
                           under the house 3-fan floor it cannot be
                           declared, since it needs 8 to exist)
  Last Tile Draw           self-draw on the wall's final tile
  Last Tile Claim          claiming the very last discard of the hand
  Out with Replacement Tile
                           winning on a kong's replacement draw
  Robbing the Kong         winning on the tile an opponent added to a kong
  Two Concealed Kongs      two closed kongs

12 FAN ───────────────────────────────────────────────────────────────────
  Lesser Honors and Knitted Tiles
                           special hand: singles of knitted-suit numbers
                           plus some of the seven honors
  Knitted Straight         1-4-7 / 2-5-8 / 3-6-9, one sequence per suit
  Upper Four               only 6789
  Lower Four               only 1234
  Big Three Winds          pungs/kongs of three of the four winds

16 FAN ───────────────────────────────────────────────────────────────────
  Pure Straight            123 456 789 all in one suit
  Three-Suited Terminal Chows
                           123+789 in two suits, a 5-pair in the third
  Pure Shifted Chows       three chows in one suit, each shifted 1 or 2
  All Fives                a 5 in every set and the pair
  Triple Pung              the same pung in all three suits
  Three Concealed Pungs    three pungs formed without claiming

24 FAN ───────────────────────────────────────────────────────────────────
  Seven Pairs              special hand: seven pairs, no sets
  Greater Honors and Knitted Tiles
                           all seven honors as singles + knitted singles
  All Even Pungs           pungs and pair entirely of 2,4,6,8
  Full Flush               one suit, nothing else
  Pure Triple Chow         three identical chows in one suit
  Pure Shifted Pungs       three consecutive pungs in one suit
  Upper Tiles              only 789
  Middle Tiles             only 456
  Lower Tiles              only 123

32 FAN ───────────────────────────────────────────────────────────────────
  Four Pure Shifted Chows  four chows in one suit, each shifted 1 or 2
  Three Kongs              any three kongs
  All Terminals and Honors only 1s, 9s, winds and dragons

48 FAN ───────────────────────────────────────────────────────────────────
  Quadruple Chow           four identical chows in one suit
  Four Pure Shifted Pungs  four consecutive pungs in one suit

64 FAN ───────────────────────────────────────────────────────────────────
  All Terminals            only 1s and 9s
  Little Four Winds        three wind pungs + the fourth wind as the pair
  Little Three Dragons     two dragon pungs + the third dragon as the pair
  All Honors               only winds and dragons
  Four Concealed Pungs     four pungs, none claimed
  Pure Terminal Chows      123 123 789 789 + a 5-pair, all one suit

88 FAN ───────────────────────────────────────────────────────────────────
  Big Four Winds           pungs/kongs of ALL four winds
  Big Three Dragons        pungs/kongs of ALL three dragons
  All Green                only 2,3,4,6,8 Bamboo and Green Dragon
  Nine Gates               1112345678999 in one suit, winning on any tile
                           of that suit
  Four Kongs               four kongs
  Seven Shifted Pairs      seven consecutive pairs in one suit
  Thirteen Orphans         one of every terminal and honor + one duplicate

How fans combine
────────────────
Most hands score by stacking small fans: a concealed all-chows hand with a
short straight and a self-draw is 2+2+1+1 = 6 fan. The wait fans (Edge,
Closed, Single Wait) count only when the wait was genuinely forced, and
each fan's "implied" weaker patterns never stack with it. When in doubt,
win the hand — the score screen itemizes every fan it found.`,
      },
      {
        slug: "house-scoring",
        title: "House scoring & payouts",
        body: `HOUSE SCORING & PAYOUTS
═══════════════════════

The floor: 3 fan
────────────────
A hand must be worth at least 3 FAN to be declared a win. The server
enforces this — if the win button isn't offered, your hand is short. (The
flower fans and Self-Drawn count toward the floor.)

From fan to points: the X table
───────────────────────────────
The winner's fan total is looked up in this table:

  fan      1    2    3   4-6   7-9  10-15  16-23  24-43  44-63  64-87  88+
  X        2    4    8   16    32    64     80    160    240    360   500

(The 1- and 2-fan rows exist for reference; the 3-fan floor makes them
unreachable.) X roughly DOUBLES per tier up to 15 fan — pushing a decent
hand one tier higher is worth as much as winning a whole extra hand. The
doubling deliberately breaks above 16 fan to keep the top end sane.

Who pays
────────
Payouts are zero-sum:

  Win by DISCARD     the player who discarded pays 2X,
                     the other two pay X each
                     → the winner receives 4X
  Win by SELF-DRAW   all three players pay 2X each
                     → the winner receives 6X

Self-draw is always worth 1.5x a discard win — a deliberate house lever
that rewards concealed play. And feeding the winner costs you DOUBLE what
the bystanders pay: watch what you discard once opponents look close to a
win (this is exactly the math our v1 bot plays by).

A worked example
────────────────
You self-draw a concealed hand: All Chows (2) + Fully Concealed Hand (4)
+ Short Straight (1) = 7 fan → X = 32 → everyone pays 64; you gain 192.
The same hand won on a discard instead: Concealed Hand (2) instead of
Fully Concealed (4), no Self-Drawn → 5 fan → X = 16; discarder pays 32,
others 16; you gain 64. Same tiles, three times the points for drawing it
yourself.

The dealer repeats (renchan)
────────────────────────────
If the DEALER wins the hand, they stay dealer and the seat winds don't
rotate. Any other result rotates the deal. There's no extra dealer bonus —
the repeat itself is the reward.

False mahjong (table rule)
──────────────────────────
Declaring a win on an illegal hand ends the hand immediately and costs the
declarer 240 points, paid 80 to each opponent — roughly half a limit hand.
Online you can't trigger this (the server only offers legal wins); it
matters when we play with physical tiles.`,
      },
    ],
  },
  {
    title: "House rules",
    docs: [
      {
        slug: "house-vs-mcr",
        title: "House vs official MCR",
        body: `HOUSE RULES vs OFFICIAL MCR
═══════════════════════════

Our house game IS Mahjong Competition Rules at its core: same tiles, same
turn structure, same claims, and the exact same 81-fan catalog — the server
scores with the same calculator the official Botzone judge uses. What
changes is the win threshold and what the fans are worth in points.

The differences
───────────────
                       OFFICIAL MCR              HOUSE RULES
  Fan floor to win     8 fan                     3 fan
  Points for a win     additive: every loser     X-table: convex tiers
                       pays (8 + fan); the       (see House scoring) —
                       discarder pays extra      4X discard / 6X self-draw
  Self-draw premium    small, only matters       always 1.5x a discard
                       at low fan totals         win, at every size
  Big-hand incentive   roughly linear in fan     X doubles per tier to 15
                       above the base            fan — big hands pay off
  Dealer               rotates every hand,       dealer repeats on a win
                       win or lose               (renchan)
  False mahjong        -30 to the declarer,      hand ends; declarer pays
                       hand continues per        240 (80 to each player)
                       official procedure
  Game length          16 hands (4 rounds)       open points ladder; a
                       fixed                     "game" is 4 full dealer
                                                 rotations in theory

Why the 3-fan floor changes how the game feels
──────────────────────────────────────────────
At 8 fan, most natural hands are illegal — you must deliberately build
toward big patterns (flushes, straights, pungs-of-everything), and play is
slow and architectural. At 3 fan, an ordinary concealed hand with a couple
of small fans is already legal — hands resolve faster, claims matter more,
and defense (not feeding cheap wins) becomes a bigger share of the skill.
The convex X table then re-adds the incentive to go big: a 7-9 fan hand
pays 4x what a 3-fan hand does.

Strategy carryover
──────────────────
Everything you learn about fan composition here transfers to official MCR
directly — the patterns are identical, you just need more of them at once
to cross the 8-fan bar.`,
      },
      {
        slug: "house-vs-riichi",
        title: "House vs Riichi",
        body: `HOUSE RULES vs RIICHI (JAPANESE MAHJONG)
════════════════════════════════════════

If you come from Riichi (the style in most apps and anime), the tiles will
feel familiar but several reflexes need retraining. Our game is from the
MCR (Chinese competition) family.

The differences that matter
───────────────────────────
                       RIICHI                    HOUSE (MCR-family)
  Bonus tiles          none (no flowers);        8 flowers, 1 fan each,
                       red fives optional        auto-replaced when drawn
  Riichi declaration   the centerpiece: bet      does not exist — there is
                       1000 to lock a ready      no declared-ready state
                       hand, enables ura-dora    and no bet
  Dora                 dora / ura-dora add       no dora of any kind —
                       big random value          only the fan patterns
  FURITEN              cannot win by ron on      NO furiten — you may win
                       a tile you discarded      on any tile, including
                                                 one you discarded earlier
  Win minimum          1 yaku                    3 fan (house floor)
  Scoring              fu x 2^(han+2) with       fan total → X table,
                       dealer multipliers        4X / 6X zero-sum payouts
  Dead wall            14 tiles always kept      no dead wall — the whole
                       back                      wall is playable
  Draw (exhausted)     tenpai players are paid   no payments; hand is a
                       by noten players          simple draw
  Dealer repeat        on dealer win or tenpai   on dealer win only
  Abortive draws       several (9 terminals,     none
                       4 winds, 4 riichi...)
  Sacred discard       furiten polices your      your discards carry no
                       own pond                  rule weight (but our v1
                                                 bot still reads them!)

What carries over
─────────────────
Tile efficiency transfers completely: shanten counting, keeping good waits
wide, the draw-discard rhythm. Defense transfers in spirit but not in
mechanism — with no furiten and no riichi declaration there is no "safe
tile by rule"; safety here is statistical (copies visible, suits an
opponent is collecting, melds on the table). Push/fold judgment matters in
both games; here the signal is exposed melds rather than a riichi stick.

What to unlearn
───────────────
Stop waiting for yaku like pinfu/tanyao — think in MCR fans instead (All
Chows and All Simples are the closest cousins). Stop fearing your own pond
(no furiten). And value kongs higher than you're used to: each one is a
fan plus a replacement draw, which is real money under the house X table.`,
      },
    ],
  },
  {
    title: "Bots",
    docs: [
      {
        slug: "bot-v0",
        title: "v0 — greedy offense",
        body: `BOT: v0 — GREEDY OFFENSE
════════════════════════

v0 is the house baseline bot: a pure attacker that races to a legal hand
as fast as possible and completely ignores everything you are doing.

How it thinks
─────────────
Every turn, v0 asks one question: "which discard leaves my hand the fewest
steps away from a hand that can legally WIN?" That last word is the subtle
part — under the 3-fan floor, a structurally complete hand worth less than
3 fan is worthless, so v0 measures distance toward hands that clear the
floor (it is "fan-aware"). When two discards tie, it keeps the hand with
more distinct tiles that improve it (the wider acceptance).

Its fixed reflexes, in priority order:

  1. If it can legally win — it wins. Always. Instantly.
  2. If it can kong — it kongs. Always (a kong is a fan plus a free
     replacement draw, which does most of the work toward the floor).
  3. It claims chi/pung ONLY when the claim strictly brings it closer to
     a winning hand; otherwise it stays concealed.
  4. Otherwise: the discard that minimizes distance-to-a-legal-win.

It is completely deterministic — the same situation always produces the
same move.

What it does NOT do
───────────────────
  - No defense. It never looks at your discards, your melds, or how close
    you look to winning. It will happily discard the exact tile you need.
  - No tile counting. It will wait on a tile even when all four copies
    are visible in ponds and melds (a provably dead wait).
  - No lookahead. It plays the best move for THIS turn only.

How to beat it
──────────────
Build something big. v0 never folds, so a table of v0s is a pure race —
and it pays no attention to danger, so your only real opponent is the
clock. If your hand is slow but valuable, v0 will keep feeding you tiles
a careful opponent would have held back.`,
      },
      {
        slug: "bot-v1",
        title: "v1 — offense + defense",
        body: `BOT: v1 — OFFENSE + DEFENSE
═══════════════════════════

v1 keeps v0's attacking engine and bolts on the two things v0 lacks: it
COUNTS TILES and it WATCHES YOU.

Tile counting (hard accounting)
───────────────────────────────
v1 tracks every visible tile — all ponds, all melds, its own hand — and
knows exactly how many copies of each tile are still live. This changes
its play in three ways:

  - It never waits on a dead tile. If all four copies of its winning tile
    are visible, it reshapes the hand instead of waiting forever (v0 sits
    on dead waits to the end).
  - It values waits by LIVE COPIES, not tile types: a wait with three
    live copies beats a wider wait whose tiles are nearly exhausted.
  - At the finish line it weighs each possible wait by payout — under the
    house X table, steering into the 7-9 fan tier pays double the 4-6
    tier, and v1 does that arithmetic on every discard at tenpai.

Threat reading and defense
──────────────────────────
v1 estimates how dangerous each opponent is from what's publicly visible:
exposed melds (the strongest tell this game has), how late the hand is,
and whether their melds commit to one suit (a flush in the making — that
suit becomes hot). Against a visible threat it grades every discard for
danger: middle tiles are riskier than terminals, honors with all copies
visible are provably safe, tiles in a flusher's suit are radioactive, and
a tile the opponent themselves discarded is heavily discounted.

Then it picks a lane:

  PUSH     no real threat visible — pure offense, like v0 but counting.
  CAREFUL  a strong threat is visible, v1 isn't ready itself, and every
           fastest discard is dangerous — it pays one step of speed for a
           provably safe tile (but never breaks its own live tenpai).
  FOLD     someone with 3 melds (or 2 melds late in the hand) is visibly
           close and v1's hand is hopeless — it stops racing and throws
           the safest tile it has. Feeding a winner costs double, so a
           dead hand's job is purely damage control.

It also refuses the rare kong that would wreck its own hand (v0 kongs
unconditionally, occasionally destroying its own tenpai).

Measured against v0
───────────────────
Over thousands of paired hands on identical walls, v1 wins slightly more
often, scores about five points per hand better, deals into opponents
less, and its winning hands average more fan. The gap is points more than
wins: defense converts losses into smaller losses.

How to beat it
──────────────
Its threat model only sees MELDS. A fully concealed hand is invisible to
v1 until the moment you win — so against v1, staying closed isn't just
worth fan, it's stealth.`,
      },
    ],
  },
];

// Flat lookup used by <docs-page> and tests.
export function docBySlug(slug) {
  for (const section of DOC_SECTIONS) {
    for (const doc of section.docs) {
      if (doc.slug === slug) return doc;
    }
  }
  return null;
}

export const FIRST_DOC_SLUG = DOC_SECTIONS[0].docs[0].slug;

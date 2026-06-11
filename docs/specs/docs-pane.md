# Spec 36 — In-client documentation pane (rules, scoring, house rules, bots)

A reader-facing documentation screen in the web client: a `[ docs ]` header
button opens a full-page view with a folder menu on the left and a document on
the right. Players can learn the game, look up the fan chart mid-session, see
exactly how the house rules differ from official MCR and from Riichi, and read
what the v0/v1 bots they're playing against actually do.

**Status:** implemented this step, on top of Spec 35 (the bot docs describe
v0 and v1, so the branch stacks on `feat/v1-rule-bot`).

## Goals

- **A documentation menu with sub-folders.** Five folders, nine documents:

  ```
  Getting started
    └ Playing on this server          (UI walkthrough: login, tables, bots)
  Rules
    ├ Basic rules — your first hand   (tiles, turns, winning shape)
    └ Claims, melds & flowers         (chi/peng/kong, claim priority, flowers)
  Scoring
    ├ Fan chart — every fan by value  (the full MCR 81-fan table, ascending)
    └ House scoring & payouts         (3-fan floor, X table, 4X/6X payouts)
  House rules
    ├ House vs official MCR
    └ House vs Riichi
  Bots
    ├ v0 — greedy offense
    └ v1 — offense + defense
  ```

- **Reachable from anywhere, including pre-login.** The public deployment
  (Spec 24) invites strangers; "what is this and how do I play" must not sit
  behind the auth wall. The docs view takes precedence over the auth form
  while open; `[ back ]`/Esc returns to wherever the reader came from
  (auth, lobby, or an in-progress table — the table keeps running underneath,
  same as the profile page).
- **House aesthetic, zero new dependencies.** Content is plain text with
  box-drawing headers rendered in a `pre-wrap` monospace block — the client's
  ASCII look — not rendered markdown. No CDN additions, no parser.
- **Content integrity is tested.** A Python test cross-checks the content
  module against the code it documents: every `SEAT_BOTS` bot_id has a doc;
  the fan chart contains every fan value tier and the engine's exact fan
  names for the load-bearing examples; the house payout table matches
  `mcr-house-3fan.json`. Docs that drift from the code fail CI.

## Non-goals

- **No markdown pipeline.** Plain text strings in a JS module. If the docs
  ever need rich rendering or server-side editing, that's a separate spec.
- **No per-account state** (read-tracking, bookmarks). Stateless view.
- **No i18n.** English only, matching the rest of the client.
- **Not the strategy guide.** The bot docs explain *how the bots decide*, not
  optimal human play.

## Architecture

Two new static modules + app wiring; no server or wire changes (static files
are already served; the content ships with the client).

- **`mahjong/web/static/docs_content.js`** — `export const DOC_SECTIONS`:
  `[{ title, docs: [{ slug, title, body }] }]`. Bodies are template-literal
  plain text, hand-wrapped at ≤ 78 columns, with `═`-underlined headings and
  ASCII tables. Content-only module: no imports, no Lit — trivially parseable
  by the Python integrity test (regex for slugs/titles, plain-string search
  for content assertions).
- **`mahjong/web/static/docs.js`** — `<docs-page>` (Lit). Sidebar lists
  sections and topics (buttons; active topic highlighted), content area shows
  the active doc in `<pre class="body">` (`white-space: pre-wrap`). State:
  `activeSlug` (defaults to the first doc). Emits `docs-back`. Narrow
  screens: sidebar collapses above the content (flex-wrap), nothing fancy.
- **`app.js` wiring** (mirrors the profile page):
  - `_view` gains `"docs"`; `_docsReturnView` remembers where to go back to
    (lobby/table/profile/auth — set on open, used on close).
  - Header gains `[ docs ]` (always visible, incl. on the auth screen).
  - Esc chain: settings → **docs** → profile.
  - Render precedence: `showDocs = this._view === "docs"` is checked *before*
    `showAuth`, so docs work pre-login; `<table-page>` stays mounted-but-
    hidden underneath, exactly like the profile view.

## Content sources (what each doc is grounded in)

| Doc | Ground truth |
| --- | --- |
| Playing on this server | the client itself (login → lobby → create-table → seat-bot picker → claim window UI) |
| Basic rules / Claims & melds | official MCR play rules; tile notation matches the client's renderer |
| Fan chart | the official MCR 2006 81-fan table — the same table PyMahjongGB (our scorer) implements; names spelled exactly as the engine's win screen prints them (e.g. "Mixed Straight", "Concealed Hand", "Single Wait") |
| House scoring & payouts | `mahjong/engine/rulesets/mcr-house-3fan.json` (floor 3, tier table, `4X` discard / `6X` self-draw, renchan, false-mahjong penalty) and ai-plan § Rulesets |
| House vs MCR / vs Riichi | same, plus official MCR scoring (fan + 8 base) and standard Riichi rules for the contrast columns |
| v0 / v1 bot docs | Spec 27 and Spec 35, summarized for players |

## Verification

UI is the pragmatic-TDD bucket (no engine logic); the *content-code agreement*
is the part worth pinning hard:

1. **Content integrity (`tests/web/test_docs_content.py`, no browser).**
   Parses `docs_content.js` as text: (a) every `SEAT_BOTS` key appears as a
   bot doc slug — adding a bot without documenting it fails; (b) the fan
   chart doc contains every official fan tier (88/64/48/32/24/16/12/8/6/4/2/1)
   and the engine-exact spellings of sentinel fans; (c) the house doc quotes
   the live `mcr-house-3fan.json` numbers (floor 3, tiers `[3,8] [6,16]
   [9,32]`, multipliers); (d) all ten slugs unique, all bodies non-trivial
   (> 400 chars).
2. **Component navigation (`tests/web/test_docs_page.py`, Playwright).**
   Mount `<docs-page>`: first doc renders by default; clicking another topic
   swaps the body; `docs-back` fires from the back button.
3. **App wiring (same file).** Full app boot against `FakeWireServer`: the
   `[ docs ]` button is visible on the auth screen, opens the docs view,
   Esc returns to the prior screen.

## Alternatives considered

**Markdown files fetched at runtime + a CDN renderer (`marked`).** Nicer
authoring, but adds a runtime dependency and a fetch/loading state for
content that ships with the client anyway; raw markdown in a `<pre>` looks
wrong, and the client's whole identity is ASCII-in-monospace. Plain text in
a content module needs zero machinery and the Python integrity test gets to
read it as a string.

**Docs as part of settings/profile.** Both are authed/overlay surfaces; docs
want to be a full-page reader reachable pre-login. A sibling top-level view
is the same complexity and strictly more useful.

**Server-rendered /docs route.** Decouples docs from the client bundle, but
duplicates the header/theme machinery and can't be opened mid-table without
losing the session view. In-client view reuses everything.

## Open questions

- **Discoverability mid-table.** The header `[ docs ]` is always there, but a
  contextual hint (e.g. linking the win screen's fan names to the chart) is
  deferred until someone asks for it.
- **Content updates cadence.** The integrity test pins bot docs to the
  registry; if v2 lands, the test fails until a v2 doc is written — that's
  intentional (docs-as-contract), revisit if it proves annoying.

# Server-Priced Paper Bets and an Admin "Set PIN" Button — Design

**Date:** 2026-09-29
**Status:** Awaiting owner review
**Base commit:** `13c0ad5` (plan 026 merged)

## Context

Plan 026 made paper trading safe to share: per-player PINs, an owner key,
bets closed at kickoff, parlays that settle, and a leaderboard ranked by
shrunk ROI with the Model as a player. Its final review found one gap it did
not fix: **players type their own odds and line.** The server accepted a
spread of "HOME +60" at −110 and a moneyline at +100000, and `grade_pick`
settles whatever line is typed, so the ROI leaderboard can be gamed through
the ordinary UI. The owner decided (2026-09-29) that the server prices every
bet, and that **no link is shared with friends until this ships.**

What exists today:

- `backend.analysis.strategy.average_odds` is "THE single definition of
  consensus in this project": moneylines and spread/total prices averaged in
  probability space via `consensus_moneyline`, spread and total lines
  averaged arithmetically. The model's `odds_at_pick` and the CLV report use it.
- `odds` holds one row per (game, bookmaker), overwritten in place, with
  `timestamp` refreshed on every upsert (`full_pipeline.py:564`).
  `player_props` holds one row per (game, bookmaker, market, player, outcome,
  line) with `fetched_at` refreshed on upsert (`:614`).
- `GET /games/today` shows `odds_rows[0]` — the first bookmaker's row, not the
  consensus. The Paper Trading form pre-fills spreads and totals with a
  hard-coded −110 and lets the player edit both the line and the price.
- `PUT /users/{id}/pin` (owner-only) exists; no UI calls it. The two legacy
  players (Marcus, Demo) have no PIN, and `scripts/share.ps1` refuses to print
  a link until they do.

## Goals

1. Every paper bet — single, parlay leg, prop, or "Bet this pick" — is priced
   by the server at the consensus price and line; the client never supplies a
   price or a line.
2. The price a player sees is produced by the same function that prices the
   bet.
3. A bet is refused, with a readable reason, when its price is stale, not
   quoted, or not gradeable.
4. The owner can set or reset any player's PIN from the Admin page, and sees
   which players have none.

## Decisions (owner, 2026-09-29)

- **Price source:** consensus across books, via `average_odds` /
  `consensus_moneyline` — the same definition the Model's picks use.
- **Lines:** consensus line only for spreads and totals; props only at lines a
  book is actually quoting, priced by the consensus of the books quoting that
  exact line.
  **Reversed for spreads and totals by the owner, 2026-10-04:** the average
  line (HOME -11.6, O/U 42.4) is one no book offers and can never push.
  Spreads and totals now follow the prop rule: the line most books quote
  (`strategy.quoted_line`; ties to the line nearest the average, then the
  one worse for the bettor), priced by the books quoting exactly it. The
  Model's own picks still use the averaged line (`average_odds`).
- **Freshness:** refuse when the newest quote behind the price is more than
  **6 hours** old.
- **Approach A:** one pricing module is the only source of prices, used by
  both the bet routes and the quote endpoints.
- **Old request shape rejected** with 422 rather than silently ignored.
- **`/games/today` switches to consensus** so every price on screen agrees.
- **Odds and Line inputs removed** from the bet forms; the parlay's combined
  price is shown as a client-side estimate, confirmed by the server.

## Non-goals

- Changing bets already placed, grading, or the Model's own picks.
- Best-price shopping across books, alternate lines, live/in-play prices.
- Refreshing odds on demand for friends (the refresh stays owner-only).
- Any schema change. `has_pin` is computed, not stored.

## Architecture

```
odds / player_props rows ──► backend/paper/pricing.py ──┬─► POST /users/{id}/picks, /parlay (prices the bet)
   (average_odds, consensus_moneyline, market_label)     ├─► GET /paper/quotes, /paper/prop-quotes (shows the price)
                                                          └─► GET /games/today (consensus display)
```

### 1. The pricing module (`backend/paper/pricing.py`)

Inputs:

- **GameBet** — `game_id`, `pick_type` ∈ {`moneyline`, `spread`,
  `over_under`}, `side` ∈ {`HOME`, `AWAY`} (moneyline, spread) or
  {`Over`, `Under`} (total).
- **PropBet** — `game_id`, `prop_player`, `prop_market`, `outcome` ∈
  {`Over`, `Under`}, `line`.

Output: a **Quote** — `pick_type`, `pick_value` (the label stored and graded),
`odds` (American int), `line` (float or null), `quoted_at` (the newest quote
time behind this price), `prop_player`, `prop_market` — or a **PricingError**
with a reason code and a readable message.

| Bet | Line | Price | `pick_value` (unchanged formats; `grade_pick` parses them) |
|---|---|---|---|
| Moneyline HOME/AWAY | — | consensus `moneyline_home/away` | `HOME ML` / `AWAY ML` |
| Spread HOME/AWAY | consensus `spread_home/away` | consensus `spread_home_price/away_price` | `HOME -3.5` |
| Total Over/Under | consensus `over_under` | consensus `over_price` / `under_price` | `Over 220.5` |
| Prop | the requested line (must be quoted) | `consensus_moneyline` of the quoting books' odds | `Jalen Hurts Over 225.5 Pass Yards` (label via `prop_markets.market_label`) |

Refusals (reason codes):

- `game_started` — the game is not open for betting (`users._open_for_betting`,
  unchanged, including the date fallback for games with no start time).
- `not_quoted` — no book quotes this market, side or line, or the consensus
  has no price for it. **No −110 fallback** for paper bets.
- `stale` — the newest `timestamp` (odds) or `fetched_at` (props) behind this
  price is older than `MAX_QUOTE_AGE = timedelta(hours=6)`. Message: "The price
  is stale — ask Marcus to refresh."
- `not_gradeable` — a prop market not in `MARKET_STAT_MAP` (e.g. the MLB
  markets with no stat columns).

Freshness is judged per market: a spread quote uses the newest timestamp among
the rows that contributed a spread price; a prop uses the newest `fetched_at`
among the rows quoting that exact (player, market, outcome, line).

**Parlays:** each leg is priced by the module; the combined American price is
computed on the server from the leg quotes (the existing decimal-product
formula, now fed server prices).

### 2. API

New read-only endpoints (open, like every GET):

- `GET /paper/quotes?game_id=N` → for that game, every game market:
  moneyline HOME/AWAY, spread HOME/AWAY, total Over/Under. Each entry is a
  Quote, or `{available: false, reason, message}`.
- `GET /paper/prop-quotes?game_id=N` → every prop (player, market, outcome,
  line) at least one book is quoting on that game and that is gradeable, each
  with its consensus Quote or refusal.

Bet placement (PIN-guarded as today):

- `POST /users/{id}/picks` body: game bet `{game_id, pick_type, side, stake}`
  or prop bet `{game_id, pick_type: "prop", prop_player, prop_market,
  outcome, line, stake}`.
- `POST /users/{id}/parlay` body: `{legs: [<game or prop leg>], stake}`.
- **Extra fields are rejected (422)** — `odds` and `pick_value` can no longer
  be sent.
- Response carries the priced bet: `pick_value`, `odds`, `line`, `quoted_at`,
  plus the existing fields. The server's current price is always charged; the
  client compares with what it displayed.
- Status codes: `game_started` → 400 (as now); `not_quoted`, `stale`,
  `not_gradeable` → 409 with the message as `detail`; malformed → 422.

Other changes:

- `GET /games/today` uses `average_odds` for its displayed prices.
- `GET /users/` (and `GET /users/{id}`) add `has_pin: bool`. The hash and salt
  never leave the server.
- `PUT /users/{id}/pin` — unchanged; now called by the UI.

### 3. Screens

**Paper Trading → Place a Pick**

- Choose a game → tabs **Moneyline / Spread / Total / Prop** → side buttons
  showing line and price (`Chiefs −3.5  −112`), from `/paper/quotes`.
- Unavailable sides are disabled with their reason shown beneath.
- The Odds and Line text inputs are removed; stake + PIN + Place remain.
- "Prices fetched Xh ago" per game; quotes refetch every 60 s and on window
  focus.
- Props: a searchable list from `/paper/prop-quotes`, one row per
  player/market/line with Over and Under price buttons.

**Parlay builder** — legs added from the same side buttons; the combined price
is displayed as "estimate — confirmed when placed".

**After placing** — the toast shows the price charged, and notes a move
("Placed at −115 — was −110 when you looked").

**Bet this pick popup (Today's Picks)** — maps the model's `pick_value` to a
side (`HOME ML` → moneyline HOME; `AWAY +3.5` → spread AWAY; `Over 220.5` →
total Over; a prop label → its prop fields), fetches the current quote, and
shows it before confirming, noting when it differs from the model's price.

**Admin → Set PIN** — each player row gets **Set PIN**, opening an inline
masked 4–6 digit field with Save/Cancel (no browser dialog). Requires the owner
key saved on the page; otherwise it says so. Players with `has_pin: false`
show a **No PIN** badge.

## Data model changes

None. `has_pin` is computed from `pin_hash IS NOT NULL`.

## Testing strategy

**Pricing module** (injected clock; expected values hand-computed, not by
calling the function under test):

- consensus of two and three books at different prices;
- the freshness boundary: 5h59m accepted, 6h01m refused, per market;
- `not_quoted` when no book prices a market — and nothing ever returns −110;
- props: unquoted line refused; a line quoted by two books gets their
  consensus; an ungradeable market refused;
- a started game refused;
- **round trip**: every label the module produces is priced, placed, the game
  finished, and graded correctly by the existing grader.

**API**:

- **anti-tamper**: a body containing `odds` or `pick_value` → 422
  (mutation-checked);
- the odds charged equal the odds `/paper/quotes` returned; a price moved
  between quote and place → the new price is charged and reported;
- parlay combined price computed server-side from leg quotes;
- `/games/today` shows the same consensus as the quotes;
- `has_pin` present and correct; no hash or salt in any response;
- the write-route coverage test still passes (new endpoints are reads).

**Frontend**:

- side buttons render line + price; unavailable sides disabled with reason;
  no Odds input exists on the bet forms;
- the popup maps `HOME ML` / `AWAY +3.5` / `Over 220.5` / a prop label to the
  right request;
- Set PIN sends only on Save, shows the owner-key message without a key, and
  the No PIN badge appears exactly where `has_pin` is false.

**Evidence rules**: every guard mutation-checked (synthetic-app meta-tests
where the permission classifier blocks security-code mutation); backend suite
with and without the live db; frontend vitest/tsc/eslint clean; a Chrome check
on a built copy whose quote timestamps are bumped so real prices show — fresh
and stale states, desktop and 414 px, computed styles where screenshots stall.
**No bet is ever placed against the live database** during verification.

## Sequencing

1. Pricing module + unit tests (no dependencies).
2. Quote endpoints + `/games/today` consensus + `has_pin` (needs 1).
3. Bet routes priced by the module; old shape rejected; parlay pricing
   (needs 1).
4. Frontend: quotes, side buttons, prop list, parlay builder, popup (needs 2, 3).
5. Admin Set PIN (needs `has_pin` from 2).
6. Visual check, then the owner sets the legacy PINs and runs
   `scripts/share.ps1` (post-merge).

## Open questions

None blocking. Decided above: consensus price; consensus line only; 6-hour
freshness; approach A; 422 for the old request shape; `/games/today` to
consensus; Odds/Line inputs removed; client-side parlay estimate.

# Sportsbook UI — design

Date: 2026-10-07. Status: approved in conversation, awaiting written-spec review.

## 1. Intent

**Owner's words:** the app should "mimic the look and feel of the popular
sports betting apps like DraftKings and FanDuel", and the paper-trading part
"should feel real even though it's for fun and bragging rights between my
friends."

**Agreed in brainstorming (2026-10-07):**

- Full sportsbook (approach A). A sportsbook section becomes the home screen,
  and the model and research pages are restyled behind a Research menu.
- The Lobby replaces Today's Picks as `/`.
- The first version includes all four extras: live scores on open bets, a
  friends' bet feed with Tail, bet receipts and win celebrations, and cash out.
- Cash out is **pre-game only**, at a **5% house margin**. It **counts toward
  the leaderboard ROI**.
- The board shows **7 days** ahead.
- Quick-stake chips are $25 / $50 / $100 / Max. "Accept any odds changes" is
  **off** by default.

**Assumptions (not stated by the owner):**

- Friends mostly use phones, through the Cloudflare tunnel link.
- It stays paper money with the name plus PIN identity.
- The branding is Metric Edge in a sportsbook style. It never uses DraftKings
  or FanDuel names, logos or exact colours.

**Success looks like this:**

- A friend opens the link on a phone and taps odds buttons to build a slip.
- They place a straight bet or a parlay and get a ticket.
- They watch it live in My Bets, and can cash it out before kickoff.
- They see each other's bets and wins in a feed, and can tail them.
- Every price and balance comes from the server, which is the final judge.

**Out of scope:**

- Live in-game betting.
- Alternate lines, futures and boosts.
- In-game cash out.
- Real money.
- A light theme.
- New identity or authentication schemes.

## 2. Constraints from the existing code

- **Prices.** Bets are priced by `backend/paper/pricing.py`:
  - `game_quotes` returns 6 sides and `prop_quotes` returns the gradeable props.
  - Each side is a quote or a refusal (`game_started`, `not_quoted`, `stale`,
    `not_gradeable`).
  - A price older than `MAX_QUOTE_AGE` (6 h) is refused.
  - `open_for_betting` closes any game whose status is not `scheduled`.
  - Every new price surface **calls these functions**; it never re-implements
    them.
- **Money.**
  - `balance_of` is starting balance + sum(payout) over settled straight bets
    and parlays.
  - `open_stakes` is the stakes where `result IS NULL`.
  - `available_of` = `balance_of` − `open_stakes`.
  - `hold_bankroll` takes the SQLite write lock before reading the balance.
- **Settlement.** `paper_settlement.grade_paper_picks`, the push rules and
  `settle_parlays` only touch rows with `result IS NULL`.
- **Feed.**
  - `ActivityFeed` plus `_log_feed_event` broadcasts over the existing
    WebSocket.
  - Won and lost events are written only by the owner's `POST /users/grade`.
    The automatic settlement writes none.
- **Parlays.**
  - `POST /users/{id}/parlay` allows same-game parlays.
  - It refuses two legs on the same market. The key is (event, pick_type), or
    (event, player, market) for props.
  - The frontend `addOrReplaceLeg` and `marketKey` mirror this rule.
  - `parlayEstimate` mirrors `pricing.combine`.
- **Scores.** `Game.home_score` / `away_score` exist. Ingestion never writes
  `in_progress` during a game. ESPN scoreboards exist for nba, nfl, ncaab,
  ncaaf, mma and mlb; boxing has none.
- **Styles.** Styles live in `frontend/src/index.css`, built on design tokens.
  `App.css` is never imported.
- **Other callers.** The Claude-picks script (`logs-archive/`) posts straight
  bets through `POST /users/{id}/picks` with a PIN. It must keep working
  unchanged.

## 3. Look and feel

- **Palette.** These tokens replace the existing `:root` set in `index.css`,
  keeping the token names where they exist so the Research pages restyle for
  free:
  - base `#0d0f12`
  - surface `#181b20`
  - elevated `#22262d`
  - accent "edge green" `#2fe37a`, with `#0d0f12` text on it
  - loss red `#ff4d5e`
  - live amber `#ffb020`
  - muted text `#8a93a3`
- **Typography.** Inter.
  - Team names at weight 700.
  - Odds and money in `font-variant-numeric: tabular-nums`.
  - Money is always shown as `$1,240.50`, and American odds always carry a sign.
- **Odds tile.**
  - A raised grey tile showing the line (top) and the price (bottom).
  - The states are: normal, selected (green with dark text), locked (🔒, with
    the refusal message as the title or a long-press), and flash (green or red
    for 1 s when a refetch changes the price).
  - Tapping the other side of a market already on the slip **swaps** it
    (`addOrReplaceLeg`).
- **Motion.**
  - A 120 ms pop when a tile is added.
  - The slip slides up.
  - The receipt drops in.
  - Confetti on a win.
  - All of it is disabled under `prefers-reduced-motion`.
- **Theme.** Dark only.

## 4. Navigation and layout

- **Top bar:** the "METRIC EDGE" wordmark, then the **player chip**
  ("Marcus · $10,240", the available balance). Tapping the chip lets you switch
  player or join.
- **Phone (< 900px):**
  - A bottom tab bar: **Lobby** `/`, **My Bets** `/bets` (badge: open count),
    **Leaders** `/leaders`, and **Research** (a menu).
  - A green bet-slip bar sits above the tab bar whenever the slip is non-empty.
- **Desktop (≥ 900px):** the tabs move into the top bar, and the slip is a
  fixed right-hand column.
- **The Research menu:**
  - Model Picks `/picks` (the old Today's Picks page)
  - Props `/props`
  - Track Record `/track-record`
  - Backtesting `/backtesting`
  - FAQ `/faq`
  - Admin `/admin`
- **Redirects:** `/paper-trading` → `/bets`.
- **New routes:** `/game/:id`, `/bets`, `/leaders`.
- **Research pages:** they keep their components. Only the tokens, fonts and
  spacing change.

## 5. Lobby and board

### Backend: `GET /paper/board?sport=<s>&days=7`

- **Read-only and open**, like the other GETs.
- **Which games:** every game where `open_for_betting(game)` is true, from
  today to today+`days` (ET). `sport` is optional; `days` is clamped to 1..14.
- **Per game:**
  - id, sport, the home and away display names, `start_time` (UTC ISO), date;
  - `quotes`: exactly `pricing.game_quotes(session, game)`;
  - `prop_count`: the number of *available* entries in
    `pricing.prop_quotes(session, game)`;
  - `model_pick`: the published model pick on this game, from
    `PickModel.published()`, as {pick_type, side, line, odds, edge_pct}, or
    null.
- **Ordering:** by start time, then id.
- **Cost:** `prop_count` prices every prop. If this is measured at more than
  1 s for a full NFL Sunday, it gets a cheaper count of priced keys behind the
  same freshness rule. Measure it on a live db snapshot before choosing.

### Frontend

- **Refetch:** the board refetches every 60 s, and on window focus.
- **Sport tabs:** only sports present on the board, ordered with the soonest
  game first.
- **Model Picks strip:** the games with a `model_pick`. A tap adds that leg to
  the slip.
- **Game cards:** grouped by ET day ("Today", "Sun Oct 11").
  - Each team row has spread, moneyline and total tiles; Over goes on the away
    row and Under on the home row.
  - The card header shows the start time.
  - The footer shows "+N props ›" → `/game/:id`.
- **`/game/:id`:**
  - A **Game Lines** tab with the same tiles, larger.
  - A **Player Props** tab grouped by `market_label`, with one row per player
    and line, carrying Over and Under tiles. It uses the existing
    `/paper/prop-quotes`.
- **Offline:** if a board fetch fails, a "Board offline — prices unavailable"
  banner shows and every tile renders locked.

## 6. Bet slip and placement

### State

- A zustand store holding legs. Each leg is
  {leg: BetLeg, label, gameLabel, startTime, odds, line, stake}.
- It is persisted to `localStorage` in a try/catch, so it survives reloads.
- Adding a leg uses `addOrReplaceLeg`.
- A leg whose `startTime` has passed is marked "Betting closed" and skipped
  when placing.

### Player identity

- No change to the identity model: a name plus a 4–6 digit PIN, kept in
  `sessionStorage` per tab (`lib/secrets.ts`).
- The PIN is asked for inline in the slip the first time per tab, and
  "Join the league" lives in the player chip.
- A 401 clears the stored PIN, as `BetModal` does today.

### Slip UI

- **Singles tab:**
  - One stake per leg, set with the quick chips $25 / $50 / $100 / Max
    (Max = the available balance).
  - A "same stake for all" toggle.
  - Each leg shows "To win".
  - The footer shows the total stake and total to win.
- **Parlay tab** (2 or more legs):
  - One stake.
  - The odds come from `parlayEstimate`, labelled as an estimate.
  - An "SGP" tag when two legs share a game.
- **Place Bet** is disabled when the total stake is above the available
  balance, which is read from the users endpoint. The server re-checks under
  `hold_bankroll`.

### Backend: price-move protection

- `PlaceBetRequest` and each parlay leg gain an optional `expected_odds: int`
  and `expected_line: float | None`.
- **When they are present:** if the freshly priced quote's odds or line
  differ, the endpoint responds **409** with
  `{"reason": "price_moved", "odds": <new>, "line": <new>, "pick_value": <new>}`
  and writes nothing.
- **When they are absent:** the behaviour is exactly as today, which keeps the
  Claude-picks script working.
- The comparison is exact on odds and on line.

### Frontend: odds changes

- The slip sends the expected odds and line unless "Accept any odds changes"
  is ticked. That checkbox defaults to off and is kept in `localStorage`.
- On `price_moved`, the leg is updated to the new price and highlighted as
  "Odds changed −110 → −120", and the button reads **Accept & Place**.

### Placing

- **Singles** are POSTed one after another.
  - Each leg reports its own outcome.
  - Placed legs leave the slip; failed legs stay, carrying the server's
    message for that refusal code.
- **Parlay:** one POST.
- Both responses already return the new bet's `id` (checked 2026-10-07), so
  the receipt uses it.

### Receipt

- A ticket sheet showing "BET PLACED ✓", the bet id (`#P-<id>` straight,
  `#PL-<id>` parlay), legs, stake, the odds charged, to win, and the placed
  time.
- Buttons: **Keep picks** (refills the slip with the same legs) and **Done**.

## 7. My Bets

- **Endpoint:** a new `GET /users/{id}/bets`, read-only.
  - `GET /users/{id}/picks` stays exactly as it is. It is a flat list that
    mixes parlay legs with straight bets, with no `parlay_id` and no scores,
    and the old page and tests depend on that shape.
  - It returns **tickets**: one per straight bet and one per parlay, newest
    first.
  - Each ticket carries `kind` ("straight" | "parlay"), id, stake, odds (the
    combined odds for a parlay), result, payout, `created_at`, and its legs.
  - Each leg carries pick_type, pick_value, the prop fields, leg result, and
    its game: sport, home and away names, `start_time`, status, scores and
    `live_detail`. `live_detail` is null until phase 4.
  - From phase 6, an open ticket also carries `cash_out`:
    `{available: true, offer}` or `{available: false, reason}` (see §9).
- **Tabs:**
  - **Open** (the default).
  - **Settled**, filterable by All, Won, Lost and Cashed out.
- **Ticket card:**
  - The bet id, type (Straight / Parlay / SGP), placed time, and stake → to win
    (or the result).
  - One row per leg with a status dot: pending, won, lost, push or cashed out.
  - A settled ticket gets a green or red edge.
- **Header strip:** Available, Settled balance, Open stakes and Today's P/L,
  taken from server fields only.
- **Celebrations:**
  - When a player's settled **win** first appears on a device, a "WINNER 🎉
    +$X" banner shows with confetti, and a larger one for a parlay.
  - The bet ids already seen are kept per player in `localStorage`, so a
    cleared browser at worst replays a banner.
  - A loss gets no animation.

## 8. Live scores

### Backend job `live_scores`

- **Schedule:** every 2 minutes. `coalesce=True`, short misfire grace.
- **When it does nothing:** when no game has `start_time <= now` with status
  in (`scheduled`, `in_progress`).
- **Otherwise:**
  - It fetches the ESPN scoreboard for each (sport, ET date) involved, through
    `ESPNCollector.fetch_scoreboard`. Boxing is skipped.
  - It matches events to games by `espn_id`.
  - It writes `status = "in_progress"`, `home_score`, `away_score` and
    `live_detail`.
- It **never writes `final`** or any other status, and never touches a
  `final` row.
- **Migration:** adds `games.live_detail` (TEXT NULL). Back up the db and stop
  the scheduler first, per the operating rules.
- **Verify in phase 4 before shipping:**
  1. The existing results collection still moves an `in_progress` row to
     `final` with scores. `full_pipeline` ~L255 / L468 has a "final /
     in_progress rows are never altered" guard. If that guard blocks it, the
     fix belongs in the results path, with a test, before the live job
     ships.
  2. `open_for_betting` refuses an `in_progress` game. It already requires
     `scheduled`, so add a test.

### Frontend

- A live ticket shows a score bar, for example "DAL 17 – TB 14 · Q3 4:12".
- Moneyline, spread and total legs carry a Winning / Losing tint, computed from
  the score and the leg's line. A push-line state is shown as "Even".
- Prop legs show "Live" with no tint.
- Boxing shows "Live" once it is past its start time.

## 9. Cash out (pre-game only)

### Offer

`offer = stake × D_placed × p_now × (1 − 0.05)`, rounded down to the cent.

- `D_placed` is the decimal price of the bet as placed. For a parlay it is the
  parlay's combined decimal price.
- `p_now` is the **no-vig** probability of the *same side at the same line*,
  taken from the current consensus pair priced by `pricing` (that side and the
  opposite side, de-vigged with the existing odds utilities). For a parlay,
  `p_now` is the product over its legs.
- `CASH_OUT_MARGIN = 0.05` is a named constant.

### Not offered (with a reason code)

- `game_started`: any leg's game is not `open_for_betting`.
- `line_moved`: a leg's current line differs from the placed line (spread,
  total or prop).
- `stale` / `not_quoted`: either side of a leg cannot be priced.
- `settled`: the bet already has a result.

### Endpoint

- `POST /users/{id}/cashout`, body `{bet_id, kind: "straight"|"parlay",
  expected_offer}`. It requires the player PIN and takes `hold_bankroll`.
- It recomputes the offer.
  - If the offer is unavailable, it returns 409 with the reason.
  - If the offer is below `expected_offer`, it returns 409 `offer_changed`
    with the new offer.
- Otherwise it sets `result = "cashed_out"`, `payout = offer − stake` and
  `graded_at = now`, and logs a `cashed_out` feed event.
- For a parlay it sets this on the `Parlay` row. The legs stay ungraded and
  are graded normally later, with stake 0, so they have no money effect.

### Effects (each needs a test)

- `balance_of` and `open_stakes` are correct with no code change.
- Settlement skips the row, because its result is not NULL.
- `settle_parlays` skips a cashed-out parlay.
- **Leaderboard:**
  - A straight cash out counts as a settled bet in ROI, with profit = payout.
  - It counts in neither wins nor losses in the record.
  - Every reader that branches on `result` must be audited: `paper_bets`,
    `summarize`, the user stats endpoint, streak logic, `export_picks`, and
    `weekly_review`.
- **Data:** a dated entry in `docs/data-dictionary.md` for the new result
  value, recording that it starts on the phase-6 merge date.

### UI

- An open ticket shows **Cash out $87.40**, or a greyed button with the reason.
- Tapping it asks for confirmation, and on success shows a ticket marked
  "CASHED OUT".

## 10. Leaders, feed and Tail

- **Leaders (`/leaders`):** the existing `/users/leaderboard` with its ranking
  rules unchanged, plus cash outs (§9).
  - Each row shows rank, initial avatar, name, profit $, ROI, W-L, streak 🔥,
    and "Unranked (n/10)" where it applies.
  - The current player's row is pinned at the bottom when it is off-screen.
- **Settlement events:**
  - One shared function writes the won / lost / pushed feed events for
    straight bets and parlays.
  - Both `POST /users/grade` and the automatic `paper_settlement` path call
    it, so the two cannot drift apart.
  - The events are de-duplicated per bet: a bet gets at most one settlement
    event.
- **Placed-bet payloads** gain `bet_id`, `kind`, and `legs` (each a `BetLeg`
  plus a display label). Older events without them simply show no Tail
  button.
- **Feed UI:**
  - A full feed on Leaders, and a one-line ticker on the Lobby.
  - Live over the WebSocket, falling back to a 60 s refetch.
- **Tail:** adds the event's legs to *your* slip at the **current** price,
  re-read from the board or the quotes. A leg that is no longer available
  shows as locked in the slip.
- **Visibility:** all bets and stakes are visible to everyone in the league,
  as they are today.

## 11. Error handling

- The server decides price, balance, game start and PIN.
- Every refusal code maps to a plain message shown on the leg or ticket it
  applies to: `stale`, `game_started`, `not_quoted`, `not_gradeable`,
  `price_moved`, `offer_changed`, `line_moved`, insufficient balance, a
  same-market parlay, and a 401 PIN failure.
- No generic "Failed" message for a known code.
- A network failure shows a banner and locks the tiles. The slip keeps its
  legs.

## 12. Testing

- **Backend (pytest).** Mutation-check each guard: break the code it protects
  and confirm the test fails, deleting the .pyc after each write and restore.
  - The board's quotes equal `game_quotes` for each game.
  - Started and out-of-range games are excluded, and `days` is clamped.
  - `expected_odds` / `expected_line` mismatch → 409 with nothing written; when
    absent, the old behaviour holds.
  - Cash out:
    - the formula on a known pair;
    - the parlay product;
    - each refusal reason;
    - `offer_changed`;
    - the balance and open-stakes effects;
    - settlement skip;
    - leaderboard ROI included, W-L excluded.
  - The live job writes scores, `live_detail` and `in_progress`. It never
    writes `final`, never touches a final row, and an `in_progress` game
    refuses bets.
  - Settlement events: written once each from both paths.
  - Tail payload fields are present on new events.
- **Frontend (vitest):**
  - slip swap, singles partial failure, odds-change accept, parlay estimate;
  - celebrate-once;
  - redirects;
  - locked tiles when offline;
  - the live tint for spread and total pushes.
- **Visual:** a claude-in-chrome check at phone (390px) and desktop widths
  against an :8001 build with a db snapshot, before each phase merges.
- **Full suites:** backend pytest, and frontend `npm test` plus `npm run build`
  (tsc), before each merge.

## 13. Phases

Each phase is one branch. It merges on the owner's OK, with a scheduler
restart when the backend changed.

| # | Ships | Backend change |
|---|---|---|
| 1 | Tokens and theme, top bar, tab bar, routes and redirects, Lobby, `/game/:id`, `GET /paper/board` | read-only endpoint |
| 2 | Slip store and UI, singles and parlays, PIN inline, `expected_odds`, receipts; retire `BetModal` and the slip on the PaperTrading page | optional request fields |
| 3 | My Bets, header strip, celebrations | `GET /users/{id}/bets` (read-only) |
| 4 | `live_scores` job, `games.live_detail` migration, live tickets | job and migration (backup, stop scheduler) |
| 5 | Leaders restyle, feed and ticker, shared settlement events, Tail payload | feed writers |
| 6 | Cash out end to end, reader audit, data-dictionary entry | endpoint and new result value |

## 13a. Unchanged surfaces

- The digest email: its links to `/` now open the Lobby.
- The Claude-picks script, which uses the old request shape.
- Model pipelines.
- The export column set. The `result` column gains a value in phase 6; this is
  documented there.

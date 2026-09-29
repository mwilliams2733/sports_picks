# Emailed-Picks Dashboard and Shared Paper Trading — Design

**Date:** 2026-09-28
**Status:** Awaiting owner review
**Base commit:** `4966bb8`

## Context

The daily digest emails picks to four people. Since `697d6fc`, every digest
that is actually sent records its picks in `emailed_picks` — value and price
**as emailed**, because a game pick is refreshed in place until kickoff — and
`backend.digest.record.emailed_record` grades those copies with the grader's
own functions. Nothing displays that record yet; the only way to read it is
`python -m backend.scripts.emailed_record`.

The React SPA already has a **Track Record** page (win rate, ROI and a
confidence table over *all* picks, not emailed ones; no weekly or monthly
view) and a **Paper Trading** page (players, straight bets, parlays, grading,
per-player stats, an activity feed). It has **no authentication**, and it
runs only on this laptop (`uvicorn` on `127.0.0.1:8000`, which serves the
built frontend), next to the scheduler that writes the database.

The owner wants:

1. A dashboard of the emailed picks' **win rate by week, by month and by
   star level**, plus other measures of whether the picks are any good.
2. **Paper trading with friends**, compared against the model.

## Goals

1. Track Record gains an **Emailed picks** view: week, month and stars
   breakdowns, a cumulative-units trend, and honest small-sample context.
2. Paper Trading gains a **leaderboard** on which the model is a player.
3. Friends reach the app through a **link to this laptop**; each player's
   bets are protected by a **PIN**; administrative actions need an **owner
   key**.
4. One definition of every metric, used by every page.

## Non-goals

- **Always-on hosting.** The link works while the laptop is awake — the same
  limit the digest already has. `fly.toml` exists but the pipeline and
  database live here; moving them is a separate project.
- **A permanent URL.** A Cloudflare quick tunnel's URL changes on each start.
  A named tunnel needs a domain on a Cloudflare account; nothing else in this
  design changes when one is added.
- **Real accounts** (passwords, sessions, email). PINs were chosen instead.
- **CLV on the dashboard.** The data exists, but every line-snapshot series
  is still depth-1 (see memory `sports-picks-clv-report`); showing it now
  would present a provisional number as a measurement.
- **Backfilling digests from 2026-09-23 to 09-27.** Never recorded; the
  picks they sent have since been refreshed. Only 2026-09-28 is recorded.
- **A new component library.** The existing page styles and Recharts are used.

## Architecture

```
emailed_picks ──► digest.record.grade_emailed ──┐
                                                ├─► analysis.scorecard ──► /api/stats/emailed*
paper_picks + parlays ──► users settled-bets ───┘                     └──► /api/users/leaderboard
                                                                      └──► /api/users/{id}/stats (migrated)
```

`backend/analysis/scorecard.py` is the only place a metric is defined. It
takes a list of settled bets and knows nothing about where they came from.

### 1. The scorecard module

Input: a sequence of `Bet(result, stake, profit, odds, day, stars)` where
`result` is `win` / `loss` / `push` / `None` (pending), `profit` is in the
bet's own currency (units for the model, dollars for a player), `day` is the
ET date the bet belongs to, and `stars` is the 1–5 confidence or `None`.

Per group of bets:

| Metric | Definition |
|---|---|
| wins, losses, pushes, pending, n | counts; n = settled bets (W+L+P) |
| win rate | wins ÷ (wins + losses). Pushes excluded. `None` with no decided bet. |
| 90% range | Wilson score interval, z = 1.645, on wins of (wins + losses) |
| break-even | mean implied probability (vig included) of the decided bets' prices — the win rate those prices required |
| profit | sum of profit over settled bets |
| ROI | profit ÷ stake over settled bets (a push is staked and returned) |

Over an ordered series: cumulative profit by day, **max drawdown** (largest
peak-to-trough fall in cumulative profit), **longest losing streak** (a push
breaks neither a losing nor a winning streak; a win ends a losing streak).

Groupings:

- **week** — Monday to Sunday, keyed by `day` (ET), labelled by the Monday
- **month** — calendar month
- **stars** — 1★ to 5★; bets with no stars form an "unrated" group

Break-even and the range are what make a small sample readable: the table
colours a group only when its whole 90% range sits above (green) or below
(red) its break-even. Otherwise it is uncoloured — not yet distinguishable
from break-even.

### 2. Emailed picks as bets

**Schema:** `emailed_picks.confidence INTEGER NULL` — the stars as emailed,
copied at send time by `record_emailed` like the price. Additive migration
in `run_migrations`. The five 2026-09-28 rows are backfilled from their
picks, which are unchanged since the send (verified before recording them).

`grade_emailed` already returns `(result, units)` per row; the adapter turns
each row into a `Bet` with stake 1u, `day = digest_date`. **Game picks and
props are never mixed in the emailed breakdowns**: both `/stats/emailed`
endpoints take `kind=game|prop`, because a prop's nominal edge is not on a
game pick's scale (the email separates them for the same reason). The
leaderboard is the one deliberate exception: it compares whole books, and a
player's book mixes game bets and props too, so the Model row does the same.

### 3. Players' bets as bets

A player's settled bets are their **straight bets** (`paper_picks` with
`parlay_id IS NULL`) plus **each parlay once**, at the parlay's own stake and
payout. Parlay legs are never counted. `day` is the game date for a straight
bet and the latest leg's game date for a parlay. Stars are not applicable.

### 4. Endpoints

All read-only and unauthenticated:

- `GET /api/stats/emailed?kind=game|prop&by=week|month|stars` — groups with
  every section-1 metric, plus an all-time total row.
- `GET /api/stats/emailed/trend?kind=game|prop` — cumulative units by
  digest date, max drawdown, longest losing streak.
- `GET /api/users/leaderboard` — one row per player plus **Model** (emailed
  game picks and props together at 1u each): W-L-P, win rate, ROI, profit,
  pending, `ranked`. Sorted by ROI. A row with fewer than **10** settled bets
  is returned with `ranked: false` and sorts after every ranked row.

`GET /api/users/{id}/stats` keeps its response shape but computes through
the scorecard. **Visible change:** its win rate moves from
wins ÷ (W+L+P) to wins ÷ (W+L), so a player with pushes sees a slightly
higher figure. Its parlay handling changes to section 3's.

### 5. Screens

**Track Record.** A source switch at the top: **All picks** (today's view,
unchanged) / **Emailed picks**. The emailed view:

- a Game / Prop toggle
- summary cards: win rate with its 90% range, ROI, units, record vs
  break-even
- a Recharts line of cumulative units, with max drawdown and longest losing
  streak stated beside it
- **Week / Month / Stars** tabs over one table: period, W-L-P, win rate,
  range, break-even, units, ROI, pending — win rate coloured per section 1

**Paper Trading.** A leaderboard above the existing content: rank, name
(Model visually distinct), W-L-P, win rate, ROI, profit, pending. Unranked
rows show "needs 10 bets" in place of a rank.

**Admin.** An owner-key field, stored in that browser's `localStorage` and
sent on owner routes. Owner actions without it show the refusal rather than
failing silently.

**Bet and parlay forms.** A PIN field; the PIN is kept in `sessionStorage`
per player for that tab only.

### 6. Access and security

Every data-changing route, and its protection:

| Route | Protection |
|---|---|
| `POST /users/{id}/picks`, `POST /users/{id}/parlay` | that player's PIN |
| `POST /users/` | open; must set a PIN |
| `DELETE /users/{id}`, `POST /users/grade`, `PUT /users/{id}/pin` (new: reset) | owner key |
| `POST /pipeline/run` | owner key (spends Odds API credits) |
| `POST/PUT/PATCH /backtest/…` (strategies, run, run-all, auto-tune) | owner key |
| every GET | open |

**Owner key.** `SPORTS_PICKS_OWNER_KEY` in `~/.secrets/shared.env`, sent as
`X-Owner-Key`, checked by one dependency `require_owner` with a
constant-time comparison. **Unset key → owner routes refuse** (fail closed).
There is no trust for loopback requests: `cloudflared` connects from
localhost, so a friend's request is indistinguishable from the owner's by
address.

**PINs.** 4–6 digits. Stored as salted PBKDF2-HMAC-SHA256
(`hashlib`, stdlib) in new nullable columns `user_profiles.pin_hash` and
`pin_salt`; the PIN itself is never stored or logged. **Five wrong PINs lock
the player for 15 minutes**, correct PIN included; the counter is in memory
(a restart clears it — acceptable at this scale) and takes an injectable
clock for testing. A player with no PIN sets one on their next bet; after
that it is required. The owner resets a PIN through the owner route.

**Sharing.** `scripts/share.ps1`:

1. Ensures the app server is running **current** code — starts it, or
   restarts it if it predates `HEAD` — with a single-instance guard in the
   style of `start_scheduler.ps1`. (The server running at design time dates
   from 2026-09-23.)
2. Starts `cloudflared tunnel --url http://127.0.0.1:8000`
   (`C:\Program Files (x86)\cloudflared\cloudflared`, already installed).
3. Prints the public URL.

## Data model changes

| Table | Change | Migration |
|---|---|---|
| `emailed_picks` | `confidence INTEGER NULL` | additive; backfill 5 rows from their picks |
| `user_profiles` | `pin_hash TEXT NULL`, `pin_salt TEXT NULL` | additive |

`record_emailed` copies `confidence` from the `DigestPick` it was sent from.
`DigestPick` already carries `confidence`.

## Testing strategy

**Scorecard**, against hand-computed values: Wilson interval vs a published
value plus 0-0, 5-0, 0-5; break-even over mixed prices (−110 and +150);
drawdown with two dips where the deeper must win; losing streak broken by a
win and not by a push; push counted as staked-and-returned in ROI; a Sunday
and a Monday email falling in different weeks, in ET.

**One definition**: the same bets through `/stats/emailed`, the
leaderboard's Model row and `/users/{id}/stats` must produce identical win
rate and ROI — a check on the wiring, since all three call one function.

**Leaderboard**: a parlay counts once and its legs never; Model uses the
emailed price, not a refreshed one; under 10 settled bets is unranked and
sorts last; order is by ROI.

**Security**:
- a **route-coverage test** enumerates every POST/PUT/PATCH/DELETE route on
  the app and fails unless it depends on `require_owner`, on the PIN check,
  or is on an explicit allow-list (`POST /users/`). A route added later
  without protection fails the suite.
- owner routes refuse with no key, a wrong key and an unset key; accept the
  right key.
- PIN: wrong refused; five wrong locks even the correct PIN; the lock lifts
  after 15 minutes on the injected clock; the stored columns never contain
  the PIN; a PIN-less player sets one once, then it is required.

API tests use `create_app(":memory:")` + `TestClient`.

**Evidence rules** (global CLAUDE.md): every guard mutation-checked — break
it, confirm a test fails; suite green on Python 3.12 and 3.14 and with
`DB_PATH=/nonexistent/no.db`; frontend `tsc`, `vitest`, `eslint` clean;
**screens verified visually in Chrome** (emailed view, leaderboard, PIN
prompt), not inferred from markup; **the tunnel verified by a real request
through the public URL** returning the leaderboard JSON, and a bet refused
without its PIN — a running `cloudflared` is not evidence.

## Sequencing and dependencies

1. **Scorecard module** and its tests — no dependencies.
2. **`emailed_picks.confidence`** + `record_emailed` copying it + backfill.
3. **Emailed endpoints** (needs 1, 2).
4. **Players' settled bets + leaderboard + stats migration** (needs 1).
5. **Security**: `require_owner`, PIN columns and check, lockout, route-
   coverage test (independent of 1–4; must land before 7).
6. **Frontend**: Track Record emailed view (needs 3), leaderboard (needs 4),
   PIN and owner-key fields (needs 5).
7. **`share.ps1`** and the end-to-end tunnel verification (needs 5 and 6).
   Nothing is shared before step 5 is live.

## Open questions

None blocking. Decided during design: emailed picks are the measured set;
game and prop kept apart; 90% Wilson range; parlay counts once; 10-bet
minimum to be ranked; existing player stats move to the shared win-rate
definition; PIN per player with lockout; owner key for admin routes; quick
tunnel from this laptop.

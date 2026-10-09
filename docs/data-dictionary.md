# Data dictionary: pick export

This documents the CSV export produced by
`python -m backend.scripts.export_picks --db <path> --out <dir> [--since YYYY-MM-DD] [--sport nfl ...]`
(see the module docstring in `backend/scripts/export_picks.py` for the exact
invocation). It is written for a reader who has never seen this project.

The exporter is read-only (opens the database `mode=ro`) and excludes
`paper_picks` and `user_profiles` -- both are private data about the owner
and paper-trading users, not data about the model, and are not needed for
model analysis.

**Every date and timestamp in this document, and every timestamp column in
both CSVs, is UTC.** Timestamps are written ISO-8601 with a `Z` suffix
(e.g. `2026-09-18T18:00:00Z`); dates (`game_date`, `emailed_digest_date`,
`season`) have no time component and need no timezone. Where this
dictionary cites a git commit's authored time, that time is in the
committer's local zone (Pacific) unless converted to UTC and said so
explicitly -- several dates below (stars hidden, the send bar) were authored
late at night Pacific and land on the *next* calendar day in UTC, which is
the date this dictionary uses throughout.

## What a "pick" is

A pick is one row this project's model generated and stored, before it is
known whether it won. Every pick lives in the `picks` table and gets one row
in `picks.csv`, whether or not it was ever graded or emailed.

There are two kinds, distinguished by `pick_type`:

- **Game picks** -- `moneyline`, `spread`, `over_under`. These are about the
  outcome of a whole game: who wins, who covers a line, whether the total
  goes over or under a number. `edge_pct` on a game pick is the model's win
  probability minus the market's no-vig win probability, in percentage
  points (moneyline), or measured against a flat 0.5 fair value
  (spread/over_under) -- see `market_prob_novig` below.
- **Props** -- `pick_type == "prop"`. These are about one player's
  statistic in one game (e.g. "Over 275.5 passing yards"). A prop's
  `edge_pct` is **not** a probability difference -- see "Known traps" below,
  this is the single most important trap in this dataset.

Game picks and props share the same table and the same columns, but several
columns mean different things (or are blank) depending on which kind a row
is. Always filter or group by `pick_type` before comparing edges, CLV, or
anything price-derived.

## `picks.csv` columns

| Column | Meaning |
|---|---|
| `pick_id` | Primary key of `picks`. Stable identifier for one pick. |
| `created_at` | **Not when the pick was first made -- when it was last REFRESHED.** A game pick is rewritten in place until kickoff (`backend/pipeline/pick_generator.py`'s `_refresh_pick`): `pick_value`, `confidence`, `edge_pct`, `odds_at_pick`, `model_prob`, `suggested_unit_size`, `rationale_json` and `created_at` are all overwritten on each refresh, so `created_at` answers "when was this version of the advice formed", not "when did this row first appear". The side or price a reader sees here can therefore differ from what an earlier snapshot (or an email) showed for the same `pick_id` -- see `emailed_pick_value` below for one way to recover what was actually sent, or **`pick_versions.csv`** for the revision history -- every earlier side/price/edge this column's overwrite would otherwise have destroyed, but **only from the merge of `feat/pick-versions` (pending) forward**; a version recorded before that merge exists only as a single backfilled "final state" row, not a true history (see "known traps: picks are refreshed in place" below). `strategy_id` (-> `strategy`) is still **NOT refreshed** -- it still names whichever strategy FIRST produced the pick, even after a later refresh (see "known traps"). |
| `first_seen_at` | `recorded_at` of the pick's version 1 in `pick_versions.csv`, or blank if the pick has no recorded version history at all. **For a pick whose version 1 was written by the backfill script, this is actually the pick's LAST pre-kickoff state, not its first** -- see "known traps: picks are refreshed in place" below and `pick_versions.csv`'s own section. |
| `n_versions` | How many rows this pick has in `pick_versions.csv`. `0` means no version history was ever recorded for it (predates this table and was never backfilled). |
| `flipped_side` | `True` if the pick's SIDE (not its raw `pick_value`) differs across any two of its recorded versions -- HOME/AWAY/Over/Under/a prop outcome, parsed the same way the `side` column is (`_side`, reused, not a second parser), `False` if the side never changed or there is only one version (or none). A line or price move alone -- `HOME -1.5` -> `HOME -2.5`, a prop's line moving -- is NOT a flip: comparing raw `pick_value` would count every spread/total/prop line move as a side flip, which `side` is specifically parsed out to avoid. |
| `game_id` | Foreign key to `games`. |
| `sport` | `nfl`, `mlb`, `nba`, `ncaaf`, `ncaab`, `boxing`, or `mma`. |
| `season` | The season string stored on the game (e.g. `"2026"`). |
| `game_date` | The game's calendar date (project convention -- see "collection start" below for how this date can predate real collection via backfill). |
| `start_time_utc` | The game's scheduled start, UTC, where known. Nullable. |
| `game_status` | `scheduled`, `final`, etc., as stored on `games.status`. |
| `home_team` / `away_team` | Full team names (`teams.name`), not abbreviations. |
| `home_score` / `away_score` | Final score, null until the game is graded. |
| `strategy` | The strategy that produced the pick, e.g. `ensemble`, `combat_sports`, `prop_value`. Blank if the strategy row is missing. |
| `pick_type` | `moneyline`, `spread`, `over_under`, or `prop`. |
| `pick_value` | The raw stored label, e.g. `"HOME ML"`, `"AWAY -1.5"`, `"Over 8.5"`. For a prop, a player-and-line string that sometimes ends in the raw API market key rather than a display label -- e.g. `"Zach Ertz Over 10.5 player_reception_yds"` -- see `pick_label` for the resolved form, and "known traps" for why. |
| `pick_label` | A human-readable label with HOME/AWAY resolved to the real team name (e.g. `"Pittsburgh Pirates to win"`), and a prop's trailing market key (if any) swapped for its display label. This reuses the same resolvers the daily email and the picks API already use (`backend/digest/render.py`'s `_selection_label` / `_prop_label`) -- there is deliberately no third, independent label function. |
| `side` | `HOME` / `AWAY` / `Over` / `Under` for a game pick; for a prop, `Over` / `Under` when present in the pick value, or `Yes` / `No` for a binary outcome (see "prop outcome shapes" below); blank if unparseable. |
| `line` | The numeric line parsed out of `pick_value` (e.g. `-1.5`, `8.5`), blank for a moneyline pick (there is no line) or when nothing parses. |
| `odds_at_pick` | The American price stored at pick time -- or, for a refreshed game pick, at the time of the **last refresh before kickoff** (see "known traps: picks are refreshed in place"). Null for a small number of legacy rows. |
| `implied_prob_raw` | What `odds_at_pick` implies, vig included (not de-vigged). Computed with `backend.analysis.odds_utils.american_to_implied_prob`. Blank if `odds_at_pick` is null or not a valid American price. |
| `model_prob` | The model's own win probability for the pick, where stored. Nullable for older rows. **Props had none until 2026-10-04**; from then it is the prop's directional probability. |
| `edge_pct` | The stored edge in percentage points. **For game picks the definition changed on 2026-10-03.** Before that it was `model_prob - market_prob_novig`, which is disagreement with the fair price. From 2026-10-03 it is `model_prob - implied_prob_raw`: the edge over the **break-even** price, after the vig, about 1.7 points smaller for the same pick. `min_edge` (3) and the tier thresholds were kept, so the bar is stricter from that date and stars shift down. **Never pool the two regimes.** **See "Known traps": a prop's `edge_pct` before 2026-10-04 is on a different, incompatible scale; from 2026-10-04 it uses the game-pick definition.** |
| `market_prob_novig` | See below. |
| `confidence` | The stored star rating (1-5), still recorded even though stars are hidden from users in the email and frontend (see "known traps"). |
| `suggested_unit_size` | Kelly stake in units, where one unit is 1% of bankroll. Null for picks made before this was persisted (not a computed 0); 0.0 means the sizer looked at the bet and declined it. **See "known traps": the sizer returned a constant 0.5 internally before 2026-09-20, but that value was never persisted -- every pick before 2026-09-21 is NULL here, not 0.5.** |
| `factors` | The rationale's factor codes, `;`-joined as `code:side:strength`, e.g. `rating_gap:home:strong;pitcher_edge:away:moderate`. Blank if `rationale_json` is missing or malformed. |
| `prop_player` / `prop_market` | Only populated for `pick_type == "prop"`: the player's name and the market key (e.g. `player_pass_yds`), not the display label. |
| `result` | `win` / `loss` / `push`, or blank if the pick has not been graded yet. A push means the bet is void -- see "How grading works". |
| `payout` | Net units from `backend.pipeline.grader.payout_for(result, odds_at_pick)`, **for a flat 1-unit stake** -- it is NOT scaled by `suggested_unit_size`. A pick sized at 0.3 units by Kelly and a pick sized at 2.0 units show the identical `payout` for the identical price and result; multiply by `suggested_unit_size` yourself if you want bankroll-scaled P&L. Blank if ungraded. |
| `odds_at_close` / `line_at_close` | The price/line at game close, captured for CLV. Blank if never captured (see "line snapshots" below). **For published (`tracking_only = False`) `spread` and `over_under` picks, `odds_at_close` is a deliberate copy of `odds_at_pick`, not a real closing price.** For `tracking_only` picks (from 2026-10-03) it is the real closing price for the picked side, or blank if no book quoted one. See "What CLV means here". |
| `clv_price_pp` / `clv_line_pts` | Closing line value for this pick, split by unit -- see "What CLV means here" below. Exactly one is filled per row (or neither, if there is no close); **never pool the two columns together.** |
| `series_depth` | The deepest per-bookmaker observation count `line_snapshots` has for this pick's game (see `line_history.csv` below). `0` means the game has no line snapshots at all (most games before 2026-09-22). `1` means no book was ever seen to change its price for that game, so any `odds_at_close`/`line_at_close`/CLV value drawn from it is the SAME observation as the opening price, not a real close. `backend.analysis.clv_report.measurable()` keeps only `series_depth > 1`, so it drops both `0` and `1` rows from its own reporting; use this column to reproduce that filter here. |
| `odds_reconstructed` | `True` when `odds_at_pick` was rebuilt afterwards from surviving book rows rather than recorded live (`backend/scripts/repair_invalid_odds.py`). Treat these as an approximation in any ROI/CLV figure. `backend.analysis.clv_report.usable()` excludes these by default too (`include_reconstructed=False`). |
| `tracking_only` | `True` for a pick generated only to measure CLV, on a market whose model is known to lose to the line. Since 2026-10-03 that means every spread and total for nfl, mlb and ncaaf. These picks are graded like any other, but were never emailed, shown on the site, counted in the record or bankroll, or used to recalibrate. **Never pool them with `False` rows** in any ROI, win-rate or CLV figure. `clv_report` reports them as separate "(tracked)" groups. |
| `withdrawn_at` | When the pick was withdrawn: a later run before kickoff stopped producing it (the market moved, or the 2026-10-03 edge change put it under the bar). Blank if never withdrawn, or if reinstated by a later run. A withdrawn pick was never graded and is not in the published record. Graded, started-game and emailed picks are never withdrawn. Each withdrawal is a `pick_versions` row with `source = withdraw`. Props from the evening of 2026-10-03 (see Known traps). **Before 2026-10-03 nothing was ever withdrawn**, so stale picks from that period stayed in the record. |
| `emailed` | `True` if this pick appears at least once in `emailed_picks`. |
| `emailed_odds` / `emailed_at` / `emailed_pick_value` / `emailed_digest_date` / `emailed_confidence` | What the email actually said, at the time it was sent, for the **most recent** digest that included this pick -- not what the pick's own (possibly since-refreshed) columns say now. A pick can be emailed on more than one digest date (the uniqueness constraint on `emailed_picks` is per `(digest_date, pick_id)`, not per pick); this export keeps the row with the latest `sent_at` and folds the rest away, so one `pick_id` is always exactly one `picks.csv` row. `emailed_pick_value` and `emailed_odds` are the side/price as displayed that day and can differ from the current `pick_value`/`odds_at_pick` if the pick was refreshed after sending (see `created_at` above). `emailed_confidence` is the star rating as sent (nullable -- rows recorded before 2026-09-29 don't always carry it). All five are blank before 2026-09-28, when `emailed_picks` recording began (nothing before that date was recorded, regardless of whether it was actually emailed). |
| `emailed_best_book` / `emailed_best_odds` | The best book and its price shown beside the pick in that email (from 2026-10-07; blank before, when no best price was shown, and when no book offered the same bet at send time). Game picks are priced at the consensus (`emailed_odds`), so the best book is usually a little better; props are already priced at their best book, so the two usually match. If the market moved against a game pick after it was priced, the email said "when picked · best now ..." and `emailed_best_odds` can be WORSE than `emailed_odds`. Book keys are the Odds API's (`williamhill_us` is Caesars). |

### `market_prob_novig`

The fair (no-vig) market probability of the pick's side. **From
2026-10-03 it is stored** (`picks.market_prob_novig`) and exported as-is,
because `edge_pct` is now measured over break-even and no longer encodes
it. Rows before then have nothing stored, and it is recovered from
`edge_pct`, which for them WAS measured against this price:

- **moneyline**: `model_prob - edge_pct / 100`. `edge_pct = (model_prob -
  market_prob) * 100`, so this is arithmetic on the two stored columns, not
  a re-derivation from raw odds -- but it is **not bit-exact**: both inputs
  are themselves rounded before storage (`edge_pct` to 0.1 percentage
  points, `model_prob` to 4 decimal places, see
  `backend/analysis/variants/ensemble.py:284-285` and `:292-293`), so the
  recovered value is accurate to within roughly ±0.0005 of what the
  strategy actually computed internally.
- **spread / over_under**: the same arithmetic, `model_prob - edge_pct /
  100`. **From 2026-10-03** these edges are measured against the de-vigged
  price, just as the moneyline is: both sides' quoted prices with the vig
  removed proportionally. At -105 / -115 the home side is 0.4884. **Before
  2026-10-03** they were measured against a flat 0.5, and the arithmetic
  recovers 0.5 for those rows, within the rounding above. A flat 0.5 equals
  the de-vigged price only when both sides are priced alike, so on an
  asymmetric market an old row's `edge_pct` is off by up to a couple of
  points. Do not pool the two regimes without accounting for that.
  A row with no `model_prob` at all -- every one of the 284 legacy
  spread/over_under picks from before 2026-09-17 (see "known traps" below)
  -- gets a **blank** here instead.
- **prop**: blank. Props are never de-vigged against a market probability
  at all (see "known traps").

## `line_history.csv` columns

One row per `line_snapshots` row -- the append-only price series (see
"known traps: odds is overwritten" below). Columns: `game_id`, `sport`,
`home_team`, `away_team`, `game_date` and `start_time_utc` (for joining back
to `picks.csv`, and for locating the pre-kickoff close for a game that has
no pick of its own at all -- without a start time there is no way to know
which snapshot was "the close"), every price field (`bookmaker`,
`moneyline_home`, `moneyline_away`, `spread_home`, `spread_away`,
`over_under`, `spread_home_price`, `spread_away_price`, `over_price`,
`under_price`), `captured_at` / `last_seen_at`, and `series_depth`. This is
**per-game, not per-row**: it is the deepest observation count any single
bookmaker has for that `game_id` (the same definition, and the same value,
`picks.csv`'s `series_depth` column carries for a pick on that game --
joinable on `game_id`, not on `(game_id, bookmaker)`). A specific row's own
bookmaker may have fewer observations than this number; `series_depth`
answers "did ANY book watch this game's line move", which is what
`backend.analysis.clv_report.measurable()` gates on, not "did THIS
bookmaker's line move". `1` means no book was ever seen to change its price
for the game at all. A row is written only when a price DIFFERS from the
latest row for the same `(game_id, bookmaker)`; an unchanged re-observation
extends `last_seen_at` on the existing row rather than adding a new one.

## `prop_history.csv` columns

One row per `prop_snapshots` row: the append-only prop price series, from
2026-10-07 (nothing earlier exists).

| Column | Meaning |
|---|---|
| `game_id`, `sport`, `home_team`, `away_team`, `game_date`, `start_time_utc` | The game, as in `line_history.csv`. |
| `bookmaker`, `market`, `player_name`, `outcome` | The prop. A series is one combination of these per game. |
| `line`, `odds` | The book's MAIN line and price at that moment, the same main line `player_props` keeps. `line` is blank for yes/no props such as anytime TD. |
| `pulled` | `True` when the book stopped quoting this player in a market it still quoted in the same fetch. `line` and `odds` are blank. A late scratch usually shows up here first. A pull is recorded once; if the prop returns, a normal row follows. |
| `captured_at` / `last_seen_at` | When this price was first seen, and when it was last confirmed still on the board. |

A row is written only when the line or price changes, so for a staleness
study the price in force at time t is the latest row with
`captured_at <= t`. Rows exist only when we fetched. From 2026-10-20 NBA
props are fetched about 2 h and about 45 min before tip (the late run). Other
sports are fetched once per window, so a series of length 1 means "seen
once", not "never moved".

## `pick_versions.csv` columns

One row per `pick_versions` row -- the append-only pick-revision series (see
"known traps: picks are refreshed in place" below, and the dated regime
bullet there for exactly when recording started). Before this table
existed, every refresh in `pick_generator._refresh_pick` and
`prop_pipeline._refresh_prop_pick` overwrote `picks` in place and destroyed
whatever the pick looked like a moment before. This table is the series
that overwrite destroys, on the same append-on-change footing
`line_snapshots` uses for prices (`backend/pipeline/pick_versions.py`'s
`record_pick_version`): a row is written only when a tracked field differs
from the pick's latest recorded version.

Columns: `pick_id` (joins to `picks.csv`'s `pick_id`), `version` (1-based
per pick), `recorded_at` (when this version was observed -- see the
backfill caveat below), `source` (`insert` / `refresh` / `backfill`),
`pick_value`, `confidence`, `edge_pct`, `odds_at_pick`, `model_prob`,
`suggested_unit_size`, and `factors` (built from the version's own
`rationale_json` the exact same way `picks.csv`'s `factors` column is,
reusing the same function -- not a second serializer).

**The backfill caveat.** A pick stored before this table existed has its
version 1 written retroactively by `backend/scripts/backfill_pick_versions.py`,
from the pick's CURRENT (last-refreshed) state, with `source='backfill'` and
`recorded_at` set to the pick's `created_at`. Because a game pick is
refreshed in place until kickoff, that current state is the pick's LAST
pre-kickoff value, not its first -- every earlier side, price or edge it
may have had before this table existed was already destroyed by the
overwrite it replaced. So for a `source='backfill'` version 1:

- `picks.csv`'s `first_seen_at` for that pick is really "last refresh
  time before this table existed", not "when the pick was first made";
- a pick's full history is only genuinely complete from its first
  version recorded with `source` `insert` or `refresh` onward.

## `manifest.txt`

Records when the export ran, the db path, the git commit (`HEAD`) the
export script was run from, row counts per file and per sport, the
`--since`/`--sport` filters applied, and a pointer back to this file.

## How grading works

- A pick is graded once its game finishes and `backend.pipeline.grader`
  processes it; until then `result`/`payout` are blank.
- `payout_for(result, odds_at_pick)` in `backend/pipeline/grader.py` is the
  one place payout is computed from a result and a price -- this export does
  not recompute it. **It is a flat 1-unit stake**, always -- it is not
  scaled by `suggested_unit_size`. A 0.3-unit Kelly stake and a 2.0-unit
  Kelly stake on the same price and result show the identical `payout`;
  multiply by `suggested_unit_size` yourself for bankroll-scaled P&L.
- **A push is a void bet, not a loss.** It should be excluded from any
  win-rate denominator (wins / (wins + losses), never wins /
  (wins + losses + pushes)) and its payout is 0.

## What CLV means here

CLV ("closing line value") compares the price a pick was taken at to the
price the market closed at. It is computed by
`backend.analysis.odds_utils.compute_pick_clv` -- this export calls that
function directly and writes back its two return values into
`clv_price_pp` and `clv_line_pts`; it does not recompute CLV independently.
This is the same function `backend.analysis.clv_report.load_samples` calls
internally, with the same five inputs from the same columns.

**The two columns are genuinely different units, which is why they are two
columns and not one: never pool or average them together.**

- **moneyline**: `clv_price_pp` is filled, in **implied-probability
  percentage points** (closing implied prob minus pick-time implied prob,
  ×100). Positive means the bettor's price beat the close. `clv_line_pts`
  is always blank for a moneyline pick.
- **spread / over_under**: `clv_line_pts` is filled, in **line points** (how
  many points the bettor beat the close by). Positive means a better line
  at pick time. `clv_price_pp` is always blank for these -- and see the
  next paragraph for why `odds_at_close` on these rows is not a real price
  to begin with.
- **prop**: both columns are always blank -- CLV is not computed for props.

Both are blank whenever there is no closing price/line on record for that
pick (absent is not the same as zero -- counting a missing close as 0 CLV
would bias every average toward "no edge").

**Three further caveats, all of which `backend.analysis.clv_report`
applies when it computes its own CLV summaries, and which this export does
NOT apply for you -- filter for them yourself if you want a comparable
number:**

1. **For published `spread` and `over_under` picks, `odds_at_close` is a
   deliberate copy of `odds_at_pick`, not a real closing price.** For
   `tracking_only` picks (from 2026-10-03) it is real; see that column
   above. `grader.py`'s
   `capture_closing_odds` sets `odds_at_close = odds_at_pick` for these two
   pick types (the juice on a line bet is rarely tracked historically and
   barely moves; the real CLV for these is in `line_at_close` /
   `clv_line_pts`). This is why `clv_price_pp` is never filled for a
   spread/over_under row -- computing one against a copied price would
   fabricate movement that never happened.
2. **Before 2026-09-23, the stored close was one arbitrary bookmaker's last
   write, not a consensus.** `capture_closing_odds`'s own docstring
   describes the earlier behavior (reading `Odds` ordered by timestamp and
   taking the first row) as comparing "the gap between one arbitrary book
   and the field" rather than real market movement. CLV on picks graded
   before that date should be treated as noisier than CLV after it.
3. **A `series_depth` of 1 means the "close" is the same observation as the
   price the pick was made from -- not a real close at all.** If a
   bookmaker's price for a game was only ever seen once, any CLV computed
   against it is structurally near zero and is not evidence the bettor beat
   (or lost to) anything; it is evidence of not having watched the line
   long enough. `backend.analysis.clv_report.measurable()` drops these rows
   entirely from its own reporting (`series_depth > 1` only) -- do the same
   here using the `series_depth` column before treating a `clv_price_pp` or
   `clv_line_pts` value as meaningful. Relatedly, `clv_report.usable()`
   also excludes `odds_reconstructed` rows by default -- see that column
   above.

## Known traps

- **From 2026-10-03, picks that stop qualifying are withdrawn
  (`withdrawn_at`).** Before then a stored pick was refreshed while it still
  qualified but never removed when it stopped, so the pre-2026-10-03 record
  includes picks whose edge had gone by kickoff. Since then such a pick is
  withdrawn, ungraded, and out of every published figure, unless it was
  already emailed, graded or underway. **This covered game picks only at
  first; prop picks (`pick_type = prop`) are withdrawn from the evening of
  2026-10-03**, so a prop row with a blank `withdrawn_at` dated earlier
  that day or before was simply never checked. A prop is withdrawn only
  when a run analysed it (its game in that window's sport) and it no
  longer qualified, or when no book offers it any more.

- **Before 2026-10-04, a Bovada prop row could be the top rung of a ladder,
  not Bovada's main line.** Bovada quotes several lines per player inside the
  standard markets; the props table kept the last one written, the highest
  (Under 275.5 -230 where the main line was 245.5 -115). Prop edge ignores
  price, so such a rung could become a pick. No stored prop pick is priced
  -180 or worse, so none is known to have, but a prop pick's line from
  before that date may not be a line any book's main market offered. From
  2026-10-04 each book's main line is stored (`full_pipeline.main_lines`).

- **From 2026-10-04, props are analysed only from each game's latest
  fetch, and only in their own sport's window.** `player_props` rows are
  upserted and never deleted, so a prop a book pulled kept its last line
  forever and was still analysed (40 of 5,971 rows on 2026-10-03). And
  every window analysed every sport's props on the date under one sport's
  thresholds. Before this, a prop pick's `odds_at_pick` could come from a
  line no book still offered, or from another sport's window.

- **Spreads and totals returned on 2026-10-03, as tracking picks
  (`tracking_only = True`).** None were generated from 2026-09-20 to
  2026-10-03, because the models lose to the market line. They are
  generated again for nfl, mlb and ncaaf, for one purpose: to measure CLV on
  real prices. Three things differ from the 241 spread/total picks made
  before 2026-09-20:
  - each carries a real quoted price, never the -110 fallback, and a side
    with no quote gets no pick;
  - `odds_at_close` is the real closing price;
  - none was ever published.

  Their win rate and ROI describe a model known to be worse than the line,
  so they are not a track record. Read them for CLV, split from published
  picks. The owner made this decision on 2026-10-03, with the numbers in
  `SPREAD_TRACKED_SPORTS` (`backend/analysis/variants/ensemble.py`).

Each bullet below was checked against the code, docs, or git history in
this repository as of 2026-09-30 (commit `1c81e33` and this branch's work
on top of it). Where a check could not be made from the code alone, that is
stated explicitly rather than left silent.

- **Picks are refreshed in place until kickoff, and (from the merge of
  `feat/pick-versions`, pending) every revision is now recorded.** This is
  the bullet every other "known traps: picks are refreshed in place"
  reference in this document points to. A game pick is rewritten in place
  by `_refresh_pick`, and a prop pick by `_refresh_prop_pick`, until its
  game starts or it is graded -- `pick_value`, `confidence`, `edge_pct`,
  `odds_at_pick`, `model_prob`, `suggested_unit_size` and (game picks only)
  `rationale_json` are all overwritten on each refresh, and `created_at`
  moves to the time of that refresh, not the pick's original creation.
  - **Before this merge, every pre-kickoff revision was simply destroyed.**
    There was no way to recover what an earlier version of a pick looked
    like -- only what `emailed_picks` happened to capture at send time (see
    "emailed picks" below), and only if the pick was ever emailed at all.
    `pick_versions` fixes this **only from the merge forward**: a pick's
    version history is genuinely complete starting from its first version
    recorded with `source` `insert` or `refresh`, not before.
  - **A backfilled version 1 is a pick's LAST pre-kickoff state, not its
    first.** `backend/scripts/backfill_pick_versions.py` writes one for
    every pick that predates the table, from that pick's CURRENT
    (last-refreshed) state at backfill time. **`first_seen_at` in
    `picks.csv` for a backfilled pick is really the last refresh time, not
    when the pick was first made** -- see `pick_versions.csv`'s own
    section above for the full caveat, including why the backfill must run
    with the scheduler stopped (stop the scheduler -> merge and deploy ->
    run `backfill --apply` -> restart), between deploy and restart, never
    against a db the new code is already live on: otherwise the
    scheduler's own first live refresh beats the backfill to writing
    version 1 (from whatever state that refresh computes, not the pick's
    true pre-merge final state), and the backfill then silently skips that
    pick forever because it now has a version.
  - **A new version is not only ever a price, side or edge change.**
    `suggested_unit_size` is tracked too, and it moves on its own: it
    follows bankroll and drawdown after grading (`kelly.py`'s sizing),
    independent of whether the model's view of the pick changed at all. A
    version whose `pick_value`/`edge_pct`/`model_prob` are identical to the
    one before it, but whose `suggested_unit_size` differs, is a real,
    correctly-recorded version -- re-sizing alone is a tracked field.
  - **Deleting a pick deletes its version history with it.**
    `pick_versions.pick_id` is `ON DELETE CASCADE`, enforced by SQLite
    itself (`PRAGMA foreign_keys=ON`). Every pick-deleting script
    (`dedupe_picks.py`, `dedupe_prop_picks.py`, `dedupe_combat_games.py`,
    the ORM delete-and-regenerate correction path) cascades cleanly and
    leaves no orphaned version rows -- but also means a deleted duplicate
    or bad pick's revision history is gone too, not preserved somewhere
    else. If a pick disappears from `picks.csv`, its rows are gone from
    `pick_versions.csv` as well; there is nothing to join.
  - **`rationale_json` (-> `factors`) is now refreshed on every flip, but
    only going forward from this same merge.** Before it, `_refresh_pick`
    updated every other tracked field on a refresh but left
    `rationale_json` untouched, so a pick that flipped sides kept the
    factors that justified the OLD side -- `factors` in `picks.csv` could
    describe the opposite side from the current `pick_value`. This is now
    built at the same call site for both insert and refresh, so no two
    code paths can disagree about what a pick's rationale contains. **This
    is user-visible, not just an export quirk**: the daily digest renders
    `pick.rationale_json` directly (`backend/digest/selector.py`), so the
    emailed rationale text for a flipped pick now explains the CURRENT
    side too, not just this export's `factors` column -- "email is
    unaffected by this merge" does not strictly hold; only `picks`' other
    columns, and everything downstream of them (grading, the send bar),
    are unaffected. **A row refreshed before this merge may still carry
    stale, pre-flip factors** -- nothing retroactively repairs
    `rationale_json` on an existing `picks` row, and `pick_versions.csv`'s
    own `factors` column for any `source='backfill'` version is subject to
    the exact same staleness, since it is read from the pick's current
    (possibly still stale) `rationale_json`. `strategy_id` (-> `strategy`)
    is a separate, still-unfixed case: it is still never refreshed, on
    either path.
- **The generator only stores a game pick with `edge_pct >= 3.0`.** Every
  team-sport strategy (`ensemble.py`, `recent_form.py`, `sport_specific.py`,
  `combat_sports.py`) reads a `min_edge` from its strategy's stored config
  and refuses to emit a pick below it (`ensemble.py:241/250`'s `_takeable`:
  `if edge < min_edge: return False`). The live `ensemble` strategy's config
  is `{"min_edge": 3, ...}` (documented directly in
  `plans/007-measure-calibration-before-retuning-min-edge.md:152-153`, and
  matching the same `3.0` the `combat_sports` strategy is seeded with in
  `backend/database.py:471`). On 2026-09-30 the live db's minimum
  `edge_pct` was at least 3.0 in every sport (exactly 3.0 in mlb/nba/ncaaf, up to 17.2 in boxing), consistent with this floor. **The edge
  distribution in this export is truncated at +3.0 by construction -- there
  is no lower tail below it to find, and its absence is not a finding.**
  (`value_only.py`'s own `min_edge` default is 10.0, higher still, for
  whichever sports use that strategy instead.)
- **284 picks predate 2026-09-17** (2026-03-14 through 2026-05-24, before
  collection effectively began -- see the next bullet). None of them have
  `model_prob` or `suggested_unit_size` populated. The "collection starts
  2026-09-17" bullet below explains why these rows exist at all; it does
  not by itself explain why they are missing these two columns specifically
  -- they simply predate the features that would have populated them.
- **Every totals (`over_under`) pick with a non-null `model_prob` has
  `model_prob = 1.0`** (46 of 46, as measured against the live db
  2026-09-30). This is not a coincidence: `ensemble.py`'s totals section
  (lines ~354-376) documents its own history in a lengthy comment --
  `_predicted_total` depends on `offensive_rating`, `defensive_rating` and
  `pace`, none of which any collector in this repo ever supplied, so all
  three silently fell back to 100.0 and `_predicted_total` came out to
  exactly 200.0 for every game in every sport, regardless of the real line.
  Against real market totals (6.5-20.5 mlb, 36.5-76.5 ncaaf, 130-172.5
  ncaab, 208.5-255.5 nba), that one constant decided the side by itself and
  saturated the CDF, giving `model_prob` exactly 1.0 and `edge_pct` exactly
  50.0 on every one of these picks. Graded, the set came out at 52% over 50
  picks at -110 -- a coin flip paying the vig, not a signal. `ensemble.py`'s
  current code gates all totals picks on `TOTALS_VALIDATED_SPORTS`, which is
  presently empty, so no NEW totals picks are generated this way -- but the
  46 already-stored rows remain in this export and would read as a striking
  "pattern" (`model_prob` always exactly 1.0) to an analyst who has not seen
  this comment.
- **Every `spread` and `over_under` pick is priced at exactly -110, and
  none exist after 2026-09-20.** All 241 of them (241 of 241 as measured
  against the live db 2026-09-30, dated 2026-03-15 through 2026-09-20) have
  `odds_at_pick = -110`. That is `STANDARD_JUICE`, the fallback used when no
  book's spread or total price was on record, not a quoted price. The
  collector only began storing those prices on 2026-09-21 20:35 UTC. So on
  these rows the price, and anything computed from it (the payout on a win,
  `clv_price_pp`), carries no information; line points (`pick_value`,
  `line_at_close`, `clv_line_pts`) are the only real market signal. Spread
  picks were gated off per sport at 2026-09-20 21:25 UTC (commit 69c13cf,
  `SPREAD_VALIDATED_SPORTS`, currently empty), and totals by
  `TOTALS_VALIDATED_SPORTS` (see the previous bullet), so no new rows of
  either type have been generated since. If a sport is re-enabled, its new
  spread/total rows will carry real prices, and `odds_at_close` will need to
  become a real closing price too (see "What CLV means here", caveat 1).
  That is a regime change to split on.
- **Collection effectively starts 2026-09-17.** Verified directly in code:
  `backend/scripts/backfill_date_range.py` states "Game collection began on
  2026-09-17, and nothing ever fetched what came [before]". Earlier rows
  exist only where backfilled (e.g. NFL history imported from nflverse --
  see `backend/analysis/epa_ratings.py` and
  `backend/scripts/backfill_line_snapshots.py`, which both reference
  nflverse imports). **Backfilled `captured_at` values in `line_history.csv`
  cannot answer "when did the line move" questions** -- they reflect when
  the backfill ran or an approximated import timestamp, not a real
  observation time.
- **The `odds` table is overwritten in place; `line_snapshots` is the only
  price history.** Verified directly in code:
  `backend/models.py`'s `LineSnapshot` docstring states "`Odds` holds the
  CURRENT price and is upserted in place, so every quote this project has
  seen except the latest is discarded." This is why `line_history.csv`
  comes from `line_snapshots`, not `odds`.
- **Picks on the same game are correlated.** Not independently re-verified
  this session beyond the structural fact that multiple `picks` rows share
  one `game_id` (visible directly in the schema) -- the statistical
  consequence (effective sample size well below row count) follows from
  that structure. Aggregate to one row per game before treating a count of
  picks as a sample size.
- **Kelly sizing returned a constant 0.5 until 2026-09-20 -- but that value
  never reached this export.** Verified via git history: commits `92e5f50`
  ("feat(kelly): wire the three adjusters, and persist the stake they
  produce") and `cdfd160` ("fix(kelly): let the sizer decline a bet, and
  stop the backtester undoing it") are both dated 2026-09-20. The constant
  `0.5` was never itself *persisted* to `suggested_unit_size`, though: every
  pick before 2026-09-21 in the live db has `suggested_unit_size` **NULL**,
  not `0.5`. `picks.csv` will show a blank for these rows, not a `0.5` value
  -- do not go looking for a literal 0.5 in the column as a regime marker;
  use `created_at` against the date instead.
- **Prop picks were re-inserted about 18x per run until a dedupe on
  2026-09-28.** The dedupe date is confirmed via git history: `d8191aa`
  ("fix(props): one pick per player-market, refreshed in place, never
  re-added") and `9c5f4ef` ("fix(props): remove the duplicate prop picks
  stored before d8191aa") are both dated 2026-09-28. The "about 18x"
  multiplier itself is carried over from project memory
  (`sports-picks-prop-duplicate-picks`) and was not independently
  re-counted against the database this session.
- **MMA duplicate bouts were merged 2026-09-20.** Verified via git history:
  commits `91fdd7a` ("fix(mma): merge the date splits, void the bouts that
  moved") and `b0d4f1b` ("fix(mma): one bout stored twice was one bout
  counted twice") are both dated 2026-09-20.
- **Stuck boxing bouts were voided 2026-09-20 (as push).** The date and the
  general action are verified via git history (`97ca42f`, "feat(boxing):
  void the bouts no source can ever settle", dated 2026-09-20). **The
  count needs a correction**: "110" (as commonly quoted) counts stuck
  BOUTS, not picks -- only 36 boxing PICKS are actually pushes as a result.
  If you are counting rows in `picks.csv`, expect 36, not 110.
- **MMA "Over 0" totals were voided 2026-09-30 (as push), phantom wins from
  the team model grading 0/1 bout scores.** The date and the mechanism are
  verified via git history and this repository's own recent commits:
  `b63c506` ("feat(scripts): void every combat spread/total already
  stored; one COMBAT_SPORTS") and `72e92cf` ("fix(combat): grader.grade_pick
  voids a combat spread/total as push") are both dated 2026-09-30, and
  `backend/scripts/audit_combat_grading.py`'s own `OU_ON_COMBAT` flag
  documents the same mechanism ("Our 'score' for a bout is a 0/1 pair ...
  so `home_score + away_score` is always 1 and `grade_pick` settles any
  'Over x' with x < 1 as a win"). **The count needs a correction**: there
  are 14 MMA `over_under` pushes from this voiding, not 13 as sometimes
  quoted -- 14 is the number to expect if you count rows.
- **Combat sports have been picked by `combat_sports` instead of
  `ensemble` since 2026-09-30.** Verified via git history: `a2c6011`
  ("fix(combat): route combat games to combat_sports, never ensemble") is
  dated 2026-09-30, and this branch itself descends from the merge commit
  (`1c81e33`) that includes this fix.
- **All dates in this dictionary are UTC (see the note at the top of this
  file). Stars (`confidence`) were hidden from users 2026-09-30 UTC, and
  the 3-point shrunk-edge send bar was both introduced AND removed within
  that same UTC day.** Verified precisely via git history, resolving an
  apparent conflict between the brief and `docs/review-remediation.md`'s
  own (Pacific-dated) section headers:
  - `a1d1949` (introduces the 3-point send bar), `867af4f` (hides stars in
    the frontend) and `d677582` (the digest-side stars/wording fix) were
    all authored late on 2026-09-29 **Pacific time** (`23:06:29`,
    `23:06:45` and `23:49:26` respectively, `-07:00`) -- which is
    **2026-09-30, 06:06-06:49 UTC**. The merge, `f7a3bee`, is
    `2026-09-30T00:06:47-07:00` = **2026-09-30T07:06:47Z**.
  - `docs/review-remediation.md`'s section header says "2026-09-29", which
    is correct in the *author's local time* but not in UTC -- the
    convention this export and dictionary use throughout. Since every
    timestamp column in the CSVs is UTC, **2026-09-30** is the date that
    actually matches the data, and is used here instead of the doc
    header's Pacific date.
  - This branch's own two commits (`de0e349`, `0ab3fd6`, removing the
    3-point bar and adding this export) are also dated 2026-09-30 UTC
    (`17:06` UTC). The 3-point bar went live 2026-09-30 ~07:06Z (merge
    `f7a3bee`) and stays in force until the first digest after this branch
    merges -- provisionally 2026-10-01 (see the next bullet).
  - **As of this branch, the 3-point bar removal is NOT YET merged to
    master or deployed.** "Every priced game pick is emailed" is only true
    starting with the first digest run AFTER this branch merges and the
    scheduler restarts -- see the note under "Emailed picks have only been
    recorded since 2026-09-28" below for the equivalent caveat on
    `emailed_picks`. Provisionally: if merged promptly, that would be the
    morning of 2026-10-01; treat that date as pending confirmation, not a
    fact already in the data.
- **Emailed picks have only been recorded since 2026-09-28.** Verified via
  git history: `697d6fc` ("feat(digest): record the picks each email sent,
  and grade them as sent") is dated 2026-09-28 (23:07 UTC).
- **Football props are tracking picks from 2026-10-04 (`tracking_only`).**
  `player_stats` "season_avg" rows for nfl and ncaaf hold season TOTALS,
  which the prop analyzer read as per-game, so every football prop
  projection before the fix is inflated (Josh Allen "average" 786 pass
  yards after 3 games). 451 graded football props from 09-24..10-02
  predicted 0.818 and hit 0.494, flat across confidence. **Treat every
  football prop pick before the fix as having no model signal.** From
  2026-10-04 they are generated and graded but unpublished, except those
  already emailed, which stay published as sent.

- **NFL yardage props get an opponent-defense adjustment from the evening
  of 2026-10-04.** Before it, NFL props had **no** matchup input: the
  analyzer scaled by the opponent's `defensive_rating`, a basketball stat
  never computed for football. From then, pass and receiving yards
  projections move by half the opponent's pass-defense factor and rush
  yards by half its run-defense factor (`backend/analysis/football_defense.py`;
  factor = season-to-date yards allowed per game over the league's, shrunk
  with 3 league-average games; about 0.83-1.20 in week 5). Anytime-TD and
  ncaaf props are unchanged. The half weight is measured, not chosen
  (`backend/scripts/prop_matchup_experiment.py`, 2022-2026: c = +0.50 pass,
  +0.52 rush, +0.43 receiving, all p < 1e-7). The same run found no sign the
  books miss the matchup (line coefficients <= 0 on 27 games, wide
  intervals), so **expect better projections, not proven edge**. Compare
  football prop calibration before and after this date, not pooled.

- **NFL rushing and receiving props get a teammate-injury adjustment from
  the first prop run after the merge of 2026-10-06 (and the scheduler
  restart that follows).** Before it, no prop knew who was missing. From
  then, if a team's season-to-date rushing-yards leader is absent, its other
  players' rush-yards projections are multiplied by 1.26. If its
  receiving-yards leader is absent, other receivers get x1.17. If its
  passing-yards leader is absent, receivers get a further -0.07
  (`backend/analysis/football_injuries.py`). "Absent" means ESPN's team
  roster at run time lists an injury status other than Questionable or
  milder (Out, Doubtful, Injured Reserve...), or the player is no longer on
  the roster. Passing-yards, anytime-TD and ncaaf props are unchanged. The
  multipliers are measured (`backend/scripts/injury_experiment.py`, nflverse
  2022-2026, yards-based leaders, clustered by game). Prop lines were not
  tested, so **expect better projections, not proven edge**. The adjustment
  is not stored on the pick. It is reproducible from the game date, the
  player game logs and the roster, but the roster at run time is not kept.
  A missing QB was found fully priced in the closing spread and total, so
  game picks are unchanged.

- **Rain-Under rule picks: `strategy = weather_rain_under`, from the first
  NFL window after the 2026-10-06 weather merge.** These are NOT the
  model's picks. A rule stores "Under <consensus total>" for any outdoor NFL
  game (home stadium open-air, not a neutral site) whose forecast at the
  window shows >= 1.0 mm of precipitation over the kickoff hour and the two
  after. The line and price are the same consensus the model uses. Each is
  `tracking_only`: never emailed or published, graded like any total. They
  differ from model picks in three ways:
  - `edge_pct` is 0.0. This breaks the "game-pick edge_pct is never below
    3.0" pattern.
  - `model_prob` is NULL.
  - `rationale_json` holds `{"rule": "rain_under", "weather_id", "precip_mm",
    "wind_mph", "temp_f"}`. `weather_id` points into `game_weather`.

  **Always split or filter on strategy:** pooled with the model's tracking
  totals, they would misstate both records. A pick whose forecast dries out
  before kickoff is withdrawn (`withdrawn_at`), like any pick.
  Backtest: archived forecasts 2022-2026, Under 26-8 on 34 games
  (`backend/scripts/weather_forecast_backtest.py`). That backtest used the
  games the idea came from, so this record is the out-of-sample test.

- **NBA points, rebounds and assists props get a star-out adjustment from
  the 2026-10-07 merge** (`backend/analysis/nba_injuries.py`). When the
  team's star is out tonight, each teammate's projection is multiplied by:
  - points x1.124
  - rebounds x1.064
  - assists x1.079

  The star is the team's points-per-game leader this season with at least
  10 games played. "Out" means ESPN's roster lists him unable to play, and
  he played in one of the team's last 5 games. Measured on 2025-26
  (`backend/scripts/nba_injury_experiment.py`).
  - **Inactive for each team's first ~10 games of 2026-27**, because no one
    qualifies as the star yet.
  - Status is read at the window run about 2 h before tip, so scratches
    announced later are missed.
  - Not stored on the pick.

- **`prop_snapshots` (new table, 2026-10-07): the append-only prop price
  history.** `player_props` keeps only each prop's latest price. This table
  keeps every price, one row per change, per (game, book, market, player,
  outcome), the same rule as `line_snapshots`.
  - An unchanged price extends `last_seen_at` instead of adding a row.
  - A row with `odds` NULL is a **pull**: the book still quoted that market
    in the fetch but no longer that player. Late scratches usually look
    like this.
  - Only main lines are recorded (`main_lines`), as in `player_props`.
  - Nothing exists before 2026-10-07.

- **NBA prop picks get a second, late run from the 2026-10-07 merge.**
  Each NBA window also re-fetches its prop lines 45 minutes before its first
  tip and re-runs the prop pipeline (`scheduler._run_late_props`), so the
  star-out adjustment reads the final injury report. As a result:
  - an NBA prop pick's `created_at`, price and value can come from about
    45 minutes before tip rather than about 2 hours (`pick_versions` keeps
    both);
  - a pick that stops qualifying at the late run is withdrawn;
  - picks on games already underway are never touched.

- **NBA player game logs: 1,038 duplicated rows deleted on 2026-10-07**
  (`backend/scripts/dedupe_game_logs.py`; backup
  `sports_picks.backup-20261006-234715-pre-gamelog-dedupe.db`). The
  2026-09-19 backfill had written some 2025-26 games twice: an identical
  stat line dated the day after the real game, 96 team-games in all.
  - The later copy was deleted when either `games` showed only the earlier
    date was played, or the row came from the 2026-09-19 backfill. Every
    one of the 896 cases `games` could decide had that pattern.
  - In 141 back-to-back cases the copy sat on the date of the team's real
    second game. That second game is NOT missing: it is stored one day
    late. For example, the Knicks' 2026-02-11 game is on 2026-02-12.
    (Corrected the same day; this note first said it was missing.)
  - **NBA game_log dates are not all the game's date.** About 5% of
    team-games are filed one day late; the older backfill dated some games
    by UTC. Match logs to games on (team, date) with care, especially
    across back-to-backs.
  - 10 team-sides of final 2025-26 games (Oct-Mar) have no lines on any
    nearby date. They were left unfilled: refetching them recreated
    duplicates, because of the date drift above.
  - 32 similar college basketball pairs fit neither rule and were left.
  - Before this date, NBA prop projections could count a duplicated game
    twice in a player's last-five-game form. Prop picks made before the
    cleanup used the duplicated data.

- **`game_weather` (new table, 2026-10-06): append-only Open-Meteo forecasts
  for outdoor NFL games.** One row per capture (`captured_at`), about 2h
  before kickoff, never overwritten. Covers only open-air home stadiums;
  domes, retractable roofs and neutral sites have no rows. `precip_mm` and
  `wind_mph` cover kickoff hour + 2; `temp_f` is the kickoff hour.

- **NFL passing and receiving props get a weather adjustment from the
  same merge.** Multipliers, applied from the latest `game_weather` row:
  - passing yards: x0.85 if the forecast is windy (>= 15 mph mean), x0.86
    if wet; both apply when both hold (x0.71);
  - receiving yards: x0.85 windy, x0.90 wet;
  - rushing: unchanged (not significant).

  Measured on archived forecasts, not observed weather, so they match what
  production sees (`backend/analysis/football_weather.py`).

- **Stored pre-game Elo was stale for some games until it was repaired
  (merge after 2026-10-04).** `elo_history` rows written before a backfill or
  a restored/merged game inserted games earlier in the history kept the
  rating computed without them. Measured 2026-10-04 against a clean replay:
  nfl 62 rows (2026 weeks 1-2, all at the 1500 seed, median 103 and up to
  272 points off), nba 1,467 (Dec 2025-Jun 2026, median under 1, up to 48),
  mlb 192 (May-Sep 2026, median 11, up to 55), ncaab 62, ncaaf none. The
  daily replay now corrects such rows on every run. **Treat `model_prob` on
  nfl picks from 2026-09-09..09-20 as computed from Elo at or near the 1500
  seed** (inferred: those games' stored rows hold the seed, so the 2022-25
  history was not yet in place when they were written). From 09-21 every
  nfl and mlb team's latest row matches the replay; nba and ncaab latest
  rows were stale until the repair.

- **NBA Elo regresses halfway to 1500 between seasons from 2026-27.** Each
  team's rating at its first game of a season keeps half its distance from
  1500 (`sport_constants.ELO_SEASON_CARRY`). Measured on 2023-24..2025-26:
  the first 15 games of a season are predicted better (rescaled Brier
  0.2110 -> 0.2066, AUC 0.714 -> 0.726, best in each season on its own).
  NFL and every other sport carry ratings over unchanged; for NFL a reset
  was measured and made weeks 1-4 worse. NBA's stored history starts in
  2025-26, so no stored row changes; it applies from opening night
  2026-10-20.

- **Prop `edge_pct` changed definition on 2026-10-04; never pool the two.**
  Before: `(model_prob - 0.5) * 200` -- price-blind, on double the scale of
  a probability difference, so a -300 prop scored the same as a -110 one at
  the same probability. (An earlier version of this entry called it a
  stat-unit gap; it was not.) From 2026-10-04 it is the edge over the
  price's break-even, `odds_utils.value_edge`, the same definition game
  picks have used since 2026-10-03, and `model_prob` is stored for props.
  The star thresholds (5/7/10/15/20) were kept, so a star means a much
  higher probability than before. Measured on 09-27..10-03 props: 85% would
  still be 5-star -- prop probabilities are overconfident (5 stars at -110
  implies >=72%; those picks won about half), so stars carry little
  information for props in either regime. A raw
  stored prop `pick_value` ends in the API market key when `market_label`
  does not recognize it -- e.g. `"Zach Ertz Over 10.5 player_reception_yds"`
  (`backend/digest/render.py:94`, `backend/tests/test_prop_market_labels.py`)
  -- which `pick_label` resolves to `"Zach Ertz Over 10.5 Receiving Yards"`.
  **Never compare `edge_pct` across `pick_type` values.**
- **MLB props and anytime-TD props cannot be graded reliably; excluded
  from emails.** Partially verified: `backend/analysis/prop_markets.py`
  explicitly documents that MLB props (`batter_hits`, `batter_home_runs`,
  `batter_total_bases`, `pitcher_strikeouts`) have no `PlayerStat` column
  to grade against and are "fetched and stored and can then be neither
  analysed nor graded" -- this is directly confirmed in code.
  `player_anytime_td` **is** handled by `grade_prop_pick` in
  `backend/pipeline/grader.py` when the stored `pick_value` is in
  "Over/Under N" form, but project memory
  (`sports-picks-prop-outcome-shapes`) states some anytime-TD rows are
  stored as a bare `Yes`/`No` outcome with a null line, which the grader's
  `Over/Under` regex cannot parse -- making those specific rows
  ungradeable in practice. This export's `side` column surfaces that shape
  directly (`Yes`/`No` instead of `Over`/`Under`) so it can be checked
  per-row rather than assumed.
- **The model's blend weight against the market was measured at 0.00 for
  NFL (1,184 games) and MLB (127) on 2026-09-29.** Verified directly:
  `config.yaml`'s `digest.send_bar.blend_weight` and
  `docs/review-remediation.md` both record this measurement, including the
  Brier scores (market alone beats model alone: 0.2109 vs 0.2258 NFL;
  0.2216 vs 0.2438 MLB). This is an **in-sample** bound, not a held-out
  result -- see `docs/review-remediation.md` for why that distinction
  matters before treating either sport's edges as validated.
- **An MLB pitcher score of 0.5 means both "league average" and
  "unknown".** Verified directly in code:
  `backend/pipeline/scheduler.py` states "`pitcher_skill_score` returns 0.5
  both for a [league-average pitcher and an unknown one]", and
  `backend/analysis/pitcher.py` documents ERA 4.00 -> ~0.50 as league
  average. There is no way to tell the two apart from `model_prob` alone
  for an MLB pick influenced by pitcher skill.
- **ncaab `neutral_site`: the great majority of games are neutral, which
  explains the home rate.** The count has moved as more games were
  collected: earlier project memory recorded 71 of 72; the live db on
  2026-09-30 shows **73 of 86**. Treat any specific count as a snapshot of
  a growing table, not a fixed fact -- re-count against a current export
  rather than citing either number going forward. The mechanism
  (`games.neutral_site`, sourced from ESPN's `competitions[0].neutralSite`)
  is confirmed to exist in `backend/models.py`.
- **Team `abbreviation` holds display names for some sports.** Not
  independently re-verified this session beyond confirming the column
  exists (`teams.abbreviation`, `nullable=False`). Carried over from
  project memory (`sports-picks-team-identity`).
- **MMA/UFC is quarantined from headline records pending a larger sample.**
  Not independently re-verified this session (would require re-running the
  same record computation against a live or snapshotted db, which this
  pass did not do). Carried over from project memory
  (`sports-picks-review-remediation-decisions` /
  `sports-picks-mma-duplicate-bouts`): moneyline record 14-25, +19.09u on
  39 picks over 3 fight nights, described there as real but thin and
  driven by one +1000 winner.

## How to not fool yourself

- **Group by game for tests.** Multiple picks share a `game_id`; treat
  `game_id`, not `pick_id`, as the unit of an independent observation when
  testing anything about the model's edge or calibration.
- **Compare against the market, not against 50%.** A 55% "win rate" on
  picks whose market price implied 58% is a loss, not an edge. Use
  `implied_prob_raw` / `market_prob_novig` as the baseline, not a flat coin
  flip.
- **Label regimes by the change dates above.** Kelly sizing, prop dedupe,
  MMA/boxing voids, the combat-vs-ensemble routing switch, the send-bar
  change, and the 2026-10-03 return of spreads/totals as `tracking_only`
  picks each split this data into a "before" that should not be pooled
  with an "after" without accounting for the change.
- **Use ROI and CLV, not win %.** Win rate ignores price -- a -110 win and
  a +300 win are not the same size. Units won (`payout`) and CLV
  (`clv_price_pp` / `clv_line_pts` -- never pool the two) are the
  measurements this project otherwise relies on; see
  `backend/analysis/clv_report.py` for why CLV in particular resolves
  faster than waiting for results.

## Paper bets (`paper_picks`, `parlays`) -- not exported

The export leaves these tables out (see the top of this file), but the
owner's analysis can read them from the database directly.

- **From 2026-10-09 (sportsbook phase 6), `result` can be `cashed_out`.**
  A player sold an open bet back before kickoff for
  `stake x placed decimal price x no-vig chance now x 0.95`, rounded down
  to the cent. `payout` is that offer minus the stake. It is usually
  negative even on a bet that was going well, because the 5% margin and
  the fair price leave less than the stake plus the winnings.
  - On a straight bet `graded_at` is the cash-out time.
  - A parlay has no `graded_at` column; its cash-out time is the
    `cashed_out` row in `activity_feed` (`$.bet_key = "parlay-<id>"`).
  - A cashed-out parlay's legs are still graded later, at stake 0, so a leg
    shows `win`/`loss` under a `cashed_out` parlay. That is expected and
    moves no money.
  - In every figure, a cash out is a settled bet: its profit is in ROI and
    its stake in money staked. It is neither a win nor a loss, and it is
    not in break-even.
  - Before this date no row has the value.

## See also

- `backend/scripts/export_picks.py` -- the export script itself.
- `backend/pipeline/pick_versions.py` -- the append-on-change helper both
  write paths call to populate `pick_versions`.
- `backend/scripts/backfill_pick_versions.py` -- the one-time backfill for
  picks stored before `pick_versions` existed.
- `backend/analysis/clv_report.py` -- the CLV methodology this export
  reuses, and why CLV is trusted over raw results in this project.
- `docs/review-remediation.md` -- the dated history of send-bar, edge-gate,
  and combat-grading decisions referenced throughout this file.

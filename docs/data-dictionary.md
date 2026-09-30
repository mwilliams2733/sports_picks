# Data dictionary: pick export

This documents the CSV export produced by
`python -m backend.scripts.export_picks --db <path> --out <dir> [--since YYYY-MM-DD] [--sport nfl ...]`
(see the module docstring in `backend/scripts/export_picks.py` for the exact
invocation). It is written for a reader who has never seen this project.

The exporter is read-only (opens the database `mode=ro`) and excludes
`paper_picks` and `user_profiles` -- both are private data about the owner
and paper-trading users, not data about the model, and are not needed for
model analysis.

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
| `created_at` | When the pick was written, UTC, ISO format. |
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
| `pick_value` | The raw stored label, e.g. `"HOME ML"`, `"AWAY -1.5"`, `"Over 8.5"`, or for a prop, a player-and-line string like `"Mahomes Over 275.5 Pass Yards"`. |
| `pick_label` | A human-readable label with HOME/AWAY resolved to the real team name (e.g. `"Pittsburgh Pirates to win"`), and a prop's trailing market key (if any) swapped for its display label. This reuses the same resolvers the daily email and the picks API already use (`backend/digest/render.py`'s `_selection_label` / `_prop_label`) -- there is deliberately no third, independent label function. |
| `side` | `HOME` / `AWAY` / `Over` / `Under` for a game pick; for a prop, `Over` / `Under` when present in the pick value, or `Yes` / `No` for a binary outcome (see "prop outcome shapes" below); blank if unparseable. |
| `line` | The numeric line parsed out of `pick_value` (e.g. `-1.5`, `8.5`), blank for a moneyline pick (there is no line) or when nothing parses. |
| `odds_at_pick` | The American price stored at pick time. Null for a small number of legacy rows. |
| `implied_prob_raw` | What `odds_at_pick` implies, vig included (not de-vigged). Computed with `backend.analysis.odds_utils.american_to_implied_prob`. Blank if `odds_at_pick` is null or not a valid American price. |
| `model_prob` | The model's own win probability for the pick, where stored. Nullable, especially for props and older rows. |
| `edge_pct` | The stored edge in percentage points. **See "Known traps": a prop's `edge_pct` is on a completely different, incompatible scale from a game pick's.** |
| `market_prob_novig` | See below. |
| `confidence` | The stored star rating (1-5), still recorded even though stars are hidden from users in the email and frontend (see "known traps"). |
| `suggested_unit_size` | Kelly stake in units, where one unit is 1% of bankroll. Null for picks made before this was persisted (not a computed 0); 0.0 means the sizer looked at the bet and declined it. **See "known traps": this returned a constant 0.5 for all picks before 2026-09-20.** |
| `factors` | The rationale's factor codes, `;`-joined as `code:side:strength`, e.g. `rating_gap:home:strong;pitcher_edge:away:moderate`. Blank if `rationale_json` is missing or malformed. |
| `prop_player` / `prop_market` | Only populated for `pick_type == "prop"`: the player's name and the market key (e.g. `player_pass_yds`), not the display label. |
| `result` | `win` / `loss` / `push`, or blank if the pick has not been graded yet. A push means the bet is void -- see "How grading works". |
| `payout` | Net units from `backend.pipeline.grader.payout_for(result, odds_at_pick)`. Blank if ungraded. |
| `odds_at_close` / `line_at_close` | The price/line at game close, captured for CLV. Blank if never captured (see "line snapshots" below). |
| `clv` | Closing line value for this pick. See "What CLV means here" below -- **do not treat this as one consistent unit across rows.** |
| `odds_reconstructed` | `True` when `odds_at_pick` was rebuilt afterwards from surviving book rows rather than recorded live (`backend/scripts/repair_invalid_odds.py`). Treat these as an approximation in any ROI/CLV figure. |
| `emailed` | `True` if this pick appears at least once in `emailed_picks`. |
| `emailed_odds` / `emailed_at` | The price and UTC timestamp of the **first** time this pick was emailed, where `emailed` is `True`. A pick can in principle be emailed on more than one digest date (the uniqueness constraint on `emailed_picks` is per `(digest_date, pick_id)`, not per pick); the earliest row is used and later re-sends are not separately represented in this export. Blank before 2026-09-28, when `emailed_picks` recording began (nothing before that date was recorded, regardless of whether it was actually emailed). |

### `market_prob_novig`

This is the fair (no-vig) market probability the pick's `edge_pct` was
measured against -- not an independently computed number:

- **moneyline**: `model_prob - edge_pct / 100`. This is exact by
  construction (`edge_pct = (model_prob - market_prob) * 100`, see
  `backend/analysis/variants/ensemble.py`), so it is arithmetic on the two
  stored columns, not a re-derivation from raw odds.
- **spread / over_under**: always `0.5`. The model does not store a
  de-vigged market win probability for a line pick; `edge_pct` for these is
  measured against a flat 0.5 fair-coin value, and `0.5` here documents that
  convention rather than computing anything.
- **prop**: blank. Props are never de-vigged against a market probability
  at all (see "known traps").

## `line_history.csv` columns

One row per `line_snapshots` row -- the append-only price series (see
"known traps: odds is overwritten" below). Columns: `game_id`, `sport`,
`home_team`, `away_team`, `game_date` (for joining back to `picks.csv`),
every price field (`bookmaker`, `moneyline_home`, `moneyline_away`,
`spread_home`, `spread_away`, `over_under`, `spread_home_price`,
`spread_away_price`, `over_price`, `under_price`), and `captured_at` /
`last_seen_at`. A row is written only when a price DIFFERS from the latest
row for the same `(game_id, bookmaker)`; an unchanged re-observation extends
`last_seen_at` on the existing row rather than adding a new one.

## `manifest.txt`

Records when the export ran, the db path, the git commit (`HEAD`) the
export script was run from, row counts per file and per sport, the
`--since`/`--sport` filters applied, and a pointer back to this file.

## How grading works

- A pick is graded once its game finishes and `backend.pipeline.grader`
  processes it; until then `result`/`payout` are blank.
- `payout_for(result, odds_at_pick)` in `backend/pipeline/grader.py` is the
  one place payout is computed from a result and a price -- this export does
  not recompute it.
- **A push is a void bet, not a loss.** It should be excluded from any
  win-rate denominator (wins / (wins + losses), never wins /
  (wins + losses + pushes)) and its payout is 0.

## What CLV means here

CLV ("closing line value") compares the price a pick was taken at to the
price the market closed at. It is computed by
`backend.analysis.odds_utils.compute_pick_clv` -- this export calls that
function directly and writes back whichever of its two return values is not
`None`; it does not recompute CLV independently.

**The unit is not the same for every row, and the two must never be
pooled or averaged together:**

- **moneyline** `clv` is in **implied-probability percentage points**
  (closing implied prob minus pick-time implied prob). Positive means the
  bettor's price beat the close.
- **spread / over_under** `clv` is in **line points** (how many points the
  bettor beat the close by). Positive means a better line at pick time.
- **prop** `clv` is always blank -- CLV is not computed for props.

`clv` is blank whenever there is no closing price/line on record for that
pick (absent is not the same as zero -- counting a missing close as 0 CLV
would bias every average toward "no edge").

## Known traps

Each bullet below was checked against the code, docs, or git history in
this repository as of 2026-09-30 (commit `1c81e33` and this branch's work
on top of it). Where a check could not be made from the code alone, that is
stated explicitly rather than left silent.

- **Collection effectively starts 2026-09-17.** Verified directly in code:
  `backend/scripts/backfill_date_range.py` states "Game collection began on
  2026-09-17, and nothing ever fetched what came [before]", and
  `backend/pipeline/scheduler.py` references the same date. Earlier rows
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
- **Kelly sizing returned a constant 0.5 until 2026-09-20.** Verified via
  git history: commits `92e5f50` ("feat(kelly): wire the three adjusters,
  and persist the stake they produce") and `cdfd160` ("fix(kelly): let the
  sizer decline a bet, and stop the backtester undoing it") are both dated
  2026-09-20 in this repository's commit history.
- **Prop picks were re-inserted about 18x per run until a dedupe on
  2026-09-28.** Not independently re-verified against git history this
  session (no commit matching a 2026-09-28 prop dedupe date was located in
  `git log`; the closest matches, `5946578` "fix(props): dedup prop picks
  across bookmakers, keeping best price" and `ab17acc` "fix(props): break
  dedup ties deterministically by bookmaker", were found but not dated in
  this check). Carried over from project memory
  (`sports-picks-prop-duplicate-picks`); treat the exact date/multiplier as
  unverified in this pass and re-check before relying on it.
- **MMA duplicate bouts were merged 2026-09-20.** Verified via git history:
  commits `91fdd7a` ("fix(mma): merge the date splits, void the bouts that
  moved") and `b0d4f1b` ("fix(mma): one bout stored twice was one bout
  counted twice") are both dated 2026-09-20.
- **110 stuck boxing bouts were voided 2026-09-20 (as push).** The date and
  the general action are verified via git history (`97ca42f`,
  "feat(boxing): void the bouts no source can ever settle", dated
  2026-09-20). The specific count (110) was not independently re-counted
  against the database this session -- treat it as unverified in this pass.
- **13 MMA "Over 0" totals were voided 2026-09-30 (as push), phantom wins
  from the team model grading 0/1 bout scores.** The date and the
  mechanism are verified via git history and this repository's own recent
  commits: `b63c506` ("feat(scripts): void every combat spread/total
  already stored; one COMBAT_SPORTS") and `72e92cf` ("fix(combat):
  grader.grade_pick voids a combat spread/total as push") are both dated
  2026-09-30, and `backend/scripts/audit_combat_grading.py`'s own
  `OU_ON_COMBAT` flag documents the same mechanism ("Our 'score' for a bout
  is a 0/1 pair ... so `home_score + away_score` is always 1 and
  `grade_pick` settles any 'Over x' with x < 1 as a win"). The specific
  count (13) was not independently re-counted this session.
- **Combat sports have been picked by `combat_sports` instead of
  `ensemble` since 2026-09-30.** Verified via git history: `a2c6011`
  ("fix(combat): route combat games to combat_sports, never ensemble") is
  dated 2026-09-30, and this branch itself descends from the merge commit
  (`1c81e33`) that includes this fix.
- **Stars (`confidence`) were hidden from users 2026-09-30, but are still
  stored.** Partially verified, with a discrepancy: `confidence` being
  stored but not rendered is directly confirmed in code
  (`backend/digest/render.py` never emits star glyphs, and
  `frontend/src/lib/display.ts`'s `SHOW_STARS` is `false` per
  `docs/review-remediation.md`). **The date does not match the brief**:
  `docs/review-remediation.md`'s own section header dates this change
  2026-09-29 ("## 2026-09-29 -- frozen send bar; stars hidden"), not
  2026-09-30. This dictionary uses the documented date, **2026-09-29**, as
  the more directly verifiable of the two.
- **The email send bar changed 2026-09-29 / 2026-09-30.** Verified: the
  3-point shrunk-edge bar was introduced 2026-09-29
  (`docs/review-remediation.md`) and removed 2026-09-30 by this same branch
  of work (see the new section appended to that file, and
  `backend/digest/selector.py`'s `_validate_send_bar`). Since 2026-09-30,
  every priced game pick is emailed with its raw edge shown, not gated.
- **Emailed picks have only been recorded since 2026-09-28.** Not
  independently re-verified against a specific commit this session; the
  `emailed_picks` table and `backend/digest/record.py` exist and are used
  by the digest job, but no commit pinning the exact start date was
  checked. Carried over from project memory
  (`sports-picks-emailed-picks`) -- treat the exact date as unverified in
  this pass if it matters for an analysis.
- **Prop `edge_pct` is NOT a probability difference.** Verified by code
  intent: `backend/digest/render.py`'s props-loop comment states explicitly
  that a prop's edge is price-blind and "not comparable to a game pick's
  de-vigged edge", which is also why the daily email never shows a prop's
  edge (see this branch's own change removing edge display for props while
  adding it for game picks). It is a stat-unit gap (e.g. projected yards
  minus line) stored in the same numeric column as a game pick's
  percentage-point edge. **Never compare `edge_pct` across `pick_type`
  values.**
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
- **ncaab `neutral_site`: 71 of 72 games are neutral, which explains the
  home rate.** Not independently re-counted against the database this
  session. Carried over from project memory
  (`sports-picks-neutral-site`); the mechanism (`games.neutral_site`,
  sourced from ESPN's `competitions[0].neutralSite`) is confirmed to exist
  in `backend/models.py`, but the specific 71/72 count was not re-verified.
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
  MMA/boxing voids, the combat-vs-ensemble routing switch, and the send-bar
  change each split this data into a "before" that should not be pooled
  with an "after" without accounting for the change.
- **Use ROI and CLV, not win %.** Win rate ignores price -- a -110 win and
  a +300 win are not the same size. Units won (`payout`) and CLV
  (`clv`, mind the per-market unit) are the measurements this project
  otherwise relies on; see `backend/analysis/clv_report.py` for why CLV in
  particular resolves faster than waiting for results.

## See also

- `backend/scripts/export_picks.py` -- the export script itself.
- `backend/analysis/clv_report.py` -- the CLV methodology this export
  reuses, and why CLV is trusted over raw results in this project.
- `docs/review-remediation.md` -- the dated history of send-bar, edge-gate,
  and combat-grading decisions referenced throughout this file.

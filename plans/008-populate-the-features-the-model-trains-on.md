# Plan 008: Populate the features the model actually trains on

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md`.
>
> **Drift check (run first)**:
> `git diff --stat 142635c..HEAD -- backend/pipeline/ backend/analysis/calibrated_model.py backend/models.py`
> If any in-scope file changed since this plan was written, compare the
> "Current state" excerpts against the live code before proceeding; on a
> mismatch, treat it as a STOP condition.

## Status

- **Priority**: P1 — this blocks plan 007's decision gate and every downstream
  tuning question
- **Effort**: L
- **Risk**: MED — it writes a lot of new rows and changes what the model sees
- **Depends on**: 007 (done — its report is the verification tool for this plan)
- **Category**: bug / data
- **Planned at**: commit `142635c`, 2026-09-16

## Why this matters

Plan 007 set out to measure calibration so `min_edge` could be retuned. It found
the question unanswerable, because **the model has almost no features**.

Verified directly against a snapshot of the production database:

```
team_stats rows                : 330
team_stats distinct game_ids   : 1      <- game 1014, out of 1058 final games
elo_history rows               : 0
final games WITH any team_stats: 1
```

**Nothing in the production pipeline ever writes a `TeamStat` row.** Every one of
roughly twenty non-test references to `TeamStat` in `backend/` is a *read* —
`calibrated_model.py`, `ensemble.py`, `pick_generator.py`, `prop_pipeline.py`,
`props.py`, `opponent_adjustments.py`. `backend/pipeline/full_pipeline.py` has no
function that computes or stores them, and `backend/collectors/espn.py` exposes
only `fetch_scoreboard` — games and scores, no team statistics. The table is
defined, queried in seven places, and populated by nothing.

### The feature vector, traced one by one

`backend/analysis/calibrated_model.py` builds:

```python
            features = [elo_diff, point_diff, net_rating_diff, rest_days_diff, pace_diff]
```

| # | Feature | Source | Reality today |
|---|---|---|---|
| 1 | `elo_diff` | `EloHistory` for that game, falling back to current `EloRating` | `elo_history` is **empty**, so every historical row uses the team's *current* rating — which already reflects the outcome being predicted. **Lookahead leakage.** |
| 2 | `point_diff` | `TeamStat("point_diff")` else `0.0` | `0.0` in 1057 of 1058 rows |
| 3 | `net_rating_diff` | `offensive_rating - defensive_rating`, else `100.0` each | identical defaults → `0.0` in 1057 of 1058 rows |
| 4 | `rest_days_diff` | `TeamStat("rest_days")` else `1.0` | **`rest_days` is never written by anything** — it is not even among the ten `stat_type` values present in the one populated game. Structurally always `0.0`. |
| 5 | `pace_diff` | `TeamStat("pace")` else `100.0` | `0.0` in 1057 of 1058 rows |

The fitted coefficients were `[0.00681, 0.15517, 0.14189, 0.0, -0.13887]`. The
**exactly zero** fourth coefficient is feature 4 — the one that is structurally
constant. That is not a coincidence; it is the diagnosis confirming itself.

So `CalibratedModel` fits coefficients for features 2, 3 and 5 from effectively a
single row (game 1014), then at serve time is handed real values with roughly ±5
logits of swing. **That train/serve skew is the mechanism behind the 99%
predictions**, not a tuning problem. No adjustment to `min_edge`, no shrinkage,
and no change of ranking key touches it.

### A second, separate bug in the same area

`backend/pipeline/pick_generator.py:208`:

```python
    stats_rows = session.query(TeamStat).filter(TeamStat.team_id == team_id).all()
```

It filters on `team_id` only — **not `game_id`**. So the handful of teams that
appear in game 1014 get that one game's stats applied to *every* game they ever
play, while every other team falls through to defaults. This is why some
generated picks carried `recent_form` / `net_rating` rationale factors and
others did not: it depended on whether the team happened to be in game 1014.

## What is and is not derivable

This matters more than anything else in the plan, because two of the five
features **cannot** be reconstructed from data this project has.

**Derivable from the `games` table alone** (1058 final rows with scores and dates):

- `point_diff` — rolling average margin over a team's prior N games
- `rest_days` — days since that team's previous game
- `elo_history` — chronological replay, writing the post-game rating per team

**NOT derivable from scores and dates:**

- `offensive_rating`, `defensive_rating` — need possessions
- `pace` — needs possessions

ESPN's scoreboard endpoint as used here does not supply possessions, and no
collector in the repo fetches them. **Do not invent these.** Fabricating a pace
number would be worse than a missing one: the model would fit a coefficient to
noise and nobody downstream would know.

That leaves a modelling decision this plan does **not** make — see "The decision
this plan feeds" at the end.

### Backfill precedent to follow

`backend/backtesting/historical.py:118-131` already replays games chronologically
and writes `EloHistory` rows with the **post-game** rating:

```python
        margin = game.home_score - game.away_score
        winner = home_team.abbreviation if margin > 0 else away_team.abbreviation
        elo.update(home_team.abbreviation, away_team.abbreviation, winner,
                   margin=abs(margin))

        home_rating_after = elo.get_rating(home_team.abbreviation)
        away_rating_after = elo.get_rating(away_team.abbreviation)
        session.add(EloHistory(team_id=game.home_team_id, game_id=game.id,
                               sport=sport, rating=home_rating_after))
        session.add(EloHistory(team_id=game.away_team_id, game_id=game.id,
                               sport=sport, rating=away_rating_after))
```

Read this before writing your own. **Note carefully**: it stores the rating
*after* the game. `calibrated_model` looks up `elo_history_map[(team_id,
game_id)]` as the feature *for* that game — so using the post-game rating is
lookahead. Your backfill must store the **pre-game** rating, or the consumer must
be changed to look up the previous game's row. Decide which, state your choice
and why in your report, and be explicit that you checked this — it is the single
easiest way to reintroduce leakage while appearing to fix it.

## The rule that governs this whole plan

**Every value written must be point-in-time.** A `TeamStat` row attached to game
G must be computed only from games strictly before G. A rolling point differential
that includes G's own result, or an Elo rating that reflects G's outcome, turns
the training set into a lookahead oracle and makes every downstream measurement
meaningless — including plan 007's report, which is how you will verify this work.

This repo's conventions name the hazard directly: *"train-test leakage, fitting
on graded outcomes that include the row being predicted"* and *"a resume/
completeness check must not assume contiguity."*

## Commands you will need

| Purpose | Command | Expected |
|---|---|---|
| Full suite | `.venv/Scripts/python.exe -m pytest backend/tests -q` | `481 passed` before, 0 failed after |
| 007's report | `.venv/Scripts/python.exe -m backend.analysis.calibration_report --sport nba --db <path>` | reliability table |

Run from repo root.

**Use a copy of the database, never the live file.** Make your own with
`sqlite3`'s backup API or a file copy, and pass it via `--db`. The backfill
writes thousands of rows; it must not touch `sports_picks.db` until the operator
decides to run it for real.

## Scope

**In scope:**
- `backend/pipeline/team_stats.py` (create) — computation + backfill
- `backend/pipeline/full_pipeline.py` — call it in the daily flow
- `backend/pipeline/pick_generator.py` — fix the `_get_team_stats` `game_id` bug
- `backend/scripts/backfill_team_stats.py` (create) — one-off historical backfill
- New tests under `backend/tests/`

**Out of scope — do not touch:**
- `backend/analysis/calibrated_model.py` — do **not** change the feature list,
  the defaults, or the model. Changing the consumer while changing the data
  makes it impossible to tell which change moved the numbers. The one exception
  is if you choose to fix the Elo lookahead on the consumer side instead of the
  producer side; if so, that is the *only* edit permitted there, and you must
  call it out prominently.
- Any strategy under `backend/analysis/variants/` — no weights, no clamps, no
  `min_edge`.
- `backend/analysis/calibration_report.py` — 007's tool is your measuring
  instrument. Do not modify the instrument you are being measured by.
- Fetching possessions / a new upstream stats source — a bigger piece of work.

## Git workflow

- Branch: `advisor/008-populate-team-stats`
- Conventional commits, one per logical unit:
  - `feat(pipeline): compute point-in-time team stats per game`
  - `fix(picks): scope team-stat lookup to the game being predicted`
  - `feat(scripts): backfill team stats and elo history from final games`
- Do NOT push.

## Steps

### Step 1: Baseline and evidence

Run the suite (`481 passed`). Then, against a **copy** of the database, record
the current state so you can prove the change later:

```
select count(*) from team_stats;                       -- expect 330
select count(distinct game_id) from team_stats;        -- expect 1
select count(*) from elo_history;                      -- expect 0
```

Put these in your report. If they differ materially from the numbers above,
STOP — the database has changed since this plan was written.

### Step 2: The computation, as pure functions

Create `backend/pipeline/team_stats.py`. Start with pure functions over
already-loaded rows, no DB access, so they are testable without a database:

- `rolling_point_diff(prior_games, team_id, lookback=10) -> float` — mean of
  `(points_for - points_against)` over that team's most recent `lookback` games
  **strictly before** the target game. Returns `0.0` when there are none.
- `rest_days(prior_games, team_id, game_date) -> int` — days between `game_date`
  and that team's most recent prior game. Choose and document a default for a
  team's first game.
- `record_splits(prior_games, team_id) -> dict` — `home_wins`, `home_losses`,
  `away_wins`, `away_losses`, `last_n_wins`, `last_n_losses`, matching the
  `stat_type` names already present in the table.

**Tests first.** At minimum: a team with no prior games; a team with fewer than
`lookback` games; the strictly-before boundary (a game on the same date must not
count itself); and rest days across a multi-day gap.

The boundary test is the important one — write it so it fails if you use `<=`
instead of `<`.

### Step 3: Prove the point-in-time property

Add a test that seeds three games for one team with known scores, computes the
stats for the *middle* game, and asserts the value reflects **only the first
game** — not the middle or the last.

Then mutate: change the filter to include the target game, confirm the test
fails, restore. Report the failing output. A point-in-time helper that silently
includes the present is the failure this whole plan exists to prevent.

### Step 4: Persist per game

Add `store_team_stats_for_game(session, game, prior_games) -> int` writing one
`TeamStat` row per `(team_id, game_id, stat_type)` for both teams. Make it
idempotent: re-running for the same game updates rather than duplicating (the
table has no unique constraint — match the upsert shape used by
`full_pipeline._store_odds`).

**Verify** with a test that calls it twice and asserts the row count does not
double.

### Step 5: Elo history, pre-game

Add the Elo backfill, following `backtesting/historical.py:118-131` — but
resolve the pre-game/post-game question from "Backfill precedent" above before
you write it, and state your decision in the report.

**Verify**: a test that replays three games and asserts the `EloHistory` row
attached to game 2 does **not** reflect game 2's result.

### Step 6: Wire it into the daily pipeline

Call the per-game computation from `full_pipeline` where final scores land, so
new games get stats going forward. Keep it out of the odds/props paths.

**Verify**: a test that runs the relevant pipeline function over a seeded final
game and asserts `TeamStat` rows now exist for it.

### Step 7: Fix the `game_id` bug

In `backend/pipeline/pick_generator.py:208`, scope the lookup to the game being
predicted as well as the team.

**Careful**: at *prediction* time the game is still `scheduled` and has no
`TeamStat` rows of its own — the stats wanted are the ones computed from prior
games. Decide whether prediction should read the most recent prior game's rows or
whether the pipeline should write a row for the upcoming game too, and say which
and why. **Do not** leave it reading an arbitrary unrelated game.

**Verify**: a test seeding two games for the same team with different stat values
and asserting the prediction path picks the right one. Mutate by restoring the
team-only filter and confirm the test fails.

### Step 8: Backfill script

Create `backend/scripts/backfill_team_stats.py`, following the shape of the
existing scripts in that directory (read `backfill_ufc_elo.py` first). It must:

- take a `--db` path, defaulting to **nothing** — require it explicitly, so no
  one runs it against production by accident
- process games in **chronological order**, so each game's stats see only prior ones
- support `--dry-run` printing counts without writing
- be resumable and **not assume contiguity** — ask per game whether it already
  has stats rather than tracking how far it got
- report rows written per sport

**Verify**: run it `--dry-run` against your copy, then for real against the copy,
then confirm:

```
select count(distinct game_id) from team_stats;   -- expect ~1058, not 1
select count(*) from elo_history;                 -- expect ~2x final games
```

### Step 9: Re-run 007's report and report the numbers

Against the backfilled copy:

```
.venv/Scripts/python.exe -m backend.analysis.calibration_report --sport nba --db <copy>
```

Put the **actual output** in your report, alongside the pre-backfill numbers from
007's original run. Do not tune anything to improve it.

This is the payoff: for the first time the model will have been trained on real
features, and the reliability table becomes meaningful. **Whatever it says, report
it as observed** — including if the model is still badly calibrated. That is a
legitimate and useful result.

### Step 10: Full suite and scope check

**Verify**:
- `.venv/Scripts/python.exe -m pytest backend/tests -q` → 0 failed
- `git diff --name-only` → only in-scope files
- `git diff --stat` shows **no** change under `backend/analysis/variants/`, and
  none to `calibrated_model.py` beyond the single permitted Elo-lookahead fix
- the live `sports_picks.db` is untouched (`git status` and file mtime)

## Done criteria

- [ ] Suite exits 0
- [ ] `select count(distinct game_id) from team_stats` on the backfilled copy is
      within a few of the final-game count, not 1
- [ ] `elo_history` is populated and its game-2 row does not reflect game 2
- [ ] The point-in-time mutation test (Step 3) was run and failed as expected
- [ ] The `game_id` mutation test (Step 7) was run and failed as expected
- [ ] 007's report re-run against backfilled data, actual output in the report
- [ ] No file outside the In-scope list modified; live DB untouched
- [ ] `plans/README.md` status row updated

## STOP conditions

Stop and report (do not improvise) if:

- Baseline counts differ materially from Step 1's expectations.
- You cannot compute a feature without possessions data — that is expected for
  `offensive_rating`, `defensive_rating` and `pace`. **Do not fabricate them, do
  not proxy them with something else, and do not quietly write defaults as if
  they were measured.** Report which features you could populate and which you
  could not.
- Resolving the Elo pre/post-game question appears to require editing
  `calibrated_model.py` beyond the one permitted line — report the options.
- The backfill would need to write to the live database to be verifiable.
- After backfilling, 007's report shows calibration that looks *too* good
  (near-perfect). That is the signature of leakage, not success — investigate
  before reporting it as a win.

## The decision this plan feeds — deliberately not made here

Three of five features become real; two (`net_rating_diff`, `pace_diff`) cannot
be populated from available data. That leaves a modelling choice:

1. **Drop them** and retrain on three features — honest, smaller model.
2. **Source possessions** from a new upstream feed — more work, keeps the model
   as designed.
3. **Leave them defaulted** — status quo for those two, meaning two of five
   inputs remain constant.

The right answer depends on what 007's report says once the other three are real,
which is why this plan stops at measuring rather than choosing. Whoever picks
should also revisit whether `min_edge` and the digest's ranking key still make
sense — both were the original questions, and both have been blocked on this the
whole time.

## Maintenance notes

- After this lands, `TeamStat` has a real producer for the first time. Anyone
  adding a `stat_type` must add it to the computation, not just the reader.
- `rest_days` is read by `calibrated_model` but was never in the table's
  `stat_type` vocabulary. Adding it changes feature 4 from structurally-zero to
  live — expect the fourth coefficient to stop being exactly `0.0`, which is a
  good sign that this worked.
- The backfill is a one-off, but the pipeline wiring is permanent. A reviewer
  should check that the daily path cannot write a stat row that includes the
  game's own result.

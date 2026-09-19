# Session handoff — 2026-09-17

Everything below is recoverable from `git log` and `plans/`; nothing important
lives only in a chat transcript.

## Production is prepared, collected and graded — 2026-09-18

The runbook has been **run against `sports_picks.db`**, each step dry-run first
and checked against what it produced on a copy. Backup, taken after a
`wal_checkpoint(TRUNCATE)` so the `.db` file is complete:
**`sports_picks.backup-20260918-222023.db`** (`integrity_check: ok`, 708 picks,
1664 games). A second, pre-collection backup:
`sports_picks.backup-20260918-223905.db`. They are the only rollback.

| | before | after |
|---|---|---|
| games | 1664 | **1713** |
| — final | 1058 | **1363** |
| — past, not final | 570 | **314** |
| games with `espn_id` | 0 | **1371** |
| duplicate `espn_id` | 0 | **0** |
| picks / max id | 708 / 708 | **708 / 708** |
| **`pick_results`** | **0** | **173** |
| — props graded | 0 | **75 of 82** |
| **`player_stats` `game_log`** | **0** | **26,843** |
| — players with ≥3 logs | 0 | **571** |
| elo_history | 2116 | 2726 |

`integrity_check: ok`. Graded: moneyline 18W/18L, over_under 15W/22L,
spread 14W/11L, **prop 48W/27L**. 535 picks remain ungraded — mostly on the
314 games still not final (ncaab and mma/boxing).

### The first prop calibration from production

```
  tier   settled   wins  losses   win%     units     roi  reliable
  5         36     27       9    75.0%    +8.62  +0.239    NO
  4         16      8       8    50.0%    -3.66  -0.229    NO
  3         10      7       3    70.0%    +0.42  +0.042    NO
  2         10      5       5    50.0%    -2.19  -0.219    NO
  1          3      1       2    33.3%    -1.07  -0.358    NO

  5-star vs 4-star: 75.0% vs 50.0% -- higher by 25.0 points.
  tier 5: n=36 across 2 games -> effective 19.5 (ICC 0.05), 10.1 (ICC 0.15)
```

Reproduce with:

```
.venv/Scripts/python.exe -m backend.analysis.prop_calibration --db <abs win path> --sport nba
```

**These are identical to the figures the copy produced, which is the good
news** — the production pipeline reproduced it exactly. **It is also the
warning**: the sample is still the same **2 games**. Every tier is flagged
unreliable, and tier 5's effective n is 19.5 against a `min_bin` of 30.

**How badly the headline moves on partial data:** a run taken when only one of
the two games had been graded reported tier 5 at **61.5%**. The same props, the
same model, a 13-point swing from which game happened to be graded. Quote the
effective n, never the raw win rate.

**`digest.enabled` stays `false`.** What changed is that this measurement is
now possible and repeatable in production — not that the model is good.

### Two collector bugs found by doing this for real

1. **Box scores were skipped per DATE, not per game.** The resumability check
   filtered on `(sport, game_date)`, so on any date with more than one game
   only the first was ever collected: **176 of 1248 final games, 3826 rows
   where the true figure is 26,843**. It surfaced because a game's props could
   not be graded when another game shared its date. `PlayerStat` has no
   `game_id`, so the check now also filters on the game's own `team_id`s.
   Fixed in `cc9eee2`. **The plan 010 test meant to guard resumability used one
   game per date, so the distinction was invisible to it.**
2. **The catch-up asked only about the stored date.** Game 1603 holds 48 props
   and stayed non-final because it sits on 2026-05-25, the UTC date of an 8pm
   ET tip, while ESPN files event `401873200` under 05-24. Now searches ±1 day,
   the same window `backfill_espn_ids` already used. Fixed in `703e6ff`.
   **That fix only works because plan 014 populated `espn_id` first** — without
   an id the fallback still matches on the wrong date and inserts a twin. The
   test encodes that dependency rather than hiding it.

Both are the session's recurring shape: **a guard that was right about the case
it imagined and blind to the one that mattered.**

### Still open after all this

- **7 props ungraded** — players ESPN's box score does not list. DNPs produce
  no row by design (absent is not zero), so this is correct behaviour.
- **314 past games still not final**, almost all ncaab (59) plus mma/boxing,
  which are out of scope for the ESPN scoreboard path.
- **ncaab duplicates.** The catch-up inserted rows for ESPN events whose
  abbreviations do not match our display-name team rows, so ~10 real games now
  exist twice. **429 of 708 picks are on ncaab.** Needs the team-identity fix
  before ncaab can be trusted; nba and mlb are unaffected.
- **More games.** Everything above rests on two nights of props. The scheduler
  is still not running; starting it is now safe for nba/mlb.

## ncaab team identity repaired in production — 2026-09-19

Plan 015 run end to end against `sports_picks.db`. Scheduler stopped first
(PIDs 11760/8460 under `nohup`); backup taken after a
`wal_checkpoint(TRUNCATE)`: **`sports_picks.backup-20260919-002602.db`**
(`integrity_check: ok`, 1713 games, 708 picks, 629 teams). It is the only
rollback.

| | before | after |
|---|---|---|
| ncaab team rows | 147 | **87** |
| — holding a display name | 78 | **1** |
| ncaab games | 120 | **86** |
| — final | 61 | **72** |
| — still not final | 59 | **14** |
| — missing `espn_id` | 59 | **12** |
| ncaab picks | 429 | 429 |
| — **graded** | **16** | **348** |
| total games | 1713 | 1679 |
| total picks / `pick_results` | 708 / 173 | 708 / **505** |

`integrity_check: ok`, 0 duplicate ncaab fixtures, abbreviations unique,
0 orphaned picks. **No picks and no final games were lost** — the 34 games
removed were empty duplicate rows.

### What was actually wrong

`_ensure_game_from_odds` created missing teams as
`Team(abbreviation=<display name>)` for every sport, though its docstring
limited that to sports without ESPN coverage. ESPN sends `PENN`; a row
holding `'Pennsylvania Quakers'` never matched, so those games never got an
`espn_id` and never finalised. Fixed in `a18bae8`: labels now resolve through
`backend/team_identity.py`, and a game whose team cannot be identified is
skipped rather than backed by a row that can never match.

**Two corrections to what this file previously claimed:**

1. It said ncaab's problem was "~10 real games now exist twice". The measured
   shape is **59 stranded originals**, and separately **34 duplicates** that
   only become visible once the team rows merge — while the two schools hold
   different team ids, a same-teams/same-date scan reads the twins as
   different fixtures. Both were true at once.
2. A survey identifying bad rows as `length(abbreviation) > 5` missed team
   327, which holds `'QUC'`. Short is not the same as valid.

### The survivor rule, measured

`resolve_duplicate_games` keeps the **final** row. All 34 groups were
`('final', 'scheduled')`: the final row carries the score, `espn_id`,
`team_stats` and `elo_history`; the scheduled twin carries the picks and odds.
**In 27 of 34 the picks sit on the non-final row**, so
`merge_duplicate_games.py`'s "row with picks wins" would have discarded real
scores in 79% of these cases. Do not reuse it here.

### Two open items this surfaced

1. **33 graded picks carry invalid `odds_at_pick`** (−99..−61; valid American
   odds are ≤ −100 or ≥ +100). `payout_for` correctly refuses to price them
   and books 0.0. Because a loss books −1.0 regardless of odds, only the
   **16 wins** are zeroed — so **ncaab ROI is biased downward, not merely
   noisy**. Any unit figure below is a floor. Where `odds_at_pick` acquires
   these values is not yet traced.
2. **12 ncaab games still have no `espn_id` and 14 remain non-final.**
   `unknown_team` in `backfill_espn_ids` dropped from 7 to **0**, so the
   identity problem is gone; what is left is ESPN genuinely not listing
   those fixtures on the dates we hold.

### First ncaab grading

```
  ncaab picks graded : 348 of 429   (141 win / 207 loss = 40.5%)
  units              : -83.8   <- understated; 16 wins booked at 0
```

**Do not read 40.5% as a model verdict yet.** 348 picks sit across 72 games —
about 4.8 per game, and picks within a game are correlated, so the effective
sample is far smaller than 348. Quote effective n, as
`backend/analysis/prop_calibration.py` already does. `digest.enabled` stays
`false`.

## Where things stand

`master` is at `cc9eee2` and **pushed**. Test baseline: **646 passing backend,
0 failed**, identical on Python **3.12 and 3.14**; frontend eslint 0/0, `tsc` clean, vitest 15/15.
Started this session at 545.

**CI is live** (`.github/workflows/ci.yml`), ~50s for all three jobs. Local:

```
.venv/Scripts/python.exe -m pytest backend/tests -q
```

`master` is the repository's only branch and its default.

### The app runs

```
.venv/Scripts/python.exe -m uvicorn backend.api.main:app --host 127.0.0.1 --port 8000
```

`/health` answers, the React bundle serves from `frontend/dist`, and
`/games/today`, `/picks/today` and `/users/feed` all return 200. Empty arrays
are correct — there are no games dated today.

**Do not use `start-server.bat`**: it `cd`s to
`C:\Users\mwill\OneDrive\...`, the path that died in July 2026. **And be
careful with `start.sh`**: it also launches `python -m backend.pipeline.scheduler`,
which ingests — see the deploy-order warning below. The scheduler is off by
default (`ENABLE_SCHEDULER` unset), so plain uvicorn is safe.

### This session (2026-09-16 evening → 09-17)

The session crossed local midnight, so timestamps differ by source: the DB
backup is stamped `20260916-231622` (local) while CI logs `06:xxZ` (UTC) the
next day. Same session.

| Commit | What landed |
|---|---|
| `c292068` | `compute_historical_elo` stopped implementing its own Elo replay |
| `9de96b8` | Calibration report stopped printing a false "LOWER BOUND" caveat |
| `fc9a3f4` | **Live `sports_picks.db` backfilled** — production had never had the 008 data |
| `9ff809e` | CI wired: backend matrix 3.12/3.14 + frontend |
| `e5e083d` | Dependencies pinned via `constraints.txt` |
| `72416da` | `sports_picks.egg-info` untracked |
| `6117d5e` | **010 T1** — `grade_pick` refuses instead of inventing a loss |
| `fb50170` | **010 T3** — ESPN post-game box-score collector |
| `cfb276d` | **010 T2** — `PickModel` carries prop player and market |
| `935ed15` | **010 T4** — props routed to the prop grader, payouts priced |
| `8a286c3` | **010 T5** — prop confidence measured against outcomes |
| `3246256` | **011** — the ASGI app builds on access, not at import |
| `5603514` | **013 T1-2** — finalize games played since the last run |
| `1c75c11` | **013 T3** — one-off catch-up for games never marked final |
| `eddf502` | **014 T1** — `Game.espn_id`, identity matching |
| `a5f9201` | **014 T1** — `espn_id` backfill script |
| `e04fa24` | **014 T2** — merge games stored twice, once per date convention |
| `822f5ef` | **014 T3** — dates are Eastern, via one shared `time_utils.et_date` |
| `adb66d8` | **012 T1** — dead recent-form fetch removed |
| `033d712` | **012 T2** — recent form bounded to before the game |
| `6c8c62e` | **012 T3** — one edge formula, on one scale |
| `e90049a` | **WebSocket transport installed** — `/ws` can finally upgrade |
| `a7112d7` | Box scores fetched by stored `espn_id` instead of searching |
| `703e6ff` | Catch-up asks about the day's neighbours too |
| `cc9eee2` | **Box scores skipped per game, not per date** — 176 of 1248 collected before this |

**Plans 001-011 and 014 are complete. 013 is done bar the production
catch-up. 012's Tasks 1-3 are done and Task 4 is blocked on data.**

### The findings worth carrying forward

Every one of these is the same shape: **a well-formed wrong value, or a
component that works while the composition does not.** None raised, none
logged an error, and several had passing tests.

1. **Nobody asked ESPN about yesterday.** `morning_scout` fetched `today` at
   8/9/10am ET — before that day's games were played — and nothing ever
   revisited a past date. **570 games with past dates were stuck non-final**,
   so grading, box scores, `game_log` and `elo_history` growth were all
   dormant. The upsert that finalizes a game was correct and unreachable.
   Fixed in `5603514` (3-day finalize-only lookback).
2. **ESPN dates are UTC; its scoreboard is Eastern.** Every game after 8pm ET
   was filed a day late, so the same game existed twice — once per convention.
   Fixed in `822f5ef`, but only after `eddf502`/`e04fa24`, because flipping the
   date first would have *doubled* the duplicates.
3. **Nine real games were counted twice in the Elo replay.** Both halves of a
   twin pair were final, so `backfill_elo_history` applied nine results twice.
   A full replay moved **957 of 2098 pre-game ratings**, median 0.32, p90 6.81,
   **max 20.64 points** — and `elo_history` is what the calibrated model trains
   on. **Every Brier number measured before that replay describes corrupted
   ratings.**
4. **Prop projections never saw recent form.** `recent_weight` is 0.6, so it is
   60% of every projection, and `game_log` was empty — every prop ever
   generated used 100% season average. The probability model never ran either:
   `use_distribution` needs three game-by-game values and has always been
   `False`, so every prop used `abs(diff / line) * 100`, which is not a
   probability and inflates small lines (a 0.5 line reported a 140% "edge").
   Fixed in `6c8c62e`.
5. **Importing `backend.api.main` migrated whatever `sports_picks.db` was in
   the cwd.** Running pytest from the repo root was enough. Harmless only
   because migrations are additive — `migrate_api_usage` contains a
   `DROP TABLE`. Fixed in `3246256`.
6. **The WebSocket feature was dead everywhere it actually runs**, and no
   functional test could have caught it. `uvicorn` was declared without a
   WebSocket library, so `/ws` answered the upgrade with a plain 200.
   `TestClient` implements WebSocket **in-process** using Starlette's own code
   and never touches uvicorn's transport, so plan 004's 15 tests all passed
   against a path production never uses. Found by launching the app; fixed in
   `e90049a` with a **dependency** guard rather than a functional one.
7. **The local venv was the stale environment, not CI.** `pyproject.toml` had
   floors only and both `Dockerfile` and CI ran a bare `pip install -e .`, so
   both tracked latest while the venv sat months behind — starlette 0.52.1
   locally against 1.6.0 everywhere else. `constraints.txt` now pins all four
   environments to one set.
8. **The deployed runtime had never run the test suite.** `Dockerfile:17` pins
   `python:3.12-slim`; the venv is 3.14. The matrix closed that, and 3.12
   passes — the risk was latent, not active.

### Merged into `master`

| Plan | What landed |
|---|---|
| 001 | Characterization tests pinning the paper-trading money path |
| 002 | Calibrator trains (`status=="final"`), recalibrated thresholds reach `calculate_confidence`, `receptions` persisted |
| 003 | Vig removed in all five strategies; odds averaged in probability space; one bad game no longer discards a batch |
| 004 | Activity feed works — `/users/feed` returns 200, real WebSocket frames delivered |
| 005 | Odds API key kept out of logs and responses; budget 429 reachable |
| 006 | Per-sport, push-aware, newest-first recalibration |
| 007 | Out-of-sample calibration report (a read-only measurement tool) |
| 008 | The model trains on real point-in-time features for the first time |
| 009 | Frontend ESLint clean — 6 errors / 2 warnings → 0 / 0 |
| — | Daily picks digest (six-task feature, dry-run by default) |

### All nine plans are now merged (historical — 010-014 came later)

**`advisor/008-populate-team-stats` — MERGED** as `bb99490`. Seven commits: the
original five, plus two review rounds. Report:
`.superpowers/008-team-stats-report.md`.

Verified post-merge on `master`: **545 backend tests passing, 0 failed**
(500 before + 45 from 008). Frontend unchanged: eslint 0/0, tsc clean,
vitest 15/15.

What it fixed: nothing in production wrote a `TeamStat` row, so the model
trained on 1058 games with 4 of 5 features identically zero, and `elo_diff`
fell back to a *current* `EloRating` for historical games — lookahead. That is
the root cause of the model claiming 99% where the de-vigged market said 84.5%.

| | |
|---|---|
| `team_stats` distinct game_ids | **1 → 1058** |
| `elo_history` rows | **0 → 2116** |
| `rest_days` | present for the first time |
| Live `sports_picks.db` | backfilled **2026-09-16** (was untouched during 008) — see below |
| Fabrication check | `pace` / `offensive_rating` / `defensive_rating` still present for **exactly 1** game. Structurally refused, not fabricated. |

**Brier ROSE 0.1836 → 0.2020, and that is the honest result.** The old number
came from a model whose only non-zero coefficient was an end-of-season Elo
rating — constant per team and unknowable before tip-off. 0.2020 is the first
measurable score. The model is still badly calibrated: overconfident on
underdogs by 15-19 points.

> **Superseded by the 2026-09-17 re-run.** The 007 report on the backfilled
> data gives **0.2032**, not 0.2020 — the small difference is consistent with
> `c292068` changing draw handling in the Elo replay. Treat **0.2032** and the
> bin table in "Calibration baseline" below as current; the numbers here are
> what 008 measured at the time.

#### The two review rounds, and why they matter

Both found the same failure shape — a value's basis changed and only some
readers were updated:

1. **Round 1**: commit `553bc41` fixed the *training* half of the Elo lookup and
   left live serving reading `EloRating`, a table written only by
   `compute_historical_elo`, whose entry point `load_historical_data` has **no
   callers**. Frozen at whatever a past manual run left. Trained and served
   ratings diverged by up to 134 points in both directions, so no intercept
   absorbed it. Fixed in `8189510` by adding a most-recent-prior-`EloHistory`
   step, strictly before the predicted game's date.
2. **Round 2**: that fix displaced the same defect into `_check_lookahead_spot`,
   which compared a history-basis `team_elo` against an `EloRating`-basis next
   opponent across a 50-point band — while the bases differed by a mean of 53.
   Fixed in `f4b93eb`. Note the obvious one-line fix was a trap: bounding by
   `next_game.date` or passing `next_game.id` would each have converted a basis
   bug into a *lookahead* bug.

Both rounds are mutation-proved. `_check_lookahead_spot` had no direct test
before round 2; it now has five.

#### Known residuals, deliberately left

- ~~**`backtesting.historical.compute_historical_elo` still writes post-game
  ratings into the `elo_history` column that now holds pre-game ones.**~~
  **RESOLVED 2026-09-16.** It no longer implements a replay at all: it
  delegates to `pipeline.team_stats.backfill_elo_history`, the function the
  daily pipeline already calls, and keeps only the `EloRating` upsert (fed by
  a new `final_ratings` key on the delegate's return). Delegation also gave it
  two guards it never had — it skips games already in the history instead of
  appending duplicates, and it refuses combat sports. That second one was a
  latent mirror of the same bug: `SEASON_RANGES` accepts `mma`/`boxing`, so the
  naive fix would have written *pre*-game rows into the grader's *post*-game
  history. Three tests added, all watched failing first and mutation-proved.
- `EloRating` is now routed around rather than fixed for team sports. Resolve
  the convention clash above first, then decide whether to wire it up or delete
  it.
- 14 of 239 upcoming NBA games still fall through to `EloRating` — teams with no
  finals, so no history to replay. 5.9%, documented, on the old basis.
- **Two of five features remain structurally constant** (`pace`,
  `offensive_rating` / `defensive_rating` need possessions). The modelling
  choice — drop them, source possessions, or leave them defaulted — is the
  natural next decision.
- `models.py` declares **no indexes** on `games`, `elo_history` or
  `elo_ratings`. Noise at ~2k rows; revisit if `elo_history` grows an order of
  magnitude.
- 007's `calibration_report.py` docstring documents the now-fixed data defect as
  a "known limitation". That paragraph is stale.
- `_check_lookahead_spot`'s thresholds (100-point favourite, 50-point band,
  4/10-day windows) are uncalibrated constants feeding a real pick adjustment.
  Correct basis now, unexamined thresholds.

**The success-shaped failure did not occur.** A post-backfill run that looked
*excellent* would have suggested leakage; instead Brier got worse with a
coherent explanation, and the fabrication check came back clean.

## How to resume

```bash
cd C:/Users/mwill/Documents/mwilliams2733/sports_picks
git worktree prune          # clears stale entries pointing at deleted temp dirs
git worktree list           # should show only the main tree
git log --oneline -12
```

Then, for whichever plan you pick up, recreate a worktree:

```bash
git worktree add -b <branch> <path> <base-commit>
```

**The worktrees have no `.venv`** (it is gitignored). Use the main tree's
interpreter by absolute path, and run pytest from the worktree root so `backend`
resolves to the worktree copy rather than the main tree:

```
C:/Users/mwill/Documents/mwilliams2733/sports_picks/.venv/Scripts/python.exe -m pytest backend/tests -q
```

Verify that once with
`... -c "import backend; print(backend.__file__)"` — it must print a path inside
the worktree.

That check matters more than it looks. `import backend` resolves by **cwd**,
not by the editable install, and on 2026-09-17 the editable install was found
pointing at `c:\users\mwill\onedrive\...` — a directory deleted in July 2026.
Everything still worked, because cwd won every time. A dangling editable
install is invisible until the day cwd is not what you assume.

### Environment

The venv is **Python 3.14**; production (`Dockerfile:17`) is **3.12**. CI runs
both. Dependencies are pinned in `constraints.txt`, consumed by both the
Dockerfile and CI — do not `pip install --upgrade` casually, since that
silently desynchronises the venv from the pinned set. To upgrade deliberately:

```
.venv/Scripts/python.exe -m pip install -e ".[dev]" --upgrade --upgrade-strategy eager
.venv/Scripts/python.exe -m pip freeze | grep -v "^-e " > constraints.txt   # then restore the header
```

Plain `--upgrade` is a no-op here: pip's default `only-if-needed` strategy
leaves already-satisfied dependencies alone. `eager` is required. Push and let
the 3.12/3.14 matrix confirm the new set before trusting it.

## Open plans

**Plans 001-011 and 014 are complete.** 013 is done bar the production
catch-up. **012's Tasks 1-3 are done; its Task 4 is blocked on data.** `plans/README.md` has the full status table, every finding
that was *not* turned into a plan, and a "considered and rejected" section so
nothing gets re-audited.

### Plan 010 — done, and what it found

`plans/010-grade-the-picks.md`, all five tasks, each annotated inline with the
corrections the plan needed once executed.

| Task | Landed |
|---|---|
| 1 | `grade_pick` returns `None` for a type it has no branch for; six call sites updated, not the two the plan named |
| 2 | `PickModel.prop_player` / `prop_market` + migration + backfill script |
| 3 | `collectors/espn_box_score.py` — post-game box scores, keyed on final games |
| 4 | Props routed to `grade_prop_pick`, with payouts priced from their own odds |
| 5 | `analysis/prop_calibration.py` — win rate and ROI per confidence tier |

**The chain works end to end.** Proven on a copy: 82 props resolved, box
scores collected for both games carrying them, **75 props graded (48 W / 27
L)**, and the report produced real numbers.

**It cannot grade anything in production yet, and that is not a code problem.**
Every prop in the database belongs to a game still `status='scheduled'` with
NULL scores, so `grade_pending_picks` correctly skips all 82. The numbers below
were obtained by reconstructing the two games' real final scores from ESPN
**on a copy** (home/away orientation verified to match before writing).
Production needs the scheduler to run and mark those games final.

#### First prop measurement (NBA, on a copy, 2026-09-17)

```
  tier   settled   wins  losses   win%     units     roi  reliable
  5         36     27       9    75.0%    +8.62  +0.239    NO
  4         16      8       8    50.0%    -3.66  -0.229    NO
  3         10      7       3    70.0%    +0.42  +0.042    NO
  2         10      5       5    50.0%    -2.19  -0.219    NO
  1          3      1       2    33.3%    -1.07  -0.358    NO
```

**5-star vs 4-star: 75.0% vs 50.0%, 5-star higher by 25 points.** Mildly
encouraging and **not actionable**: all 75 graded props come from **2 games**,
so tier 5's effective n is 19.5 (ICC 0.05) down to 10.1 (ICC 0.15). Every tier
is flagged unreliable, correctly.

Reproduce with:

```
.venv/Scripts/python.exe -m backend.analysis.prop_calibration --db <abs win path> --sport nba
```

**If it prints `REFUSING: no graded prop picks`, that is the tool working** —
an all-zero table would read as "confidence predicts nothing", which is a
finding, not the absence of one.

Of the five "natural next pieces" listed on 2026-09-16, three are done:

1. ~~Reconcile `historical.py`'s post-game Elo convention.~~ **Done** —
   `c292068`. It no longer implements a replay at all; it delegates to
   `team_stats.backfill_elo_history` and keeps only the `EloRating` upsert.
3. ~~Re-run the 007 calibration report.~~ **Done** — numbers below.
5. ~~Wire CI.~~ **Done** — `9ff809e`, green on 3.12 and 3.14.

### Still open

1. **Decide `EloRating`'s fate.** Routed around rather than fixed. Still
   written by `compute_historical_elo` and still read as the pick generator's
   fallback for the 14/239 upcoming NBA games with no replayable history. The
   convention clash that blocked this is resolved, so the decision is now
   unblocked: wire it up properly or delete it.
2. **Decide what to do about the three constant features.** `pace`,
   `offensive_rating` and `defensive_rating` all need possession counts no
   collector supplies. Every Brier number this project has produced came from
   four working features, not seven. Options: source possessions, drop the
   features, or leave them defaulted and stop counting them.
3. **The underdog overconfidence — do not retune on it yet.** See the
   calibration section; the bins carrying the finding are n=15 and n=28, under
   the report's own `min_bin=30`. **More completed games, not a threshold
   change.**
4. **Migrate `PaperTrading.tsx` to React Query** — 009 left a documented
   suppression there naming this as the real fix.
5. **Prepare production and start the pipeline.** See the block at the top of
   this file. Until then nothing grades, no box scores accumulate, and 012's
   Task 4 cannot measure anything.
6. **012 Task 4 needs volume, not code.** Box scores exist for 2 games, so all
   52 players have exactly one `game_log` row and **zero props clear the three
   values Task 3 now requires**. Running the report today returns 0 analysed
   and the calibration tool correctly refuses.
7. **Make `espn_box_score` use `game.espn_id`** before that collection run.
   It still calls `resolve_espn_event`, which searches the scoreboard on three
   dates per game, because it predates the column — and its module docstring
   now asserts something 014 made false ("ESPN shares no id with our Game").
   Using the id turns ~4000 requests into ~1014 for a 1014-game run.
8. **ncaab teams hold display names in `abbreviation`** (`"Pennsylvania
   Quakers"`), so 60 rows can never match ESPN. Needs a team-identity fix.
9. **`EspnStatsSource._find_team_id` does not exist**, so
   `fetch_season_averages` raises `AttributeError` and the season-average
   fallback is dead. P1 in its own right, given `nba_api` cannot reach
   `stats.nba.com` from here.
10. **`MARKET_STAT_MAP` is duplicated** in `grader.py:13` and
    `prop_analyzer.py:12` (as `MARKET_TO_STAT`). A real drift risk.
11. **Guard `migrate_api_usage`'s `DROP TABLE`.** It is the sharpest object in
    the repo and the reason 011 mattered; an explicit opt-in for destructive
    migrations would shrink the blast radius of every future mistake.
5. **Let the scheduler run, then re-measure props.** This is the gate on the
   digest now. The grading chain is built and verified; it needs games
   carrying props to reach `final`. Until then the prop numbers rest on two
   nights of basketball.
6. **Fix the import side effect on `backend.api.main`** — finding 4 above.
   Lazy app construction, or requiring `DATABASE_PATH` with no default, would
   both do it. Needs its own plan; the fix has to keep
   `uvicorn backend.api.main:app` working (`Dockerfile:44`).
7. **`nba_api_source.fetch_recent_games` is broken** and nothing depends on it
   for grading any more. `nba_api_source.py:157` passes `last_n_games=n` to
   `PlayerGameLog`, which has no such parameter, so it raises before any HTTP
   call and the fallback chain swallows it as a warning. Line 167 already
   slices `games[:n]`, so the fix is deleting the kwarg — but
   `stats.nba.com` also read-times-out from this machine, so fixing it buys
   nothing for data. It matters because it silently degrades **pre-game** prop
   analysis: `prop_pipeline.py:80-83` asks for recent form, always gets
   nothing, and logs a source failure rather than a bug. **Props are being
   analysed on season averages alone.**

### Calibration baseline (NBA, measured 2026-09-17)

Out-of-sample, 310 eval games, split 2026-01-31:

| | Brier |
|---|---|
| Out-of-sample | **0.2032** |
| In-sample control (`--in-sample`) | 0.1948 |
| Always predict 0.5 | 0.25 |

The out-of-sample penalty is only **0.0084** — the model is *not* badly
overfit. Its problem is bias, not variance.

The 0.5-0.7 range (45% of games) is now well calibrated: gaps **-0.006** and
**-0.008**, improved from +0.050 / -0.156 before the backfill. That is 008
working.

**Underdog overconfidence survived 008** and is the clearest open modelling
problem: the 0.2-0.3 bin predicts 0.263 and observes 0.067 (**+0.196**);
0.3-0.4 predicts 0.360, observes 0.250 (**+0.110**). Both bins are below
`min_bin` and flagged `NO`. **Directional, not established.**

Effective n is **263 (ICC 0.01) to 164 (ICC 0.05)**, not the raw 310 — games
sharing a team are not independent observations.

Only NBA has the volume (1023 finals). ncaab has 22 and mlb 13, both far under
`MIN_EVAL_GAMES = 200`.

Reproduce with:

```
.venv/Scripts/python.exe -m backend.analysis.calibration_report --sport nba --db 'C:\Users\mwill\Documents\mwilliams2733\sports_picks\sports_picks.db'
```

`--db` needs a **Windows** path; a git-bash `/c/...` path fails to open.
**If it ever reports 0.1836 again, the features are missing, not good** — that
is the pre-backfill number.

## Outstanding operator actions

1. ~~**Rotate the Odds API key.**~~ **DONE, fully closed 2026-09-17.** A live
   key had sat in plaintext in `uvicorn.log`. The key was rotated, `uvicorn.log`
   was truncated to 0 bytes, and the public git history was checked: the only
   `apiKey=` matches in tracked files are the redaction regex in
   `odds_api.py`, the `FAKEKEY123` test fixture, and an `abc1…` placeholder in
   plan 005. Nothing real was ever committed.

   **If you rotate again:** the key must reach **both** places the deployment
   reads it — `~/.secrets/shared.env` locally and `/opt/sports-picks/.env` on
   the server (`deploy/setup.sh:51`). Updating only the local one leaves the
   deployed copy failing on its next odds fetch.

   Note the repo is **public** (`github.com/mwilliams2733/sports_picks`).
2. **Do not set `digest.enabled: true` yet.** Still the recommendation, but
   the reason has moved twice and is now much narrower.

   Everything that *was* blocking it is done. The 007 report has been re-run
   against the backfilled production data. The dry-run preview has been run
   and it works — real matchups, real odds, real rationales, and the pre-008
   failure mode is gone (that preview led with five heavy favourites from
   −286 to −2336; the current one leads with a −110 at three stars). Props
   are now gradeable and measurable.

   **What blocks it now is sample size, not machinery.** The digest is
   majority props by row count, and every prop measurement rests on **two
   games**: 5-star beats 4-star by 25 points, which is encouraging, with an
   effective n of 10-20. The game model is separately still overconfident on
   underdogs by 11–20 points on n=15 and n=28 bins, and three of its features
   are constant.

   **The gate is: let the scheduler run, let props accumulate across dozens
   of games, then re-run `prop_calibration` and the 007 report.** The data is
   honest, the tools are built and verified, and the evidence is two nights
   deep.
3. The digest's dry-run preview writes `digest_preview.html` to the repo root;
   it is git-ignored.

### The dry-run preview was run on 2026-09-17 — and found the real blocker

The preview works. For 2026-05-26 it rendered one game pick (Over 217.1,
−110, ★★★☆☆, +7.3%) and five player props, with real matchups, odds and
rationales. The pre-008 failure mode is **gone**: that preview led with five
heavy favourites from −286 to −2336; this one leads with a −110 at three
stars. Run for *today* it correctly produced nothing, there being no games
dated 2026-09-17.

Nothing was sent. Four independent guards: `digest.enabled: false`, the
`dry_run_path` early return in `sender.send_email`, no `RESEND_API_KEY`, and
empty `recipients`. `digest.selector` is pure-read, so production was not
written to.

**But five of the preview's six rows are props, and props have never been
graded — nor can they be.** `pick_results` has **0 rows**. Three defects sit
behind that, and they compound:

1. **`grade_pick` grades every prop as a loss.** It has branches for
   moneyline, spread and over_under, then `else: return "loss", -1.0`
   (`grader.py:62-63`). `pick_type="prop"` hits the else.
   `scheduler.grade_pending_picks` routes *every* ungraded pick through it.
   This is **latent, not active** — `pick_results` is empty because the
   scheduler has not run, not because the code is safe. The next
   `morning_scout` marks all 82 props as losses.
2. **`PickModel` has no `prop_player` or `prop_market` columns.** `PaperPick`
   has both, which is why the PaperPick loop 30 lines below
   (`scheduler.py:290`) branches correctly to `grade_prop_pick` while the
   strategy-pick loop cannot. The obvious fix — copy that branch — has nothing
   to pass.
3. **`player_stats` holds 0 rows with `stat_type="game_log"`.**
   `grade_prop_pick` looks up exactly that, so even with the schema fixed it
   returns `None` for every prop. There is no outcome data to grade against.

The same file contains the correct pattern and the broken one, thirty lines
apart. That is the dead-wiring shape again: `grade_prop_pick` is correct, is
tested, and is simply never reached from the strategy path.

**Consequence for the digest decision.** Everything measured on 2026-09-17 —
Brier 0.2032, the bin table, the underdog finding — describes the **game**
model. The digest is majority props by row count, and prop confidence has
never been checked against a single outcome (39 of 82 props, 48%, sit at
maximum confidence). Keep `digest.enabled: false`: not because the digest is
broken, but because its dominant content is unvalidated and currently
ungradeable.

Planned as **`plans/010-grade-the-picks.md`**. **Task 1 is done**
(`6117d5e`): `grade_pick` now returns `None` for a type it has no branch for,
and all six call sites treat that as "leave ungraded". The latent corruption is
closed — props are correctly ungraded rather than wrongly graded.

**Task 3's spike is done too, and the answer is good: ESPN, free, no purchase
needed.** All three NBA sources currently fail, but only one of the three for a
reason that costs money:

- `NbaApiSource` passes `last_n_games` to `PlayerGameLog`, which has no such
  parameter — a `TypeError` thrown *before* any HTTP call, which masked
  everything else. Remove it and the real problem appears: `stats.nba.com`
  read-times-out from this machine (30s, 60s, 90s all failed). Unusable here.
- `BallDontLie` returns 401; it needs a paid key.
- `ESPN` 404s because the code asks `site.api.../nba/athletes`, a path that
  does not exist. The scoreboard and summary endpoints both return 200, and
  `summary?event=<id>` carries full player box scores — MIN, PTS, REB, AST,
  3PT, STL, BLK, TO — covering every NBA market in `MARKET_STAT_MAP`.

Two traps recorded in the plan. There is **no shared game id** (`Game` has no
`espn_id`), so events must be matched on date plus team abbreviations. And
**our dates run one day ahead of ESPN's** (UTC vs ET): of 8 sampled final NBA
games, 7 matched at offset −1 and 1 matched exactly, 0 missing. Matching on
exact date alone finds about 1 in 8 and looks like missing data rather than a
timezone bug.

## Scratch that did not survive

Under the session temp dir, now gone: the SDD ledger for the digest plan, the
per-task briefs and review packages, `sports_picks.backup.db` and
`sports_picks.work008.db` (DB copies), and the review diffs. None of it is
needed — the git history and `plans/` are the record.

`.superpowers/` in the repo holds the execution reports that were written:
`004-activity-feed-report.md`, `007-calibration-report.md`,
`008-team-stats-report.md` and `009-frontend-eslint-report.md`. It is
git-ignored, so those survive on disk but are not in history.

From the 2026-09-17 session, still on disk and worth keeping until you are
satisfied with live behaviour:

- `sports_picks.backup-20260916-231622.db` — the pre-backfill production
  database, git-ignored, verified `integrity_check: ok` with all 708 picks
  before anything was written.

Gone with the session temp dir, and not needed: the plan-010 working copies
(`props.db`, `box.db`, `chain.db`) and the scripts that drove them. Everything
they proved is in the commits and in `plans/010-grade-the-picks.md`. Note the
**production database was never written to by plan 010** beyond the two
columns a stray import migrated in (finding 4 above) — all 82 props are still
ungraded there, with `prop_player` and `prop_market` NULL.

## Your database — backfilled 2026-09-16, still ungraded

During plan 008 all analysis ran against copies and production was left empty.
It has now been backfilled deliberately:

| | before | after |
|---|---|---|
| `team_stats` distinct game_ids | 1 | **1058** |
| `elo_history` rows | 0 | **2116** |
| `picks` count / max id | 708 / 708 | **708 / 708** unchanged |
| `games` | 1664 | **1664** unchanged |
| `integrity_check` | ok | **ok** |

Backup taken first: `sports_picks.backup-20260916-231622.db` in the repo root,
git-ignored, verified `integrity_check: ok` with all 708 picks. Keep it until
you are satisfied with live behaviour.

`pace` / `offensive_rating` / `defensive_rating` are still present for exactly
**1** game — structurally refused, not fabricated.

**This changes live pick generation.** The model now trains on real
point-in-time features instead of a matrix that was almost entirely zeros, so
picks generated from here differ from those generated before. The calibration
report against production reproduces the copy exactly: **Brier 0.2032**
out-of-sample, 0.1948 in-sample control.

The daily pipeline keeps both tables current from here
(`update_team_stats_for_games` and `backfill_elo_history` in
`full_pipeline.py:234`), so this was a one-off.

### Current state after plan 010

| | value |
|---|---|
| `pick_results` | **0 rows** — nothing has ever been graded in production |
| props | 82, all with `prop_player` / `prop_market` **NULL** |
| games carrying props | 2, both still `status='scheduled'` with NULL scores |
| `player_stats` `stat_type='game_log'` | **0 rows** |
| `picks.prop_player` column | present, added by a stray import (finding 4) |

Plan 010 wrote nothing to production. Everything it proved ran against copies.

> **Superseded.** The ordered steps below covered plan 010 only. Plans 013 and
> 014 added more, and the authoritative list is now
> **"READ THIS BEFORE STARTING THE PIPELINE"** at the top of this file. Follow
> that one; these three are a subset of it.

To grade in production, in order (subset — see above):

1. Let the scheduler run so the two games reach `status='final'` with scores —
   or, if you want it now, `morning_scout` does box-score collection and
   grading in one pass.
2. `python -m backend.scripts.backfill_prop_fields --db <abs win path>` to fill
   `prop_player` / `prop_market` for the 82 existing props. New props carry
   them at generation time.
3. `python -m backend.analysis.prop_calibration --db <abs win path> --sport nba`

**Back it up first, and dry-run step 2.** Both were verified against copies
(82/82 resolved, resumable on re-run), but the rule stands.

## Historical note: state during plan 008

`sports_picks.db` had **708 picks** (max id 708). Eighteen test picks were
generated during a digest preview and deleted afterwards, with a
blast-radius check confirming no `pick_results` referenced them. All analysis
work ran against copies.

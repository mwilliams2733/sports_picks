# Session handoff — 2026-09-17

Everything below is recoverable from `git log` and `plans/`; nothing important
lives only in a chat transcript.

## Where things stand

`master` is at `72416da` and **pushed** — local, `origin/master` and the last
CI run all agree on that SHA. Test baseline: **549 passing backend, 0 failed**,
identical on Python **3.12 and 3.14**; frontend eslint 0/0, `tsc` clean,
vitest 15/15.

**CI is live** (`.github/workflows/ci.yml`) and is now the fastest way to get
that baseline — ~50s for all three jobs, versus ~1-4 min locally. Local command
is unchanged:

```
.venv/Scripts/python.exe -m pytest backend/tests -q
```

`master` is the repository's only branch and its default. The `main` scaffold
and all 13 merged `advisor/*` and `feature/*` branches were deleted on
2026-09-17; every commit they held is reachable from `master`.

### This session (2026-09-16 evening → 09-17)

The session crossed local midnight, so timestamps differ by source: the DB
backup is stamped `20260916-231622` (local) while the CI runs log `06:xxZ`
(UTC) the next day. Same session.

| Commit | What landed |
|---|---|
| `c292068` | `compute_historical_elo` no longer implements its own Elo replay |
| `9de96b8` | Calibration report stopped printing a false "LOWER BOUND" caveat |
| `fc9a3f4` | **Live `sports_picks.db` backfilled** — production had never had the 008 data |
| `9ff809e` | CI wired: backend matrix 3.12/3.14 + frontend lint/test/build |
| `e5e083d` | Dependencies pinned via `constraints.txt` |
| `72416da` | `sports_picks.egg-info` untracked |

Three findings from it worth carrying forward:

1. **Production was still running the pre-008 model.** Plan 008 fixed the code
   but ran its backfill against copies only, so the live DB kept `team_stats`
   for 1 game and 0 `elo_history` rows. Every pick generated between 008
   landing and 2026-09-17 came from the degenerate model. Fixed; see the
   database section below.
2. **The local venv was the stale environment, not CI.** `pyproject.toml` has
   floors only, and both `Dockerfile` and CI ran a bare `pip install -e .`, so
   both tracked latest while the venv sat months behind — starlette 0.52.1
   locally against 1.6.0 everywhere else. A local green said nothing about the
   image being built. `constraints.txt` now pins all four environments to one
   set, and the 3.12/3.14 matrix validates it.
3. **The deployed runtime had never run the test suite.** `Dockerfile:17` pins
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

### All nine plans are now merged

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

**None.** All nine are merged. `plans/README.md` has the full status table,
every finding that was *not* turned into a plan, and a "considered and
rejected" section so nothing gets re-audited.

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
2. **Do not set `digest.enabled: true` yet.** Still the standing
   recommendation, but the reasoning has moved on. The 007 report *has* now
   been re-run against the backfilled production database (numbers above), so
   the outstanding step is the **dry-run preview**, not the calibration
   measurement.

   What changed: the data defect is fixed and production now trains on real
   point-in-time features, so the ranking no longer surfaces the model's
   largest errors the way it did (a pre-008 preview's top 5 was five heavy
   favourites from −286 to −2336, one rated 99% where the market said 84.5%).
   What has not changed: the model is still overconfident on underdogs by
   11–20 points, and three of its features are constant. **The data is honest
   now; the model is not yet good.**
3. The digest's dry-run preview writes `digest_preview.html` to the repo root;
   it is git-ignored.

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

## Your database — backfilled 2026-09-16

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

## Historical note: state during plan 008

`sports_picks.db` had **708 picks** (max id 708). Eighteen test picks were
generated during a digest preview and deleted afterwards, with a
blast-radius check confirming no `pick_results` referenced them. All analysis
work ran against copies.

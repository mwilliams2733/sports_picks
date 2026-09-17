# Session handoff — 2026-09-16

Written mid-session before a restart. Everything below is recoverable from
`git log` and `plans/`; nothing important lives only in a chat transcript.

## Where things stand

`master` is at the **Merge plan 008** commit (`bb99490`) — the last of the nine.
Run `git log --oneline -15` to see the merge history. Test baseline on
`master`: **545 passing backend, 0 failed**; frontend **eslint 0/0, tsc clean,
vitest 15/15** (all verified)
(`.venv/Scripts/python.exe -m pytest backend/tests -q`, ~4.5 min).

Started this session at 360 tests.

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
| Live `sports_picks.db` | **untouched** — backfill ran against copies only |
| Fabrication check | `pace` / `offensive_rating` / `defensive_rating` still present for **exactly 1** game. Structurally refused, not fabricated. |

**Brier ROSE 0.1836 → 0.2020, and that is the honest result.** The old number
came from a model whose only non-zero coefficient was an end-of-season Elo
rating — constant per team and unknowable before tip-off. 0.2020 is the first
measurable score. The model is still badly calibrated: overconfident on
underdogs by 15-19 points.

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

## Open plans

**None.** All nine are merged. `plans/README.md` has the full status table,
every finding that was *not* turned into a plan, and a "considered and
rejected" section so nothing gets re-audited.

The natural next pieces of work, none of them planned yet:

1. ~~Reconcile `historical.py`'s post-game Elo convention.~~ **Done
   2026-09-16** — see residuals above. `EloRating` is still written by that
   path and still read as a fallback by the pick generator for the 14/239
   upcoming NBA games with no replayable history; deciding its fate is what
   remains of this thread.
2. Decide what to do about the two features that cannot be populated without
   possession data.
3. Re-run the 007 calibration report now that 008 unblocked it, and only then
   revisit `min_edge` and the ranking key.
4. Migrate `PaperTrading.tsx` to React Query — 009 left a documented
   suppression there naming this as the real fix.
5. Wire CI. `npm run lint` and the backend suite are both green, so this is
   cheap now. Note `npm ci` fails on a pre-existing `vite`/`vite-plugin-pwa`
   peer conflict; `npm ci --legacy-peer-deps` works and `Dockerfile:8-12`
   already documents it.

## Outstanding operator actions

1. **Rotate the Odds API key.** A live key sat in plaintext in `uvicorn.log`
   (git-ignored, never committed, so not a repo leak). Plan 005 stopped the
   recurrence; only rotation fixes the exposure. You said you would do this
   later — it is recorded in `plans/README.md` too.
2. **Do not set `digest.enabled: true` yet.** The digest works end to end and a
   dry-run preview rendered real picks with real rationales. 008 has now fixed
   the data defect that made the ranking surface the model's largest errors
   (a preview's top 5 was five heavy favourites from −286 to −2336, one rated
   99% where the market said 84.5%). But the model is still badly calibrated —
   Brier 0.2020, overconfident on underdogs by 15-19 points, and two of its
   five features remain constant. **Re-run the dry-run preview and the 007
   calibration report before enabling it**; the data is honest now, the model
   is not yet good.
3. The digest's dry-run preview writes `digest_preview.html` to the repo root;
   it is git-ignored.

## Scratch that did not survive

Under the session temp dir, now gone: the SDD ledger for the digest plan, the
per-task briefs and review packages, `sports_picks.backup.db` and
`sports_picks.work008.db` (DB copies), and the review diffs. None of it is
needed — the git history and `plans/` are the record.

`.superpowers/` in the repo holds the execution reports that were written:
`004-activity-feed-report.md` and `007-calibration-report.md`. It is git-ignored,
so those survive on disk but are not in history.

## Your database was not modified

`sports_picks.db` still has **708 picks** (max id 708). Eighteen test picks were
generated during a digest preview and deleted afterwards, with a
blast-radius check confirming no `pick_results` referenced them. All analysis
work ran against copies.

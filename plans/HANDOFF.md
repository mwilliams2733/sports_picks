# Session handoff — 2026-09-16

Written mid-session before a restart. Everything below is recoverable from
`git log` and `plans/`; nothing important lives only in a chat transcript.

## Where things stand

`master` is at the **Merge plan 007** commit. Run `git log --oneline -12` to see
the merge history. Test baseline on `master`: **~494 passing, 0 failed**
(`.venv/Scripts/python.exe -m pytest backend/tests -q`, ~4 min).

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
| — | Daily picks digest (six-task feature, dry-run by default) |

### In flight when the session ended

Two subagents were mid-run and did **not** survive the restart. Their branches
and commits DO survive in `.git`; only the worktree directories under
`AppData/Local/Temp/claude/...` are lost.

**`advisor/008-populate-team-stats` — 5 commits, reported DONE_WITH_CONCERNS,
NOT reviewed, NOT merged.** It came back just before the restart. Report:
`.superpowers/008-team-stats-report.md`. Branched from
`advisor/007-calibration-report` (now merged to master, so it will rebase or
merge cleanly). Commits:

```
11db971 fix(pipeline): keep elo_history current after the one-off backfill
553bc41 fix(picks): serve the pre-game Elo rating, not the end-of-history one
848dbea feat(scripts): backfill team stats and elo history from final games
399ac79 fix(picks): scope team-stat lookup to the game being predicted
81dc426 feat(pipeline): compute point-in-time team stats per game
```

Reports 520 passed (487 baseline + 33 new) and both required mutation proofs
run and failed as expected.

**Independently verified against its working DB copy before the restart:**

| | |
|---|---|
| `team_stats` distinct game_ids | **1 → 1058** |
| `elo_history` rows | **0 → 2116** |
| `picks` | still 708 (wrote none) |
| Live `sports_picks.db` | **untouched** — still 1 and 0 |
| Fabrication check | `pace` / `offensive_rating` / `defensive_rating` present for **exactly 1** game (the legacy game-1014 rows). The eight derivable stat types are at 1058. **It refused to fabricate**, as the plan required. |
| `rest_days` | now present for all 1058 — the feature that was structurally absent |

**The headline result is counter-intuitive and worth understanding before
reviewing:** the 99% predictions are gone (top bin now 2 games at 0.91, mass
sits 0.4-0.8), but **Brier rose from 0.1836 to 0.2020**. That is not a
regression. The old number came from a model whose only non-zero coefficient
was an end-of-season Elo rating — constant per team and unknowable before
tip-off. 0.2020 is the first honestly-measurable score. The model is still
badly calibrated: overconfident on underdogs by 15-19 points.

Elo decision: it wrote **pre-game** ratings, not post-game as `historical.py`
does, because both consumers read `elo_history[(team_id, game_id)]` as the
feature *for* that game. `calibrated_model.py` was left untouched. This was the
trap the plan flagged hardest and it resolved it the right way.

**Still needs a code review before merge.** What it flagged itself, worth
checking:

1. `backtesting.historical.compute_historical_elo` **still writes post-game
   ratings into the same column**. If it runs after this merges it will corrupt
   `elo_history` with the opposite convention. Reconcile before it next runs —
   this is the highest-risk loose end.
2. 007's `calibration_report.py` docstring documents this exact defect as a
   "known limitation". That paragraph goes stale the moment 008 merges.
3. 99 legacy orphan rows on game 1014 were left in place; it guarded the
   fallback rather than deleting them.
4. Two changes beyond the literal step list (`553bc41`, `11db971`), both in
   in-scope files, separately committed — the data fix exposed a train/serve
   Elo skew that had been hidden.
5. Two of five features remain structurally constant. The modelling choice —
   drop them, source possessions, or leave them defaulted — is still unmade and
   is the natural next decision.

**`advisor/009-frontend-eslint` — 0 commits.** It was still running `npm ci`
when the session ended. Nothing was done; re-dispatch from scratch.

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

### What to do about 008

Its outcomes were spot-checked (table above) but **its code was never
reviewed**. Recommended: dispatch a code review of
`git diff master..advisor/008-populate-team-stats` before merging, focused on

- the two mutation proofs it claims (strictly-before boundary; `game_id`
  scoping) — read the tests, not just the report
- whether the pre-game Elo convention is applied consistently everywhere, given
  `historical.py` still writes post-game into the same column
- the two out-of-step-list commits (`553bc41`, `11db971`) — documented
  deviations, judged on merit
- whether any test would pass against the pre-fix code

**The success-shaped failure was checked and did not occur.** A post-backfill
run that looked *excellent* would have suggested leakage; instead Brier got
*worse* (0.1836 → 0.2020) with a coherent explanation, and the fabrication
check came back clean. That is the shape of an honest result.

## Open plans

- `plans/008-populate-the-features-the-model-trains-on.md` — the important one.
  Nothing in production writes a `TeamStat` row, so the model trains on 1058
  games where 4 of 5 features are identically zero in 1057 of them. **This is
  the root cause of the model claiming 99% on NBA games where the market says
  84.5%**, and it blocks every calibration and `min_edge` question.
- `plans/009-frontend-eslint-errors.md` — 6 ESLint errors, 2 warnings. Five are
  fixable; `PaperTrading.tsx:70` is the React Query migration in disguise and
  the plan calls for a documented suppression, not a refactor.

`plans/README.md` has the full status table, every finding that was *not*
turned into a plan, and a "considered and rejected" section so nothing gets
re-audited.

## Outstanding operator actions

1. **Rotate the Odds API key.** A live key sat in plaintext in `uvicorn.log`
   (git-ignored, never committed, so not a repo leak). Plan 005 stopped the
   recurrence; only rotation fixes the exposure. You said you would do this
   later — it is recorded in `plans/README.md` too.
2. **Do not set `digest.enabled: true` yet.** The digest works end to end and a
   dry-run preview rendered real picks with real rationales. But the ranking
   currently surfaces the model's largest errors — a preview's top 5 was five
   heavy favourites from −286 to −2336, one of which the model rated 99% where
   the market said 84.5%. Fix the data (008) first.
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

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

**`advisor/008-populate-team-stats` — 6 commits, NOT reviewed, NOT merged.**
This is the valuable one. Branched from `advisor/007-calibration-report`
(it needs 007's report tool). Its commits:

```
11db971 fix(pipeline): keep elo_history current after the one-off backfill
553bc41 fix(picks): serve the pre-game Elo rating, not the end-of-history one
848dbea feat(scripts): backfill team stats and elo history from final games
399ac79 fix(picks): scope team-stat lookup to the game being predicted
81dc426 feat(pipeline): compute point-in-time team stats per game
```

It never reported back, so **no execution report exists** and its work is
unverified. Note `553bc41` suggests it resolved the pre-game/post-game Elo
question in the right direction, which is encouraging but not proof.

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

It has real work and no verification. Options, in order of preference:

1. **Review its diff** (`git diff master..advisor/008-populate-team-stats`) and
   decide. What to check is in `plans/008-...md`: both mutation proofs (the
   strictly-before boundary, and the `game_id` scoping), the pre/post-game Elo
   decision, and — critically — that `pace` and `net_rating` were **refused**
   rather than filled with plausible substitutes.
2. **Re-dispatch 008 from scratch** against the plan, discarding the branch.
3. Leave it; the branch keeps.

**The success-shaped failure to watch for:** if a post-backfill calibration run
looks *excellent*, that is more likely leakage than a fix. Three ways it
happens — a `<=` instead of `<` on the boundary, copying the existing Elo
precedent that writes the post-game rating into the row `calibrated_model`
reads as that game's feature, or fabricating the two unpopulatable features.

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

# Plan 022: An unknown starter is not an average one

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md` — unless a reviewer dispatched you and told you they
> maintain the index.
>
> **Drift check (run first)**:
> `git diff --stat 86742f3..HEAD -- backend/pipeline/scheduler.py backend/analysis/pitcher.py backend/analysis/variants/ensemble.py`
> If any in-scope file changed since this plan was written, compare the
> "Current state" excerpts against the live code before proceeding; on a
> mismatch, treat it as a STOP condition.

## Status

- **Priority**: P1
- **Effort**: S
- **Risk**: MED — this changes live MLB win probabilities for any game with exactly one announced starter. The change is small and the blast radius is known; the risk is that it is invisible without the before/after count Step 4 requires.
- **Depends on**: plans 018, 019, 021 (all merged)
- **Category**: bug
- **Planned at**: commit `86742f3`, 2026-09-23
- **Executor model**: `sonnet` (Agent tool `model` value). The production change is three lines, but the tests are written from prose and Task 4 requires a measurement whose result the executor must report rather than assert. Mid-tier is the floor. Not `haiku`.

## Why this matters

`pitcher_skill_score(era, k9)` returns **0.5 for a league-average pitcher and
0.5 for a pitcher it knows nothing about**. Its only production caller,
`fetch_pitcher_scores_for_date`, computes each side independently and
substitutes that 0.5 upstream, so it **never emits `None`**.

Every consumer downstream was written to treat an unknown starter as absent
rather than average, and every one of those guards is dead:

| consumer | guard | reachable in production? |
|---|---|---|
| `ensemble.pitcher_logit_shift` | `if h is None or a is None: return 0.0` | **no** |
| `sport_specific._pitcher_score` | `if h is None or a is None: return 0.5` | **no** |
| `strategy._build_factors` | `if ... is not None and ... is not None` | **no** |
| `scheduler._persist_pitcher_scores` | `if value is None: continue` | **no** |

The harmful case is one starter announced and the other not. The shift then
computes `real - 0.5` and prices a known ace against a phantom — exactly what
`pitcher_logit_shift`'s docstring says it prevents. Measured on the first
persisted day, 2026-09-23: 4 of 32 rows were exactly 0.5, and all four fell
in two games where *both* sides were unknown, so the error cancelled. The
mixed case did not occur that day. It is not prevented.

It also poisons the measurement plan 021 exists to enable. The
`team_stats` rows with `stat_type='pitcher_skill_score'` cannot distinguish
"two league-average starters" from "we did not know", and that was an eighth
of day one.

After this plan the producer emits `None` for a side it has no data for, all
four guards become live, and the stored rows are unambiguous.

## Current state

- `backend/analysis/pitcher.py` — `pitcher_skill_score(era, k9) -> float`.
  A pure function. Its docstring says "Missing inputs -> 0.5 (neutral) so MLB
  picks still generate when pitchers haven't been announced." **That rationale
  is now false**: picks still generate because the consumers skip the term,
  not because the score is neutral.
- `backend/pipeline/scheduler.py:658-690` — `fetch_pitcher_scores_for_date`,
  the **only** production caller (verified: `grep -rn "pitcher_skill_score" backend --include=*.py`
  outside tests shows the definition, this caller, and consumers reading the
  `TeamStats` attribute).
- `backend/collectors/mlb_stats.py` — `fetch_pitcher_recent(...)` returns
  either `None` or a dict with float `era_recent` and `k9_recent`
  (`if not eras or ip_total <= 0: return None`). So "no data for this side"
  is exactly `stats is None`; there is no partial dict to worry about.
- `backend/pipeline/pick_generator.py:414-418` — copies the values onto
  `TeamStats` with `ps.get("home")`, so a `None` propagates naturally with no
  change needed.
- `backend/tests/test_pitcher.py` — tests the pure function directly,
  including its 0.5-for-missing behaviour. Those tests stay valid: this plan
  does not change `pitcher_skill_score` itself.

The producer today, `scheduler.py:676-687`:

```python
            home_stats = await collector.fetch_pitcher_recent(home_id, season=target_date.year) if home_id else None
            away_stats = await collector.fetch_pitcher_recent(away_id, season=target_date.year) if away_id else None
            out[(home_abbr, away_abbr)] = {
                "home": pitcher_skill_score(
                    era=home_stats["era_recent"] if home_stats else None,
                    k9=home_stats["k9_recent"] if home_stats else None,
                ),
                "away": pitcher_skill_score(
                    era=away_stats["era_recent"] if away_stats else None,
                    k9=away_stats["k9_recent"] if away_stats else None,
                ),
            }
```

Conventions to match:
- **A missing input is absent, never defaulted to a neutral value.** This is
  the repo's standing rule; see `kelly.sizing_fraction` ("An absent input is
  skipped, never defaulted") and `confidence.calculate_confidence`
  ("absent evidence rather than a vote"). This plan applies it to one more
  producer.
- A constant or behaviour change carries the measurement behind it in a
  comment.
- A backfill or cleanup runs against a **copy** first and leaves production to
  the operator. See the plan 008 and 013 rows in `plans/README.md`.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Pitcher tests | `<python> -m pytest backend/tests/test_pitcher.py backend/tests/test_ensemble_pitcher.py backend/tests/test_pitcher_persistence.py backend/tests/test_unknown_starter.py -q` | all pass |
| Scheduler tests | `<python> -m pytest backend/tests/test_morning_slate.py backend/tests/test_mlb_integration.py backend/tests/test_mlb_stats.py backend/tests/test_pick_generator.py -q` | all pass |
| Full suite | `<python> -m pytest backend/tests -q` | all pass (1472 at `86742f3`) |
| Cleanup script, DRY RUN only | `<python> -m backend.scripts.drop_unknown_pitcher_rows --db <abs windows path>` | prints what it WOULD delete, deletes nothing |

`--db` takes a **Windows** path; a git-bash `/c/...` path fails to open.

## Scope

**In scope**:
- `backend/pipeline/scheduler.py` (the producer)
- `backend/analysis/pitcher.py` (docstring only — correct the stale rationale)
- `backend/scripts/drop_unknown_pitcher_rows.py` (create)
- `backend/tests/test_unknown_starter.py` (create)

**Out of scope**:
- **All four consumer guards.** They are already correct; this plan makes them
  reachable. Do not touch `ensemble.pitcher_logit_shift`,
  `sport_specific._pitcher_score`, `strategy._build_factors`, or
  `scheduler._persist_pitcher_scores`.
- `pitcher_skill_score`'s **behaviour**. It stays a pure function returning
  0.5 for `None` inputs; the fix is to stop calling it that way. Changing it
  would break `test_pitcher.py` for no gain.
- `backend/collectors/mlb_stats.py` — its `None` return is already the right
  contract.
- **Running the cleanup against production.** The executor runs the dry run
  only. The live delete is the operator's, after reviewing the dry-run output.

## Git workflow

- Branch: the worktree's own; do not create one.
- Conventional commits, e.g. `fix(mlb): an unannounced starter is unknown, not league average`
- Do NOT push or open a PR.
- End commit messages with:
  `Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>`

## Steps

### Step 1: The producer emits None for a side it has no data for

In `scheduler.py`, replace the `out[...]` assignment with:

```python
            def _score(stats):
                """The side's skill score, or None when no starter is known.

                None, not 0.5: `pitcher_skill_score` returns 0.5 both for a
                league-average pitcher and for one it has no data on, and the
                four consumers downstream all branch on None to mean
                "unknown". Substituting 0.5 here made every one of those
                branches dead, so a game with ONE announced starter priced
                the known pitcher against a phantom average one -- which is
                the case `ensemble.pitcher_logit_shift` documents itself as
                preventing. Measured 2026-09-23, the first day these were
                persisted: 4 of 32 stored rows were exactly 0.5.
                """
                if stats is None:
                    return None
                return pitcher_skill_score(era=stats["era_recent"],
                                           k9=stats["k9_recent"])

            out[(home_abbr, away_abbr)] = {"home": _score(home_stats),
                                           "away": _score(away_stats)}
```

Keep the existing `skipped` counter and its warning untouched.

**Verify**: `<python> -m pytest backend/tests/test_mlb_integration.py backend/tests/test_mlb_stats.py backend/tests/test_morning_slate.py -q` → all pass.

### Step 2: Correct the stale rationale in the pure function

In `backend/analysis/pitcher.py`, amend `pitcher_skill_score`'s docstring.
Keep the behaviour. Replace the sentence

> Missing inputs -> 0.5 (neutral) so MLB picks still generate when pitchers
> haven't been announced.

with wording that says: missing inputs still return 0.5, but **no production
caller passes them any more** — `scheduler.fetch_pitcher_scores_for_date`
emits `None` for a side with no data, because 0.5 is indistinguishable from a
genuinely league-average starter and the consumers use `None` to mean
unknown. Note that picks still generate for an unannounced starter because
each consumer skips the pitcher term, not because the score is neutral.

This is a comment-only edit; change no code in that file.

**Verify**: `<python> -m pytest backend/tests/test_pitcher.py -q` → all pass (behaviour unchanged).

### Step 3: Tests

Create `backend/tests/test_unknown_starter.py`. Module docstring states the
bug: every consumer's `None` guard was dead because the producer substituted
0.5, and a game with one announced starter priced it against a phantom.

The producer is `async` and hits the network, which the suite blocks, so fake
the collector. `test_mlb_integration.py` and `test_mlb_stats.py` show the
established shape — read one before writing. Monkeypatch
`backend.collectors.mlb_stats.MLBStatsCollector` (or the name
`scheduler.fetch_pitcher_scores_for_date` imports) so `fetch_schedule`
returns one game and `fetch_pitcher_recent` returns a dict for one pitcher id
and `None` for the other.

Tests (names as given):

1. `test_a_side_with_no_data_is_none_not_a_half` — one side's
   `fetch_pitcher_recent` returns `None`; assert that side's value **is
   `None`**, and assert explicitly `!= 0.5`.
2. `test_a_side_with_data_still_scores` — the other side returns a real dict;
   assert its value is a float strictly between 0 and 1 and not `None`.
3. `test_both_sides_unknown_are_both_none` — both return `None`; assert both
   values are `None`.
4. `test_a_missing_probable_pitcher_id_is_none` — `home_probable_pitcher_id`
   absent from the schedule payload; assert `None` without
   `fetch_pitcher_recent` being called for that side.
5. `test_the_shift_declines_when_one_starter_is_unknown` — the integration
   guard. Build a `GameData` whose `home_stats.pitcher_skill_score` is a real
   score and whose away side is `None`; assert
   `ensemble.pitcher_logit_shift(game) == 0.0`. **This is the test that would
   have caught the bug**, so say so in its docstring.
6. `test_persistence_skips_an_unknown_side` — call
   `scheduler._persist_pitcher_scores` with `{"home": 0.7, "away": None}` on a
   seeded in-memory database; assert exactly one `TeamStat` row, for the home
   team. (Model the database setup on `test_pitcher_persistence.py`.)

**Mutation check, required** (repo rule: a test that still passes when the
implementation is broken is not a test): restore the old behaviour by making
`_score` return `pitcher_skill_score(None, None)` instead of `None`; tests 1,
3 and 6 must fail. Restore and report which failed and how.

**Verify**: `<python> -m pytest backend/tests/test_unknown_starter.py -q` → 6 passed.

### Step 4: Measure what the change does to live probabilities

This is the point of the MED risk rating. Write a short throwaway snippet (do
not commit it) that, for **today's** MLB games in the production database,
reports how many games have exactly one side with a stored
`pitcher_skill_score` of exactly 0.5, and therefore would have had a shift
applied against a phantom under the old behaviour.

Report, in your final report:
- the number of today's MLB games with **both** sides at exactly 0.5,
- the number with **exactly one** side at 0.5,
- and for the second group, the logit shift the old code would have applied
  (`real - 0.5`) and the probability change that implies from a 0.50 base.

Read the production database **read-only** (`mode=ro` URI). Write nothing.

**Verify**: the numbers appear in your report. If the "exactly one side" count is zero, say so plainly — that is the expected answer for 2026-09-23 and means the bug has not yet cost a real pick.

### Step 5: A cleanup script for the ambiguous rows already stored

Create `backend/scripts/drop_unknown_pitcher_rows.py`.

It finds `TeamStat` rows with `stat_type='pitcher_skill_score'` and
`value = 0.5` exactly, and deletes them — because a genuine 0.5 requires an
ERA of exactly 4.00 **and** a K/9 of exactly 8.5, which is not a real
measurement, while an unknown starter produces exactly 0.5 every time.

Requirements:
- **Dry run by default.** It prints what it would delete and exits without
  writing. A `--apply` flag performs the delete. This mirrors how backfills
  in this repo are run (see the plan 008 and 013 rows in `plans/README.md`).
- Print, per affected game: the game id, date, the two team abbreviations,
  and whether one or both sides are at 0.5.
- Print a total, and refuse with a non-zero exit if `--apply` is passed
  without the database having been backed up in the same invocation — simplest
  honest form: require an explicit `--i-have-a-backup` flag alongside
  `--apply`, and say in the help text which backup file the operator should
  have taken.
- `main(argv)` returning an int exit code, `--db` argument, in the shape the
  other scripts in `backend/scripts/` use — read one first.

Add tests to `backend/tests/test_unknown_starter.py`:

7. `test_cleanup_dry_run_deletes_nothing` — seed two 0.5 rows and one real
   row; run without `--apply`; assert all three rows survive.
8. `test_cleanup_apply_removes_only_the_exact_halves` — same fixture, run with
   `--apply --i-have-a-backup`; assert the two 0.5 rows are gone and the real
   row survives.

**Verify**: `<python> -m pytest backend/tests/test_unknown_starter.py -q` → 8 passed. Then run the dry run against the production database and paste its output; it must report 4 rows across 2 games and delete nothing.

### Step 6: Full suite

**Verify**: `<python> -m pytest backend/tests -q` → all pass. Report exact counts against the 1472 baseline.

## Done criteria

- [ ] Full suite exits 0, count ≥ 1480
- [ ] `grep -n "pitcher_skill_score(era=None\|era=.*if .*else None" backend/pipeline/scheduler.py` → no matches (the producer no longer passes None into the scorer)
- [ ] `grep -n "def _score" backend/pipeline/scheduler.py` → present inside `fetch_pitcher_scores_for_date`
- [ ] `git diff 86742f3..HEAD -- backend/analysis/pitcher.py` contains docstring lines only
- [ ] `git diff --stat 86742f3..HEAD -- backend/analysis/variants/ensemble.py backend/analysis/variants/sport_specific.py backend/analysis/strategy.py` is empty
- [ ] Mutation check performed, with the failing test names reported
- [ ] Step 4's three numbers are in the report
- [ ] The dry run was run against production, output pasted, and **nothing was deleted**
- [ ] No files outside the in-scope list are modified (`git status`)

## STOP conditions

Stop and report back (do not improvise) if:

- `fetch_pitcher_scores_for_date` no longer matches the excerpt.
- `fetch_pitcher_recent` can return a dict with a `None` `era_recent` or
  `k9_recent` — then `stats is None` is not the right test for "no data" and
  the plan's premise needs revisiting.
- Any consumer guard turns out NOT to handle `None` (check all four in the
  table under "Why this matters" before Step 1).
- A pre-existing test outside the in-scope files fails after Step 1. That
  would mean some test depended on the 0.5 substitution, which is a real
  signal about a consumer this plan did not map.
- You are tempted to run the cleanup with `--apply` against production.
- `import backend` resolves outside the worktree.

## Maintenance notes

- **The operator still has to run the cleanup.** After this merges, take a
  backup (`sqlite3 .backup`, not `cp` — the live file has a WAL), review the
  dry-run output, then run with `--apply --i-have-a-backup`.
- Whoever performs the plan 021 measurement should no longer need to filter
  exact-0.5 rows once the cleanup has run and this producer is live, but
  should verify the filter is unnecessary rather than assume it.
- This is the fourth instance of the same shape in this repo: a guard written
  for a case the producer never generates. `kelly.sizing_fraction`,
  `confidence.calculate_confidence` and
  `ensemble._count_agreeing_models` all carry notes about it. If a fifth
  appears, consider a test that asserts each producer's output domain
  actually reaches its consumers' branches.
- A reviewer should check that no consumer file changed, and that Step 4's
  measurement was reported rather than asserted.

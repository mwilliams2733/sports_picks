# Plan 023: A daily check that the pitcher rows landed

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md` — unless a reviewer dispatched you and told you they
> maintain the index.
>
> **Drift check (run first)**:
> `git diff --stat c213ddf..HEAD -- backend/scripts/check_digest.py scripts/check_digest.ps1 scripts/register_digest_check_task.ps1 backend/pipeline/scheduler.py`
> Expected: empty. On a mismatch, treat it as a STOP condition.

## Status

- **Priority**: P2
- **Effort**: S
- **Risk**: LOW — read-only; it writes one log file and touches neither the database nor the scheduler.
- **Depends on**: plans 019, 021, 022 (all merged)
- **Category**: dx
- **Planned at**: commit `c213ddf`, 2026-09-23
- **Executor model**: `sonnet` (Agent tool `model` value). The module is short but its classification logic and tests are written from prose, and it must reuse two helpers from a sibling module rather than copy them. Mid-tier is the floor. Not `haiku`.

## Why this matters

Three changes landed on 2026-09-23 whose effect is only visible in the
morning: the scout fetches pitcher scores (019), persists them (021), and
records `None` rather than a phantom 0.5 for an unknown starter (022).
Nothing checks that any of it keeps working. The scheduler already logs the
evidence — `MLB pitcher scores: N game(s), M stat row(s) recorded` — but a
log line nobody reads is not a check.

The specific regression worth catching is **a row stored at exactly 0.5**.
Plan 022 made that impossible: a real pitcher lands on 0.5 only with an ERA
of exactly 4.00 and a K/9 of exactly 8.5. So any such row means the producer
started substituting a neutral value again, which silently prices announced
pitchers against phantoms and poisons the measurement plan 021 exists to
enable. Before 022 there were four such rows; there are now zero.

This plan mirrors the existing digest health check exactly, including its
09:18 local trigger, which is already reasoned out for both daylight-saving
regimes. It changes nothing that works today.

## Current state

- `backend/scripts/check_digest.py` — the model to follow. Read it before
  writing anything. It exposes two helpers this plan must **reuse, not
  copy**: `rotate(path, *, max_bytes, keep)` and
  `append_entry(path, body, *, exit_code, max_bytes, keep)`. It defines an
  `Outcome` enum and a frozen `Result` dataclass with an `exit_code` property
  returning `0 if self.ok else 1`. Its `main(argv)` takes `--log`, `--db`,
  `--date`, `--json`, `--health-log`.
- `scripts/check_digest.ps1` — the launcher. Runs the module, prints its
  output, and appends a `LAUNCH FAILED` entry only when Python produced no
  verdict of its own. Exits with the module's code.
- `scripts/register_digest_check_task.ps1` — registers
  `sports_picks digest check` daily at 09:18 local, Interactive principal,
  `RunLevel Limited`, `-StartWhenAvailable`, idempotent. **No elevation
  needed**; verified against the live task.
- `backend/pipeline/scheduler.py` — `mlb_pitcher_scores` logs
  `MLB pitcher scores: %d game(s), %d stat row(s) recorded` on its success
  path. `PITCHER_STAT_TYPE = "pitcher_skill_score"` is the stat type written.
- `backend/tests/test_check_digest.py` — the test pattern: small helpers fed
  synthetic log lines, asserting on classification rather than on formatting.

**Timing, already solved — do not re-derive it.** This machine is on Arizona
time (no daylight saving); the pipeline scheduler is Eastern. The morning
scout runs 8 ET with retries at 9 and 10 ET, so the last retry is 07:00 local
in summer and 08:00 local in winter. **09:18 local is after everything in both
regimes**, which is why the digest check uses it. Use the same trigger time.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| New tests | `<python> -m pytest backend/tests/test_check_pitcher_rows.py -q` | all pass |
| Sibling still green | `<python> -m pytest backend/tests/test_check_digest.py backend/tests/test_health_log_rotation.py -q` | all pass |
| Full suite | `<python> -m pytest backend/tests -q` | all pass (1480 baseline) |
| Run the check by hand | `<python> -m backend.scripts.check_pitcher_rows --db C:\Users\mwill\Documents\mwilliams2733\sports_picks\sports_picks.db` | prints a verdict, exits 0 or 1 |

## Scope

**In scope**:
- `backend/scripts/check_pitcher_rows.py` (create)
- `scripts/check_pitcher_rows.ps1` (create)
- `scripts/register_pitcher_check_task.ps1` (create)
- `backend/tests/test_check_pitcher_rows.py` (create)

**Out of scope**:
- `backend/scripts/check_digest.py` and its two `.ps1` files — **do not
  modify them**. Import the two helpers; changing a working health check to
  accommodate a new one is how both end up broken.
- `backend/pipeline/scheduler.py` — it already logs what this reads.
- **Registering the scheduled task.** The executor writes the registration
  script and does not run it. Registering from a worktree is wrong, and the
  reviewer performs it against the main checkout after merge.

## Git workflow

- Conventional commits, e.g. `feat(ops): a daily check that the pitcher rows landed`
- Do NOT push or open a PR.
- End commit messages with:
  `Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>`

## Steps

### Step 1: The check module

Create `backend/scripts/check_pitcher_rows.py`. Read-only with respect to the
database and `scheduler.log`; it writes only its own health log.

Module docstring must state what it answers and why the 0.5 case is the point
(see "Why this matters"), in the register the sibling module uses.

Reuse from `check_digest`: `from backend.scripts.check_digest import rotate, append_entry`.
Define `HEALTH_LOG = os.path.join(REPO, "pitcher_health.log")` — its own file,
so a pitcher problem never scrolls a digest problem out of view.

Classify into an `Outcome` enum and a frozen `Result` dataclass with the same
`exit_code` property shape as the sibling:

| outcome | condition | ok? |
|---|---|---|
| `NO_MLB_TODAY` | no MLB games on the target date | yes |
| `ROWS_WRITTEN` | at least one row for today's MLB games, none at 0.5 | yes |
| `NO_STARTERS_ANNOUNCED` | MLB games exist, the scout's log line for today is present, and it reports 0 stat rows | yes |
| `SCOUT_NEVER_RAN` | MLB games exist and there is **no** `MLB pitcher scores` line for today | **no** |
| `AMBIGUOUS_HALVES` | **any** stored row for today is exactly 0.5 | **no** |

`AMBIGUOUS_HALVES` outranks the others: if rows exist and any is 0.5, report
that, whatever else is true. Its detail text must name plan 022 and say the
producer has started substituting a neutral value again.

The log-line search must be **date-aware in the same way the sibling's is** —
read `check_digest.digest_lines` and `slate_line` to see how it decides a line
belongs to the target date, and follow it. Log timestamps are local while
`et_today()` is Eastern; the sibling already handles that and you must not
invent a second convention.

`main(argv)` returning an int, with `--log`, `--db`, `--date`, `--json`,
`--health-log`, matching the sibling's argument names.

**Verify**: `<python> -m backend.scripts.check_pitcher_rows --db C:\Users\mwill\Documents\mwilliams2733\sports_picks\sports_picks.db --json` → valid JSON, exits 0 or 1. Paste the output. Note: today (2026-09-23) has 14 games with rows and 0 at 0.5, but the scheduler was restarted after those rows were written, so the log line may be absent — say which outcome you got and why.

### Step 2: The launcher

Create `scripts/check_pitcher_rows.ps1`, modeled on `scripts/check_digest.ps1`:
absolute paths, the venv interpreter, `2>&1` capture, print the output, exit
the module's code, and append a `LAUNCH FAILED` entry to `pitcher_health.log`
**only** when the module produced no dated verdict of its own. Its docstring
should say what the exit code means and how to read `LastTaskResult`.

**Verify**: `powershell -ExecutionPolicy Bypass -File scripts\check_pitcher_rows.ps1` → prints a verdict and exits 0 or 1; `pitcher_health.log` gains exactly one entry.

### Step 3: The registration script

Create `scripts/register_pitcher_check_task.ps1`, modeled on
`register_digest_check_task.ps1`. Task name `sports_picks pitcher check`,
daily at **09:18** local, Interactive principal, `RunLevel Limited`,
`-MultipleInstances IgnoreNew`, `-AllowStartIfOnBatteries`,
`-StartWhenAvailable`, idempotent.

Its docstring must state the timing reasoning **by reference**, not by
re-deriving it: the same 09:18 local as the digest check, after the 8 ET scout
and both retries in both daylight-saving regimes, and that no elevation is
needed.

**DO NOT RUN IT.** Registering from a worktree points the task at a directory
that will be deleted. The reviewer registers it after merge.

**Verify**: `powershell -NoProfile -Command "& { . ./scripts/register_pitcher_check_task.ps1 -WhatIf }"` is NOT required and may not work; instead verify by reading the file and confirming with `grep -n "09:18\|sports_picks pitcher check\|StartWhenAvailable" scripts/register_pitcher_check_task.ps1` that all three appear.

### Step 4: Tests

Create `backend/tests/test_check_pitcher_rows.py`, modeled on
`test_check_digest.py`. Feed synthetic log lines and a seeded in-memory
database; assert on `Outcome`, not on formatting.

Tests (names as given):

1. `test_no_mlb_games_today_is_healthy` — no games; `NO_MLB_TODAY`, ok.
2. `test_rows_written_is_healthy` — games plus rows, none at 0.5;
   `ROWS_WRITTEN`, ok.
3. `test_a_row_at_exactly_one_half_is_an_alarm` — one row at 0.5 among
   several good ones; `AMBIGUOUS_HALVES`, **not** ok, and the detail mentions
   plan 022. **This is the test the plan exists for.**
4. `test_ambiguous_halves_outranks_rows_written` — rows exist AND one is 0.5;
   assert the outcome is `AMBIGUOUS_HALVES`, not `ROWS_WRITTEN`.
5. `test_no_scout_line_with_games_scheduled_is_an_alarm` — games, no log
   line, no rows; `SCOUT_NEVER_RAN`, not ok.
6. `test_a_scout_line_reporting_zero_rows_is_not_an_alarm` — games, a log
   line saying `16 game(s), 0 stat row(s) recorded`, no rows;
   `NO_STARTERS_ANNOUNCED`, ok. (Nobody announced a starter; that is baseball,
   not a fault.)
7. `test_yesterdays_scout_line_is_not_todays` — a log line dated yesterday
   with games today; must classify `SCOUT_NEVER_RAN`, not healthy.
8. `test_the_health_log_gets_one_entry_per_run` — run `main` twice against a
   tmp health log; assert exactly two dated entries.

**Mutation check, required** (repo rule: a test that still passes when the
implementation is broken is not a test): change the 0.5 detection to
`value < 0` (a condition no row meets); tests 3 and 4 must fail. Restore and
report which failed and how.

**Verify**: `<python> -m pytest backend/tests/test_check_pitcher_rows.py -q` → 8 passed.

### Step 5: Full suite

**Verify**: `<python> -m pytest backend/tests -q` → all pass. Report exact counts against the 1480 baseline.

## Done criteria

- [ ] Full suite exits 0, count ≥ 1488
- [ ] `grep -n "from backend.scripts.check_digest import" backend/scripts/check_pitcher_rows.py` → shows `rotate` and `append_entry` imported, not redefined
- [ ] `grep -c "def rotate\|def append_entry" backend/scripts/check_pitcher_rows.py` → `0`
- [ ] `git diff --stat c213ddf..HEAD -- backend/scripts/check_digest.py scripts/check_digest.ps1 scripts/register_digest_check_task.ps1` is empty
- [ ] The module ran by hand against the production database, output pasted, outcome explained
- [ ] The launcher ran and appended exactly one entry to `pitcher_health.log`
- [ ] Mutation check performed, failing test names reported
- [ ] The registration script was **not** executed
- [ ] No files outside the in-scope list are modified (`git status`)

## STOP conditions

Stop and report back (do not improvise) if:

- `check_digest.rotate` or `check_digest.append_entry` do not exist with the
  signatures in "Current state".
- The scheduler's log line format differs from
  `MLB pitcher scores: %d game(s), %d stat row(s) recorded`.
- `test_check_digest.py` or `test_health_log_rotation.py` fail at any point —
  that means you changed shared behaviour.
- You are tempted to edit `check_digest.py` to make reuse easier. Report what
  is in the way instead.
- You are tempted to run the registration script.
- `import backend` resolves outside the worktree.

## Maintenance notes

- **The reviewer must register the task after merge**, from the main checkout:
  `powershell -ExecutionPolicy Bypass -File scripts\register_pitcher_check_task.ps1`
  then confirm with
  `(Get-ScheduledTaskInfo 'sports_picks pitcher check').LastTaskResult`.
- `pitcher_health.log` is gitignored by the same rule that covers
  `digest_health.log`; confirm rather than assume.
- If a fourth morning check ever appears, the three launchers and three
  registration scripts are worth collapsing into one parameterised pair. Two
  is not yet worth it.
- The `AMBIGUOUS_HALVES` guard is the only one that catches a *silent* model
  regression. If plan 022's producer fix is ever reverted or refactored, this
  is what fires. A reviewer should confirm its test actually fails under
  mutation rather than trusting the report.

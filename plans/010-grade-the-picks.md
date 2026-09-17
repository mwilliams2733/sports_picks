# Plan 010: Make picks gradeable, then grade them

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan in
> `plans/README.md`.
>
> **For agentic workers:** REQUIRED SUB-SKILL: use
> `superpowers:subagent-driven-development` (recommended) or
> `superpowers:executing-plans`. Steps use checkbox (`- [ ]`) syntax.
>
> **Drift check (run first)**:
> `git diff --stat 25bdb09..HEAD -- backend/pipeline/grader.py backend/pipeline/scheduler.py backend/pipeline/prop_pipeline.py backend/models.py`
> If any in-scope file changed since this plan was written, compare the
> "Current state" excerpts against the live code before proceeding; on a
> mismatch, treat it as a STOP condition.

**Goal:** Make prop picks gradeable and graded, so prop confidence can be
measured against outcomes instead of asserted.

**Architecture:** Four sequential layers. Stop the silent mis-grading first
(safety), then give `PickModel` the fields grading needs (schema), then collect
the post-game box scores that do not exist today (data), then route props to
the correct grader and measure (wiring + measurement). Each layer is useless
without the one before it, so the order is not negotiable.

**Tech Stack:** Python 3.14 local / 3.12 production, SQLAlchemy 2.0, pytest,
SQLite.

**Spec:** none — this plan was derived directly from the 2026-09-17
investigation recorded in `plans/HANDOFF.md` ("The dry-run preview was run on
2026-09-17 — and found the real blocker"). That section is the spec; read it
first.

## Status

- **Priority**: P1
- **Effort**: L
- **Risk**: MEDIUM (Task 3 depends on an external data source that may not
  exist; see STOP conditions)
- **Depends on**: none
- **Category**: bug + missing capability
- **Planned at**: commit `25bdb09`, 2026-09-17
- **Blocks**: enabling `digest.enabled`, and any ROI or prop-calibration claim

## Global Constraints

- Python floor is `>=3.12` (`pyproject.toml`); CI runs **3.12 and 3.14** and
  both must pass.
- Dependencies are pinned in `constraints.txt`. Adding a dependency means
  regenerating it — see the Environment section of `plans/HANDOFF.md`.
- **Never run a migration or backfill against `sports_picks.db` directly.**
  Copy it first. The live DB currently holds 708 picks and 82 props.
- Grading runs inside the scheduler. **It must never raise** — a grading
  failure must not take down pick generation.
- Tests must be mutation-proved: break the implementation and confirm the test
  fails before calling it a guard.

## Why this matters

The daily digest is majority player props by row count, and **no prop has ever
been graded**. `pick_results` has 0 rows. Three defects compound:

1. **`grade_pick` grades every prop as a loss.** `grader.py:34-72` branches on
   `moneyline`, `spread`, `over_under`, then falls through to
   `else: return "loss", -1.0`. `pick_type="prop"` hits the else.
   `scheduler.grade_pending_picks` (`scheduler.py:256-274`) routes *every*
   ungraded pick through it.
2. **`PickModel` cannot describe a prop.** It has no `prop_player` or
   `prop_market` column. `PaperPick` has both, which is why the PaperPick loop
   thirty lines below (`scheduler.py:290`) branches correctly to
   `grade_prop_pick` while the strategy loop cannot.
3. **There is no post-game player data.** `player_stats` holds 390
   `season_avg` rows and **0** `game_log` rows. `grade_prop_pick` looks up
   `stat_type="game_log"` filtered by `game_date`, so it returns `None` for
   every prop even if 1 and 2 were fixed.

Defect 1 is **latent, not active**. `pick_results` is empty because the
scheduler has not run, not because the code is safe. The next `morning_scout`
marks all 82 props as losses at -1.0 payout, and every ROI and calibration
number computed afterwards is silently wrong. **This is the urgent part.**

Note the shape: `grade_prop_pick` is correct, is tested
(`test_grader.py:58-64`), and is simply never reached from the strategy path.
Same file, thirty lines apart, correct pattern and broken one. This is the
dead-wiring pattern from `plans/HANDOFF.md` for the fourth time in this repo.

## Current state (verified 2026-09-17 at `25bdb09`)

```
pick_results rows                      0
picks (pick_type='prop')              82
  of which confidence=5               39   (48%)
player_stats stat_type='season_avg'  390
player_stats stat_type='game_log'      0
```

```python
>>> from backend.pipeline.grader import grade_pick
>>> grade_pick('prop', 'Dean Wade Over 0.5 3-Pointers', 110, 105, -200)
('loss', -1.0)
>>> grade_pick('prop', 'anything at all', 999, 0, -110)
('loss', -1.0)
```

`prop_pipeline.py:80-83` *does* ask for game logs:

```python
for s in stats:
    recent, rsource = await collector.fetch_player_recent(team.sport, s["player_name"], n=5)
    if recent and rsource:
        collector.store_stats(session, recent, "game_log", team.id, team.sport, rsource)
```

390 `season_avg` rows landed from the same loop and 0 `game_log` rows did, so
`fetch_player_recent` is returning nothing for every player. **Task 3 must
establish why before writing a collector.**

Even if it worked, it would not be enough: this runs against
`Game.status == "scheduled"` (`prop_pipeline.py:62`) — *before* the game. A
last-5 fetched before tip-off cannot contain the game being graded. **No code
path in this repo fetches a post-game box score.** That is the real gap.

## File structure

| File | Responsibility | Task |
|---|---|---|
| `backend/pipeline/grader.py` | `grade_pick` refuses unknown types instead of guessing | 1 |
| `backend/pipeline/scheduler.py` | both grading loops skip ungradeable picks; strategy loop routes props | 1, 4 |
| `backend/models.py` | `PickModel` gains `prop_player`, `prop_market` | 2 |
| `backend/database.py` | migration adding the two columns | 2 |
| `backend/pipeline/prop_pipeline.py` | writes the new columns when generating prop picks | 2 |
| `backend/scripts/backfill_prop_fields.py` | **new** — populates the columns for the 82 existing props | 2 |
| `backend/collectors/player_stats/collector.py` | post-game box score fetch | 3 |
| `backend/pipeline/scheduler.py` | `morning_scout` collects box scores before grading | 3 |
| `backend/analysis/prop_calibration.py` | **new** — read-only prop reliability report | 5 |

---

### Task 1: Stop `grade_pick` inventing a result for pick types it does not understand

> **DONE 2026-09-17 — `6117d5e`.** 549 -> 552 passing, mutation-proved.
> **Correction for later tasks: there were six call sites, not the two named
> below.** `api/users.py` x3 (add-pick, parlay leg, grade endpoint) and
> `backtesting/backtester.py` x1 also unpacked the return value and would have
> raised `TypeError` on `None`. All six are updated. Note the backtester
> excludes ungradeable picks from its sample entirely -- counting them as
> losses understates the strategy, counting them as pushes inflates the
> denominator.
>
> One trap worth carrying into Task 4: the grade endpoint already has a local
> named `graded` (a counter, `graded += 1`). Naming the new local `graded`
> shadows it and turns the increment into `tuple + int`. The new locals are
> called `grade_outcome`.


This is the safety fix and is worth landing on its own, immediately. After it,
the worst case is that props stay ungraded — not that they are recorded as
losses.

**Files:**
- Modify: `backend/pipeline/grader.py:34-72`
- Modify: `backend/pipeline/scheduler.py:256-305`
- Test: `backend/tests/test_grader.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `grade_pick(pick_type, pick_value, home_score, away_score, odds_at_pick) -> tuple[str, float] | None`.
  `None` means "this pick type cannot be graded here" and every caller must
  skip on it. The signature is otherwise unchanged.

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_grader.py` (the file needs `import pytest` at
the top — check before adding, it may not have it yet):

```python
def test_grade_pick_refuses_a_prop_instead_of_calling_it_a_loss():
    """An unknown pick type must not be resolved into a confident result.

    `pick_type="prop"` has no branch here -- props are graded by
    `grade_prop_pick` against player box scores, which this function never
    sees. Returning ("loss", -1.0) records a real, wrong outcome for every
    prop ever generated and silently poisons any ROI or calibration number
    computed afterwards.
    """
    assert grade_pick("prop", "Dean Wade Over 0.5 3-Pointers", 110, 105, -200) is None
    # Scores that would make any real pick a win must not change the answer.
    assert grade_pick("prop", "Dean Wade Over 0.5 3-Pointers", 999, 0, -200) is None


def test_grade_pick_refuses_an_unrecognised_pick_type():
    """The same guard for anything else added later without a branch here."""
    assert grade_pick("team_total", "HOME Over 110.5", 120, 100, -110) is None


def test_grade_pick_still_grades_the_types_it_does_support():
    """The refusal must not swallow the working paths."""
    result, payout = grade_pick("moneyline", "HOME", 110, 100, -110)
    assert result == "win"
    assert payout == pytest.approx(100 / 110)   # -110 stake returns 0.909...
    assert grade_pick("moneyline", "AWAY", 110, 100, -110) == ("loss", -1.0)
```

- [ ] **Step 2: Run the test and watch it fail**

```
.venv/Scripts/python.exe -m pytest backend/tests/test_grader.py -q -k "refuses"
```

Expected: both refusal tests FAIL with `assert ('loss', -1.0) is None`.
If they pass, the fix is already in — STOP and report.

- [ ] **Step 3: Make `grade_pick` refuse**

In `backend/pipeline/grader.py`, change the signature and the fallthrough:

```python
def grade_pick(pick_type: str, pick_value: str, home_score: int, away_score: int,
               odds_at_pick: int) -> tuple[str, float] | None:
    """Grade a game-level pick from the final score.

    Returns ``(result, payout_ratio)``, or ``None`` when ``pick_type`` is not
    one this function can grade -- notably ``"prop"``, which needs player box
    scores and belongs to :func:`grade_prop_pick`. Callers must treat ``None``
    as "leave this pick ungraded" and must not substitute a default: an
    invented result is indistinguishable from a measured one once it is in
    ``pick_results``.
    """
```

Replace the final `else` branch:

```python
    else:
        logger.warning("grade_pick cannot grade pick_type=%r; leaving ungraded", pick_type)
        return None
```

- [ ] **Step 4: Run the test and watch it pass**

```
.venv/Scripts/python.exe -m pytest backend/tests/test_grader.py -q
```

Expected: PASS, all tests in the file.

- [ ] **Step 5: Make both callers handle `None`**

`backend/pipeline/scheduler.py`, strategy-pick loop (~line 264):

```python
    for pick, game in ungraded:
        if game.home_score is not None and game.away_score is not None:
            graded = grade_pick(pick.pick_type, pick.pick_value,
                                game.home_score, game.away_score,
                                pick.odds_at_pick or -110)
            if graded is None:
                skipped += 1
                continue
            result, payout = graded
```

Initialise `skipped = 0` before the loop and change the closing log so the
count is visible rather than implied:

```python
    session.commit()
    logger.info("Graded %d strategy picks (%d skipped as ungradeable here)",
                len(ungraded) - skipped, skipped)
```

`backend/pipeline/scheduler.py`, PaperPick loop `else` branch (~line 300):

```python
        else:
            graded = grade_pick(
                pick.pick_type, pick.pick_value,
                game.home_score, game.away_score, pick.odds
            )
            if graded is None:
                continue
            pick.result = graded[0]
```

- [ ] **Step 6: Run the full suite**

```
.venv/Scripts/python.exe -m pytest backend/tests -q
```

Expected: `552 passed` (549 + 3 new), 0 failed. Any other failure is a caller
you have not updated — find it with
`grep -rn "grade_pick(" backend/ --include=*.py`.

- [ ] **Step 7: Mutation-prove the guard**

Temporarily restore `return "loss", -1.0` in place of `return None`, run
`pytest backend/tests/test_grader.py -q -k refuses`, and confirm both tests
FAIL. Restore the fix. A guard you have not seen fail is not a guard.

- [ ] **Step 8: Commit**

```bash
git add backend/pipeline/grader.py backend/pipeline/scheduler.py backend/tests/test_grader.py
git commit -m "fix(grader): refuse to grade pick types with no branch, instead of returning a loss"
```

---

### Task 2: Give `PickModel` the fields a prop grader needs

**Files:**
- Modify: `backend/models.py` (class `PickModel`, near line 200 for the
  `PaperPick` precedent)
- Modify: `backend/database.py` (migration block)
- Modify: `backend/pipeline/prop_pipeline.py` (~line 190, where prop picks are
  created)
- Create: `backend/scripts/backfill_prop_fields.py`
- Test: `backend/tests/test_prop_pick_fields.py` (new)

**Interfaces:**
- Consumes: Task 1's `grade_pick` returning `None` for props.
- Produces: `PickModel.prop_player: str | None` and
  `PickModel.prop_market: str | None`, populated for every
  `pick_type == "prop"` row. `prop_market` holds the **market key**
  (`"player_threes"`), not the display label (`"3-Pointers"`), because
  `MARKET_STAT_MAP` is keyed by the former.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_prop_pick_fields.py`:

```python
from backend.models import Base, PickModel


def test_pick_model_carries_the_player_and_market_a_prop_needs(db_session):
    """`grade_prop_pick(pick_value, market, player_stat)` needs a market key
    and the player's box score. `pick_value` is prose -- "Dean Wade Over 0.5
    3-Pointers" -- so the market must be stored, not re-parsed from a display
    label that is lossy (`_market_label` maps several markets to themselves).
    """
    Base.metadata.create_all(db_session.get_bind())
    db_session.add(PickModel(
        game_id=1, strategy_id=1, pick_type="prop",
        pick_value="Dean Wade Over 0.5 3-Pointers",
        confidence=5, odds_at_pick=-200,
        prop_player="Dean Wade", prop_market="player_threes",
    ))
    db_session.commit()

    row = db_session.query(PickModel).one()
    assert row.prop_player == "Dean Wade"
    assert row.prop_market == "player_threes"
```

- [ ] **Step 2: Run it and watch it fail**

```
.venv/Scripts/python.exe -m pytest backend/tests/test_prop_pick_fields.py -q
```

Expected: FAIL with `TypeError: 'prop_player' is an invalid keyword argument for PickModel`.

- [ ] **Step 3: Add the columns**

In `backend/models.py`, class `PickModel`, mirroring `PaperPick:200`:

```python
    prop_player = Column(String, nullable=True)  # only for pick_type="prop"
    prop_market = Column(String, nullable=True)  # market KEY, e.g. "player_threes"
```

- [ ] **Step 4: Add the migration**

In `backend/database.py`, alongside the existing migration statements, add the
two `ALTER TABLE` guards. Follow the file's established pattern exactly — read
the surrounding migrations first and match their idempotency check rather than
inventing a new one.

```sql
ALTER TABLE picks ADD COLUMN prop_player TEXT
ALTER TABLE picks ADD COLUMN prop_market TEXT
```

- [ ] **Step 5: Run the test and watch it pass**

```
.venv/Scripts/python.exe -m pytest backend/tests/test_prop_pick_fields.py -q
```

Expected: PASS.

- [ ] **Step 6: Populate the columns at write time**

In `backend/pipeline/prop_pipeline.py`, where the prop `PickModel` is
constructed (~line 190), pass `prop_player=prop.player_name` and
`prop_market=prop.market`. Read the surrounding lines to get the exact local
variable names — do not guess them.

Add a test asserting a generated prop pick carries both fields, in the style
of `backend/tests/test_team_stats_pipeline.py`.

- [ ] **Step 7: Write the backfill script**

Create `backend/scripts/backfill_prop_fields.py`, modelled closely on
`backend/scripts/backfill_team_stats.py` — copy its `--db` safety (required,
no default, refuses a path that does not exist), its `--dry-run`, and its
resumability (ask each row, never assume contiguity).

It must derive `prop_player` and `prop_market` from `pick_value` for rows where
`pick_type='prop'` and the columns are NULL.

**The market is the hard half and the script must refuse rather than guess.**
`prop_pipeline._market_label` (line 208) maps market keys to labels, and it is
**partial** — it has entries for 7 markets and `MARKET_STAT_MAP` (`grader.py:13`)
has 16. Anything unmapped falls through as `labels.get(market, market)`, so the
label *is* the key for 9 markets. Build the reverse map from `_market_label`'s
own dict rather than retyping it, and for a `pick_value` whose trailing label
matches no key, **leave both columns NULL and count it** — do not fall back to
a plausible market. A wrong market silently grades against the wrong stat.

Report counts by outcome: resolved, unresolved-label, already-populated.

- [ ] **Step 8: Dry-run the backfill against a copy**

```bash
cp sports_picks.db /tmp/props.db
.venv/Scripts/python.exe -m backend.scripts.backfill_prop_fields --db 'C:\path\to\props.db' --dry-run
```

Expected: 82 prop rows considered. Record how many resolve and how many do
not. **If more than a handful are unresolved, STOP and report the label
distribution** — it means the reverse map needs widening before this is worth
running.

- [ ] **Step 9: Run it against the copy and verify**

```
.venv/Scripts/python.exe -m pytest backend/tests -q
```

Expected: `554 passed` or more, 0 failed.

- [ ] **Step 10: Commit**

```bash
git add backend/models.py backend/database.py backend/pipeline/prop_pipeline.py backend/scripts/backfill_prop_fields.py backend/tests/test_prop_pick_fields.py
git commit -m "feat(picks): carry prop player and market on PickModel"
```

---

### Task 3: Collect post-game player box scores

**This is the task that decides whether the plan is finishable.** Tasks 1, 2
and 4 are code; this one depends on a data source existing. Do the spike in
Step 1 before writing anything.

**Files:**
- Modify: `backend/collectors/player_stats/collector.py`
- Modify: `backend/collectors/player_stats/nba_api_source.py` (and siblings as
  the spike dictates)
- Modify: `backend/pipeline/scheduler.py` (`morning_scout`)
- Test: `backend/tests/test_box_score_collection.py` (new)

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `PlayerStat` rows with `stat_type="game_log"` and `game_date` set
  to the date of a **completed** game — exactly the shape
  `grade_prop_pick` looks up via
  `filter_by(player_name=..., stat_type="game_log", game_date=game.date)`.

- [ ] **Step 1: Spike — find out why `fetch_player_recent` returns nothing**

Time-box this. It is a question, not a deliverable.

```
.venv/Scripts/python.exe -c "
import asyncio
from backend.collectors.player_stats.collector import PlayerStatsCollector
c = PlayerStatsCollector()
print(asyncio.run(c.fetch_player_recent('nba', 'Donovan Mitchell', n=5)))
"
```

Three possible answers, each changing what you build:

- **It returns data.** Then the pre-game path works and only the *post-game*
  trigger is missing — go to Step 2 and wire collection into `morning_scout`.
- **It returns empty because the source is unreachable or unauthenticated.**
  Check which sources `prop_pipeline` constructs (`nba_api`, `espn_stats`,
  `balldontlie`, `mysportsfeeds`) and which need a key. **STOP and report**
  which source is needed and what it costs — a key purchase is your decision,
  not the executor's.
- **It returns empty because the method is unimplemented or always returns
  `[]`.** Then this task is "write a box-score collector", and the spike should
  say which of the four sources exposes per-game logs.

Record the answer in the plan file before continuing.

- [ ] **Step 2: Write the failing test**

Write it against a **stubbed source**, not the network — `pytest-httpx` is
already a dependency and the suite is network-free today. Keep it that way.

```python
def test_box_scores_are_stored_against_the_game_that_was_played(db_session):
    """Grading looks up (player_name, stat_type="game_log", game_date). A row
    stored without game_date, or against the fetch date rather than the game
    date, is invisible to grade_prop_pick even though it is present.
    """
```

Assert on a real `PlayerStat` row read back from the session, with
`stat_type == "game_log"` and `game_date` equal to the **game's** date.

- [ ] **Step 3: Run it and watch it fail.** Then implement the minimum that
  makes it pass, per the spike's answer.

- [ ] **Step 4: Call it from `morning_scout` before grading**

In `backend/pipeline/scheduler.py`, `morning_scout` currently opens with:

```python
        grade_pending_picks(session)
        grade_completed_games(session)
```

Box-score collection must run **before** `grade_pending_picks`, and must be
wrapped so a collector failure cannot stop grading:

```python
        try:
            collect_box_scores_for_final_games(session)
        except Exception:
            logger.exception("Box score collection failed; grading with what is present")
        grade_pending_picks(session)
```

- [ ] **Step 5: Full suite, then commit**

```
.venv/Scripts/python.exe -m pytest backend/tests -q
git commit -m "feat(collectors): fetch post-game player box scores for grading"
```

---

### Task 4: Route prop picks to the prop grader

**Files:**
- Modify: `backend/pipeline/scheduler.py:256-274`
- Test: `backend/tests/test_grade_pending_props.py` (new)

**Interfaces:**
- Consumes: Task 1's `None` contract, Task 2's `prop_player`/`prop_market`,
  Task 3's `game_log` rows.
- Produces: `PickResult` rows for prop picks.

- [ ] **Step 1: Write the failing test**

```python
def test_prop_picks_are_graded_against_the_player_box_score(db_session):
    """The strategy loop must branch on pick_type exactly as the PaperPick
    loop thirty lines below it already does. Before this, props reached
    grade_pick, which has no prop branch.
    """
```

Seed a final game, a prop `PickModel` with `prop_player`/`prop_market`, and a
matching `PlayerStat` game_log row whose stat clears the line. Call
`grade_pending_picks(session)`. Assert a `PickResult` exists for that pick with
`result == "win"`.

Add a second test: with **no** matching `PlayerStat`, `grade_pending_picks`
creates **no** `PickResult` for the prop and does not raise. Ungraded is the
correct outcome for missing data; a default is not.

- [ ] **Step 2: Run both and watch them fail.**

- [ ] **Step 3: Add the branch**, mirroring `scheduler.py:290-305`:

```python
    for pick, game in ungraded:
        if game.home_score is None or game.away_score is None:
            continue

        if pick.pick_type == "prop" and pick.prop_player and pick.prop_market:
            player_stat = (
                session.query(PlayerStat)
                .filter_by(player_name=pick.prop_player, stat_type="game_log",
                           game_date=game.date)
                .first()
            )
            graded = grade_prop_pick(pick.pick_value, pick.prop_market, player_stat)
        else:
            graded = grade_pick(pick.pick_type, pick.pick_value,
                                game.home_score, game.away_score,
                                pick.odds_at_pick or -110)
        if graded is None:
            skipped += 1
            continue
        result, payout = graded
```

- [ ] **Step 4: Run both and watch them pass. Then the full suite.**

- [ ] **Step 5: Mutation-prove** — delete the `prop` branch, confirm the first
  test fails. Restore.

- [ ] **Step 6: Commit**

```bash
git commit -m "fix(scheduler): grade prop picks with the prop grader"
```

---

### Task 5: Measure prop calibration

Only now is there anything to measure.

**Files:**
- Create: `backend/analysis/prop_calibration.py`
- Test: `backend/tests/test_prop_calibration.py` (new)

**Interfaces:**
- Consumes: `PickResult` rows for props from Task 4.
- Produces: a read-only CLI report, same shape as
  `backend/analysis/calibration_report.py` — copy its structure, its
  `--db`-required safety, its `min_bin` reliability flag, and its effective
  sample size block.

- [ ] **Step 1: Write the failing test** — a seeded set of prop picks and
  results produces a known win rate per confidence tier.

- [ ] **Step 2: Run it, watch it fail, implement, watch it pass.**

- [ ] **Step 3: Report observed win rate per confidence tier (1-5).**

The question this exists to answer: **do 5-star props win more often than
4-star props?** 39 of 82 props sit at confidence 5. If tier 5 does not
outperform tier 4, the confidence score carries no information and the digest
should not rank by it.

State the effective sample size with any claim. 82 props across a handful of
games are **not** 82 independent observations — props from the same game share
its pace, blowout risk and rotation. Cluster on `game_id`, not on player.

- [ ] **Step 4: Commit, then update `plans/README.md` and `plans/HANDOFF.md`
  with the measured numbers.**

---

## STOP conditions

Stop and report; do not improvise.

1. **Task 3's spike shows no source supplies per-game box scores** without a
   paid key. That is a purchasing decision. Tasks 1, 2 and 4 still stand on
   their own — land them and leave props correctly *ungraded* rather than
   wrongly graded.
2. **The Task 2 backfill cannot resolve the market for more than a handful of
   the 82 props.** Report the label distribution instead of guessing markets.
3. **Any test passes the first time you run it.** It is not testing what you
   think. Fix the test before the code.
4. **The full suite drops below 549 passing** at any point.
5. **You are about to run a migration or backfill against `sports_picks.db`.**
   Copy it first, always.

## Verification

Baseline before starting: **549 passing**, 0 failed, on 3.12 and 3.14.

```
.venv/Scripts/python.exe -m pytest backend/tests -q
```

Final state should be 549 + every test this plan adds, with CI green on both
Python versions.

## Out of scope

Deliberately not in this plan, so they do not get re-audited:

- **Retuning `min_edge` or the ranking key.** Blocked on measurement, and the
  game-model underdog finding is n=15/28 — under the report's own `min_bin`.
  More completed games first.
- **`MARKET_STAT_MAP` is duplicated** in `grader.py:13` and
  `prop_analyzer.py:13`. A derive-don't-duplicate violation and a real drift
  risk, but fixing it while also changing grading would confuse the diff. Worth
  its own small plan.
- **Enabling `digest.enabled`.** That decision waits on Task 5's numbers.

# Plan 002: Reconnect three subsystems that run but whose output is discarded

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md`.
>
> **Drift check (run first)**:
> `git diff --stat 0e1f13f..HEAD -- backend/analysis/ backend/collectors/player_stats/ backend/pipeline/`
> If any in-scope file changed since this plan was written, compare the
> "Current state" excerpts against the live code before proceeding; on a
> mismatch, treat it as a STOP condition.

## Status

- **Priority**: P1
- **Effort**: M
- **Risk**: MED
- **Depends on**: none
- **Category**: bug
- **Planned at**: commit `5c2e0d0`, 2026-09-16
- **Refreshed at**: commit `0e1f13f`, 2026-09-16 — plan 006 merged and rewrote
  `get_thresholds` / `get_prop_thresholds`. The Part B excerpt below has been
  updated to the current code. **No in-scope file changed**; Part A and Part C
  are unaffected.

## Why this matters

Three subsystems in this app execute successfully, log success, and then have
their results thrown away. Each fails *quietly* — the logs look like a normal
cold start, not a defect — which is why all three have survived.

1. **The logistic-regression calibrator has never trained.** It queries
   `Game.status == "completed"`, but every writer in the codebase writes
   `"final"`. Verified empirically: seeding 300 `status="final"` games and
   calling `train_from_db` logs *"Only 0 completed games found (need 30)"* and
   sets `trained = False`. Every prediction silently falls through to a
   heuristic.
2. **The nightly recalibration job's output is never read.** It computes and
   persists new per-tier confidence thresholds every night, but
   `get_thresholds()` has zero callers, so every confidence tier is computed
   from the hardcoded `DEFAULT_THRESHOLDS`. The system cannot self-correct a
   miscalibrated tier — which is the entire point of the feature.
3. **`receptions` is never persisted.** The column exists, a migration adds it,
   the prop analyzer and grader both map `player_receptions` to it — but the
   collector omits it from both its update list and its insert kwargs, so every
   `player_receptions` prop silently produces no pick and can never be graded.

Each fix is small and independently verifiable. Together they turn on three
features that are currently decorative.

## Current state

### Part A — `calibrated_model.py` filters on a status that never exists

`backend/analysis/calibrated_model.py:104-122`:

```python
        completed_games = (
            session.query(Game)
            .filter(
                and_(
                    Game.status == "completed",
                    Game.home_score.isnot(None),
                    Game.away_score.isnot(None),
                )
            )
            .all()
        )

        if len(completed_games) < MIN_TRAINING_GAMES:
            logger.info(
                "Only %d completed games found (need %d). Skipping calibration.",
                len(completed_games),
                MIN_TRAINING_GAMES,
            )
            self.trained = False
            return
```

Every producer of `Game.status` writes one of `scheduled / in_progress / final /
postponed / cancelled`:
- `backend/collectors/espn.py:13-19` — `STATUS_MAP`
- `backend/pipeline/scheduler.py:416` — `existing_game.status = "final"`
- `backend/pipeline/scheduler.py:421` — `status="final"`
- `backend/pipeline/full_pipeline.py:203,317` — `"scheduled"`

Every other consumer queries `"final"` (e.g. `ensemble.py:183`,
`grader.py:218`, `recalibration_job.py:34`). `calibrated_model.py:108` is the
**only** place in the backend that compares a `Game.status` to `"completed"`.

> Note: `"completed"` is also used for `BacktestRun.status`
> (`backend/api/backtest.py:112,309`) and as a literal in
> `backend/api/pipeline_api.py:104`. **Those are a different table and a
> different field — do not touch them.**

### Part B — recalibrated thresholds are written but never read

`backend/analysis/confidence.py` in full:

```python
DEFAULT_THRESHOLDS = {5: 12.0, 4: 8.0, 3: 5.0, 2: 5.0, 1: 3.0}
DEFAULT_MIN_MODELS = {5: 3, 4: 2, 3: 2, 2: 1, 1: 0}


def get_thresholds(session=None, sport: str = "nba") -> dict:
    """Load latest confidence thresholds from DB, or return defaults."""
    if session is None:
        return DEFAULT_THRESHOLDS
    from backend.models import CalibrationHistory
    rows = (
        session.query(CalibrationHistory)
        .filter(CalibrationHistory.sport == sport)
        .order_by(CalibrationHistory.date.asc())
        .all()
    )
    thresholds = dict(DEFAULT_THRESHOLDS)
    for row in rows:
        thresholds[row.confidence_tier] = row.new_threshold
    return thresholds


def calculate_confidence(
    edge_pct: float, models_agreeing: int, thresholds: dict | None = None,
) -> int:
    if thresholds is None:
        thresholds = DEFAULT_THRESHOLDS
    min_models = DEFAULT_MIN_MODELS
    for tier in (5, 4, 3, 2, 1):
        if edge_pct >= thresholds.get(tier, 999) and models_agreeing >= min_models.get(tier, 0):
            return tier
    return 0
```

`calculate_confidence` **already accepts** a `thresholds` argument — it is
simply never passed. There are 14 call sites, none of which pass it:

| File | Lines |
|---|---|
| `backend/analysis/variants/ensemble.py` | 44, 51, 72, 84, 105, 116 |
| `backend/analysis/variants/combat_sports.py` | 50, 58 |
| `backend/analysis/variants/value_only.py` | 35, 44 |
| `backend/analysis/variants/sport_specific.py` | 44, 53 |
| `backend/analysis/variants/recent_form.py` | 35, 44 |

`backend/analysis/prop_confidence.py` has the identical dead pattern:
`get_prop_thresholds()` is uncalled, and `calculate_prop_confidence(edge_pct)`
is called without thresholds at `prop_analyzer.py:217` and `:234`. It now has
the same corrected shape as `get_thresholds` above (ascending order, merged
over `DEFAULT_PROP_THRESHOLDS`), and still encodes prop tiers with a +100
offset in `CalibrationHistory.confidence_tier`, decoding with `- 100` on read.
**Preserve that encoding.**

> Both threshold readers were corrected by plan 006 (merged as `0e1f13f`):
> newest row per tier now wins, and the result is always `DEFAULTS` updated
> with DB values so a partially-populated table cannot make a tier
> unreachable. Their *signatures are unchanged* — `get_thresholds(session,
> sport)` — so this plan's wiring work is unaffected. Do **not** modify either
> function; this plan only *calls* them.

The base class is tiny — `backend/analysis/strategy.py` in full:

```python
from abc import ABC, abstractmethod
from backend.data_types import GameData, Pick

class Strategy(ABC):
    def __init__(self, name: str, config: dict):
        self.name = name
        self.config = config

    @abstractmethod
    def predict(self, game: GameData) -> list[Pick]:
        pass

    @classmethod
    def from_config(cls, config: dict) -> "Strategy":
        return cls(name=config.get("name", cls.__name__), config=config)
```

Strategies are constructed in `backend/pipeline/pick_generator.py:37-40`,
inside the per-game loop, and a `Session` is in scope there:

```python
        if game.sport in ("mma", "boxing"):
            strategy = CombatSportsStrategy(strat_row.name, config)
        else:
            strategy = strategy_cls(strat_row.name, config)
```

`PropAnalyzer` is constructed once at `backend/pipeline/prop_pipeline.py:112`
via `analyzer = PropAnalyzer(**analyzer_kwargs)`; its constructor
(`prop_analyzer.py:83-91`) takes `season_weight`, `recent_weight`, `min_edge`.

### Part C — `receptions` dropped on the floor

`backend/collectors/player_stats/collector.py:60-86`:

```python
            existing = session.query(PlayerStat).filter_by(
                player_name=name, sport=sport, stat_type=stat_type, game_date=game_date
            ).first()

            if existing:
                for field in ["minutes", "points", "rebounds", "assists", "threes",
                              "steals", "blocks", "turnovers", "pass_yards",
                              "rush_yards", "rec_yards", "touchdowns"]:
                    val = s.get(field)
                    if val is not None:
                        setattr(existing, field, float(val))
                existing.source = source
                existing.fetched_at = now
                existing.is_stale = is_stale
            else:
                row = PlayerStat(
                    player_name=name, team_id=team_id, sport=sport,
                    stat_type=stat_type, game_date=game_date,
                    minutes=s.get("minutes"), points=s.get("points"),
                    rebounds=s.get("rebounds"), assists=s.get("assists"),
                    threes=s.get("threes"), steals=s.get("steals"),
                    blocks=s.get("blocks"), turnovers=s.get("turnovers"),
                    pass_yards=s.get("pass_yards"), rush_yards=s.get("rush_yards"),
                    rec_yards=s.get("rec_yards"), touchdowns=s.get("touchdowns"),
                    source=source, fetched_at=now, is_stale=is_stale,
                )
                session.add(row)
```

`receptions` is absent from both lists; every other nullable stat column on
`PlayerStat` (`backend/models.py:146-160`) is present in both. The column and
its migration already exist (`models.py:159`, `database.py:50-58`), and both
`prop_analyzer.py:32` and `grader.py:29` map `player_receptions -> ["receptions"]`.

**This repo's stated convention** (from `CLAUDE.md`): *"Derive, don't duplicate.
When two code paths must agree, make one call the other."* The two field lists
above are exactly that failure — fix them by deriving both from one tuple.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Full suite | `.venv/Scripts/python.exe -m pytest backend/tests -q` | `395 passed` (before), all pass (after) |
| Targeted | `.venv/Scripts/python.exe -m pytest backend/tests/test_calibrated_model.py backend/tests/test_confidence.py backend/tests/test_player_stats.py -q` | all pass |
| Ad-hoc check | `.venv/Scripts/python.exe -c "..."` | see steps |

Run from repo root. The full suite takes ~4 minutes.

## Scope

**In scope**:
- `backend/analysis/calibrated_model.py`
- `backend/analysis/strategy.py`
- `backend/analysis/variants/ensemble.py`
- `backend/analysis/variants/combat_sports.py`
- `backend/analysis/variants/value_only.py`
- `backend/analysis/variants/sport_specific.py`
- `backend/analysis/variants/recent_form.py`
- `backend/analysis/prop_analyzer.py`
- `backend/pipeline/pick_generator.py`
- `backend/pipeline/prop_pipeline.py`
- `backend/collectors/player_stats/collector.py`
- New/updated tests under `backend/tests/`

**Out of scope** (do NOT touch, even though they look related):
- `backend/api/backtest.py` and `backend/api/pipeline_api.py` — their
  `"completed"` strings refer to `BacktestRun.status`, a **different table**.
  Changing them will break backtest status reporting.
- `backend/analysis/recalibrator.py` — it has its own separate confirmed bugs
  (missing sport filter, pushes counted as losses). Wiring its *output* in is
  this plan; fixing its *computation* is not. Do not change it.
- The vig/odds-averaging bugs in the strategy variants — those are plan 003.
  You will be editing the same files; **only** touch the `calculate_confidence`
  call sites and the constructor.
- `backend/analysis/ml_model.py` / LightGBM persistence — separate finding.

## Git workflow

- Branch: `advisor/002-reconnect-dead-wiring`
- Conventional commits; one commit per part is ideal:
  - `fix(model): train calibrator on status="final", not "completed"`
  - `fix(confidence): wire recalibrated thresholds into pick generation`
  - `fix(collector): persist receptions so player_receptions props work`

## Steps

### Step 1: Establish the baseline

Run the full suite and record the count.

**Verify**: `.venv/Scripts/python.exe -m pytest backend/tests -q` → `395 passed`.
If it is not 395 passing, STOP and report.

### Step 2 (Part A): Train on `"final"`

In `backend/analysis/calibrated_model.py`, change the filter at line 108 from
`Game.status == "completed"` to `Game.status == "final"`. Update the log
message at :118 to say `final` instead of `completed` so the wording matches
the query.

Prefer referencing a shared constant if one already exists; if not, a plain
`"final"` literal matching the rest of the codebase is correct — do **not**
introduce a new constants module in this plan.

**Verify** — this exact command must print `TRAINED? True`:

```
.venv/Scripts/python.exe -c "
from backend.database import get_engine, get_session
from backend.models import Base, Game, Team
from backend.analysis.calibrated_model import CalibratedModel
import datetime as dt
e=get_engine(':memory:'); Base.metadata.create_all(e); s=get_session(e)
for i in range(300):
    h=Team(name=f'H{i}',abbreviation='H',sport='nba'); a=Team(name=f'A{i}',abbreviation='A',sport='nba')
    s.add_all([h,a]); s.flush()
    s.add(Game(sport='nba',season='2026',date=dt.date(2026,1,1)+dt.timedelta(days=i%300),home_team_id=h.id,away_team_id=a.id,status='final',home_score=110+i%20,away_score=100+i%15))
s.commit()
m=CalibratedModel(); m.train_from_db(s); print('TRAINED?', m.trained)
"
```

Before your change this prints `TRAINED? False`. After it must print
`TRAINED? True`. If it still prints `False`, the training path has a *second*
blocker — STOP and report what the log says rather than changing more code.

### Step 3 (Part A): Add a regression test

Add `test_train_from_db_uses_final_status` to
`backend/tests/test_calibrated_model.py` (create the file if absent, following
the conventions in `backend/tests/test_api_picks.py`). Seed ≥ `MIN_TRAINING_GAMES`
games with `status="final"` and assert `model.trained is True`. Add a second
test seeding games with `status="scheduled"` asserting `model.trained is False`.

**Prove the guard is real**: temporarily revert line 108 to `"completed"`,
confirm the new test **fails**, then restore `"final"`.

**Verify**: `.venv/Scripts/python.exe -m pytest backend/tests/test_calibrated_model.py -q`
→ all pass.

### Step 4 (Part B): Thread thresholds through the Strategy base

In `backend/analysis/strategy.py`, add an optional `thresholds` parameter:

```python
class Strategy(ABC):
    def __init__(self, name: str, config: dict, thresholds: dict | None = None):
        self.name = name
        self.config = config
        self.thresholds = thresholds
```

Keep `from_config` working (it passes no thresholds; `None` is the correct
default and preserves today's behavior).

Then in **each** of the five variant files, change every `calculate_confidence(...)`
call to pass `self.thresholds` as the third argument. All 14 sites are listed in
the "Current state" table above. Example, `value_only.py:35`:

```python
confidence=calculate_confidence(home_edge, models, self.thresholds),
```

Do not change the edge computation, the `min_edge` logic, or anything else in
these files.

**Verify**: `.venv/Scripts/python.exe -m pytest backend/tests -q` → still all
pass (behavior is unchanged because `thresholds` defaults to `None`, which
`calculate_confidence` already maps to `DEFAULT_THRESHOLDS`).
Also: `grep -c "calculate_confidence(" backend/analysis/variants/*.py` — the
total across the five files must be 14, and
`grep -n "calculate_confidence(" backend/analysis/variants/*.py | grep -vc "self.thresholds"` must return `0`.

### Step 5 (Part B): Load thresholds in the pick generator

In `backend/pipeline/pick_generator.py`, inside `generate_and_store_picks`,
load thresholds **once per sport** before the per-game loop (not per game —
that would issue a query per game). Build a small
`dict[str, dict]` cache keyed by sport, populated lazily via
`get_thresholds(session, sport)` from `backend.analysis.confidence`.

Pass the sport's thresholds as the third constructor argument at both
construction sites (`pick_generator.py:37-40`).

**Verify**: add and run a test asserting that a `CalibrationHistory` row with a
lowered threshold changes the resulting pick's confidence tier. Concretely:
seed a game + odds that produce a known edge, run `generate_and_store_picks`
once with no `CalibrationHistory` rows and record the tier; insert a
`CalibrationHistory` row for that sport and tier with a `new_threshold` low
enough to bump the tier; re-run and assert the tier increased.

**This is the load-bearing test of Part B** — without it you have not proven the
wiring works. If it passes identically before and after inserting the row, the
thresholds are still not reaching `calculate_confidence`; STOP and report.

### Step 6 (Part B): Same wiring for props

In `backend/analysis/prop_analyzer.py`, add `thresholds: dict | None = None`
to `PropAnalyzer.__init__` (store as `self.thresholds`) and pass it at both
`calculate_prop_confidence` call sites (`:217`, `:234`).

In `backend/pipeline/prop_pipeline.py`, load `get_prop_thresholds(session, sport)`
from `backend.analysis.prop_confidence` and include it in `analyzer_kwargs`
at `:112`.

Note `get_prop_thresholds` stores prop tiers offset by +100 in
`CalibrationHistory.confidence_tier` and subtracts 100 on read — do not change
that encoding.

**Verify**: `.venv/Scripts/python.exe -m pytest backend/tests -q` → all pass.

### Step 7 (Part C): Persist `receptions`

In `backend/collectors/player_stats/collector.py`, define **one** module-level
tuple of the stat field names and derive both the update loop and the insert
kwargs from it, so the two can never drift again:

```python
_STAT_FIELDS = (
    "minutes", "points", "rebounds", "assists", "threes",
    "steals", "blocks", "turnovers", "pass_yards",
    "rush_yards", "rec_yards", "receptions", "touchdowns",
)
```

Use it for the `for field in ...` loop, and build the insert with
`**{f: s.get(f) for f in _STAT_FIELDS}` rather than 13 hand-written kwargs.

**Verify** — this must print `receptions persisted: 7.0`:

```
.venv/Scripts/python.exe -c "
from backend.database import get_engine, get_session
from backend.models import Base, Team, PlayerStat
from backend.collectors.player_stats.collector import PlayerStatsCollector
import datetime as dt, inspect
e=get_engine(':memory:'); Base.metadata.create_all(e); s=get_session(e)
t=Team(name='X',abbreviation='X',sport='nfl'); s.add(t); s.commit()
print(inspect.signature(PlayerStatsCollector.store_stats))
"
```

Then write a proper test (step 8) rather than relying on the probe — adapt the
probe to whatever `store_stats`' real signature requires.

### Step 8 (Part C): Regression test for `receptions`

Add a test to the existing player-stats test module (find it with
`ls backend/tests | grep -i player`) that calls `store_stats` with a payload
containing `receptions`, then queries the stored `PlayerStat` row and asserts
`row.receptions == <the value>`. Add a second assertion covering the **update**
branch (store once, store again with a different `receptions`, assert updated).

**Prove the guard is real**: temporarily remove `"receptions"` from
`_STAT_FIELDS`, confirm both new assertions fail, then restore it.

**Verify**: `.venv/Scripts/python.exe -m pytest backend/tests -q` → all pass.

### Step 9: Full suite and diff review

**Verify**:
- `.venv/Scripts/python.exe -m pytest backend/tests -q` → 0 failed
- `git diff --stat` → only files from the In-scope list appear
- `git diff | grep -n '"completed"'` → no line **removing** a `"completed"`
  string outside `backend/analysis/calibrated_model.py`

## Test plan

New tests, all following `backend/tests/test_api_picks.py` conventions:

1. `test_train_from_db_uses_final_status` — trains on `status="final"` (step 3)
2. `test_train_from_db_ignores_scheduled` — does not train on scheduled (step 3)
3. `test_recalibrated_threshold_changes_confidence_tier` — the load-bearing
   Part B test (step 5)
4. `test_store_stats_persists_receptions` — insert branch (step 8)
5. `test_store_stats_updates_receptions` — update branch (step 8)

Each of tests 1, 3, 4, 5 must be shown to fail when its fix is reverted.

## Done criteria

ALL must hold:

- [ ] `.venv/Scripts/python.exe -m pytest backend/tests -q` exits 0, 0 failed
- [ ] The step-2 probe prints `TRAINED? True`
- [ ] `grep -rn 'Game.status == "completed"' backend/` returns no matches
- [ ] `grep -n "calculate_confidence(" backend/analysis/variants/*.py | grep -vc "self.thresholds"` returns `0`
- [ ] `grep -c receptions backend/collectors/player_stats/collector.py` returns ≥ 1
- [ ] Step 5's threshold test demonstrably changes tier when a
      `CalibrationHistory` row is inserted
- [ ] Mutation checks in steps 3, 5, 8 were each run and failed as expected
- [ ] `git diff --name-only` contains no file outside the In-scope list
- [ ] `plans/README.md` status row for 002 updated

## STOP conditions

Stop and report back (do not improvise) if:

- The baseline suite in step 1 is not `395 passed`.
- After step 2 the probe still prints `TRAINED? False` — there is a second
  blocker in the training path; report the log line rather than changing more.
- Turning on the calibrated model changes **existing** test expectations. Some
  tests may assert heuristic-fallback probabilities. If an existing test fails
  after step 2, do **not** edit the assertion to match the new number — report
  which test and what the two values are. That is a judgment call about model
  behavior, not a mechanical fix.
- Wiring thresholds (step 5) changes pick counts in existing backtest tests.
  Same rule: report, don't re-baseline.
- You find yourself needing to modify `backend/analysis/recalibrator.py` — it
  is explicitly out of scope.
- Any `"completed"` string outside `calibrated_model.py` looks like it needs
  changing. It does not; those are `BacktestRun.status`.

## Maintenance notes

- **Part A turns on a model that has never run in production.** Expect
  predicted probabilities to move the first time the nightly job runs against
  the real DB. That is the intended effect, but the first day's picks after
  deploy deserve a manual look.
- **Part B makes the nightly recalibration job load-bearing.** Its computation
  has two known bugs that are deliberately *not* fixed here (missing sport
  filter; pushes counted as losses in `recalibrator.py:45-64`). Until those are
  fixed, the thresholds now flowing into production are computed from
  cross-sport pooled data with pushes treated as losses. **This is a real
  consequence of this plan** — consider fixing `recalibrator.py` immediately
  after, and flag it to the operator when reporting completion.
- Part C's `_STAT_FIELDS` tuple is now the single source of truth. Any new
  `PlayerStat` column must be added there *and* to the model — a reviewer
  should check both.
- A future change adding a new `Game.status` value must check
  `calibrated_model.py` alongside every other `"final"` comparison; consider a
  shared `FINAL_STATUSES` constant if a third status ever becomes gradeable.

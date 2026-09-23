# Plan 019: The morning slate prices MLB with the day's starting pitchers

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md` — unless a reviewer dispatched you and told you they
> maintain the index.
>
> **Drift check (run first)**:
> `git diff --stat 84dc78c..HEAD -- backend/pipeline/scheduler.py backend/tests/test_morning_slate.py`
> If any in-scope file changed since this plan was written, compare the
> "Current state" excerpts against the live code before proceeding; on a
> mismatch, treat it as a STOP condition.

## Status

- **Priority**: P1
- **Effort**: S
- **Risk**: LOW
- **Depends on**: none to build; **plan 018** for the change to have any
  effect on the numbers (until 018 lands, the ensemble discards the scores
  this plan supplies).
- **Category**: bug
- **Planned at**: commit `84dc78c`, 2026-09-23
- **Executor model**: `sonnet` (Agent tool `model` value). The helper and fixtures are literal, but test 4 has a conditional branch (count the definition plus one call, or fall back to a regex on call sites) and Step 4 asks for a next-morning log check to be interpreted and reported. That interpretation is prose work; mid-tier is the floor. `haiku` would be adequate for Steps 1-2 alone, but splitting the plan is not worth the hand-off.

## Why this matters

The 11 ET digest reads the picks the 8 ET morning scout wrote. The scout
prices the whole day's slate through `fetch_odds_and_pick`, which calls
`generate_and_store_picks` **without** pitcher scores. Only the per-game
window job, which fires two hours before first pitch, fetches the probable
pitchers. So every MLB pick in the email is priced on a neutral starter,
and the corrected pick that the window writes later never reaches a reader.

After this plan both callers derive the pitcher scores through one helper,
so the morning slate and the window refresh price the same matchup.

## Current state

- `backend/pipeline/scheduler.py` — APScheduler jobs. Two functions call
  `generate_and_store_picks`:
  - `fetch_odds_and_pick(config, engine, sports)` at line 176, used by the
    morning scout for windowless sports and for the day's slate;
  - `_run_window(config, engine, sport, window)` at line 420, the two-hour
    pre-game job.
- `fetch_pitcher_scores_for_date(target_date)` at line 579 (async; hits the
  MLB Stats API) and `_remap_pitcher_scores_to_game_ids(session, scores_by_abbr, target_date)`
  at line 715, both in the same file.
- `backend/tests/test_morning_slate.py` — tests for `fetch_odds_and_pick`,
  with a `calls` fixture that fakes `get_session`, `fetch_and_store_odds`
  and `generate_and_store_picks` and records the kwargs it saw.

The window's pitcher block, `scheduler.py:437-447`:

```python
            pitcher_scores = None
            if sport == "mlb":
                try:
                    scores_by_abbr = asyncio.run(fetch_pitcher_scores_for_date(today))
                    pitcher_scores = _remap_pitcher_scores_to_game_ids(session, scores_by_abbr, today)
                except Exception as exc:
                    logger.warning(
                        "MLB pitcher fetch failed (%s); proceeding with neutral pitcher scores", exc
                    )
                    pitcher_scores = None
            count = generate_and_store_picks(session, game_strategy.id, today, pitcher_scores=pitcher_scores)
```

The slate's call, `scheduler.py:207-216`:

```python
        strategy = session.query(StrategyModel).filter(
            StrategyModel.is_active == True,           # noqa: E712
            StrategyModel.strategy_type == "game",
        ).first()
        if strategy is not None:
            count = generate_and_store_picks(
                session, strategy.id, et_today(), sports=tuple(sports))
            logger.info("Generated %d picks for %s", count, ", ".join(sports))
```

`generate_and_store_picks` (in `backend/pipeline/pick_generator.py`)
accepts `pitcher_scores: dict[int, dict[str, float]] | None` and attaches
them per MLB game id; refreshes an existing un-started pick in place when
inputs change, so a morning pick re-priced by the window is one row, not two.

Repo rule that applies here (from the global `CLAUDE.md`): **derive, don't
duplicate** — when two code paths must agree, make one call the other
rather than copying the block.

Convention in this file: every collector call is wrapped so a failure is
logged and the rest of the morning continues (see the comments at
`scheduler.py:193-196`). A pitcher fetch failure must not stop odds or
picks.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Slate tests | `.venv/Scripts/python.exe -m pytest backend/tests/test_morning_slate.py -q` | all pass |
| Scheduler tests | `.venv/Scripts/python.exe -m pytest backend/tests/test_morning_slate.py backend/tests/test_windowless_sports.py backend/tests/test_mlb_integration.py -q` | all pass |
| Full suite | `.venv/Scripts/python.exe -m pytest backend/tests -q` | all pass |

Run from the repo root with the Bash tool. Tests block real network at the
socket layer (`conftest.no_real_network`), so the pitcher fetch must be
monkeypatched in every test that reaches it.

## Scope

**In scope**:
- `backend/pipeline/scheduler.py`
- `backend/tests/test_morning_slate.py`

**Out of scope**:
- `backend/pipeline/pick_generator.py` — already accepts the argument.
- `backend/collectors/mlb_stats.py` — the fetch works; it is just not called
  from the slate.
- `backend/analysis/variants/ensemble.py` — plan 018.
- The 8/9/10 ET retry structure of `morning_scout` — unchanged.

## Git workflow

- Branch: `advisor/019-morning-slate-prices-the-pitcher`
- Conventional commit, e.g. `fix(mlb): price the morning slate with the day's starters`
- Do NOT push or open a PR unless the operator instructed it.

## Steps

### Step 1: Extract the helper

In `scheduler.py`, above `fetch_odds_and_pick`, add:

```python
def mlb_pitcher_scores(session, today) -> dict[int, dict[str, float]] | None:
    """Today's probable-pitcher skill scores keyed by MLB game id, or None.

    The one place the pitcher fetch is wired, so the morning slate and the
    pre-game window price the same starters. Until this existed only the
    window fetched them, and the 11 ET digest -- which reads the morning
    picks -- went out priced on a neutral starter every day.

    None on any failure, logged: a dead MLB Stats API must not cost the
    odds fetch or the picks, only the pitcher term.
    """
    try:
        scores_by_abbr = asyncio.run(fetch_pitcher_scores_for_date(today))
        return _remap_pitcher_scores_to_game_ids(session, scores_by_abbr, today)
    except Exception as exc:
        logger.warning(
            "MLB pitcher fetch failed (%s); proceeding with neutral pitcher scores", exc)
        return None
```

Replace the block at `scheduler.py:437-446` in `_run_window` with:

```python
            pitcher_scores = mlb_pitcher_scores(session, today) if sport == "mlb" else None
```

**Verify**: `.venv/Scripts/python.exe -m pytest backend/tests/test_mlb_integration.py backend/tests/test_morning_slate.py -q` → all pass.

### Step 2: Use it in the slate

In `fetch_odds_and_pick`, change the pick call to:

```python
        if strategy is not None:
            today = et_today()
            # The slate is what the 11 ET digest reads. Without the starters
            # here, every MLB pick in the email priced a neutral pitcher.
            pitcher_scores = (mlb_pitcher_scores(session, today)
                              if "mlb" in sports else None)
            count = generate_and_store_picks(
                session, strategy.id, today, sports=tuple(sports),
                pitcher_scores=pitcher_scores)
            logger.info("Generated %d picks for %s", count, ", ".join(sports))
```

**Verify**: `.venv/Scripts/python.exe -m pytest backend/tests/test_morning_slate.py -q` → all pass (the existing fake `fake_picks(session, strategy_id, target_date, **kw)` accepts the new kwarg).

### Step 3: Tests

In `backend/tests/test_morning_slate.py`, extend the `calls` fixture so
`fake_picks` also records `kw.get("pitcher_scores")`:

```python
    seen = {"odds": [], "picks": [], "pitchers": []}
    ...
    def fake_picks(session, strategy_id, target_date, **kw):
        seen["picks"].append(tuple(kw.get("sports") or ()))
        seen["pitchers"].append(kw.get("pitcher_scores"))
        return 0
```

Add a second fixture:

```python
@pytest.fixture()
def pitcher_fetch(monkeypatch):
    """Fake the MLB Stats API round trip; records whether it was asked."""
    state = {"asked": 0, "raise": False}

    async def fake_fetch(target_date):
        state["asked"] += 1
        if state["raise"]:
            raise RuntimeError("mlb api down")
        return {("NYY", "BOS"): {"home": 0.7, "away": 0.4}}

    monkeypatch.setattr(sch, "fetch_pitcher_scores_for_date", fake_fetch)
    monkeypatch.setattr(sch, "_remap_pitcher_scores_to_game_ids",
                        lambda session, by_abbr, today: {101: by_abbr[("NYY", "BOS")]})
    return state
```

Tests (names as given), each calling
`sch.fetch_odds_and_pick(CONFIG, object(), [...])`:

1. `test_slate_with_mlb_passes_pitcher_scores` — sports `["mlb"]`; assert
   `pitcher_fetch["asked"] == 1` and
   `calls["pitchers"] == [{101: {"home": 0.7, "away": 0.4}}]`.
2. `test_slate_without_mlb_does_not_ask_for_pitchers` — sports `["nfl"]`;
   assert `pitcher_fetch["asked"] == 0` and `calls["pitchers"] == [None]`.
3. `test_pitcher_fetch_failure_still_generates_picks` — set
   `pitcher_fetch["raise"] = True`, sports `["mlb", "nfl"]`; assert
   `calls["picks"] == [("mlb", "nfl")]` and `calls["pitchers"] == [None]`.
4. `test_window_and_slate_share_one_helper` — a structural guard for the
   derive-don't-duplicate rule:
   `import inspect; src = inspect.getsource(sch)` and assert
   `src.count("fetch_pitcher_scores_for_date(") == 2` (the definition and
   the single call inside `mlb_pitcher_scores`). If the file already has a
   docstring mention that changes the count, count call sites with
   `re.findall(r"asyncio\.run\(fetch_pitcher_scores_for_date", src)` and
   assert exactly one.

**Mutation check, required**: temporarily remove `pitcher_scores=pitcher_scores`
from the slate's `generate_and_store_picks` call; test 1 must fail. Restore
and report.

**Verify**: `.venv/Scripts/python.exe -m pytest backend/tests/test_morning_slate.py -q` → all pass, four more than before.

### Step 4: Full suite, then a production check the next morning

**Verify**: `.venv/Scripts/python.exe -m pytest backend/tests -q` → all pass.

After the change is deployed and the scheduler restarted (restarting is part
of shipping in this repo — see `plans/README.md`'s scheduler notes), the
next morning's `scheduler.log` must show, in order: `Morning slate for
<date>: ... mlb ...`, then either a `MLB pitcher remap` warning per
unmatched game or none, then `Generated N picks for ... mlb`. Confirm with:

`grep -n "Morning slate\|MLB pitcher\|Generated .* picks" scheduler.log | tail -8`

Then confirm the morning MLB picks carry the factor (needs plan 018):

`.venv/Scripts/python.exe -c "import sqlite3;c=sqlite3.connect('file:sports_picks.db?mode=ro',uri=True);print(c.execute(\"select count(*),sum(rationale_json like '%pitcher_edge%') from picks p join games g on g.id=p.game_id where g.sport='mlb' and g.date=date('now','localtime')\").fetchone())"`

→ second number > 0 on a day with announced starters. (Report the numbers;
zero on a day without announced starters is not a failure.)

## Test plan

- Four new tests in `backend/tests/test_morning_slate.py` per Step 3,
  modeled on the file's existing `calls` fixture tests.
- Verification: the Scheduler tests command → all pass.

## Done criteria

- [ ] `.venv/Scripts/python.exe -m pytest backend/tests -q` exits 0
- [ ] `grep -c "asyncio.run(fetch_pitcher_scores_for_date" backend/pipeline/scheduler.py` → `1`
- [ ] `grep -n "mlb_pitcher_scores(" backend/pipeline/scheduler.py` shows the definition and two call sites
- [ ] Mutation check performed and reported
- [ ] No files outside the in-scope list are modified (`git status`)
- [ ] `plans/README.md` status row updated

## STOP conditions

Stop and report back if:

- `fetch_odds_and_pick` or `_run_window` no longer match the excerpts.
- `generate_and_store_picks` no longer accepts `pitcher_scores` (check its
  signature in `backend/pipeline/pick_generator.py`).
- The existing `test_morning_slate.py` `calls` fixture has changed shape so
  the kwarg recording in Step 3 cannot be added without rewriting other
  tests.
- The next-morning log check shows the slate ran but no `Generated` line
  for mlb — that is a different failure, not this plan's.

## Maintenance notes

- Probable pitchers are usually posted the evening before, but a late
  scratch changes the matchup after 8 ET. The window refresh two hours
  before first pitch re-prices the pick in place; the email will carry the
  morning starter. That is acceptable and should be stated in the email
  footer if readers ask.
- Each MLB game costs two MLB Stats API requests for pitcher logs. The
  slate adds one such pass per morning; no Odds API credits are involved.
- If a third caller of `generate_and_store_picks` for MLB ever appears
  (a manual `fetch_odds_now` script, say), route it through
  `mlb_pitcher_scores` — test 4 will not catch a caller that skips the
  helper entirely.

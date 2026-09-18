# Plan 012: Prop projections have never seen recent form

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
> `git diff --stat da1a767..HEAD -- backend/analysis/prop_analyzer.py backend/pipeline/prop_pipeline.py backend/collectors/player_stats/ backend/collectors/espn_box_score.py`
> If any in-scope file changed since this plan was written, compare the
> "Current state" excerpts against the live code before proceeding; on a
> mismatch, treat it as a STOP condition.

**Goal:** Make prop projections actually use recent game-by-game form, so the
60% of the projection weight the design allocates to it stops being discarded
and the variance-based probability model starts running.

**Architecture:** The data problem is already solved — plan 010's
`collectors/espn_box_score.py` writes exactly the `game_log` rows
`prop_pipeline` reads. Three things still block it: a `TypeError` in a dead
fetch path that masks the gap as a "source failure", an unbounded `recent`
query that would be lookahead the moment it has data, and a threshold set
shared between two edge formulas on different scales.

**Tech Stack:** Python 3.14 local / 3.12 production, SQLAlchemy 2.0, scipy,
pytest.

**Spec:** none. Derived from the plan 010 Task 3 spike and the investigation
recorded here. Read "Why this matters" as the spec.

## Status

- **Priority**: P1
- **Effort**: M
- **Risk**: MEDIUM — this changes what every prop pick's confidence means, so
  it invalidates the plan 010 baseline rather than extending it
- **Depends on**: plan 010 (done) supplies the `game_log` data
- **Category**: bug
- **Planned at**: commit `da1a767`, 2026-09-17
- **Blocks**: any claim that prop confidence is meaningful; `digest.enabled`

## Global Constraints

- Python floor `>=3.12`; CI runs **3.12 and 3.14** and both must pass.
- Dependencies pinned in `constraints.txt`; scipy is already a dependency.
- **Never run a backfill against `sports_picks.db` directly.** Copy it first.
- **This plan invalidates the plan 010 prop baseline.** Do not compare new
  numbers against tier win rates measured before it. Say so in the report.
- Tests must be mutation-proved.

## Why this matters

`PropAnalyzer` (`analysis/prop_analyzer.py:83-87`) is built to blend two
inputs:

```python
season_weight: float = 0.4,
recent_weight: float = 0.6,
```

**Recent form carries 60% of every projection. It has never been supplied.**
`prop_pipeline.py:156-158` reads it from the database:

```python
recent = (session.query(PlayerStat)
    .filter_by(player_name=prop.player_name, stat_type="game_log")
    .order_by(PlayerStat.game_date.desc()).limit(5).all())
```

and before plan 010 there were **0 rows** with `stat_type="game_log"`. So
`recent_val` was always `None` and `prop_analyzer.py:133-134` took the
season-only branch on every prop ever generated:

```python
elif season_val is not None:
    projection = season_val        # the branch that has always run
```

**Worse, the probability model has never run either.**
`prop_analyzer.py:188`:

```python
use_distribution = _HAS_SCIPY and len(game_values) >= _MIN_VARIANCE_SAMPLES
```

`game_values` comes from the same empty `recent_games`, and
`_MIN_VARIANCE_SAMPLES` is 3, so `use_distribution` has always been `False`.
Every prop went down the fallback at `prop_analyzer.py:222-226`:

```python
diff = projection - line
edge_pct = abs(diff / line) * 100
```

That is not a probability. It is a percentage distance from the line, **divided
by the line**, so a small line manufactures a large edge: a 0.5 line with a 1.2
projection reports a 140% "edge". The distribution branch it replaced computes
`edge_pct = (directional_prob - 0.5) * 200` — a real probability on a bounded
scale.

Evidence consistent with that, from the 82 production props (graded on a copy
during plan 010):

```
  confidence | n  | median line | edge_pct range
           5 | 39 |         3.5 | 20-122
           4 | 17 |         6.5 | 16-20
           3 | 11 |         4.5 | 10-15
           2 | 11 |         4.5 | 8-10
           1 |  4 |         7.5 | 5-6
```

Tier 5 has the **lowest** median line and tier 1 the **highest**. That is what
dividing by the line predicts. **Directional, not established** — the middle
tiers are not monotonic and n=82.

**The two scales were measured directly** against the live analyser while this
plan was written. Same prop, `line=20.5`, `season_avg` 24.0:

| input | `edge_pct` | confidence |
|---|---|---|
| season only — today's behaviour | **17.07** | 4 |
| plus 3 game logs (18, 22, 25) | **45.01** | **5** |

The same prop scores 17 or 45 depending only on whether history exists, and
crosses two confidence tiers. And the small-line case, `line=0.5` with a
projection of 1.2:

| input | `edge_pct` | confidence |
|---|---|---|
| season only | **140.0** | 5 |

So the inflation is not hypothetical: a half-point line reports a 140% "edge"
and lands at maximum confidence.

**`_HAS_SCIPY` is `True` here (scipy 1.18.1).** `use_distribution` was
therefore `False` purely because `game_values` was empty — the distribution
model is installed, working, and ready the moment the data exists. Nothing
needs to be built for it.

### Why the data was missing

Three independent failures, established by the plan 010 Task 3 spike:

1. **`nba_api_source.py:157` passes `last_n_games=n` to `PlayerGameLog`, which
   has no such parameter.** It raises `TypeError` before any HTTP request, and
   the fallback chain's blanket `except Exception` logs it as
   `"{source} failed for {player}"` — indistinguishable in the log from "the
   source had no data". Line 167 already slices `games[:n]`, so the kwarg is
   redundant as well as wrong.
2. **`stats.nba.com` read-times-out from this machine** — 30s, 60s and 90s all
   failed, while ESPN and BallDontLie answered on the same run. Deleting the
   kwarg alone therefore buys nothing.
3. **`EspnStatsSource` asks `site.api.espn.com/.../nba/athletes`, which 404s**
   with or without the `search` param.

**None of that needs fixing to get the data.** Plan 010 already ships a working
collector (`collectors/espn_box_score.py`) that writes `game_log` rows keyed on
final games, verified live. `prop_pipeline` reads `recent` from the database, so
the collector is the supply. The pre-game fetch at `prop_pipeline.py:80-83` is
a second, broken path to the same table.

## Current state (verified 2026-09-17 at `da1a767`)

```
player_stats stat_type='game_log'  0 rows in production
props                              82, all analysed season-only
use_distribution                   has never been True
```

`prop_pipeline.py:78-83` — the fetch that has never produced a row:

```python
count = collector.store_stats(session, stats, "season_avg", team.id, team.sport, source)
stats_count += count
for s in stats:
    recent, rsource = await collector.fetch_player_recent(team.sport, s["player_name"], n=5)
    if recent and rsource:
        collector.store_stats(session, recent, "game_log", team.id, team.sport, rsource)
```

## File structure

| File | Responsibility | Task |
|---|---|---|
| `backend/collectors/player_stats/nba_api_source.py` | drop the dead kwarg so failures read as failures | 1 |
| `backend/pipeline/prop_pipeline.py` | remove the broken fetch; bound `recent` to the prop's game | 1, 2 |
| `backend/analysis/prop_analyzer.py` | make the two edge scales impossible to mix silently | 3 |
| `backend/tests/test_prop_recent_form.py` | **new** | 1, 2, 3 |

---

### Task 1: Stop the dead fetch from masking the gap

**Files:**
- Modify: `backend/collectors/player_stats/nba_api_source.py:157`
- Modify: `backend/pipeline/prop_pipeline.py:80-83`
- Test: `backend/tests/test_prop_recent_form.py` (new)

**Interfaces:**
- Consumes: nothing.
- Produces: `fetch_player_recent` still exists with the same signature; it no
  longer raises `TypeError` before its request. `run_prop_pipeline` no longer
  calls it.

- [ ] **Step 1: Write the failing test**

```python
def test_fetch_recent_games_does_not_raise_before_making_a_request(monkeypatch):
    """`last_n_games` is not a PlayerGameLog parameter. The TypeError fired
    before any HTTP call and the chain's blanket except logged it as a source
    failure -- so an unusable call site was indistinguishable, in the log,
    from a source that had no data.

    Asserted by capturing the kwargs rather than by hitting the network:
    stats.nba.com read-times-out from here, which is a separate problem.
    """
    import inspect
    from nba_api.stats.endpoints import PlayerGameLog
    from backend.collectors.player_stats import nba_api_source

    allowed = set(inspect.signature(PlayerGameLog.__init__).parameters)
    src = inspect.getsource(nba_api_source.NbaApiSource.fetch_recent_games)
    used = set(re.findall(r"PlayerGameLog\(([^)]*)\)", src))
    for call in used:
        for kwarg in re.findall(r"(\w+)\s*=", call):
            assert kwarg in allowed, (
                f"PlayerGameLog has no parameter {kwarg!r}; "
                f"the call raises TypeError before any request")
```

- [ ] **Step 2: Run it and watch it fail**

```
.venv/Scripts/python.exe -m pytest backend/tests/test_prop_recent_form.py -q
```

Expected: FAIL naming `last_n_games`.

- [ ] **Step 3: Delete the kwarg**

`nba_api_source.py:157`, inside `_get_gamelog`:

```python
                log = PlayerGameLog(player_id=pid, last_n_games=n)
```

becomes

```python
                # No last_n_games parameter exists; the slice below does it.
                log = PlayerGameLog(player_id=pid)
```

Line 167's `for g in games[:n]:` already limits the result, so behaviour is
unchanged apart from no longer raising.

- [ ] **Step 4: Run it and watch it pass.**

- [ ] **Step 5: Remove the pre-game fetch**

In `prop_pipeline.py`, delete these four lines:

```python
            for s in stats:
                recent, rsource = await collector.fetch_player_recent(team.sport, s["player_name"], n=5)
                if recent and rsource:
                    collector.store_stats(session, recent, "game_log", team.id, team.sport, rsource)
```

and put a comment in their place:

```python
            # game_log rows come from collectors/espn_box_score.py, which runs
            # post-game from morning_scout. Fetching "last 5" here was a second
            # path to the same table that never produced a row: nba_api raised
            # before its request, stats.nba.com times out from this network,
            # and the ESPN athlete endpoint 404s. Removing it drops one HTTP
            # call per player per run for no loss.
```

Add a test asserting `run_prop_pipeline` makes no `fetch_player_recent` call —
monkeypatch it to `pytest.fail` and run the pipeline against a seeded session.

- [ ] **Step 6: Full suite, then commit**

```
.venv/Scripts/python.exe -m pytest backend/tests -q
git commit -m "fix(collectors): drop the nonexistent last_n_games kwarg; stop the dead pre-game fetch"
```

---

### Task 2: Bound `recent` to the game being predicted

Do this **before** Task 3. The moment `game_log` has rows, the unbounded query
becomes lookahead, and Task 3 makes those rows load-bearing.

**Files:**
- Modify: `backend/pipeline/prop_pipeline.py:156-158`
- Test: `backend/tests/test_prop_recent_form.py`

**Interfaces:**
- Consumes: Task 1.
- Produces: `recent` contains only rows with `game_date` strictly earlier than
  the prop's game date.

- [ ] **Step 1: Write the failing test**

```python
def test_recent_form_excludes_the_game_being_predicted_and_anything_after(db_session):
    """Same defect shape as plan 008's team-stat scoping (`399ac79`).

    The query takes the 5 most recent game_log rows with no date bound. While
    the table was empty that was harmless. With data it reads the future: a
    prop on a game that has since been played would be analysed using that
    game's own box score, and any later game's too.
    """
```

Seed a scheduled game on day 10 with a prop, and `game_log` rows on days 8, 9,
**10** and 11 for that player. Assert the analyser receives only days 8 and 9.

- [ ] **Step 2: Run it and watch it fail** — expect 4 rows where 2 are correct.

- [ ] **Step 3: Add the bound**

```python
        recent = (session.query(PlayerStat)
            .join(Game, Game.id == prop.game_id)
            .filter(PlayerStat.player_name == prop.player_name,
                    PlayerStat.stat_type == "game_log",
                    PlayerStat.game_date < Game.date)
            .order_by(PlayerStat.game_date.desc()).limit(5).all())
```

Read the surrounding code first and match how `game_teams` already resolves the
prop's game — if a `Game` object is in scope, filter on its `date` directly
rather than adding a join.

- [ ] **Step 4: Run it, watch it pass, mutation-prove** by removing the bound
  and confirming the test fails.

- [ ] **Step 5: Full suite, then commit.**

---

### Task 3: Make the two edge scales impossible to mix silently

**This is the task with a real decision in it.** Tasks 1 and 2 are mechanical;
this one changes what confidence means.

Once `game_log` has three rows for a player, `use_distribution` flips to `True`
for that prop and its `edge_pct` becomes `(directional_prob - 0.5) * 200` — a
probability on a bounded 0-100 scale. A prop with two rows keeps
`abs(diff / line) * 100`, which is unbounded and line-size dependent (the
current data reaches 122).

**Both are then fed to the same `calculate_prop_confidence(edge_pct)`
thresholds, and ranked against each other in the digest.** That is worse than
either alone: a 5-star from one formula and a 5-star from the other would not
mean the same thing, and the digest sorts them together.

**Files:**
- Modify: `backend/analysis/prop_analyzer.py:188-240`
- Test: `backend/tests/test_prop_recent_form.py`

- [ ] **Step 1: Decide, and record the decision in this plan before coding**

Two defensible options. **Recommended: (a).**

- **(a) Require the distribution path.** Return `None` when
  `len(game_values) < _MIN_VARIANCE_SAMPLES`, so a prop with too little history
  is not analysed at all. One formula, one scale, one meaning for a star.
  Cost: fewer props, and none at all until the collector has three games for a
  player. That is the honest cost of having had no measurement.
- **(b) Keep both, and tag which one produced the pick.** Add the formula to
  `PropAnalysis` and to `rationale_json`, and make the digest refuse to rank
  across formulas. More code, and it preserves a number that is not a
  probability.

**Do not proceed until this is chosen.** Under (a) the remaining steps are as
written; under (b) they need rewriting and this plan should be amended first.

- [ ] **Step 2: Write the failing test (option (a))**

```python
def test_a_prop_with_too_little_history_is_not_analysed_at_all():
    """Two edge formulas on different scales cannot share one threshold set.
    `(prob - 0.5) * 200` is bounded 0-100; `abs(diff / line) * 100` is
    unbounded and inflates small lines -- production reached 122. A 5-star
    from each does not mean the same thing, and the digest ranks them together.
    """
    analyzer = PropAnalyzer()
    assert analyzer.analyze(prop, season_avg=stat(points=20.0), recent_games=[]) is None


def test_a_prop_with_enough_history_is_analysed_on_the_distribution():
    result = analyzer.analyze(prop, season_avg=stat(points=20.0),
                              recent_games=[stat(points=v) for v in (18, 22, 25)])
    assert result is not None
    assert 0 <= result.edge_pct <= 100        # bounded: it is a probability
```

- [ ] **Step 3: Run both, watch the first fail, then implement.**

- [ ] **Step 4: Mutation-prove** — restore the fallback branch and confirm the
  first test fails.

- [ ] **Step 5: Run the full suite.** Expect prop tests elsewhere to fail:
  several fixtures pass `recent_games=[]` and rely on the fallback. **Each one
  needs its fixture updated to supply three game logs, not its assertion
  loosened.** If a test cannot be expressed with real game logs, that is a
  STOP condition — it means option (a) breaks a behaviour someone wanted.

- [ ] **Step 6: Commit.**

---

### Task 4: Re-measure, and say plainly that the old baseline is void

**Files:**
- No production code. Uses `backend/analysis/prop_calibration.py` from plan 010.

- [ ] **Step 1: Against a copy**, run the chain from plan 010's notes: collect
  box scores, regenerate props for a date with games, grade, then
  `prop_calibration`.

- [ ] **Step 2: Report three things:**
  1. How many props are analysed now versus before. Under option (a) this
     **will drop**, possibly to zero until the collector has three games per
     player. A drop is the expected outcome, not a failure.
  2. Whether `use_distribution` is now `True` — log it, do not assume it.
  3. The new per-tier win rates, with effective n clustered on game.

- [ ] **Step 3: State that the plan 010 baseline (tier 5 at 75.0%, tier 4 at
  50.0%) is no longer comparable.** Those numbers came from a projection using
  100% season average and an edge that divided by the line. They describe a
  different model. Put this in `plans/HANDOFF.md` next to the old numbers
  rather than deleting them.

---

## STOP conditions

1. **Task 3's decision is not made.** Do not pick (a) or (b) by starting to
   code. Record the choice in this file first.
2. **A test can only be made to pass by loosening an assertion** rather than
   by giving it real game logs. Under option (a) that means a wanted behaviour
   is being removed — report instead.
3. **Prop count drops to zero and stays there** after Task 3. Expected briefly;
   if the collector has run and no player anywhere has three game logs,
   something is wrong with the collector, not the analyser.
4. **You are about to "fix" `stats.nba.com`.** It read-times-out from this
   network; three attempts at 30s, 60s and 90s all failed. Nothing in this plan
   needs it.
5. **The full suite drops below 595 passing** at any point.

## Verification

Baseline before starting: **595 passing**, 0 failed, on 3.12 and 3.14.

## Out of scope

- **`MARKET_STAT_MAP` is duplicated** in `grader.py:13` and
  `prop_analyzer.py:12` (as `MARKET_TO_STAT`). A real drift risk, carried over
  from plan 010's out-of-scope list. Its own small plan.
- **`migrate_api_usage`'s `DROP TABLE`**, flagged in plan 011. Unrelated but
  still the sharpest object in the repo.
- **Whether `min_edge = 5.0` is right for a probability-scaled edge.** Under
  option (a) every edge becomes `(prob - 0.5) * 200`, so `min_edge = 5` means
  "52.5% or better" — plausible, but it was tuned against a different scale
  and nobody has checked it. Needs the Task 4 numbers first, then its own
  decision.
- **Recent form for non-NBA sports.** `espn_box_score.SPORT_PATHS` covers nba,
  nfl, ncaab and ncaaf, but only NBA has enough graded volume to measure.
  Extend once NBA is working.

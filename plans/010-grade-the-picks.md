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
- **Risk**: LOW-MEDIUM (was MEDIUM; the Task 3 spike resolved the open
  question on 2026-09-17 — ESPN supplies box scores free, no purchase needed)
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

> **DONE 2026-09-17 — `cfb276d`.** 564 -> 576 passing. Against a copy of
> production: **82 of 82 props resolved, 0 unresolved** (player_rebounds 24,
> player_points 24, player_assists 20, player_threes 14), so STOP condition 2
> was not triggered. Re-running reports 82 already populated — resumable.
>
> **Production prop data is NOT backfilled.** Only a copy was written. The
> live `picks` table does have the two columns, but every value is NULL — see
> the note below on how the columns got there.
>
> Pick construction moved into `_build_prop_pick(analysis, strategy_id)` so it
> is testable without running the async pipeline; Task 4 does not touch it.


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

> **Found while running Task 2, unrelated to it, and worth its own plan:**
> **importing `backend.api.main` runs migrations against whatever
> `sports_picks.db` is in the current working directory.**
> `main.py:128` is a module-level
> `app = create_app(os.environ.get("DATABASE_PATH", "sports_picks.db"))`,
> needed so `uvicorn backend.api.main:app` works (`Dockerfile:44`), and
> `create_app` calls `run_migrations`. Proven: dropping the two new columns
> from a copy and then merely importing the module put them back.
>
> That is how production gained `picks.prop_player` during this task without
> anyone backfilling it — running the test suite from the repo root is enough.
> The container is unaffected (`DATABASE_PATH=/tmp/sports_picks.db`).
>
> Today's migrations are additive so nothing was damaged, **but
> `migrate_api_usage` does `DROP TABLE api_usage`** under a schema condition.
> An import is therefore one condition away from dropping a production table.

### Task 3: Collect post-game player box scores

> **DONE 2026-09-17 — `fb50170`.** 552 -> 564 passing. Live smoke test against
> a copy trimmed to two games: both events resolved across the date offset, 36
> player rows written across four teams, all 36 `game_date` values matching our
> game dates, zero mismatched. Production untouched.
>
> **Two corrections for whoever writes the remaining tasks:**
> - **`url__regex` is not a pytest-httpx matcher** — it was invented in Step 2
>   below. This repo's tests match on exact `url=` strings (see
>   `test_mlb_stats.py:46`), and so do the ones that shipped.
> - **`PlayerStat.team_id` is `nullable=False`**, so `parse_box_score` also
>   returns `team_abbr`, taken from each block's `team.abbreviation`. Block
>   order is undocumented; inferring home/away from it would mislabel a whole
>   team. The shipped signature is unchanged otherwise.
>
> `collect_box_scores_for_final_games` **commits** (via `store_stats`), unlike
> most of `backend.pipeline`. Task 4 should not assume it can roll back a
> collection.


**This was the task that decided whether the plan is finishable.** The spike
has run: ESPN supplies what is needed, free. See the box below.

**Files:**
- Create: `backend/collectors/espn_box_score.py`
- Modify: `backend/pipeline/scheduler.py` (`morning_scout`)
- Test: `backend/tests/test_espn_box_score.py` (new)

Nothing under `collectors/player_stats/` is modified. That package stays
player-keyed and pre-game; this is game-keyed and post-game. The one thing
reused from it is `PlayerStatsCollector.store_stats`, called rather than
reimplemented.

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces, all in `backend/collectors/espn_box_score.py`:
  - `resolve_espn_event(sport: str, game_date: date, home_abbr: str, away_abbr: str, client: httpx.Client | None = None) -> str | None`
  - `parse_box_score(summary: dict) -> list[dict]` — each dict has
    `player_name` plus any of `minutes, points, rebounds, assists, threes,
    steals, blocks, turnovers`
  - `collect_box_scores_for_final_games(session, sport: str | None = None) -> int`
    — returns rows written
- And the rows themselves: `PlayerStat` with `stat_type="game_log"` and
  `game_date` set to **our `Game.date`**, which is the shape
  `grade_prop_pick` looks up via
  `filter_by(player_name=..., stat_type="game_log", game_date=game.date)`.
  Task 4 depends on that date being ours and not ESPN's.

> **SPIKE DONE 2026-09-17. Answer: ESPN, free, no purchase needed — but not
> the shape this task assumed.** STOP condition 1 is **not** triggered. Details
> below; Steps 2-5 still stand but build against ESPN, not `fetch_player_recent`.
>
> **The spike command in Step 1 below is wrong.** `PlayerStatsCollector.__init__`
> *requires* `fallback_chains`, so `PlayerStatsCollector()` raises `TypeError`
> before testing anything. Use `prop_pipeline.build_default_collector()`.
>
> **All three NBA sources fail, for three different reasons:**
>
> | source | failure | kind |
> |---|---|---|
> | `NbaApiSource` | `PlayerGameLog.__init__() got an unexpected keyword argument 'last_n_games'` | our bug |
> | `BallDontLieSource` | `401 Unauthorized` | needs a paid key |
> | `EspnStatsSource` | `404` on athlete search | our bug |
>
> 1. **`nba_api_source.py:157` passes `last_n_games=n`, which is not a
>    parameter of `PlayerGameLog`** (verified against the installed 1.11.4:
>    the real ones are `player_id`, `season`, `season_type_all_star`,
>    `date_from_nullable`, `date_to_nullable`, ...). Line 167 already does
>    `games[:n]`, so the kwarg is both wrong and redundant. **This TypeError
>    fires before any HTTP request, which masked everything below it.**
>    Removing it reveals the real problem: `stats.nba.com` **read-times-out
>    from this machine** — three attempts at 30s, 60s and 90s, all
>    `ReadTimeout`. Not a general network fault; ESPN and BallDontLie both
>    answered on the same run. **Treat nba_api as unavailable here.**
> 2. **BallDontLie needs a paid key.** `build_default_collector()` constructs
>    `BallDontLieSource()` with no key, so no `Authorization` header. Only
>    relevant if ESPN is abandoned.
> 3. **ESPN is reachable and free; the code points at a URL that does not
>    exist.** `site.api.espn.com/.../nba/athletes` returns 404 *with or
>    without* the `search` param. `sports.core.api.espn.com/v2/.../athletes`
>    returns 200, and so does the scoreboard.
>
> **The viable path is scoreboard -> summary, not athlete search.** Verified
> end to end:
>
> ```
> GET site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard?dates=YYYYMMDD   -> 200
> GET site.api.espn.com/apis/site/v2/sports/basketball/nba/summary?event=<id>          -> 200
>     boxscore.players[].statistics[0].athletes[] each carry:
>     labels = MIN, PTS, FG, 3PT, FT, REB, AST, TO, STL, BLK, OREB, DREB, PF, +/-
>     e.g. Isaiah Hartenstein ['18','12','6-11','0-0','0-0','7','3','1','0','0',...]
> ```
>
> That covers every NBA key in `MARKET_STAT_MAP`. Field mapping onto
> `_STAT_FIELDS`: `MIN`->minutes, `PTS`->points, `REB`->rebounds,
> `AST`->assists, `STL`->steals, `BLK`->blocks, `TO`->turnovers, and
> **`3PT` is a made-attempted string** (`"0-1"`), so `threes` is the part
> before the hyphen.
>
> **Mapping our games to ESPN events needs care — there is no shared id.**
> `Game` has no `espn_id` column (`models.py:19-33`); `historical.store_games`
> dedupes on date + teams. And **our dates run one day ahead of ESPN's**: of 8
> randomly sampled final NBA games, **7 matched at offset -1 and 1 matched
> exactly; 0 were missing**. That is UTC-vs-ET, and the offset games are the
> late western ones. Match on (ESPN date in ET, home+away abbreviations) with
> a +/-1 day window, or derive the ET date from `Game.start_time`. **Do not
> match on exact date alone — it silently finds ~1 in 8.**
>
> **Revised shape of this task:** not "repair `fetch_player_recent`" but "add
> an ESPN box-score collector keyed on final games". `fetch_player_recent`'s
> pre-game, last-5 contract is the wrong shape for grading anyway, as the
> Current state section already notes.

- [ ] ~~**Step 1: Spike — find out why `fetch_player_recent` returns nothing**~~ **DONE 2026-09-17 — see the box above.**

> The steps below were rewritten around ESPN after the spike. The original
> steps assumed the fix was repairing `fetch_player_recent`; it is not.

- [ ] **Step 2: Write the failing test for event resolution**

Network-free. `pytest-httpx` is already a dependency and the suite makes no
real requests today — keep it that way. Create
`backend/tests/test_espn_box_score.py`.

```python
import datetime
import pytest
from backend.collectors.espn_box_score import resolve_espn_event


def _scoreboard(*short_names):
    return {"events": [{"id": f"40{i}", "shortName": n}
                       for i, n in enumerate(short_names)]}


def test_resolves_an_event_listed_on_the_previous_espn_day(httpx_mock):
    """Our Game.date runs a day ahead of ESPN's for most games.

    Of 8 sampled final NBA games, 7 matched at ESPN offset -1 and 1 matched
    exactly. Searching the exact date alone finds about one game in eight, and
    the misses look like missing data rather than a UTC/ET offset.
    """
    httpx_mock.add_response(url__regex=r".*dates=20251231.*", json=_scoreboard("GS @ CHA"))
    httpx_mock.add_response(url__regex=r".*dates=20251230.*", json=_scoreboard("MIN @ LAL"))

    event_id = resolve_espn_event(
        sport="nba", game_date=datetime.date(2025, 12, 31),
        home_abbr="LAL", away_abbr="MIN",
    )
    assert event_id == "400"


def test_returns_none_rather_than_a_wrong_event_when_no_day_matches(httpx_mock):
    """A near-miss must not resolve. Grading the wrong game is worse than not
    grading: it produces a real, confident, wrong result."""
    httpx_mock.add_response(url__regex=r".*dates=.*", json=_scoreboard("BOS @ NY"))

    assert resolve_espn_event(
        sport="nba", game_date=datetime.date(2025, 12, 31),
        home_abbr="LAL", away_abbr="MIN",
    ) is None
```

- [ ] **Step 3: Run it and watch it fail**

```
.venv/Scripts/python.exe -m pytest backend/tests/test_espn_box_score.py -q
```

Expected: `ModuleNotFoundError: No module named 'backend.collectors.espn_box_score'`.

- [ ] **Step 4: Implement `resolve_espn_event`**

Create `backend/collectors/espn_box_score.py`. The search order is `0, -1, +1`
so an exact match always beats a neighbouring day.

```python
"""Post-game player box scores from ESPN, keyed on a finished Game.

Deliberately separate from ``collectors/player_stats``. That package is
player-keyed and pre-game: it answers "how has this player been doing lately",
which is a prediction feature. Grading needs the opposite -- "what did this
player actually do in that game" -- so this is keyed on a Game and only ever
runs after one is final.

ESPN shares no id with our ``Game`` (there is no ``espn_id`` column), so an
event is resolved by date plus team abbreviations. Our dates run one day ahead
of ESPN's for most games (UTC vs ET), so the search covers a +/-1 day window.
"""
import logging
from datetime import date, timedelta

import httpx

logger = logging.getLogger(__name__)

SPORT_PATHS = {
    "nba": "basketball/nba",
    "nfl": "football/nfl",
    "ncaab": "basketball/mens-college-basketball",
    "ncaaf": "football/college-football",
}
_BASE = "https://site.api.espn.com/apis/site/v2/sports"
_TIMEOUT = 20.0


def resolve_espn_event(sport: str, game_date: date, home_abbr: str,
                       away_abbr: str, client: httpx.Client | None = None) -> str | None:
    """The ESPN event id for this game, or None if no day in the window matches.

    Returns None rather than a best guess. Grading against the wrong game
    produces a confident wrong result, which is strictly worse than leaving the
    pick ungraded.
    """
    path = SPORT_PATHS.get(sport)
    if path is None:
        return None
    owns_client = client is None
    client = client or httpx.Client(timeout=_TIMEOUT)
    try:
        for delta in (0, -1, 1):
            stamp = (game_date + timedelta(days=delta)).strftime("%Y%m%d")
            try:
                resp = client.get(f"{_BASE}/{path}/scoreboard", params={"dates": stamp})
                resp.raise_for_status()
                events = resp.json().get("events", [])
            except Exception as exc:
                logger.warning("ESPN scoreboard %s failed: %s", stamp, exc)
                continue
            for event in events:
                name = event.get("shortName", "")
                if home_abbr in name and away_abbr in name:
                    return str(event.get("id"))
        return None
    finally:
        if owns_client:
            client.close()
```

- [ ] **Step 5: Run it and watch it pass**

```
.venv/Scripts/python.exe -m pytest backend/tests/test_espn_box_score.py -q
```

Expected: PASS, 2 tests.

- [ ] **Step 6: Write the failing tests for box-score parsing**

Both traps here produce plausible-looking rows, so both get a test.

```python
from backend.collectors.espn_box_score import parse_box_score

_LABELS = ["MIN", "PTS", "FG", "3PT", "FT", "REB", "AST", "TO", "STL", "BLK",
           "OREB", "DREB", "PF", "+/-"]


def _summary(*athletes):
    return {"boxscore": {"players": [
        {"statistics": [{"labels": _LABELS, "athletes": list(athletes)}]}
    ]}}


def test_threes_come_from_the_made_half_of_the_made_attempted_pair():
    """ESPN reports 3PT as "made-attempted" ("2-7"). Storing the raw string, or
    the attempted count, silently grades every threes prop against the wrong
    number."""
    rows = parse_box_score(_summary({
        "athlete": {"displayName": "Chet Holmgren"},
        "stats": ["26", "10", "3-8", "2-7", "4-6", "9", "2", "3", "1", "0",
                  "3", "6", "2", "+2"],
    }))
    assert len(rows) == 1
    assert rows[0]["threes"] == 2.0
    assert rows[0]["points"] == 10.0
    assert rows[0]["rebounds"] == 9.0
    assert rows[0]["assists"] == 2.0
    assert rows[0]["turnovers"] == 3.0
    assert rows[0]["steals"] == 1.0
    assert rows[0]["blocks"] == 0.0
    assert rows[0]["minutes"] == 26.0


def test_a_player_who_did_not_play_produces_no_row_at_all():
    """ESPN gives DNP players `stats: []` and `didNotPlay: true`.

    Zero-filling them is not a harmless default: a stored 0 rebounds makes
    "Under 1.5 Rebounds" grade as a WIN for a player who never took the court.
    An absent row leaves the pick ungraded, which is the honest outcome.
    """
    rows = parse_box_score(_summary({
        "athlete": {"displayName": "Bismack Biyombo"},
        "didNotPlay": True,
        "stats": [],
    }))
    assert rows == []


def test_labels_are_read_positionally_per_block_not_assumed():
    """Label order is a property of each statistics block. Hard-coding indexes
    works until a sport or a season reorders them, and then grades everything
    against the wrong column."""
    payload = {"boxscore": {"players": [{"statistics": [{
        "labels": ["PTS", "MIN", "REB"],
        "athletes": [{"athlete": {"displayName": "X"}, "stats": ["11", "30", "4"]}],
    }]}]}}
    rows = parse_box_score(payload)
    assert rows[0]["points"] == 11.0
    assert rows[0]["minutes"] == 30.0
```

- [ ] **Step 7: Run all three, watch them fail, then implement**

```python
#: ESPN box-score label -> PlayerStat field. Only labels that map to a
#: ``_STAT_FIELDS`` column appear; the rest (FG, FT, OREB, DREB, PF, +/-) are
#: ignored.
_LABEL_FIELD = {
    "MIN": "minutes", "PTS": "points", "REB": "rebounds", "AST": "assists",
    "STL": "steals", "BLK": "blocks", "TO": "turnovers",
}
#: Labels reported as "made-attempted"; only the made half is a stat we store.
_MADE_ATTEMPTED = {"3PT": "threes"}


def parse_box_score(summary: dict) -> list[dict]:
    """Player rows from an ESPN ``summary`` payload.

    Players who did not play are omitted entirely rather than zero-filled: a
    stored zero is a measurement, and grading an Under against a player who
    never appeared would record a confident wrong win.
    """
    rows: list[dict] = []
    for block in summary.get("boxscore", {}).get("players", []):
        for stat_block in block.get("statistics", []):
            labels = stat_block.get("labels", [])
            for entry in stat_block.get("athletes", []):
                values = entry.get("stats") or []
                if entry.get("didNotPlay") or len(values) != len(labels):
                    continue
                name = (entry.get("athlete") or {}).get("displayName", "")
                if not name:
                    continue
                row = {"player_name": name}
                for label, raw in zip(labels, values):
                    field = _LABEL_FIELD.get(label)
                    if field is not None:
                        try:
                            row[field] = float(raw)
                        except (TypeError, ValueError):
                            pass
                        continue
                    made_field = _MADE_ATTEMPTED.get(label)
                    if made_field is not None:
                        try:
                            row[made_field] = float(str(raw).split("-")[0])
                        except (TypeError, ValueError):
                            pass
                rows.append(row)
    return rows
```

- [ ] **Step 8: Run them, watch them pass, then write the storage test**

This is the test that matters most, and its trap is the subtlest in the plan:

```python
def test_rows_are_stored_against_OUR_game_date_not_espns(db_session, monkeypatch):
    """`grade_prop_pick` is looked up with `game_date=game.date` -- OUR date.

    ESPN's date for the same game is usually a day earlier. Storing ESPN's date
    writes rows that are present, correct, and permanently invisible to
    grading. Store our Game.date.
    """
```

Seed a final `Game` on a known date, monkeypatch `resolve_espn_event` and the
summary fetch to return a fixed payload, call
`collect_box_scores_for_final_games(session, "nba")`, then assert a `PlayerStat`
exists with `stat_type == "game_log"` and `game_date == game.date`.

- [ ] **Step 9: Implement `collect_box_scores_for_final_games`**

Reuse `PlayerStatsCollector.store_stats` rather than writing `PlayerStat` rows
directly — it already owns name normalisation and the upsert on
`(player_name, sport, stat_type, game_date)`, and duplicating that is exactly
how two paths drift. `store_stats` touches no fallback chain
(`_normalize_name` is a `@staticmethod`), so an empty collector is a legitimate
way to reach it:

```python
    collector = PlayerStatsCollector({})   # store_stats needs no chains
    collector.store_stats(session, rows, "game_log", team_id, sport, "espn")
```

Set `row["game_date"]` to the **Game's** date as an ISO string before storing —
`store_stats` parses `YYYY-MM-DD`.

Skip games that already have `game_log` rows so the collector is resumable, and
ask **per game** rather than tracking how far it got. Never assume contiguity.

- [ ] **Step 10: Call it from `morning_scout` before grading**

In `backend/pipeline/scheduler.py`, `morning_scout` currently opens with:

```python
        grade_pending_picks(session)
        grade_completed_games(session)
```

Collection must run **before** grading, and must not be able to stop it:

```python
        try:
            collect_box_scores_for_final_games(session)
        except Exception:
            logger.exception("Box score collection failed; grading with what is present")
        grade_pending_picks(session)
```

- [ ] **Step 11: Full suite, then commit**

```
.venv/Scripts/python.exe -m pytest backend/tests -q
```

Expected: 552 plus every test added here, 0 failed, and **no network access** —
if the suite slows noticeably, a test is reaching ESPN and must be stubbed.

```bash
git add backend/collectors/espn_box_score.py backend/pipeline/scheduler.py backend/tests/test_espn_box_score.py
git commit -m "feat(collectors): fetch post-game player box scores from ESPN"
```

- [ ] **Step 12: One live smoke test, by hand, not in the suite**

The unit tests prove the parsing; they cannot prove the endpoint still behaves.
Against a **copy** of the database:

```
cp sports_picks.db /tmp/box.db
```

Then call the collector against `/tmp/box.db` and confirm `player_stats` gains
rows with `stat_type='game_log'` whose `game_date` values match `games.date`
for the games collected. **If they are offset by a day, the date bug is in the
writer, and grading will find nothing at all.**

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
- **`nba_api_source.fetch_recent_games` is broken** and this plan no longer
  touches it. `nba_api_source.py:157` passes `last_n_games=n` to
  `PlayerGameLog`, which has no such parameter, so the call raises `TypeError`
  before any request and the fallback chain swallows it as a warning. Line 167
  already slices `games[:n]`, so the fix is deleting the kwarg. **But fixing it
  buys nothing here:** `stats.nba.com` read-times-out from this machine (30s,
  60s and 90s all failed) while ESPN and BallDontLie answered on the same run.
  It is still worth recording because it silently degrades the *pre-game* prop
  analysis: `prop_pipeline.py:80-83` asks for recent game logs to analyse props
  with, always gets nothing, and logs it as a source failure rather than a bug.
  Props are being analysed on season averages alone. Its own small plan.

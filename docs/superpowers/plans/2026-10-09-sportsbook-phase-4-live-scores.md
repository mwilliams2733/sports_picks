# Sportsbook Phase 4 — Live Scores — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Open bets come alive while their games are on.
- A `live_scores` job reads ESPN every 2 minutes, but only while a game is under way. It writes the running score, ESPN's one-line clock ("Q3 4:12", "Bot 7th", "Halftime") and status `in_progress`.
- My Bets shows a LIVE pill, the score line, and a Winning / Losing / Even tint for each game leg.

**Architecture:**
- **Backend:**
  - A new `games.live_detail` column, added by migration.
  - `ESPNCollector.fetch_scoreboard` also returns ESPN's `state` (pre/in/post) and `shortDetail`.
  - A new `backend/pipeline/live_scores.py` is registered in `configure_scheduler` as a 2-minute interval job.
  - `/users/{id}/bets` passes `live_detail` through.
  - The job **never writes `final`**: the existing results path keeps that, with its reconciliation and score healing.
- **Safety around the new status:**
  - A test proves the results path still moves an `in_progress` row to `final`.
  - A test proves `open_for_betting` refuses `in_progress`.
  - The settlement push rule for games stuck past their date now covers `in_progress` too, so a stuck live row can't hold stakes forever.
- **Frontend:**
  - Pure helpers `isLive` and `legTint`, plus a live `gameLine`, in `lib/bets.ts`.
  - `TicketCard` shows the pill and tints.
  - `lib/gameLock.ts` treats `in_progress` as locked, and reads naive UTC start times correctly (the Phase 2 note in memory).

**Tech Stack:** FastAPI, SQLAlchemy, APScheduler, httpx, pytest (+ pytest-asyncio); React 19, vitest; plain CSS.

**Spec:** `docs/superpowers/specs/2026-10-07-sportsbook-ui-design.md`. This plan implements §8, the Phase 4 row of §13, and §12's live-job tests.

## Global Constraints

- **The job never writes `final`** or any other status except `in_progress`, and never touches a `final` row (spec §8). It never changes `start_time`, `date`, `espn_id` or teams.
- **Combat sports are skipped (deviation from spec §8, decided while planning).** The spec lists MMA among the ESPN scoreboards, but:
  - `fetch_scoreboard` reads only `competitions[0]`, so an MMA card yields one bout;
  - no upcoming MMA row carries an `espn_id` (checked on the live db, 2026-10-09);
  - an `in_progress` MMA row would be skipped by `finalize_combat.stuck_combat`, which only finalises `scheduled` rows.

  Boxing has no ESPN feed at all. Combat legs therefore show "Live" from their start time with no score, as spec §8 already says for boxing.
- **Live means ESPN `state`, not `status.type.name`.** ESPN's halftime, end-of-period and rain-delay names map to `scheduled` in `STATUS_MAP`, but their state is `"in"`.
  - A `"post"` state counts only when the status is `final`.
  - A postponed or canceled game (`"post"`, non-final) is left to the results path.
- **Polling limits.** The job polls only games dated today or yesterday (ET). It makes no request at all when nothing is live, and one scoreboard request per (sport, ET date) when something is.
- **Migration.** `games.live_detail` (VARCHAR NULL) is additive and idempotent. On the live db it is applied with the scheduler and API **stopped and the db backed up first**, using the sqlite backup API (memory `sports-picks-db-snapshot`, spec §8).
- **Never run `npm run build` in the main working tree on a branch** (memory `sports-picks-dist-is-live`). Build only in the Task 6 worktree, and on master at merge.
- **Write code with the Write and Edit tools.** Anything containing backslashes (the regexes in `lib/bets.ts`) goes through them, never through Bash heredocs (memory `sports-picks-heredoc-backslash`).
- **Commits.**
  - Messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
  - Never stage `config.yaml`. Stage only the named files.
- **Mutation checks.** Mutation-check each guard. Delete the touched module's `.pyc` after every Python write and every restore.

## Review Focus

1. **Halftime, an end-of-period break, or a rain delay.** The game stays live and keeps its score. It never flips back to `scheduled` or loses its score. Test is in Task 2.
2. **A game postponed after kickoff** (ESPN `post`, status postponed). The job leaves it alone. The results path owns it, and settlement's push rule eventually returns stakes. Tests are in Tasks 2 and 3.
3. **A finished game is never made `final` by the job,** yet still reaches `final` with its score, and grades, through the results path. Tests are in Tasks 2 and 3.
4. **An ESPN outage or error for one sport** leaves that sport's rows untouched and doesn't stop other sports. The job never raises into the scheduler. Test is in Task 2.
5. **A spread or total leg sitting exactly on the line** shows "Even", not Winning or Losing, both for an `AWAY +9` that is down 9 and for an Over 49 at 49. Test is in Task 4.

---

### Task 1: `games.live_detail` and ESPN's live fields

**Files:**
- Modify: `backend/models.py` (`Game.live_detail`), `backend/database.py` (`migrate_game_live_detail` plus its `MIGRATIONS` entry), `backend/collectors/espn.py` (`fetch_scoreboard` adds `state` and `live_detail`)
- Test: `backend/tests/test_live_scores.py` (new, migration and collector part), `backend/tests/test_espn.py` (append)

**Interfaces:**
- **Produces:**
  - `Game.live_detail: str | None`.
  - `migrate_game_live_detail(engine)`.
  - `fetch_scoreboard` events gain `"state": "pre" | "in" | "post" | None` and `"live_detail": str | None`.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_live_scores.py`:

```python
"""Live scores (sportsbook spec 2026-10-07 §8): the live_detail column, the
live_scores job, and the guards around the new in_progress status."""
from sqlalchemy import create_engine, inspect, text

from backend.database import run_migrations
from backend.models import Base


def test_the_migration_adds_live_detail_to_an_existing_games_table():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with engine.begin() as conn:                       # an older database: no column yet
        conn.execute(text("ALTER TABLE games DROP COLUMN live_detail"))
    run_migrations(engine)
    assert "live_detail" in [c["name"] for c in inspect(engine).get_columns("games")]
    run_migrations(engine)                              # idempotent
```

Append to `backend/tests/test_espn.py`:

```python
LIVE_SCOREBOARD = {"events": [{
    "id": "401", "date": "2026-10-11T17:00Z",
    "status": {"type": {"name": "STATUS_HALFTIME", "state": "in", "shortDetail": "Halftime"}},
    "competitions": [{"competitors": [
        {"id": "1", "homeAway": "home", "team": {"abbreviation": "DAL", "displayName": "Dallas Cowboys"}, "score": "17"},
        {"id": "2", "homeAway": "away", "team": {"abbreviation": "TB", "displayName": "Tampa Bay Buccaneers"}, "score": "14"},
    ]}],
}]}


@pytest.mark.asyncio
async def test_fetch_scoreboard_reports_the_live_state_and_clock(monkeypatch):
    async def mock_get(self, url, **kwargs):
        return httpx.Response(200, json=LIVE_SCOREBOARD, request=httpx.Request("GET", url))
    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)
    [g] = await ESPNCollector().fetch_scoreboard("nfl", "20261011")
    # Halftime's NAME maps to "scheduled"; its STATE says the game is on.
    assert (g["status"], g["state"], g["live_detail"]) == ("scheduled", "in", "Halftime")
    assert (g["home_score"], g["away_score"]) == (17, 14)
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `.venv/Scripts/python -m pytest backend/tests/test_live_scores.py backend/tests/test_espn.py -q -p no:warnings`

Expected:
- The migration test fails at `DROP COLUMN live_detail`: no such column.
- The ESPN test fails with `KeyError: 'state'`.

- [ ] **Step 3: Add the column, the migration and the ESPN fields**

In `backend/models.py`, in `class Game`, after `away_score = Column(Integer, nullable=True)`, add:

```python
    #: ESPN's one-line game clock while a game is under way ("Q3 4:12",
    #: "Bot 7th", "Halftime", "Final"), written only by the live_scores job.
    live_detail = Column(String, nullable=True)
```

In `backend/database.py`, after `migrate_game_espn_id`, add:

```python
def migrate_game_live_detail(engine):
    """Add games.live_detail (sportsbook spec §8): ESPN's game clock, written
    by the live_scores job while a game is under way."""
    from sqlalchemy import inspect as sa_inspect, text
    inspector = sa_inspect(engine)
    if "games" in inspector.get_table_names():
        columns = [c["name"] for c in inspector.get_columns("games")]
        if "live_detail" not in columns:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE games ADD COLUMN live_detail VARCHAR"))
```

Then append `migrate_game_live_detail,` as the last entry of `MIGRATIONS`, after `migrate_pick_withdrawal,`.

In `backend/collectors/espn.py`, in `fetch_scoreboard`:
1. Replace `espn_status = event["status"]["type"]["name"]` with:

```python
            status_type = event["status"]["type"]
            espn_status = status_type["name"]
```

2. In the dict appended to `games`, after the `"status": ...` line, add:

```python
                # "pre" / "in" / "post". Unlike the name, every in-game pause
                # (halftime, end of a period, a rain delay) is "in" -- STATUS_MAP
                # would call those "scheduled".
                "state": status_type.get("state"),
                # ESPN's own one-line clock: "Q3 4:12", "Bot 7th", "Halftime", "Final".
                "live_detail": status_type.get("shortDetail"),
```

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `.venv/Scripts/python -m pytest backend/tests/test_live_scores.py backend/tests/test_espn.py backend/tests/test_espn_reconciliation.py backend/tests/test_espn_fetch_window.py -q -p no:warnings`

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git checkout feat/sportsbook-phase-4    # created with the plan commit
git add backend/models.py backend/database.py backend/collectors/espn.py backend/tests/test_live_scores.py backend/tests/test_espn.py
git commit -m "feat(live): games.live_detail column and ESPN's live state/clock on the scoreboard

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: The `live_scores` job

**Files:**
- Create: `backend/pipeline/live_scores.py`
- Modify: `backend/pipeline/scheduler.py` (register the job in `configure_scheduler`)
- Test: `backend/tests/test_live_scores.py` (append)

**Interfaces:**
- **Consumes:** `ESPNCollector.fetch_scoreboard` (Task 1), `Game.live_detail`, and `time_utils.ET` / `game_start_utc`.
- **Produces:**
  - **Constants:** `live_scores.LIVE_SPORTS` and `LIVE_LOOKBACK_DAYS`.
  - **Functions:**
    - `live_candidates(session, now) -> list[Game]`
    - `apply_scoreboard(games, events) -> int`
    - `async update_live_scores(session, *, now=None, fetch=None) -> int`
    - `run_live_scores(engine) -> None`
  - **Scheduler job** `live_scores`: interval 2 min, with `coalesce=True`, `max_instances=1` and `misfire_grace_time=60`.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_live_scores.py`:

```python
import asyncio
from datetime import date, datetime, timedelta, timezone

import pytest

from backend.database import get_engine, get_session
from backend.models import Game, Team
from backend.pipeline import live_scores

NOW = datetime(2026, 10, 11, 19, 0, tzinfo=timezone.utc)      # Sun 3:00 PM ET
TODAY = date(2026, 10, 11)


@pytest.fixture
def session():
    s = get_session(create_engine("sqlite:///:memory:"))
    Base.metadata.create_all(s.get_bind())
    s.add_all([Team(id=1, name="DAL", abbreviation="DAL", sport="nfl"),
               Team(id=2, name="TB", abbreviation="TB", sport="nfl"),
               Team(id=3, name="NYY", abbreviation="NYY", sport="mlb"),
               Team(id=4, name="BOS", abbreviation="BOS", sport="mlb"),
               Team(id=5, name="A", abbreviation="A", sport="mma"),
               Team(id=6, name="B", abbreviation="B", sport="mma")])
    s.commit()
    yield s
    s.close()


def _game(s, *, espn_id="401", start=NOW - timedelta(hours=1), status="scheduled", sport="nfl",
          day=TODAY, home=1, away=2):
    g = Game(sport=sport, season="2026", date=day, espn_id=espn_id, status=status,
             start_time=start.replace(tzinfo=None) if start else None, home_team_id=home, away_team_id=away)
    s.add(g)
    s.commit()
    return g


def _event(espn_id="401", state="in", status="in_progress", home=17, away=14, detail="Q3 4:12"):
    return {"espn_id": espn_id, "state": state, "status": status, "home_score": home,
            "away_score": away, "live_detail": detail}


class Fetch:
    """Stands in for ESPNCollector.fetch_scoreboard."""
    def __init__(self, events=None, fail=()):
        self.events, self.fail, self.calls = events or {}, set(fail), []

    async def __call__(self, sport, date_str):
        self.calls.append((sport, date_str))
        if sport in self.fail:
            raise RuntimeError("ESPN down")
        return self.events.get(sport, [])


def _run(session, fetch, now=NOW):
    return asyncio.run(live_scores.update_live_scores(session, now=now, fetch=fetch))


def test_a_started_game_gets_its_running_score_and_clock(session):
    g = _game(session)
    fetch = Fetch({"nfl": [_event()]})
    assert _run(session, fetch) == 1
    assert fetch.calls == [("nfl", "20261011")]
    assert (g.status, g.home_score, g.away_score, g.live_detail) == ("in_progress", 17, 14, "Q3 4:12")


def test_halftime_is_still_live(session):
    """Review Focus 1: ESPN names halftime STATUS_HALFTIME -> "scheduled"; its state is "in"."""
    g = _game(session, status="in_progress")
    _run(session, Fetch({"nfl": [_event(status="scheduled", detail="Halftime")]}))
    assert (g.status, g.home_score, g.live_detail) == ("in_progress", 17, "Halftime")


def test_the_job_never_writes_final(session):
    """Review Focus 3: the results path owns final, with its reconciliation."""
    g = _game(session, status="in_progress")
    _run(session, Fetch({"nfl": [_event(state="post", status="final", home=24, away=21, detail="Final")]}))
    assert (g.status, g.home_score, g.away_score, g.live_detail) == ("in_progress", 24, 21, "Final")


def test_a_game_postponed_after_kickoff_is_left_to_the_results_path(session):
    """Review Focus 2."""
    g = _game(session)
    _run(session, Fetch({"nfl": [_event(state="post", status="postponed", home=None, away=None,
                                        detail="Postponed")]}))
    assert (g.status, g.home_score, g.live_detail) == ("scheduled", None, None)


def test_a_pregame_event_leaves_the_row_alone(session):
    g = _game(session)
    _run(session, Fetch({"nfl": [_event(state="pre", status="scheduled", home=None, away=None)]}))
    assert (g.status, g.home_score) == ("scheduled", None)


def test_a_final_row_is_never_fetched_or_touched(session):
    g = _game(session, status="final")
    g.home_score, g.away_score = 30, 20
    session.commit()
    fetch = Fetch({"nfl": [_event()]})
    assert _run(session, fetch) == 0
    assert fetch.calls == []
    assert (g.status, g.home_score, g.live_detail) == ("final", 30, None)


def test_nothing_live_means_no_request(session):
    _game(session, start=NOW + timedelta(hours=2))              # not started
    _game(session, espn_id="402", start=None)                   # no kickoff known
    _game(session, espn_id=None)                                # can't be matched
    _game(session, espn_id="403", sport="mma", home=5, away=6)  # combat: no live feed
    _game(session, espn_id="404", day=TODAY - timedelta(days=3))  # stuck, not live
    fetch = Fetch()
    assert _run(session, fetch) == 0
    assert fetch.calls == []


def test_one_failing_scoreboard_does_not_stop_the_others(session):
    """Review Focus 4."""
    nfl = _game(session)
    mlb = _game(session, espn_id="501", sport="mlb", home=3, away=4)
    fetch = Fetch({"mlb": [_event(espn_id="501", home=3, away=2, detail="Bot 7th")]}, fail={"nfl"})
    assert _run(session, fetch) == 1
    assert (nfl.status, nfl.home_score) == ("scheduled", None)
    assert (mlb.status, mlb.home_score, mlb.live_detail) == ("in_progress", 3, "Bot 7th")


def test_run_live_scores_never_raises_into_the_scheduler(monkeypatch):
    engine = get_engine(":memory:")
    Base.metadata.create_all(engine)

    async def boom(session, **kw):
        raise RuntimeError("anything")
    monkeypatch.setattr(live_scores, "update_live_scores", boom)
    live_scores.run_live_scores(engine)                          # must not raise


def test_the_job_is_registered_every_two_minutes():
    from backend.pipeline.scheduler import configure_scheduler
    sched = configure_scheduler({"database_path": ":memory:", "digest": {"enabled": False}},
                                get_engine(":memory:"))
    job = sched.get_job("live_scores")
    assert job is not None
    assert job.trigger.interval == timedelta(minutes=2)
    assert (job.coalesce, job.max_instances) == (True, 1)
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `.venv/Scripts/python -m pytest backend/tests/test_live_scores.py -q -p no:warnings`

Expected: FAIL on `ImportError: cannot import name 'live_scores'`.

- [ ] **Step 3: Write `backend/pipeline/live_scores.py`**

```python
"""Live scores for games under way (sportsbook spec 2026-10-07 §8).

Every 2 minutes, for team-sport games past kickoff and not yet final, read
ESPN's scoreboard for their (sport, ET date) and write the running score,
ESPN's one-line clock and status "in_progress". It never writes "final" --
the results path owns that, with its reconciliation and score healing -- and
never touches a final row. When nothing is live it makes no request at all.

Combat sports are skipped: the scoreboard parser reads one bout per MMA card,
no MMA row carries an ESPN id, and boxing has no feed.
"""
from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from backend.collectors.espn import ESPNCollector
from backend.database import get_session
from backend.models import Game
from backend.time_utils import ET, game_start_utc

logger = logging.getLogger(__name__)

LIVE_SPORTS = ("nfl", "ncaaf", "nba", "ncaab", "mlb")
#: Games dated today or yesterday (ET). A row unfinished after that is a
#: results problem, not a live one -- polling it forever would cost requests.
LIVE_LOOKBACK_DAYS = 1


def live_candidates(session, now: datetime) -> list[Game]:
    first_day = now.astimezone(ET).date() - timedelta(days=LIVE_LOOKBACK_DAYS)
    rows = (session.query(Game)
            .filter(Game.sport.in_(LIVE_SPORTS),
                    Game.status.in_(("scheduled", "in_progress")),
                    Game.espn_id.isnot(None), Game.start_time.isnot(None),
                    Game.date >= first_day)
            .all())
    return [g for g in rows if game_start_utc(g) <= now]


def _is_live(event: dict) -> bool:
    """ESPN's state, not its status name: halftime and delays are "in". A
    finished game ("post") counts only if final -- a postponed or canceled
    one is the results path's to handle."""
    state = event.get("state")
    return state == "in" or (state == "post" and event.get("status") == "final")


def apply_scoreboard(games: list[Game], events: list[dict]) -> int:
    by_id = {str(e.get("espn_id")): e for e in events}
    updated = 0
    for game in games:
        event = by_id.get(str(game.espn_id))
        if event is None or game.status == "final" or not _is_live(event):
            continue
        game.status = "in_progress"
        if event.get("home_score") is not None:
            game.home_score = event["home_score"]
        if event.get("away_score") is not None:
            game.away_score = event["away_score"]
        game.live_detail = event.get("live_detail")
        updated += 1
    return updated


async def update_live_scores(session, *, now: datetime | None = None, fetch=None) -> int:
    now = now or datetime.now(timezone.utc)
    games = live_candidates(session, now)
    if not games:
        return 0
    by_board: dict[tuple, list[Game]] = defaultdict(list)
    for game in games:
        by_board[(game.sport, game.date)].append(game)
    collector = ESPNCollector() if fetch is None else None
    fetcher = fetch or collector.fetch_scoreboard
    updated = 0
    try:
        for (sport, day), board_games in by_board.items():
            try:
                events = await fetcher(sport, day.strftime("%Y%m%d"))
            except Exception:
                logger.warning("live_scores: %s scoreboard for %s failed", sport, day, exc_info=True)
                continue
            updated += apply_scoreboard(board_games, events)
        session.commit()
    finally:
        if collector is not None:
            await collector.close()
    if updated:
        logger.info("live_scores: updated %d game(s)", updated)
    return updated


def run_live_scores(engine) -> None:
    """The scheduler's entry point: never raises, so a bad poll can't hurt
    the jobs around it."""
    session = get_session(engine)
    try:
        asyncio.run(update_live_scores(session))
    except Exception:
        session.rollback()
        logger.exception("live_scores failed")
    finally:
        session.close()
```

- [ ] **Step 4: Register the job in `backend/pipeline/scheduler.py`**

In `configure_scheduler`, after the `recalibration` job, add:

```python
    from backend.pipeline.live_scores import run_live_scores
    # Live scores for open bets (sportsbook spec §8). Every 2 minutes, but it
    # makes no request unless a game is under way; never a backlog after a
    # sleep, and never two at once.
    scheduler.add_job(
        lambda: run_live_scores(engine),
        'interval', minutes=2, id='live_scores', replace_existing=True,
        coalesce=True, max_instances=1, misfire_grace_time=60,
    )
```

- [ ] **Step 5: Run the tests and confirm they pass**

Run: `.venv/Scripts/python -m pytest backend/tests/test_live_scores.py backend/tests/test_digest_sender.py backend/tests/test_price_refresh.py -q -p no:warnings`

Expected: all pass.

- [ ] **Step 6: Mutation-check**

Make each change, run `test_live_scores.py`, confirm the named test fails, then restore. Delete `backend/pipeline/__pycache__/live_scores.*.pyc` after every write and every restore.
1. `_is_live`: `return state in ("in", "post")`. The postponed test must fail.
2. `_is_live`: `return event.get("status") == "in_progress"`. The halftime test must fail.
3. `apply_scoreboard`: `game.status = event["status"]`. The never-final test must fail.
4. Remove `Game.date >= first_day`. The nothing-live test must fail.
5. Move `session.commit()` and the loop out of the per-sport `try`, so one failure raises. The failing-scoreboard test must fail.

- [ ] **Step 7: Commit**

```bash
git add backend/pipeline/live_scores.py backend/pipeline/scheduler.py backend/tests/test_live_scores.py
git commit -m "feat(live): live_scores job — ESPN running scores every 2 min while a game is on

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Guards around `in_progress` — results path, betting, settlement, My Bets

**Files:**
- Modify: `backend/pipeline/paper_settlement.py` (the push rule covers `in_progress`), `backend/paper/bets.py` (pass `live_detail` through)
- Test: `backend/tests/test_live_scores.py` (append), `backend/tests/test_paper_void.py` (append), `backend/tests/test_paper_bets.py` (append)

**Interfaces:**
- **Consumes:** `full_pipeline._store_games`, `pricing.open_for_betting`, `paper_settlement.grade_paper_picks`, `bets.tickets`.
- **Produces:**
  - Settlement pushes a bet on a game still `in_progress` `STALE_SCHEDULED_DAYS` after its date.
  - Ticket legs carry `game.live_detail`.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_live_scores.py`:

```python
def test_the_results_path_still_finalises_an_in_progress_game(session):
    """Spec §8 verify item 1 / Review Focus 3: _store_games' guard only skips
    in_progress rows during cancel-reconciliation, not on a final upsert."""
    from backend.pipeline.full_pipeline import _store_games
    g = _game(session, status="in_progress")
    g.home_score, g.away_score, g.live_detail = 17, 14, "Q4 0:12"
    session.commit()
    _store_games(session, "nfl", TODAY, [{
        "espn_id": "401", "date": "2026-10-11T17:00Z", "status": "final",
        "home_team": "DAL", "home_team_name": "DAL", "away_team": "TB", "away_team_name": "TB",
        "home_score": 24, "away_score": 21}], reconcile=False)
    session.refresh(g)
    assert (g.status, g.home_score, g.away_score) == ("final", 24, 21)


def test_an_in_progress_game_is_not_open_for_betting():
    """Spec §8 verify item 2."""
    from backend.paper.pricing import open_for_betting
    assert open_for_betting(Game(status="in_progress", start_time=None)) is False
```

Append to `backend/tests/test_paper_void.py`:

```python
def test_a_game_stuck_in_progress_for_a_week_is_pushed(session):
    """Sportsbook Phase 4: a live row whose final never arrives (a game
    postponed mid-way, an ESPN gap) would otherwise hold its stake forever."""
    bet = _bet(session, status="in_progress", days_ago=7)
    grade_paper_picks(session, today=TODAY)
    assert (bet.result, bet.payout) == ("push", 0.0)


def test_an_in_progress_game_a_few_days_past_waits(session):
    bet = _bet(session, status="in_progress", days_ago=3)
    grade_paper_picks(session, today=TODAY)
    assert bet.result is None
```

Append to `backend/tests/test_paper_bets.py`:

```python
def test_a_live_leg_carries_the_game_clock():
    client = _client()
    [gid] = _games(client)
    uid = _user(client)
    _bet(client, uid, gid)
    s = get_session(client.app.state.engine)
    g = s.get(Game, gid)
    g.status, g.home_score, g.away_score, g.live_detail = "in_progress", 7, 3, "Q2 1:05"
    s.commit()
    s.close()
    [leg] = _bets(client, uid)["tickets"][0]["legs"]
    assert {k: leg["game"][k] for k in ("status", "home_score", "away_score", "live_detail")} == \
        {"status": "in_progress", "home_score": 7, "away_score": 3, "live_detail": "Q2 1:05"}
```

Run: `.venv/Scripts/python -m pytest backend/tests/test_live_scores.py backend/tests/test_paper_void.py backend/tests/test_paper_bets.py -q -p no:warnings`

Expected:
- **Fail:** the week-stuck `in_progress` void, and the live-leg clock (which gets `None`).
- **Pass already:** the results-path test, the betting test and the 3-day wait test. They pin behaviour that already holds, which is the spec's "verify before shipping".

- [ ] **Step 2: Implement**

In `backend/pipeline/paper_settlement.py`, `_push_ungradeable`, change:

```python
            and_(Game.status == "scheduled", Game.date <= stale),
```

to:

```python
            and_(Game.status.in_(("scheduled", "in_progress")), Game.date <= stale),
```

Then extend the docstring paragraph that begins "A still-``scheduled`` game" with the sentence: "So does one stuck ``in_progress`` (the live_scores job never writes final; a game postponed mid-way or an ESPN gap would otherwise sit there)."

In `backend/paper/bets.py`, `_game`, replace `"live_detail": None}                     # Phase 4 fills this in` with `"live_detail": game.live_detail}`.

- [ ] **Step 3: Run the tests and confirm they pass**

Run: `.venv/Scripts/python -m pytest backend/tests/test_live_scores.py backend/tests/test_paper_void.py backend/tests/test_paper_bets.py backend/tests/test_paper_settlement.py -q -p no:warnings`

Expected: all pass.

- [ ] **Step 4: Mutation-check**

Make each change, confirm a test fails, then restore. Delete the `.pyc` after every write and every restore.
1. Revert the settlement change. The week-stuck test must fail.
2. In `full_pipeline._store_games`, change `if existing.status != "final":` to `if existing.status == "scheduled":`. The results-path test must fail. Restore it straight away; this is a guard check only.

- [ ] **Step 5: Commit**

```bash
git add backend/pipeline/paper_settlement.py backend/paper/bets.py backend/tests/test_live_scores.py backend/tests/test_paper_void.py backend/tests/test_paper_bets.py
git commit -m "feat(live): settle bets on a game stuck in_progress; My Bets carries the game clock

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Live helpers — `isLive`, `legTint`, a live `gameLine`, and `gameLock`

**Files:**
- Modify: `frontend/src/lib/bets.ts`, `frontend/src/lib/gameLock.ts`
- Test: `frontend/src/lib/bets.test.ts` (append), `frontend/src/lib/gameLock.test.ts` (new)

**Interfaces:**
- **Consumes:** `legFromPick` (`lib/quotes.ts`), `lineFromPick` and `startInstant` (`lib/slip.ts`).
- **Produces:**
  - `isLive(g: TicketGame, now?: Date): boolean`
  - `legTint(l: TicketLeg): LegTint | null`, with type `LegTint = 'winning' | 'losing' | 'even'`
  - `gameLine` shows the score and ESPN clock for a live game
  - `getGameLockState` locks `in_progress`

- [ ] **Step 1: Write the failing tests**

Append to `frontend/src/lib/bets.test.ts` (add `isLive, legTint` to the `./bets` import):

```ts
describe('live games', () => {
  const live = (over: Partial<TicketGame> = {}) =>
    game({ status: 'in_progress', home_score: 17, away_score: 14, live_detail: 'Q3 4:12', ...over })

  it('shows the running score and ESPN clock', () => {
    expect(gameLine(live())).toBe('TB 14 – DAL 17 · Q3 4:12')
    expect(gameLine(live({ home_score: null, away_score: null, live_detail: null }))).toBe('TB @ DAL · Live')
  })
  it('calls a scheduled game past its kickoff Live (combat has no live feed)', () => {
    const now = new Date('2026-10-09T01:00:00Z')
    expect(isLive(game(), now)).toBe(true)                                  // kicked off 00:15Z
    expect(gameLine(game(), now)).toBe('TB @ DAL · Live')
    expect(isLive(game({ start_time: '2026-10-09T00:15:00' }), now)).toBe(true)   // naive UTC
    expect(isLive(game(), new Date('2026-10-08T22:00:00Z'))).toBe(false)
    expect(isLive(game({ status: 'final' }), now)).toBe(false)
  })
  it('tints a moneyline by who leads', () => {
    expect(legTint(leg({ pick_type: 'moneyline', pick_value: 'HOME ML', game: live() }))).toBe('winning')
    expect(legTint(leg({ pick_type: 'moneyline', pick_value: 'AWAY ML', game: live() }))).toBe('losing')
    expect(legTint(leg({ pick_type: 'moneyline', pick_value: 'AWAY ML', game: live({ home_score: 14 }) }))).toBe('even')
  })
  it('tints a spread with its line, Even exactly on it (Review Focus 5)', () => {
    expect(legTint(leg({ game: live({ home_score: 21, away_score: 14 }) }))).toBe('winning')   // AWAY +9, down 7
    expect(legTint(leg({ game: live({ home_score: 23, away_score: 14 }) }))).toBe('even')      // down 9
    expect(legTint(leg({ game: live({ home_score: 27, away_score: 14 }) }))).toBe('losing')
    expect(legTint(leg({ pick_value: 'HOME -3.5', game: live() }))).toBe('losing')             // up 3
  })
  it('tints a total against its line', () => {
    expect(legTint(leg({ pick_type: 'over_under', pick_value: 'Over 49', game: live() }))).toBe('losing')   // 31
    expect(legTint(leg({ pick_type: 'over_under', pick_value: 'Under 49', game: live() }))).toBe('winning')
    expect(legTint(leg({ pick_type: 'over_under', pick_value: 'Over 49', game: live({ home_score: 28, away_score: 21 }) })))
      .toBe('even')
  })
  it('never tints a prop, a settled leg, or a game that is not live', () => {
    expect(legTint(leg({ pick_type: 'prop', pick_value: 'Dak Prescott Over 255.5 Pass Yards', game: live() }))).toBeNull()
    expect(legTint(leg({ result: 'win', game: live() }))).toBeNull()
    expect(legTint(leg())).toBeNull()
  })
})
```

Create `frontend/src/lib/gameLock.test.ts`:

```ts
import { describe, it, expect, vi, afterEach } from 'vitest'
import { getGameLockState } from './gameLock'

afterEach(() => vi.useRealTimers())

describe('getGameLockState', () => {
  it('locks a game the live job has marked in progress, whatever its start time says', () => {
    expect(getGameLockState('in_progress', '2099-01-01T00:00:00Z')).toBe('locked')
  })
  it('reads a naive start time as UTC, as /games/today sends it', () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-10-11T17:30:00Z'))
    expect(getGameLockState('scheduled', '2026-10-11T17:00:00')).toBe('locked')
    expect(getGameLockState('scheduled', '2026-10-11T18:00:00')).toBe('open')
    expect(getGameLockState('final', '2026-10-11T17:00:00')).toBe('final')
  })
})
```

Run: `cd frontend && npx vitest run src/lib/bets.test.ts src/lib/gameLock.test.ts`

Expected:
- The live tests fail, because `isLive` and `legTint` don't exist yet.
- The `in_progress` lock test fails, because the start time is in the future.
- The naive-UTC test fails on this machine (America/Phoenix), where `new Date('…T18:00:00')` is local time.

- [ ] **Step 2: Implement in `frontend/src/lib/bets.ts`, using the Edit tool**

1. Add these imports: `import { legFromPick } from './quotes'` (alongside the existing `resolveLabel` import from `./quotes`) and `import { lineFromPick, startInstant } from './slip'`.
2. Add this above `gameLine`:

```ts
export type LegTint = 'winning' | 'losing' | 'even'

/** Under way: marked in_progress by the live job, or past its kickoff without
 *  a result (combat sports get no live feed). */
export function isLive(g: TicketGame, now: Date = new Date()): boolean {
  if (g.status === 'in_progress') return true
  return g.status === 'scheduled' && g.start_time !== null && startInstant(g.start_time) <= now.getTime()
}

/** Winning / Losing / Even for a live game leg from the running score and the
 *  leg's line; null for props, settled legs and games without a live score. */
export function legTint(l: TicketLeg): LegTint | null {
  const g = l.game
  if (l.result !== null || g.status !== 'in_progress' || g.home_score === null || g.away_score === null) return null
  const leg = legFromPick(l.pick_type, l.pick_value, g.id)
  if (!leg || leg.pick_type === 'prop') return null
  let edge: number
  if (leg.pick_type === 'over_under') {
    const line = lineFromPick('over_under', l.pick_value)
    if (line === null) return null
    const total = g.home_score + g.away_score
    edge = leg.side === 'Over' ? total - line : line - total
  } else {
    const margin = leg.side === 'HOME' ? g.home_score - g.away_score : g.away_score - g.home_score
    edge = leg.pick_type === 'spread' ? margin + (lineFromPick('spread', l.pick_value) ?? 0) : margin
  }
  return edge > 0 ? 'winning' : edge < 0 ? 'losing' : 'even'
}
```

3. In `gameLine`, insert these lines right after the `final` branch:

```ts
  if (g.status === 'in_progress') {
    return g.home_score !== null && g.away_score !== null
      ? `${g.away_team} ${g.away_score} – ${g.home_team} ${g.home_score} · ${g.live_detail ?? 'Live'}`
      : `${g.away_team} @ ${g.home_team} · Live`
  }
  if (isLive(g, now)) return `${g.away_team} @ ${g.home_team} · Live`
```

Replace `frontend/src/lib/gameLock.ts`'s header comment and `getGameLockState` with:

```ts
import { startInstant } from './slip'

export type GameLockState = 'open' | 'locked' | 'final';

// 'in_progress' comes from the live_scores job; a 'scheduled' game whose
// start time has passed is locked too (started, not yet updated). Start times
// from /games/today are naive UTC, so they go through startInstant.
export function getGameLockState(status: string, startTime: string | null): GameLockState {
  if (status === 'final') return 'final';
  if (status === 'in_progress') return 'locked';
  if (startTime && startInstant(startTime) <= Date.now()) return 'locked';
  return 'open';
}
```

Keep `lockStateLabel` and `lockStateTooltip` as they are.

- [ ] **Step 3: Run the tests and confirm they pass**

Run: `cd frontend && npx vitest run src/lib/bets.test.ts src/lib/gameLock.test.ts src/components src/pages && npx tsc -p tsconfig.app.json --noEmit && npx eslint .`

Expected: all pass. GameCard and PicksTable tests still pass with the new lock rule.

- [ ] **Step 4: Mutation-check**

Make each change with Edit, confirm a test fails, then restore:
1. In `legTint`, drop the spread line (`margin` only). The spread test must fail.
2. Change `edge > 0 ? 'winning' : edge < 0 ? 'losing' : 'even'` to `edge >= 0 ? 'winning' : 'losing'`. The Even test must fail.
3. In `gameLock`, remove the `in_progress` line. The lock test must fail.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/bets.ts frontend/src/lib/bets.test.ts frontend/src/lib/gameLock.ts frontend/src/lib/gameLock.test.ts
git commit -m "feat(live): live game line, Winning/Losing/Even leg tint; lock in_progress games

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Live tickets in My Bets

**Files:**
- Modify: `frontend/src/components/TicketCard.tsx`, `frontend/src/sportsbook.css` (append)
- Test: `frontend/src/pages/MyBets.test.tsx` (append)

**Interfaces:**
- **Consumes:** `isLive` and `legTint` (Task 4).
- **Produces:**
  - An open ticket with any live leg shows a `LIVE` pill.
  - Each game leg in a live game shows a Winning / Losing / Even tag; prop legs don't.

- [ ] **Step 1: Write the failing test**

Append inside `describe('MyBets', ...)` in `frontend/src/pages/MyBets.test.tsx`:

```tsx
  it('marks a live ticket and tints its legs', async () => {
    const liveGame = { id: 9, sport: 'nfl', home_team: 'DAL', away_team: 'TB', start_time: '2026-10-09T00:15:00+00:00',
      status: 'in_progress', home_score: 21, away_score: 14, live_detail: 'Q3 4:12' }
    vi.mocked(api.users.bets).mockResolvedValue({
      summary: { available: 9900, balance: 10000, open_stakes: 100, today_pl: 0 },
      tickets: [t({ id: 8, legs: [leg({ game: liveGame }),
        leg({ pick_type: 'prop', pick_value: 'Dak Prescott Over 255.5 Pass Yards', game: liveGame })] })] })
    renderPage()
    const card = await screen.findByRole('article', { name: 'Bet #P-8' })
    expect(card).toHaveTextContent('LIVE')
    expect(card).toHaveTextContent('TB 14 – DAL 21 · Q3 4:12')
    expect(card).toHaveTextContent('Winning')                     // TB +9, down 7
    expect(card.querySelectorAll('.sb-tint')).toHaveLength(1)     // the prop gets none
  })
```

Run: `cd frontend && npx vitest run src/pages/MyBets.test.tsx`

Expected: FAIL. There is no LIVE pill or tint yet; the score line already passes from Task 4.

- [ ] **Step 2: Implement in `frontend/src/components/TicketCard.tsx`**

1. Add `isLive, legTint` to the `../lib/bets` import.
2. Add `const TINT: Record<string, string> = { winning: 'Winning', losing: 'Losing', even: 'Even' }` beside `RESULT`.
3. Inside the component, add `const live = t.result === null && t.legs.some(l => isLive(l.game))`.
4. In the header, after `<span>{ticketCode(t)}</span>`, add `{live && <span className="sb-live">LIVE</span>}`.
5. In each leg row, replace `<span className="sb-bet-odds">{formatOdds(l.odds)}</span>` with:

```tsx
            {(() => {
              const tint = legTint(l)
              return tint && <span className={`sb-tint sb-tint-${tint}`}>{TINT[tint]}</span>
            })()}
            <span className="sb-bet-odds">{formatOdds(l.odds)}</span>
```

Append this to `frontend/src/sportsbook.css`:

```css
/* ─── Live tickets (spec §8) ────────────────────────────────── */
.sb-live {
  padding: 0.05rem 0.4rem; border-radius: var(--radius-sm); background: var(--yellow-dim);
  color: var(--yellow); font-size: 0.65rem; font-weight: 800; letter-spacing: 0.06em;
  animation: sb-pulse 1.6s ease-in-out infinite;
}
@keyframes sb-pulse { 50% { opacity: 0.55; } }
.sb-tint { flex: none; padding: 0.05rem 0.4rem; border-radius: var(--radius-sm); font-size: 0.7rem; font-weight: 800; }
.sb-tint-winning { background: var(--green-dim); color: var(--green); }
.sb-tint-losing { background: var(--red-dim); color: var(--red); }
.sb-tint-even { background: var(--yellow-dim); color: var(--yellow); }
@media (prefers-reduced-motion: reduce) { .sb-live { animation: none; } }
```

- [ ] **Step 3: Run the tests and confirm they pass**

Run: `cd frontend && npm test && npx tsc -p tsconfig.app.json --noEmit && npx eslint .`

Expected: all pass.

- [ ] **Step 4: Mutation-check**

Make each change, confirm a test fails, then restore:
1. Set `const live = false`. The live test must fail.
2. Render the tint for every leg, using `legTint(l) ?? 'even'`. The prop-count assertion must fail.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/TicketCard.tsx frontend/src/pages/MyBets.test.tsx frontend/src/sportsbook.css
git commit -m "feat(live): LIVE pill and Winning/Losing/Even tags on My Bets tickets

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Verify in a worktree, then merge with the migration procedure

**Files:** none are created.

- [ ] **Step 1: Run the full suites**

```bash
cd /c/Users/mwill/Documents/mwilliams2733/sports_picks
.venv/Scripts/python -m pytest -q -p no:warnings
cd frontend && npm test && npx tsc -p tsconfig.app.json --noEmit && npx eslint .
```

Expected:
- The backend gives 2326, the Phase 3 merge baseline, plus 17 new tests, with 0 failures.
- The frontend passes, clean.

- [ ] **Step 2: Run the job for real against a snapshot, read-only toward ESPN**

1. Take a snapshot with the sqlite backup API and run `run_migrations` on the snapshot.
2. Run `asyncio.run(live_scores.update_live_scores(session))` against it.
   - If a team-sport game is on right now, expect `updated >= 1` and the row's score and clock set.
   - If none is on, expect `0` and no request.
3. Then, on the **snapshot only**, set one started game's `start_time` to an hour ago and run the job again. That game is a real `espn_id` from today or yesterday whose ESPN state is `"in"` or `"post"`. Expect it updated from ESPN's live scoreboard. This is a real call to ESPN, which is free and unauthenticated; it is not the Odds API.

- [ ] **Step 3: Check it in Chrome in a worktree**

Same setup as Phase 3:
1. `git worktree add --detach "$WT" HEAD`, then `npm ci --legacy-peer-deps` and `npm run build` there.
2. Serve the snapshot on :8001 from the worktree with the main `.venv` and `ENABLE_SCHEDULER=0`.

**On the snapshot,** mark the game on one of Claude's open bets `in_progress` with a score and `live_detail`. If Claude has none open, place a test bet first, as in Phase 2's check. Then check at desktop and phone width:
1. My Bets shows the LIVE pill, "TB 14 – DAL 21 · Q3 4:12" and the Winning tag.
2. Model Picks shows the game's Bet This button as Locked.

Save screenshots. Then remove the worktree with the long-path fallback if needed, and confirm `git worktree list` shows only the main tree.

- [ ] **Step 4: Report to the owner and wait for "merge"**

Include:
- the live run's result from Step 2;
- the screenshots;
- the combat-skip deviation;
- that merging runs a **migration on the live db**, with the procedure below.

- [ ] **Step 5: Merge with the migration procedure, on the owner's OK**

1. **Pick the time.** Read `scheduler.log` for today's windows and choose a moment at least 30 minutes from any window, the 8:00 scout, 03:00 recalibration and the :30 price refreshes. Prefer a moment with no game in progress.
2. **Merge:**

```bash
git checkout master
git merge --no-ff feat/sportsbook-phase-4 -m "Merge: sportsbook phase 4 — live scores

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
find backend -path "*__pycache__*" -name "*.pyc" -newer backend/pipeline/live_scores.py -delete
```

3. **Stop the scheduler, then uvicorn on :8000.** Copy `scheduler.log` and `scheduler.log.prev` aside first. Find each process by its command line, and stop only those.
4. **Back up the live db** with the sqlite backup API to `sports_picks.backup-<YYYYMMDD-HHMMSS>-pre-live-detail.db`. Check that the backup opens and has the same `games` row count.
5. **Apply the migration explicitly:**

```bash
.venv/Scripts/python -c "from backend.database import get_engine, run_migrations; run_migrations(get_engine('sports_picks.db'))"
.venv/Scripts/python -c "import sqlite3; print([r[1] for r in sqlite3.connect('file:sports_picks.db?mode=ro', uri=True).execute('pragma table_info(games)')][-3:])"
```

Expected: `live_detail` is listed.

6. **Build and restart.**
   1. Run `npm --prefix frontend run build`.
   2. Start uvicorn on :8000 as `share.ps1` does: `ENABLE_SCHEDULER=0`, `DATABASE_PATH`, detached, stderr to `app.log` (move the old one to `app.log.prev` first).
   3. Start the scheduler with `Start-Process powershell -ArgumentList '-NoProfile','-File','scripts\start_scheduler.ps1' -WindowStyle Hidden`.
7. **Confirm:**
   - `scheduler.log` shows the `live_scores` job added and "Scheduler started".
   - Two minutes later there's no traceback. A "live_scores: updated N" line appears only if a game is on.
   - `/users/3/bets` returns 200.
   - The tunnel serves the new `index-*.js`.
8. **Update memory** in `sports-picks-sportsbook-ui.md` with "Phase 4 merged <sha>", the backup file name, and the live_scores job and its combat skip. Add a line to `sports-picks-architecture.md`: `in_progress` is now a real status, written only by `live_scores`.

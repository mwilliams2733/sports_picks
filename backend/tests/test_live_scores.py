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

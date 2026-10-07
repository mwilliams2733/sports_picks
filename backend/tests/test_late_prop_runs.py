"""NBA windows get a second prop run 45 minutes before tip-off.

The window's own run is two hours out; NBA teams confirm who sits about 30
minutes before tip, which is exactly the news the star-out adjustment needs.
"""
import datetime

import pytest
from apscheduler.schedulers.background import BackgroundScheduler

import backend.pipeline.scheduler as sch
from backend.database import get_engine, get_session
from backend.models import Base, Game, Team
from backend.time_utils import ET, et_today

TODAY = et_today()
CONFIG = {"seasons": {"nba": {"start": "01-01", "end": "12-31"},
                      "ncaaf": {"start": "01-01", "end": "12-31"}}}


def _at(hour, minute=0):
    return datetime.datetime.combine(TODAY, datetime.time(hour, minute), tzinfo=ET)


@pytest.fixture()
def engine(tmp_path):
    e = get_engine(str(tmp_path / "s.db"))
    Base.metadata.create_all(e)
    s = get_session(e)
    for sport, base in (("nba", 1), ("ncaaf", 3)):
        s.add_all([Team(id=base, name=f"{sport}A", abbreviation=f"{sport}A", sport=sport),
                   Team(id=base + 1, name=f"{sport}B", abbreviation=f"{sport}B", sport=sport)])

    def game(gid, sport, home, hour, minute=0):
        start = _at(hour, minute).astimezone(datetime.timezone.utc).replace(tzinfo=None)
        return Game(id=gid, sport=sport, season="2026-27", date=TODAY, status="scheduled",
                    home_team_id=home, away_team_id=home + 1, start_time=start)

    # NBA tips at 19:00 and 22:00 ET; one ncaaf game at 19:00.
    s.add_all([game(10, "nba", 1, 19), game(11, "nba", 1, 22), game(20, "ncaaf", 3, 19)])
    s.commit()
    s.close()
    return e


def _jobs(scheduler, prefix):
    return {j.id: j.trigger.run_date for j in scheduler.get_jobs() if j.id.startswith(prefix)}


def test_each_nba_window_gets_a_late_run_45_minutes_before_tip(engine, monkeypatch):
    monkeypatch.setattr(sch, "_run_late_props", lambda *a: pytest.fail("ran"))
    scheduler = BackgroundScheduler(timezone=ET)
    sch.restore_today_windows(CONFIG, engine, scheduler, now=_at(9))
    late = _jobs(scheduler, "late_")
    assert late == {f"late_nba_{TODAY}_0": _at(18, 15), f"late_nba_{TODAY}_1": _at(21, 15)}


def test_no_late_run_for_other_sports(engine, monkeypatch):
    scheduler = BackgroundScheduler(timezone=ET)
    sch.restore_today_windows(CONFIG, engine, scheduler, now=_at(9))
    assert not [j for j in _jobs(scheduler, "late_") if "ncaaf" in j]


def test_an_overdue_late_run_is_never_scheduled(engine, monkeypatch):
    """At 18:30 the 19:00 game's late run (18:15) is past; the 22:00 one is not."""
    monkeypatch.setattr(sch, "_run_late_props", lambda *a: pytest.fail("ran an overdue run"))
    scheduler = BackgroundScheduler(timezone=ET)
    sch.restore_today_windows(CONFIG, engine, scheduler, now=_at(18, 30))
    assert list(_jobs(scheduler, "late_")) == [f"late_nba_{TODAY}_1"]


def test_the_late_run_refetches_the_window_and_reruns_nba_props(engine, monkeypatch):
    calls = {}

    async def fake_fetch(session, sports, api_key, budget=None, window_game_ids=None):
        calls["fetch"] = (sports, window_game_ids)

    async def fake_pipeline(session, target_date=None, strategy_id=None, sports=None):
        calls["pipeline"] = sports
        return {}

    monkeypatch.setattr(sch, "fetch_and_store_props", fake_fetch)
    monkeypatch.setattr(sch, "run_prop_pipeline", fake_pipeline)
    window = {"games": [{"id": 10}, {"id": 12}]}
    sch._run_late_props({"odds_api_key": "k"}, engine, "nba", window)
    assert calls == {"fetch": (["nba"], {10, 12}), "pipeline": ("nba",)}


def test_a_late_run_orphaned_by_a_recluster_is_removed(engine, monkeypatch):
    """The 9/10am retries can cluster fewer windows; a late job numbered past
    the new count would otherwise fire for nothing."""
    scheduler = BackgroundScheduler(timezone=ET)
    scheduler.add_job(lambda: None, "date", run_date=_at(23), id=f"late_nba_{TODAY}_5")
    sch.restore_today_windows(CONFIG, engine, scheduler, now=_at(9))
    assert f"late_nba_{TODAY}_5" not in _jobs(scheduler, "late_")

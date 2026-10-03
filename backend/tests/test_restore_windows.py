"""A restart must not drop the day's game windows.

The morning scout adds each window as a date-job on the running process's
in-memory scheduler, and `configure_scheduler` registers only the cron jobs.
On 2026-10-03 a restart at 11:54 ET silently lost 13 windows: about 43 ncaaf
games and 3 mlb games had no pre-game odds or picks. Nothing logged it.

`restore_today_windows` re-creates them at startup. Two rules matter:

* Only windows whose run time is still ahead are restored. A fresh process
  cannot know whether an overdue one already ran, and re-running a finished
  window spends credits for nothing.
* Job ids come from the scout's own clustering, so the 9/10am retries see a
  restored job as already scheduled and do not double-book it.
"""
import datetime

import pytest
from apscheduler.schedulers.background import BackgroundScheduler

import backend.pipeline.scheduler as sch
from backend.database import get_engine, get_session
from backend.models import Base, Game, Team
from backend.time_utils import ET, et_today

TODAY = et_today()
CONFIG = {"seasons": {"ncaaf": {"start": "01-01", "end": "12-31"}}}


def _now_at(hour, minute=0):
    """A moment today, ET, as an aware datetime."""
    return datetime.datetime.combine(TODAY, datetime.time(hour, minute), tzinfo=ET)


@pytest.fixture()
def engine(tmp_path):
    e = get_engine(str(tmp_path / "s.db"))
    Base.metadata.create_all(e)
    s = get_session(e)
    s.add_all([Team(id=1, name="A", abbreviation="A", sport="ncaaf"),
               Team(id=2, name="B", abbreviation="B", sport="ncaaf")])

    def game(gid, hour, minute=0):
        start = _now_at(hour, minute).astimezone(datetime.timezone.utc).replace(tzinfo=None)
        return Game(id=gid, sport="ncaaf", season="2026", date=TODAY,
                    status="scheduled", home_team_id=1, away_team_id=2,
                    start_time=start)

    # Three clusters: 12:00, 15:30, 19:00 ET. Runs at 10:00, 13:30, 17:00.
    s.add_all([game(10, 12), game(11, 15, 30), game(12, 19)])
    s.commit()
    s.close()
    return e


def _window_jobs(scheduler):
    return sorted(j.id for j in scheduler.get_jobs() if j.id.startswith("window_"))


def test_windows_still_ahead_are_restored_with_the_scouts_ids(engine, monkeypatch):
    monkeypatch.setattr(sch, "_run_window", lambda *a: pytest.fail("ran a window"))
    scheduler = BackgroundScheduler(timezone=ET)

    sch.restore_today_windows(CONFIG, engine, scheduler, now=_now_at(14))

    # The 10:00 and 13:30 runs are past. Index 2, not 0: the id counts the
    # skipped clusters, exactly as the scout numbered them this morning.
    assert _window_jobs(scheduler) == [f"window_ncaaf_{TODAY}_2"]


def test_an_overdue_window_is_neither_run_nor_scheduled(engine, monkeypatch):
    """At 14:00 the 15:30 games have not started, but their 13:30 run is
    past. The old process most likely ran it, and a fresh one cannot tell."""
    ran = []
    monkeypatch.setattr(sch, "_run_window", lambda c, e, s, w: ran.append(w))
    scheduler = BackgroundScheduler(timezone=ET)

    sch.restore_today_windows(CONFIG, engine, scheduler, now=_now_at(14))

    assert ran == []
    assert f"window_ncaaf_{TODAY}_1" not in _window_jobs(scheduler)

    # And nothing is handed back to run now, so no caller can run it either.
    session = get_session(engine)
    try:
        due = sch._schedule_windows(
            CONFIG, engine, BackgroundScheduler(timezone=ET), session, ["ncaaf"],
            TODAY, now=_now_at(14).astimezone(datetime.timezone.utc),
            run_overdue=False)
    finally:
        session.close()
    assert due == []


def test_before_the_morning_scout_nothing_is_restored(engine):
    """Before 8am the scout has not fetched today's games yet. Windows built
    from yesterday's view of the slate would be numbered differently from
    the scout's, and the scout keeps any id that already exists."""
    scheduler = BackgroundScheduler(timezone=ET)

    sch.restore_today_windows(CONFIG, engine, scheduler, now=_now_at(7, 59))

    assert _window_jobs(scheduler) == []


def test_the_scout_does_not_double_book_a_restored_window(engine, monkeypatch):
    """A retry after a restart sees the restored ids and leaves them alone."""
    scheduler = BackgroundScheduler(timezone=ET)
    sch.restore_today_windows(CONFIG, engine, scheduler, now=_now_at(9))
    before = _window_jobs(scheduler)
    added = []
    real_add = scheduler.add_job
    monkeypatch.setattr(scheduler, "add_job",
                        lambda *a, **k: (added.append(k.get("id")), real_add(*a, **k))[1])
    session = get_session(engine)
    try:
        sch._schedule_windows(CONFIG, engine, scheduler, session, ["ncaaf"],
                              TODAY, now=_now_at(9).astimezone(datetime.timezone.utc))
    finally:
        session.close()

    assert before == [f"window_ncaaf_{TODAY}_{i}" for i in range(3)]
    assert added == []


def test_a_failure_does_not_stop_startup(monkeypatch):
    """Restoring is a convenience. A broken db must not keep the cron jobs
    from starting."""
    scheduler = BackgroundScheduler(timezone=ET)

    sch.restore_today_windows(CONFIG, None, scheduler, now=_now_at(14))

    assert _window_jobs(scheduler) == []


def test_start_scheduler_restores_before_it_returns(monkeypatch):
    calls = []
    monkeypatch.setattr(sch, "restore_today_windows",
                        lambda c, e, s, **k: calls.append(s))
    scheduler = sch.start_scheduler({"seasons": {}}, engine=None)
    try:
        assert scheduler.running
        assert calls == [scheduler]
    finally:
        scheduler.shutdown(wait=False)

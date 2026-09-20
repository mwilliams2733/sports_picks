"""The mma finalizer must run inside the morning scout, and must be bounded.

Two separate failures are guarded here.

**It never ran.** `finalize_mma` was written on 2026-09-20 and wired to
nothing: `configure_scheduler` registered five jobs and none of them
finalized a combat game. The 62 bouts it closed were a one-time hand
catch-up, so mma rows would have piled up `scheduled` again after the very
next event.

**Unbounded, it would cost a request per stuck date, forever.** 64 mma
bouts are permanently unmatchable -- the odds feed carries promotions
ESPN's UFC scoreboard does not cover -- and they span 18 distinct dates
from 2026-03-16 to 2026-08-02. Asking ESPN about all of them every morning
is 18 guaranteed-empty requests a day, and the set only grows. The scout
passes its own `LOOKBACK_DAYS`, so a date is asked about on a few
consecutive mornings and then left alone.
"""
import datetime

import pytest

from backend.models import Base, Game, Team
from backend.database import get_session
from sqlalchemy import create_engine

from backend.scripts.finalize_mma import finalize_stuck_bouts, stuck_bouts

TODAY = datetime.date(2026, 9, 20)


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return get_session(engine)


def _bout(session, day, home="Alpha Fighter", away="Beta Fighter"):
    def team(name):
        t = session.query(Team).filter(Team.sport == "mma",
                                       Team.abbreviation == name).first()
        if t:
            return t
        t = Team(name=name, abbreviation=name, sport="mma")
        session.add(t)
        session.flush()
        return t

    g = Game(sport="mma", date=day, status="scheduled", season="2026",
             home_team_id=team(home).id, away_team_id=team(away).id)
    session.add(g)
    session.flush()
    return g


def test_stuck_bouts_respects_a_date_floor(session):
    """`since` drops dates older than the floor, keeps the rest."""
    _bout(session, datetime.date(2026, 3, 16), "Old A", "Old B")
    _bout(session, datetime.date(2026, 9, 18), "New A", "New B")
    session.commit()

    bounded = stuck_bouts(session, TODAY, since=datetime.date(2026, 9, 17))

    assert list(bounded) == [datetime.date(2026, 9, 18)]


def test_stuck_bouts_unbounded_by_default(session):
    """The manual catch-up still sees all history when no floor is given."""
    _bout(session, datetime.date(2026, 3, 16), "Old A", "Old B")
    _bout(session, datetime.date(2026, 9, 18), "New A", "New B")
    session.commit()

    assert len(stuck_bouts(session, TODAY)) == 2


def test_lookback_days_bounds_the_dates_requested(session, monkeypatch):
    """A lookback turns 'every stuck date ever' into a fixed daily cost."""
    _bout(session, datetime.date(2026, 3, 16), "Old A", "Old B")
    _bout(session, datetime.date(2026, 9, 18), "New A", "New B")
    session.commit()

    asked: list[datetime.date] = []

    async def fake_fetch(client, day):
        asked.append(day)
        return []

    monkeypatch.setattr("backend.scripts.finalize_mma.fetch_ufc_events",
                        fake_fetch)

    finalize_stuck_bouts(session, today=TODAY, lookback_days=3)

    assert asked == [datetime.date(2026, 9, 18)], (
        "the 2026-03-16 bout is unmatchable forever; re-asking about it "
        "every morning is a permanent cost for a guaranteed-empty answer")


# --- the scout must actually call it -------------------------------------

def _scout_with_stubs(monkeypatch, *, finalizer):
    """Run morning_scout with every collector stubbed, recording call order.

    Returns the ordered list of stage names. The scout's own ESPN fetch is
    stubbed to raise, which makes it return early right after grading --
    everything this test cares about happens before that point, and it keeps
    the test off the network.
    """
    from backend.pipeline import scheduler as sched
    order: list[str] = []

    monkeypatch.setattr(sched, "collect_box_scores_for_final_games",
                        lambda s: order.append("box_scores"))
    monkeypatch.setattr(sched, "grade_pending_picks",
                        lambda s: order.append("grade_picks") or {})
    monkeypatch.setattr(sched, "grade_completed_games",
                        lambda s: order.append("grade_games"))

    def wrapped(session, *a, **k):
        order.append("finalize_mma")
        return finalizer(session, *a, **k)

    monkeypatch.setattr(sched, "finalize_stuck_bouts", wrapped)

    def scores_stub(session, sport, api_key, *a, **k):
        order.append(f"scores:{sport}")
        return {"sport": sport, "considered": 0, "finalized": 0,
                "unmatched": 0, "skipped_no_key": False}

    monkeypatch.setattr(sched, "finalize_from_scores", scores_stub)
    monkeypatch.setattr(sched, "is_sport_in_season", lambda *a, **k: False)

    class _Sched:
        def get_jobs(self): return []
        def remove_job(self, _): pass

    sched.morning_scout({"seasons": {}, "database_path": ":memory:"},
                        _fake_engine(), _Sched())
    return order


def _fake_engine():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine


def test_morning_scout_finalizes_mma(monkeypatch):
    """The scheduled path calls the finalizer at all. It never used to."""
    order = _scout_with_stubs(
        monkeypatch, finalizer=lambda s, *a, **k: {"finalized": 0})

    assert "finalize_mma" in order, (
        "finalize_mma ran only by hand; an mma bout was never finalized "
        "by any scheduled job")


def test_mma_is_finalized_before_grading(monkeypatch):
    """Order matters: a bout finalized after grading waits a day to grade."""
    order = _scout_with_stubs(
        monkeypatch, finalizer=lambda s, *a, **k: {"finalized": 0})

    assert order.index("finalize_mma") < order.index("grade_games"), (
        "grade_completed_games applies combat Elo and grades the picks for a "
        "final bout; finalizing after it means a full day of lag")


def test_a_finalizer_failure_does_not_stop_grading(monkeypatch):
    """ESPN is not a dependency of grading picks that are already final."""
    def boom(session, *a, **k):
        raise RuntimeError("ESPN down")

    order = _scout_with_stubs(monkeypatch, finalizer=boom)

    assert "grade_games" in order and "grade_picks" in order, (
        "an ESPN outage must not cost the grading of games already final")


# --- the odds-feed finalizer must run too ---------------------------------

def test_scout_finalizes_both_combat_sports_from_the_odds_feed(monkeypatch):
    """boxing has no other source at all: ESPN answers "Invalid sport
    (boxing)". mma needs it too -- ESPN's UFC scoreboard misses 51% of our
    bouts, which are regional promotions the odds feed does carry."""
    order = _scout_with_stubs(
        monkeypatch, finalizer=lambda s, *a, **k: {"finalized": 0})

    assert "scores:boxing" in order, "boxing would never be finalized at all"
    assert "scores:mma" in order


def test_odds_finalizers_run_before_grading(monkeypatch):
    order = _scout_with_stubs(
        monkeypatch, finalizer=lambda s, *a, **k: {"finalized": 0})

    assert order.index("scores:boxing") < order.index("grade_games")
    assert order.index("scores:mma") < order.index("grade_games")


# --- a silent no-op is indistinguishable from never running ---------------

def test_the_finalizers_log_even_when_they_do_nothing(monkeypatch, caplog):
    """Combat cards are weekly and the window is three days, so "nothing to
    do" is the NORMAL outcome -- it is what the 8 AM scout will show almost
    every morning. Logging only the interesting case makes that ordinary
    path byte-identical in the log to the code having been deleted, which
    is exactly how this repository's dead wiring hid for months. The forced
    scout on 2026-09-20 emitted no combat line at all."""
    import logging
    caplog.set_level(logging.INFO, logger="backend.pipeline.scheduler")

    _scout_with_stubs(monkeypatch,
                      finalizer=lambda s, *a, **k: {"finalized": 0, "dates": 0})

    messages = [r.getMessage().lower() for r in caplog.records]
    # Each finalizer separately: one of them staying silent is exactly the
    # failure being guarded, and asserting on "any combat line" lets the
    # other one cover for it.
    assert any("mma espn finalize" in m for m in messages),         "the ESPN mma finalizer logged nothing on a zero run"
    assert any("mma odds finalize" in m for m in messages),         "the odds mma finalizer logged nothing on a zero run"
    assert any("boxing odds finalize" in m for m in messages),         "the odds boxing finalizer logged nothing on a zero run"

"""Parlays settle once every leg is graded, from whichever grading pass runs:
the owner's POST /users/grade or the scheduler's grade_pending_picks."""
from datetime import date

import pytest
from sqlalchemy import create_engine

from backend.database import get_session
from backend.models import Base, Game, PaperPick, Parlay, Team, UserProfile
from backend.pipeline.paper_settlement import settle_parlays


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    s = get_session(engine)
    s.add(UserProfile(id=1, name="p"))
    s.add_all([Team(id=1, name="H", abbreviation="H", sport="nba"),
               Team(id=2, name="A", abbreviation="A", sport="nba")])
    s.flush()
    yield s
    s.close()


def _parlay(s, leg_results, stake=100.0, odds=-110):
    p = Parlay(user_id=1, stake=stake, combined_odds=264)
    s.add(p)
    s.flush()
    for i, r in enumerate(leg_results):
        g = Game(sport="nba", season="2026", date=date(2026, 9, 28), home_team_id=1,
                 away_team_id=2, status="final" if r else "scheduled")
        s.add(g)
        s.flush()
        s.add(PaperPick(user_id=1, game_id=g.id, pick_type="moneyline",
                        pick_value="HOME ML", odds=odds, stake=0, result=r,
                        payout=0, parlay_id=p.id))
    s.commit()
    return p


def test_all_legs_won_pays_the_combined_price(session):
    p = _parlay(session, ["win", "win"], stake=500)
    assert settle_parlays(session) == 1
    assert p.result == "win"
    assert p.payout == pytest.approx(1322.31, abs=0.01)


def test_any_losing_leg_loses_the_stake(session):
    p = _parlay(session, ["win", "loss"])
    settle_parlays(session)
    assert (p.result, p.payout) == ("loss", -100.0)


def test_a_push_with_no_loss_pushes(session):
    p = _parlay(session, ["win", "push"])
    settle_parlays(session)
    assert (p.result, p.payout) == ("push", 0.0)


def test_an_ungraded_leg_leaves_it_pending(session):
    """Review Focus 4: a canceled or postponed leg never grades; the parlay
    must stay pending, not become a loss."""
    p = _parlay(session, ["win", None])
    assert settle_parlays(session) == 0
    assert p.result is None


def test_the_scheduler_pass_settles_parlays(session):
    """Dead-wiring guard: settle_parlays working is worth nothing if the
    daily grading never calls it."""
    from backend.pipeline.scheduler import grade_pending_picks
    p = _parlay(session, ["win", "win"])
    grade_pending_picks(session)
    assert p.result == "win"

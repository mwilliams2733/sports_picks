"""A paper bet that can never be graded is pushed after two days.

Owner ruling 2026-09-30 (review finding I2 on fix/paper-trading). Open
stakes are now reserved against what a player can bet, so a bet that never
grades -- a canceled or postponed game, or a prop with no stat line (MLB
props, anytime-TD rows) -- would hold its stake forever, and so would any
parlay with such a leg. It is settled as a push (stake returned) once its
game is two days past and still ungradeable; a parlay with a pushed leg
then pushes under the existing parlay rule.
"""
from datetime import date, timedelta

import pytest
from sqlalchemy import create_engine

from backend.api.users import open_stakes
from backend.database import get_session
from backend.models import Base, Game, PaperPick, Parlay, Team, UserProfile
from backend.pipeline.paper_settlement import grade_paper_picks, settle_parlays

TODAY = date(2026, 10, 10)


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    s = get_session(engine)
    s.add(UserProfile(id=1, name="p"))
    s.add_all([Team(id=1, name="H", abbreviation="H", sport="mlb"),
               Team(id=2, name="A", abbreviation="A", sport="mlb")])
    s.flush()
    yield s
    s.close()


def _bet(s, *, status, days_ago, pick_type="moneyline", scores=None,
         parlay_id=None, stake=100.0):
    home, away = scores or (None, None)
    g = Game(sport="mlb", season="2026", date=TODAY - timedelta(days=days_ago),
             home_team_id=1, away_team_id=2, status=status,
             home_score=home, away_score=away)
    s.add(g)
    s.flush()
    prop = pick_type == "prop"
    p = PaperPick(user_id=1, game_id=g.id, pick_type=pick_type,
                  pick_value="Some Hitter Over 1.5 Hits" if prop else "HOME ML",
                  odds=-110, stake=0 if parlay_id else stake, result=None,
                  payout=None if not parlay_id else 0,
                  prop_market="batter_hits" if prop else None,
                  prop_player="Some Hitter" if prop else None,
                  parlay_id=parlay_id)
    s.add(p)
    s.commit()
    return p


@pytest.mark.parametrize("status", ["canceled", "postponed"])
def test_an_unplayed_game_two_days_past_is_pushed(session, status):
    bet = _bet(session, status=status, days_ago=2)
    grade_paper_picks(session, today=TODAY)
    assert (bet.result, bet.payout) == ("push", 0.0)
    assert open_stakes(session, session.get(UserProfile, 1)) == 0


@pytest.mark.parametrize("status", ["canceled", "postponed"])
def test_an_unplayed_game_one_day_past_waits(session, status):
    # A wrongly-canceled game has been corrected before; one day is not enough.
    bet = _bet(session, status=status, days_ago=1)
    grade_paper_picks(session, today=TODAY)
    assert bet.result is None
    assert open_stakes(session, session.get(UserProfile, 1)) == 100.0


def test_an_ungradeable_prop_two_days_past_is_pushed(session):
    # Final, but there is no box score for the player: grade_prop_pick
    # returns None and the normal pass leaves it.
    bet = _bet(session, status="final", days_ago=2, pick_type="prop", scores=(5, 3))
    grade_paper_picks(session, today=TODAY)
    assert (bet.result, bet.payout) == ("push", 0.0)


def test_an_ungradeable_prop_one_day_past_waits(session):
    # Box scores can land the next day.
    bet = _bet(session, status="final", days_ago=1, pick_type="prop", scores=(5, 3))
    grade_paper_picks(session, today=TODAY)
    assert bet.result is None


def test_a_gradeable_bet_is_graded_not_pushed(session):
    bet = _bet(session, status="final", days_ago=5, scores=(5, 3))
    grade_paper_picks(session, today=TODAY)
    assert bet.result == "win"


def test_a_scheduled_game_is_not_voided(session):
    # Not part of the ruling: a late-finalizing game is still to be graded.
    bet = _bet(session, status="scheduled", days_ago=5)
    grade_paper_picks(session, today=TODAY)
    assert bet.result is None


def test_a_parlay_with_a_canceled_leg_pushes_and_releases_its_stake(session):
    parlay = Parlay(user_id=1, stake=250.0, combined_odds=264)
    session.add(parlay)
    session.flush()
    _bet(session, status="final", days_ago=3, scores=(5, 3), parlay_id=parlay.id)
    _bet(session, status="canceled", days_ago=3, parlay_id=parlay.id)
    user = session.get(UserProfile, 1)
    assert open_stakes(session, user) == 250.0

    grade_paper_picks(session, today=TODAY)
    settle_parlays(session)

    assert (parlay.result, parlay.payout) == ("push", 0.0)
    assert open_stakes(session, user) == 0

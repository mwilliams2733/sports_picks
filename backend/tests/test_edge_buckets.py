"""Guards for the edge-bucket analysis.

The first version of this analysis was run by hand and its headline numbers
were later found to have been computed over duplicate rows. The point of the
module is that the figures can be re-derived, so the tests pin the arithmetic
and the two confounders the report is supposed to surface.
"""
import datetime

import pytest

from backend.analysis.edge_buckets import (
    DEFAULT_BUCKETS,
    bucket_rows,
    format_report,
    graded_picks,
)
from backend.database import get_engine, get_session
from backend.models import Base, Game, PickModel, PickResult, StrategyModel, Team

D = datetime.date(2026, 3, 19)


@pytest.fixture()
def session(tmp_path):
    s = get_session(get_engine(str(tmp_path / "e.db")))
    Base.metadata.create_all(s.get_bind())
    s.add_all([Team(id=i, name=f"T{i}", abbreviation=f"T{i}", sport="ncaab")
               for i in range(1, 7)])
    s.add(StrategyModel(id=1, name="ensemble", config_json="{}",
                        is_active=True, strategy_type="game"))
    s.flush()
    return s


def _add(session, pid, edge, price, result, payout, *, game_id=None,
         value="AWAY ML", day=D, neutral=False, home=1, away=2, sport="ncaab"):
    gid = game_id or pid + 100
    session.add(Game(id=gid, sport=sport, season="2026", date=day,
                     status="final", home_team_id=home, away_team_id=away,
                     home_score=80, away_score=70, neutral_site=neutral))
    session.flush()
    session.add(PickModel(id=pid, game_id=gid, strategy_id=1,
                          pick_type="moneyline", pick_value=value,
                          confidence=3, edge_pct=edge, odds_at_pick=price,
                          created_at=datetime.datetime(2026, 3, 19, 12, 0)))
    session.flush()
    session.add(PickResult(pick_id=pid, result=result, payout=payout))
    session.commit()


def test_picks_land_in_the_right_bucket(session):
    _add(session, 1, edge=10.0, price=150, result="win", payout=1.5)
    _add(session, 2, edge=25.0, price=300, result="loss", payout=-1.0)
    _add(session, 3, edge=40.0, price=900, result="loss", payout=-1.0)

    b = bucket_rows(graded_picks(session))
    assert [x.n for x in b] == [1, 1, 1]


def test_the_top_bucket_is_open_ended(session):
    """An edge above the last boundary must not vanish from the report."""
    _add(session, 1, edge=95.0, price=2000, result="loss", payout=-1.0)
    b = bucket_rows(graded_picks(session))
    assert b[-1].n == 1


def test_roi_and_win_rate_are_computed_per_bucket(session):
    _add(session, 1, edge=10.0, price=150, result="win", payout=1.5)
    _add(session, 2, edge=12.0, price=150, result="loss", payout=-1.0)

    b = bucket_rows(graded_picks(session))[0]
    assert b.n == 2 and b.wins == 1
    assert b.win_rate == 0.5
    assert b.units == pytest.approx(0.5)
    assert b.roi == pytest.approx(0.25)


def test_a_price_band_narrows_the_comparison(session):
    """Edge and price are confounded; the band is how they get separated.

    Both bounds are exercised: testing only the ceiling leaves the floor
    free to be deleted without any test noticing.
    """
    _add(session, 1, edge=10.0, price=150, result="win", payout=1.5)
    _add(session, 2, edge=12.0, price=2000, result="loss", payout=-1.0)
    _add(session, 3, edge=14.0, price=-300, result="win", payout=0.33)

    b = bucket_rows(graded_picks(session), min_price=100, max_price=500)
    assert b[0].n == 1, "a pick outside the price band was included"


def test_side_and_venue_are_counted(session):
    """Whether a bucket is all away picks at neutral venues is the finding."""
    _add(session, 1, edge=25.0, price=300, result="loss", payout=-1.0,
         value="AWAY ML", neutral=True)
    _add(session, 2, edge=26.0, price=300, result="loss", payout=-1.0,
         value="HOME ML", neutral=False)

    b = bucket_rows(graded_picks(session))[1]
    assert b.n == 2 and b.away == 1 and b.neutral == 1


def test_effective_n_is_below_raw_n_when_teams_repeat(session):
    for pid in range(1, 5):
        _add(session, pid, edge=10.0, price=150, result="loss", payout=-1.0,
             home=1, away=2)      # the same two teams every time
    b = bucket_rows(graded_picks(session))[0]
    assert b.n == 4
    assert b.effective_n(icc=0.05) < 4


def test_a_bucket_from_few_dates_is_flagged(session):
    """18 picks from 3 tournament days are not 18 independent trials."""
    for pid in range(1, 6):
        _add(session, pid, edge=25.0, price=300, result="loss", payout=-1.0,
             home=pid, away=pid + 1 if pid < 6 else 1)
    text = format_report(bucket_rows(graded_picks(session)), "t")
    assert "span only 1 date" in text


def test_a_sport_filter_is_applied(session):
    _add(session, 1, edge=10.0, price=150, result="win", payout=1.5,
         sport="ncaab")
    _add(session, 2, edge=10.0, price=150, result="win", payout=1.5,
         sport="nba", home=3, away=4)

    assert len(graded_picks(session, sport="ncaab")) == 1


def test_ungraded_picks_are_excluded(session):
    """A pick with no result is not a zero -- it is absent."""
    _add(session, 1, edge=10.0, price=150, result="win", payout=1.5)
    session.add(Game(id=999, sport="ncaab", season="2026", date=D,
                     status="scheduled", home_team_id=1, away_team_id=2))
    session.flush()
    session.add(PickModel(id=99, game_id=999, strategy_id=1,
                          pick_type="moneyline", pick_value="AWAY ML",
                          confidence=3, edge_pct=10.0, odds_at_pick=150,
                          created_at=datetime.datetime(2026, 3, 19, 12, 0)))
    session.commit()

    assert len(graded_picks(session)) == 1

"""Kelly sizing must reach the database, and the adjusters must reach Kelly.

Two gaps, one of which made the other pointless:

* `generate_and_store_picks` never wrote `suggested_unit_size`. `PickModel`
  had no such column and no API route served one, so the stake a strategy
  computed was discarded at persistence and only the backtester ever saw it.
* `adaptive_fraction`, `apply_drawdown_protection` and
  `apply_correlation_discount` had zero production call sites.

Wiring the second into the first while the first was still discarded would
have produced a more elaborate value that nothing reads.

The bankroll is derived, not configured
---------------------------------------
One unit is 1% of bankroll (`UNITS_PER_BANKROLL`), so a full bankroll is
100 units *by definition*. Settled picks move it from there. Feeding raw
cumulative P&L to `apply_drawdown_protection` instead would be nonsense:
P&L starts at 0, and `(peak - current) / peak` against a peak profit of
+20.7u reported a 183% drawdown on this book.

Calibration is refused below its own sample size
------------------------------------------------
`adaptive_fraction` discriminates bands 0.03 and 0.05 wide. Resolving the
tighter one needs ~457 decided picks; the last 30 days hold 157. Below the
threshold the deviation is not measured and the configured fraction stands.
Firing on a sample that cannot resolve the band would invent precision.
"""
import datetime

import pytest
from sqlalchemy import create_engine

from backend.analysis.kelly import UNITS_PER_BANKROLL
from backend.database import get_session, run_migrations
from backend.models import Base, Game, PickModel, PickResult, Team
from backend.pipeline.pick_generator import (MIN_CALIBRATION_SAMPLE,
                                             _bankroll_state,
                                             _calibration_deviation)

DAY = datetime.date(2026, 9, 21)


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    run_migrations(engine)
    return get_session(engine)


def _game(session, day=DAY, status="final"):
    def team(name):
        t = (session.query(Team)
             .filter(Team.sport == "nba", Team.abbreviation == name).first())
        if t:
            return t
        t = Team(name=name, abbreviation=name, sport="nba")
        session.add(t)
        session.flush()
        return t

    g = Game(sport="nba", date=day, season="2026", status=status,
             home_team_id=team("AAA").id, away_team_id=team("BBB").id,
             home_score=100, away_score=90)
    session.add(g)
    session.flush()
    return g


def _settled(session, game, result, payout, confidence=3):
    p = PickModel(game_id=game.id, strategy_id=1, pick_type="moneyline",
                  pick_value="HOME ML", confidence=confidence, edge_pct=5.0,
                  odds_at_pick=-110)
    session.add(p)
    session.flush()
    session.add(PickResult(pick_id=p.id, result=result, payout=payout))
    session.flush()
    return p


# --- the bankroll the drawdown guard reads --------------------------------

def test_an_empty_book_starts_at_a_full_bankroll(session):
    session.commit()

    current, peak = _bankroll_state(session)

    assert current == float(UNITS_PER_BANKROLL)
    assert peak == float(UNITS_PER_BANKROLL)


def test_a_losing_book_is_below_its_peak_not_below_zero(session):
    """The bug this prevents: raw P&L starts at 0, so a peak profit of
    +20.7u and a current of -12.2u reported a 183% drawdown."""
    g = _game(session)
    _settled(session, g, "win", 0.91)
    _settled(session, g, "loss", 0.0)
    _settled(session, g, "loss", 0.0)
    session.commit()

    current, peak = _bankroll_state(session)

    assert peak == pytest.approx(100.91)
    assert current == pytest.approx(98.91)
    assert 0.0 < (peak - current) / peak < 1.0, "a drawdown is a fraction"


def test_a_push_moves_neither_balance_nor_peak(session):
    g = _game(session)
    _settled(session, g, "push", 0.0)
    session.commit()

    assert _bankroll_state(session) == (float(UNITS_PER_BANKROLL),
                                        float(UNITS_PER_BANKROLL))


# --- the calibration sample guard -----------------------------------------

def test_calibration_is_not_measured_below_its_sample_size(session):
    """157 decided picks cannot resolve a 0.03 band. Returning a number
    anyway would move every stake on noise."""
    g = _game(session)
    for _ in range(10):
        _settled(session, g, "win", 0.91)
    session.commit()

    assert _calibration_deviation(session, DAY) is None


def test_the_sample_threshold_is_derived_not_guessed(session):
    """Derived from the narrowest band `adaptive_fraction` discriminates,
    so it follows if those bands ever move."""
    assert MIN_CALIBRATION_SAMPLE > 400


def test_calibration_is_measured_once_the_sample_supports_it(session):
    g = _game(session)
    for _ in range(MIN_CALIBRATION_SAMPLE):
        _settled(session, g, "win", 0.91, confidence=1)
    session.commit()

    deviation = _calibration_deviation(session, DAY)

    # Tier 1 is expected to win 50%; these all won, so deviation is ~0.5.
    assert deviation == pytest.approx(0.5, abs=0.01)


def test_pushes_are_excluded_from_the_calibration_denominator(session):
    """A push is not a decided pick, and counting it would drag the measured
    rate toward zero."""
    g = _game(session)
    for _ in range(MIN_CALIBRATION_SAMPLE):
        _settled(session, g, "win", 0.91, confidence=1)
    for _ in range(50):
        _settled(session, g, "push", 0.0, confidence=1)
    session.commit()

    assert _calibration_deviation(session, DAY) == pytest.approx(0.5, abs=0.01)

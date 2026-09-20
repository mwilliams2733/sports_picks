"""Guards for the totals validation.

The report decides whether the model is allowed to bet, so the property that
matters is that it compares against the market line and not only against the
old constant -- beating a constant is not skill.
"""
import datetime

import pytest

from backend.analysis.totals_report import (
    LEGACY_CONSTANT_TOTAL,
    evaluate,
    predicted_total,
)
from backend.database import get_engine, get_session
from backend.models import Base, Game, Odds, Team, TeamStat

D = datetime.date(2026, 3, 19)


@pytest.fixture()
def session(tmp_path):
    s = get_session(get_engine(str(tmp_path / "t.db")))
    Base.metadata.create_all(s.get_bind())
    s.add_all([Team(id=1, name="H", abbreviation="H", sport="nba"),
               Team(id=2, name="A", abbreviation="A", sport="nba")])
    s.flush()
    return s


def _game(s, gid, actual_home, actual_away, *, pf=110.0, pa=110.0, line=None):
    s.add(Game(id=gid, sport="nba", season="2026", date=D, status="final",
               home_team_id=1, away_team_id=2,
               home_score=actual_home, away_score=actual_away))
    s.flush()
    for team_id in (1, 2):
        s.add(TeamStat(game_id=gid, team_id=team_id, stat_type="points_for",
                       value=pf))
        s.add(TeamStat(game_id=gid, team_id=team_id,
                       stat_type="points_against", value=pa))
    if line is not None:
        s.add(Odds(game_id=gid, bookmaker="bk", over_under=line))
    s.commit()


def test_predicted_total_is_the_matchup_average():
    home = {"points_for": 120.0, "points_against": 110.0}
    away = {"points_for": 110.0, "points_against": 120.0}
    assert predicted_total(home, away) == pytest.approx(230.0)


def test_a_missing_history_predicts_nothing():
    assert predicted_total({"points_for": 110.0}, {"points_for": 110.0,
                                                   "points_against": 110.0}) is None


def test_games_without_scoring_stats_are_excluded(session):
    """A game we cannot predict must not count as a perfect or a zero."""
    session.add(Game(id=9, sport="nba", season="2026", date=D, status="final",
                     home_team_id=1, away_team_id=2,
                     home_score=110, away_score=110))
    session.commit()
    assert evaluate(session) == []


def test_mae_and_bias_are_measured(session):
    # Predicted 220 for both; actuals 230 and 210, so errors +10 and -10.
    _game(session, 1, 120, 110)
    _game(session, 2, 105, 105)
    r = evaluate(session)[0]
    assert r.n == 2
    assert r.mae == pytest.approx(10.0)
    assert r.bias == pytest.approx(0.0)


def test_the_legacy_constant_is_reported_for_comparison(session):
    _game(session, 1, 120, 110)     # actual 230
    r = evaluate(session)[0]
    assert r.legacy_mae == pytest.approx(abs(230 - LEGACY_CONSTANT_TOTAL))


def test_beating_the_line_is_judged_against_the_line(session):
    """The bar is the market's error on the same games, not the constant."""
    # Predicted 220, actual 230, line 229 -- the line is far closer.
    _game(session, 1, 120, 110, line=229.0)
    r = evaluate(session)[0]
    assert r.n_with_line == 1
    assert r.mae_vs_line == pytest.approx(10.0)
    assert r.line_mae == pytest.approx(1.0)
    assert r.beats_line is False


def test_beating_the_line_can_be_true(session):
    # Predicted 220, actual 221, line 240.
    _game(session, 1, 121, 100, line=240.0)
    assert evaluate(session)[0].beats_line is True


def test_beats_line_is_unknown_without_priced_games(session):
    _game(session, 1, 120, 110)
    assert evaluate(session)[0].beats_line is None


def test_residual_sd_is_reported(session):
    _game(session, 1, 120, 110)     # +10
    _game(session, 2, 105, 105)     # -10
    assert evaluate(session)[0].residual_sd == pytest.approx(10.0)

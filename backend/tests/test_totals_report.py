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


# --------------------------------------------------------------------------
# Venue splits, compared head to head with the blended rate.
# --------------------------------------------------------------------------

def _split_game(s, gid, actual_home, actual_away, *, home_split, away_split,
                blended=110.0, neutral=False):
    s.add(Game(id=gid, sport="nba", season="2026", date=D, status="final",
               home_team_id=1, away_team_id=2, neutral_site=neutral,
               home_score=actual_home, away_score=actual_away))
    s.flush()
    for team_id in (1, 2):
        for k in ("points_for", "points_against"):
            s.add(TeamStat(game_id=gid, team_id=team_id, stat_type=k,
                           value=blended))
    s.add(TeamStat(game_id=gid, team_id=1, stat_type="points_for_home",
                   value=home_split[0]))
    s.add(TeamStat(game_id=gid, team_id=1, stat_type="points_against_home",
                   value=home_split[1]))
    s.add(TeamStat(game_id=gid, team_id=2, stat_type="points_for_away",
                   value=away_split[0]))
    s.add(TeamStat(game_id=gid, team_id=2, stat_type="points_against_away",
                   value=away_split[1]))
    s.commit()


def test_the_split_prediction_uses_each_sides_own_venue(session):
    _split_game(session, 1, 120, 110, home_split=(118, 112),
                away_split=(108, 122))
    r = evaluate(session)[0]
    assert r.n_split == 1
    # (118 + 112 + 108 + 122) / 2 = 230
    assert r.split_mae == pytest.approx(abs(230 - 230))


def test_the_comparison_is_on_identical_games(session):
    """A game with no split must not enter either side of the comparison."""
    _split_game(session, 1, 120, 110, home_split=(118, 112),
                away_split=(108, 122))
    _game(session, 2, 130, 130)          # blended only, no splits
    r = evaluate(session)[0]
    assert r.n == 2
    assert r.n_split == 1, "an unsplit game leaked into the split comparison"


def test_a_neutral_game_is_excluded_from_the_split_comparison(session):
    """Neither team is at home, so a venue split does not apply."""
    _split_game(session, 1, 120, 110, home_split=(118, 112),
                away_split=(108, 122), neutral=True)
    assert evaluate(session)[0].n_split == 0


def test_splits_help_is_reported_both_ways(session):
    # Blended predicts 220, split predicts 230, actual 230 -> split wins.
    _split_game(session, 1, 120, 110, home_split=(118, 112),
                away_split=(108, 122))
    assert evaluate(session)[0].splits_help is True


def test_splits_help_is_false_when_the_blend_is_closer(session):
    # Blended predicts 220, split predicts 260, actual 220 -> blend wins.
    _split_game(session, 1, 110, 110, home_split=(130, 130),
                away_split=(130, 130))
    assert evaluate(session)[0].splits_help is False


def test_splits_help_is_unknown_without_any_split_games(session):
    _game(session, 1, 120, 110)
    assert evaluate(session)[0].splits_help is None


# --------------------------------------------------------------------------
# Long-layoff bias.
#
# Rest does not predict totals within a phase -- the nba regular-season
# slope is +0.135 (t +0.47) and the postseason slope -0.123 (t -0.49). But
# pooled they give -0.342 (t -2.25), because playoff games carry a long
# layoff AND score about 20 points less. Splitting keeps that from being
# modelled as a rest effect.
# --------------------------------------------------------------------------

def _rested_game(s, gid, actual_home, actual_away, rest_each, *, blended=110.0):
    s.add(Game(id=gid, sport="nba", season="2026", date=D, status="final",
               home_team_id=1, away_team_id=2,
               home_score=actual_home, away_score=actual_away))
    s.flush()
    for team_id in (1, 2):
        for k in ("points_for", "points_against"):
            s.add(TeamStat(game_id=gid, team_id=team_id, stat_type=k,
                           value=blended))
        s.add(TeamStat(game_id=gid, team_id=team_id, stat_type="rest_days",
                       value=rest_each))
    s.commit()


def test_normal_and_long_layoff_games_are_split(session):
    _rested_game(session, 1, 120, 110, rest_each=2.0)    # combined 4
    _rested_game(session, 2, 100, 100, rest_each=10.0)   # combined 20
    r = evaluate(session)[0]
    assert r.n_normal_rest == 1
    assert r.n_long_layoff == 1


def test_the_two_biases_are_reported_separately(session):
    # Both predict 220. Normal game lands on it; layoff game lands 20 under.
    _rested_game(session, 1, 110, 110, rest_each=2.0)
    _rested_game(session, 2, 100, 100, rest_each=10.0)
    r = evaluate(session)[0]
    assert r.bias_normal_rest == pytest.approx(0.0)
    assert r.bias_long_layoff == pytest.approx(-20.0)


def test_a_game_without_rest_data_enters_neither_group(session):
    """Missing is not normal; counting it as such would dilute the split."""
    _game(session, 1, 120, 110)          # no rest_days rows
    r = evaluate(session)[0]
    assert r.n == 1
    assert r.n_normal_rest == 0 and r.n_long_layoff == 0


def test_the_boundary_is_inclusive_on_the_layoff_side(session):
    from backend.analysis.totals_report import LONG_LAYOFF_DAYS
    _rested_game(session, 1, 110, 110, rest_each=LONG_LAYOFF_DAYS / 2)
    assert evaluate(session)[0].n_long_layoff == 1

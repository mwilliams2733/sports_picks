"""Per-sport, per-phase bias correction for the totals model.

nba postseason games score far less than the regular season -- 210.5 against
231.1 -- and the model, fitted on regular-season scoring rates, misses them
by -17.09 on average (n=21, sd 20.6, t = -3.80).

Keyed on (sport, phase), not phase alone: ncaab postseason shows no bias
(-1.58, t = -0.50, n=20), because a single-elimination tournament runs at
roughly regular-season pace while an nba playoff series does not. A global
playoff correction would have been wrong for it.
"""
import datetime

import pytest

import backend.analysis.variants.ensemble as ens
from backend.analysis.sport_constants import TOTAL_BIAS_BY_PHASE, get_total_bias
from backend.data_types import GameData, OddsSnapshot, TeamStats

S = ens.EnsembleStrategy("ensemble", {}, {})


def _stats(pf=110.0, pa=110.0):
    return TeamStats(
        point_diff=0.0, home_record=(5, 5), away_record=(5, 5),
        last_n_record=(5, 5), offensive_rating=100.0, defensive_rating=100.0,
        pace=100.0, strength_of_schedule=0.5, elo_rating=1500.0, rest_days=1,
        points_for=pf, points_against=pa)


def _game(sport, phase):
    return GameData(
        game_id=1, sport=sport, date=datetime.date(2026, 5, 20),
        home_team_id=1, away_team_id=2, season_type=phase,
        home_stats=_stats(), away_stats=_stats(),
        odds=[OddsSnapshot(bookmaker="b", moneyline_home=-110,
                           moneyline_away=-110, spread_home=-1.0,
                           spread_away=1.0, over_under=220.0)])


def test_an_nba_playoff_total_is_corrected_downward():
    assert S._predicted_total(_game("nba", "postseason")) == \
        pytest.approx(220.0 - 17.1)


def test_an_nba_regular_season_total_is_untouched():
    assert S._predicted_total(_game("nba", "regular")) == pytest.approx(220.0)


def test_ncaab_postseason_is_not_corrected():
    """Measured at -1.58 (t -0.50); a global playoff rule would be wrong."""
    assert S._predicted_total(_game("ncaab", "postseason")) == \
        pytest.approx(220.0)


def test_an_unknown_phase_is_not_corrected():
    """Rows predating the column must not be silently shifted."""
    assert S._predicted_total(_game("nba", "unknown")) == pytest.approx(220.0)


def test_an_unmeasured_sport_is_not_corrected():
    assert get_total_bias("nfl", "postseason") == 0.0
    assert get_total_bias("mlb", "postseason") == 0.0


def test_only_measured_entries_are_present():
    """A table of guesses would be indistinguishable from one of measurements.

    If this fails someone added a phase; check totals_report supports it on a
    sample worth the name.
    """
    assert set(TOTAL_BIAS_BY_PHASE) == {("nba", "postseason")}


def test_the_correction_has_the_measured_sign_and_scale():
    bias = get_total_bias("nba", "postseason")
    assert -26.5 < bias < -7.7, "outside the measured 95% interval"

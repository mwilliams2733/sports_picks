"""The ensemble is the only active game strategy (row 1 of `strategies`,
`is_active=1`), and until this change it never read the starting pitcher:
zero of the 50 MLB picks in the database carried a `pitcher_edge` factor,
even though the scheduler fetches probable-pitcher stats and attaches a
`pitcher_skill_score` to every MLB team's stats. This file tests the logit
shift in `_calibrated_probability` that fixes that, and that the shift is
reflected honestly in the `pitcher_edge` factor emitted on the pick.
"""
from datetime import date

import pytest

from backend.analysis.variants.ensemble import EnsembleStrategy
from backend.data_types import GameData, TeamStats, OddsSnapshot


def _stats(**kw):
    defaults = dict(point_diff=0.0, home_record=(0, 0), away_record=(0, 0),
        last_n_record=(5, 5), offensive_rating=100.0, defensive_rating=100.0,
        pace=100.0, strength_of_schedule=0.5, elo_rating=1500.0, rest_days=2,
        points_for=105.0, points_against=105.0)
    defaults.update(kw)
    return TeamStats(**defaults)


def _mlb_game(home_pitcher, away_pitcher, sport="mlb"):
    return GameData(game_id=1, sport=sport, date=date(2026, 6, 1),
        home_team_id=1, away_team_id=2,
        home_stats=_stats(pitcher_skill_score=home_pitcher),
        away_stats=_stats(pitcher_skill_score=away_pitcher),
        odds=[OddsSnapshot(bookmaker="dk", moneyline_home=-110, moneyline_away=-110,
                           spread_home=-1.5, spread_away=1.5, over_under=8.5)])


@pytest.fixture
def base_half(monkeypatch):
    """Pin the base model at exactly 0.5 so every test measures only the
    pitcher term. Pins the LEGACY method, not `_calibrated_probability`,
    because the latter is what is under test."""
    monkeypatch.setattr(EnsembleStrategy, "_legacy_calibrated_probability",
                        lambda self, game: 0.5)


def test_no_pitchers_no_shift(base_half):
    strategy = EnsembleStrategy("ensemble", {})
    game = _mlb_game(None, None)
    assert strategy._calibrated_probability(game) == 0.5


def test_one_missing_pitcher_no_shift(base_half):
    strategy = EnsembleStrategy("ensemble", {})
    assert strategy._calibrated_probability(_mlb_game(0.8, None)) == 0.5
    assert strategy._calibrated_probability(_mlb_game(None, 0.8)) == 0.5


def test_home_ace_raises_home_probability(base_half):
    strategy = EnsembleStrategy("ensemble", {})
    result = strategy._calibrated_probability(_mlb_game(0.76, 0.50))
    assert result > 0.5
    assert 0.55 <= result <= 0.60


def test_shift_is_antisymmetric(base_half):
    strategy = EnsembleStrategy("ensemble", {})
    p1 = strategy._calibrated_probability(_mlb_game(0.76, 0.26))
    p2 = strategy._calibrated_probability(_mlb_game(0.26, 0.76))
    assert abs((p1 - 0.5) + (p2 - 0.5)) < 1e-9


def test_non_mlb_ignores_pitcher_scores(base_half):
    strategy = EnsembleStrategy("ensemble", {})
    result = strategy._calibrated_probability(_mlb_game(0.9, 0.1, sport="nfl"))
    assert result == 0.5


def test_pitcher_edge_factor_is_emitted_on_the_pick(base_half):
    strategy = EnsembleStrategy("ensemble", {"min_edge": 1.0, "max_edge": 100.0})
    picks = strategy.predict(_mlb_game(0.80, 0.30))
    assert len(picks) >= 1
    assert picks[0].pick_value == "HOME ML"
    assert "pitcher_edge" in {f.code for f in picks[0].factors}


def test_no_pitcher_edge_factor_when_scores_missing(monkeypatch):
    monkeypatch.setattr(EnsembleStrategy, "_legacy_calibrated_probability",
                        lambda self, game: 0.6)
    strategy = EnsembleStrategy("ensemble", {"min_edge": 1.0, "max_edge": 100.0})
    picks = strategy.predict(_mlb_game(None, None))
    assert len(picks) >= 1
    codes = {f.code for p in picks for f in p.factors}
    assert "pitcher_edge" not in codes


def test_pitcher_shift_respects_the_clamp(monkeypatch):
    monkeypatch.setattr(EnsembleStrategy, "_legacy_calibrated_probability",
                        lambda self, game: 0.985)
    strategy = EnsembleStrategy("ensemble", {})
    result = strategy._calibrated_probability(_mlb_game(1.0, 0.0))
    assert result <= 0.99

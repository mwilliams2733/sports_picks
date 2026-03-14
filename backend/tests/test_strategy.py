import pytest
from backend.analysis.strategy import Strategy
from backend.data_types import GameData, TeamStats, Pick
from datetime import date

def _make_stats(**overrides):
    defaults = dict(point_diff=0.0, home_record=(0, 0), away_record=(0, 0),
        last_n_record=(0, 0), offensive_rating=100.0, defensive_rating=100.0,
        pace=100.0, strength_of_schedule=0.5, elo_rating=1500.0, rest_days=1)
    defaults.update(overrides)
    return TeamStats(**defaults)

def _make_game(**overrides):
    defaults = dict(game_id=1, sport="nba", date=date(2026, 3, 13),
        home_team_id=1, away_team_id=2, home_stats=_make_stats(), away_stats=_make_stats(), odds=[], week=None)
    defaults.update(overrides)
    return GameData(**defaults)

def test_strategy_is_abstract():
    with pytest.raises(TypeError):
        Strategy("test", {})

def test_concrete_strategy_can_predict():
    class TestStrategy(Strategy):
        def predict(self, game):
            return [Pick(game_id=game.game_id, pick_type="moneyline", pick_value="HOME ML",
                confidence=3, edge_pct=6.0, model_probability=0.6, implied_probability=0.54, odds_at_pick=-150)]
    s = TestStrategy("test", {"min_edge": 5.0})
    picks = s.predict(_make_game())
    assert len(picks) == 1
    assert picks[0].pick_type == "moneyline"

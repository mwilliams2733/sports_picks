from backend.analysis.variants.prop_value import PropValueStrategy


def test_prop_value_strategy_exists():
    strategy = PropValueStrategy("prop_value", {"recent_weight": 0.6, "season_weight": 0.4, "min_edge": 5.0})
    assert strategy.name == "prop_value"


def test_prop_value_predict_returns_empty():
    from backend.data_types import GameData, TeamStats
    from datetime import date
    strategy = PropValueStrategy("prop_value", {})
    dummy_stats = TeamStats(point_diff=0, home_record=(0, 0), away_record=(0, 0),
        last_n_record=(0, 0), offensive_rating=100, defensive_rating=100,
        pace=100, strength_of_schedule=0.5, elo_rating=1500, rest_days=2)
    game = GameData(game_id=1, sport="nba", date=date.today(),
        home_team_id=1, away_team_id=2, home_stats=dummy_stats, away_stats=dummy_stats)
    result = strategy.predict(game)
    assert result == []

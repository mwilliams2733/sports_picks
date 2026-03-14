from datetime import date
from backend.backtesting.backtester import Backtester
from backend.analysis.strategy import Strategy
from backend.data_types import GameData, TeamStats, Pick, OddsSnapshot

class MockStrategy(Strategy):
    def predict(self, game):
        return [Pick(game_id=game.game_id, pick_type="moneyline", pick_value="HOME ML",
            confidence=4, edge_pct=8.0, model_probability=0.65, implied_probability=0.55, odds_at_pick=-150)]

def _game(gid, home_score, away_score):
    stats = TeamStats(point_diff=0, home_record=(0,0), away_record=(0,0),
        last_n_record=(0,0), offensive_rating=100, defensive_rating=100,
        pace=100, strength_of_schedule=0.5, elo_rating=1500, rest_days=2)
    return GameData(game_id=gid, sport="nba", date=date(2026, 1, 1),
        home_team_id=1, away_team_id=2, home_stats=stats, away_stats=stats,
        odds=[OddsSnapshot("dk", -150, 130, -3.5, 3.5, 215.0)]), home_score, away_score

def test_backtest_calculates_record():
    strategy = MockStrategy("mock", {})
    games = [_game(1, 110, 100), _game(2, 95, 105), _game(3, 108, 102)]
    bt = Backtester(strategy)
    results = bt.run(games)
    assert results["wins"] == 2
    assert results["losses"] == 1
    assert results["total"] == 3
    assert abs(results["win_rate"] - 66.67) < 1

def test_backtest_calculates_roi():
    strategy = MockStrategy("mock", {})
    games = [_game(1, 110, 100), _game(2, 95, 105)]
    bt = Backtester(strategy)
    results = bt.run(games)
    assert results["roi"] < 0

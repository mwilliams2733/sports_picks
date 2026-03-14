from datetime import date
from backend.analysis.variants.ensemble import EnsembleStrategy
from backend.data_types import GameData, TeamStats, OddsSnapshot

def _stats(**kw):
    defaults = dict(point_diff=0.0, home_record=(0, 0), away_record=(0, 0),
        last_n_record=(0, 0), offensive_rating=100.0, defensive_rating=100.0,
        pace=100.0, strength_of_schedule=0.5, elo_rating=1500.0, rest_days=2)
    defaults.update(kw)
    return TeamStats(**defaults)

def test_ensemble_returns_picks_when_edge_exists():
    game = GameData(game_id=1, sport="nba", date=date(2026, 3, 13),
        home_team_id=1, away_team_id=2,
        home_stats=_stats(point_diff=8.0, elo_rating=1600, offensive_rating=115.0, defensive_rating=105.0),
        away_stats=_stats(point_diff=-3.0, elo_rating=1400, offensive_rating=105.0, defensive_rating=112.0),
        odds=[OddsSnapshot(bookmaker="dk", moneyline_home=-120, moneyline_away=100,
            spread_home=-3.5, spread_away=3.5, over_under=215.0)])
    strategy = EnsembleStrategy("ensemble", {"min_edge": 5.0, "k_factor": 20, "lookback": 10})
    picks = strategy.predict(game)
    assert isinstance(picks, list)
    for pick in picks:
        assert pick.edge_pct >= 0

def test_ensemble_no_picks_without_odds():
    game = GameData(game_id=1, sport="nba", date=date(2026, 3, 13),
        home_team_id=1, away_team_id=2, home_stats=_stats(), away_stats=_stats(), odds=[])
    strategy = EnsembleStrategy("ensemble", {"min_edge": 5.0, "k_factor": 20, "lookback": 10})
    picks = strategy.predict(game)
    assert picks == []

from datetime import date
from backend.analysis.variants.value_only import ValueOnlyStrategy
from backend.data_types import GameData, TeamStats, OddsSnapshot


def _stats(**kw):
    defaults = dict(point_diff=0.0, home_record=(0, 0), away_record=(0, 0),
        last_n_record=(0, 0), offensive_rating=100.0, defensive_rating=100.0,
        pace=100.0, strength_of_schedule=0.5, elo_rating=1500.0, rest_days=2)
    defaults.update(kw)
    return TeamStats(**defaults)


def test_value_only_picks_large_edge():
    """Should pick when edge is >= 10% (default threshold)."""
    game = GameData(game_id=1, sport="nba", date=date(2026, 3, 13),
        home_team_id=1, away_team_id=2,
        home_stats=_stats(point_diff=10.0, elo_rating=1650, offensive_rating=118.0, defensive_rating=105.0),
        away_stats=_stats(point_diff=-5.0, elo_rating=1350, offensive_rating=102.0, defensive_rating=115.0),
        odds=[OddsSnapshot(bookmaker="dk", moneyline_home=100, moneyline_away=-100,
            spread_home=-5.0, spread_away=5.0, over_under=215.0)])
    strategy = ValueOnlyStrategy("value_only", {"min_edge": 10.0})
    picks = strategy.predict(game)
    assert len(picks) >= 1
    assert picks[0].edge_pct >= 10.0


def test_value_only_skips_small_edge():
    """Should not pick when edge is below 10%."""
    game = GameData(game_id=1, sport="nba", date=date(2026, 3, 13),
        home_team_id=1, away_team_id=2,
        home_stats=_stats(point_diff=1.0, elo_rating=1510, offensive_rating=106.0, defensive_rating=105.0),
        away_stats=_stats(point_diff=-0.5, elo_rating=1490, offensive_rating=105.0, defensive_rating=106.0),
        odds=[OddsSnapshot(bookmaker="dk", moneyline_home=-120, moneyline_away=100,
            spread_home=-2.5, spread_away=2.5, over_under=210.0)])
    strategy = ValueOnlyStrategy("value_only", {"min_edge": 10.0})
    picks = strategy.predict(game)
    assert picks == []


def test_value_only_no_odds():
    game = GameData(game_id=1, sport="nba", date=date(2026, 3, 13),
        home_team_id=1, away_team_id=2, home_stats=_stats(), away_stats=_stats(), odds=[])
    strategy = ValueOnlyStrategy("value_only", {"min_edge": 10.0})
    assert strategy.predict(game) == []

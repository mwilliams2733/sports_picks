from datetime import date
from backend.data_types import GameData, TeamStats, Pick, OddsSnapshot

def test_team_stats_defaults():
    stats = TeamStats(
        point_diff=3.5, home_record=(10, 5), away_record=(8, 7),
        last_n_record=(7, 3), offensive_rating=112.0, defensive_rating=108.0,
        pace=100.0, strength_of_schedule=0.55, elo_rating=1520.0, rest_days=1
    )
    assert stats.turnover_margin is None
    assert stats.red_zone_pct is None
    assert stats.conference_strength is None

def test_pick_edge_calculation():
    pick = Pick(
        game_id=1, pick_type="moneyline", pick_value="BOS ML",
        confidence=4, edge_pct=8.2, model_probability=0.65,
        implied_probability=0.568, odds_at_pick=-110
    )
    assert pick.edge_pct == 8.2
    assert pick.confidence == 4

def test_odds_snapshot():
    snap = OddsSnapshot(
        bookmaker="draftkings", moneyline_home=-150, moneyline_away=130,
        spread_home=-4.5, spread_away=4.5, over_under=218.5
    )
    assert snap.bookmaker == "draftkings"

def test_game_data():
    stats = TeamStats(
        point_diff=0.0, home_record=(0, 0), away_record=(0, 0),
        last_n_record=(0, 0), offensive_rating=100.0, defensive_rating=100.0,
        pace=100.0, strength_of_schedule=0.5, elo_rating=1500.0, rest_days=1
    )
    game = GameData(
        game_id=1, sport="nba", date=date(2026, 3, 13),
        home_team_id=1, away_team_id=2,
        home_stats=stats, away_stats=stats, odds=[], week=None
    )
    assert game.sport == "nba"
    assert game.week is None

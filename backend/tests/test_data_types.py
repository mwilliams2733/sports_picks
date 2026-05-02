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


def test_team_stats_supports_optional_pitcher_skill_score():
    """MLB needs a per-game pitcher signal carried alongside team stats."""
    from backend.data_types import TeamStats
    stats = TeamStats(
        point_diff=0.0, home_record=(0, 0), away_record=(0, 0),
        last_n_record=(0, 0), offensive_rating=100.0, defensive_rating=100.0,
        pace=100.0, strength_of_schedule=0.5, elo_rating=1500, rest_days=1,
        pitcher_skill_score=0.72,
    )
    assert stats.pitcher_skill_score == 0.72


def test_team_stats_pitcher_skill_score_defaults_to_none():
    """Field must be optional for non-MLB sports — backwards compatibility."""
    from backend.data_types import TeamStats
    stats = TeamStats(
        point_diff=0.0, home_record=(0, 0), away_record=(0, 0),
        last_n_record=(0, 0), offensive_rating=100.0, defensive_rating=100.0,
        pace=100.0, strength_of_schedule=0.5, elo_rating=1500, rest_days=1,
    )
    assert stats.pitcher_skill_score is None


def test_fighter_stats_carries_elo_form_and_opponent_quality():
    from backend.data_types import FighterStats
    fs = FighterStats(
        elo_rating=1650.0,
        recent_form_score=0.78,
        opponent_avg_elo=1520.0,
        fights_count=22,
        days_since_last_fight=120,
    )
    assert fs.elo_rating == 1650.0
    assert fs.recent_form_score == 0.78
    assert fs.opponent_avg_elo == 1520.0
    assert fs.fights_count == 22
    assert fs.days_since_last_fight == 120


def test_fighter_stats_handles_unrated_rookie():
    """A debut fighter has no Elo, no opponents to average — defaults must be sensible."""
    from backend.data_types import FighterStats
    fs = FighterStats(elo_rating=1500.0, recent_form_score=0.5,
                      opponent_avg_elo=None, fights_count=0,
                      days_since_last_fight=None)
    assert fs.opponent_avg_elo is None
    assert fs.fights_count == 0
    assert fs.days_since_last_fight is None

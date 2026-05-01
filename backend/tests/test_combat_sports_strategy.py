"""CombatSportsStrategy: h2h-only picks driven by fighter Elo + recent form."""
from datetime import date as _date

from backend.analysis.variants.combat_sports import CombatSportsStrategy
from backend.data_types import GameData, FighterStats, OddsSnapshot, TeamStats


def _empty_team_stats() -> TeamStats:
    """Combat games still need a TeamStats placeholder — fields ignored by the strategy."""
    return TeamStats(
        point_diff=0.0, home_record=(0, 0), away_record=(0, 0),
        last_n_record=(0, 0), offensive_rating=0.0, defensive_rating=0.0,
        pace=0.0, strength_of_schedule=0.5, elo_rating=1500.0, rest_days=0,
    )


def _fight_data(home_fs: FighterStats, away_fs: FighterStats, odds: OddsSnapshot, sport: str = "mma") -> GameData:
    gd = GameData(
        game_id=1, sport=sport, date=_date(2026, 4, 29),
        home_team_id=1, away_team_id=2,
        home_stats=_empty_team_stats(), away_stats=_empty_team_stats(),
        odds=[odds],
    )
    gd.home_fighter = home_fs
    gd.away_fighter = away_fs
    return gd


def test_strategy_picks_strong_favorite_at_plus_money():
    home = FighterStats(elo_rating=1750, recent_form_score=0.80,
                       opponent_avg_elo=1600, fights_count=15, days_since_last_fight=180)
    away = FighterStats(elo_rating=1550, recent_form_score=0.50,
                       opponent_avg_elo=1480, fights_count=12, days_since_last_fight=210)
    odds = OddsSnapshot(bookmaker="dk", moneyline_home=+120, moneyline_away=-140,
                        spread_home=0.0, spread_away=0.0, over_under=0.0)
    strat = CombatSportsStrategy(name="combat", config={"min_edge": 3.0})
    picks = strat.predict(_fight_data(home, away, odds))
    assert len(picks) == 1
    assert picks[0].pick_type == "moneyline"
    assert "HOME" in picks[0].pick_value


def test_strategy_skips_picks_with_no_edge():
    home = FighterStats(elo_rating=1500, recent_form_score=0.5,
                       opponent_avg_elo=1500, fights_count=10, days_since_last_fight=180)
    away = FighterStats(elo_rating=1500, recent_form_score=0.5,
                       opponent_avg_elo=1500, fights_count=10, days_since_last_fight=180)
    odds = OddsSnapshot(bookmaker="dk", moneyline_home=-110, moneyline_away=-110,
                        spread_home=0.0, spread_away=0.0, over_under=0.0)
    strat = CombatSportsStrategy(name="combat", config={"min_edge": 3.0})
    picks = strat.predict(_fight_data(home, away, odds))
    assert picks == []


def test_strategy_emits_no_spread_or_total_picks():
    home = FighterStats(elo_rating=1800, recent_form_score=0.9,
                       opponent_avg_elo=1700, fights_count=20, days_since_last_fight=120)
    away = FighterStats(elo_rating=1500, recent_form_score=0.4,
                       opponent_avg_elo=1450, fights_count=8, days_since_last_fight=300)
    odds = OddsSnapshot(bookmaker="dk", moneyline_home=-300, moneyline_away=+250,
                        spread_home=-2.5, spread_away=2.5, over_under=4.5)
    strat = CombatSportsStrategy(name="combat", config={"min_edge": 3.0})
    picks = strat.predict(_fight_data(home, away, odds))
    pick_types = {p.pick_type for p in picks}
    assert pick_types <= {"moneyline"}, f"Combat strategy emitted non-moneyline: {pick_types}"


def test_strategy_handles_debut_fighter_gracefully():
    """Debut fighter has fights_count=0 and opponent_avg_elo=None — must not crash."""
    home = FighterStats(elo_rating=1500, recent_form_score=0.5,
                       opponent_avg_elo=None, fights_count=0, days_since_last_fight=None)
    away = FighterStats(elo_rating=1600, recent_form_score=0.6,
                       opponent_avg_elo=1500, fights_count=10, days_since_last_fight=200)
    odds = OddsSnapshot(bookmaker="dk", moneyline_home=+150, moneyline_away=-180,
                        spread_home=0.0, spread_away=0.0, over_under=0.0)
    strat = CombatSportsStrategy(name="combat", config={"min_edge": 3.0})
    picks = strat.predict(_fight_data(home, away, odds))  # must not raise

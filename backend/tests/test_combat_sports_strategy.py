"""CombatSportsStrategy: h2h-only picks driven by fighter Elo + recent form."""
import math
from datetime import date as _date

import numpy as np

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


def test_boxing_strategy_skips_when_either_fighter_has_no_history():
    """For boxing: if either fighter has fights_count == 0, no pick — Wikidata
    coverage is sparse outside top fighters, so 'no fights logged' likely means
    'we know nothing' rather than 'genuine debut.'"""
    home = FighterStats(elo_rating=1500, recent_form_score=0.5,
                       opponent_avg_elo=None, fights_count=0, days_since_last_fight=None)
    away = FighterStats(elo_rating=1500, recent_form_score=0.5,
                       opponent_avg_elo=None, fights_count=0, days_since_last_fight=None)
    odds = OddsSnapshot(bookmaker="dk", moneyline_home=+200, moneyline_away=-250,
                        spread_home=0.0, spread_away=0.0, over_under=0.0)
    strat = CombatSportsStrategy(name="combat", config={"min_edge": 3.0})
    picks = strat.predict(_fight_data(home, away, odds, sport="boxing"))
    assert picks == []


def test_boxing_strategy_skips_when_only_one_fighter_has_history():
    """Asymmetric: one rated fighter, one unknown → also no pick."""
    home = FighterStats(elo_rating=1700, recent_form_score=0.8,
                       opponent_avg_elo=1500, fights_count=15, days_since_last_fight=120)
    away = FighterStats(elo_rating=1500, recent_form_score=0.5,
                       opponent_avg_elo=None, fights_count=0, days_since_last_fight=None)
    odds = OddsSnapshot(bookmaker="dk", moneyline_home=-300, moneyline_away=+250,
                        spread_home=0.0, spread_away=0.0, over_under=0.0)
    strat = CombatSportsStrategy(name="combat", config={"min_edge": 3.0})
    picks = strat.predict(_fight_data(home, away, odds, sport="boxing"))
    assert picks == []


def test_boxing_strategy_emits_pick_when_both_have_history():
    """Sanity check on the gate: when both have history, the boxing path
    works the same as MMA — strong favorite produces a pick."""
    home = FighterStats(elo_rating=1750, recent_form_score=0.85,
                       opponent_avg_elo=1600, fights_count=20, days_since_last_fight=150)
    away = FighterStats(elo_rating=1500, recent_form_score=0.50,
                       opponent_avg_elo=1480, fights_count=10, days_since_last_fight=200)
    odds = OddsSnapshot(bookmaker="dk", moneyline_home=+100, moneyline_away=-120,
                        spread_home=0.0, spread_away=0.0, over_under=0.0)
    strat = CombatSportsStrategy(name="combat", config={"min_edge": 3.0})
    picks = strat.predict(_fight_data(home, away, odds, sport="boxing"))
    assert len(picks) == 1
    assert picks[0].pick_type == "moneyline"


def test_mma_strategy_skips_when_either_fighter_has_no_history():   # Review Focus 5
    """Owner, 2026-10-10: once UFC history is loaded, an MMA fighter with no
    fights usually means "not in the UFC data", not a debut -- and pricing a
    known fighter against the 1500 seed is a built-in bias, not an edge. So
    MMA has the boxing gate too."""
    home = FighterStats(elo_rating=1700, recent_form_score=0.8,
                       opponent_avg_elo=1500, fights_count=15, days_since_last_fight=120)
    away = FighterStats(elo_rating=1500, recent_form_score=0.5,
                       opponent_avg_elo=None, fights_count=0, days_since_last_fight=None)
    odds = OddsSnapshot(bookmaker="dk", moneyline_home=-150, moneyline_away=+130,
                        spread_home=0.0, spread_away=0.0, over_under=0.0)
    strat = CombatSportsStrategy(name="combat", config={"min_edge": 3.0})
    assert strat.predict(_fight_data(home, away, odds, sport="mma")) == []


def test_mma_strategy_picks_when_both_fighters_have_history():      # Review Focus 5
    home = FighterStats(elo_rating=1750, recent_form_score=0.85,
                       opponent_avg_elo=1600, fights_count=20, days_since_last_fight=150)
    away = FighterStats(elo_rating=1500, recent_form_score=0.50,
                       opponent_avg_elo=1480, fights_count=10, days_since_last_fight=200)
    odds = OddsSnapshot(bookmaker="dk", moneyline_home=+100, moneyline_away=-120,
                        spread_home=0.0, spread_away=0.0, over_under=0.0)
    strat = CombatSportsStrategy(name="combat", config={"min_edge": 3.0})
    picks = strat.predict(_fight_data(home, away, odds, sport="mma"))
    assert len(picks) == 1 and picks[0].pick_type == "moneyline"


def test_mma_probability_is_symmetric_in_the_corners():           # Review Focus 5
    strat = CombatSportsStrategy(name="combat", config={})
    a = FighterStats(elo_rating=1650, recent_form_score=0.8, opponent_avg_elo=1550,
                     fights_count=5, days_since_last_fight=120)
    b = FighterStats(elo_rating=1500, recent_form_score=0.4, opponent_avg_elo=1480,
                     fights_count=3, days_since_last_fight=200)
    assert abs(strat._model_probability(a, b, "mma") + strat._model_probability(b, a, "mma") - 1) < 1e-9


def test_mma_uses_the_fitted_coefficients_and_boxing_keeps_the_blend():   # Review Focus 5
    from backend.analysis.variants import combat_sports as cs
    strat = CombatSportsStrategy(name="combat", config={})
    a = FighterStats(elo_rating=1600, recent_form_score=0.6, opponent_avg_elo=None,
                     fights_count=2, days_since_last_fight=None)
    b = FighterStats(elo_rating=1500, recent_form_score=0.5, opponent_avg_elo=None,
                     fights_count=2, days_since_last_fight=None)
    z = cs.MMA_COEF[0] * 100 / 400 + cs.MMA_COEF[1] * 0.1
    assert abs(strat._model_probability(a, b, "mma") - 1 / (1 + math.exp(-z))) < 1e-9
    blend = 0.78 / (1 + 10 ** (-100 / 400)) + 0.22 / (1 + 10 ** (-0.1 / 2.0))
    assert abs(strat._model_probability(a, b, "boxing") - blend) < 1e-9


def test_live_mma_formula_agrees_with_the_calibration():
    from backend.analysis.combat_calibration import probability
    from backend.analysis.combat_history import BoutFeatures
    from backend.analysis.variants import combat_sports as cs
    f = BoutFeatures(1, _date(2026, 1, 1), 1, 2, 1620.0, 1540.0, 0.8, 0.4, 1560.0, 1500.0, 5, 3, 1.0)
    a = FighterStats(elo_rating=1620.0, recent_form_score=0.8, opponent_avg_elo=1560.0,
                     fights_count=5, days_since_last_fight=None)
    b = FighterStats(elo_rating=1540.0, recent_form_score=0.4, opponent_avg_elo=1500.0,
                     fights_count=3, days_since_last_fight=None)
    strat = CombatSportsStrategy(name="combat", config={})
    assert abs(probability(np.array(cs.MMA_COEF), f) - strat._model_probability(a, b, "mma")) < 1e-9


def test_mma_elo_k_is_the_fitted_k():
    from backend.analysis.elo import get_k_factor
    assert get_k_factor("mma") == 16 and get_k_factor("boxing") == 24

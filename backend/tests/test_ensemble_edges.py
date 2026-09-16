"""Tests for distribution-based edge calculations in EnsembleStrategy."""
from datetime import date

import pytest

from backend.analysis.variants.ensemble import EnsembleStrategy
from backend.analysis.variants.value_only import ValueOnlyStrategy
from backend.analysis.variants.sport_specific import SportSpecificStrategy
from backend.analysis.variants.recent_form import RecentFormStrategy
from backend.analysis.variants.combat_sports import CombatSportsStrategy
from backend.data_types import GameData, TeamStats, OddsSnapshot, FighterStats


def test_spread_cover_probability_home():
    """P(home covers -3.5) when predicted diff is +7 should be well above 50%.

    cover_threshold = 3.5 (home must win by >3.5)
    P(margin > 3.5) where margin ~ N(7, 12) = P(Z > -0.2917) ≈ 0.615
    """
    strategy = EnsembleStrategy(name="test", config={})
    prob = strategy._spread_cover_prob(predicted_diff=7.0, cover_threshold=3.5, std=12.0)
    assert 0.5 < prob < 1.0
    assert abs(prob - 0.615) < 0.02


def test_spread_cover_probability_away():
    """P(away covers +3.5) when predicted diff is +1 (slight home favorite).

    P(away covers) = 1 - P(home margin > 3.5)
    With predicted_diff=1, margin ~ N(1, 12): P(margin > 3.5) ≈ 0.418
    So P(away covers) ≈ 0.582.
    """
    strategy = EnsembleStrategy(name="test", config={})
    home_cover_prob = strategy._spread_cover_prob(predicted_diff=1.0, cover_threshold=3.5, std=12.0)
    away_cover_prob = 1.0 - home_cover_prob
    assert 0.5 < away_cover_prob < 0.7


def test_ou_over_probability():
    """P(over 220.5) when predicted total is 225 should be above 50%."""
    strategy = EnsembleStrategy(name="test", config={})
    prob = strategy._over_probability(predicted_total=225.0, ou_line=220.5, std=15.0)
    assert 0.5 < prob < 1.0
    # P(X > 220.5) where X ~ N(225, 15) = P(Z > -0.3) ≈ 0.618
    assert abs(prob - 0.618) < 0.05


def test_ou_under_probability():
    """P(over 230) when predicted total is 220 should be below 50%."""
    strategy = EnsembleStrategy(name="test", config={})
    prob = strategy._over_probability(predicted_total=220.0, ou_line=230.0, std=15.0)
    assert prob < 0.5


def test_edge_uses_vig_adjusted_prob():
    """Edge calculated with no-vig implied prob should be larger than with raw implied."""
    model_prob = 0.60
    raw_implied = 0.5238  # -110
    no_vig = 0.50
    edge_with_vig = (model_prob - raw_implied) * 100
    edge_no_vig = (model_prob - no_vig) * 100
    assert edge_no_vig > edge_with_vig


def _team_stats() -> TeamStats:
    return TeamStats(
        point_diff=0.0, home_record=(0, 0), away_record=(0, 0),
        last_n_record=(0, 0), offensive_rating=100.0, defensive_rating=100.0,
        pace=100.0, strength_of_schedule=0.5, elo_rating=1500.0, rest_days=1,
    )


def _game_with_pickem_odds(sport: str = "nba") -> GameData:
    """Two books at -110/-110 -- a symmetric, vig-heavy quote."""
    odds = [
        OddsSnapshot(bookmaker="book1", moneyline_home=-110, moneyline_away=-110,
                     spread_home=None, spread_away=None, over_under=None),
        OddsSnapshot(bookmaker="book2", moneyline_home=-110, moneyline_away=-110,
                     spread_home=None, spread_away=None, over_under=None),
    ]
    return GameData(
        game_id=1, sport=sport, date=date(2026, 3, 13),
        home_team_id=1, away_team_id=2,
        home_stats=_team_stats(), away_stats=_team_stats(), odds=odds,
    )


def test_all_variants_agree_on_devigged_edge(monkeypatch):
    """Design doc §1f: "Update all edge calculations across strategy variants."

    Before plan 003, ensemble already de-vigged (this file's own
    test_edge_uses_vig_adjusted_prob documented that the four other variants
    did not, and so disagreed with ensemble for identical inputs). After
    plan 003 all five variants share Strategy._average_odds and call
    remove_vig identically, so a model that believes the *true* (de-vigged)
    fair probability is exactly 0.5 must show ~0 edge in every variant --
    not the ~+2.4 that a naive "matches the raw -110 price" model would
    show against the still-vig-laden raw implied probability.
    """
    fair_prob = 0.5  # no_vig_implied_prob("home", -110, -110)
    # A very negative min_edge guarantees a pick is emitted regardless of
    # the (near-zero) edge sign, so we can read edge_pct off the Pick.
    config = {"min_edge": -100.0}

    monkeypatch.setattr(EnsembleStrategy, "_calibrated_probability", lambda self, game: fair_prob)
    monkeypatch.setattr(ValueOnlyStrategy, "_model_probability", lambda self, game: fair_prob)
    monkeypatch.setattr(SportSpecificStrategy, "_model_probability", lambda self, game: fair_prob)
    monkeypatch.setattr(RecentFormStrategy, "_model_probability", lambda self, game, lookback: fair_prob)
    monkeypatch.setattr(CombatSportsStrategy, "_model_probability", lambda self, home, away: fair_prob)

    game = _game_with_pickem_odds()
    for strategy in (
        EnsembleStrategy(name="ensemble", config=config),
        ValueOnlyStrategy(name="value_only", config=config),
        SportSpecificStrategy(name="sport_specific", config=config),
        RecentFormStrategy(name="recent_form", config=config),
    ):
        picks = strategy.predict(game)
        assert len(picks) == 1, f"{strategy.name} produced {len(picks)} picks"
        assert picks[0].edge_pct == pytest.approx(0.0, abs=0.5), strategy.name

    combat_game = _game_with_pickem_odds(sport="mma")
    combat_game.home_fighter = FighterStats(elo_rating=1500, recent_form_score=0.5,
                                             opponent_avg_elo=1500, fights_count=10,
                                             days_since_last_fight=90)
    combat_game.away_fighter = FighterStats(elo_rating=1500, recent_form_score=0.5,
                                             opponent_avg_elo=1500, fights_count=10,
                                             days_since_last_fight=90)
    combat_strategy = CombatSportsStrategy(name="combat_sports", config=config)
    combat_picks = combat_strategy.predict(combat_game)
    assert len(combat_picks) == 1
    assert combat_picks[0].edge_pct == pytest.approx(0.0, abs=0.5)

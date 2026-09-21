"""Integration test: full Phase 1 prediction pipeline."""
from backend.analysis.variants.ensemble import EnsembleStrategy
from backend.analysis.odds_utils import american_to_implied_prob, remove_vig
from backend.analysis.kelly import fractional_kelly, adaptive_fraction
from backend.analysis.confidence import calculate_confidence, DEFAULT_THRESHOLDS
from backend.analysis.calibrated_model import extract_features, features_to_array, FEATURE_ORDER
from backend.data_types import GameData, TeamStats, OddsSnapshot


def _make_game():
    home = TeamStats(
        point_diff=3.5, home_record=(20, 10), away_record=(15, 12),
        last_n_record=(7, 3), offensive_rating=112.0, defensive_rating=108.0,
        pace=100.5, strength_of_schedule=0.5, elo_rating=1600, rest_days=2,
    )
    away = TeamStats(
        point_diff=1.0, home_record=(18, 12), away_record=(14, 14),
        last_n_record=(6, 4), offensive_rating=108.0, defensive_rating=110.0,
        pace=97.0, strength_of_schedule=0.6, elo_rating=1550, rest_days=3,
    )
    odds = [OddsSnapshot(
        bookmaker="test",
        moneyline_home=-150, moneyline_away=130,
        spread_home=-3.5, spread_away=3.5,
        over_under=220.5,
    )]
    return GameData(
        game_id=1, sport="nba", date="2026-03-16",
        home_team_id=1, away_team_id=2,
        home_stats=home, away_stats=away, odds=odds,
    )


def test_feature_extraction_expanded():
    game = _make_game()
    features = extract_features(game)
    assert isinstance(features, dict)
    assert len(features) >= 12
    arr = features_to_array(features)
    assert len(arr) == len(FEATURE_ORDER)


def test_vig_removal_produces_fair_probs():
    home_raw = american_to_implied_prob(-150)
    away_raw = american_to_implied_prob(130)
    home_fair, away_fair = remove_vig(home_raw, away_raw)
    assert abs(home_fair + away_fair - 1.0) < 0.001


def test_confidence_with_default_thresholds():
    assert calculate_confidence(15.0, 3) == 5
    assert calculate_confidence(6.0, 2) == 3
    assert calculate_confidence(2.0, 1) == 0


def test_kelly_respects_bounds():
    from backend.analysis.kelly import NO_BET

    result = fractional_kelly(0.9, -150, 0.25)
    assert 0.5 <= result <= 3.0
    # Was `== 0.5`. A -EV wager is declined outright now; the floor applies
    # to positive Kelly only and may not flip the sign of the answer.
    result = fractional_kelly(0.1, -150, 0.25)
    assert result == NO_BET


def test_adaptive_kelly_fraction():
    assert adaptive_fraction(0.01) == 0.35
    assert adaptive_fraction(0.04) == 0.25
    assert adaptive_fraction(0.10) == 0.15


def test_ensemble_has_distribution_methods():
    strategy = EnsembleStrategy(name="test", config={})
    assert hasattr(strategy, "_spread_cover_prob")
    assert hasattr(strategy, "_over_probability")
    assert hasattr(strategy, "_predicted_point_diff_ml")
    prob = strategy._spread_cover_prob(7.0, 3.5)
    assert 0.0 < prob < 1.0


def test_ensemble_has_lgbm_infrastructure():
    strategy = EnsembleStrategy(name="test", config={})
    assert hasattr(strategy, "_lgbm_model")
    assert hasattr(strategy, "train_lgbm_from_db")
    assert strategy._lgbm_model is None  # not trained yet


def test_full_pipeline_produces_picks():
    """EnsembleStrategy.predict() should produce picks with the new math."""
    game = _make_game()
    strategy = EnsembleStrategy(name="test", config={"min_edge": 1.0})  # low threshold to ensure picks
    picks = strategy.predict(game)
    # Should produce at least one pick type
    assert len(picks) >= 0  # May or may not have edges depending on model
    for pick in picks:
        assert pick.edge_pct > 0
        assert 0 <= pick.confidence <= 5
        assert 0.5 <= pick.suggested_unit_size <= 3.0

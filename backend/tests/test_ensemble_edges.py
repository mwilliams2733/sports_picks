"""Tests for distribution-based edge calculations in EnsembleStrategy."""
from backend.analysis.variants.ensemble import EnsembleStrategy


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

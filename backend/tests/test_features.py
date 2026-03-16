"""Tests for expanded feature extraction."""
from backend.analysis.calibrated_model import extract_features, features_to_array, FEATURE_ORDER
from backend.data_types import GameData, TeamStats, OddsSnapshot


def _make_game(**overrides) -> GameData:
    home = TeamStats(
        point_diff=3.5, home_record=(20, 10), away_record=(15, 12),
        last_n_record=(7, 3), offensive_rating=112.0, defensive_rating=108.0,
        pace=100.5, strength_of_schedule=0.5, elo_rating=1600, rest_days=2,
    )
    away = TeamStats(
        point_diff=5.0, home_record=(18, 12), away_record=(14, 14),
        last_n_record=(6, 4), offensive_rating=115.0, defensive_rating=105.0,
        pace=98.0, strength_of_schedule=0.6, elo_rating=1650, rest_days=1,
    )
    defaults = dict(
        game_id=1, sport="nba", date="2026-03-16",
        home_team_id=1, away_team_id=2,
        home_stats=home, away_stats=away, odds=[],
    )
    defaults.update(overrides)
    return GameData(**defaults)


def test_extract_features_returns_dict():
    game = _make_game()
    features = extract_features(game)
    assert isinstance(features, dict)


def test_extract_features_has_expanded_keys():
    game = _make_game()
    features = extract_features(game)
    expected_keys = {
        "elo_diff", "point_diff", "net_rating_diff",
        "home_rest_days", "away_rest_days", "pace_diff",
        "home_flag",
        "offensive_rating_home", "offensive_rating_away",
        "defensive_rating_home", "defensive_rating_away",
        "back_to_back_home", "back_to_back_away",
    }
    for key in expected_keys:
        assert key in features, f"Missing feature: {key}"


def test_extract_features_values():
    game = _make_game()
    features = extract_features(game)
    assert features["elo_diff"] == -50  # 1600 - 1650
    assert features["point_diff"] == -1.5  # 3.5 - 5.0
    assert features["home_rest_days"] == 2
    assert features["away_rest_days"] == 1
    assert features["home_flag"] == 1
    assert features["offensive_rating_home"] == 112.0
    assert features["defensive_rating_home"] == 108.0
    assert features["back_to_back_home"] == 0  # 2 rest days, not B2B
    assert features["back_to_back_away"] == 1  # 1 rest day = B2B


def test_feature_order_matches_extract_features():
    """FEATURE_ORDER must contain exactly the keys extract_features returns."""
    game = _make_game()
    features = extract_features(game)
    assert set(FEATURE_ORDER) == set(features.keys())


def test_features_to_array_length():
    game = _make_game()
    features = extract_features(game)
    arr = features_to_array(features)
    assert len(arr) == len(FEATURE_ORDER)
    assert len(arr) == len(features)


def test_features_to_array_order():
    """Array should follow FEATURE_ORDER."""
    game = _make_game()
    features = extract_features(game)
    arr = features_to_array(features)
    for i, key in enumerate(FEATURE_ORDER):
        assert arr[i] == features[key], f"Mismatch at index {i} ({key})"

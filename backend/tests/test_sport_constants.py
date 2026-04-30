from backend.analysis.sport_constants import (
    get_point_diff_std, get_total_points_std, get_home_advantage_elo, get_home_win_rate,
)

def test_nba_defaults():
    assert get_point_diff_std("nba") == 12.0
    assert get_total_points_std("nba") == 15.0
    assert get_home_advantage_elo("nba") == 100
    assert get_home_win_rate("nba") == 0.60

def test_nfl_values():
    assert get_point_diff_std("nfl") == 13.5
    assert get_total_points_std("nfl") == 13.0
    assert get_home_advantage_elo("nfl") == 48

def test_ncaab_values():
    assert get_point_diff_std("ncaab") == 11.0
    assert get_home_advantage_elo("ncaab") == 120
    assert get_home_win_rate("ncaab") == 0.67

def test_unknown_sport_returns_default():
    assert get_point_diff_std("curling") == 12.0
    assert get_home_win_rate("curling") == 0.57

def test_mlb_constants_have_baseball_calibrated_values():
    """MLB scoring is much lower-variance than other sports — constants must reflect that."""
    from backend.analysis.sport_constants import (
        get_point_diff_std, get_total_points_std,
        get_home_advantage_elo, get_home_win_rate,
    )
    # Run differential SD: empirically ~3.0 for MLB vs ~12 for NBA.
    assert get_point_diff_std("mlb") == 3.5
    # Total runs SD: empirically ~4.0.
    assert get_total_points_std("mlb") == 4.0
    # MLB has the smallest HCA in major US sports.
    assert get_home_advantage_elo("mlb") == 24
    assert get_home_win_rate("mlb") == 0.54

from backend.analysis.combat_elo import actual_score, elo_delta


def test_equal_ratings_win_moves_half_k():
    assert elo_delta(1500, 1500, 1.0, 24) == 12.0
    assert elo_delta(1500, 1500, 0.5, 24) == 0.0


def test_upset_moves_more_than_expected_win():
    assert elo_delta(1400, 1600, 1.0, 24) > elo_delta(1600, 1400, 1.0, 24) > 0


def test_actual_score():
    assert (actual_score(1, 0), actual_score(0, 1), actual_score(1, 1)) == (1.0, 0.0, 0.5)


def test_the_400_point_scale():
    # A 200-point favourite is expected to win 1/(1+10^-0.5) = 0.7597.
    assert round(elo_delta(1600, 1400, 1.0, 24), 4) == 5.7661

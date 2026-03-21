from backend.analysis.elo import EloSystem

def test_initial_rating():
    elo = EloSystem(k_factor=20)
    assert elo.get_rating("BOS") == 1500.0

def test_winner_gains_rating():
    elo = EloSystem(k_factor=20)
    elo.update("BOS", "LAL", winner="BOS")
    assert elo.get_rating("BOS") > 1500.0
    assert elo.get_rating("LAL") < 1500.0

def test_ratings_are_zero_sum():
    elo = EloSystem(k_factor=20)
    elo.update("BOS", "LAL", winner="BOS")
    total = elo.get_rating("BOS") + elo.get_rating("LAL")
    assert abs(total - 3000.0) < 0.01

def test_expected_score():
    elo = EloSystem(k_factor=20)
    expected = elo.expected_score(1600, 1400)
    assert 0.75 < expected < 0.77

def test_upset_causes_larger_shift():
    elo = EloSystem(k_factor=20)
    elo.ratings["BOS"] = 1700
    elo.ratings["LAL"] = 1300
    rating_before = elo.get_rating("LAL")
    elo.update("BOS", "LAL", winner="LAL")
    gain = elo.get_rating("LAL") - rating_before
    assert gain > 15

def test_blowout_win_moves_rating_more_than_close_win():
    from backend.analysis.elo import EloSystem
    elo1 = EloSystem(k_factor=20)
    elo1.update("A", "B", "A", margin=1)
    a_close = elo1.get_rating("A")

    elo2 = EloSystem(k_factor=20)
    elo2.update("A", "B", "A", margin=25)
    a_blowout = elo2.get_rating("A")

    assert a_blowout > a_close, "Blowout should produce larger rating change"

def test_home_advantage_boosts_expected():
    from backend.analysis.elo import EloSystem
    elo = EloSystem(k_factor=20, home_advantage=100)
    expected_home = elo.expected_score(1500, 1500, home_advantage=100)
    expected_neutral = elo.expected_score(1500, 1500, home_advantage=0)
    assert expected_home > expected_neutral
    assert expected_home > 0.5

def test_mov_autocorrelation_dampens_expected_blowouts():
    from backend.analysis.elo import EloSystem
    elo = EloSystem(k_factor=20)
    elo.ratings["FAV"] = 1700
    elo.ratings["DOG"] = 1300
    elo.update("FAV", "DOG", "FAV", margin=30)
    change = elo.get_rating("FAV") - 1700
    assert change < 6, f"Expected dampened change for heavy favorite blowout, got {change}"

def test_backward_compat_no_margin():
    from backend.analysis.elo import EloSystem
    elo = EloSystem()
    elo.update("A", "B", "A")
    assert elo.get_rating("A") > 1500
    assert elo.get_rating("B") < 1500

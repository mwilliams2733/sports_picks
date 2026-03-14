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

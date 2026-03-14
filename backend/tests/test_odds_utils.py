from backend.analysis.odds_utils import american_to_implied_prob, calculate_payout

def test_negative_odds_implied_prob():
    prob = american_to_implied_prob(-150)
    assert abs(prob - 0.6) < 0.01

def test_positive_odds_implied_prob():
    prob = american_to_implied_prob(130)
    assert abs(prob - 0.4348) < 0.01

def test_payout_negative_odds():
    payout = calculate_payout(-150)
    assert abs(payout - 0.6667) < 0.01

def test_payout_positive_odds():
    payout = calculate_payout(130)
    assert abs(payout - 1.3) < 0.01

from backend.analysis.odds_utils import (
    american_to_implied_prob,
    calculate_payout,
    remove_vig,
    no_vig_implied_prob,
)

def test_american_to_implied_prob_favorite():
    assert abs(american_to_implied_prob(-150) - 0.6) < 0.001

def test_american_to_implied_prob_underdog():
    assert abs(american_to_implied_prob(150) - 0.4) < 0.001

def test_calculate_payout_favorite():
    assert abs(calculate_payout(-150) - 0.6667) < 0.001

def test_calculate_payout_underdog():
    assert abs(calculate_payout(150) - 1.5) < 0.001

def test_remove_vig_standard_line():
    home_raw = american_to_implied_prob(-110)
    away_raw = american_to_implied_prob(-110)
    home_fair, away_fair = remove_vig(home_raw, away_raw)
    assert abs(home_fair - 0.5) < 0.001
    assert abs(away_fair - 0.5) < 0.001
    assert abs(home_fair + away_fair - 1.0) < 0.001

def test_remove_vig_heavy_favorite():
    home_raw = american_to_implied_prob(-300)
    away_raw = american_to_implied_prob(250)
    home_fair, away_fair = remove_vig(home_raw, away_raw)
    assert home_fair + away_fair < 1.001
    assert home_fair + away_fair > 0.999
    assert home_fair > 0.7
    assert home_fair < 0.75

def test_no_vig_implied_prob_home():
    prob = no_vig_implied_prob(side="home", home_odds=-150, away_odds=130)
    assert 0.0 < prob < 1.0
    assert abs(prob - 0.5798) < 0.01

def test_no_vig_implied_prob_away():
    prob = no_vig_implied_prob(side="away", home_odds=-150, away_odds=130)
    assert abs(prob - 0.4202) < 0.01

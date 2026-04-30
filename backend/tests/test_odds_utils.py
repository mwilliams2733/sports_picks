from backend.analysis.odds_utils import (
    american_to_implied_prob,
    calculate_payout,
    remove_vig,
    no_vig_implied_prob,
    parse_pick_line,
    signed_line_clv,
    compute_pick_clv,
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


def test_parse_pick_line_handles_spread_and_total():
    assert parse_pick_line("HOME -3.5") == -3.5
    assert parse_pick_line("AWAY +4.0") == 4.0
    assert parse_pick_line("Over 218.5") == 218.5
    assert parse_pick_line("Under 220") == 220.0
    assert parse_pick_line("HOME ML") is None


def test_signed_line_clv_spread_home_better_line():
    """Bet HOME -3.0, closed HOME -3.5 -> took easier line, +0.5 CLV."""
    assert signed_line_clv("spread", "HOME -3.0", -3.5) == 0.5


def test_signed_line_clv_spread_away_better_line():
    """Bet AWAY +3.0, closed AWAY +2.5 -> got more points, +0.5 CLV."""
    assert signed_line_clv("spread", "AWAY +3.0", 2.5) == 0.5


def test_signed_line_clv_over_better_line():
    """Bet Over 218.0, closed 219.5 -> we got the lower number, +1.5 CLV."""
    assert signed_line_clv("over_under", "Over 218.0", 219.5) == 1.5


def test_signed_line_clv_under_better_line():
    """Bet Under 220.0, closed 218.5 -> we got the higher number, +1.5 CLV."""
    assert signed_line_clv("over_under", "Under 220.0", 218.5) == 1.5


def test_compute_pick_clv_moneyline_returns_pct_only():
    pct, pts = compute_pick_clv("moneyline", "HOME ML", -150, -160, None)
    assert pts is None
    # -150 -> 60%, -160 -> 61.5%, delta = +1.5pp
    assert abs(pct - 1.538) < 0.01


def test_compute_pick_clv_spread_returns_points_only():
    pct, pts = compute_pick_clv("spread", "HOME -3.0", -110, -110, -3.5)
    assert pct is None
    assert pts == 0.5


def test_compute_pick_clv_handles_missing_data():
    assert compute_pick_clv("moneyline", "HOME ML", None, -160, None) == (None, None)
    assert compute_pick_clv("moneyline", "HOME ML", -150, None, None) == (None, None)
    assert compute_pick_clv("spread", "HOME -3.0", -110, None, None) == (None, None)
    assert compute_pick_clv("prop", "Player Over 25.5", -110, -110, None) == (None, None)

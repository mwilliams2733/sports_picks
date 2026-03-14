from backend.pipeline.grader import grade_pick
from backend.analysis.odds_utils import calculate_payout

def test_grade_moneyline_home_win():
    result, payout = grade_pick("moneyline", "HOME ML", 110, 100, -150)
    assert result == "win"
    assert abs(payout - calculate_payout(-150)) < 0.01

def test_grade_moneyline_away_win():
    result, payout = grade_pick("moneyline", "AWAY ML", 100, 110, 130)
    assert result == "win"

def test_grade_moneyline_loss():
    result, payout = grade_pick("moneyline", "HOME ML", 95, 105, -150)
    assert result == "loss"
    assert payout == -1.0

def test_grade_spread_cover():
    result, payout = grade_pick("spread", "HOME -4.5", 110, 100, -110)
    assert result == "win"

def test_grade_spread_no_cover():
    result, payout = grade_pick("spread", "HOME -4.5", 103, 100, -110)
    assert result == "loss"

def test_grade_over_hit():
    result, payout = grade_pick("over_under", "Over 218.5", 115, 110, -110)
    assert result == "win"

def test_grade_under_hit():
    result, payout = grade_pick("over_under", "Under 218.5", 100, 105, -110)
    assert result == "win"

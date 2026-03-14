from backend.analysis.confidence import calculate_confidence

def test_five_stars(): assert calculate_confidence(edge_pct=15.0, models_agreeing=3) == 5
def test_four_stars(): assert calculate_confidence(edge_pct=9.0, models_agreeing=2) == 4
def test_three_stars(): assert calculate_confidence(edge_pct=6.0, models_agreeing=2) == 3
def test_two_stars(): assert calculate_confidence(edge_pct=5.5, models_agreeing=1) == 2
def test_one_star(): assert calculate_confidence(edge_pct=3.5, models_agreeing=1) == 1
def test_zero_below_threshold(): assert calculate_confidence(edge_pct=2.0, models_agreeing=1) == 0

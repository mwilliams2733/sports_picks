from backend.analysis.prop_confidence import calculate_prop_confidence

def test_5_star():
    assert calculate_prop_confidence(20.0) == 5

def test_4_star():
    assert calculate_prop_confidence(15.0) == 4

def test_3_star():
    assert calculate_prop_confidence(10.0) == 3

def test_2_star():
    assert calculate_prop_confidence(7.0) == 2

def test_1_star():
    assert calculate_prop_confidence(5.0) == 1

def test_0_star_below_threshold():
    assert calculate_prop_confidence(4.9) == 0

def test_boundary_values():
    assert calculate_prop_confidence(19.9) == 4
    assert calculate_prop_confidence(14.9) == 3
    assert calculate_prop_confidence(9.9) == 2
    assert calculate_prop_confidence(6.9) == 1

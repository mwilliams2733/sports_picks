"""Tests for DB-driven confidence thresholds."""
from backend.analysis.confidence import calculate_confidence, DEFAULT_THRESHOLDS
from backend.analysis.prop_confidence import calculate_prop_confidence, DEFAULT_PROP_THRESHOLDS


def test_default_thresholds():
    assert DEFAULT_THRESHOLDS == {5: 12.0, 4: 8.0, 3: 5.0, 2: 5.0, 1: 3.0}


def test_default_prop_thresholds():
    assert DEFAULT_PROP_THRESHOLDS == {5: 20.0, 4: 15.0, 3: 10.0, 2: 7.0, 1: 5.0}


def test_calculate_confidence_uses_defaults():
    assert calculate_confidence(12.0, 3) == 5
    assert calculate_confidence(8.0, 2) == 4
    assert calculate_confidence(5.0, 2) == 3
    assert calculate_confidence(5.0, 1) == 2
    assert calculate_confidence(3.0, 1) == 1
    assert calculate_confidence(2.0, 1) == 0


def test_calculate_confidence_with_custom_thresholds():
    custom = {5: 15.0, 4: 10.0, 3: 7.0, 2: 5.0, 1: 4.0}
    assert calculate_confidence(12.0, 3, thresholds=custom) == 4  # was 5, now needs 15%
    assert calculate_confidence(15.0, 3, thresholds=custom) == 5


def test_calculate_prop_confidence_uses_defaults():
    assert calculate_prop_confidence(20.0) == 5
    assert calculate_prop_confidence(15.0) == 4
    assert calculate_prop_confidence(10.0) == 3
    assert calculate_prop_confidence(7.0) == 2
    assert calculate_prop_confidence(5.0) == 1
    assert calculate_prop_confidence(4.0) == 0


def test_calculate_prop_confidence_with_custom_thresholds():
    custom = {5: 25.0, 4: 18.0, 3: 12.0, 2: 8.0, 1: 6.0}
    assert calculate_prop_confidence(20.0, thresholds=custom) == 4
    assert calculate_prop_confidence(25.0, thresholds=custom) == 5

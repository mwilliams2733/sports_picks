"""Tests for dynamic Kelly sizing."""
from backend.analysis.kelly import (
    fractional_kelly,
    adaptive_fraction,
    apply_drawdown_protection,
    apply_correlation_discount,
)


def test_fractional_kelly_basic():
    result = fractional_kelly(0.60, -150, 0.25)
    assert 0.5 <= result <= 3.0


def test_fractional_kelly_no_edge():
    result = fractional_kelly(0.40, -150, 0.25)
    assert result == 0.5


def test_adaptive_fraction_well_calibrated():
    frac = adaptive_fraction(calibration_deviation=0.02)
    assert frac == 0.35


def test_adaptive_fraction_poorly_calibrated():
    frac = adaptive_fraction(calibration_deviation=0.06)
    assert frac == 0.15


def test_adaptive_fraction_moderate():
    frac = adaptive_fraction(calibration_deviation=0.04)
    assert frac == 0.25


def test_drawdown_protection_no_drawdown():
    frac = apply_drawdown_protection(
        base_fraction=0.25, current_balance=100000, peak_balance=100000,
    )
    assert frac == 0.25


def test_drawdown_protection_severe_drawdown():
    frac = apply_drawdown_protection(
        base_fraction=0.25, current_balance=80000, peak_balance=100000,
    )
    assert frac == 0.125


def test_drawdown_protection_mild_drawdown():
    frac = apply_drawdown_protection(
        base_fraction=0.25, current_balance=90000, peak_balance=100000,
    )
    assert frac == 0.25


def test_correlation_discount():
    frac = apply_correlation_discount(base_fraction=0.25, same_game_picks=2)
    assert abs(frac - 0.20) < 0.001


def test_correlation_discount_single_pick():
    frac = apply_correlation_discount(base_fraction=0.25, same_game_picks=1)
    assert frac == 0.25

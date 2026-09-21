"""The three Kelly adjusters, composed into one fraction.

`adaptive_fraction`, `apply_drawdown_protection` and
`apply_correlation_discount` had **zero production call sites** — only
tests — while the module docstring advertised all three. `fractional_kelly`
was the only one `ensemble` and `recent_form` ever called.

`sizing_fraction` is the single place they compose, so callers cannot apply
two of three and silently skip the last.

Order matters and is fixed
--------------------------
1. **adaptive_fraction REPLACES the base.** It returns an absolute fraction
   (0.35 / 0.25 / 0.15), not a multiplier, so applying it after a discount
   would throw the discount away.
2. **drawdown halves what survives**, because it is a statement about the
   bankroll, not about this bet.
3. **correlation discounts last**, because it is the only per-game term.

Absent input is not a neutral input
-----------------------------------
Each adjuster is skipped when its input is unavailable, and the base value
carries through. A missing calibration sample is not "deviation = 0", and a
bankroll with no peak is not "drawdown = 0" — asserting either would invent
a measurement. The same reasoning as `calculate_confidence`, which treats an
absent signal as absent evidence rather than a vote.
"""
import pytest

from backend.analysis.kelly import (DRAWDOWN_THRESHOLD, adaptive_fraction,
                                    sizing_fraction)


def test_with_no_inputs_the_base_fraction_survives_untouched():
    assert sizing_fraction(0.25) == 0.25


def test_calibration_replaces_the_base_rather_than_scaling_it():
    """adaptive_fraction returns an absolute fraction. A well-calibrated
    model is sized at 0.35 whatever the config said."""
    assert sizing_fraction(0.25, calibration_deviation=0.02) == 0.35
    assert sizing_fraction(0.25, calibration_deviation=0.06) == 0.15


def test_an_absent_calibration_sample_is_not_a_deviation_of_zero():
    """None means "we did not measure", which must not be read as "perfectly
    calibrated" — that would silently raise every stake to 0.35."""
    assert sizing_fraction(0.25, calibration_deviation=None) == 0.25
    assert adaptive_fraction(0.0) == 0.35, "0.0 really does mean well calibrated"


def test_a_drawdown_past_the_threshold_halves_the_fraction():
    result = sizing_fraction(0.25, current_balance=80.0, peak_balance=100.0)

    assert result == pytest.approx(0.125)


def test_a_drawdown_short_of_the_threshold_changes_nothing():
    result = sizing_fraction(0.25, current_balance=90.0, peak_balance=100.0)

    assert result == 0.25
    assert (100.0 - 90.0) / 100.0 < DRAWDOWN_THRESHOLD


def test_a_bankroll_with_no_peak_is_skipped_not_divided_by_zero():
    assert sizing_fraction(0.25, current_balance=0.0, peak_balance=0.0) == 0.25


def test_a_second_pick_on_the_same_game_is_discounted():
    assert sizing_fraction(0.25, same_game_picks=2) == pytest.approx(0.20)


def test_a_single_pick_on_a_game_is_not_discounted():
    assert sizing_fraction(0.25, same_game_picks=1) == 0.25


def test_the_three_compose_in_a_fixed_order():
    """Calibration sets 0.35, drawdown halves it to 0.175, correlation takes
    20% off to 0.14. Applying calibration last would discard both."""
    result = sizing_fraction(
        0.25, calibration_deviation=0.02,
        current_balance=80.0, peak_balance=100.0, same_game_picks=3)

    assert result == pytest.approx(0.35 / 2 * 0.8)


def test_the_result_is_never_negative_or_absurd():
    result = sizing_fraction(0.25, calibration_deviation=0.99,
                             current_balance=1.0, peak_balance=1000.0,
                             same_game_picks=9)

    assert 0.0 < result <= 0.35

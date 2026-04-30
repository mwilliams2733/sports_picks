"""Pitcher skill score: reduces ERA + K/9 to a [0, 1] score.

Calibration: league-average ERA ~4.00 -> 0.50; ace ERA ~2.50 -> ~0.85;
replacement ERA ~5.50 -> ~0.20. K/9 contributes a smaller bump.
"""
from backend.analysis.pitcher import pitcher_skill_score


def test_average_pitcher_scores_around_half():
    s = pitcher_skill_score(era=4.00, k9=8.5)  # ~league average
    assert 0.45 <= s <= 0.55


def test_ace_pitcher_scores_high():
    s = pitcher_skill_score(era=2.40, k9=11.5)  # Cy Young calibre
    assert s >= 0.80


def test_replacement_level_scores_low():
    s = pitcher_skill_score(era=5.80, k9=6.5)
    assert s <= 0.25


def test_score_is_clamped_to_unit_interval():
    """Extreme inputs must not produce <0 or >1."""
    very_low = pitcher_skill_score(era=0.10, k9=20.0)
    very_high = pitcher_skill_score(era=15.0, k9=2.0)
    assert 0.0 <= very_low <= 1.0
    assert 0.0 <= very_high <= 1.0


def test_missing_inputs_return_neutral():
    """If we have no pitcher data, skill = 0.5 (no edge either direction)."""
    assert pitcher_skill_score(era=None, k9=None) == 0.5

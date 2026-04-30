"""Reduce a pitcher's recent rolling stats to a [0, 1] skill score.

Used by the MLB branch of SportSpecificStrategy as the dominant signal —
in MLB, the starting pitcher is the single biggest game-to-game variable.

Calibration (rough, league-relative):
  ERA 2.50 -> ~0.85   (ace)
  ERA 4.00 -> ~0.50   (league average)
  ERA 5.50 -> ~0.20   (replacement-level)
"""
from __future__ import annotations
import math


# League-average anchors. Centered so that ERA=4.00 + K/9=8.5 yields exactly 0.5.
_LEAGUE_ERA = 4.00
_LEAGUE_K9 = 8.5

# Sigmoid scale parameters: smaller scale = sharper slope around the anchor.
_ERA_SCALE = 0.9  # ERA contributes the bulk of the signal
_K9_SCALE = 4.0   # K/9 is a tie-breaker / strikeout-stuff bump

# Weights sum to 1.0
_W_ERA = 0.75
_W_K9 = 0.25


def _sigmoid(x: float) -> float:
    # Stable for both signs.
    if x >= 0:
        return 1.0 / (1.0 + math.exp(-x))
    e = math.exp(x)
    return e / (1.0 + e)


def pitcher_skill_score(era: float | None, k9: float | None) -> float:
    """Return a skill score in [0, 1] where 0.5 is league-average.

    Lower ERA -> higher score; higher K/9 -> higher score.
    Missing inputs -> 0.5 (neutral) so MLB picks still generate when pitchers
    haven't been announced.
    """
    if era is None and k9 is None:
        return 0.5
    era_term = _sigmoid((_LEAGUE_ERA - (era if era is not None else _LEAGUE_ERA)) / _ERA_SCALE)
    k9_term = _sigmoid(((k9 if k9 is not None else _LEAGUE_K9) - _LEAGUE_K9) / _K9_SCALE)
    score = _W_ERA * era_term + _W_K9 * k9_term
    return max(0.0, min(1.0, score))

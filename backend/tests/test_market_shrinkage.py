"""How much of the model's disagreement with the price is worth keeping.

The ensemble prices a game from team features and never reads the market.
Its edge is therefore `model - implied`, and every pick is a bet that the
model knows something the price does not.

Measured on 2026-09-21 that bet loses. Blending

    p_final = lam * p_model + (1 - lam) * p_market_devigged

and scoring Brier across every final game that has both, the curve rises
monotonically in lam: 0.1725 at lam=0 against 0.2131 at lam=1. Fitted
out-of-sample on a time split -- trained on nba/ncaab/mlb through May,
scored on ncaaf/mlb/nfl from June -- lam came out at **0.00**, with eval
Brier 0.1516 against 0.2125 for the model alone.

That is why post-hoc calibration did not help. A Platt fit on 877 nba games
moved Brier by 0.0008 (a=1.038), because the reliability error is not
monotone: gaps run +0.124, -0.004, -0.189, -0.079, -0.131, +0.027 across
the bins, changing sign twice. No monotone reshaping of the probability
axis can repair that, and both Platt and isotonic are monotone.

This module measures lam. It does not apply it: lam = 0 means the model
never disagrees with the price, so no pick clears any edge threshold and
the system stops producing picks entirely. That is a decision about what
the product does, not a calibration detail.

Sample sizes are small and mostly single-window per sport, so lam is
reported with its n and its date span, never bare.
"""
import datetime

import pytest

from backend.analysis.market_shrinkage import (MIN_SHRINKAGE_GAMES, Blend,
                                               best_lambda, blended_brier)


def _rows(pairs, sport="nba", day=datetime.date(2026, 5, 1)):
    """(model prob, market prob, outcome) triples."""
    return [Blend(sport=sport, date=day, model=m, market=k, outcome=o)
            for m, k, o in pairs]


def test_a_perfect_model_and_a_useless_market_gives_lambda_one():
    """The control: when the model is right and the price is not, the fit
    must keep the model. A fitter that always returns 0 would 'confirm' the
    live finding without measuring anything."""
    rows = _rows([(1.0, 0.5, 1), (0.0, 0.5, 0)] * MIN_SHRINKAGE_GAMES)

    assert best_lambda(rows) == pytest.approx(1.0)


def test_a_useless_model_and_a_perfect_market_gives_lambda_zero():
    rows = _rows([(0.5, 1.0, 1), (0.5, 0.0, 0)] * MIN_SHRINKAGE_GAMES)

    assert best_lambda(rows) == pytest.approx(0.0)


def test_two_sources_right_about_different_games_land_between():
    """Neither source dominating is the interesting case, and a fitter that
    only ever returns an endpoint would pass both tests above.

    The errors have to be UNCORRELATED for a blend to beat both ends. Here
    the model is confident and right on the first game while the market is
    flat, and the reverse on the second, which puts the optimum at 0.5.
    """
    rows = _rows([(0.9, 0.5, 1), (0.5, 0.9, 1)] * MIN_SHRINKAGE_GAMES)

    lam = best_lambda(rows)

    assert lam == pytest.approx(0.5, abs=0.01)
    assert blended_brier(rows, lam) < blended_brier(rows, 0.0)
    assert blended_brier(rows, lam) < blended_brier(rows, 1.0)


def test_brier_is_computed_on_the_blend_not_on_either_source():
    rows = _rows([(1.0, 0.0, 1)])

    assert blended_brier(rows, 1.0) == pytest.approx(0.0)
    assert blended_brier(rows, 0.0) == pytest.approx(1.0)
    assert blended_brier(rows, 0.5) == pytest.approx(0.25)


def test_too_few_games_refuses_rather_than_returning_a_number():
    """A lambda from 12 games looks exactly like a lambda from 1,200. The
    caller has to be told it is not measurable yet."""
    rows = _rows([(0.6, 0.5, 1)] * 5)

    with pytest.raises(ValueError, match="at least"):
        best_lambda(rows)


def test_a_push_or_tie_has_no_outcome_to_score(monkeypatch):
    """Outcomes are binary. A drawn game is excluded upstream; if one
    arrives, it must not be silently scored as a loss."""
    with pytest.raises(ValueError, match="outcome"):
        best_lambda(_rows([(0.6, 0.5, 2)] * MIN_SHRINKAGE_GAMES))


def test_the_search_resolves_a_lambda_a_coarse_grid_would_round_away():
    """The live answer is 0.03. A 0.1 grid rounds that to 0.00 and loses the
    difference between "the model adds a little" and "the model adds
    nothing" -- which is the entire question this module exists to answer.

    Brier over these rows minimises at lambda = -sum(e*d) / sum(d^2), with
    d = model - market and e = market - outcome. The pair below is
    constructed so that works out to exactly 0.03.
    """
    rows = _rows([(0.2, 0.1, 0), (0.994, 0.894, 1)] * MIN_SHRINKAGE_GAMES)

    lam = best_lambda(rows)

    assert lam == pytest.approx(0.03, abs=0.005), lam
    assert lam != 0.0, "a coarse grid would collapse this to 'model adds nothing'"

"""The statistics the EPA experiment reaches its verdict with.

The experiment answers one question -- does an EPA rating know anything the
closing spread does not -- so the arithmetic that produces that verdict has
to be right for the right reason. Two traps are pinned here specifically:

* the LINE's own margin forecast is `-spread_home`, not `spread_home`.
  Unflipped, every comparison runs backwards and the experiment reports the
  market as anti-predictive.
* a push is not a loss. Counting pushes as losses understates a break-even
  strategy by roughly two points of win rate at NFL push frequencies.
"""
import pytest

from backend.scripts.epa_experiment import (BREAK_EVEN, Outcome, ats_record,
                                            incremental_information,
                                            line_margin, rmse)


def _o(line_pred, model_pred, margin):
    return Outcome(line_pred=line_pred, model_pred=model_pred, margin=margin)


# --- sign conventions -----------------------------------------------------

def test_a_home_favourite_is_a_positive_line_margin():
    """A 3.5-point home favourite is quoted spread_home = -3.5 and the line
    is forecasting the home side to win by 3.5."""
    assert line_margin(-3.5) == pytest.approx(3.5)


def test_a_home_underdog_is_a_negative_line_margin():
    assert line_margin(6.0) == pytest.approx(-6.0)


# --- error --------------------------------------------------------------

def test_rmse_is_zero_for_a_perfect_forecast():
    assert rmse([(3.0, 3.0), (-7.0, -7.0)]) == pytest.approx(0.0)


def test_rmse_penalises_a_big_miss_more_than_two_small_ones():
    """Squared error is the point: it is why a margin model is scored this
    way rather than on mean absolute error."""
    assert rmse([(0.0, 10.0), (0.0, 0.0)]) > rmse([(0.0, 5.0), (0.0, 5.0)])


def test_rmse_needs_at_least_one_pair():
    with pytest.raises(ValueError):
        rmse([])


# --- does the model add anything to the line? -----------------------------

def test_a_model_that_is_pure_noise_gets_no_weight():
    """margin is built from the line alone, so the model coefficient must
    come out near zero however wild the model's own numbers are."""
    import random
    rng = random.Random(0)
    rows = []
    for _ in range(400):
        line = rng.uniform(-14, 14)
        rows.append(_o(line, rng.uniform(-14, 14), line + rng.gauss(0, 13)))

    result = incremental_information(rows)

    assert abs(result.model_coef) < 0.15, result
    assert result.model_p > 0.05, result


def test_a_model_carrying_real_signal_gets_weight():
    """The other half. Without it, a function returning 0.0 unconditionally
    would pass the test above."""
    import random
    rng = random.Random(1)
    rows = []
    for _ in range(400):
        line = rng.uniform(-14, 14)
        secret = rng.gauss(0, 6)          # something the line cannot see
        rows.append(_o(line, line + secret, line + secret + rng.gauss(0, 3)))

    result = incremental_information(rows)

    assert result.model_coef > 0.5, result
    assert result.model_p < 0.01, result


def test_the_line_coefficient_is_reported_too():
    """A line coefficient far from 1.0 means the market itself is biased on
    this sample, which changes how the model's coefficient reads."""
    import random
    rng = random.Random(2)
    rows = [_o(l := rng.uniform(-14, 14), rng.uniform(-14, 14),
               l + rng.gauss(0, 12)) for _ in range(400)]

    assert incremental_information(rows).line_coef == pytest.approx(1.0, abs=0.2)


def test_the_fit_refuses_a_sample_too_small_to_mean_anything():
    with pytest.raises(ValueError):
        incremental_information([_o(1.0, 2.0, 3.0)])


# --- betting the disagreement ---------------------------------------------

def test_a_home_bet_wins_when_the_home_side_beats_the_number():
    """Model says home by 10, line says home by 3, home wins by 7."""
    rec = ats_record([_o(3.0, 10.0, 7.0)], threshold=1.0)

    assert (rec.won, rec.lost, rec.pushed) == (1, 0, 0)


def test_a_home_bet_loses_when_the_home_side_falls_short():
    rec = ats_record([_o(3.0, 10.0, 1.0)], threshold=1.0)

    assert (rec.won, rec.lost, rec.pushed) == (0, 1, 0)


def test_an_away_bet_wins_when_the_home_side_falls_short():
    """The away branch is reached only when the model likes the road side,
    and could skip its comparison entirely without any home test noticing."""
    rec = ats_record([_o(3.0, -4.0, 1.0)], threshold=1.0)

    assert (rec.won, rec.lost, rec.pushed) == (1, 0, 0)


def test_an_away_bet_loses_when_the_home_side_covers():
    rec = ats_record([_o(3.0, -4.0, 7.0)], threshold=1.0)

    assert (rec.won, rec.lost, rec.pushed) == (0, 1, 0)


def test_a_push_is_not_a_loss():
    """Landing exactly on the number returns the stake. Counting it as a
    loss understates a break-even strategy."""
    rec = ats_record([_o(3.0, 10.0, 3.0)], threshold=1.0)

    assert (rec.won, rec.lost, rec.pushed) == (0, 0, 1)


def test_a_game_inside_the_threshold_is_not_bet():
    rec = ats_record([_o(3.0, 3.5, 7.0)], threshold=1.0)

    assert (rec.won, rec.lost, rec.pushed, rec.bet) == (0, 0, 0, 0)


def test_a_bigger_threshold_bets_fewer_games():
    rows = [_o(3.0, 3.0 + d, 7.0) for d in (0.5, 2.0, 5.0)]

    assert ats_record(rows, threshold=1.0).bet == 2
    assert ats_record(rows, threshold=3.0).bet == 1


def test_the_break_even_rate_excludes_pushes():
    """52.38% is the -110 break-even on DECIDED bets. A push is neither."""
    rec = ats_record([_o(3.0, 10.0, 7.0), _o(3.0, 10.0, 1.0),
                      _o(3.0, 10.0, 3.0)], threshold=1.0)

    assert rec.bet == 3 and rec.decided == 2
    assert rec.win_rate == pytest.approx(0.5)


def test_a_record_with_no_decided_bets_has_no_win_rate():
    """Reporting 0.0 would read as a catastrophic strategy rather than an
    absent measurement."""
    assert ats_record([_o(3.0, 3.5, 7.0)], threshold=1.0).win_rate is None


def test_the_break_even_rate_is_the_minus_110_one():
    assert BREAK_EVEN == pytest.approx(110.0 / 210.0)


def test_a_rate_that_beats_a_coin_flip_can_still_lose_money():
    """The reason the null is 52.38% and not 50%.

    51.5% over 5,000 bets is significant against a coin flip (p = 0.018) and
    loses 84 units. Scored against the right null it is p = 0.896 -- no
    evidence of an edge, which is the true reading.

    The sample is 5,000 because the two nulls have to be far enough apart to
    tell apart: at n=100 a 53% record gives 0.31 against a coin flip and 0.49
    against break-even, and an earlier version of this test passed with
    BREAK_EVEN set to 0.5.
    """
    rec = ats_record([_o(3.0, 10.0, 7.0)] * 2575 + [_o(3.0, 10.0, 1.0)] * 2425,
                     threshold=1.0)

    assert rec.win_rate == pytest.approx(0.515)
    assert rec.units < 0, "51.5% at -110 loses money"
    assert rec.p_value_vs_breakeven > 0.5, rec

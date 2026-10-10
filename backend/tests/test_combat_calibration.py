from datetime import date

import numpy as np

from backend.analysis.combat_calibration import design, eligible, fit, probability
from backend.analysis.combat_history import BoutFeatures


def _f(elo_a, elo_b, outcome, form_a=0.5, form_b=0.5, n_a=3, n_b=3):
    return BoutFeatures(1, date(2020, 1, 1), 1, 2, elo_a, elo_b, form_a, form_b,
                        1500.0, 1500.0, n_a, n_b, outcome)


def test_probability_is_symmetric_in_the_corners():               # Review Focus 1
    coef = np.array([1.2, 0.8, 0.3, 0.1])
    f = _f(1600, 1500, 1.0, form_a=0.8, form_b=0.4)
    g = BoutFeatures(1, f.date, 2, 1, f.elo_b, f.elo_a, f.form_b, f.form_a,
                     f.quality_b, f.quality_a, f.fights_b, f.fights_a, 0.0)
    assert abs(probability(coef, f) + probability(coef, g) - 1.0) < 1e-12


def test_first_listed_always_winning_is_not_learned():             # Review Focus 1
    # The CSV's artifact in its purest form: the first-listed fighter ALWAYS
    # wins, and the features are symmetric (each pairing appears in both
    # orders). Any corner term would learn "first wins"; this model must not.
    rng = np.random.default_rng(0)
    data = []
    for _ in range(1000):
        ea, eb = 1500 + rng.normal(0, 80), 1500 + rng.normal(0, 80)
        data += [_f(ea, eb, 1.0), _f(eb, ea, 1.0)]
    coef = fit(data)
    assert abs(probability(coef, _f(1500, 1500, 1.0)) - 0.5) < 1e-9   # no intercept, mirrored
    assert np.all(np.abs(coef) < 1e-3)                                 # nothing to learn but the order


def test_a_real_rating_signal_is_recovered():
    rng = np.random.default_rng(1)
    data = []
    for _ in range(4000):
        ea, eb = 1500 + rng.normal(0, 120), 1500 + rng.normal(0, 120)
        p = 1 / (1 + 10 ** (-(ea - eb) / 400))
        data.append(_f(ea, eb, float(rng.random() < p)))
    coef = fit(data)
    assert coef[0] > 1.5                     # elo_diff/400 coefficient near ln(10)=2.30


def test_eligibility_matches_the_live_gate():
    assert eligible(_f(1500, 1500, 1.0)) and not eligible(_f(1500, 1500, 0.5))
    assert not eligible(_f(1500, 1500, 1.0, n_a=0))

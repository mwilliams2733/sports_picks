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


def test_market_report_scores_priced_eligible_bouts_and_counts_dog_picks(db_engine, db_session):
    from datetime import datetime
    from backend.analysis.combat_calibration import market_report
    from backend.analysis.odds_utils import american_to_implied_prob, remove_vig
    from backend.models import Base, Game, Odds, Team
    Base.metadata.create_all(db_engine)
    for tid in range(1, 5):
        db_session.add(Team(id=tid, name=f"F{tid}", abbreviation=f"F{tid}", sport="mma"))
    db_session.flush()
    # History so both fighters of bouts 3 and 4 have >= 1 earlier bout.
    for gid, d, h, a in [(1, 1, 1, 3), (2, 2, 2, 4)]:
        db_session.add(Game(id=gid, sport="mma", season="2026", date=date(2026, 1, d),
                            home_team_id=h, away_team_id=a, status="final", home_score=1, away_score=0))
    for gid, d, h, a in [(3, 10, 1, 2), (4, 11, 3, 4)]:
        db_session.add(Game(id=gid, sport="mma", season="2026", date=date(2026, 1, d),
                            home_team_id=h, away_team_id=a, status="final", home_score=1, away_score=0))
    db_session.flush()
    db_session.add_all([
        Odds(game_id=3, bookmaker="dk", moneyline_home=-200, moneyline_away=170,
             spread_home=0.0, spread_away=0.0, over_under=0.0, timestamp=datetime(2026, 1, 10)),
        Odds(game_id=4, bookmaker="dk", moneyline_home=150, moneyline_away=-180,
             spread_home=0.0, spread_away=0.0, over_under=0.0, timestamp=datetime(2026, 1, 11)),
    ])
    db_session.commit()
    coef = np.array([0.0, 0.0, 0.0, 0.0])         # model says 50% every time
    rep = market_report(db_session, coef, k=24, min_edge=5.0)
    assert rep["n"] == 2
    expected_home = remove_vig(american_to_implied_prob(-200), american_to_implied_prob(170))[0]
    assert abs(rep["market_probs"][0] - expected_home) < 1e-9
    # 50% vs a +170 dog and a +150 dog: both picks are the underdog.
    assert rep["simulated_picks"] == 2 and rep["underdog_share"] == 1.0

"""Partial-pool the per-sport home-advantage intercept.

`build_feature_row` gives each sport its own one-hot slot, because home
advantage has no other representation in the model -- there is no home
feature, so the intercept carries it. That fixed a real bug: one intercept
across a pool that is 91% nba scored ncaab as though it were nba.

It replaced it with a subtler one. Every slot is fitted freely, so the
coefficient for a sport with 31 training games is trusted exactly as much
as one with 1,253:

    nba    1,253 trainable games
    ncaaf    260
    mlb      132
    ncaab     85
    nfl       31

nfl's home advantage is a raw estimate from 31 games. It is what produced a
49% moneyline edge on 2026-09-20, and it is the reason `DEFAULT_MAX_EDGE`
exists as a blunt ceiling over the same problem.

The fix is the standard one: shrink each sport's coefficient toward the
pooled mean in proportion to how little data supports it.

    w_s      = n_s / (n_s + K)
    shrunk_s = mu + (coef_s - mu) * w_s

`mu` is the games-weighted mean of the sport coefficients. `K` is derived,
not chosen: with a binary outcome the sampling variance of a slot's
coefficient goes as `1 / (n * p(1-p))`, which is `4/n` at p = 0.5, so the
empirical-Bayes weight `tau^2 / (tau^2 + 4/n)` gives `K = 4 / tau^2` with
`tau^2` the between-sport variance actually observed.

Why not a GLMM
--------------
`statsmodels.BinomialBayesMixedGLM` fits this properly as a random
intercept, and is the textbook answer. It is not used because statsmodels
is not installed, and adding a production dependency -- plus swapping a
deterministic `LogisticRegression` for a variational-Bayes fit that can
fail to converge -- is not worth it for a change whose benefit is unproven.
A random intercept IS a shrunk fixed intercept; this computes the shrinkage
directly with numpy, deterministically, and can be unit-tested.
"""
import pytest

from backend.analysis.calibrated_model import shrink_sport_coefficients


def test_a_sport_with_far_more_data_barely_moves():
    """nba has 1,253 games. Its own estimate should stand."""
    shrunk = shrink_sport_coefficients({"nba": 1.0, "nfl": 3.0},
                                       {"nba": 1253, "nfl": 31})

    assert abs(shrunk["nba"] - 1.0) < 0.15


def test_a_sport_with_little_data_is_pulled_toward_the_pool():
    """nfl's 31 games cannot support an estimate three times the pool's."""
    shrunk = shrink_sport_coefficients({"nba": 1.0, "nfl": 3.0},
                                       {"nba": 1253, "nfl": 31})

    assert shrunk["nfl"] < 3.0
    assert shrunk["nfl"] > 1.0, "pulled toward the pool, not past it"


def test_shrinkage_is_monotone_in_sample_size():
    """A sport with more data must keep more of its own estimate."""
    coefs = {"a": 3.0, "b": 3.0, "big": 0.0}
    shrunk = shrink_sport_coefficients(coefs, {"a": 20, "b": 200, "big": 1000})

    assert shrunk["b"] > shrunk["a"], "more data means less shrinkage"


def test_no_coefficient_overshoots_the_pooled_mean():
    """Shrinkage moves an estimate toward the mean and stops there. An
    overshoot would invert the sport's effect, which is worse than the
    unpooled estimate it replaced."""
    coefs = {"hi": 2.0, "lo": -2.0, "mid": 0.0}
    counts = {"hi": 5, "lo": 5, "mid": 5000}
    shrunk = shrink_sport_coefficients(coefs, counts)

    for sport in coefs:
        lo, hi = sorted((coefs[sport], shrunk["mid"]))
        assert lo - 1e-9 <= shrunk[sport] <= hi + 1e-9, sport


def test_identical_coefficients_are_left_alone():
    """No between-sport variance means nothing to pool away from."""
    shrunk = shrink_sport_coefficients({"a": 1.5, "b": 1.5},
                                       {"a": 10, "b": 1000})

    assert shrunk == pytest.approx({"a": 1.5, "b": 1.5})


def test_a_single_sport_cannot_be_pooled():
    """With one group there is no pool to shrink toward, and shrinking to
    its own mean would be a no-op dressed up as a correction."""
    shrunk = shrink_sport_coefficients({"nba": 0.8}, {"nba": 1253})

    assert shrunk == pytest.approx({"nba": 0.8})


def test_a_sport_with_no_training_games_inherits_the_pool():
    """Better than the 0.0 an unfitted slot carries, which reads as "this
    sport has no home advantage" rather than "unknown".

    Its own coefficient is ignored entirely: with no games there is no
    evidence behind it, and letting it into the mean would let a slot
    fitted from nothing drag the value it is about to be shrunk toward.
    """
    shrunk = shrink_sport_coefficients({"nba": 1.0, "new": 0.0},
                                       {"nba": 1253, "new": 0})

    assert shrunk["new"] == pytest.approx(1.0), "the pool's home advantage"
    assert shrunk["nba"] == pytest.approx(1.0), "the only informed sport stands"


def test_an_empty_input_is_handled_rather_than_dividing_by_zero():
    assert shrink_sport_coefficients({}, {}) == {}


def test_the_weight_formula_is_the_documented_one():
    """K = 4 / tau^2 with tau^2 the games-weighted between-sport variance.
    Pinned so a future edit cannot silently swap in a hand-tuned constant.
    """
    # Two sports, coefficients +-1 about a weighted mean of 0, equal n.
    coefs = {"a": 1.0, "b": -1.0}
    counts = {"a": 100, "b": 100}
    shrunk = shrink_sport_coefficients(coefs, counts)

    tau2 = 1.0                      # weighted variance of (+1, -1) about 0
    k = 4.0 / tau2                  # = 4
    w = 100 / (100 + k)             # = 0.9615...
    assert shrunk["a"] == pytest.approx(0.0 + (1.0 - 0.0) * w)
    assert shrunk["b"] == pytest.approx(0.0 + (-1.0 - 0.0) * w)


# --- the values must land in the right coefficients -----------------------

def test_pooling_writes_to_the_sport_slots_and_not_the_legacy_features():
    """`SPORT_SLOT_OFFSET` is what keeps the shrunk values off the five
    leading difference features. Reading offset 0 instead would overwrite
    elo_diff and point_diff with home-advantage numbers, and every test
    above would still pass because they exercise the pure function only.
    """
    import datetime as dt

    from backend.analysis.calibrated_model import (SPORT_SLOT_OFFSET,
                                                   SPORT_VOCAB,
                                                   CalibratedModel)
    from backend.database import get_engine, get_session
    from backend.models import Base, Game, Team

    engine = get_engine(":memory:")
    Base.metadata.create_all(engine)
    session = get_session(engine)
    # Two sports with very different home rates and very different volumes,
    # so pooling has something to do.
    for sport, count, home_wins in (("nba", 260, 0.55), ("nfl", 20, 0.95)):
        for i in range(count):
            h = Team(name=f"{sport}H{i}", abbreviation="H", sport=sport)
            a = Team(name=f"{sport}A{i}", abbreviation="A", sport=sport)
            session.add_all([h, a])
            session.flush()
            home_won = (i / count) < home_wins
            session.add(Game(
                sport=sport, season="2026",
                date=dt.date(2026, 1, 1) + dt.timedelta(days=i),
                home_team_id=h.id, away_team_id=a.id, status="final",
                home_score=110 if home_won else 100,
                away_score=100 if home_won else 110))
    session.commit()

    model = CalibratedModel()
    model.train_from_db(session)
    session.close()

    assert model.trained
    coefs = model.model.coef_[0]
    pooled = model.sport_pooling

    # Every pooled value must be readable at its own slot index. Writing at
    # offset 0 instead puts home-advantage numbers over elo_diff and
    # point_diff, and leaves the real slots holding their raw fit.
    for i, sport in enumerate(SPORT_VOCAB):
        assert coefs[SPORT_SLOT_OFFSET + i] == pytest.approx(
            pooled["after"][sport]), sport

    # And pooling must actually have done something to the small sport.
    assert pooled["hosted_games"]["nfl"] == 20
    assert pooled["hosted_games"]["nba"] == 260
    assert pooled["after"]["nfl"] != pytest.approx(pooled["before"]["nfl"]),         "20 games should have been pulled toward the pool"
    assert abs(pooled["after"]["nba"] - pooled["before"]["nba"]) <         abs(pooled["after"]["nfl"] - pooled["before"]["nfl"]),         "the sport with more data should have moved less"


def test_a_neutral_game_does_not_count_as_evidence_of_home_advantage():
    """`build_feature_row` fires NO sport slot at a neutral venue, so a
    neutral game contributes nothing to that coefficient -- and must not
    count toward the sample size the shrinkage trusts it for.

    This is not hypothetical. ncaab in production is 73 neutral games
    against 12 hosted: counting neutrals would present a coefficient fitted
    from 12 games as though 85 stood behind it, and shrink it far too
    little.
    """
    import datetime as dt

    from backend.analysis.calibrated_model import CalibratedModel
    from backend.database import get_engine, get_session
    from backend.models import Base, Game, Team

    engine = get_engine(":memory:")
    Base.metadata.create_all(engine)
    session = get_session(engine)
    for sport, hosted, neutral in (("nba", 250, 0), ("ncaab", 12, 73)):
        for i in range(hosted + neutral):
            h = Team(name=f"{sport}H{i}", abbreviation="H", sport=sport)
            a = Team(name=f"{sport}A{i}", abbreviation="A", sport=sport)
            session.add_all([h, a])
            session.flush()
            session.add(Game(
                sport=sport, season="2026",
                date=dt.date(2026, 1, 1) + dt.timedelta(days=i),
                home_team_id=h.id, away_team_id=a.id, status="final",
                home_score=110 if i % 3 else 100,
                away_score=100 if i % 3 else 110,
                neutral_site=i >= hosted))
    session.commit()

    model = CalibratedModel()
    model.train_from_db(session)
    session.close()

    counts = model.sport_pooling["hosted_games"]
    assert counts["ncaab"] == 12, (
        f"only hosted games are evidence of home advantage, got {counts['ncaab']}")
    assert counts["nba"] == 250

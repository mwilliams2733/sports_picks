"""Tests for the out-of-sample calibration report.

Tests 1-4 exercise the pure statistics core and need no database.
Test 5 is the load-bearing one: it proves the fit set and the evaluation set
are disjoint, i.e. the model is never scored against a game it was fit on.
Test 6 guards the leakage control itself -- an in-sample run must report that
it overlapped, or the comparison that validates the whole report is worthless.
"""
from datetime import date, timedelta

import pytest

from backend.analysis.calibration_report import (
    binary_pairs,
    brier_score,
    choose_split_date,
    effective_sample_size,
    evaluate,
    format_report,
    is_leaked,
    reliability_bins,
)
from backend.models import Base, EloRating, Game, Odds, Team


# --- 1. Perfectly calibrated input -----------------------------------------

def test_perfect_calibration_has_near_zero_gap_and_known_brier():
    # 10 predictions at 0.2 that win twice, 10 at 0.8 that win eight times.
    pairs = [(0.2, 1)] * 2 + [(0.2, 0)] * 8 + [(0.8, 1)] * 8 + [(0.8, 0)] * 2

    bins = [b for b in reliability_bins(pairs, n_bins=10, min_bin=5) if b.n]
    assert len(bins) == 2
    for b in bins:
        assert b.gap == pytest.approx(0.0, abs=1e-9)
        assert b.observed_rate == pytest.approx(b.mean_predicted, abs=1e-9)

    # Hand-computed: 4 pairs contribute 0.64 and 16 contribute 0.04,
    # so the total is 2.56 + 0.64 = 3.2 over 20 pairs = 0.16.
    assert brier_score(pairs) == pytest.approx(0.16, abs=1e-9)


# --- 2. Overconfident input is detected ------------------------------------

def test_overconfident_bin_is_reported_as_overconfident():
    # Predicts 0.95; actually wins 70% of the time. Gap should be ~+0.25.
    pairs = [(0.95, 1)] * 70 + [(0.95, 0)] * 30

    bins = [b for b in reliability_bins(pairs, n_bins=10, min_bin=30) if b.n]
    assert len(bins) == 1
    top = bins[0]
    assert top.n == 100
    assert top.mean_predicted == pytest.approx(0.95, abs=1e-9)
    assert top.observed_rate == pytest.approx(0.70, abs=1e-9)
    # Signed gap = predicted - observed. Positive means overconfident.
    assert top.gap == pytest.approx(0.25, abs=1e-9)
    assert top.gap > 0


# --- 3. Ties are excluded, not counted as losses ---------------------------

def test_ties_are_excluded_not_counted_as_losses():
    rows = [
        (0.6, 110, 100),  # home win
        (0.4, 90, 100),   # home loss
        (0.5, 100, 100),  # tie -> must vanish
    ]
    pairs = binary_pairs(rows)
    assert pairs == [(0.6, 1), (0.4, 0)]
    # A tie counted as a loss would make this 1/3; excluded it is 1/2.
    assert sum(o for _, o in pairs) / len(pairs) == pytest.approx(0.5)


# --- 4. Bin counts reported; thin bins flagged unreliable -------------------

def test_thin_bins_are_flagged_unreliable_and_counts_reported():
    pairs = [(0.15, 1)] * 2 + [(0.15, 0)] * 1 + [(0.85, 1)] * 40
    bins = {b.lo: b for b in reliability_bins(pairs, n_bins=10, min_bin=30)}

    thin = next(b for b in bins.values() if b.n == 3)
    fat = next(b for b in bins.values() if b.n == 40)

    assert thin.n == 3 and thin.reliable is False
    assert fat.n == 40 and fat.reliable is True
    # Every bin is represented, so the reader can see where the mass is.
    assert len(bins) == 10
    assert sum(b.n for b in bins.values()) == 43


# --- 5. The split is genuinely out of sample -------------------------------

def _seed_two_windows(session, n_early=70, n_late=40):
    """Seed NBA games in two date windows with odds so they are predictable."""
    teams = [Team(id=i, name=f"T{i}", abbreviation=f"T{i}", sport="nba")
             for i in range(1, 9)]
    session.add_all(teams)
    session.flush()  # teams must exist before the ELO/game foreign keys
    session.add_all([EloRating(team_id=i, sport="nba", rating=1500.0 + 20 * i)
                     for i in range(1, 9)])
    start = date(2026, 1, 1)
    gid = 0
    for n in (n_early, n_late):
        for k in range(n):
            gid += 1
            home = (gid % 8) + 1
            away = ((gid + 3) % 8) + 1
            session.add(Game(
                id=gid, sport="nba", season="2025-26",
                date=start + timedelta(days=gid),
                home_team_id=home, away_team_id=away,
                # Both classes must appear or the logistic fit cannot converge.
                home_score=110 + (gid % 5),
                away_score=(120 if gid % 3 == 0 else 100) + (gid % 7),
                status="final",
            ))
            session.add(Odds(game_id=gid, bookmaker="book",
                             moneyline_home=-150, moneyline_away=130))
    session.commit()


def test_evaluation_games_are_never_in_the_fit_set(db_session):
    Base.metadata.create_all(db_session.get_bind())
    _seed_two_windows(db_session)

    split = choose_split_date(db_session, "nba", train_frac=0.7)
    report = evaluate(db_session, "nba", n_bins=10, min_bin=5, split_date=split)

    assert report.n_fit > 0 and report.n_eval > 0
    # The load-bearing assertion: no game was both fit on and scored.
    assert not (report.fit_game_ids & report.eval_game_ids)
    # And the model really was fit on exactly the fit window, nothing more.
    assert report.n_model_training_games == report.n_fit
    # Every evaluation game falls on or after the split date.
    assert report.eval_start >= split
    assert report.fit_end < split


def test_in_sample_control_reports_itself_as_leaked(db_session):
    """The leakage control must admit it leaked.

    If this ever passes silently, the in-sample/out-of-sample comparison that
    validates the whole report would be comparing two identical things without
    anyone noticing.
    """
    Base.metadata.create_all(db_session.get_bind())
    _seed_two_windows(db_session)

    split = choose_split_date(db_session, "nba", train_frac=0.7)
    clean = evaluate(db_session, "nba", min_bin=5, split_date=split)
    leaked = evaluate(db_session, "nba", min_bin=5, split_date=split, in_sample=True)

    # Same evaluation window in both, so only the fit differs.
    assert clean.eval_game_ids == leaked.eval_game_ids
    # The clean run is disjoint; the control overlaps and says so.
    assert not (clean.fit_game_ids & clean.eval_game_ids)
    assert leaked.fit_game_ids & leaked.eval_game_ids
    assert leaked.n_model_training_games > clean.n_model_training_games


# --- 7. Effective sample size is arithmetic in a measurement instrument -----

def test_effective_sample_size_matches_the_design_effect_formula():
    # The real NBA evaluation set: 310 games across 33 teams.
    # m    = 2 * 310 / 33      = 18.787878...
    # deff = 1 + (m - 1) * .05 =  1.889393...
    # ess  = 310 / deff        = 164.07...
    m = 2 * 310 / 33
    deff = 1 + (m - 1) * 0.05
    assert effective_sample_size(310, 33, 0.05) == pytest.approx(310 / deff)
    assert effective_sample_size(310, 33, 0.05) == pytest.approx(164.08, abs=0.01)
    # A smaller ICC must cost less precision.
    assert effective_sample_size(310, 33, 0.01) == pytest.approx(263.18, abs=0.01)
    # Zero correlation means no penalty at all.
    assert effective_sample_size(310, 33, 0.0) == pytest.approx(310.0)


def test_effective_sample_size_degenerate_cluster_size_is_a_no_op():
    # 7 games across 14 teams: every team appears exactly once, so the average
    # cluster holds one observation and there is no correlation to discount --
    # whatever the ICC.
    for icc in (0.0, 0.01, 0.05, 0.5):
        assert effective_sample_size(7, 14, icc) == pytest.approx(7.0)
    # Guard the empty cases rather than dividing by zero.
    assert effective_sample_size(0, 33, 0.05) == 0.0
    assert effective_sample_size(310, 0, 0.05) == 0.0


# --- 8. The header cannot contradict the overlap warning beneath it ---------

def test_eval_on_fit_without_in_sample_is_labelled_leaked(db_session):
    """`--eval-on fit` fits on date<split and scores date<split: fully leaked.

    `in_sample` is False for that run, so a header derived from the flag would
    read "out-of-sample" above a leaked table. The header is the line that gets
    pasted into downstream reports, so it must be derived from the real
    fit/eval intersection.
    """
    Base.metadata.create_all(db_session.get_bind())
    _seed_two_windows(db_session)

    split = choose_split_date(db_session, "nba", train_frac=0.7)
    report = evaluate(db_session, "nba", min_bin=5, split_date=split,
                      in_sample=False, eval_on="fit")

    assert report.in_sample is False           # the flag says clean...
    assert is_leaked(report)                   # ...but the sets say otherwise
    header = format_report(report).splitlines()[0]
    assert "LEAKED" in header
    assert "out-of-sample" not in header


def test_clean_run_header_says_out_of_sample(db_session):
    Base.metadata.create_all(db_session.get_bind())
    _seed_two_windows(db_session)

    split = choose_split_date(db_session, "nba", train_frac=0.7)
    report = evaluate(db_session, "nba", min_bin=5, split_date=split)

    assert not is_leaked(report)
    assert "out-of-sample" in format_report(report).splitlines()[0]


# --- 9. An unfitted model must not produce a reliability table -------------

def test_untrained_model_refuses_to_print_a_reliability_table(db_session):
    """Below MIN_TRAINING_GAMES the strategy serves a fixed heuristic.

    CalibratedModel sets trained=False and predict_home_win_prob silently falls
    back to _fallback_probability. A table built from that describes the
    heuristic, not the model, and must never be printable as calibration.
    """
    Base.metadata.create_all(db_session.get_bind())
    # 14 games before the split: too few for CalibratedModel to fit.
    _seed_two_windows(db_session, n_early=14, n_late=30)

    split = date(2026, 1, 16)  # leaves the 14 earliest games in the fit window
    report = evaluate(db_session, "nba", min_bin=5, split_date=split)

    assert report.n_fit == 14
    assert report.model_trained is False
    assert report.n_model_training_games == 0

    out = format_report(report)
    assert "REFUSING TO PRINT A RELIABILITY TABLE" in out
    assert "_fallback_probability" in out
    # The numbers themselves must not be rendered.
    assert "Brier score" not in out
    assert "mean_pred" not in out


def test_trained_model_does_print_the_table(db_session):
    """The refusal must be conditional, not a blanket suppression."""
    Base.metadata.create_all(db_session.get_bind())
    _seed_two_windows(db_session)

    split = choose_split_date(db_session, "nba", train_frac=0.7)
    report = evaluate(db_session, "nba", min_bin=5, split_date=split)

    assert report.model_trained is True
    out = format_report(report)
    assert "REFUSING" not in out
    assert "Brier score" in out


# --- 10. The caveat travels with the numbers, and is true ------------------

def test_report_does_not_claim_the_error_is_a_lower_bound(db_session):
    """Plan 008 made the evaluation features point-in-time.

    Until it did, this report warned that team stats and Elo were read as they
    stand today, so the measured error was a lower bound on the truth.  That
    warning is now false: ``_build_game_data`` passes ``game_id`` and
    ``game_date`` down to ``_team_stat_rows``, which can never reach forward in
    time.  Printing it anyway tells a reader to discount a number that does not
    need discounting.
    """
    Base.metadata.create_all(db_session.get_bind())
    _seed_two_windows(db_session)

    split = choose_split_date(db_session, "nba", train_frac=0.7)
    out = format_report(evaluate(db_session, "nba", min_bin=5, split_date=split))

    assert "LOWER BOUND" not in out
    assert "season-end snapshots" not in out


def test_constant_feature_caveat_is_printed_next_to_the_brier_score(db_session):
    """The limitation that remains is that three features carry no signal.

    ``offensive_rating``, ``defensive_rating`` and ``pace`` need possession
    counts no collector supplies, so they fall back to a constant for every
    game.  A reader comparing this Brier against another model has to know the
    model produced it on four working features, not seven.
    """
    Base.metadata.create_all(db_session.get_bind())
    _seed_two_windows(db_session)

    split = choose_split_date(db_session, "nba", train_frac=0.7)
    out = format_report(evaluate(db_session, "nba", min_bin=5, split_date=split))

    assert "CAVEAT" in out
    assert "offensive_rating" in out
    assert "pace" in out
    # It must sit with the Brier score, not be buried at the top.
    assert out.index("Brier score") < out.index("CAVEAT")


# --------------------------------------------------------------------------
# The fitted home baseline, against the sport's real home rate.
#
# CalibratedModel carries home advantage entirely in its intercept, so a
# baseline that has drifted away from the sport's actual rate is invisible in
# the Brier score but decides every moneyline edge. It belongs next to the
# other caveats.
# --------------------------------------------------------------------------


def _seed_sport(session, sport, n, home_rate, team_base, gid_base):
    """n games of one sport whose home side wins home_rate of them.

    Home wins are spread with a modulus rather than front-loaded, so the
    rate holds in both the fit and the evaluation window instead of putting
    every home win on one side of the split.
    """
    session.add_all([
        Team(id=team_base + i, name=f"{sport}{i}", abbreviation=f"{sport}{i}",
             sport=sport)
        for i in range(4)
    ])
    session.flush()
    session.add_all([EloRating(team_id=team_base + i, sport=sport,
                               rating=1500.0 + 20 * i) for i in range(4)])
    threshold = round(home_rate * 10)
    for k in range(n):
        gid = gid_base + k
        home_won = (k % 10) < threshold
        session.add(Game(
            id=gid, sport=sport, season="2025-26",
            date=date(2026, 1, 1) + timedelta(days=k),
            home_team_id=team_base + (k % 4),
            away_team_id=team_base + ((k + 1) % 4),
            home_score=110 if home_won else 100,
            away_score=100 if home_won else 110,
            status="final",
        ))
        session.add(Odds(game_id=gid, bookmaker="book",
                         moneyline_home=-150, moneyline_away=130))
    session.commit()


def _two_sport_db(db_session):
    """nba at a 0.4 home rate, ncaab at 0.9 -- deliberately far apart.

    The pooled rate lands near 0.5, so a report that mistakenly uses the
    whole fit set cannot accidentally agree with either sport's own rate.
    """
    Base.metadata.create_all(db_session.get_bind())
    _seed_sport(db_session, "nba", 100, 0.4, team_base=100, gid_base=1000)
    _seed_sport(db_session, "ncaab", 60, 0.9, team_base=200, gid_base=2000)
    return db_session


def test_the_report_carries_the_fitted_home_baseline(db_session):
    r = evaluate(_two_sport_db(db_session), "ncaab")
    assert r.fitted_home_baseline is not None
    assert 0.0 < r.fitted_home_baseline < 1.0


def test_the_actual_home_rate_is_this_sports_games_not_the_whole_pool(db_session):
    """The comparison is only meaningful sport against sport.

    ``fit_games`` holds every sport's games -- that is deliberately what the
    model saw. But comparing a sport-specific baseline against an all-sport
    home rate would put the pooled number back on the other side of the
    comparison, which is the exact confusion this line exists to expose.
    """
    session = _two_sport_db(db_session)
    ncaab = evaluate(session, "ncaab")
    nba = evaluate(session, "nba")

    assert ncaab.actual_home_rate > 0.8, (
        f"{ncaab.actual_home_rate}: looks pooled, not ncaab's own 0.9"
    )
    assert nba.actual_home_rate < 0.5, (
        f"{nba.actual_home_rate}: looks pooled, not nba's own 0.4"
    )


def test_the_baseline_is_printed_against_the_actual_rate(db_session):
    text = format_report(evaluate(_two_sport_db(db_session), "ncaab"))
    assert "Fitted home baseline" in text
    assert "Actual home win rate" in text
    assert "gap" in text


def test_a_sport_outside_the_vocabulary_is_warned_about(db_session):
    """Such a sport sets no one-hot slot, so its baseline cannot move."""
    Base.metadata.create_all(db_session.get_bind())
    _seed_sport(db_session, "nba", 100, 0.4, team_base=100, gid_base=1000)
    _seed_sport(db_session, "hurling", 60, 0.9, team_base=200, gid_base=2000)

    text = format_report(evaluate(db_session, "hurling"))
    assert "SPORT_VOCAB" in text


def test_a_sport_inside_the_vocabulary_is_not_warned_about(db_session):
    """The guard must discriminate, not print unconditionally."""
    text = format_report(evaluate(_two_sport_db(db_session), "ncaab"))
    assert "SPORT_VOCAB" not in text

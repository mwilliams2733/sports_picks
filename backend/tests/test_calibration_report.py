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
    evaluate,
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

    # Hand-computed: every pair contributes 0.64 or 0.04; total 6.4 over 20.
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

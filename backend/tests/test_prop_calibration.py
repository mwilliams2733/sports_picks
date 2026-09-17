"""Guards for the prop reliability report.

The question this tool exists to answer: do 5-star props win more often than
4-star props? If they do not, the confidence score carries no information and
the digest must not rank by it.
"""
import datetime

import pytest

from backend.analysis.prop_calibration import (
    effective_prop_sample_size, evaluate_prop_calibration, format_prop_report,
)
from backend.models import (
    Base, Game, PickModel, PickResult, StrategyModel, Team,
)


def _seed(session, rows, *, n_games=1):
    """rows: (confidence, result, payout, game_no)."""
    Base.metadata.create_all(session.get_bind())
    session.add_all([
        Team(id=1, name="Cleveland Cavaliers", abbreviation="CLE", sport="nba"),
        Team(id=2, name="New York Knicks", abbreviation="NY", sport="nba"),
    ])
    session.add(StrategyModel(id=1, name="props", config_json="{}",
                              strategy_type="prop"))
    session.flush()
    for g in range(1, n_games + 1):
        session.add(Game(id=g, sport="nba", season="2025-26",
                         date=datetime.date(2026, 1, 1) + datetime.timedelta(days=g),
                         home_team_id=1,
                         away_team_id=2, home_score=110, away_score=105,
                         status="final"))
    session.flush()
    for i, (conf, result, payout, game_no) in enumerate(rows, start=1):
        session.add(PickModel(id=i, game_id=game_no, strategy_id=1,
                              pick_type="prop", pick_value=f"P{i} Over 1.5 Points",
                              confidence=conf, edge_pct=5.0, odds_at_pick=-110,
                              prop_player=f"P{i}", prop_market="player_points"))
        session.flush()
        session.add(PickResult(pick_id=i, result=result, payout=payout))
    session.commit()


def test_win_rate_excludes_pushes_from_the_denominator(db_session):
    """A push is not a loss. Counting it as one understates every tier --
    the same defect plan 006 fixed in the recalibrator."""
    _seed(db_session, [
        (5, "win", 0.91, 1), (5, "win", 0.91, 1),
        (5, "loss", -1.0, 1), (5, "push", 0.0, 1),
    ])

    report = evaluate_prop_calibration(db_session, "nba", min_bin=1)
    tier = report.tiers[5]

    assert tier.wins == 2 and tier.losses == 1 and tier.pushes == 1
    assert tier.win_rate == pytest.approx(2 / 3)       # not 2/4


def test_roi_comes_from_the_stored_payout_not_a_flat_unit(db_session):
    """payout is priced per pick's odds by payout_for. Recomputing it here
    from a flat unit would contradict the table it is reading."""
    _seed(db_session, [(4, "win", 0.5, 1), (4, "loss", -1.0, 1)])

    tier = evaluate_prop_calibration(db_session, "nba", min_bin=1).tiers[4]

    assert tier.units == pytest.approx(-0.5)
    assert tier.roi == pytest.approx(-0.25)            # -0.5 units over 2 settled


def test_a_tier_under_min_bin_is_flagged_unreliable(db_session):
    """Two picks is not a win rate. The flag is what stops a reader acting on
    it."""
    _seed(db_session, [(3, "win", 0.91, 1), (3, "loss", -1.0, 1)])

    report = evaluate_prop_calibration(db_session, "nba", min_bin=30)

    assert report.tiers[3].reliable is False
    assert "NO" in format_prop_report(report)


def test_effective_sample_size_clusters_on_the_game_not_the_pick(db_session):
    """Six props from one game are not six independent observations: they
    share its pace, blowout risk and rotation. The team-clustered helper in
    calibration_report.py assumes two clusters per observation and is the
    wrong model here."""
    # 6 observations in 1 cluster is far less information than 6 in 6.
    concentrated = effective_prop_sample_size(6, 1, icc=0.05)
    spread = effective_prop_sample_size(6, 6, icc=0.05)

    assert concentrated < spread
    assert spread == pytest.approx(6.0)       # one per cluster: no deflation
    assert concentrated == pytest.approx(6 / (1 + 5 * 0.05))


def test_report_states_whether_five_star_beats_four_star(db_session):
    """The tool's entire purpose. A reader must not have to eyeball the table
    to learn the answer."""
    _seed(db_session, [(5, "win", 0.91, 1)] * 3 + [(4, "loss", -1.0, 1)] * 3)

    out = format_prop_report(evaluate_prop_calibration(db_session, "nba", min_bin=1))

    assert "5-star vs 4-star" in out


def test_a_report_with_nothing_graded_refuses_rather_than_reading_as_a_result(db_session):
    """An all-zero table looks like a measured finding. It is the absence of
    one. This is the state production is in right now: 82 props, 0 graded.
    """
    _seed(db_session, [])

    out = format_prop_report(evaluate_prop_calibration(db_session, "nba"))

    assert "REFUSING" in out
    assert "0.0%" not in out          # no fabricated win rate


def test_reliability_is_judged_on_effective_n_not_the_raw_count(db_session):
    """The report tells readers to use the effective n. Flagging a tier
    reliable on the raw count contradicts its own advice.

    36 props from 2 games clear min_bin=30 on raw count but carry roughly half
    that much information. This is the real shape of the production data.
    """
    _seed(db_session, [(5, "win", 0.91, 1)] * 18 + [(5, "win", 0.91, 2)] * 18,
          n_games=2)

    report = evaluate_prop_calibration(db_session, "nba", min_bin=30)

    assert report.tiers[5].settled == 36          # raw count clears the bar
    assert report.tiers[5].reliable is False      # effective n does not


def test_a_tier_spread_across_many_games_is_reliable_at_the_same_raw_n(db_session):
    """Same 36 picks, one per game, carries full information and passes."""
    _seed(db_session, [(5, "win", 0.91, g) for g in range(1, 37)], n_games=36)

    report = evaluate_prop_calibration(db_session, "nba", min_bin=30)

    assert report.tiers[5].settled == 36
    assert report.tiers[5].reliable is True

"""Tests for confidence recalibrator."""
from datetime import date, datetime, timedelta, timezone
from backend.analysis.recalibrator import Recalibrator, MIN_PICKS_PER_TIER
from backend.models import Base, PickModel, PickResult, Game, Team, StrategyModel, CalibrationHistory
from backend.database import get_engine, get_session


def _setup_db():
    engine = get_engine(":memory:")
    Base.metadata.create_all(engine)
    session = get_session(engine)
    t1 = Team(name="Team A", abbreviation="TA", sport="nba")
    t2 = Team(name="Team B", abbreviation="TB", sport="nba")
    session.add_all([t1, t2])
    session.commit()
    strat = StrategyModel(name="test", sport="nba", config_json="{}")
    session.add(strat)
    session.commit()
    return session, t1, t2, strat


def _add_picks(session, t1, t2, strat, confidence, wins, losses, sport="nba",
               pushes=0, created_at=None):
    for i in range(wins + losses + pushes):
        game = Game(
            sport=sport, season="2025-26",
            date=date(2026, 3, i % 28 + 1),
            home_team_id=t1.id, away_team_id=t2.id,
            home_score=100 + i, away_score=95,
            status="final",
        )
        session.add(game)
        session.flush()
        pick = PickModel(
            game_id=game.id, strategy_id=strat.id,
            pick_type="moneyline", pick_value="HOME ML",
            confidence=confidence, edge_pct=10.0, odds_at_pick=-150,
            **({"created_at": created_at + timedelta(seconds=i)}
               if created_at is not None else {}),
        )
        session.add(pick)
        session.flush()
        if i < wins:
            result_value, payout = "win", 100.0
        elif i < wins + losses:
            result_value, payout = "loss", 0.0
        else:
            result_value, payout = "push", 0.0
        result = PickResult(
            pick_id=pick.id,
            result=result_value,
            payout=payout,
        )
        session.add(result)
    session.commit()


def test_min_picks_per_tier_is_large_enough_to_mean_something():
    """Pinning the literal is what let this sit at 20 unexamined.

    See test_the_minimum_is_derived_from_the_deviation_it_acts_on for the
    relationship that actually constrains it; this only guards against a
    value so small the tier is noise.
    """
    assert MIN_PICKS_PER_TIER >= 100


def test_recalibrator_skips_small_samples():
    session, t1, t2, strat = _setup_db()
    _add_picks(session, t1, t2, strat, confidence=5,
               wins=MIN_PICKS_PER_TIER // 4, losses=MIN_PICKS_PER_TIER // 4)
    recal = Recalibrator(session, sport="nba")
    adjustments = recal.run()
    assert 5 not in adjustments


def test_recalibrator_tightens_when_underperforming():
    session, t1, t2, strat = _setup_db()
    _add_picks(session, t1, t2, strat, confidence=5,
               wins=int(MIN_PICKS_PER_TIER * 0.55) + 1,
               losses=MIN_PICKS_PER_TIER - int(MIN_PICKS_PER_TIER * 0.55))
    recal = Recalibrator(session, sport="nba")
    adjustments = recal.run()
    assert 5 in adjustments
    assert adjustments[5]["direction"] == "tighten"
    assert adjustments[5]["new_threshold"] > 12.0


def test_recalibrator_loosens_when_overperforming():
    session, t1, t2, strat = _setup_db()
    _add_picks(session, t1, t2, strat, confidence=3,
               wins=int(MIN_PICKS_PER_TIER * 0.75) + 1,
               losses=MIN_PICKS_PER_TIER - int(MIN_PICKS_PER_TIER * 0.75))
    recal = Recalibrator(session, sport="nba")
    adjustments = recal.run()
    assert 3 in adjustments
    assert adjustments[3]["direction"] == "loosen"
    assert adjustments[3]["new_threshold"] < 5.0


def test_recalibrator_saves_to_db():
    session, t1, t2, strat = _setup_db()
    _add_picks(session, t1, t2, strat, confidence=5,
               wins=int(MIN_PICKS_PER_TIER * 0.55) + 1,
               losses=MIN_PICKS_PER_TIER - int(MIN_PICKS_PER_TIER * 0.55))
    recal = Recalibrator(session, sport="nba")
    recal.run()
    rows = session.query(CalibrationHistory).all()
    assert len(rows) >= 1
    assert rows[0].sport == "nba"
    assert rows[0].confidence_tier == 5


def test_recalibrator_only_counts_its_own_sport():
    session, t1, t2, strat = _setup_db()
    t3 = Team(name="Team C", abbreviation="TC", sport="nfl")
    t4 = Team(name="Team D", abbreviation="TD", sport="nfl")
    session.add_all([t3, t4])
    session.commit()
    # nba tier-5 picks, all wins
    _add_picks(session, t1, t2, strat, confidence=5,
               wins=MIN_PICKS_PER_TIER + 1, losses=0, sport="nba")
    # nfl tier-5 picks, all losses -- must not reach the nba run
    _add_picks(session, t3, t4, strat, confidence=5,
               wins=0, losses=MIN_PICKS_PER_TIER + 1, sport="nfl")

    recal = Recalibrator(session, sport="nba")
    recal.run()
    rows = session.query(CalibrationHistory).filter(
        CalibrationHistory.sport == "nba", CalibrationHistory.confidence_tier == 5
    ).all()
    assert len(rows) == 1
    assert rows[0].actual_win_rate == 1.0


def test_pushes_excluded_from_win_rate():
    session, t1, t2, strat = _setup_db()
    _add_picks(session, t1, t2, strat, confidence=5,
               wins=MIN_PICKS_PER_TIER + 1, losses=0, pushes=9)
    recal = Recalibrator(session, sport="nba")
    recal.run()
    rows = session.query(CalibrationHistory).filter(
        CalibrationHistory.sport == "nba", CalibrationHistory.confidence_tier == 5
    ).all()
    assert len(rows) == 1
    assert rows[0].actual_win_rate == 1.0
    assert rows[0].sample_size == MIN_PICKS_PER_TIER + 1


def test_newest_threshold_wins():
    session, t1, t2, strat = _setup_db()
    session.add(CalibrationHistory(
        date=date(2026, 1, 1), sport="nba", confidence_tier=5,
        predicted_win_rate=0.70, actual_win_rate=0.60,
        sample_size=20, old_threshold=12.0, new_threshold=9.0,
    ))
    session.add(CalibrationHistory(
        date=date(2026, 3, 1), sport="nba", confidence_tier=5,
        predicted_win_rate=0.70, actual_win_rate=0.60,
        sample_size=20, old_threshold=12.0, new_threshold=7.0,
    ))
    session.commit()
    # 25 picks that deviate enough to trigger an adjustment and record old_threshold
    _add_picks(session, t1, t2, strat, confidence=5,
               wins=int(MIN_PICKS_PER_TIER * 0.45),
               losses=MIN_PICKS_PER_TIER - int(MIN_PICKS_PER_TIER * 0.45) + 1)
    recal = Recalibrator(session, sport="nba")
    adjustments = recal.run()
    assert 5 in adjustments
    assert adjustments[5]["old_threshold"] == 7.0


def test_picks_from_a_previous_season_still_count():
    """A sport's offseason must not empty its own calibration history.

    The recalibrator used to filter `created_at >= today - 90 days`. nba and
    ncaab finish in spring and restart in autumn, so on the first day of a new
    season that window is empty and stays empty until twenty fresh picks have
    graded -- exactly the stretch where the thresholds are least trustworthy.
    On 2026-09-20 it hid 203 of the 328 graded picks in the database.
    """
    session, t1, t2, strat = _setup_db()
    _add_picks(session, t1, t2, strat, confidence=5, wins=int(MIN_PICKS_PER_TIER * 0.45),
               losses=MIN_PICKS_PER_TIER - int(MIN_PICKS_PER_TIER * 0.45) + 1,
               created_at=datetime(2026, 3, 1, 12, 0))
    adjustments = Recalibrator(session, sport="nba").run()
    assert 5 in adjustments
    assert adjustments[5]["sample_size"] == MIN_PICKS_PER_TIER + 1


def test_only_the_newest_picks_inside_the_cap_count():
    """The window is a count, so it must be the *most recent* count."""
    session, t1, t2, strat = _setup_db()
    # Older: all losses. Newer: all wins. A cap of exactly the newer batch
    # must see only the wins.
    _add_picks(session, t1, t2, strat, confidence=5, wins=0, losses=MIN_PICKS_PER_TIER + 1,
               created_at=datetime(2025, 11, 1, 12, 0))
    _add_picks(session, t1, t2, strat, confidence=5, wins=MIN_PICKS_PER_TIER + 1, losses=0,
               created_at=datetime(2026, 3, 1, 12, 0))
    adjustments = Recalibrator(session, sport="nba").run(max_picks=MIN_PICKS_PER_TIER + 1)
    assert adjustments[5]["sample_size"] == MIN_PICKS_PER_TIER + 1
    assert adjustments[5]["actual_rate"] == 1.0


def test_the_window_can_hold_the_minimum_sample():
    """A cap below the floor makes the recalibrator inert with no error.

    Every tier would fetch at most MAX_PICKS_PER_TIER picks and then be
    rejected for having fewer than MIN_PICKS_PER_TIER, logging only the
    ordinary "not enough picks" line. Nothing would ever recalibrate and
    nothing would say why.
    """
    from backend.analysis.recalibrator import MAX_PICKS_PER_TIER
    assert MAX_PICKS_PER_TIER >= MIN_PICKS_PER_TIER


def test_the_minimum_is_derived_from_the_deviation_it_acts_on():
    """Not a chosen number: the sample at which a DEVIATION_THRESHOLD-sized
    gap is distinguishable from noise. At the old value of 20 it was 0.45
    standard errors -- the recalibrator was adjusting on coin flips."""
    import math
    from backend.analysis.recalibrator import DEVIATION_THRESHOLD
    se = 0.5 / math.sqrt(MIN_PICKS_PER_TIER)
    assert 1.6 <= DEVIATION_THRESHOLD / se <= 1.7

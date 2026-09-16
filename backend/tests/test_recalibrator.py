"""Tests for confidence recalibrator."""
from datetime import date, datetime, timezone
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


def _add_picks(session, t1, t2, strat, confidence, wins, losses, sport="nba", pushes=0):
    for i in range(wins + losses + pushes):
        game = Game(
            sport=sport, season="2025-26",
            date=date(2026, 3, i % 28 + 1),
            home_team_id=t1.id, away_team_id=t2.id,
            home_score=100 + i, away_score=95,
            status="final",
        )
        session.add(game)
        session.commit()
        pick = PickModel(
            game_id=game.id, strategy_id=strat.id,
            pick_type="moneyline", pick_value="HOME ML",
            confidence=confidence, edge_pct=10.0, odds_at_pick=-150,
        )
        session.add(pick)
        session.commit()
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


def test_min_picks_per_tier():
    assert MIN_PICKS_PER_TIER == 20


def test_recalibrator_skips_small_samples():
    session, t1, t2, strat = _setup_db()
    _add_picks(session, t1, t2, strat, confidence=5, wins=8, losses=2)
    recal = Recalibrator(session, sport="nba")
    adjustments = recal.run(days=90)
    assert 5 not in adjustments


def test_recalibrator_tightens_when_underperforming():
    session, t1, t2, strat = _setup_db()
    _add_picks(session, t1, t2, strat, confidence=5, wins=11, losses=9)
    recal = Recalibrator(session, sport="nba")
    adjustments = recal.run(days=90)
    assert 5 in adjustments
    assert adjustments[5]["direction"] == "tighten"
    assert adjustments[5]["new_threshold"] > 12.0


def test_recalibrator_loosens_when_overperforming():
    session, t1, t2, strat = _setup_db()
    _add_picks(session, t1, t2, strat, confidence=3, wins=18, losses=6)
    recal = Recalibrator(session, sport="nba")
    adjustments = recal.run(days=90)
    assert 3 in adjustments
    assert adjustments[3]["direction"] == "loosen"
    assert adjustments[3]["new_threshold"] < 5.0


def test_recalibrator_saves_to_db():
    session, t1, t2, strat = _setup_db()
    _add_picks(session, t1, t2, strat, confidence=5, wins=11, losses=9)
    recal = Recalibrator(session, sport="nba")
    recal.run(days=90)
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
    # 25 nba tier-5 picks, all wins
    _add_picks(session, t1, t2, strat, confidence=5, wins=25, losses=0, sport="nba")
    # 25 nfl tier-5 picks, all losses
    _add_picks(session, t3, t4, strat, confidence=5, wins=0, losses=25, sport="nfl")

    recal = Recalibrator(session, sport="nba")
    recal.run(days=90)
    rows = session.query(CalibrationHistory).filter(
        CalibrationHistory.sport == "nba", CalibrationHistory.confidence_tier == 5
    ).all()
    assert len(rows) == 1
    assert rows[0].actual_win_rate == 1.0


def test_pushes_excluded_from_win_rate():
    session, t1, t2, strat = _setup_db()
    _add_picks(session, t1, t2, strat, confidence=5, wins=21, losses=0, pushes=9)
    recal = Recalibrator(session, sport="nba")
    recal.run(days=90)
    rows = session.query(CalibrationHistory).filter(
        CalibrationHistory.sport == "nba", CalibrationHistory.confidence_tier == 5
    ).all()
    assert len(rows) == 1
    assert rows[0].actual_win_rate == 1.0
    assert rows[0].sample_size == 21


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
    _add_picks(session, t1, t2, strat, confidence=5, wins=11, losses=14)
    recal = Recalibrator(session, sport="nba")
    adjustments = recal.run(days=90)
    assert 5 in adjustments
    assert adjustments[5]["old_threshold"] == 7.0

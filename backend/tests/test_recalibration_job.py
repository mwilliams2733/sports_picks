"""The nightly job must reach every sport that produces picks.

mlb was absent from both loops in `run_recalibration` while appearing in every
other pipeline, so its graded picks were collected, stored and never used to
adjust anything. The two loops also listed their sports separately, which is
how a sport comes to be added to one and forgotten in the other.
"""
from datetime import date, datetime

from backend.database import get_engine, get_session, run_migrations
from backend.models import Game, PickModel, PickResult, StrategyModel, Team
from backend.pipeline.recalibration_job import RECALIBRATED_SPORTS, run_recalibration


def _seed(db_path: str, sport: str, *, wins: int, losses: int,
          ungraded: int = 0) -> None:
    engine = get_engine(db_path)
    run_migrations(engine)
    session = get_session(engine)
    t1 = Team(name=f"{sport} A", abbreviation=f"{sport}A", sport=sport)
    t2 = Team(name=f"{sport} B", abbreviation=f"{sport}B", sport=sport)
    session.add_all([t1, t2])
    session.commit()
    strat = StrategyModel(name="t", sport=sport, config_json="{}")
    session.add(strat)
    session.commit()
    for i in range(wins + losses + ungraded):
        game = Game(
            sport=sport, season="2026", date=date(2026, 6, i % 28 + 1),
            home_team_id=t1.id, away_team_id=t2.id,
            home_score=5, away_score=3, status="final",
        )
        session.add(game)
        session.commit()
        pick = PickModel(
            game_id=game.id, strategy_id=strat.id, pick_type="moneyline",
            pick_value="HOME ML", confidence=5, edge_pct=10.0, odds_at_pick=-150,
            created_at=datetime(2026, 6, 1, 12, i % 60),
        )
        session.add(pick)
        session.commit()
        if i >= wins + losses:
            continue  # left ungraded on purpose, for the job to grade
        session.add(PickResult(
            pick_id=pick.id,
            result="win" if i < wins else "loss",
            payout=100.0 if i < wins else 0.0,
        ))
    session.commit()
    session.close()


def test_mlb_is_recalibrated(tmp_path):
    db = str(tmp_path / "t.db")
    # 40 games clears the job's own `game_count < 30` gate; 18-22 at tier 5 is
    # far enough below the 70% expectation to force a threshold move.
    _seed(db, "mlb", wins=18, losses=22)
    summary = run_recalibration(db)
    assert "mlb" in summary["recalibrated"], summary


def test_both_loops_share_one_sport_list():
    """Derived, not duplicated: the retrain loop cannot drift from the
    recalibration loop if there is only one list to edit."""
    src = open("backend/pipeline/recalibration_job.py", encoding="utf-8").read()
    assert src.count("RECALIBRATED_SPORTS") >= 3  # definition + both loops
    # No loop may carry its own inline list of sports.
    assert "for sport in (" not in src
    assert "mlb" in RECALIBRATED_SPORTS


def test_summary_reports_what_was_actually_graded(tmp_path):
    """`summary["graded"]` was initialised to 0 and never assigned.

    `grade_pending_picks` counted its work, logged it, and returned None, so
    the job reported "graded: 0" in the same breath as a log line saying it
    had graded 122 picks. A summary that cannot be wrong is not a summary.
    """
    db = str(tmp_path / "t.db")
    _seed(db, "mlb", wins=18, losses=22, ungraded=7)
    summary = run_recalibration(db)
    assert summary["graded"] == 7, summary

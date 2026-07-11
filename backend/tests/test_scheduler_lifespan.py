"""Tests for the APScheduler-in-FastAPI-lifespan wiring.

We can't easily assert "the cron actually fired at 8am ET" inside a unit
test without mocking time, so these tests focus on:
  - configure_scheduler returns a scheduler with the four standard jobs
    registered with the expected IDs and triggers
  - The FastAPI lifespan starts the scheduler iff ENABLE_SCHEDULER is set
    (default: off, so tests don't accidentally spin up cron threads)
"""
import os


def test_configure_scheduler_registers_expected_cron_jobs():
    """configure_scheduler must wire the four cron jobs the deployment
    expects: 8am morning scout, 9/10am retries, 3am recalibration."""
    from apscheduler.schedulers.background import BackgroundScheduler
    from backend.pipeline.scheduler import configure_scheduler

    config = {"database_path": ":memory:", "seasons": {}, "odds_budget": {}}
    scheduler = configure_scheduler(config, engine=None)
    try:
        job_ids = {j.id for j in scheduler.get_jobs()}
        assert job_ids == {
            "morning_scout",
            "scout_retry_9",
            "scout_retry_10",
            "recalibration",
        }
        # Cron triggers — verify the hours match what the deployment expects.
        hours_by_id = {j.id: str(j.trigger) for j in scheduler.get_jobs()}
        assert "hour='8'" in hours_by_id["morning_scout"]
        assert "hour='9'" in hours_by_id["scout_retry_9"]
        assert "hour='10'" in hours_by_id["scout_retry_10"]
        assert "hour='3'" in hours_by_id["recalibration"]
    finally:
        # Scheduler hasn't been started, but call shutdown defensively in
        # case a future change starts it inside configure_scheduler.
        if isinstance(scheduler, BackgroundScheduler) and scheduler.running:
            scheduler.shutdown(wait=False)


def test_create_app_does_not_start_scheduler_by_default(monkeypatch):
    """Default behavior: no scheduler started. Critical for tests — otherwise
    every test that calls create_app(':memory:') would spawn cron threads."""
    monkeypatch.delenv("ENABLE_SCHEDULER", raising=False)
    from backend.api.main import create_app

    app = create_app(":memory:")
    # Lifespan only fires on real uvicorn run, but we can verify the app
    # itself didn't attach a running scheduler.
    assert getattr(app.state, "scheduler", None) is None


def test_morning_scout_grades_completed_combat_games():
    """morning_scout must invoke grade_completed_games (combat Elo) alongside
    grade_pending_picks, so fighter Elo doesn't stay frozen at seed values
    in production. Use an empty `seasons` config so morning_scout returns
    right after grading, before touching the network."""
    from datetime import date as _date
    from backend.database import get_engine, get_session
    from backend.models import Base, Team, Game, EloRating
    from backend.pipeline.scheduler import morning_scout

    engine = get_engine(":memory:")
    Base.metadata.create_all(engine)
    session = get_session(engine)
    home = Team(id=1, name="A", abbreviation="A", sport="mma")
    away = Team(id=2, name="B", abbreviation="B", sport="mma")
    session.add_all([home, away])
    session.flush()
    session.add_all([
        EloRating(team_id=1, sport="mma", rating=1500.0),
        EloRating(team_id=2, sport="mma", rating=1500.0),
    ])
    session.add(Game(id=1, sport="mma", season="2026", date=_date(2026, 4, 29),
                     home_team_id=1, away_team_id=2,
                     home_score=1, away_score=0, status="final"))
    session.commit()
    session.close()

    config = {"seasons": {}, "odds_budget": {}}
    morning_scout(config, engine, scheduler=None)

    verify_session = get_session(engine)
    home_elo = (verify_session.query(EloRating)
                .filter(EloRating.team_id == 1, EloRating.sport == "mma").first()).rating
    assert abs(home_elo - 1512.0) < 0.5, f"morning_scout should have graded the MMA game, got {home_elo}"


def test_create_app_attaches_scheduler_when_enabled(monkeypatch, tmp_path):
    """With ENABLE_SCHEDULER=1, the lifespan starts an APScheduler instance
    and stops it on shutdown. Use TestClient context manager to drive the
    lifespan events end-to-end."""
    from fastapi.testclient import TestClient

    db_path = str(tmp_path / "lifespan.db")
    monkeypatch.setenv("ENABLE_SCHEDULER", "1")
    monkeypatch.setenv("DATABASE_PATH", db_path)
    # Reset module-level app instance so it picks up the env var.
    import importlib
    import backend.api.main
    importlib.reload(backend.api.main)

    from backend.api.main import create_app
    app = create_app(db_path)

    # Drive lifespan startup + shutdown via TestClient context.
    with TestClient(app):
        scheduler = getattr(app.state, "scheduler", None)
        assert scheduler is not None
        assert scheduler.running
        job_ids = {j.id for j in scheduler.get_jobs()}
        assert "morning_scout" in job_ids

    # After exiting the context, lifespan shutdown should have fired.
    assert not app.state.scheduler.running

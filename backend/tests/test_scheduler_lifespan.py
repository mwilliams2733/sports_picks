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


def test_morning_scout_removes_stale_window_jobs_after_recluster(monkeypatch):
    """If an earlier run today clustered games into 2 windows and a later
    run (9/10 AM retry) reclusters them into just 1 (e.g. a game got moved
    closer together), the orphaned window_nba_{today}_1 job must be removed
    rather than left to linger forever."""
    from datetime import date as _date, datetime, timezone, timedelta
    from apscheduler.schedulers.background import BackgroundScheduler
    from backend.database import get_engine, get_session
    from backend.models import Base, Team, Game
    from backend.pipeline import scheduler as scheduler_module

    async def _noop_fetch_games(session, sports, target_date, *, reconcile=True):
        # `reconcile` accepted because morning_scout now walks a lookback
        # window and passes it per day. Without it this stub raises TypeError,
        # morning_scout's broad `except Exception` swallows it into "Scout
        # failed fetching games", and no window jobs are created at all.
        return None

    monkeypatch.setattr(scheduler_module, "fetch_and_store_games", _noop_fetch_games)

    engine = get_engine(":memory:")
    Base.metadata.create_all(engine)
    session = get_session(engine)
    session.add_all([
        Team(id=1, name="A", abbreviation="A", sport="nba"),
        Team(id=2, name="B", abbreviation="B", sport="nba"),
    ])
    session.flush()

    today = _date.today()
    far_future_base = datetime.now(tz=timezone.utc) + timedelta(hours=6)
    session.add_all([
        Game(id=1, sport="nba", season="2025-26", date=today,
             home_team_id=1, away_team_id=2, status="scheduled",
             start_time=far_future_base),
        Game(id=2, sport="nba", season="2025-26", date=today,
             home_team_id=1, away_team_id=2, status="scheduled",
             start_time=far_future_base + timedelta(hours=3)),
    ])
    session.commit()
    session.close()

    config = {"seasons": {"nba": {"start": "01-01", "end": "12-31"}}, "odds_budget": {}}
    aps_scheduler = BackgroundScheduler()
    try:
        # First pass: games 3 hours apart cluster into 2 separate windows.
        scheduler_module.morning_scout(config, engine, aps_scheduler)
        job_ids = {j.id for j in aps_scheduler.get_jobs()}
        assert f"window_nba_{today}_0" in job_ids
        assert f"window_nba_{today}_1" in job_ids

        # Recluster: pull game 2 close to game 1 so they fall in one window.
        verify_session = get_session(engine)
        game_2 = verify_session.query(Game).filter(Game.id == 2).first()
        game_2.start_time = far_future_base + timedelta(minutes=10)
        verify_session.commit()
        verify_session.close()

        scheduler_module.morning_scout(config, engine, aps_scheduler)
        job_ids = {j.id for j in aps_scheduler.get_jobs()}
        assert f"window_nba_{today}_0" in job_ids
        assert f"window_nba_{today}_1" not in job_ids, "stale window job must be removed"
    finally:
        if aps_scheduler.running:
            aps_scheduler.shutdown(wait=False)


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

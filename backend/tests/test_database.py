from sqlalchemy import text
from backend.models import Base

def test_engine_creates_sqlite_with_wal(db_engine):
    with db_engine.connect() as conn:
        result = conn.execute(text("PRAGMA journal_mode")).scalar()
        assert result == "wal"

def test_session_can_execute_query(db_session):
    result = db_session.execute(text("SELECT 1")).scalar()
    assert result == 1

def test_create_all_tables(db_engine):
    Base.metadata.create_all(db_engine)
    with db_engine.connect() as conn:
        tables = conn.execute(text(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )).scalars().all()
    expected = [
        "api_usage", "backtest_picks", "backtest_runs", "elo_ratings",
        "games", "odds", "pick_results", "picks", "strategies",
        "team_stats", "teams"
    ]
    assert sorted(tables) == sorted(expected)

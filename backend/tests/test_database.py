from sqlalchemy import text

def test_engine_creates_sqlite_with_wal(db_engine):
    with db_engine.connect() as conn:
        result = conn.execute(text("PRAGMA journal_mode")).scalar()
        assert result == "wal"

def test_session_can_execute_query(db_session):
    result = db_session.execute(text("SELECT 1")).scalar()
    assert result == 1

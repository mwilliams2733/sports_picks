"""Live scores (sportsbook spec 2026-10-07 §8): the live_detail column, the
live_scores job, and the guards around the new in_progress status."""
from sqlalchemy import create_engine, inspect, text

from backend.database import run_migrations
from backend.models import Base


def test_the_migration_adds_live_detail_to_an_existing_games_table():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with engine.begin() as conn:                       # an older database: no column yet
        conn.execute(text("ALTER TABLE games DROP COLUMN live_detail"))
    run_migrations(engine)
    assert "live_detail" in [c["name"] for c in inspect(engine).get_columns("games")]
    run_migrations(engine)                              # idempotent

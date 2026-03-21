from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

def get_engine(db_path: str):
    if db_path == ":memory:":
        engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
    else:
        engine = create_engine(f"sqlite:///{db_path}")

    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_connection, _):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return engine

def get_session(engine) -> Session:
    return Session(engine)


def migrate_api_usage(engine):
    """Drop old ApiUsage table if it has the legacy schema (source column)."""
    from sqlalchemy import inspect as sa_inspect, text
    inspector = sa_inspect(engine)
    if "api_usage" in inspector.get_table_names():
        columns = [c["name"] for c in inspector.get_columns("api_usage")]
        if "source" in columns and "endpoint" not in columns:
            with engine.begin() as conn:
                conn.execute(text("DROP TABLE api_usage"))


def migrate_game_start_time(engine):
    """Add start_time column to games table if missing."""
    from sqlalchemy import inspect as sa_inspect, text
    inspector = sa_inspect(engine)
    if "games" in inspector.get_table_names():
        columns = [c["name"] for c in inspector.get_columns("games")]
        if "start_time" not in columns:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE games ADD COLUMN start_time DATETIME"))


def migrate_player_stat_receptions(engine):
    """Add receptions column to player_stats table if missing."""
    from sqlalchemy import inspect as sa_inspect, text
    inspector = sa_inspect(engine)
    if "player_stats" in inspector.get_table_names():
        columns = [c["name"] for c in inspector.get_columns("player_stats")]
        if "receptions" not in columns:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE player_stats ADD COLUMN receptions FLOAT"))


def migrate_elo_history(engine):
    """Create elo_history table if missing."""
    from sqlalchemy import inspect as sa_inspect
    inspector = sa_inspect(engine)
    if "elo_history" not in inspector.get_table_names():
        from backend.models import EloHistory
        EloHistory.__table__.create(engine)


def migrate_parlays(engine):
    """Create parlays table and add parlay_id to paper_picks if missing."""
    from sqlalchemy import inspect as sa_inspect, text
    inspector = sa_inspect(engine)
    if "parlays" not in inspector.get_table_names():
        from backend.models import Parlay
        Parlay.__table__.create(engine)
    if "paper_picks" in inspector.get_table_names():
        columns = [c["name"] for c in inspector.get_columns("paper_picks")]
        if "parlay_id" not in columns:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE paper_picks ADD COLUMN parlay_id INTEGER REFERENCES parlays(id)"))

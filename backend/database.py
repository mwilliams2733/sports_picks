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


def migrate_pick_result_line_at_close(engine):
    """Add line_at_close column to pick_results if missing.

    Stores the closing spread or total line for spread/over_under bets so that
    line CLV can be computed (price CLV is meaningless for those bet types
    since the juice rarely moves but the line moves significantly).
    """
    from sqlalchemy import inspect as sa_inspect, text
    inspector = sa_inspect(engine)
    if "pick_results" in inspector.get_table_names():
        columns = [c["name"] for c in inspector.get_columns("pick_results")]
        if "line_at_close" not in columns:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE pick_results ADD COLUMN line_at_close FLOAT"))


def migrate_pick_model_prob(engine):
    """Add model_prob column to picks if missing.

    Persists the strategy's continuous probability output per pick so that
    finer-grained reliability diagrams can be built from real predicted-vs-
    actual data (rather than just the 5-tier confidence buckets). Strategies
    already compute the value (Pick.model_probability dataclass field); this
    migration adds the place to store it.
    """
    from sqlalchemy import inspect as sa_inspect, text
    inspector = sa_inspect(engine)
    if "picks" in inspector.get_table_names():
        columns = [c["name"] for c in inspector.get_columns("picks")]
        if "model_prob" not in columns:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE picks ADD COLUMN model_prob FLOAT"))

def migrate_pick_rationale(engine):
    """Add rationale_json column to picks if missing.

    Stores the structured PickFactor list that explains why a pick was made,
    so the daily digest can render a rationale without re-deriving (and
    possibly contradicting) the reasoning the strategy actually used.
    """
    from sqlalchemy import inspect as sa_inspect, text
    inspector = sa_inspect(engine)
    if "picks" in inspector.get_table_names():
        columns = [c["name"] for c in inspector.get_columns("picks")]
        if "rationale_json" not in columns:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE picks ADD COLUMN rationale_json TEXT"))


# The single source of truth for which schema migrations run, and in what
# order. Every process that opens the database — the FastAPI app AND the
# standalone pipeline scheduler — calls this instead of listing migrations
# itself, so a new migration cannot land in one entry point and be forgotten
# in the other. (It was: the scheduler ran only two of eight, so a
# pre-existing DB had no picks.rationale_json and every pick insert failed.)
MIGRATIONS = (
    migrate_api_usage,
    migrate_game_start_time,
    migrate_player_stat_receptions,
    migrate_elo_history,
    migrate_parlays,
    migrate_pick_result_line_at_close,
    migrate_pick_model_prob,
    migrate_pick_rationale,
)


def run_migrations(engine) -> None:
    """Apply every schema migration, then create any still-missing tables.

    Idempotent: each migration checks for its own column/table first, and
    `create_all` only creates what does not exist. Safe to call on every
    process start.
    """
    from backend.models import Base
    for migration in MIGRATIONS:
        migration(engine)
    Base.metadata.create_all(engine)

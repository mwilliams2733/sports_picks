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


def migrate_pick_prop_fields(engine):
    """Add prop_player/prop_market to picks if missing.

    Without these a prop pick cannot be graded: `grade_prop_pick` needs a
    market key and a player name, and `pick_value` is prose that does not map
    back to a market one-to-one.
    """
    from sqlalchemy import inspect as sa_inspect, text
    inspector = sa_inspect(engine)
    if "picks" in inspector.get_table_names():
        columns = [c["name"] for c in inspector.get_columns("picks")]
        with engine.begin() as conn:
            if "prop_player" not in columns:
                conn.execute(text("ALTER TABLE picks ADD COLUMN prop_player VARCHAR"))
            if "prop_market" not in columns:
                conn.execute(text("ALTER TABLE picks ADD COLUMN prop_market VARCHAR"))


def migrate_game_espn_id(engine):
    """Add espn_id to games if missing.

    Without it a game is identified by (sport, date, home, away), which breaks
    when the same game arrives under two date conventions: ESPN timestamps in
    UTC but files its scoreboard by Eastern date, so an evening game lands a
    day late and a twin row is created.
    """
    from sqlalchemy import inspect as sa_inspect, text
    inspector = sa_inspect(engine)
    if "games" in inspector.get_table_names():
        columns = [c["name"] for c in inspector.get_columns("games")]
        if "espn_id" not in columns:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE games ADD COLUMN espn_id VARCHAR"))


# The single source of truth for which schema migrations run, and in what
# order. Every process that opens the database — the FastAPI app AND the
# standalone pipeline scheduler — calls this instead of listing migrations
# itself, so a new migration cannot land in one entry point and be forgotten
# in the other. (It was: the scheduler ran only two of eight, so a
# pre-existing DB had no picks.rationale_json and every pick insert failed.)
def migrate_pick_odds_reconstructed(engine):
    """Add picks.odds_reconstructed if missing.

    Marks a pick whose `odds_at_pick` was recomputed after the fact rather
    than recorded at pick time. 34 ensemble moneyline picks from March 2026
    stored a value inside the invalid (-100, 100) band, because the consensus
    divided by every odds row rather than by the rows carrying a price. Those
    prices cannot be graded, so a win booked 0.0 payout and ROI was biased
    downward.

    The repair recomputes them from the surviving book rows, which is a
    reconstruction and not the price the pick was actually taken at. This
    column exists so that distinction survives in the data instead of living
    in a commit message.
    """
    from sqlalchemy import inspect as sa_inspect, text
    inspector = sa_inspect(engine)
    if "picks" in inspector.get_table_names():
        columns = [c["name"] for c in inspector.get_columns("picks")]
        if "odds_reconstructed" not in columns:
            with engine.begin() as conn:
                conn.execute(text(
                    "ALTER TABLE picks ADD COLUMN odds_reconstructed "
                    "BOOLEAN NOT NULL DEFAULT 0"))


def migrate_game_neutral_site(engine):
    """Add games.neutral_site if missing.

    A game at a neutral venue has no host, so `home_team_id` is a bracket or
    seed designation rather than a team playing at home. Without this column
    every such game teaches the model a home advantage that does not exist:
    all 72 ncaab final games in production are NCAA tournament games at
    neutral sites, and their 0.708 "home win rate" is higher seeds beating
    lower seeds.

    Existing rows default to 0. That is wrong for those 72 games until
    `backfill_neutral_site` runs, and deliberately so -- a migration that
    guessed would be indistinguishable from one that measured.
    """
    from sqlalchemy import inspect as sa_inspect, text
    inspector = sa_inspect(engine)
    if "games" in inspector.get_table_names():
        columns = [c["name"] for c in inspector.get_columns("games")]
        if "neutral_site" not in columns:
            with engine.begin() as conn:
                conn.execute(text(
                    "ALTER TABLE games ADD COLUMN neutral_site "
                    "BOOLEAN NOT NULL DEFAULT 0"))


def migrate_game_season_type(engine):
    """Add games.season_type if missing.

    Existing rows default to "unknown" rather than "regular": most of them
    are regular-season, but guessing would be indistinguishable from having
    measured, and the postseason rows are exactly the ones that matter.
    `backfill_game_flags` fills them from ESPN.
    """
    from sqlalchemy import inspect as sa_inspect, text
    inspector = sa_inspect(engine)
    if "games" in inspector.get_table_names():
        columns = [c["name"] for c in inspector.get_columns("games")]
        if "season_type" not in columns:
            with engine.begin() as conn:
                conn.execute(text(
                    "ALTER TABLE games ADD COLUMN season_type "
                    "VARCHAR NOT NULL DEFAULT 'unknown'"))


MIGRATIONS = (
    migrate_api_usage,
    migrate_game_start_time,
    migrate_player_stat_receptions,
    migrate_elo_history,
    migrate_parlays,
    migrate_pick_result_line_at_close,
    migrate_pick_model_prob,
    migrate_pick_rationale,
    migrate_pick_prop_fields,
    migrate_game_espn_id,
    migrate_pick_odds_reconstructed,
    migrate_game_neutral_site,
    migrate_game_season_type,
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

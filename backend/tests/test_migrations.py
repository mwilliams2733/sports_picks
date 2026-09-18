"""Guards for backend.database.run_migrations.

The standalone pipeline process (`python -m backend.pipeline.scheduler`) and
the web app are separate processes against the same file. Before this, each
listed its own migration calls and the scheduler listed only two of eight —
so a pre-existing database had no `picks.rationale_json`, and every pick
insert inside `_run_window` died inside a broad `except`. Both entry points
now derive their migration set from the same function.
"""
import inspect

from sqlalchemy import inspect as sa_inspect

from backend.database import get_engine, run_migrations
from backend.models import Base


def _columns(engine, table):
    return {c["name"] for c in sa_inspect(engine).get_columns(table)}


def test_run_migrations_adds_the_digest_columns_to_a_legacy_picks_table():
    """Simulate the real failure: a picks table created before this branch."""
    engine = get_engine(":memory:")
    with engine.begin() as conn:
        conn.exec_driver_sql(
            "CREATE TABLE picks ("
            " id INTEGER PRIMARY KEY, game_id INTEGER NOT NULL,"
            " strategy_id INTEGER NOT NULL, pick_type VARCHAR NOT NULL,"
            " pick_value VARCHAR NOT NULL, confidence INTEGER NOT NULL,"
            " edge_pct FLOAT NOT NULL, odds_at_pick INTEGER,"
            " created_at DATETIME NOT NULL)"
        )
    legacy = _columns(engine, "picks")
    assert "model_prob" not in legacy and "rationale_json" not in legacy

    run_migrations(engine)

    cols = _columns(engine, "picks")
    assert "model_prob" in cols
    assert "rationale_json" in cols


def test_create_all_alone_does_not_repair_a_legacy_picks_table():
    """Base.metadata.create_all is not a migration — this is why the bug bit."""
    engine = get_engine(":memory:")
    with engine.begin() as conn:
        conn.exec_driver_sql(
            "CREATE TABLE picks ("
            " id INTEGER PRIMARY KEY, game_id INTEGER NOT NULL,"
            " strategy_id INTEGER NOT NULL, pick_type VARCHAR NOT NULL,"
            " pick_value VARCHAR NOT NULL, confidence INTEGER NOT NULL,"
            " edge_pct FLOAT NOT NULL, odds_at_pick INTEGER,"
            " created_at DATETIME NOT NULL)"
        )
    Base.metadata.create_all(engine)
    assert "rationale_json" not in _columns(engine, "picks")


def test_run_migrations_is_idempotent():
    engine = get_engine(":memory:")
    run_migrations(engine)
    first = _columns(engine, "picks")
    run_migrations(engine)  # must not raise "duplicate column name"
    assert _columns(engine, "picks") == first


def test_pipeline_and_web_app_share_one_migration_path():
    """Derive, don't duplicate: neither entry point may list migrations itself."""
    from backend.api import main as api_main
    from backend.pipeline import scheduler as pipeline_scheduler

    api_src = inspect.getsource(api_main.create_app)
    sched_src = inspect.getsource(pipeline_scheduler.run_pipeline)

    assert "run_migrations(engine)" in api_src
    assert "run_migrations(engine)" in sched_src
    for src, who in ((api_src, "create_app"), (sched_src, "run_pipeline")):
        assert "migrate_" not in src, (
            f"{who} calls a migrate_* directly; add it to database.MIGRATIONS instead"
        )


def test_both_entry_points_produce_the_same_picks_columns():
    """The two processes' schemas cannot drift."""
    from backend.database import MIGRATIONS

    def _build():
        engine = get_engine(":memory:")
        for m in MIGRATIONS:
            m(engine)
        Base.metadata.create_all(engine)
        return _columns(engine, "picks")

    web = _build()
    pipeline = _build()
    assert web == pipeline
    assert {"model_prob", "rationale_json"} <= web


def test_run_migrations_adds_the_prop_grading_columns_to_a_legacy_picks_table():
    """Same failure shape as the digest columns above.

    Without a registered migration, a pre-existing database gets the new model
    attributes but not the columns, and every prop pick insert dies inside a
    broad `except`. `Base.metadata.create_all` does not repair an existing
    table -- that is the whole reason this file exists.
    """
    engine = get_engine(":memory:")
    with engine.begin() as conn:
        conn.exec_driver_sql(
            "CREATE TABLE picks ("
            " id INTEGER PRIMARY KEY, game_id INTEGER NOT NULL,"
            " strategy_id INTEGER NOT NULL, pick_type VARCHAR NOT NULL,"
            " pick_value VARCHAR NOT NULL, confidence INTEGER NOT NULL,"
            " edge_pct FLOAT NOT NULL, odds_at_pick INTEGER,"
            " created_at DATETIME NOT NULL)"
        )
    legacy = _columns(engine, "picks")
    assert "prop_player" not in legacy and "prop_market" not in legacy

    run_migrations(engine)

    cols = _columns(engine, "picks")
    assert "prop_player" in cols
    assert "prop_market" in cols


def test_run_migrations_adds_espn_id_to_a_legacy_games_table():
    """An unregistered migration gives a pre-existing database the model
    attribute but not the column, and every game insert then dies inside a
    broad `except`. That already happened once with picks.rationale_json.
    """
    engine = get_engine(":memory:")
    with engine.begin() as conn:
        conn.exec_driver_sql(
            "CREATE TABLE games ("
            " id INTEGER PRIMARY KEY, sport VARCHAR NOT NULL,"
            " season VARCHAR NOT NULL, date DATE NOT NULL,"
            " home_team_id INTEGER NOT NULL, away_team_id INTEGER NOT NULL,"
            " status VARCHAR NOT NULL)"
        )
    assert "espn_id" not in _columns(engine, "games")

    run_migrations(engine)

    assert "espn_id" in _columns(engine, "games")

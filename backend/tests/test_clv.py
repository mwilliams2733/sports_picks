"""The line_at_close migration.

The eight `capture_closing_odds` tests that used to live here were removed
on 2026-09-23. Each built its "closing snapshot" by writing TWO `dk` rows
for one game -- a state `_store_odds` cannot produce, because it upserts on
(game_id, bookmaker). They were encoding the very model that made
`capture_closing_odds` wrong: that `Odds` ordered by timestamp is a time
series, when it is one row per book.

Their cases are covered, against the real schema, by
test_capture_closing_odds.py and test_closing_consensus.py.
"""
from backend.database import get_engine


def test_migrate_pick_result_line_at_close_adds_column():
    """The line_at_close migration should add the column when missing and be idempotent."""
    from backend.database import migrate_pick_result_line_at_close
    from sqlalchemy import inspect as sa_inspect, text
    engine = get_engine(":memory:")
    # Build a pick_results table without line_at_close to simulate pre-migration state.
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE pick_results ("
            " id INTEGER PRIMARY KEY,"
            " pick_id INTEGER NOT NULL,"
            " result TEXT NOT NULL,"
            " payout REAL NOT NULL DEFAULT 0.0,"
            " odds_at_close INTEGER"
            ")"
        ))
    inspector = sa_inspect(engine)
    cols = [c["name"] for c in inspector.get_columns("pick_results")]
    assert "line_at_close" not in cols
    migrate_pick_result_line_at_close(engine)
    inspector = sa_inspect(engine)
    cols = [c["name"] for c in inspector.get_columns("pick_results")]
    assert "line_at_close" in cols
    # Idempotent on second call.
    migrate_pick_result_line_at_close(engine)

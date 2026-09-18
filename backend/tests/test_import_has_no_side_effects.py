"""Importing the web app must not touch a database.

`create_app` calls `run_migrations`, and a module-level
`app = create_app(...)` meant that importing ANY name from
`backend.api.main` migrated whatever `sports_picks.db` was in the process's
working directory. Production gained two columns that way during plan 010,
from nothing more than running pytest in the repo root.
"""
import os
import sqlite3
import subprocess
import sys
import textwrap

REPO_ROOT = __file__.rsplit("backend", 1)[0]

LEGACY_PICKS = (
    "CREATE TABLE picks ("
    " id INTEGER PRIMARY KEY, game_id INTEGER NOT NULL,"
    " strategy_id INTEGER NOT NULL, pick_type VARCHAR NOT NULL,"
    " pick_value VARCHAR NOT NULL, confidence INTEGER NOT NULL,"
    " edge_pct FLOAT NOT NULL, odds_at_pick INTEGER,"
    " created_at DATETIME NOT NULL)"
)


def _columns(db_path):
    con = sqlite3.connect(db_path)
    try:
        return {r[1] for r in con.execute("pragma table_info(picks)")}
    finally:
        con.close()


def _legacy_db(tmp_path):
    db = tmp_path / "sports_picks.db"
    con = sqlite3.connect(db)
    con.execute(LEGACY_PICKS)
    con.commit()
    con.close()
    return db


def _run_in(tmp_path, snippet):
    """Run `snippet` with tmp_path as the cwd, so a bare "sports_picks.db"
    resolves there rather than to the real one.

    A fresh process is essential: by the time this test runs,
    `backend.api.main` is already in `sys.modules` from a dozen other test
    files, so an in-process import would be a no-op and would pass against
    the bug.

    The environment is INHERITED with only DATABASE_PATH removed. A minimal
    env looks safer and is not: on Windows, `import asyncio` needs SystemRoot
    to initialise Winsock and dies with
    `OSError: [WinError 10106] The requested service provider could not be
    loaded or initialized`. Dropping DATABASE_PATH is what guarantees the
    subprocess falls back to the bare "sports_picks.db" in its cwd.
    """
    env = dict(os.environ)
    env["PYTHONPATH"] = REPO_ROOT
    env.pop("DATABASE_PATH", None)
    return subprocess.run(
        [sys.executable, "-c", textwrap.dedent(snippet)],
        cwd=str(tmp_path), capture_output=True, text=True, env=env,
    )


def test_importing_the_module_does_not_migrate_the_cwd_database(tmp_path):
    db = _legacy_db(tmp_path)
    assert "prop_player" not in _columns(db)

    proc = _run_in(tmp_path, """
        from backend.api.main import create_app   # exactly what the tests do
        print("imported", create_app is not None)
    """)

    assert proc.returncode == 0, proc.stderr
    assert "prop_player" not in _columns(db), (
        "importing backend.api.main migrated the database in the cwd")


def test_the_asgi_target_uvicorn_resolves_still_builds_an_app(tmp_path):
    """`uvicorn backend.api.main:app` must keep working. Five launch sites
    name that target, one of them a systemd unit on the server."""
    proc = _run_in(tmp_path, """
        from uvicorn.importer import import_from_string
        app = import_from_string("backend.api.main:app")
        print("TITLE:", app.title)
    """)

    assert proc.returncode == 0, proc.stderr
    assert "TITLE: Sports Picks API" in proc.stdout


def test_resolving_the_app_is_what_migrates_and_it_happens_once(tmp_path):
    """Constructing the app deliberately SHOULD migrate -- that is what an
    explicit request for a working app means. Repeated access must reuse the
    one instance rather than build a second engine."""
    db = _legacy_db(tmp_path)

    proc = _run_in(tmp_path, """
        import backend.api.main as m
        first, second = m.app, m.app
        print("SAME:", first is second)
    """)

    assert proc.returncode == 0, proc.stderr
    assert "SAME: True" in proc.stdout
    assert "prop_player" in _columns(db)      # resolving it DID migrate

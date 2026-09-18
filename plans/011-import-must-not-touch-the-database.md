# Plan 011: Importing `backend.api.main` must not touch a database

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan in
> `plans/README.md`.
>
> **For agentic workers:** REQUIRED SUB-SKILL: use
> `superpowers:subagent-driven-development` (recommended) or
> `superpowers:executing-plans`. Steps use checkbox (`- [ ]`) syntax.
>
> **Drift check (run first)**:
> `git diff --stat 9dc0877..HEAD -- backend/api/main.py backend/database.py backend/tests/test_migrations.py Dockerfile deploy/ start.sh`
> If any in-scope file changed since this plan was written, compare the
> "Current state" excerpts against the live code before proceeding; on a
> mismatch, treat it as a STOP condition.

**Goal:** Make `import backend.api.main` a pure import, so it cannot run
schema migrations against whatever `sports_picks.db` happens to be in the
current working directory.

**Architecture:** Delete the module-level `app = create_app(...)` and serve
`app` through a PEP 562 module `__getattr__` that constructs it on first
access. `uvicorn backend.api.main:app` resolves the attribute and keeps
working; importing any *other* name from the module — which every API test
does — stops touching the disk. `create_app` itself is unchanged and still
migrates, because explicit construction is a request for a working app.

**Tech Stack:** Python 3.14 local / 3.12 production, FastAPI, uvicorn,
SQLAlchemy 2.0, pytest.

**Spec:** none. Derived from finding 4 in `plans/HANDOFF.md`, discovered while
executing plan 010 Task 2. That section is the spec; read it first.

## Status

- **Priority**: P2 (latent; nothing is broken today)
- **Effort**: S
- **Risk**: LOW-MEDIUM — the change is small but it is on the ASGI entry point,
  so a mistake stops the server rather than failing a test
- **Depends on**: none
- **Category**: bug (foot-gun / blast radius)
- **Planned at**: commit `9dc0877`, 2026-09-17

## Global Constraints

- Python floor is `>=3.12` (`pyproject.toml`); CI runs **3.12 and 3.14** and
  both must pass. Module `__getattr__` is PEP 562, available since 3.7.
- **Do not change any launch site.** There are five, and one is a systemd unit
  on the server; an image that updates while the unit does not means the
  service will not start. Keeping `backend.api.main:app` valid is the whole
  point of the chosen approach.
- `create_app` must keep calling `run_migrations(engine)` **in its own body**.
  `test_migrations.py:test_pipeline_and_web_app_share_one_migration_path`
  asserts exactly that by reading `inspect.getsource(create_app)`.
- Tests must be mutation-proved: break the implementation and confirm the test
  fails before calling it a guard.

## Why this matters

`backend/api/main.py:128` is a module-level construction:

```python
app = create_app(os.environ.get("DATABASE_PATH", "sports_picks.db"))
```

`create_app`'s body calls `run_migrations(engine)`. Python executes a module's
body on first import, and **importing a single name from a module runs the
whole body** — so every one of the twelve test files that does
`from backend.api.main import create_app` migrates whatever `sports_picks.db`
sits in the process's working directory.

**Proven, not inferred.** Two columns were dropped from a copy of production,
the module was imported, and the columns came back:

```
before import, prop_player present: False
after  import, prop_player present: True
```

That is how production gained `picks.prop_player` during plan 010 with nobody
running a backfill: `pytest` from the repo root was enough.

**Today this is harmless.** Every entry in `database.MIGRATIONS` is additive
and idempotent, so the worst outcome so far is two unused nullable columns.

**It is one condition away from not being harmless.** `migrate_api_usage`
(`database.py:28-36`) does:

```python
if "source" in columns and "endpoint" not in columns:
    conn.execute(text("DROP TABLE api_usage"))
```

An `import` statement is therefore one schema shape away from dropping a
production table, with no command, no flag, and no confirmation. The container
is unaffected because `DATABASE_PATH` points at `/tmp`; a developer's machine
sitting in the repo root is not.

## Current state (verified 2026-09-17 at `9dc0877`)

`backend/api/main.py`, last line:

```python
app = create_app(os.environ.get("DATABASE_PATH", "sports_picks.db"))
```

Importers — all of them reach the module body:

```
backend/tests/test_api_backtest.py, test_api_credits.py, test_api_main.py,
test_api_picks.py, test_api_stats.py, test_api_users.py,
test_backtest_run_api.py, test_budget_alarm_and_redaction.py,
test_games_api.py, test_pipeline_api.py, test_pipeline_integration.py
    from backend.api.main import create_app
backend/tests/test_migrations.py:70
    from backend.api import main as api_main
```

Launch sites naming the ASGI target — **none of these may change**:

```
Dockerfile:44                      uvicorn backend.api.main:app
deploy/sports-picks-web.service:11 uvicorn backend.api.main:app
start.sh:14                        uvicorn backend.api.main:app
start-server.bat:3                 uvicorn backend.api.main:app
start-server-loop.bat:4            uvicorn backend.api.main:app
```

## Approach, and what was rejected

**Chosen: PEP 562 module `__getattr__`.** Verified against uvicorn's own
importer before this plan was written:

```
after plain import, side effects: []
after `from pkg.mod import create_app`: []
uvicorn import_from_string('pkg.mod:app') -> <app env.db>
after uvicorn resolves app: ['migrated env.db']
```

`uvicorn.importer.import_from_string` walks the target with `getattr`, so a
module `__getattr__` fires and every launch site keeps working untouched.

**Rejected — move `run_migrations` into the lifespan.** The natural-looking
fix, and wrong twice over. `TestClient` only runs lifespan inside a `with`
block, and this suite has **58 bare `TestClient(app)` calls against 4 inside
`with`** — so ~58 tests would hit an unmigrated database. It also breaks
`test_pipeline_and_web_app_share_one_migration_path`, which asserts
`run_migrations(engine)` appears in `create_app`'s source.

**Rejected — `uvicorn --factory`.** Clean and conventional, but it requires
editing all five launch sites including a remote systemd unit. The failure
mode is a server that will not start, and the coordination risk is much larger
than the bug being fixed.

**Rejected — require `DATABASE_PATH` with no default.** Safest in principle:
an import would raise instead of migrating. But it breaks local development
and every launch site that does not set the variable (four of the five), for a
problem the chosen fix solves without breaking anything.

## File structure

| File | Responsibility |
|---|---|
| `backend/api/main.py` | replace module-level construction with lazy `__getattr__` |
| `backend/tests/test_import_has_no_side_effects.py` | **new** — the regression guard |

---

### Task 1: Make the import pure

> **DONE 2026-09-17 — `3246256`.** 592 -> 595 passing, mutation-proved
> (re-adding the module-level line fails the guard). Step 7's by-hand check
> passed: `uvicorn backend.api.main:app` logged
> `Uvicorn running on http://127.0.0.1:8123` and `/health` returned
> `{"status":"ok"}`.
>
> The plan's predictions held exactly — the first test failed with its own
> message while the other two passed, and the final count was 595. No launch
> site was touched.


**Files:**
- Modify: `backend/api/main.py:128`
- Test: `backend/tests/test_import_has_no_side_effects.py` (new)

**Interfaces:**
- Consumes: nothing.
- Produces: `backend.api.main.app` continues to resolve to a `FastAPI`
  instance, constructed on first attribute access and cached thereafter.
  `create_app(db_path)` is unchanged in signature and behaviour.

- [ ] **Step 1: Write the failing test**

**This test must run the import in a subprocess.** By the time it executes,
`backend.api.main` is already in `sys.modules` from other test files, so an
in-process import would be a no-op and the test would pass against the bug —
the worst possible outcome for a regression guard.

Create `backend/tests/test_import_has_no_side_effects.py`:

```python
"""Importing the web app must not touch a database.

`create_app` calls `run_migrations`, and a module-level
`app = create_app(...)` meant that importing ANY name from
`backend.api.main` migrated whatever `sports_picks.db` was in the process's
working directory. Production gained two columns that way during plan 010,
from nothing more than running pytest in the repo root.
"""
import os
import subprocess
import sqlite3
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
    db = tmp_path / "sports_picks.db"
    con = sqlite3.connect(db)
    con.execute(LEGACY_PICKS)
    con.commit()
    con.close()
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
    db = tmp_path / "sports_picks.db"
    con = sqlite3.connect(db)
    con.execute(LEGACY_PICKS)
    con.commit()
    con.close()

    proc = _run_in(tmp_path, """
        import backend.api.main as m
        first, second = m.app, m.app
        print("SAME:", first is second)
    """)

    assert proc.returncode == 0, proc.stderr
    assert "SAME: True" in proc.stdout
    assert "prop_player" in _columns(db)      # resolving it DID migrate
```

- [ ] **Step 2: Run the tests and watch the first one fail**

```
.venv/Scripts/python.exe -m pytest backend/tests/test_import_has_no_side_effects.py -q
```

Expected: `test_importing_the_module_does_not_migrate_the_cwd_database`
**FAILS** with "importing backend.api.main migrated the database in the cwd".
The other two should pass already — they describe behaviour that must be
preserved, not behaviour being added.

**If the first test passes, stop.** Either the fix is already in, or the
subprocess is not reaching the repo's `backend` package — check
`proc.stderr`, and confirm `REPO_ROOT` resolves to the directory containing
`backend/`.

- [ ] **Step 3: Replace the module-level construction**

In `backend/api/main.py`, delete the last line:

```python
app = create_app(os.environ.get("DATABASE_PATH", "sports_picks.db"))
```

and put this in its place:

```python
#: The ASGI app, built on first access rather than at import.
_app: FastAPI | None = None


def __getattr__(name: str):
    """Construct the ASGI app only when something actually asks for it.

    ``uvicorn backend.api.main:app`` resolves the attribute, so all five
    launch sites keep working unchanged (`Dockerfile:44`,
    `deploy/sports-picks-web.service:11`, `start.sh:14`, `start-server.bat:3`,
    `start-server-loop.bat:4`).

    Importing any *other* name no longer touches the disk. It used to:
    ``create_app`` runs ``run_migrations``, Python executes a module body on
    first import, and every API test does
    ``from backend.api.main import create_app`` -- so ``pytest`` in the repo
    root migrated the real ``sports_picks.db``. Harmless while every migration
    is additive, but ``migrate_api_usage`` contains a ``DROP TABLE``.

    PEP 562. Only called for names not already defined in the module.
    """
    if name == "app":
        global _app
        if _app is None:
            _app = create_app(os.environ.get("DATABASE_PATH", "sports_picks.db"))
        return _app
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
```

- [ ] **Step 4: Run the tests and watch all three pass**

```
.venv/Scripts/python.exe -m pytest backend/tests/test_import_has_no_side_effects.py -q
```

Expected: PASS, 3 tests.

- [ ] **Step 5: Run the full suite**

```
.venv/Scripts/python.exe -m pytest backend/tests -q
```

Expected: **595 passing** (592 + 3), 0 failed. In particular
`test_migrations.py` must still pass — `create_app` is untouched, so
`test_pipeline_and_web_app_share_one_migration_path` still finds
`run_migrations(engine)` in its source.

- [ ] **Step 6: Mutation-prove the guard**

Temporarily restore the module-level line alongside the new `__getattr__`,
run the file again, and confirm
`test_importing_the_module_does_not_migrate_the_cwd_database` **fails**. Then
remove it. A guard you have not watched fail is not a guard — and this one is
especially easy to fool, because the module is already imported by the time
most of the suite runs.

- [ ] **Step 7: Prove the server still starts, by hand**

The tests resolve the ASGI target through uvicorn's importer, which is the same
lookup uvicorn performs — but they do not bind a port. Do this once:

```
.venv/Scripts/python.exe -m uvicorn backend.api.main:app --port 8123
```

Confirm it logs `Uvicorn running on http://127.0.0.1:8123`, then
`curl http://127.0.0.1:8123/health` returns `{"status":"ok"}`, then stop it.
**This is the step that protects the five launch sites**; do not skip it
because the unit tests are green.

- [ ] **Step 8: Commit**

```bash
git add backend/api/main.py backend/tests/test_import_has_no_side_effects.py
git commit -m "fix(api): build the ASGI app on access, not at import"
```

---

## STOP conditions

Stop and report; do not improvise.

1. **The first test passes before the fix.** The subprocess is probably not
   importing the repo's `backend` package, so the test proves nothing. Check
   `proc.stderr` before changing any production code. Verified while writing
   this plan: with the environment inherited, the harness reproduces the bug
   (`before: False` / `after: True`).
2. **The subprocess exits non-zero with `WinError 10106`.** The environment
   was stripped instead of inherited — `import asyncio` needs SystemRoot on
   Windows. See `_run_in`; do not "simplify" it back to a minimal env.
3. **`uvicorn backend.api.main:app` fails to start in Step 7.** Revert
   immediately. Five launch sites depend on that target and one of them is a
   systemd unit you cannot conveniently test from here.
4. **You are about to edit `Dockerfile`, `deploy/`, `start.sh`, or a `.bat`
   file.** The chosen approach exists specifically to avoid that. If it seems
   necessary, the approach is wrong — report instead.
5. **You are about to move `run_migrations` out of `create_app`.** A test pins
   it there, and the lifespan alternative breaks ~58 bare `TestClient` calls.
   See "Approach, and what was rejected".
6. **The full suite drops below 592 passing** at any point.

## Verification

Baseline before starting: **592 passing**, 0 failed, on 3.12 and 3.14.

```
.venv/Scripts/python.exe -m pytest backend/tests -q
```

Final state should be 595 passing, CI green on both Python versions, and
`uvicorn backend.api.main:app` serving `/health`.

## Out of scope

- **`backend/pipeline/scheduler.py:115` also calls `run_migrations`**, but from
  inside `run_pipeline`, a function — not at module level. Importing the
  scheduler is already pure. Left alone.
- **`create_app`'s `db_path` default of `"sports_picks.db"`.** Still a sharp
  edge: an explicit `create_app()` with no argument migrates the cwd database.
  That is at least a deliberate call rather than an import, and no caller does
  it today (the tests all pass `":memory:"`). Tightening it to require a path
  is a separate, wider change.
- **Making the migrations themselves safe to re-run against a wrong database.**
  `migrate_api_usage`'s `DROP TABLE` is the actual sharp object here, and
  guarding it — or requiring an explicit opt-in for destructive migrations —
  would reduce the blast radius of every future mistake, not just this one.
  Worth its own plan.

# Credit Budget & Smart Scheduler Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the Odds API key to `.env`, enforce a 20K credit/month budget with tracking, and replace the fixed 6 AM cron with a dynamic game-window scheduler that runs ~2 hours before tip-off.

**Architecture:** Config loads API key from `.env` via `python-dotenv`. A new per-call `ApiUsage` table tracks every Odds API request. Budget enforcement happens in pipeline functions (not the HTTP client). A morning scout at 8 AM ET fetches today's schedule, clusters games into time windows, and schedules one-shot pipeline runs ~2 hours before each window.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy, APScheduler, python-dotenv, React/TypeScript frontend

**Spec:** `docs/superpowers/specs/2026-03-17-credit-budget-and-smart-scheduler-design.md`

---

## Task 1: Environment Config — Move API Key to `.env`

**Files:**
- Create: `.env`
- Create: `.env.example`
- Modify: `config.yaml`
- Modify: `pyproject.toml:5-18`
- Modify: `backend/config.py:1-6`
- Test: `backend/tests/test_config.py`

- [ ] **Step 1: Write failing test for env-based config loading**

```python
# backend/tests/test_config.py — add new test
def test_load_config_reads_odds_api_key_from_env(tmp_path, monkeypatch):
    """Config should read ODDS_API_KEY from env, not from yaml."""
    f = tmp_path / "c.yaml"
    f.write_text('database_path: "d.db"\nseasons:\n  nba:\n    start: "10-22"\n    end: "06-20"\nodds_budget:\n  monthly_limit: 20000\n  daily_target: 600\n  reserve: 2000\n')
    monkeypatch.setenv("ODDS_API_KEY", "env_test_key")
    cfg = load_config(str(f))
    assert cfg["odds_api_key"] == "env_test_key"


def test_load_config_no_env_key_returns_none(tmp_path, monkeypatch):
    """Config should return None for odds_api_key if env var is not set."""
    f = tmp_path / "c.yaml"
    f.write_text('database_path: "d.db"\nseasons:\n  nba:\n    start: "10-22"\n    end: "06-20"\nodds_budget:\n  monthly_limit: 20000\n  daily_target: 600\n  reserve: 2000\n')
    monkeypatch.delenv("ODDS_API_KEY", raising=False)
    cfg = load_config(str(f))
    assert cfg.get("odds_api_key") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest backend/tests/test_config.py -v`
Expected: FAIL — `load_config` still reads from yaml, not env

- [ ] **Step 3: Add `python-dotenv` dependency**

In `pyproject.toml`, add `"python-dotenv>=1.0.0"` to the `dependencies` list (after `"pyyaml>=6.0"`).

- [ ] **Step 4: Install the new dependency**

Run: `pip install python-dotenv`

- [ ] **Step 5: Update `backend/config.py` to load from `.env`**

```python
import os
from datetime import date
import yaml
from dotenv import load_dotenv

load_dotenv(override=False)  # reads .env file if present; env vars take precedence

def load_config(path: str) -> dict:
    with open(path) as f:
        config = yaml.safe_load(f)
    # Inject API key from environment (never from yaml)
    api_key = os.environ.get("ODDS_API_KEY")
    if api_key:
        config["odds_api_key"] = api_key
    return config

def is_sport_in_season(sport: str, seasons: dict, today: date | None = None) -> bool:
    today = today or date.today()
    season = seasons.get(sport)
    if not season:
        return False
    start_month, start_day = map(int, season["start"].split("-"))
    end_month, end_day = map(int, season["end"].split("-"))
    start = date(today.year, start_month, start_day)
    end = date(today.year, end_month, end_day)
    if start <= end:
        return start <= today <= end
    else:
        return today >= start or today <= end
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `python -m pytest backend/tests/test_config.py -v`
Expected: All PASS

- [ ] **Step 7: Update the existing test to not rely on yaml key**

Update `test_load_config` in `backend/tests/test_config.py` — the yaml no longer has `odds_api_key`, so the existing assertion `assert cfg["odds_api_key"] == "k"` should be removed. Replace the test:

```python
def test_load_config(tmp_path):
    f = tmp_path / "c.yaml"
    f.write_text('database_path: "d.db"\nseasons:\n  nba:\n    start: "10-22"\n    end: "06-20"\nodds_budget:\n  monthly_limit: 20000\n  daily_target: 600\n  reserve: 2000\n')
    cfg = load_config(str(f))
    assert cfg["database_path"] == "d.db"
    assert cfg["odds_budget"]["monthly_limit"] == 20000
```

- [ ] **Step 8: Create `.env` and `.env.example` files**

`.env` (this file is gitignored — contains real key):
```
ODDS_API_KEY=<copy your key from the current config.yaml before modifying it>
```

`.env.example` (this file IS committed — template):
```
ODDS_API_KEY=your_odds_api_key_here
```

- [ ] **Step 9: Remove `odds_api_key` from `config.yaml` and update budget**

```yaml
database_path: "sports_picks.db"

seasons:
  nba:
    start: "10-22"
    end: "06-20"
  nfl:
    start: "09-05"
    end: "02-10"
  ncaab:
    start: "11-04"
    end: "04-08"
  ncaaf:
    start: "08-24"
    end: "01-20"
  boxing:
    start: "01-01"
    end: "12-31"
  mma:
    start: "01-01"
    end: "12-31"

odds_budget:
  monthly_limit: 20000
  daily_target: 600
  reserve: 2000
```

- [ ] **Step 10: Run full test suite to check nothing broke**

Run: `python -m pytest backend/tests/ -v`
Expected: All existing tests pass (some may need yaml fixture updates — fix any that reference `odds_api_key` in yaml)

- [ ] **Step 11: Commit**

Note: `config.yaml` is in `.gitignore` (it used to contain the API key). Now that the key is in `.env`, the yaml is safe to commit. Use `git add -f` for config.yaml:

```bash
git add .env.example pyproject.toml backend/config.py backend/tests/test_config.py
git add -f config.yaml
git commit -m "feat: move Odds API key to .env, add python-dotenv"
```

---

## Task 2: Replace `ApiUsage` Model + Add `Game.start_time`

**Files:**
- Modify: `backend/models.py:19-30` (Game), `backend/models.py:154-160` (ApiUsage)
- Create: `backend/exceptions.py`
- Modify: `backend/database.py` (migration helper)
- Test: `backend/tests/test_new_tables.py` (or create new test file)

- [ ] **Step 1: Write failing tests for new ApiUsage model and Game.start_time**

Create `backend/tests/test_api_usage.py`:

```python
from datetime import datetime, timezone
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from backend.models import Base, ApiUsage, Game, Team


def make_session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return Session(engine), engine


def test_api_usage_has_endpoint_column():
    session, _ = make_session()
    row = ApiUsage(endpoint="events", sport="nba", credits_used=1, created_at=datetime.now(tz=timezone.utc))
    session.add(row)
    session.commit()
    assert row.id is not None
    assert row.endpoint == "events"


def test_api_usage_has_requests_remaining():
    session, _ = make_session()
    row = ApiUsage(endpoint="player_props", sport="nba", credits_used=1,
                   requests_remaining=19500, created_at=datetime.now(tz=timezone.utc))
    session.add(row)
    session.commit()
    assert row.requests_remaining == 19500


def test_api_usage_created_at_index():
    """The created_at column should have an index for efficient budget queries."""
    _, engine = make_session()
    indexes = inspect(engine).get_indexes("api_usage")
    indexed_cols = [col for idx in indexes for col in idx["column_names"]]
    assert "created_at" in indexed_cols


def test_game_has_start_time():
    session, _ = make_session()
    team1 = Team(name="Team A", abbreviation="A", sport="nba")
    team2 = Team(name="Team B", abbreviation="B", sport="nba")
    session.add_all([team1, team2])
    session.flush()
    game = Game(
        sport="nba", season="2026-2027", date=datetime(2026, 3, 17).date(),
        home_team_id=team1.id, away_team_id=team2.id, status="scheduled",
        start_time=datetime(2026, 3, 17, 23, 30, tzinfo=timezone.utc),
    )
    session.add(game)
    session.commit()
    assert game.start_time == datetime(2026, 3, 17, 23, 30, tzinfo=timezone.utc)


def test_game_start_time_nullable():
    session, _ = make_session()
    team1 = Team(name="Team C", abbreviation="C", sport="nba")
    team2 = Team(name="Team D", abbreviation="D", sport="nba")
    session.add_all([team1, team2])
    session.flush()
    game = Game(
        sport="nba", season="2026-2027", date=datetime(2026, 3, 17).date(),
        home_team_id=team1.id, away_team_id=team2.id, status="scheduled",
    )
    session.add(game)
    session.commit()
    assert game.start_time is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest backend/tests/test_api_usage.py -v`
Expected: FAIL — `ApiUsage` has no `endpoint` column, `Game` has no `start_time`

- [ ] **Step 3: Update `ApiUsage` model in `backend/models.py`**

Replace lines 154-160:

```python
class ApiUsage(Base):
    __tablename__ = "api_usage"
    id = Column(Integer, primary_key=True)
    endpoint = Column(String, nullable=False)  # "odds", "events", "player_props"
    sport = Column(String, nullable=False)
    credits_used = Column(Integer, nullable=False, default=1)
    requests_remaining = Column(Integer, nullable=True)
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(tz=timezone.utc), index=True)
```

- [ ] **Step 4: Add `start_time` to `Game` model in `backend/models.py`**

Add after line 25 (`date = Column(Date, ...)`):

```python
    start_time = Column(DateTime, nullable=True)
```

- [ ] **Step 5: Create `backend/exceptions.py`**

```python
class BudgetExhaustedError(Exception):
    """Raised when the Odds API credit budget is exhausted."""
    def __init__(self, monthly_used: int, monthly_limit: int, daily_used: int):
        self.monthly_used = monthly_used
        self.monthly_limit = monthly_limit
        self.daily_used = daily_used
        super().__init__(
            f"Budget exhausted: {monthly_used}/{monthly_limit} monthly, {daily_used} today"
        )
```

- [ ] **Step 6: Add migration helpers to `backend/database.py`**

Add these functions after `get_session`:

```python
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
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `python -m pytest backend/tests/test_api_usage.py -v`
Expected: All PASS

- [ ] **Step 7b: Write and run migration tests**

Add to `backend/tests/test_api_usage.py`:

```python
from sqlalchemy import text, inspect
from backend.database import migrate_api_usage, migrate_game_start_time


def test_migrate_api_usage_drops_old_schema():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    # Create old schema manually
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE api_usage (id INTEGER PRIMARY KEY, source TEXT, request_count INTEGER, month TEXT, updated_at DATETIME)"
        ))
    migrate_api_usage(engine)
    # Old table should be dropped
    assert "api_usage" not in inspect(engine).get_table_names()
    # Now create_all should recreate with new schema
    Base.metadata.create_all(engine)
    columns = [c["name"] for c in inspect(engine).get_columns("api_usage")]
    assert "endpoint" in columns
    assert "source" not in columns


def test_migrate_game_start_time_adds_column():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    # Create games table without start_time
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE games (id INTEGER PRIMARY KEY, sport TEXT, season TEXT, date DATE, "
            "home_team_id INTEGER, away_team_id INTEGER, status TEXT)"
        ))
    migrate_game_start_time(engine)
    columns = [c["name"] for c in inspect(engine).get_columns("games")]
    assert "start_time" in columns
```

Run: `python -m pytest backend/tests/test_api_usage.py -v`
Expected: All PASS

- [ ] **Step 8: Run full test suite**

Run: `python -m pytest backend/tests/ -v`
Expected: All pass. The old `test_budget.py` tests will likely fail because they use the old `ApiUsage` schema and `ApiBudgetTracker`. That's expected — we'll update those in Task 3.

- [ ] **Step 9: Commit**

```bash
git add backend/models.py backend/exceptions.py backend/database.py backend/tests/test_api_usage.py
git commit -m "feat: replace ApiUsage model, add Game.start_time, add BudgetExhaustedError"
```

---

## Task 3: Budget Gate + Credit Tracking in Pipeline

**Files:**
- Modify: `backend/collectors/budget.py` (rewrite)
- Modify: `backend/pipeline/full_pipeline.py:1-11,57-91,94-132`
- Test: `backend/tests/test_budget.py` (rewrite)

- [ ] **Step 1: Write failing tests for new budget gate**

Rewrite `backend/tests/test_budget.py`:

```python
from datetime import datetime, date, timezone, timedelta
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from backend.models import Base, ApiUsage
from backend.collectors.budget import check_budget, record_api_call, get_credit_summary, BudgetStatus


def make_session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return Session(engine)


def test_check_budget_allows_when_under_limit():
    session = make_session()
    budget = {"monthly_limit": 20000, "daily_target": 600, "reserve": 2000}
    status = check_budget(session, budget)
    assert status == BudgetStatus.OK


def test_check_budget_blocks_at_monthly_limit():
    session = make_session()
    budget = {"monthly_limit": 5, "daily_target": 600, "reserve": 2}
    for i in range(5):
        session.add(ApiUsage(endpoint="odds", sport="nba", credits_used=1,
                             created_at=datetime.now(tz=timezone.utc)))
    session.commit()
    status = check_budget(session, budget)
    assert status == BudgetStatus.MONTHLY_EXHAUSTED


def test_check_budget_warns_at_daily_target():
    session = make_session()
    budget = {"monthly_limit": 20000, "daily_target": 3, "reserve": 2000}
    for i in range(3):
        session.add(ApiUsage(endpoint="odds", sport="nba", credits_used=1,
                             created_at=datetime.now(tz=timezone.utc)))
    session.commit()
    status = check_budget(session, budget)
    assert status == BudgetStatus.DAILY_SOFT_LIMIT


def test_check_budget_blocks_daily_when_in_reserve():
    session = make_session()
    budget = {"monthly_limit": 100, "daily_target": 3, "reserve": 50}
    # 55 monthly calls — within reserve zone
    for i in range(55):
        session.add(ApiUsage(endpoint="odds", sport="nba", credits_used=1,
                             created_at=datetime.now(tz=timezone.utc)))
    session.commit()
    status = check_budget(session, budget)
    assert status == BudgetStatus.RESERVE_EXHAUSTED


def test_record_api_call_creates_row():
    session = make_session()
    record_api_call(session, "events", "nba", requests_remaining=19999)
    rows = session.query(ApiUsage).all()
    assert len(rows) == 1
    assert rows[0].endpoint == "events"
    assert rows[0].sport == "nba"
    assert rows[0].requests_remaining == 19999


def test_get_credit_summary():
    session = make_session()
    budget = {"monthly_limit": 20000, "daily_target": 600, "reserve": 2000}
    record_api_call(session, "events", "nba", requests_remaining=19999)
    record_api_call(session, "player_props", "nba", requests_remaining=19998)
    summary = get_credit_summary(session, budget)
    assert summary["monthly_used"] == 2
    assert summary["monthly_limit"] == 20000
    assert summary["monthly_remaining"] == 19998
    assert summary["daily_used"] == 2
    assert summary["daily_target"] == 600
    assert summary["api_requests_remaining"] == 19998
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest backend/tests/test_budget.py -v`
Expected: FAIL — old module doesn't have these functions

- [ ] **Step 3: Rewrite `backend/collectors/budget.py`**

```python
import enum
import logging
from datetime import datetime, date, timezone
from sqlalchemy import func
from sqlalchemy.orm import Session
from backend.models import ApiUsage

logger = logging.getLogger(__name__)

DEFAULT_BUDGET = {"monthly_limit": 20000, "daily_target": 600, "reserve": 2000}


class BudgetStatus(enum.Enum):
    OK = "ok"
    DAILY_SOFT_LIMIT = "daily_soft_limit"
    RESERVE_EXHAUSTED = "reserve_exhausted"
    MONTHLY_EXHAUSTED = "monthly_exhausted"


def check_budget(session: Session, budget: dict) -> BudgetStatus:
    """Check if we can make another Odds API call."""
    monthly_limit = budget["monthly_limit"]
    daily_target = budget["daily_target"]
    reserve = budget["reserve"]

    now = datetime.now(tz=timezone.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    monthly_used = session.query(func.sum(ApiUsage.credits_used)).filter(
        ApiUsage.created_at >= month_start
    ).scalar() or 0

    if monthly_used >= monthly_limit:
        return BudgetStatus.MONTHLY_EXHAUSTED

    daily_used = session.query(func.sum(ApiUsage.credits_used)).filter(
        ApiUsage.created_at >= today_start
    ).scalar() or 0

    monthly_remaining = monthly_limit - monthly_used
    if daily_used >= daily_target and monthly_remaining <= reserve:
        return BudgetStatus.RESERVE_EXHAUSTED

    if daily_used >= daily_target:
        return BudgetStatus.DAILY_SOFT_LIMIT

    return BudgetStatus.OK


def record_api_call(session: Session, endpoint: str, sport: str,
                    requests_remaining: int | None = None) -> None:
    """Record a single Odds API call."""
    session.add(ApiUsage(
        endpoint=endpoint, sport=sport, credits_used=1,
        requests_remaining=requests_remaining,
        created_at=datetime.now(tz=timezone.utc),
    ))
    session.commit()
    logger.info(f"API credit used: {endpoint}/{sport} (remaining: {requests_remaining})")


def get_credit_summary(session: Session, budget: dict) -> dict:
    """Get credit usage summary for API response / UI."""
    now = datetime.now(tz=timezone.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    monthly_used = session.query(func.sum(ApiUsage.credits_used)).filter(
        ApiUsage.created_at >= month_start
    ).scalar() or 0

    daily_used = session.query(func.sum(ApiUsage.credits_used)).filter(
        ApiUsage.created_at >= today_start
    ).scalar() or 0

    latest = session.query(ApiUsage).order_by(ApiUsage.created_at.desc()).first()
    api_remaining = latest.requests_remaining if latest else None

    return {
        "monthly_used": monthly_used,
        "monthly_limit": budget["monthly_limit"],
        "monthly_remaining": budget["monthly_limit"] - monthly_used,
        "daily_used": daily_used,
        "daily_target": budget["daily_target"],
        "api_requests_remaining": api_remaining,
    }
```

- [ ] **Step 4: Run budget tests to verify they pass**

Run: `python -m pytest backend/tests/test_budget.py -v`
Expected: All PASS

- [ ] **Step 5: Update `full_pipeline.py` — add budget gate + `start_time` storage + window filtering**

Modify `backend/pipeline/full_pipeline.py`. Key changes:

1. Add imports at top:
```python
from backend.collectors.budget import check_budget, record_api_call, BudgetStatus
from backend.exceptions import BudgetExhaustedError
```

2. Add `_parse_start_time` helper next to `_parse_date`:
```python
def _parse_start_time(date_str: str) -> datetime:
    """Parse ISO datetime string to full UTC datetime."""
    return datetime.fromisoformat(date_str.replace("Z", "+00:00"))
```

3. Update `_store_games` — store `start_time` when creating a game. After `game_date = _parse_date(g["date"])` add:
```python
        start_time = _parse_start_time(g["date"])
```
And in the `Game(...)` constructor, add `start_time=start_time`.
Also update existing games' `start_time` if missing (inside the `if existing:` block):
```python
            if existing.start_time is None:
                existing.start_time = _parse_start_time(g["date"])
```

4. Update `fetch_and_store_odds` — add budget gate. New signature:
```python
async def fetch_and_store_odds(session: Session, sports: list[str], api_key: str,
                                budget: dict | None = None) -> int:
```
Before calling `collector.fetch_odds(sport)`, check budget:
```python
                if budget:
                    status = check_budget(session, budget)
                    if status == BudgetStatus.MONTHLY_EXHAUSTED:
                        raise BudgetExhaustedError(...)
```
After the call, record usage:
```python
                record_api_call(session, "odds", sport, collector.requests_remaining)
```

5. Update `fetch_and_store_props` — add budget gate + window filtering. New signature:
```python
async def fetch_and_store_props(session: Session, sports: list[str], api_key: str,
                                 budget: dict | None = None,
                                 window_game_ids: set[int] | None = None) -> int:
```
Before `collector.fetch_events`, check budget. After, record usage.
Before `collector.fetch_player_props`, check budget AND check window filter:
```python
                    game = _find_game_for_event(session, sport, event)
                    if not game:
                        continue
                    if window_game_ids is not None and game.id not in window_game_ids:
                        continue
                    # Budget check before expensive prop call
                    if budget:
                        status = check_budget(session, budget)
                        if status in (BudgetStatus.MONTHLY_EXHAUSTED, BudgetStatus.RESERVE_EXHAUSTED):
                            logger.warning(f"Budget limit reached, stopping prop fetch")
                            break
                    props = await collector.fetch_player_props(sport, event_id)
                    record_api_call(session, "player_props", sport, collector.requests_remaining)
```

- [ ] **Step 6: Run all tests**

Run: `python -m pytest backend/tests/ -v`
Expected: All pass

- [ ] **Step 7: Commit**

```bash
git add backend/collectors/budget.py backend/pipeline/full_pipeline.py backend/tests/test_budget.py
git commit -m "feat: add budget gate and credit tracking to pipeline functions"
```

---

## Task 4: Credits API Endpoint

**Files:**
- Create: `backend/api/credits.py`
- Modify: `backend/api/main.py:18-33`
- Test: `backend/tests/test_api_credits.py`

- [ ] **Step 1: Write failing test**

Create `backend/tests/test_api_credits.py`:

```python
from fastapi.testclient import TestClient
from backend.api.main import create_app
from backend.models import Base


def test_credits_endpoint_returns_summary():
    app = create_app(":memory:")
    Base.metadata.create_all(app.state.engine)
    client = TestClient(app)
    resp = client.get("/credits/")
    assert resp.status_code == 200
    data = resp.json()
    assert "monthly_used" in data
    assert "monthly_limit" in data
    assert "monthly_remaining" in data
    assert "daily_used" in data
    assert "daily_target" in data
    assert data["monthly_used"] == 0
    assert data["monthly_limit"] == 20000
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest backend/tests/test_api_credits.py -v`
Expected: FAIL — 404, route doesn't exist

- [ ] **Step 3: Create `backend/api/credits.py`**

```python
from fastapi import APIRouter, Request
from backend.config import load_config
from backend.database import get_session
from backend.collectors.budget import get_credit_summary, DEFAULT_BUDGET

router = APIRouter()


@router.get("/")
def get_credits(request: Request):
    """Return current API credit usage for the month and today."""
    session = get_session(request.app.state.engine)
    try:
        config = load_config("config.yaml")
        budget = config.get("odds_budget", DEFAULT_BUDGET)
        return get_credit_summary(session, budget)
    finally:
        session.close()
```

- [ ] **Step 4: Register router in `backend/api/main.py`**

Add after the `pipeline_router` import block:

```python
    from backend.api.credits import router as credits_router
    app.include_router(credits_router, prefix="/credits", tags=["credits"])
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest backend/tests/test_api_credits.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/api/credits.py backend/api/main.py backend/tests/test_api_credits.py
git commit -m "feat: add GET /credits endpoint for budget visibility"
```

---

## Task 5: Fix Pipeline API — Add `run_prop_pipeline` + Sport Filter + Credit Info

**Files:**
- Modify: `backend/api/pipeline_api.py`
- Modify: `backend/tests/test_pipeline_api.py`

- [ ] **Step 1: Write failing test for prop pipeline integration and sport filter**

Add to `backend/tests/test_pipeline_api.py`:

```python
def test_pipeline_run_returns_credit_info():
    app = create_app(":memory:")
    Base.metadata.create_all(app.state.engine)
    client = TestClient(app)
    resp = client.post("/pipeline/run")
    assert resp.status_code == 200
    data = resp.json()
    # New fields should be present
    assert "credits_used" in data
    assert "credits_remaining_month" in data
    assert "props_analyzed" in data


def test_pipeline_run_with_sport_filter():
    app = create_app(":memory:")
    Base.metadata.create_all(app.state.engine)
    client = TestClient(app)
    resp = client.post("/pipeline/run?sport=nba")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] in ("completed", "error")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest backend/tests/test_pipeline_api.py -v`
Expected: FAIL — missing fields in response

- [ ] **Step 3: Rewrite `backend/api/pipeline_api.py`**

```python
import logging
from datetime import date
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from backend.config import load_config, is_sport_in_season
from backend.database import get_session
from backend.pipeline.full_pipeline import (
    fetch_and_store_games, fetch_and_store_odds, fetch_and_store_props, ALL_SPORTS,
)
from backend.pipeline.pick_generator import generate_and_store_picks
from backend.pipeline.prop_pipeline import run_prop_pipeline
from backend.collectors.budget import get_credit_summary, DEFAULT_BUDGET
from backend.exceptions import BudgetExhaustedError
from backend.models import StrategyModel

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/run")
async def trigger_pipeline(
    request: Request,
    sport: str | None = None,
    window_start: str | None = None,
    window_end: str | None = None,
):
    """Run the pipeline: fetch games, odds, props, generate picks.

    Optional filters:
    - sport: only fetch for this sport (e.g., ?sport=nba)
    - window_start/window_end: ISO datetimes to scope a time window
    """
    session = get_session(request.app.state.engine)
    try:
        config = load_config("config.yaml")
        today = date.today()
        budget = config.get("odds_budget", DEFAULT_BUDGET)

        if sport:
            active_sports = [sport]
        else:
            active_sports = [s for s in ALL_SPORTS if is_sport_in_season(s, config["seasons"], today)]
        logger.info(f"Pipeline: active sports = {active_sports}")

        # Build window game filter if time window specified
        window_game_ids = None
        if window_start or window_end:
            from datetime import datetime as dt
            from backend.models import Game
            query = session.query(Game.id).filter(Game.date == today)
            if sport:
                query = query.filter(Game.sport == sport)
            if window_start:
                ws = dt.fromisoformat(window_start)
                query = query.filter(Game.start_time >= ws)
            if window_end:
                we = dt.fromisoformat(window_end)
                query = query.filter(Game.start_time <= we)
            window_game_ids = {row[0] for row in query.all()}

        # Step 1: Fetch and store games from ESPN
        games_stored = await fetch_and_store_games(session, active_sports, today)

        # Step 2: Fetch and store odds + props from The Odds API
        odds_stored = 0
        props_stored = 0
        api_key = config.get("odds_api_key")
        if api_key:
            odds_stored = await fetch_and_store_odds(session, active_sports, api_key, budget=budget)
            props_stored = await fetch_and_store_props(
                session, active_sports, api_key, budget=budget,
                window_game_ids=window_game_ids,
            )

        # Step 3: Generate game picks
        game_picks = 0
        game_strategy = session.query(StrategyModel).filter(
            StrategyModel.is_active == True,
            StrategyModel.strategy_type == "game",
        ).first()
        if game_strategy:
            game_picks = generate_and_store_picks(session, game_strategy.id, today)

        # Step 4: Run prop pipeline (THE BUG FIX)
        props_analyzed = 0
        prop_picks = 0
        prop_strategy = session.query(StrategyModel).filter(
            StrategyModel.is_active == True,
            StrategyModel.strategy_type == "prop",
        ).first()
        try:
            prop_result = await run_prop_pipeline(
                session, target_date=today,
                strategy_id=prop_strategy.id if prop_strategy else None,
            )
            props_analyzed = prop_result.get("props_analyzed", 0)
            prop_picks = prop_result.get("picks_generated", 0)
        except Exception as e:
            logger.error(f"Prop pipeline error: {e}")

        summary = get_credit_summary(session, budget)

        return {
            "status": "completed",
            "active_sports": active_sports,
            "games_stored": games_stored,
            "odds_stored": odds_stored,
            "props_stored": props_stored,
            "props_analyzed": props_analyzed,
            "picks_generated": game_picks + prop_picks,
            "credits_used": summary["daily_used"],
            "credits_remaining_today": summary["daily_target"] - summary["daily_used"],
            "credits_remaining_month": summary["monthly_remaining"],
        }
    except BudgetExhaustedError as e:
        summary = get_credit_summary(session, budget)
        return JSONResponse(
            status_code=429,
            content={"status": "budget_exhausted", "message": str(e), **summary},
        )
    except Exception as e:
        logger.error(f"Pipeline error: {e}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content={"status": "error", "message": str(e)},
        )
    finally:
        session.close()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest backend/tests/test_pipeline_api.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add backend/api/pipeline_api.py backend/tests/test_pipeline_api.py
git commit -m "fix: add run_prop_pipeline to API, add sport filter and credit info"
```

---

## Task 6: Smart Scheduler — Morning Scout + Window System

**Files:**
- Modify: `backend/pipeline/scheduler.py` (full rewrite)
- Test: `backend/tests/test_scheduler.py` (new)

- [ ] **Step 1: Write tests for window clustering and scout logic**

Create `backend/tests/test_scheduler.py`:

```python
from datetime import datetime, timezone, timedelta
from backend.pipeline.scheduler import cluster_game_windows


def _make_game(hour, minute=0):
    """Helper: create a mock game dict with start_time."""
    return {
        "id": hash((hour, minute)),
        "start_time": datetime(2026, 3, 17, hour, minute, tzinfo=timezone.utc),
    }


def test_cluster_single_window():
    """All games within 30 min → one window."""
    games = [_make_game(23, 0), _make_game(23, 10), _make_game(23, 30)]
    windows = cluster_game_windows(games)
    assert len(windows) == 1
    assert len(windows[0]["games"]) == 3


def test_cluster_two_windows():
    """Games >30 min apart → separate windows."""
    games = [_make_game(17, 0), _make_game(17, 15), _make_game(23, 0), _make_game(23, 30)]
    windows = cluster_game_windows(games)
    assert len(windows) == 2
    assert len(windows[0]["games"]) == 2
    assert len(windows[1]["games"]) == 2


def test_cluster_empty_list():
    windows = cluster_game_windows([])
    assert windows == []


def test_window_run_time_is_2_hours_before():
    """Each window's run_at should be ~2 hours before the earliest game."""
    games = [_make_game(23, 0), _make_game(23, 30)]
    windows = cluster_game_windows(games)
    expected_run = datetime(2026, 3, 17, 21, 0, tzinfo=timezone.utc)
    assert windows[0]["run_at"] == expected_run
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest backend/tests/test_scheduler.py -v`
Expected: FAIL — `cluster_game_windows` doesn't exist

- [ ] **Step 3: Rewrite `backend/pipeline/scheduler.py`**

```python
import asyncio
import logging
import time
from datetime import date, datetime, timezone, timedelta
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from backend.config import load_config, is_sport_in_season
from backend.database import get_engine, get_session, migrate_api_usage
from backend.pipeline.full_pipeline import (
    fetch_and_store_games, fetch_and_store_odds, fetch_and_store_props, ALL_SPORTS,
)
from backend.pipeline.pick_generator import generate_and_store_picks
from backend.pipeline.prop_pipeline import run_prop_pipeline
from backend.pipeline.grader import grade_pick, grade_prop_pick
from backend.collectors.budget import get_credit_summary
from backend.models import (
    Base, Game, Odds, PickModel, PickResult, StrategyModel,
    PaperPick, UserProfile, PlayerStat,
)
from backend.analysis.odds_utils import calculate_payout

logger = logging.getLogger(__name__)
ET = ZoneInfo("America/New_York")
LEAD_TIME = timedelta(hours=2)


def cluster_game_windows(games: list[dict], gap_minutes: int = 30) -> list[dict]:
    """Group games into time windows by start_time proximity.

    Sort by start_time, then greedily cluster: start a new window when a
    game's start_time exceeds the first game in the current window by more
    than gap_minutes.

    Returns list of {"games": [...], "run_at": datetime, "window_start": datetime}.
    """
    if not games:
        return []

    sorted_games = sorted(games, key=lambda g: g["start_time"])
    gap = timedelta(minutes=gap_minutes)
    windows = []
    current = [sorted_games[0]]
    window_anchor = sorted_games[0]["start_time"]

    for g in sorted_games[1:]:
        if g["start_time"] - window_anchor > gap:
            windows.append(_build_window(current))
            current = [g]
            window_anchor = g["start_time"]
        else:
            current.append(g)

    windows.append(_build_window(current))
    return windows


def _build_window(games: list[dict]) -> dict:
    earliest = min(g["start_time"] for g in games)
    return {
        "games": games,
        "run_at": earliest - LEAD_TIME,
        "window_start": earliest,
        "window_end": max(g["start_time"] for g in games),
    }


def run_pipeline(config_path: str = "config.yaml"):
    config = load_config(config_path)
    engine = get_engine(config["database_path"])
    migrate_api_usage(engine)
    Base.metadata.create_all(engine)

    scheduler = BackgroundScheduler(timezone=ET)

    # Morning scout at 8 AM ET
    scheduler.add_job(
        lambda: morning_scout(config, engine, scheduler),
        'cron', hour=8, minute=0, id='morning_scout',
        replace_existing=True,
    )

    # Scout retry fallbacks at 9 AM and 10 AM ET (only run if 8 AM failed)
    # These are registered but the morning_scout removes them on success
    scheduler.add_job(
        lambda: morning_scout(config, engine, scheduler, is_retry=True),
        'cron', hour=9, minute=0, id='scout_retry_9',
        replace_existing=True,
    )
    scheduler.add_job(
        lambda: morning_scout(config, engine, scheduler, is_retry=True),
        'cron', hour=10, minute=0, id='scout_retry_10',
        replace_existing=True,
    )

    # Recalibration at 3 AM ET (unchanged)
    from backend.pipeline.recalibration_job import run_recalibration
    scheduler.add_job(
        lambda: run_recalibration(config["database_path"]),
        'cron', hour=3, minute=0, id='recalibration',
        replace_existing=True,
    )

    scheduler.start()
    logger.info("Scheduler started (morning scout at 8 AM ET, recalibration at 3 AM ET)")
    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        scheduler.shutdown()


def morning_scout(config, engine, scheduler, is_retry=False):
    """Fetch today's schedule and schedule window runs."""
    session = get_session(engine)
    try:
        # Grade pending picks first
        grade_pending_picks(session)

        active_sports = [s for s in ALL_SPORTS if is_sport_in_season(s, config["seasons"])]
        scheduled_sports = [s for s in active_sports if s in ("nba", "nfl")]

        if not scheduled_sports:
            logger.info("No auto-scheduled sports in season today")
            return

        today = date.today()

        # Fetch games from ESPN so we have start_times
        try:
            asyncio.run(fetch_and_store_games(session, scheduled_sports, today))
        except Exception as e:
            if is_retry:
                logger.error(f"Scout retry failed fetching games: {e}")
                return
            logger.warning(f"Scout failed fetching games: {e}, will retry at 9/10 AM")
            return

        # Scout succeeded — remove retry jobs so they don't fire unnecessarily
        for retry_id in ("scout_retry_9", "scout_retry_10"):
            try:
                scheduler.remove_job(retry_id)
            except Exception:
                pass

        # Check if window jobs already exist (from earlier scout run)
        existing_jobs = {j.id for j in scheduler.get_jobs()}

        for sport in scheduled_sports:
            games = session.query(Game).filter(
                Game.sport == sport,
                Game.date == today,
                Game.status == "scheduled",
                Game.start_time.isnot(None),
            ).all()

            if not games:
                logger.info(f"No {sport} games scheduled for today, no windows created")
                continue

            game_dicts = [{"id": g.id, "start_time": g.start_time} for g in games]
            windows = cluster_game_windows(game_dicts)

            for i, window in enumerate(windows):
                job_id = f"window_{sport}_{today}_{i}"
                if job_id in existing_jobs:
                    continue

                run_at = window["run_at"]
                now_utc = datetime.now(tz=timezone.utc)

                # If run_at is in the past, run immediately
                if run_at <= now_utc:
                    logger.info(f"Window {job_id} run_at is past, running now")
                    _run_window(config, engine, sport, window)
                else:
                    game_ids = [g["id"] for g in window["games"]]
                    run_at_et = run_at.astimezone(ET)
                    scheduler.add_job(
                        lambda c=config, e=engine, s=sport, w=window: _run_window(c, e, s, w),
                        'date', run_date=run_at_et, id=job_id,
                        replace_existing=True,
                    )
                    earliest_et = window["window_start"].astimezone(ET)
                    n_games = len(window["games"])
                    logger.info(
                        f"Scheduled {sport} window: {n_games} games tipping off "
                        f"~{earliest_et.strftime('%I:%M %p')} ET, pipeline run at "
                        f"{run_at_et.strftime('%I:%M %p')} ET"
                    )
    except Exception as e:
        logger.error(f"Morning scout error: {e}", exc_info=True)
    finally:
        session.close()


def _run_window(config, engine, sport: str, window: dict):
    """Execute a pipeline run for a specific game window."""
    session = get_session(engine)
    try:
        today = date.today()
        from backend.collectors.budget import DEFAULT_BUDGET
        budget = config.get("odds_budget", DEFAULT_BUDGET)
        api_key = config.get("odds_api_key")
        window_game_ids = {g["id"] for g in window["games"]}

        logger.info(f"Running {sport} window: {len(window_game_ids)} games")

        if api_key:
            asyncio.run(fetch_and_store_odds(session, [sport], api_key, budget=budget))
            asyncio.run(fetch_and_store_props(
                session, [sport], api_key, budget=budget,
                window_game_ids=window_game_ids,
            ))

        # Generate game picks
        game_strategy = session.query(StrategyModel).filter(
            StrategyModel.is_active == True,
            StrategyModel.strategy_type == "game",
        ).first()
        if game_strategy:
            count = generate_and_store_picks(session, game_strategy.id, today)
            logger.info(f"Generated {count} game picks")

        # Run prop analysis
        prop_strategy = session.query(StrategyModel).filter(
            StrategyModel.is_active == True,
            StrategyModel.strategy_type == "prop",
        ).first()
        try:
            result = asyncio.run(run_prop_pipeline(
                session, target_date=today,
                strategy_id=prop_strategy.id if prop_strategy else None,
            ))
            logger.info(f"Prop pipeline: {result}")
        except Exception as e:
            logger.error(f"Prop pipeline error: {e}")

        summary = get_credit_summary(session, budget)
        logger.info(f"Window complete. Credits today: {summary['daily_used']}, month: {summary['monthly_used']}/{summary['monthly_limit']}")

    except Exception as e:
        logger.error(f"Window run error ({sport}): {e}", exc_info=True)
    finally:
        session.close()


def grade_pending_picks(session):
    """Grade completed game and paper picks. Moved from old daily_job."""
    ungraded = (
        session.query(PickModel, Game)
        .join(Game, PickModel.game_id == Game.id)
        .filter(Game.status == "final")
        .filter(~PickModel.id.in_(session.query(PickResult.pick_id)))
        .all()
    )
    for pick, game in ungraded:
        if game.home_score is not None and game.away_score is not None:
            result, payout = grade_pick(pick.pick_type, pick.pick_value,
                game.home_score, game.away_score, pick.odds_at_pick or -110)

            closing_odds_val = None
            closing_odds_row = (
                session.query(Odds)
                .filter(Odds.game_id == game.id)
                .order_by(Odds.timestamp.desc())
                .first()
            )
            if closing_odds_row:
                if pick.pick_type == "moneyline":
                    closing_odds_val = closing_odds_row.moneyline_home if "HOME" in pick.pick_value else closing_odds_row.moneyline_away
                elif pick.pick_type in ("spread", "over_under"):
                    closing_odds_val = -110
                elif pick.pick_type == "prop":
                    closing_odds_val = closing_odds_row.moneyline_home

            session.add(PickResult(
                pick_id=pick.id, result=result, payout=payout,
                odds_at_close=closing_odds_val
            ))
    session.commit()
    logger.info(f"Graded {len(ungraded)} strategy picks")

    # Grade pending PaperPicks
    pending_paper = (
        session.query(PaperPick, Game)
        .join(Game, PaperPick.game_id == Game.id)
        .filter(PaperPick.result.is_(None))
        .filter(Game.status == "final")
        .all()
    )
    paper_graded = 0
    for pick, game in pending_paper:
        if game.home_score is None or game.away_score is None:
            continue

        if pick.pick_type == "prop" and pick.prop_player and pick.prop_market:
            player_stat = (
                session.query(PlayerStat)
                .filter_by(player_name=pick.prop_player, stat_type="game_log", game_date=game.date)
                .first()
            )
            prop_result = grade_prop_pick(pick.pick_value, pick.prop_market, player_stat)
            if not prop_result:
                continue
            pick.result = prop_result[0]
        else:
            result, _ = grade_pick(
                pick.pick_type, pick.pick_value,
                game.home_score, game.away_score, pick.odds
            )
            pick.result = result

        if pick.result == "win":
            pick.payout = pick.stake * calculate_payout(pick.odds)
        elif pick.result == "push":
            pick.payout = 0.0
        else:
            pick.payout = -pick.stake

        pick.graded_at = datetime.now(timezone.utc) if hasattr(pick, 'graded_at') else None
        paper_graded += 1

    session.commit()
    logger.info(f"Auto-graded {paper_graded} paper picks")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_pipeline()
```

- [ ] **Step 4: Run scheduler tests to verify they pass**

Run: `python -m pytest backend/tests/test_scheduler.py -v`
Expected: All PASS

- [ ] **Step 5: Run full test suite**

Run: `python -m pytest backend/tests/ -v`
Expected: All pass

- [ ] **Step 6: Commit**

```bash
git add backend/pipeline/scheduler.py backend/tests/test_scheduler.py
git commit -m "feat: replace fixed cron with morning scout + game window scheduler"
```

---

## Task 7: Frontend — Credit Usage Widget

**Files:**
- Create: `frontend/src/components/CreditUsage.tsx`
- Modify: `frontend/src/api/client.ts:87-91`
- Modify: `frontend/src/pages/TodaysPicks.tsx` (mount widget)

- [ ] **Step 1: Add credits API to client**

In `frontend/src/api/client.ts`, add to the `api` object after `pipeline`:

```typescript
  credits: {
    get: () => get<{
      monthly_used: number; monthly_limit: number; monthly_remaining: number;
      daily_used: number; daily_target: number;
      api_requests_remaining: number | null;
    }>('/credits/'),
  },
```

Also update the `pipeline.run` type to include new fields:

```typescript
  pipeline: {
    run: (sport?: string) => post<{
      status: string; active_sports: string[];
      games_stored: number; odds_stored: number; props_stored: number;
      props_analyzed: number; picks_generated: number;
      credits_used: number; credits_remaining_today: number;
      credits_remaining_month: number;
    }>(`/pipeline/run${sport ? `?sport=${sport}` : ''}`, {}),
  },
```

- [ ] **Step 2: Create `frontend/src/components/CreditUsage.tsx`**

```tsx
import { useEffect, useState } from 'react';
import { api } from '../api/client';

interface CreditData {
  monthly_used: number;
  monthly_limit: number;
  monthly_remaining: number;
  daily_used: number;
  daily_target: number;
  api_requests_remaining: number | null;
}

export default function CreditUsage() {
  const [data, setData] = useState<CreditData | null>(null);

  useEffect(() => {
    api.credits.get().then(setData).catch(() => {});
  }, []);

  if (!data) return null;

  const monthPct = Math.round((data.monthly_used / data.monthly_limit) * 100);
  const dayPct = Math.round((data.daily_used / data.daily_target) * 100);

  return (
    <div style={{
      display: 'flex', gap: '1rem', alignItems: 'center',
      padding: '0.5rem 1rem', fontSize: '0.8rem', color: '#94a3b8',
      borderTop: '1px solid #1e293b',
    }}>
      <span title="Monthly API credits used">
        Month: {data.monthly_used.toLocaleString()}/{data.monthly_limit.toLocaleString()} ({monthPct}%)
      </span>
      <span title="Today's API credits used">
        Today: {data.daily_used}/{data.daily_target}
      </span>
      {data.api_requests_remaining !== null && (
        <span title="Credits remaining per Odds API">
          API: {data.api_requests_remaining.toLocaleString()} left
        </span>
      )}
    </div>
  );
}
```

- [ ] **Step 3: Mount widget in `TodaysPicks.tsx`**

Add import at top:
```tsx
import CreditUsage from '../components/CreditUsage';
```

Add `<CreditUsage />` at the bottom of the page's JSX, before the closing `</div>` or fragment.

- [ ] **Step 4: Build frontend to verify no compilation errors**

Run: `cd frontend && npm run build`
Expected: Build succeeds

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/CreditUsage.tsx frontend/src/api/client.ts frontend/src/pages/TodaysPicks.tsx
git commit -m "feat: add CreditUsage widget to frontend"
```

---

## Task 8: Call `migrate_api_usage` in App Startup

**Files:**
- Modify: `backend/api/main.py:6-10`

- [ ] **Step 1: Add migration call in `create_app`**

In `backend/api/main.py`, after `engine = get_engine(db_path)` and before `Base.metadata.create_all(engine)`:

```python
    from backend.database import migrate_api_usage, migrate_game_start_time
    migrate_api_usage(engine)
    migrate_game_start_time(engine)
```

- [ ] **Step 2: Run full test suite to verify nothing broke**

Run: `python -m pytest backend/tests/ -v`
Expected: All pass

- [ ] **Step 3: Commit**

```bash
git add backend/api/main.py
git commit -m "feat: add ApiUsage migration check on app startup"
```

---

## Task 9: Integration Test — End-to-End Pipeline Run

**Files:**
- Create: `backend/tests/test_pipeline_integration.py`

- [ ] **Step 1: Write integration test**

```python
"""Integration test: verify the full pipeline flow with budget tracking."""
from datetime import datetime, date, timezone
from fastapi.testclient import TestClient
from backend.api.main import create_app
from backend.models import Base, Team, Game, ApiUsage
from backend.database import get_session


def test_full_pipeline_with_credits():
    """Pipeline run should return credit info and not crash."""
    app = create_app(":memory:")
    Base.metadata.create_all(app.state.engine)
    client = TestClient(app)

    # Run pipeline (no games, no API key in env — should complete gracefully)
    resp = client.post("/pipeline/run")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "completed"
    assert "credits_used" in data
    assert "credits_remaining_month" in data
    assert "props_analyzed" in data


def test_pipeline_with_sport_filter():
    app = create_app(":memory:")
    Base.metadata.create_all(app.state.engine)
    client = TestClient(app)

    resp = client.post("/pipeline/run?sport=nba")
    assert resp.status_code == 200
    data = resp.json()
    assert data["active_sports"] == ["nba"]


def test_credits_endpoint_after_usage():
    app = create_app(":memory:")
    engine = app.state.engine
    Base.metadata.create_all(engine)

    # Manually insert some usage
    session = get_session(engine)
    session.add(ApiUsage(
        endpoint="events", sport="nba", credits_used=1,
        requests_remaining=19999, created_at=datetime.now(tz=timezone.utc),
    ))
    session.commit()
    session.close()

    client = TestClient(app)
    resp = client.get("/credits/")
    assert resp.status_code == 200
    data = resp.json()
    assert data["monthly_used"] == 1
    assert data["api_requests_remaining"] == 19999
```

- [ ] **Step 2: Run integration tests**

Run: `python -m pytest backend/tests/test_pipeline_integration.py -v`
Expected: All PASS

- [ ] **Step 3: Run full test suite one final time**

Run: `python -m pytest backend/tests/ -v`
Expected: All pass

- [ ] **Step 4: Commit**

```bash
git add backend/tests/test_pipeline_integration.py
git commit -m "test: add end-to-end pipeline integration tests with credit tracking"
```

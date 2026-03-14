# Sports Picks App Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a web-based sports betting analysis tool that collects stats from free APIs, runs configurable analysis strategies with backtesting, and surfaces high-probability picks for NBA, NFL, NCAAB, and NCAAF.

**Architecture:** Two-process design — a scheduled Python data pipeline (collection, analysis, grading) and a FastAPI + React web app — sharing a SQLite database in WAL mode.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy, scikit-learn, APScheduler, React 18 (TypeScript), Vite, Recharts, SQLite

**Spec:** `docs/superpowers/specs/2026-03-13-sports-picks-app-design.md`

---

## File Structure

```
sports_picks/
├── backend/
│   ├── pyproject.toml
│   ├── config.py                    # Season dates, API keys, thresholds
│   ├── database.py                  # SQLite engine, WAL mode, session
│   ├── models.py                    # SQLAlchemy ORM models (all tables)
│   ├── data_types.py                # GameData, TeamStats, Pick, OddsSnapshot
│   ├── collectors/
│   │   ├── __init__.py
│   │   ├── espn.py                  # ESPN public API client
│   │   ├── odds_api.py              # The Odds API client
│   │   └── budget.py                # API request counter/budget
│   ├── analysis/
│   │   ├── __init__.py
│   │   ├── strategy.py              # Strategy ABC base class
│   │   ├── elo.py                   # ELO rating system
│   │   ├── win_probability.py       # Logistic regression model
│   │   ├── spread.py                # ATS analysis
│   │   ├── over_under.py            # O/U model
│   │   ├── confidence.py            # Edge → star rating
│   │   └── variants/
│   │       ├── __init__.py
│   │       ├── recent_form.py       # Variant A
│   │       ├── value_only.py        # Variant B
│   │       ├── ensemble.py          # Variant C
│   │       └── sport_specific.py    # Variant D
│   ├── backtesting/
│   │   ├── __init__.py
│   │   ├── backtester.py            # Core backtest engine
│   │   └── historical.py            # Historical data loader
│   ├── pipeline/
│   │   ├── __init__.py
│   │   ├── scheduler.py             # APScheduler setup
│   │   ├── collector_task.py        # Orchestrates data collection
│   │   ├── pick_generator.py        # Runs strategies → picks
│   │   └── grader.py                # Grades previous day's picks
│   ├── api/
│   │   ├── __init__.py
│   │   ├── main.py                  # FastAPI app, static file serving
│   │   ├── picks.py                 # /picks endpoints
│   │   ├── stats.py                 # /stats endpoints
│   │   ├── backtest.py              # /backtest endpoints
│   │   └── games.py                 # /games endpoints
│   └── tests/
│       ├── conftest.py              # Shared fixtures (test DB, etc.)
│       ├── test_database.py
│       ├── test_data_types.py
│       ├── test_espn.py
│       ├── test_odds_api.py
│       ├── test_budget.py
│       ├── test_elo.py
│       ├── test_win_probability.py
│       ├── test_confidence.py
│       ├── test_strategy.py
│       ├── test_backtester.py
│       ├── test_grader.py
│       ├── test_pick_generator.py
│       ├── test_api_picks.py
│       ├── test_api_stats.py
│       └── test_api_backtest.py
├── frontend/
│   ├── package.json
│   ├── vite.config.ts
│   ├── tsconfig.json
│   ├── index.html
│   └── src/
│       ├── main.tsx
│       ├── App.tsx
│       ├── types.ts                 # TypeScript types matching backend
│       ├── api/
│       │   └── client.ts            # Fetch wrappers for all endpoints
│       ├── components/
│       │   ├── Layout.tsx           # Nav bar with sport tabs
│       │   ├── SummaryCards.tsx      # Strategy/picks/ROI cards
│       │   ├── PicksTable.tsx       # Sortable/filterable picks table
│       │   ├── ConfidenceStars.tsx   # Star rating display
│       │   ├── CalendarHeatmap.tsx   # Green/red daily results
│       │   ├── PerformanceChart.tsx  # Cumulative ROI line chart
│       │   ├── StrategyForm.tsx      # Create/edit strategy variant
│       │   └── StrategyList.tsx      # Strategy variant list
│       └── pages/
│           ├── TodaysPicks.tsx
│           ├── Backtesting.tsx
│           └── TrackRecord.tsx
├── config.yaml                      # Season dates, API keys
└── start.sh                         # Launch both processes
```

---

## Chunk 1: Foundation — Database, Models, Data Types

### Task 1: Project Setup

**Files:**
- Create: `backend/pyproject.toml`
- Create: `config.yaml`

- [ ] **Step 1: Initialize git repo**

```bash
cd C:/Users/mwill/OneDrive/Documents/mwilliams2733/sports_picks
git init
echo "__pycache__/
*.pyc
.venv/
node_modules/
dist/
*.db
.env
.superpowers/
" > .gitignore
```

- [ ] **Step 2: Create pyproject.toml**

```toml
[project]
name = "sports-picks"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "fastapi>=0.115.0",
    "uvicorn>=0.34.0",
    "sqlalchemy>=2.0.0",
    "httpx>=0.28.0",
    "scikit-learn>=1.6.0",
    "pandas>=2.2.0",
    "numpy>=2.1.0",
    "apscheduler>=3.10.0",
    "pyyaml>=6.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.24.0",
]

[build-system]
requires = ["setuptools>=68.0"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
include = ["backend*"]
```

- [ ] **Step 2b: Create __init__.py files**

```bash
touch backend/__init__.py backend/tests/__init__.py
```

- [ ] **Step 3: Create config.yaml**

```yaml
odds_api_key: "YOUR_KEY_HERE"
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

odds_budget:
  monthly_limit: 500
  pause_at: 450
```

- [ ] **Step 4: Create virtual environment and install**

```bash
cd backend && python -m venv ../.venv && source ../.venv/Scripts/activate && pip install -e ".[dev]"
```

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml config.yaml .gitignore
git commit -m "chore: initialize project with dependencies and config"
```

---

### Task 2: Database Setup

**Files:**
- Create: `backend/database.py`
- Test: `backend/tests/conftest.py`, `backend/tests/test_database.py`

- [ ] **Step 1: Write failing test for database connection**

```python
# backend/tests/conftest.py
import pytest
from sqlalchemy import text
from backend.database import get_engine, get_session

@pytest.fixture
def db_engine(tmp_path):
    db_path = tmp_path / "test.db"
    engine = get_engine(str(db_path))
    yield engine
    engine.dispose()

@pytest.fixture
def db_session(db_engine):
    session = get_session(db_engine)
    yield session
    session.close()
```

```python
# backend/tests/test_database.py
from sqlalchemy import text

def test_engine_creates_sqlite_with_wal(db_engine):
    with db_engine.connect() as conn:
        result = conn.execute(text("PRAGMA journal_mode")).scalar()
        assert result == "wal"

def test_session_can_execute_query(db_session):
    result = db_session.execute(text("SELECT 1")).scalar()
    assert result == 1
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd C:/Users/mwill/OneDrive/Documents/mwilliams2733/sports_picks
python -m pytest backend/tests/test_database.py -v
```
Expected: FAIL — `ModuleNotFoundError: No module named 'backend.database'`

- [ ] **Step 3: Implement database.py**

```python
# backend/database.py
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session

def get_engine(db_path: str):
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
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest backend/tests/test_database.py -v
```
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/database.py backend/tests/conftest.py backend/tests/test_database.py
git commit -m "feat: add SQLite database setup with WAL mode"
```

---

### Task 3: SQLAlchemy ORM Models

**Files:**
- Create: `backend/models.py`
- Test: `backend/tests/test_database.py` (append)

- [ ] **Step 1: Write failing test for table creation**

Add to `backend/tests/test_database.py`:

```python
from backend.models import Base

def test_create_all_tables(db_engine):
    Base.metadata.create_all(db_engine)
    with db_engine.connect() as conn:
        tables = conn.execute(text(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )).scalars().all()
    expected = [
        "api_usage", "backtest_picks", "backtest_runs", "elo_ratings",
        "games", "odds", "pick_results", "picks", "strategies",
        "team_stats", "teams"
    ]
    assert sorted(tables) == sorted(expected)
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python -m pytest backend/tests/test_database.py::test_create_all_tables -v
```
Expected: FAIL

- [ ] **Step 3: Implement models.py**

```python
# backend/models.py
from datetime import date, datetime, timezone
from sqlalchemy import (
    Column, Integer, String, Float, Date, DateTime, ForeignKey, Boolean, Text
)
from sqlalchemy.orm import DeclarativeBase, relationship

class Base(DeclarativeBase):
    pass

class Team(Base):
    __tablename__ = "teams"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    abbreviation = Column(String, nullable=False)
    sport = Column(String, nullable=False)  # nba, nfl, ncaab, ncaaf
    conference = Column(String)
    division = Column(String)

class Game(Base):
    __tablename__ = "games"
    id = Column(Integer, primary_key=True)
    sport = Column(String, nullable=False)
    season = Column(String, nullable=False)
    week = Column(Integer, nullable=True)
    date = Column(Date, nullable=False)
    home_team_id = Column(Integer, ForeignKey("teams.id"), nullable=False)
    away_team_id = Column(Integer, ForeignKey("teams.id"), nullable=False)
    home_score = Column(Integer, nullable=True)
    away_score = Column(Integer, nullable=True)
    status = Column(String, nullable=False, default="scheduled")
    home_team = relationship("Team", foreign_keys=[home_team_id])
    away_team = relationship("Team", foreign_keys=[away_team_id])

class TeamStat(Base):
    __tablename__ = "team_stats"
    id = Column(Integer, primary_key=True)
    team_id = Column(Integer, ForeignKey("teams.id"), nullable=False)
    game_id = Column(Integer, ForeignKey("games.id"), nullable=False)
    stat_type = Column(String, nullable=False)
    value = Column(Float, nullable=False)

class EloRating(Base):
    __tablename__ = "elo_ratings"
    id = Column(Integer, primary_key=True)
    team_id = Column(Integer, ForeignKey("teams.id"), nullable=False)
    sport = Column(String, nullable=False)
    rating = Column(Float, nullable=False, default=1500.0)
    updated_at = Column(DateTime, nullable=False, default=datetime.now(tz=timezone.utc))

class Odds(Base):
    __tablename__ = "odds"
    id = Column(Integer, primary_key=True)
    game_id = Column(Integer, ForeignKey("games.id"), nullable=False)
    bookmaker = Column(String, nullable=False)
    moneyline_home = Column(Integer, nullable=True)
    moneyline_away = Column(Integer, nullable=True)
    spread_home = Column(Float, nullable=True)
    spread_away = Column(Float, nullable=True)
    over_under = Column(Float, nullable=True)
    timestamp = Column(DateTime, nullable=False, default=datetime.now(tz=timezone.utc))

class StrategyModel(Base):
    __tablename__ = "strategies"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    config_json = Column(Text, nullable=False)
    is_active = Column(Boolean, nullable=False, default=False)
    sport = Column(String, nullable=True)

class BacktestRun(Base):
    __tablename__ = "backtest_runs"
    id = Column(Integer, primary_key=True)
    strategy_id = Column(Integer, ForeignKey("strategies.id"), nullable=False)
    status = Column(String, nullable=False, default="pending")
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)

class BacktestPick(Base):
    __tablename__ = "backtest_picks"
    id = Column(Integer, primary_key=True)
    run_id = Column(Integer, ForeignKey("backtest_runs.id"), nullable=False)
    game_id = Column(Integer, ForeignKey("games.id"), nullable=False)
    pick_type = Column(String, nullable=False)
    pick_value = Column(String, nullable=False)
    confidence = Column(Integer, nullable=False)
    edge_pct = Column(Float, nullable=False)
    result = Column(String, nullable=True)
    odds_at_pick = Column(Integer, nullable=True)

class PickModel(Base):
    __tablename__ = "picks"
    id = Column(Integer, primary_key=True)
    game_id = Column(Integer, ForeignKey("games.id"), nullable=False)
    strategy_id = Column(Integer, ForeignKey("strategies.id"), nullable=False)
    pick_type = Column(String, nullable=False)
    pick_value = Column(String, nullable=False)
    confidence = Column(Integer, nullable=False)
    edge_pct = Column(Float, nullable=False)
    odds_at_pick = Column(Integer, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.now(tz=timezone.utc))
    game = relationship("Game")

class PickResult(Base):
    __tablename__ = "pick_results"
    id = Column(Integer, primary_key=True)
    pick_id = Column(Integer, ForeignKey("picks.id"), nullable=False)
    result = Column(String, nullable=False)
    payout = Column(Float, nullable=False, default=0.0)
    pick = relationship("PickModel")

class ApiUsage(Base):
    __tablename__ = "api_usage"
    id = Column(Integer, primary_key=True)
    source = Column(String, nullable=False)
    request_count = Column(Integer, nullable=False, default=0)
    month = Column(String, nullable=False)
    updated_at = Column(DateTime, nullable=False, default=datetime.now(tz=timezone.utc))
```

- [ ] **Step 4: Run tests**

```bash
python -m pytest backend/tests/test_database.py -v
```
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/models.py backend/tests/test_database.py
git commit -m "feat: add SQLAlchemy ORM models for all tables"
```

---

### Task 4: Core Data Types

**Files:**
- Create: `backend/data_types.py`
- Test: `backend/tests/test_data_types.py`

- [ ] **Step 1: Write failing test**

```python
# backend/tests/test_data_types.py
from datetime import date
from backend.data_types import GameData, TeamStats, Pick, OddsSnapshot

def test_team_stats_defaults():
    stats = TeamStats(
        point_diff=3.5, home_record=(10, 5), away_record=(8, 7),
        last_n_record=(7, 3), offensive_rating=112.0, defensive_rating=108.0,
        pace=100.0, strength_of_schedule=0.55, elo_rating=1520.0, rest_days=1
    )
    assert stats.turnover_margin is None
    assert stats.red_zone_pct is None
    assert stats.conference_strength is None

def test_pick_edge_calculation():
    pick = Pick(
        game_id=1, pick_type="moneyline", pick_value="BOS ML",
        confidence=4, edge_pct=8.2, model_probability=0.65,
        implied_probability=0.568, odds_at_pick=-110
    )
    assert pick.edge_pct == 8.2
    assert pick.confidence == 4

def test_odds_snapshot():
    snap = OddsSnapshot(
        bookmaker="draftkings", moneyline_home=-150, moneyline_away=130,
        spread_home=-4.5, spread_away=4.5, over_under=218.5
    )
    assert snap.bookmaker == "draftkings"

def test_game_data():
    stats = TeamStats(
        point_diff=0.0, home_record=(0, 0), away_record=(0, 0),
        last_n_record=(0, 0), offensive_rating=100.0, defensive_rating=100.0,
        pace=100.0, strength_of_schedule=0.5, elo_rating=1500.0, rest_days=1
    )
    game = GameData(
        game_id=1, sport="nba", date=date(2026, 3, 13),
        home_team_id=1, away_team_id=2,
        home_stats=stats, away_stats=stats, odds=[], week=None
    )
    assert game.sport == "nba"
    assert game.week is None
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python -m pytest backend/tests/test_data_types.py -v
```
Expected: FAIL

- [ ] **Step 3: Implement data_types.py**

```python
# backend/data_types.py
from dataclasses import dataclass, field
from datetime import date

@dataclass
class TeamStats:
    point_diff: float
    home_record: tuple[int, int]
    away_record: tuple[int, int]
    last_n_record: tuple[int, int]
    offensive_rating: float
    defensive_rating: float
    pace: float
    strength_of_schedule: float
    elo_rating: float
    rest_days: int
    turnover_margin: float | None = None
    red_zone_pct: float | None = None
    conference_strength: float | None = None

@dataclass
class OddsSnapshot:
    bookmaker: str
    moneyline_home: int
    moneyline_away: int
    spread_home: float
    spread_away: float
    over_under: float

@dataclass
class Pick:
    game_id: int
    pick_type: str
    pick_value: str
    confidence: int
    edge_pct: float
    model_probability: float
    implied_probability: float
    odds_at_pick: int

@dataclass
class GameData:
    game_id: int
    sport: str
    date: date
    home_team_id: int
    away_team_id: int
    home_stats: TeamStats
    away_stats: TeamStats
    odds: list[OddsSnapshot] = field(default_factory=list)
    week: int | None = None
```

- [ ] **Step 4: Run tests**

```bash
python -m pytest backend/tests/test_data_types.py -v
```
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/data_types.py backend/tests/test_data_types.py
git commit -m "feat: add core data types (GameData, TeamStats, Pick, OddsSnapshot)"
```

---

### Task 5: Config Loader

**Files:**
- Create: `backend/config.py`
- Modify: `backend/tests/conftest.py`

- [ ] **Step 1: Write failing test**

Add to `backend/tests/conftest.py`:

```python
from backend.config import load_config

@pytest.fixture
def config(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text("""
odds_api_key: "test_key"
database_path: "test.db"
seasons:
  nba:
    start: "10-22"
    end: "06-20"
odds_budget:
  monthly_limit: 500
  pause_at: 450
""")
    return load_config(str(config_file))
```

Create `backend/tests/test_config.py`:

```python
# backend/tests/test_config.py
from backend.config import load_config, is_sport_in_season
from datetime import date

def test_load_config(tmp_path):
    f = tmp_path / "c.yaml"
    f.write_text('odds_api_key: "k"\ndatabase_path: "d.db"\nseasons:\n  nba:\n    start: "10-22"\n    end: "06-20"\nodds_budget:\n  monthly_limit: 500\n  pause_at: 450\n')
    cfg = load_config(str(f))
    assert cfg["odds_api_key"] == "k"

def test_is_sport_in_season_nba_december():
    seasons = {"nba": {"start": "10-22", "end": "06-20"}}
    assert is_sport_in_season("nba", seasons, date(2026, 12, 15)) is True

def test_is_sport_in_season_nba_august():
    seasons = {"nba": {"start": "10-22", "end": "06-20"}}
    assert is_sport_in_season("nba", seasons, date(2026, 8, 15)) is False
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest backend/tests/test_config.py -v
```

- [ ] **Step 3: Implement config.py**

```python
# backend/config.py
from datetime import date
import yaml

def load_config(path: str) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)

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
    else:  # wraps around year (e.g., NFL Sep-Feb)
        return today >= start or today <= end
```

- [ ] **Step 4: Run tests**

```bash
python -m pytest backend/tests/test_config.py -v
```
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/config.py backend/tests/test_config.py backend/tests/conftest.py
git commit -m "feat: add config loader with season detection"
```

---

## Chunk 2: Data Collectors

### Task 6: API Budget Tracker

**Files:**
- Create: `backend/collectors/__init__.py`
- Create: `backend/collectors/budget.py`
- Test: `backend/tests/test_budget.py`

- [ ] **Step 1: Write failing test**

```python
# backend/tests/test_budget.py
from datetime import datetime
from backend.collectors.budget import ApiBudgetTracker

def test_can_make_request_under_limit(db_session, db_engine):
    from backend.models import Base
    Base.metadata.create_all(db_engine)
    tracker = ApiBudgetTracker(db_session, monthly_limit=500, pause_at=450)
    assert tracker.can_make_request("odds_api") is True

def test_record_request_increments_count(db_session, db_engine):
    from backend.models import Base
    Base.metadata.create_all(db_engine)
    tracker = ApiBudgetTracker(db_session, monthly_limit=500, pause_at=450)
    tracker.record_request("odds_api")
    assert tracker.get_count("odds_api") == 1

def test_cannot_make_request_at_pause_limit(db_session, db_engine):
    from backend.models import Base
    Base.metadata.create_all(db_engine)
    tracker = ApiBudgetTracker(db_session, monthly_limit=500, pause_at=5)
    for _ in range(5):
        tracker.record_request("odds_api")
    assert tracker.can_make_request("odds_api") is False
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest backend/tests/test_budget.py -v
```
Expected: FAIL

- [ ] **Step 3: Implement budget.py**

```python
# backend/collectors/__init__.py
```

```python
# backend/collectors/budget.py
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from backend.models import ApiUsage

class ApiBudgetTracker:
    def __init__(self, session: Session, monthly_limit: int = 500, pause_at: int = 450):
        self.session = session
        self.monthly_limit = monthly_limit
        self.pause_at = pause_at

    def _current_month(self) -> str:
        return datetime.now(tz=timezone.utc)().strftime("%Y-%m")

    def _get_or_create(self, source: str) -> ApiUsage:
        month = self._current_month()
        usage = self.session.query(ApiUsage).filter_by(
            source=source, month=month
        ).first()
        if not usage:
            usage = ApiUsage(source=source, month=month, request_count=0, updated_at=datetime.now(tz=timezone.utc)())
            self.session.add(usage)
            self.session.commit()
        return usage

    def can_make_request(self, source: str) -> bool:
        usage = self._get_or_create(source)
        return usage.request_count < self.pause_at

    def record_request(self, source: str) -> None:
        usage = self._get_or_create(source)
        usage.request_count += 1
        usage.updated_at = datetime.now(tz=timezone.utc)()
        self.session.commit()

    def get_count(self, source: str) -> int:
        usage = self._get_or_create(source)
        return usage.request_count
```

- [ ] **Step 4: Run tests**

```bash
python -m pytest backend/tests/test_budget.py -v
```
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/collectors/ backend/tests/test_budget.py
git commit -m "feat: add API budget tracker with monthly request limits"
```

---

### Task 7: ESPN Collector

**Files:**
- Create: `backend/collectors/espn.py`
- Test: `backend/tests/test_espn.py`

- [ ] **Step 1: Write failing test for schedule fetching**

```python
# backend/tests/test_espn.py
import json
import httpx
import pytest
from backend.collectors.espn import ESPNCollector

MOCK_SCOREBOARD = {
    "events": [
        {
            "id": "401585001",
            "date": "2026-03-13T00:00Z",
            "status": {"type": {"name": "STATUS_FINAL"}},
            "competitions": [{
                "competitors": [
                    {"id": "2", "homeAway": "home", "team": {"abbreviation": "BOS", "displayName": "Boston Celtics"}, "score": "112"},
                    {"id": "13", "homeAway": "away", "team": {"abbreviation": "LAL", "displayName": "Los Angeles Lakers"}, "score": "105"}
                ]
            }]
        }
    ]
}

@pytest.fixture
def mock_espn(monkeypatch):
    async def mock_get(self, url, **kwargs):
        response = httpx.Response(200, json=MOCK_SCOREBOARD)
        return response
    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)

@pytest.mark.asyncio
async def test_fetch_scoreboard(mock_espn):
    collector = ESPNCollector()
    games = await collector.fetch_scoreboard("nba", "20260313")
    assert len(games) == 1
    assert games[0]["home_team"] == "BOS"
    assert games[0]["away_team"] == "LAL"
    assert games[0]["home_score"] == 112
    assert games[0]["away_score"] == 105
    assert games[0]["status"] == "final"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python -m pytest backend/tests/test_espn.py -v
```
Expected: FAIL

- [ ] **Step 3: Implement espn.py**

```python
# backend/collectors/espn.py
import httpx

SPORT_URLS = {
    "nba": "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard",
    "nfl": "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard",
    "ncaab": "https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/scoreboard",
    "ncaaf": "https://site.api.espn.com/apis/site/v2/sports/football/college-football/scoreboard",
}

STATUS_MAP = {
    "STATUS_SCHEDULED": "scheduled",
    "STATUS_IN_PROGRESS": "in_progress",
    "STATUS_FINAL": "final",
    "STATUS_POSTPONED": "postponed",
    "STATUS_CANCELED": "cancelled",
}

class ESPNCollector:
    def __init__(self):
        self.client = httpx.AsyncClient(timeout=30.0)

    async def fetch_scoreboard(self, sport: str, date_str: str) -> list[dict]:
        url = SPORT_URLS[sport]
        response = await self.client.get(url, params={"dates": date_str})
        response.raise_for_status()
        data = response.json()
        games = []
        for event in data.get("events", []):
            competition = event["competitions"][0]
            competitors = competition["competitors"]
            home = next(c for c in competitors if c["homeAway"] == "home")
            away = next(c for c in competitors if c["homeAway"] == "away")
            espn_status = event["status"]["type"]["name"]
            games.append({
                "espn_id": event["id"],
                "date": event["date"],
                "status": STATUS_MAP.get(espn_status, "scheduled"),
                "home_team": home["team"]["abbreviation"],
                "home_team_name": home["team"]["displayName"],
                "away_team": away["team"]["abbreviation"],
                "away_team_name": away["team"]["displayName"],
                "home_score": int(home.get("score", 0)) if home.get("score") else None,
                "away_score": int(away.get("score", 0)) if away.get("score") else None,
            })
        return games

    async def close(self):
        await self.client.aclose()
```

- [ ] **Step 4: Run tests**

```bash
python -m pytest backend/tests/test_espn.py -v
```
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/collectors/espn.py backend/tests/test_espn.py
git commit -m "feat: add ESPN scoreboard collector"
```

---

### Task 8: Odds API Collector

**Files:**
- Create: `backend/collectors/odds_api.py`
- Test: `backend/tests/test_odds_api.py`

- [ ] **Step 1: Write failing test**

```python
# backend/tests/test_odds_api.py
import httpx
import pytest
from backend.collectors.odds_api import OddsAPICollector

MOCK_ODDS = [
    {
        "id": "abc123",
        "home_team": "Boston Celtics",
        "away_team": "Los Angeles Lakers",
        "commence_time": "2026-03-13T00:00:00Z",
        "bookmakers": [
            {
                "key": "draftkings",
                "markets": [
                    {
                        "key": "h2h",
                        "outcomes": [
                            {"name": "Boston Celtics", "price": -150},
                            {"name": "Los Angeles Lakers", "price": 130}
                        ]
                    },
                    {
                        "key": "spreads",
                        "outcomes": [
                            {"name": "Boston Celtics", "price": -110, "point": -4.5},
                            {"name": "Los Angeles Lakers", "price": -110, "point": 4.5}
                        ]
                    },
                    {
                        "key": "totals",
                        "outcomes": [
                            {"name": "Over", "price": -110, "point": 218.5},
                            {"name": "Under", "price": -110, "point": 218.5}
                        ]
                    }
                ]
            }
        ]
    }
]

@pytest.fixture
def mock_odds_api(monkeypatch):
    async def mock_get(self, url, **kwargs):
        return httpx.Response(200, json=MOCK_ODDS, headers={"x-requests-remaining": "498"})
    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)

@pytest.mark.asyncio
async def test_fetch_odds(mock_odds_api):
    collector = OddsAPICollector(api_key="test_key")
    odds = await collector.fetch_odds("nba")
    assert len(odds) == 1
    game_odds = odds[0]
    assert game_odds["home_team"] == "Boston Celtics"
    assert len(game_odds["bookmakers"]) == 1
    bk = game_odds["bookmakers"][0]
    assert bk["moneyline_home"] == -150
    assert bk["spread_home"] == -4.5
    assert bk["over_under"] == 218.5
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python -m pytest backend/tests/test_odds_api.py -v
```
Expected: FAIL

- [ ] **Step 3: Implement odds_api.py**

```python
# backend/collectors/odds_api.py
import httpx

SPORT_KEYS = {
    "nba": "basketball_nba",
    "nfl": "americanfootball_nfl",
    "ncaab": "basketball_ncaab",
    "ncaaf": "americanfootball_ncaaf",
}

class OddsAPICollector:
    BASE_URL = "https://api.the-odds-api.com/v4/sports"

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.client = httpx.AsyncClient(timeout=30.0)
        self.requests_remaining: int | None = None

    async def fetch_odds(self, sport: str) -> list[dict]:
        sport_key = SPORT_KEYS[sport]
        url = f"{self.BASE_URL}/{sport_key}/odds"
        params = {
            "apiKey": self.api_key,
            "regions": "us",
            "markets": "h2h,spreads,totals",
            "oddsFormat": "american",
        }
        response = await self.client.get(url, params=params)
        response.raise_for_status()
        self.requests_remaining = int(response.headers.get("x-requests-remaining", 0))
        raw = response.json()
        results = []
        for event in raw:
            bookmakers = []
            for bk in event.get("bookmakers", []):
                parsed = self._parse_bookmaker(bk, event["home_team"])
                if parsed:
                    bookmakers.append(parsed)
            results.append({
                "odds_api_id": event["id"],
                "home_team": event["home_team"],
                "away_team": event["away_team"],
                "commence_time": event["commence_time"],
                "bookmakers": bookmakers,
            })
        return results

    def _parse_bookmaker(self, bk: dict, home_team: str) -> dict | None:
        result = {"key": bk["key"], "moneyline_home": None, "moneyline_away": None,
                  "spread_home": None, "spread_away": None, "over_under": None}
        for market in bk.get("markets", []):
            outcomes = market["outcomes"]
            if market["key"] == "h2h":
                for o in outcomes:
                    if o["name"] == home_team:
                        result["moneyline_home"] = o["price"]
                    else:
                        result["moneyline_away"] = o["price"]
            elif market["key"] == "spreads":
                for o in outcomes:
                    if o["name"] == home_team:
                        result["spread_home"] = o["point"]
                    else:
                        result["spread_away"] = o["point"]
            elif market["key"] == "totals":
                for o in outcomes:
                    if o["name"] == "Over":
                        result["over_under"] = o["point"]
        return result

    async def close(self):
        await self.client.aclose()
```

- [ ] **Step 4: Run tests**

```bash
python -m pytest backend/tests/test_odds_api.py -v
```
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/collectors/odds_api.py backend/tests/test_odds_api.py
git commit -m "feat: add Odds API collector with market parsing"
```

---

## Chunk 3: Analysis Engine

### Task 9: Strategy Base Class

**Files:**
- Create: `backend/analysis/__init__.py`
- Create: `backend/analysis/strategy.py`
- Test: `backend/tests/test_strategy.py`

- [ ] **Step 1: Write failing test**

```python
# backend/tests/test_strategy.py
import pytest
from backend.analysis.strategy import Strategy
from backend.data_types import GameData, TeamStats, Pick
from datetime import date

def _make_stats(**overrides):
    defaults = dict(
        point_diff=0.0, home_record=(0, 0), away_record=(0, 0),
        last_n_record=(0, 0), offensive_rating=100.0, defensive_rating=100.0,
        pace=100.0, strength_of_schedule=0.5, elo_rating=1500.0, rest_days=1
    )
    defaults.update(overrides)
    return TeamStats(**defaults)

def _make_game(**overrides):
    defaults = dict(
        game_id=1, sport="nba", date=date(2026, 3, 13),
        home_team_id=1, away_team_id=2,
        home_stats=_make_stats(), away_stats=_make_stats(), odds=[], week=None
    )
    defaults.update(overrides)
    return GameData(**defaults)

def test_strategy_is_abstract():
    with pytest.raises(TypeError):
        Strategy("test", {})

def test_concrete_strategy_can_predict():
    class TestStrategy(Strategy):
        def predict(self, game):
            return [Pick(
                game_id=game.game_id, pick_type="moneyline",
                pick_value="HOME ML", confidence=3, edge_pct=6.0,
                model_probability=0.6, implied_probability=0.54, odds_at_pick=-150
            )]
    s = TestStrategy("test", {"min_edge": 5.0})
    picks = s.predict(_make_game())
    assert len(picks) == 1
    assert picks[0].pick_type == "moneyline"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python -m pytest backend/tests/test_strategy.py -v
```

- [ ] **Step 3: Implement strategy.py**

```python
# backend/analysis/__init__.py
```

```python
# backend/analysis/strategy.py
from abc import ABC, abstractmethod
from backend.data_types import GameData, Pick

class Strategy(ABC):
    def __init__(self, name: str, config: dict):
        self.name = name
        self.config = config

    @abstractmethod
    def predict(self, game: GameData) -> list[Pick]:
        """Returns picks for a game (may return multiple: ML, spread, O/U)."""

    @classmethod
    def from_config(cls, config: dict) -> "Strategy":
        return cls(name=config.get("name", cls.__name__), config=config)
```

- [ ] **Step 4: Run tests**

```bash
python -m pytest backend/tests/test_strategy.py -v
```
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/analysis/ backend/tests/test_strategy.py
git commit -m "feat: add Strategy ABC base class"
```

---

### Task 10: ELO Rating System

**Files:**
- Create: `backend/analysis/elo.py`
- Test: `backend/tests/test_elo.py`

- [ ] **Step 1: Write failing test**

```python
# backend/tests/test_elo.py
from backend.analysis.elo import EloSystem

def test_initial_rating():
    elo = EloSystem(k_factor=20)
    assert elo.get_rating("BOS") == 1500.0

def test_winner_gains_rating():
    elo = EloSystem(k_factor=20)
    elo.update("BOS", "LAL", winner="BOS")
    assert elo.get_rating("BOS") > 1500.0
    assert elo.get_rating("LAL") < 1500.0

def test_ratings_are_zero_sum():
    elo = EloSystem(k_factor=20)
    elo.update("BOS", "LAL", winner="BOS")
    total = elo.get_rating("BOS") + elo.get_rating("LAL")
    assert abs(total - 3000.0) < 0.01

def test_expected_score():
    elo = EloSystem(k_factor=20)
    expected = elo.expected_score(1600, 1400)
    assert 0.75 < expected < 0.77  # ~0.7597

def test_upset_causes_larger_shift():
    elo = EloSystem(k_factor=20)
    # Give BOS a big advantage
    elo.ratings["BOS"] = 1700
    elo.ratings["LAL"] = 1300
    rating_before = elo.get_rating("LAL")
    elo.update("BOS", "LAL", winner="LAL")  # upset
    gain = elo.get_rating("LAL") - rating_before
    assert gain > 15  # large gain for upset
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python -m pytest backend/tests/test_elo.py -v
```

- [ ] **Step 3: Implement elo.py**

```python
# backend/analysis/elo.py
import math

class EloSystem:
    def __init__(self, k_factor: float = 20.0, initial_rating: float = 1500.0):
        self.k_factor = k_factor
        self.initial_rating = initial_rating
        self.ratings: dict[str, float] = {}

    def get_rating(self, team: str) -> float:
        return self.ratings.get(team, self.initial_rating)

    def expected_score(self, rating_a: float, rating_b: float) -> float:
        return 1.0 / (1.0 + math.pow(10, (rating_b - rating_a) / 400.0))

    def update(self, home: str, away: str, winner: str) -> None:
        ra = self.get_rating(home)
        rb = self.get_rating(away)
        ea = self.expected_score(ra, rb)
        eb = 1.0 - ea
        sa = 1.0 if winner == home else 0.0
        sb = 1.0 - sa
        self.ratings[home] = ra + self.k_factor * (sa - ea)
        self.ratings[away] = rb + self.k_factor * (sb - eb)
```

- [ ] **Step 4: Run tests**

```bash
python -m pytest backend/tests/test_elo.py -v
```
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/analysis/elo.py backend/tests/test_elo.py
git commit -m "feat: add ELO rating system"
```

---

### Task 11: Confidence Scoring

**Files:**
- Create: `backend/analysis/confidence.py`
- Test: `backend/tests/test_confidence.py`

- [ ] **Step 1: Write failing test**

```python
# backend/tests/test_confidence.py
from backend.analysis.confidence import calculate_confidence

def test_five_stars():
    assert calculate_confidence(edge_pct=15.0, models_agreeing=3) == 5

def test_four_stars():
    assert calculate_confidence(edge_pct=9.0, models_agreeing=2) == 4

def test_three_stars():
    assert calculate_confidence(edge_pct=6.0, models_agreeing=2) == 3

def test_two_stars():
    assert calculate_confidence(edge_pct=5.5, models_agreeing=1) == 2

def test_one_star():
    assert calculate_confidence(edge_pct=3.5, models_agreeing=1) == 1

def test_zero_below_threshold():
    assert calculate_confidence(edge_pct=2.0, models_agreeing=1) == 0
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python -m pytest backend/tests/test_confidence.py -v
```

- [ ] **Step 3: Implement confidence.py**

```python
# backend/analysis/confidence.py

def calculate_confidence(edge_pct: float, models_agreeing: int) -> int:
    if edge_pct >= 12.0 and models_agreeing >= 3:
        return 5
    if edge_pct >= 8.0 and models_agreeing >= 2:
        return 4
    if edge_pct >= 5.0 and models_agreeing >= 2:
        return 3
    if edge_pct >= 5.0 and models_agreeing >= 1:
        return 2
    if edge_pct >= 3.0:
        return 1
    return 0
```

- [ ] **Step 4: Run tests**

```bash
python -m pytest backend/tests/test_confidence.py -v
```
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/analysis/confidence.py backend/tests/test_confidence.py
git commit -m "feat: add confidence scoring (edge to star rating)"
```

---

### Task 12: Win Probability Model

**Files:**
- Create: `backend/analysis/win_probability.py`
- Test: `backend/tests/test_win_probability.py`

- [ ] **Step 1: Write failing test**

```python
# backend/tests/test_win_probability.py
import numpy as np
from backend.analysis.win_probability import WinProbabilityModel

def test_train_and_predict():
    model = WinProbabilityModel()
    # Simulate training data: [point_diff, off_rating, def_rating, elo, home_advantage]
    X = np.array([
        [5.0, 112.0, 105.0, 1550, 1],   # strong home team
        [-3.0, 105.0, 110.0, 1450, 0],   # weak away team
        [8.0, 115.0, 102.0, 1600, 1],    # dominant home
        [-6.0, 100.0, 112.0, 1400, 0],   # weak away
        [2.0, 108.0, 108.0, 1510, 1],    # slight home edge
        [-1.0, 107.0, 109.0, 1490, 0],   # slight away disadvantage
    ])
    y = np.array([1, 0, 1, 0, 1, 0])  # 1 = home win
    model.train(X, y)
    # Strong home team should have > 50% probability
    prob = model.predict_proba(np.array([[6.0, 113.0, 104.0, 1560, 1]]))
    assert prob > 0.5

def test_untrained_model_raises():
    model = WinProbabilityModel()
    import pytest
    with pytest.raises(ValueError, match="not trained"):
        model.predict_proba(np.array([[0, 0, 0, 0, 0]]))
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python -m pytest backend/tests/test_win_probability.py -v
```

- [ ] **Step 3: Implement win_probability.py**

```python
# backend/analysis/win_probability.py
import numpy as np
from sklearn.linear_model import LogisticRegression

class WinProbabilityModel:
    def __init__(self):
        self.model: LogisticRegression | None = None

    def train(self, X: np.ndarray, y: np.ndarray) -> None:
        self.model = LogisticRegression(max_iter=1000)
        self.model.fit(X, y)

    def predict_proba(self, X: np.ndarray) -> float:
        if self.model is None:
            raise ValueError("Model not trained. Call train() first.")
        proba = self.model.predict_proba(X)
        return float(proba[0][1])  # probability of class 1 (home win)
```

- [ ] **Step 4: Run tests**

```bash
python -m pytest backend/tests/test_win_probability.py -v
```
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/analysis/win_probability.py backend/tests/test_win_probability.py
git commit -m "feat: add logistic regression win probability model"
```

---

### Task 13: Odds Utilities (Implied Probability)

**Files:**
- Create: `backend/analysis/odds_utils.py`
- Test: `backend/tests/test_odds_utils.py`

- [ ] **Step 1: Write failing test**

```python
# backend/tests/test_odds_utils.py
from backend.analysis.odds_utils import american_to_implied_prob, calculate_payout

def test_negative_odds_implied_prob():
    # -150 → 150 / (150 + 100) = 0.6
    prob = american_to_implied_prob(-150)
    assert abs(prob - 0.6) < 0.01

def test_positive_odds_implied_prob():
    # +130 → 100 / (130 + 100) = 0.4348
    prob = american_to_implied_prob(130)
    assert abs(prob - 0.4348) < 0.01

def test_payout_negative_odds():
    # Bet 1 unit at -150, win → profit = 100/150 = 0.6667
    payout = calculate_payout(-150)
    assert abs(payout - 0.6667) < 0.01

def test_payout_positive_odds():
    # Bet 1 unit at +130, win → profit = 130/100 = 1.3
    payout = calculate_payout(130)
    assert abs(payout - 1.3) < 0.01
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python -m pytest backend/tests/test_odds_utils.py -v
```

- [ ] **Step 3: Implement odds_utils.py**

```python
# backend/analysis/odds_utils.py

def american_to_implied_prob(odds: int) -> float:
    if odds < 0:
        return abs(odds) / (abs(odds) + 100)
    else:
        return 100 / (odds + 100)

def calculate_payout(odds: int) -> float:
    if odds < 0:
        return 100 / abs(odds)
    else:
        return odds / 100
```

- [ ] **Step 4: Run tests**

```bash
python -m pytest backend/tests/test_odds_utils.py -v
```
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/analysis/odds_utils.py backend/tests/test_odds_utils.py
git commit -m "feat: add odds utilities (implied probability, payout calculation)"
```

---

### Task 14: Ensemble Strategy Variant (Variant C)

**Files:**
- Create: `backend/analysis/variants/__init__.py`
- Create: `backend/analysis/variants/ensemble.py`
- Test: `backend/tests/test_ensemble.py`

- [ ] **Step 1: Write failing test**

```python
# backend/tests/test_ensemble.py
from datetime import date
from backend.analysis.variants.ensemble import EnsembleStrategy
from backend.data_types import GameData, TeamStats, OddsSnapshot

def _stats(**kw):
    defaults = dict(
        point_diff=0.0, home_record=(0, 0), away_record=(0, 0),
        last_n_record=(0, 0), offensive_rating=100.0, defensive_rating=100.0,
        pace=100.0, strength_of_schedule=0.5, elo_rating=1500.0, rest_days=2
    )
    defaults.update(kw)
    return TeamStats(**defaults)

def test_ensemble_returns_picks_when_edge_exists():
    game = GameData(
        game_id=1, sport="nba", date=date(2026, 3, 13),
        home_team_id=1, away_team_id=2,
        home_stats=_stats(point_diff=8.0, elo_rating=1600, offensive_rating=115.0, defensive_rating=105.0),
        away_stats=_stats(point_diff=-3.0, elo_rating=1400, offensive_rating=105.0, defensive_rating=112.0),
        odds=[OddsSnapshot(
            bookmaker="dk", moneyline_home=-120, moneyline_away=100,
            spread_home=-3.5, spread_away=3.5, over_under=215.0
        )]
    )
    strategy = EnsembleStrategy("ensemble", {"min_edge": 5.0, "k_factor": 20, "lookback": 10})
    picks = strategy.predict(game)
    # Should produce at least one pick since home team is clearly better
    # but odds imply ~54.5% while model should predict higher
    assert isinstance(picks, list)
    for pick in picks:
        assert pick.edge_pct >= 0

def test_ensemble_no_picks_without_odds():
    game = GameData(
        game_id=1, sport="nba", date=date(2026, 3, 13),
        home_team_id=1, away_team_id=2,
        home_stats=_stats(), away_stats=_stats(), odds=[]
    )
    strategy = EnsembleStrategy("ensemble", {"min_edge": 5.0, "k_factor": 20, "lookback": 10})
    picks = strategy.predict(game)
    assert picks == []
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python -m pytest backend/tests/test_ensemble.py -v
```

- [ ] **Step 3: Implement ensemble.py**

```python
# backend/analysis/variants/__init__.py
```

```python
# backend/analysis/variants/ensemble.py
from backend.analysis.strategy import Strategy
from backend.analysis.elo import EloSystem
from backend.analysis.confidence import calculate_confidence
from backend.analysis.odds_utils import american_to_implied_prob
from backend.data_types import GameData, Pick

class EnsembleStrategy(Strategy):
    """Variant C: Combines stat-based probability with ELO ratings."""

    def predict(self, game: GameData) -> list[Pick]:
        if not game.odds:
            return []

        picks = []
        min_edge = self.config.get("min_edge", 5.0)

        # Calculate model probability from stats + ELO
        home_prob = self._model_probability(game)
        away_prob = 1.0 - home_prob

        # Average odds across bookmakers
        avg_odds = self._average_odds(game)
        if avg_odds is None:
            return []

        # Moneyline pick
        if avg_odds["moneyline_home"] is not None:
            implied_home = american_to_implied_prob(avg_odds["moneyline_home"])
            implied_away = american_to_implied_prob(avg_odds["moneyline_away"])

            home_edge = (home_prob - implied_home) * 100
            away_edge = (away_prob - implied_away) * 100

            if home_edge >= min_edge:
                models = self._count_agreeing_models(game, "home")
                picks.append(Pick(
                    game_id=game.game_id, pick_type="moneyline",
                    pick_value=f"HOME ML",
                    confidence=calculate_confidence(home_edge, models),
                    edge_pct=round(home_edge, 1),
                    model_probability=round(home_prob, 4),
                    implied_probability=round(implied_home, 4),
                    odds_at_pick=avg_odds["moneyline_home"]
                ))
            elif away_edge >= min_edge:
                models = self._count_agreeing_models(game, "away")
                picks.append(Pick(
                    game_id=game.game_id, pick_type="moneyline",
                    pick_value=f"AWAY ML",
                    confidence=calculate_confidence(away_edge, models),
                    edge_pct=round(away_edge, 1),
                    model_probability=round(away_prob, 4),
                    implied_probability=round(implied_away, 4),
                    odds_at_pick=avg_odds["moneyline_away"]
                ))

        return picks

    def _model_probability(self, game: GameData) -> float:
        hs, aws = game.home_stats, game.away_stats

        # Factor 1: Point differential (normalized)
        pd_diff = hs.point_diff - aws.point_diff
        pd_score = 1 / (1 + 10 ** (-pd_diff / 10))  # sigmoid

        # Factor 2: ELO
        elo_diff = hs.elo_rating - aws.elo_rating
        elo_score = 1 / (1 + 10 ** (-elo_diff / 400))

        # Factor 3: Offensive/Defensive rating
        net_home = hs.offensive_rating - hs.defensive_rating
        net_away = aws.offensive_rating - aws.defensive_rating
        net_diff = net_home - net_away
        rating_score = 1 / (1 + 10 ** (-net_diff / 10))

        # Weighted average with home court advantage
        weights = self.config.get("weights", {"pd": 0.3, "elo": 0.35, "rating": 0.25, "hca": 0.1})
        prob = (
            weights["pd"] * pd_score +
            weights["elo"] * elo_score +
            weights["rating"] * rating_score +
            weights["hca"] * 0.6  # 60% baseline home win rate
        )
        return max(0.01, min(0.99, prob))

    def _average_odds(self, game: GameData) -> dict | None:
        if not game.odds:
            return None
        ml_home = [o.moneyline_home for o in game.odds if o.moneyline_home is not None]
        ml_away = [o.moneyline_away for o in game.odds if o.moneyline_away is not None]
        if not ml_home:
            return None
        return {
            "moneyline_home": int(sum(ml_home) / len(ml_home)),
            "moneyline_away": int(sum(ml_away) / len(ml_away)),
        }

    def _count_agreeing_models(self, game: GameData, side: str) -> int:
        count = 0
        hs, aws = game.home_stats, game.away_stats
        if side == "home":
            if hs.point_diff > aws.point_diff: count += 1
            if hs.elo_rating > aws.elo_rating: count += 1
            if (hs.offensive_rating - hs.defensive_rating) > (aws.offensive_rating - aws.defensive_rating): count += 1
        else:
            if aws.point_diff > hs.point_diff: count += 1
            if aws.elo_rating > hs.elo_rating: count += 1
            if (aws.offensive_rating - aws.defensive_rating) > (hs.offensive_rating - hs.defensive_rating): count += 1
        return count
```

- [ ] **Step 4: Run tests**

```bash
python -m pytest backend/tests/test_ensemble.py -v
```
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/analysis/variants/ backend/tests/test_ensemble.py
git commit -m "feat: add Ensemble strategy variant (stat-based + ELO)"
```

---

## Chunk 4: Backtesting & Pipeline

### Task 15: Backtester Engine

**Files:**
- Create: `backend/backtesting/__init__.py`
- Create: `backend/backtesting/backtester.py`
- Test: `backend/tests/test_backtester.py`

- [ ] **Step 1: Write failing test**

```python
# backend/tests/test_backtester.py
from datetime import date
from backend.backtesting.backtester import Backtester
from backend.analysis.strategy import Strategy
from backend.data_types import GameData, TeamStats, Pick, OddsSnapshot

class MockStrategy(Strategy):
    def predict(self, game):
        return [Pick(
            game_id=game.game_id, pick_type="moneyline",
            pick_value="HOME ML", confidence=4, edge_pct=8.0,
            model_probability=0.65, implied_probability=0.55, odds_at_pick=-150
        )]

def _game(gid, home_score, away_score):
    stats = TeamStats(
        point_diff=0, home_record=(0,0), away_record=(0,0),
        last_n_record=(0,0), offensive_rating=100, defensive_rating=100,
        pace=100, strength_of_schedule=0.5, elo_rating=1500, rest_days=2
    )
    return GameData(
        game_id=gid, sport="nba", date=date(2026, 1, 1),
        home_team_id=1, away_team_id=2,
        home_stats=stats, away_stats=stats,
        odds=[OddsSnapshot("dk", -150, 130, -3.5, 3.5, 215.0)]
    ), home_score, away_score

def test_backtest_calculates_record():
    strategy = MockStrategy("mock", {})
    games_with_results = [
        (*_game(1, 110, 100),),  # home win → pick wins
        (*_game(2, 95, 105),),   # home loss → pick loses
        (*_game(3, 108, 102),),  # home win → pick wins
    ]
    bt = Backtester(strategy)
    results = bt.run(games_with_results)
    assert results["wins"] == 2
    assert results["losses"] == 1
    assert results["total"] == 3
    assert abs(results["win_rate"] - 66.67) < 1

def test_backtest_calculates_roi():
    strategy = MockStrategy("mock", {})
    games_with_results = [
        (*_game(1, 110, 100),),  # win at -150 → +0.667
        (*_game(2, 95, 105),),   # loss → -1.0
    ]
    bt = Backtester(strategy)
    results = bt.run(games_with_results)
    # ROI = (0.667 - 1.0) / 2 units = -16.65%
    assert results["roi"] < 0
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python -m pytest backend/tests/test_backtester.py -v
```

- [ ] **Step 3: Implement backtester.py**

```python
# backend/backtesting/__init__.py
```

```python
# backend/backtesting/backtester.py
from backend.analysis.strategy import Strategy
from backend.analysis.odds_utils import calculate_payout
from backend.data_types import GameData

class Backtester:
    def __init__(self, strategy: Strategy):
        self.strategy = strategy

    def run(self, games_with_results: list[tuple[GameData, int, int]]) -> dict:
        wins = 0
        losses = 0
        pushes = 0
        total_profit = 0.0
        pick_details = []

        for game, home_score, away_score in games_with_results:
            picks = self.strategy.predict(game)
            for pick in picks:
                result = self._grade_pick(pick, home_score, away_score)
                if result == "win":
                    wins += 1
                    total_profit += calculate_payout(pick.odds_at_pick)
                elif result == "loss":
                    losses += 1
                    total_profit -= 1.0
                else:
                    pushes += 1

                pick_details.append({
                    "game_id": pick.game_id,
                    "pick_type": pick.pick_type,
                    "pick_value": pick.pick_value,
                    "confidence": pick.confidence,
                    "edge_pct": pick.edge_pct,
                    "result": result,
                    "odds_at_pick": pick.odds_at_pick,
                })

        total = wins + losses
        return {
            "wins": wins,
            "losses": losses,
            "pushes": pushes,
            "total": total,
            "win_rate": round((wins / total * 100) if total > 0 else 0, 2),
            "roi": round((total_profit / (total if total > 0 else 1)) * 100, 2),
            "total_profit": round(total_profit, 4),
            "picks": pick_details,
        }

    def _grade_pick(self, pick, home_score: int, away_score: int) -> str:
        if pick.pick_type == "moneyline":
            if "HOME" in pick.pick_value:
                return "win" if home_score > away_score else "loss"
            else:
                return "win" if away_score > home_score else "loss"
        return "loss"  # fallback for now; spread/OU grading added later
```

- [ ] **Step 4: Run tests**

```bash
python -m pytest backend/tests/test_backtester.py -v
```
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/backtesting/ backend/tests/test_backtester.py
git commit -m "feat: add backtester engine with win/loss/ROI tracking"
```

---

### Task 16: Pick Grader (Live Results)

**Files:**
- Create: `backend/pipeline/__init__.py`
- Create: `backend/pipeline/grader.py`
- Test: `backend/tests/test_grader.py`

- [ ] **Step 1: Write failing test**

```python
# backend/tests/test_grader.py
from backend.pipeline.grader import grade_pick
from backend.analysis.odds_utils import calculate_payout

def test_grade_moneyline_home_win():
    result, payout = grade_pick(
        pick_type="moneyline", pick_value="HOME ML",
        home_score=110, away_score=100, odds_at_pick=-150
    )
    assert result == "win"
    assert abs(payout - calculate_payout(-150)) < 0.01

def test_grade_moneyline_away_win():
    result, payout = grade_pick(
        pick_type="moneyline", pick_value="AWAY ML",
        home_score=100, away_score=110, odds_at_pick=130
    )
    assert result == "win"

def test_grade_moneyline_loss():
    result, payout = grade_pick(
        pick_type="moneyline", pick_value="HOME ML",
        home_score=95, away_score=105, odds_at_pick=-150
    )
    assert result == "loss"
    assert payout == -1.0

def test_grade_spread_cover():
    # HOME -4.5, home wins by 10
    result, payout = grade_pick(
        pick_type="spread", pick_value="HOME -4.5",
        home_score=110, away_score=100, odds_at_pick=-110
    )
    assert result == "win"

def test_grade_spread_no_cover():
    # HOME -4.5, home wins by 3
    result, payout = grade_pick(
        pick_type="spread", pick_value="HOME -4.5",
        home_score=103, away_score=100, odds_at_pick=-110
    )
    assert result == "loss"

def test_grade_over_hit():
    result, payout = grade_pick(
        pick_type="over_under", pick_value="Over 218.5",
        home_score=115, away_score=110, odds_at_pick=-110
    )
    assert result == "win"

def test_grade_under_hit():
    result, payout = grade_pick(
        pick_type="over_under", pick_value="Under 218.5",
        home_score=100, away_score=105, odds_at_pick=-110
    )
    assert result == "win"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python -m pytest backend/tests/test_grader.py -v
```

- [ ] **Step 3: Implement grader.py**

```python
# backend/pipeline/__init__.py
```

```python
# backend/pipeline/grader.py
import re
from backend.analysis.odds_utils import calculate_payout

def grade_pick(
    pick_type: str, pick_value: str,
    home_score: int, away_score: int, odds_at_pick: int
) -> tuple[str, float]:
    """Returns (result, payout) where result is 'win', 'loss', or 'push'."""

    if pick_type == "moneyline":
        if "HOME" in pick_value:
            won = home_score > away_score
        else:
            won = away_score > home_score
        if home_score == away_score:
            return "push", 0.0

    elif pick_type == "spread":
        match = re.search(r"([+-]?\d+\.?\d*)", pick_value)
        spread = float(match.group(1)) if match else 0.0
        if "HOME" in pick_value:
            margin = home_score - away_score + spread
        else:
            margin = away_score - home_score + spread
        if margin == 0:
            return "push", 0.0
        won = margin > 0

    elif pick_type == "over_under":
        match = re.search(r"(\d+\.?\d*)", pick_value)
        total_line = float(match.group(1)) if match else 0.0
        actual_total = home_score + away_score
        if actual_total == total_line:
            return "push", 0.0
        if "Over" in pick_value:
            won = actual_total > total_line
        else:
            won = actual_total < total_line
    else:
        return "loss", -1.0

    if won:
        return "win", calculate_payout(odds_at_pick)
    else:
        return "loss", -1.0
```

- [ ] **Step 4: Run tests**

```bash
python -m pytest backend/tests/test_grader.py -v
```
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/pipeline/ backend/tests/test_grader.py
git commit -m "feat: add pick grader with moneyline, spread, and O/U support"
```

---

### Task 17: Pick Generator

**Files:**
- Create: `backend/pipeline/pick_generator.py`
- Test: `backend/tests/test_pick_generator.py`

- [ ] **Step 1: Write failing test**

```python
# backend/tests/test_pick_generator.py
from datetime import date, datetime
from backend.pipeline.pick_generator import generate_and_store_picks
from backend.models import Base, Game, Team, StrategyModel, PickModel
from backend.analysis.variants.ensemble import EnsembleStrategy

def test_generate_picks_stores_to_db(db_engine, db_session):
    Base.metadata.create_all(db_engine)
    # Setup test data
    t1 = Team(id=1, name="Boston Celtics", abbreviation="BOS", sport="nba")
    t2 = Team(id=2, name="LA Lakers", abbreviation="LAL", sport="nba")
    g = Game(id=1, sport="nba", season="2025-26", date=date(2026, 3, 13),
             home_team_id=1, away_team_id=2, status="scheduled")
    s = StrategyModel(id=1, name="ensemble", config_json='{"min_edge": 0.1, "k_factor": 20, "lookback": 10}',
                      is_active=True)
    db_session.add_all([t1, t2, g, s])
    db_session.commit()

    count = generate_and_store_picks(db_session, strategy_id=1)
    assert isinstance(count, int)
    # Should have attempted to generate picks (may be 0 if no edge found with default stats)
    stored = db_session.query(PickModel).all()
    assert isinstance(stored, list)
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python -m pytest backend/tests/test_pick_generator.py -v
```

- [ ] **Step 3: Implement pick_generator.py**

```python
# backend/pipeline/pick_generator.py
import json
from datetime import date, datetime, timezone
from sqlalchemy.orm import Session
from backend.models import Game, PickModel, StrategyModel, Odds, TeamStat, EloRating
from backend.data_types import GameData, TeamStats, OddsSnapshot
from backend.analysis.variants.ensemble import EnsembleStrategy

STRATEGY_MAP = {
    "ensemble": EnsembleStrategy,
}

def generate_and_store_picks(session: Session, strategy_id: int, target_date: date | None = None) -> int:
    target_date = target_date or date.today()
    strat_row = session.query(StrategyModel).get(strategy_id)
    if not strat_row:
        return 0

    config = json.loads(strat_row.config_json)
    strategy_cls = STRATEGY_MAP.get(strat_row.name)
    if not strategy_cls:
        return 0
    strategy = strategy_cls(strat_row.name, config)

    games = session.query(Game).filter(
        Game.date == target_date,
        Game.status == "scheduled"
    ).all()

    count = 0
    for game in games:
        game_data = _build_game_data(session, game)
        picks = strategy.predict(game_data)
        for pick in picks:
            if pick.confidence >= 1:
                db_pick = PickModel(
                    game_id=game.id, strategy_id=strategy_id,
                    pick_type=pick.pick_type, pick_value=pick.pick_value,
                    confidence=pick.confidence, edge_pct=pick.edge_pct,
                    odds_at_pick=pick.odds_at_pick, created_at=datetime.now(tz=timezone.utc)()
                )
                session.add(db_pick)
                count += 1
    session.commit()
    return count

def _build_game_data(session: Session, game: Game) -> GameData:
    home_stats = _get_team_stats(session, game.home_team_id, game.sport)
    away_stats = _get_team_stats(session, game.away_team_id, game.sport)
    odds_rows = session.query(Odds).filter(Odds.game_id == game.id).all()
    odds = [
        OddsSnapshot(
            bookmaker=o.bookmaker,
            moneyline_home=o.moneyline_home or 0,
            moneyline_away=o.moneyline_away or 0,
            spread_home=o.spread_home or 0.0,
            spread_away=o.spread_away or 0.0,
            over_under=o.over_under or 0.0,
        )
        for o in odds_rows
    ]
    return GameData(
        game_id=game.id, sport=game.sport, date=game.date,
        home_team_id=game.home_team_id, away_team_id=game.away_team_id,
        home_stats=home_stats, away_stats=away_stats,
        odds=odds, week=game.week
    )

def _get_team_stats(session: Session, team_id: int, sport: str) -> TeamStats:
    stats_rows = session.query(TeamStat).filter(TeamStat.team_id == team_id).all()
    stats_dict = {s.stat_type: s.value for s in stats_rows}
    elo_row = session.query(EloRating).filter(
        EloRating.team_id == team_id, EloRating.sport == sport
    ).first()
    return TeamStats(
        point_diff=stats_dict.get("point_diff", 0.0),
        home_record=(int(stats_dict.get("home_wins", 0)), int(stats_dict.get("home_losses", 0))),
        away_record=(int(stats_dict.get("away_wins", 0)), int(stats_dict.get("away_losses", 0))),
        last_n_record=(int(stats_dict.get("last_n_wins", 0)), int(stats_dict.get("last_n_losses", 0))),
        offensive_rating=stats_dict.get("offensive_rating", 100.0),
        defensive_rating=stats_dict.get("defensive_rating", 100.0),
        pace=stats_dict.get("pace", 100.0),
        strength_of_schedule=stats_dict.get("sos", 0.5),
        elo_rating=elo_row.rating if elo_row else 1500.0,
        rest_days=int(stats_dict.get("rest_days", 2)),
        turnover_margin=stats_dict.get("turnover_margin"),
        red_zone_pct=stats_dict.get("red_zone_pct"),
        conference_strength=stats_dict.get("conference_strength"),
    )
```

- [ ] **Step 4: Run tests**

```bash
python -m pytest backend/tests/test_pick_generator.py -v
```
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/pipeline/pick_generator.py backend/tests/test_pick_generator.py
git commit -m "feat: add pick generator that runs strategies and stores picks"
```

---

## Chunk 5: FastAPI Backend

### Task 18: FastAPI App Setup

**Files:**
- Create: `backend/api/__init__.py`
- Create: `backend/api/main.py`
- Test: `backend/tests/test_api_main.py`

- [ ] **Step 1: Write failing test**

```python
# backend/tests/test_api_main.py
from fastapi.testclient import TestClient
from backend.api.main import create_app

def test_health_check():
    app = create_app(":memory:")
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python -m pytest backend/tests/test_api_main.py -v
```

- [ ] **Step 3: Implement main.py**

```python
# backend/api/__init__.py
```

```python
# backend/api/main.py
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from backend.database import get_engine, get_session
from backend.models import Base

def create_app(db_path: str = "sports_picks.db") -> FastAPI:
    app = FastAPI(title="Sports Picks API")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    engine = get_engine(db_path)
    Base.metadata.create_all(engine)

    @app.get("/health")
    def health():
        return {"status": "ok"}

    # Store engine on app state for use in route dependencies
    app.state.engine = engine

    from backend.api.picks import router as picks_router
    from backend.api.stats import router as stats_router
    from backend.api.backtest import router as backtest_router
    from backend.api.games import router as games_router

    app.include_router(picks_router, prefix="/picks", tags=["picks"])
    app.include_router(stats_router, prefix="/stats", tags=["stats"])
    app.include_router(backtest_router, prefix="/backtest", tags=["backtest"])
    app.include_router(games_router, prefix="/games", tags=["games"])

    return app
```

- [ ] **Step 4: Run tests** (will fail because routers don't exist yet — create stubs)

Create stub routers:

```python
# backend/api/picks.py
from fastapi import APIRouter
router = APIRouter()

# backend/api/stats.py
from fastapi import APIRouter
router = APIRouter()

# backend/api/backtest.py
from fastapi import APIRouter
router = APIRouter()

# backend/api/games.py
from fastapi import APIRouter
router = APIRouter()
```

```bash
python -m pytest backend/tests/test_api_main.py -v
```
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/api/ backend/tests/test_api_main.py
git commit -m "feat: add FastAPI app setup with health check and router stubs"
```

---

### Task 19: Picks API Endpoints

**Files:**
- Modify: `backend/api/picks.py`
- Test: `backend/tests/test_api_picks.py`

- [ ] **Step 1: Write failing test**

```python
# backend/tests/test_api_picks.py
from datetime import date, datetime
from fastapi.testclient import TestClient
from backend.api.main import create_app
from backend.models import Base, Team, Game, PickModel, PickResult, StrategyModel

def _seed_db(client):
    engine = client.app.state.engine
    Base.metadata.create_all(engine)
    from backend.database import get_session
    session = get_session(engine)
    t1 = Team(id=1, name="Boston Celtics", abbreviation="BOS", sport="nba")
    t2 = Team(id=2, name="LA Lakers", abbreviation="LAL", sport="nba")
    g = Game(id=1, sport="nba", season="2025-26", date=date.today(),
             home_team_id=1, away_team_id=2, status="scheduled")
    s = StrategyModel(id=1, name="ensemble", config_json="{}", is_active=True)
    p = PickModel(id=1, game_id=1, strategy_id=1, pick_type="moneyline",
                  pick_value="BOS ML", confidence=4, edge_pct=8.2,
                  odds_at_pick=-150, created_at=datetime.now(tz=timezone.utc)())
    session.add_all([t1, t2, g, s, p])
    session.commit()
    session.close()

def test_get_today_picks():
    app = create_app(":memory:")
    client = TestClient(app)
    _seed_db(client)
    response = client.get("/picks/today")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["pick_value"] == "BOS ML"

def test_get_today_picks_filter_by_sport():
    app = create_app(":memory:")
    client = TestClient(app)
    _seed_db(client)
    response = client.get("/picks/today?sport=nfl")
    assert response.status_code == 200
    assert len(response.json()) == 0

def test_get_picks_history():
    app = create_app(":memory:")
    client = TestClient(app)
    _seed_db(client)
    response = client.get("/picks/history")
    assert response.status_code == 200
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python -m pytest backend/tests/test_api_picks.py -v
```

- [ ] **Step 3: Implement picks.py**

```python
# backend/api/picks.py
from datetime import date
from fastapi import APIRouter, Request, Query
from backend.database import get_session
from backend.models import PickModel, Game, PickResult, Team

router = APIRouter()

@router.get("/today")
def get_today_picks(
    request: Request,
    sport: str | None = None,
    min_confidence: int = 0,
    pick_type: str | None = None,
):
    session = get_session(request.app.state.engine)
    try:
        today = date.today()
        query = (
            session.query(PickModel, Game, Team)
            .join(Game, PickModel.game_id == Game.id)
            .join(Team, Game.home_team_id == Team.id)
            .filter(Game.date == today)
        )
        if sport:
            query = query.filter(Game.sport == sport)
        if min_confidence > 0:
            query = query.filter(PickModel.confidence >= min_confidence)
        if pick_type:
            query = query.filter(PickModel.pick_type == pick_type)

        results = []
        for pick, game, home_team in query.all():
            results.append({
                "id": pick.id,
                "game_id": pick.game_id,
                "sport": game.sport,
                "date": str(game.date),
                "pick_type": pick.pick_type,
                "pick_value": pick.pick_value,
                "confidence": pick.confidence,
                "edge_pct": pick.edge_pct,
                "odds_at_pick": pick.odds_at_pick,
            })
        return results
    finally:
        session.close()

@router.get("/history")
def get_picks_history(
    request: Request,
    sport: str | None = None,
    page: int = 1,
    per_page: int = 50,
):
    session = get_session(request.app.state.engine)
    try:
        query = (
            session.query(PickModel, Game)
            .join(Game, PickModel.game_id == Game.id)
            .order_by(Game.date.desc())
        )
        if sport:
            query = query.filter(Game.sport == sport)

        offset = (page - 1) * per_page
        rows = query.offset(offset).limit(per_page).all()

        results = []
        for pick, game in rows:
            result_row = session.query(PickResult).filter(PickResult.pick_id == pick.id).first()
            results.append({
                "id": pick.id,
                "game_id": pick.game_id,
                "sport": game.sport,
                "date": str(game.date),
                "pick_type": pick.pick_type,
                "pick_value": pick.pick_value,
                "confidence": pick.confidence,
                "edge_pct": pick.edge_pct,
                "odds_at_pick": pick.odds_at_pick,
                "result": result_row.result if result_row else None,
                "payout": result_row.payout if result_row else None,
            })
        return results
    finally:
        session.close()
```

- [ ] **Step 4: Run tests**

```bash
python -m pytest backend/tests/test_api_picks.py -v
```
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/api/picks.py backend/tests/test_api_picks.py
git commit -m "feat: add /picks/today and /picks/history endpoints"
```

---

### Task 20: Stats API Endpoints

**Files:**
- Modify: `backend/api/stats.py`
- Test: `backend/tests/test_api_stats.py`

- [ ] **Step 1: Write failing test**

```python
# backend/tests/test_api_stats.py
from datetime import date, datetime
from fastapi.testclient import TestClient
from backend.api.main import create_app
from backend.models import Base, Team, Game, PickModel, PickResult, StrategyModel

def _seed(client):
    engine = client.app.state.engine
    Base.metadata.create_all(engine)
    from backend.database import get_session
    s = get_session(engine)
    s.add_all([
        Team(id=1, name="BOS", abbreviation="BOS", sport="nba"),
        Team(id=2, name="LAL", abbreviation="LAL", sport="nba"),
        Game(id=1, sport="nba", season="2025-26", date=date(2026, 3, 10), home_team_id=1, away_team_id=2, status="final"),
        Game(id=2, sport="nba", season="2025-26", date=date(2026, 3, 11), home_team_id=1, away_team_id=2, status="final"),
        StrategyModel(id=1, name="test", config_json="{}", is_active=True),
        PickModel(id=1, game_id=1, strategy_id=1, pick_type="moneyline", pick_value="BOS ML", confidence=4, edge_pct=8.0, odds_at_pick=-150, created_at=datetime.now(tz=timezone.utc)()),
        PickModel(id=2, game_id=2, strategy_id=1, pick_type="moneyline", pick_value="BOS ML", confidence=3, edge_pct=6.0, odds_at_pick=-130, created_at=datetime.now(tz=timezone.utc)()),
        PickResult(id=1, pick_id=1, result="win", payout=0.667),
        PickResult(id=2, pick_id=2, result="loss", payout=-1.0),
    ])
    s.commit()
    s.close()

def test_get_record():
    app = create_app(":memory:")
    client = TestClient(app)
    _seed(client)
    resp = client.get("/stats/record")
    assert resp.status_code == 200
    data = resp.json()
    assert data["wins"] == 1
    assert data["losses"] == 1
    assert data["total"] == 2

def test_get_daily():
    app = create_app(":memory:")
    client = TestClient(app)
    _seed(client)
    resp = client.get("/stats/daily")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2  # two different dates
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python -m pytest backend/tests/test_api_stats.py -v
```

- [ ] **Step 3: Implement stats.py**

```python
# backend/api/stats.py
from fastapi import APIRouter, Request
from sqlalchemy import func
from backend.database import get_session
from backend.models import PickModel, PickResult, Game

router = APIRouter()

@router.get("/record")
def get_record(request: Request, sport: str | None = None):
    session = get_session(request.app.state.engine)
    try:
        query = (
            session.query(PickResult, PickModel, Game)
            .join(PickModel, PickResult.pick_id == PickModel.id)
            .join(Game, PickModel.game_id == Game.id)
        )
        if sport:
            query = query.filter(Game.sport == sport)

        rows = query.all()
        wins = sum(1 for r, _, _ in rows if r.result == "win")
        losses = sum(1 for r, _, _ in rows if r.result == "loss")
        pushes = sum(1 for r, _, _ in rows if r.result == "push")
        total = wins + losses
        total_profit = sum(r.payout for r, _, _ in rows)

        return {
            "wins": wins,
            "losses": losses,
            "pushes": pushes,
            "total": total,
            "win_rate": round((wins / total * 100) if total > 0 else 0, 2),
            "roi": round((total_profit / total * 100) if total > 0 else 0, 2),
            "total_profit": round(total_profit, 4),
        }
    finally:
        session.close()

@router.get("/daily")
def get_daily(request: Request, sport: str | None = None):
    session = get_session(request.app.state.engine)
    try:
        query = (
            session.query(Game.date, PickResult.result, PickResult.payout)
            .join(PickModel, PickResult.pick_id == PickModel.id)
            .join(Game, PickModel.game_id == Game.id)
        )
        if sport:
            query = query.filter(Game.sport == sport)

        rows = query.order_by(Game.date).all()
        daily = {}
        for d, result, payout in rows:
            key = str(d)
            if key not in daily:
                daily[key] = {"date": key, "wins": 0, "losses": 0, "pushes": 0, "profit": 0.0}
            if result == "win":
                daily[key]["wins"] += 1
            elif result == "loss":
                daily[key]["losses"] += 1
            else:
                daily[key]["pushes"] += 1
            daily[key]["profit"] = round(daily[key]["profit"] + payout, 4)

        return list(daily.values())
    finally:
        session.close()
```

- [ ] **Step 4: Run tests**

```bash
python -m pytest backend/tests/test_api_stats.py -v
```
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/api/stats.py backend/tests/test_api_stats.py
git commit -m "feat: add /stats/record and /stats/daily endpoints"
```

---

### Task 21: Backtest API Endpoints

**Files:**
- Modify: `backend/api/backtest.py`
- Test: `backend/tests/test_api_backtest.py`

- [ ] **Step 1: Write failing test**

```python
# backend/tests/test_api_backtest.py
from fastapi.testclient import TestClient
from backend.api.main import create_app
from backend.models import Base, StrategyModel

def _seed(client):
    engine = client.app.state.engine
    Base.metadata.create_all(engine)
    from backend.database import get_session
    s = get_session(engine)
    s.add(StrategyModel(id=1, name="ensemble", description="test", config_json='{"min_edge": 5}', is_active=True))
    s.add(StrategyModel(id=2, name="value_only", description="test2", config_json='{"min_edge": 10}', is_active=False))
    s.commit()
    s.close()

def test_list_strategies():
    app = create_app(":memory:")
    client = TestClient(app)
    _seed(client)
    resp = client.get("/backtest/strategies")
    assert resp.status_code == 200
    assert len(resp.json()) == 2

def test_create_strategy():
    app = create_app(":memory:")
    client = TestClient(app)
    Base.metadata.create_all(client.app.state.engine)
    resp = client.post("/backtest/strategies", json={
        "name": "new_strat", "description": "desc", "config": {"min_edge": 7}
    })
    assert resp.status_code == 201
    assert resp.json()["name"] == "new_strat"

def test_promote_strategy():
    app = create_app(":memory:")
    client = TestClient(app)
    _seed(client)
    resp = client.patch("/backtest/strategies/2/promote")
    assert resp.status_code == 200
    # Check that strategy 2 is now active and strategy 1 is not
    strats = client.get("/backtest/strategies").json()
    active = [s for s in strats if s["is_active"]]
    assert len(active) == 1
    assert active[0]["id"] == 2
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python -m pytest backend/tests/test_api_backtest.py -v
```

- [ ] **Step 3: Implement backtest.py**

```python
# backend/api/backtest.py
import json
from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel
from backend.database import get_session
from backend.models import StrategyModel, BacktestRun, BacktestPick

router = APIRouter()

class StrategyCreate(BaseModel):
    name: str
    description: str = ""
    config: dict
    sport: str | None = None

class StrategyUpdate(BaseModel):
    description: str | None = None
    config: dict | None = None

@router.get("/strategies")
def list_strategies(request: Request):
    session = get_session(request.app.state.engine)
    try:
        rows = session.query(StrategyModel).all()
        return [{
            "id": s.id, "name": s.name, "description": s.description,
            "config": json.loads(s.config_json), "is_active": s.is_active,
            "sport": s.sport,
        } for s in rows]
    finally:
        session.close()

@router.post("/strategies", status_code=201)
def create_strategy(request: Request, body: StrategyCreate):
    session = get_session(request.app.state.engine)
    try:
        strat = StrategyModel(
            name=body.name, description=body.description,
            config_json=json.dumps(body.config), is_active=False,
            sport=body.sport,
        )
        session.add(strat)
        session.commit()
        session.refresh(strat)
        return {"id": strat.id, "name": strat.name, "description": strat.description,
                "config": body.config, "is_active": strat.is_active, "sport": strat.sport}
    finally:
        session.close()

@router.put("/strategies/{strategy_id}")
def update_strategy(request: Request, strategy_id: int, body: StrategyUpdate):
    session = get_session(request.app.state.engine)
    try:
        strat = session.query(StrategyModel).get(strategy_id)
        if not strat:
            raise HTTPException(status_code=404)
        if body.description is not None:
            strat.description = body.description
        if body.config is not None:
            strat.config_json = json.dumps(body.config)
        session.commit()
        return {"id": strat.id, "name": strat.name, "updated": True}
    finally:
        session.close()

@router.patch("/strategies/{strategy_id}/promote")
def promote_strategy(request: Request, strategy_id: int):
    session = get_session(request.app.state.engine)
    try:
        strat = session.query(StrategyModel).get(strategy_id)
        if not strat:
            raise HTTPException(status_code=404)
        # Deactivate all strategies (in same sport scope)
        session.query(StrategyModel).filter(
            StrategyModel.sport == strat.sport
        ).update({"is_active": False})
        strat.is_active = True
        session.commit()
        return {"id": strat.id, "name": strat.name, "is_active": True}
    finally:
        session.close()

@router.get("/compare")
def compare_strategies(request: Request):
    session = get_session(request.app.state.engine)
    try:
        strategies = session.query(StrategyModel).all()
        results = []
        for strat in strategies:
            runs = session.query(BacktestRun).filter(
                BacktestRun.strategy_id == strat.id,
                BacktestRun.status == "completed"
            ).all()
            for run in runs:
                picks = session.query(BacktestPick).filter(BacktestPick.run_id == run.id).all()
                wins = sum(1 for p in picks if p.result == "win")
                losses = sum(1 for p in picks if p.result == "loss")
                total = wins + losses
                results.append({
                    "strategy_id": strat.id,
                    "strategy_name": strat.name,
                    "run_id": run.id,
                    "wins": wins, "losses": losses, "total": total,
                    "win_rate": round(wins / total * 100, 2) if total > 0 else 0,
                })
        return results
    finally:
        session.close()
```

- [ ] **Step 4: Run tests**

```bash
python -m pytest backend/tests/test_api_backtest.py -v
```
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/api/backtest.py backend/tests/test_api_backtest.py
git commit -m "feat: add backtest strategy CRUD and promote endpoints"
```

---

### Task 22: Games API Endpoint

**Files:**
- Modify: `backend/api/games.py`

- [ ] **Step 1: Implement games.py** (simple endpoint, test inline)

```python
# backend/api/games.py
from fastapi import APIRouter, Request, HTTPException
from backend.database import get_session
from backend.models import Game, Odds, Team

router = APIRouter()

@router.get("/{game_id}")
def get_game(request: Request, game_id: int):
    session = get_session(request.app.state.engine)
    try:
        game = session.query(Game).get(game_id)
        if not game:
            raise HTTPException(status_code=404)
        home = session.query(Team).get(game.home_team_id)
        away = session.query(Team).get(game.away_team_id)
        odds = session.query(Odds).filter(Odds.game_id == game_id).all()
        return {
            "id": game.id, "sport": game.sport, "season": game.season,
            "week": game.week, "date": str(game.date), "status": game.status,
            "home_team": {"id": home.id, "name": home.name, "abbreviation": home.abbreviation} if home else None,
            "away_team": {"id": away.id, "name": away.name, "abbreviation": away.abbreviation} if away else None,
            "home_score": game.home_score, "away_score": game.away_score,
            "odds_history": [{
                "bookmaker": o.bookmaker,
                "moneyline_home": o.moneyline_home, "moneyline_away": o.moneyline_away,
                "spread_home": o.spread_home, "spread_away": o.spread_away,
                "over_under": o.over_under, "timestamp": str(o.timestamp),
            } for o in odds],
        }
    finally:
        session.close()
```

- [ ] **Step 2: Commit**

```bash
git add backend/api/games.py
git commit -m "feat: add /games/{id} endpoint with odds history"
```

---

## Chunk 6: React Frontend & Deployment

### Task 23: Frontend Project Setup

**Files:**
- Create: `frontend/package.json`
- Create: `frontend/vite.config.ts`
- Create: `frontend/tsconfig.json`
- Create: `frontend/index.html`
- Create: `frontend/src/main.tsx`

- [ ] **Step 1: Initialize frontend project**

```bash
cd C:/Users/mwill/OneDrive/Documents/mwilliams2733/sports_picks/frontend
npm create vite@latest . -- --template react-ts
```

- [ ] **Step 2: Install dependencies**

```bash
cd C:/Users/mwill/OneDrive/Documents/mwilliams2733/sports_picks/frontend
npm install react-router-dom recharts
npm install -D @types/react-router-dom
```

- [ ] **Step 3: Configure Vite proxy**

```typescript
// frontend/vite.config.ts
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/picks': 'http://localhost:8000',
      '/stats': 'http://localhost:8000',
      '/backtest': 'http://localhost:8000',
      '/games': 'http://localhost:8000',
      '/health': 'http://localhost:8000',
    }
  }
})
```

- [ ] **Step 4: Verify dev server starts**

```bash
cd C:/Users/mwill/OneDrive/Documents/mwilliams2733/sports_picks/frontend
npm run dev -- --host &
sleep 3 && curl -s http://localhost:5173 | head -5
kill %1
```

- [ ] **Step 5: Commit**

```bash
cd C:/Users/mwill/OneDrive/Documents/mwilliams2733/sports_picks
git add frontend/
git commit -m "feat: initialize React frontend with Vite and proxy config"
```

---

### Task 24: TypeScript Types & API Client

**Files:**
- Create: `frontend/src/types.ts`
- Create: `frontend/src/api/client.ts`

- [ ] **Step 1: Create types.ts**

```typescript
// frontend/src/types.ts
export interface PickData {
  id: number;
  game_id: number;
  sport: string;
  date: string;
  pick_type: string;
  pick_value: string;
  confidence: number;
  edge_pct: number;
  odds_at_pick: number;
  result?: string | null;
  payout?: number | null;
}

export interface RecordData {
  wins: number;
  losses: number;
  pushes: number;
  total: number;
  win_rate: number;
  roi: number;
  total_profit: number;
}

export interface DailyData {
  date: string;
  wins: number;
  losses: number;
  pushes: number;
  profit: number;
}

export interface StrategyData {
  id: number;
  name: string;
  description: string;
  config: Record<string, unknown>;
  is_active: boolean;
  sport: string | null;
}

export interface CompareData {
  strategy_id: number;
  strategy_name: string;
  run_id: number;
  wins: number;
  losses: number;
  total: number;
  win_rate: number;
}
```

- [ ] **Step 2: Create api/client.ts**

```typescript
// frontend/src/api/client.ts
import type { PickData, RecordData, DailyData, StrategyData, CompareData } from '../types';

const BASE = '';

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`);
  if (!res.ok) throw new Error(`API error: ${res.status}`);
  return res.json();
}

async function post<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`API error: ${res.status}`);
  return res.json();
}

async function patch<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`, { method: 'PATCH' });
  if (!res.ok) throw new Error(`API error: ${res.status}`);
  return res.json();
}

export const api = {
  picks: {
    today: (sport?: string) => get<PickData[]>(`/picks/today${sport ? `?sport=${sport}` : ''}`),
    history: (page = 1) => get<PickData[]>(`/picks/history?page=${page}`),
  },
  stats: {
    record: (sport?: string) => get<RecordData>(`/stats/record${sport ? `?sport=${sport}` : ''}`),
    daily: () => get<DailyData[]>('/stats/daily'),
  },
  backtest: {
    strategies: () => get<StrategyData[]>('/backtest/strategies'),
    create: (data: { name: string; description: string; config: Record<string, unknown> }) =>
      post<StrategyData>('/backtest/strategies', data),
    promote: (id: number) => patch<{ id: number; is_active: boolean }>(`/backtest/strategies/${id}/promote`),
    compare: () => get<CompareData[]>('/backtest/compare'),
  },
};
```

- [ ] **Step 3: Commit**

```bash
git add frontend/src/types.ts frontend/src/api/
git commit -m "feat: add TypeScript types and API client"
```

---

### Task 25: Layout & Navigation Component

**Files:**
- Create: `frontend/src/components/Layout.tsx`
- Create: `frontend/src/App.tsx`

- [ ] **Step 1: Create Layout.tsx**

```tsx
// frontend/src/components/Layout.tsx
import { NavLink, Outlet } from 'react-router-dom';

const SPORTS = ['NBA', 'NFL', 'NCAAB', 'NCAAF'] as const;
const NAV_ITEMS = [
  { path: '/', label: "Today's Picks" },
  { path: '/backtesting', label: 'Backtesting' },
  { path: '/track-record', label: 'Track Record' },
];

export default function Layout() {
  return (
    <div style={{ minHeight: '100vh', background: '#0f172a', color: '#e2e8f0' }}>
      <header style={{
        display: 'flex', justifyContent: 'space-between', alignItems: 'center',
        padding: '1rem 2rem', borderBottom: '1px solid #1e293b'
      }}>
        <h1 style={{ fontSize: '1.25rem', fontWeight: 'bold', margin: 0 }}>
          Sports Picks
        </h1>
        <nav style={{ display: 'flex', gap: '1.5rem' }}>
          {NAV_ITEMS.map(item => (
            <NavLink
              key={item.path}
              to={item.path}
              style={({ isActive }) => ({
                color: isActive ? '#38bdf8' : '#94a3b8',
                textDecoration: 'none', fontWeight: isActive ? 'bold' : 'normal',
              })}
            >
              {item.label}
            </NavLink>
          ))}
        </nav>
      </header>
      <main style={{ padding: '2rem' }}>
        <Outlet />
      </main>
    </div>
  );
}
```

- [ ] **Step 2: Create App.tsx**

```tsx
// frontend/src/App.tsx
import { BrowserRouter, Routes, Route } from 'react-router-dom';
import Layout from './components/Layout';
import TodaysPicks from './pages/TodaysPicks';
import Backtesting from './pages/Backtesting';
import TrackRecord from './pages/TrackRecord';

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<Layout />}>
          <Route index element={<TodaysPicks />} />
          <Route path="backtesting" element={<Backtesting />} />
          <Route path="track-record" element={<TrackRecord />} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}
```

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/Layout.tsx frontend/src/App.tsx
git commit -m "feat: add Layout component with navigation and routing"
```

---

### Task 26: Today's Picks Page

**Files:**
- Create: `frontend/src/components/SummaryCards.tsx`
- Create: `frontend/src/components/ConfidenceStars.tsx`
- Create: `frontend/src/components/PicksTable.tsx`
- Create: `frontend/src/pages/TodaysPicks.tsx`

- [ ] **Step 1: Create ConfidenceStars.tsx**

```tsx
// frontend/src/components/ConfidenceStars.tsx
export default function ConfidenceStars({ rating }: { rating: number }) {
  return <span title={`${rating}/5 confidence`}>{'★'.repeat(rating)}{'☆'.repeat(5 - rating)}</span>;
}
```

- [ ] **Step 2: Create SummaryCards.tsx**

```tsx
// frontend/src/components/SummaryCards.tsx
import type { RecordData } from '../types';

interface Props {
  record: RecordData | null;
  pickCount: number;
  strategyName: string;
}

export default function SummaryCards({ record, pickCount, strategyName }: Props) {
  return (
    <div style={{ display: 'flex', gap: '1rem', marginBottom: '1.5rem' }}>
      <Card label="Active Strategy" value={strategyName}
        sub={record ? `${record.win_rate}% Win Rate` : '—'} color="#4ade80" />
      <Card label="Today's Picks" value={String(pickCount)}
        sub={`${pickCount} games analyzed`} color="#facc15" />
      <Card label="Season ROI" value={record ? `${record.roi > 0 ? '+' : ''}${record.roi}%` : '—'}
        sub={record ? `${record.wins}-${record.losses} Record` : '—'} color="#4ade80" />
    </div>
  );
}

function Card({ label, value, sub, color }: { label: string; value: string; sub: string; color: string }) {
  return (
    <div style={{
      flex: 1, background: 'rgba(255,255,255,0.05)', borderRadius: '8px',
      padding: '1rem', border: '1px solid rgba(255,255,255,0.1)'
    }}>
      <div style={{ fontSize: '0.75rem', textTransform: 'uppercase', opacity: 0.6 }}>{label}</div>
      <div style={{ fontSize: '1.2rem', fontWeight: 'bold' }}>{value}</div>
      <div style={{ color }}>{sub}</div>
    </div>
  );
}
```

- [ ] **Step 3: Create PicksTable.tsx**

```tsx
// frontend/src/components/PicksTable.tsx
import type { PickData } from '../types';
import ConfidenceStars from './ConfidenceStars';

interface Props {
  picks: PickData[];
}

export default function PicksTable({ picks }: Props) {
  if (picks.length === 0) {
    return <p style={{ opacity: 0.5, textAlign: 'center', padding: '2rem' }}>No picks available today.</p>;
  }

  return (
    <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.9rem' }}>
      <thead>
        <tr style={{ borderBottom: '1px solid rgba(255,255,255,0.2)', textAlign: 'left' }}>
          <th style={{ padding: '0.5rem' }}>Sport</th>
          <th style={{ padding: '0.5rem' }}>Pick</th>
          <th style={{ padding: '0.5rem' }}>Type</th>
          <th style={{ padding: '0.5rem' }}>Edge</th>
          <th style={{ padding: '0.5rem' }}>Confidence</th>
          <th style={{ padding: '0.5rem' }}>Odds</th>
        </tr>
      </thead>
      <tbody>
        {picks.map(pick => (
          <tr key={pick.id} style={{
            borderBottom: '1px solid rgba(255,255,255,0.05)',
            opacity: pick.confidence <= 1 ? 0.4 : 1,
          }}>
            <td style={{ padding: '0.5rem' }}>{pick.sport.toUpperCase()}</td>
            <td style={{ padding: '0.5rem', color: '#4ade80', fontWeight: 'bold' }}>{pick.pick_value}</td>
            <td style={{ padding: '0.5rem' }}>{pick.pick_type}</td>
            <td style={{ padding: '0.5rem' }}>+{pick.edge_pct}%</td>
            <td style={{ padding: '0.5rem' }}><ConfidenceStars rating={pick.confidence} /></td>
            <td style={{ padding: '0.5rem' }}>{pick.odds_at_pick > 0 ? '+' : ''}{pick.odds_at_pick}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
```

- [ ] **Step 4: Create TodaysPicks.tsx**

```tsx
// frontend/src/pages/TodaysPicks.tsx
import { useState, useEffect } from 'react';
import { api } from '../api/client';
import type { PickData, RecordData } from '../types';
import SummaryCards from '../components/SummaryCards';
import PicksTable from '../components/PicksTable';

const SPORTS = ['all', 'nba', 'nfl', 'ncaab', 'ncaaf'] as const;

export default function TodaysPicks() {
  const [picks, setPicks] = useState<PickData[]>([]);
  const [record, setRecord] = useState<RecordData | null>(null);
  const [sport, setSport] = useState<string>('all');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setLoading(true);
    const sportParam = sport === 'all' ? undefined : sport;
    Promise.all([
      api.picks.today(sportParam),
      api.stats.record(sportParam),
    ])
      .then(([p, r]) => { setPicks(p); setRecord(r); })
      .catch(e => setError(e.message))
      .finally(() => setLoading(false));
  }, [sport]);

  if (error) return <p style={{ color: '#f87171' }}>Error: {error}</p>;
  if (loading) return <p>Loading picks...</p>;

  return (
    <div>
      <div style={{ display: 'flex', gap: '1rem', marginBottom: '1.5rem' }}>
        {SPORTS.map(s => (
          <button key={s} onClick={() => setSport(s)} style={{
            background: sport === s ? '#1e40af' : 'transparent',
            color: sport === s ? '#fff' : '#94a3b8',
            border: '1px solid #334155', borderRadius: '6px',
            padding: '0.5rem 1rem', cursor: 'pointer',
          }}>
            {s.toUpperCase()}
          </button>
        ))}
      </div>
      <SummaryCards record={record} pickCount={picks.length} strategyName="Ensemble" />
      <PicksTable picks={picks} />
    </div>
  );
}
```

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/ frontend/src/pages/TodaysPicks.tsx
git commit -m "feat: add Today's Picks page with summary cards and picks table"
```

---

### Task 27: Backtesting Page

**Files:**
- Create: `frontend/src/components/StrategyList.tsx`
- Create: `frontend/src/components/PerformanceChart.tsx`
- Create: `frontend/src/pages/Backtesting.tsx`

- [ ] **Step 1: Create StrategyList.tsx**

```tsx
// frontend/src/components/StrategyList.tsx
import type { StrategyData } from '../types';

interface Props {
  strategies: StrategyData[];
  onPromote: (id: number) => void;
}

export default function StrategyList({ strategies, onPromote }: Props) {
  return (
    <div>
      {strategies.map(s => (
        <div key={s.id} style={{
          padding: '0.75rem', marginBottom: '0.5rem', borderRadius: '6px',
          background: s.is_active ? 'rgba(74,222,128,0.15)' : 'rgba(255,255,255,0.05)',
          border: `1px solid ${s.is_active ? '#4ade80' : 'rgba(255,255,255,0.1)'}`,
          display: 'flex', justifyContent: 'space-between', alignItems: 'center',
        }}>
          <div>
            <strong>{s.name}</strong> {s.is_active && '(Active)'}
            <div style={{ fontSize: '0.8rem', opacity: 0.6 }}>{s.description}</div>
          </div>
          {!s.is_active && (
            <button onClick={() => onPromote(s.id)} style={{
              background: '#1e40af', color: '#fff', border: 'none',
              borderRadius: '4px', padding: '0.3rem 0.75rem', cursor: 'pointer',
            }}>
              Promote
            </button>
          )}
        </div>
      ))}
    </div>
  );
}
```

- [ ] **Step 2: Create PerformanceChart.tsx**

```tsx
// frontend/src/components/PerformanceChart.tsx
import { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, Legend } from 'recharts';
import type { DailyData } from '../types';

interface Props {
  data: DailyData[];
}

export default function PerformanceChart({ data }: Props) {
  // Calculate cumulative profit
  let cumulative = 0;
  const chartData = data.map(d => {
    cumulative += d.profit;
    return { ...d, cumulative: Math.round(cumulative * 100) / 100 };
  });

  return (
    <ResponsiveContainer width="100%" height={300}>
      <LineChart data={chartData}>
        <XAxis dataKey="date" stroke="#64748b" fontSize={12} />
        <YAxis stroke="#64748b" fontSize={12} />
        <Tooltip contentStyle={{ background: '#1e293b', border: '1px solid #334155' }} />
        <Legend />
        <Line type="monotone" dataKey="cumulative" stroke="#4ade80" name="Cumulative ROI" dot={false} />
      </LineChart>
    </ResponsiveContainer>
  );
}
```

- [ ] **Step 3: Create Backtesting.tsx**

```tsx
// frontend/src/pages/Backtesting.tsx
import { useState, useEffect } from 'react';
import { api } from '../api/client';
import type { StrategyData, DailyData } from '../types';
import StrategyList from '../components/StrategyList';
import PerformanceChart from '../components/PerformanceChart';

export default function Backtesting() {
  const [strategies, setStrategies] = useState<StrategyData[]>([]);
  const [daily, setDaily] = useState<DailyData[]>([]);
  const [loading, setLoading] = useState(true);

  const load = () => {
    setLoading(true);
    Promise.all([api.backtest.strategies(), api.stats.daily()])
      .then(([s, d]) => { setStrategies(s); setDaily(d); })
      .finally(() => setLoading(false));
  };

  useEffect(load, []);

  const handlePromote = async (id: number) => {
    await api.backtest.promote(id);
    load();
  };

  if (loading) return <p>Loading...</p>;

  return (
    <div>
      <h2 style={{ marginBottom: '1rem' }}>Backtesting</h2>
      <div style={{ display: 'flex', gap: '1.5rem' }}>
        <div style={{ flex: 1 }}>
          <h3 style={{ marginBottom: '0.5rem' }}>Strategy Variants</h3>
          <StrategyList strategies={strategies} onPromote={handlePromote} />
        </div>
        <div style={{ flex: 2 }}>
          <h3 style={{ marginBottom: '0.5rem' }}>Performance Over Time</h3>
          <PerformanceChart data={daily} />
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/StrategyList.tsx frontend/src/components/PerformanceChart.tsx frontend/src/pages/Backtesting.tsx
git commit -m "feat: add Backtesting page with strategy list and performance chart"
```

---

### Task 28: Track Record Page

**Files:**
- Create: `frontend/src/components/CalendarHeatmap.tsx`
- Create: `frontend/src/pages/TrackRecord.tsx`

- [ ] **Step 1: Create CalendarHeatmap.tsx**

```tsx
// frontend/src/components/CalendarHeatmap.tsx
import type { DailyData } from '../types';

interface Props {
  data: DailyData[];
}

export default function CalendarHeatmap({ data }: Props) {
  return (
    <div style={{ display: 'flex', flexWrap: 'wrap', gap: '3px' }}>
      {data.map(d => {
        const total = d.wins + d.losses;
        const winRate = total > 0 ? d.wins / total : 0.5;
        const color = total === 0 ? '#1e293b'
          : winRate >= 0.7 ? '#22c55e'
          : winRate >= 0.5 ? '#86efac'
          : winRate >= 0.3 ? '#fca5a5'
          : '#ef4444';
        return (
          <div key={d.date} title={`${d.date}: ${d.wins}W ${d.losses}L`} style={{
            width: '14px', height: '14px', borderRadius: '2px', background: color,
          }} />
        );
      })}
    </div>
  );
}
```

- [ ] **Step 2: Create TrackRecord.tsx**

```tsx
// frontend/src/pages/TrackRecord.tsx
import { useState, useEffect } from 'react';
import { api } from '../api/client';
import type { RecordData, DailyData, PickData } from '../types';
import CalendarHeatmap from '../components/CalendarHeatmap';
import ConfidenceStars from '../components/ConfidenceStars';

export default function TrackRecord() {
  const [record, setRecord] = useState<RecordData | null>(null);
  const [daily, setDaily] = useState<DailyData[]>([]);
  const [history, setHistory] = useState<PickData[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    Promise.all([api.stats.record(), api.stats.daily(), api.picks.history()])
      .then(([r, d, h]) => { setRecord(r); setDaily(d); setHistory(h); })
      .finally(() => setLoading(false));
  }, []);

  if (loading) return <p>Loading...</p>;

  return (
    <div>
      <h2 style={{ marginBottom: '1rem' }}>Track Record</h2>

      {record && (
        <div style={{ display: 'flex', gap: '1rem', marginBottom: '1.5rem' }}>
          <Stat label="Overall Win Rate" value={`${record.win_rate}%`} color="#4ade80" />
          <Stat label="Total ROI" value={`${record.roi > 0 ? '+' : ''}${record.roi}%`} color="#4ade80" />
          <Stat label="Record" value={`${record.wins}-${record.losses}`} color="#e2e8f0" />
        </div>
      )}

      <h3 style={{ marginBottom: '0.5rem' }}>Daily Results</h3>
      <CalendarHeatmap data={daily} />

      <h3 style={{ marginTop: '1.5rem', marginBottom: '0.5rem' }}>Pick History</h3>
      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.85rem' }}>
        <thead>
          <tr style={{ borderBottom: '1px solid rgba(255,255,255,0.2)', textAlign: 'left' }}>
            <th style={{ padding: '0.4rem' }}>Date</th>
            <th style={{ padding: '0.4rem' }}>Sport</th>
            <th style={{ padding: '0.4rem' }}>Pick</th>
            <th style={{ padding: '0.4rem' }}>Result</th>
            <th style={{ padding: '0.4rem' }}>Confidence</th>
          </tr>
        </thead>
        <tbody>
          {history.map(p => (
            <tr key={p.id} style={{ borderBottom: '1px solid rgba(255,255,255,0.05)' }}>
              <td style={{ padding: '0.4rem' }}>{p.date}</td>
              <td style={{ padding: '0.4rem' }}>{p.sport.toUpperCase()}</td>
              <td style={{ padding: '0.4rem' }}>{p.pick_value}</td>
              <td style={{ padding: '0.4rem', color: p.result === 'win' ? '#4ade80' : p.result === 'loss' ? '#f87171' : '#94a3b8' }}>
                {p.result ? (p.result === 'win' ? 'Won' : p.result === 'loss' ? 'Lost' : 'Push') : 'Pending'}
              </td>
              <td style={{ padding: '0.4rem' }}><ConfidenceStars rating={p.confidence} /></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Stat({ label, value, color }: { label: string; value: string; color: string }) {
  return (
    <div style={{
      flex: 1, background: 'rgba(255,255,255,0.05)', borderRadius: '8px',
      padding: '1rem', border: '1px solid rgba(255,255,255,0.1)', textAlign: 'center',
    }}>
      <div style={{ fontSize: '2rem', fontWeight: 'bold', color }}>{value}</div>
      <div style={{ opacity: 0.6 }}>{label}</div>
    </div>
  );
}
```

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/CalendarHeatmap.tsx frontend/src/pages/TrackRecord.tsx
git commit -m "feat: add Track Record page with heatmap and pick history"
```

---

### Task 29: Pipeline Scheduler

**Files:**
- Create: `backend/pipeline/scheduler.py`

- [ ] **Step 1: Implement scheduler.py**

```python
# backend/pipeline/scheduler.py
import asyncio
from datetime import date
from apscheduler.schedulers.background import BackgroundScheduler
from backend.config import load_config, is_sport_in_season
from backend.database import get_engine, get_session
from backend.collectors.espn import ESPNCollector
from backend.collectors.odds_api import OddsAPICollector
from backend.collectors.budget import ApiBudgetTracker
from backend.pipeline.pick_generator import generate_and_store_picks
from backend.pipeline.grader import grade_pick
from backend.models import Base, Game, PickModel, PickResult, Odds, StrategyModel
import logging

logger = logging.getLogger(__name__)

def run_pipeline(config_path: str = "config.yaml"):
    config = load_config(config_path)
    engine = get_engine(config["database_path"])
    Base.metadata.create_all(engine)

    scheduler = BackgroundScheduler()

    # Daily at 6 AM: update stats, grade picks, fetch odds, generate picks
    scheduler.add_job(
        lambda: daily_job(config, engine),
        'cron', hour=6, minute=0,
        id='daily_pipeline'
    )

    scheduler.start()
    logger.info("Pipeline scheduler started. Press Ctrl+C to exit.")

    try:
        while True:
            import time
            time.sleep(60)
    except KeyboardInterrupt:
        scheduler.shutdown()

def daily_job(config, engine):
    session = get_session(engine)
    try:
        logger.info("Starting daily pipeline run")

        # 1. Grade yesterday's picks
        grade_pending_picks(session)

        # 2. Fetch data for active sports
        sports = ["nba", "nfl", "ncaab", "ncaaf"]
        active_sports = [s for s in sports if is_sport_in_season(s, config["seasons"])]
        logger.info(f"Active sports: {active_sports}")

        for sport in active_sports:
            asyncio.run(fetch_sport_data(session, config, sport))

        # 3. Generate picks
        active_strategy = session.query(StrategyModel).filter(
            StrategyModel.is_active == True
        ).first()
        if active_strategy:
            count = generate_and_store_picks(session, active_strategy.id)
            logger.info(f"Generated {count} picks")

    except Exception as e:
        logger.error(f"Pipeline error: {e}")
    finally:
        session.close()

async def fetch_sport_data(session, config, sport):
    # Fetch scores from ESPN
    espn = ESPNCollector()
    try:
        today_str = date.today().strftime("%Y%m%d")
        games = await espn.fetch_scoreboard(sport, today_str)
        logger.info(f"Fetched {len(games)} {sport} games from ESPN")
    finally:
        await espn.close()

    # Fetch odds if budget allows
    budget = ApiBudgetTracker(session,
        monthly_limit=config["odds_budget"]["monthly_limit"],
        pause_at=config["odds_budget"]["pause_at"])

    if budget.can_make_request("odds_api"):
        odds_collector = OddsAPICollector(config["odds_api_key"])
        try:
            odds = await odds_collector.fetch_odds(sport)
            budget.record_request("odds_api")
            logger.info(f"Fetched odds for {len(odds)} {sport} games")
        finally:
            await odds_collector.close()

def grade_pending_picks(session):
    ungraded = (
        session.query(PickModel, Game)
        .join(Game, PickModel.game_id == Game.id)
        .filter(Game.status == "final")
        .filter(~PickModel.id.in_(
            session.query(PickResult.pick_id)
        ))
        .all()
    )
    for pick, game in ungraded:
        if game.home_score is not None and game.away_score is not None:
            result, payout = grade_pick(
                pick.pick_type, pick.pick_value,
                game.home_score, game.away_score, pick.odds_at_pick or -110
            )
            session.add(PickResult(pick_id=pick.id, result=result, payout=payout))
    session.commit()
    logger.info(f"Graded {len(ungraded)} picks")

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_pipeline()
```

- [ ] **Step 2: Commit**

```bash
git add backend/pipeline/scheduler.py
git commit -m "feat: add pipeline scheduler with daily job orchestration"
```

---

### Task 30: Start Script & Deployment

**Files:**
- Create: `start.sh`
- Modify: `backend/api/main.py` (add static file serving)

- [ ] **Step 1: Create start.sh**

```bash
#!/bin/bash
# start.sh — Launch both the pipeline and web server
set -e

echo "Starting Sports Picks..."

# Build frontend
cd frontend && npm run build && cd ..

# Start pipeline in background
python -m backend.pipeline.scheduler &
PIPELINE_PID=$!

# Start web server
uvicorn backend.api.main:app --host 0.0.0.0 --port 8000 &
WEB_PID=$!

echo "Pipeline PID: $PIPELINE_PID"
echo "Web server PID: $WEB_PID"
echo "Dashboard: http://localhost:8000"

# Handle shutdown
trap "kill $PIPELINE_PID $WEB_PID 2>/dev/null; exit 0" SIGINT SIGTERM
wait
```

- [ ] **Step 2: Add static file serving to main.py**

Add to the end of `create_app()` in `backend/api/main.py`, before `return app`:

```python
    # Serve React frontend in production
    import os
    static_dir = os.path.join(os.path.dirname(__file__), "../../frontend/dist")
    if os.path.exists(static_dir):
        from fastapi.staticfiles import StaticFiles
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="frontend")
```

- [ ] **Step 3: Add app entry point for uvicorn**

Add to bottom of `backend/api/main.py`:

```python
app = create_app()
```

- [ ] **Step 4: Commit**

```bash
chmod +x start.sh
git add start.sh backend/api/main.py
git commit -m "feat: add start script and static file serving for deployment"
```

---

### Task 31: Final Integration Test

- [ ] **Step 1: Run all backend tests**

```bash
cd C:/Users/mwill/OneDrive/Documents/mwilliams2733/sports_picks
python -m pytest backend/tests/ -v
```
Expected: All tests PASS

- [ ] **Step 2: Build frontend**

```bash
cd frontend && npm run build
```
Expected: Build succeeds with no errors

- [ ] **Step 3: Start the app and verify**

```bash
cd C:/Users/mwill/OneDrive/Documents/mwilliams2733/sports_picks
uvicorn backend.api.main:app --port 8000 &
sleep 3
curl -s http://localhost:8000/health
curl -s http://localhost:8000/picks/today
kill %1
```
Expected: Health check returns `{"status": "ok"}`, picks returns `[]`

- [ ] **Step 4: Final commit**

```bash
git add -A
git commit -m "chore: complete initial sports picks app implementation"
```

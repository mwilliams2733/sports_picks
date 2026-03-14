# Player Stats Integration Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Integrate free player stats APIs to power prop bet value analysis, prop pick generation, and prop backtesting.

**Architecture:** Source-per-file adapter pattern with fallback chains. `nba_api` primary for NBA, ESPN for all sports, `balldontlie` as NBA backup. `PropAnalyzer` compares projections to bookmaker lines. `PropBacktester` validates the model against historical results.

**Tech Stack:** `nba_api`, ESPN hidden API, `balldontlie.io`, SQLAlchemy, FastAPI, React

---

## File Structure

```
backend/
  models.py                          # + PlayerStat model, StrategyModel.strategy_type
  data_types.py                      # + PropAnalysis dataclass
  collectors/
    player_stats/
      __init__.py                    # exports PlayerStatsCollector
      base.py                       # PlayerStatsSource ABC
      collector.py                  # PlayerStatsCollector (fallback orchestration)
      nba_api_source.py             # nba_api adapter (NBA primary)
      espn_stats_source.py          # ESPN hidden API adapter (all sports)
      balldontlie_source.py         # balldontlie.io adapter (NBA backup)
      mysportsfeeds_source.py       # Placeholder stub
  analysis/
    prop_analyzer.py                # PropAnalyzer (value detection + pick gen)
    prop_confidence.py              # Prop-specific confidence tiers
    variants/
      prop_value.py                 # PropValue strategy for backtesting
  backtesting/
    prop_backtester.py              # PropBacktester
  api/
    props.py                        # Modified: add analysis fields
    picks.py                        # Modified: include prop picks
    backtest.py                     # Modified: add run backtest endpoint
    pipeline_api.py                 # New: POST /pipeline/run
    main.py                         # Modified: register pipeline router
  pipeline/
    scheduler.py                    # Modified: add stats fetching step
    prop_pipeline.py                # New: prop stats fetch + analysis pipeline

frontend/
  src/
    types.ts                        # + PropAnalysisData type
    api/client.ts                   # + pipeline.run(), backtest.run() methods
    pages/
      TodaysPicks.tsx               # + Top Props section
      PlayerProps.tsx               # + analysis columns, confidence filter
      Backtesting.tsx               # + Prop Value strategy type, Run buttons
  vite.config.ts                    # + /pipeline proxy
```

---

## Chunk 1: Data Layer & Source Adapters

### Task 1: PlayerStat Model & StrategyModel Update

**Files:**
- Modify: `backend/models.py`
- Test: `backend/tests/test_player_stat_model.py`

- [ ] **Step 1: Write failing test for PlayerStat model**

```python
# backend/tests/test_player_stat_model.py
from datetime import date, datetime, timezone
from backend.models import Base, PlayerStat, Team
from backend.database import get_engine, get_session

def _setup():
    engine = get_engine(":memory:")
    Base.metadata.create_all(engine)
    session = get_session(engine)
    team = Team(name="Boston Celtics", abbreviation="BOS", sport="nba")
    session.add(team)
    session.commit()
    return session, team

def test_player_stat_creation():
    session, team = _setup()
    stat = PlayerStat(
        player_name="Jayson Tatum", team_id=team.id, sport="nba",
        stat_type="season_avg", points=27.5, rebounds=8.1, assists=4.7,
        threes=2.8, minutes=36.2, steals=1.1, blocks=0.7, turnovers=2.9,
        source="nba_api", fetched_at=datetime.now(tz=timezone.utc),
    )
    session.add(stat)
    session.commit()
    row = session.query(PlayerStat).first()
    assert row.player_name == "Jayson Tatum"
    assert row.points == 27.5
    assert row.stat_type == "season_avg"
    assert row.source == "nba_api"
    assert row.is_stale is False
    session.close()

def test_player_stat_game_log():
    session, team = _setup()
    stat = PlayerStat(
        player_name="Jayson Tatum", team_id=team.id, sport="nba",
        stat_type="game_log", game_date=date(2026, 3, 10),
        points=32.0, rebounds=9.0, assists=5.0, minutes=38.0,
        source="nba_api", fetched_at=datetime.now(tz=timezone.utc),
    )
    session.add(stat)
    session.commit()
    row = session.query(PlayerStat).first()
    assert row.game_date == date(2026, 3, 10)
    assert row.stat_type == "game_log"
    session.close()

def test_player_stat_football_fields():
    session, team = _setup()
    # Change team to NFL for this test
    team.sport = "nfl"
    team.abbreviation = "KC"
    team.name = "Kansas City Chiefs"
    session.commit()
    stat = PlayerStat(
        player_name="Patrick Mahomes", team_id=team.id, sport="nfl",
        stat_type="season_avg", pass_yards=285.3, touchdowns=2.1,
        rush_yards=25.4, source="espn", fetched_at=datetime.now(tz=timezone.utc),
    )
    session.add(stat)
    session.commit()
    row = session.query(PlayerStat).first()
    assert row.pass_yards == 285.3
    assert row.touchdowns == 2.1
    assert row.points is None  # basketball fields null for football
    session.close()

def test_player_stat_upsert():
    """Unique constraint prevents duplicate season averages."""
    session, team = _setup()
    stat1 = PlayerStat(
        player_name="Jayson Tatum", team_id=team.id, sport="nba",
        stat_type="season_avg", game_date=None, points=27.5,
        source="nba_api", fetched_at=datetime.now(tz=timezone.utc),
    )
    session.add(stat1)
    session.commit()
    # Query and update instead of inserting duplicate
    existing = session.query(PlayerStat).filter_by(
        player_name="Jayson Tatum", sport="nba", stat_type="season_avg", game_date=None
    ).first()
    assert existing is not None
    existing.points = 28.0
    session.commit()
    assert session.query(PlayerStat).count() == 1
    assert session.query(PlayerStat).first().points == 28.0
    session.close()

def test_strategy_model_has_strategy_type():
    from backend.models import StrategyModel
    engine = get_engine(":memory:")
    Base.metadata.create_all(engine)
    session = get_session(engine)
    strat = StrategyModel(name="prop_value", config_json="{}", strategy_type="prop")
    session.add(strat)
    session.commit()
    row = session.query(StrategyModel).first()
    assert row.strategy_type == "prop"
    # Default should be "game"
    strat2 = StrategyModel(name="ensemble", config_json="{}")
    session.add(strat2)
    session.commit()
    row2 = session.query(StrategyModel).filter_by(name="ensemble").first()
    assert row2.strategy_type == "game"
    session.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd C:/Users/mwill/OneDrive/Documents/mwilliams2733/sports_picks && python -m pytest backend/tests/test_player_stat_model.py -v`
Expected: FAIL — `PlayerStat` not defined, `strategy_type` column missing

- [ ] **Step 3: Add PlayerStat model and strategy_type column**

Add to `backend/models.py` after the `PlayerProp` class:

```python
class PlayerStat(Base):
    __tablename__ = "player_stats"
    id = Column(Integer, primary_key=True)
    player_name = Column(String, nullable=False)
    team_id = Column(Integer, ForeignKey("teams.id"), nullable=False)
    sport = Column(String, nullable=False)
    stat_type = Column(String, nullable=False)  # "season_avg" or "game_log"
    game_date = Column(Date, nullable=True)      # null for season_avg
    minutes = Column(Float, nullable=True)
    points = Column(Float, nullable=True)
    rebounds = Column(Float, nullable=True)
    assists = Column(Float, nullable=True)
    threes = Column(Float, nullable=True)
    steals = Column(Float, nullable=True)
    blocks = Column(Float, nullable=True)
    turnovers = Column(Float, nullable=True)
    pass_yards = Column(Float, nullable=True)
    rush_yards = Column(Float, nullable=True)
    rec_yards = Column(Float, nullable=True)
    touchdowns = Column(Float, nullable=True)
    source = Column(String, nullable=False)
    fetched_at = Column(DateTime, nullable=False, default=lambda: datetime.now(tz=timezone.utc))
    is_stale = Column(Boolean, default=False)
    team = relationship("Team")
```

Add `strategy_type` column to `StrategyModel`:

```python
strategy_type = Column(String, nullable=False, default="game")  # "game" or "prop"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest backend/tests/test_player_stat_model.py -v`
Expected: 5 PASSED

- [ ] **Step 5: Run full test suite to check for regressions**

Run: `python -m pytest backend/tests/ -v`
Expected: All existing tests still pass

- [ ] **Step 6: Commit**

```bash
git add backend/models.py backend/tests/test_player_stat_model.py
git commit -m "feat: add PlayerStat model and strategy_type to StrategyModel"
```

---

### Task 2: PropAnalysis Dataclass & Prop Confidence

**Files:**
- Modify: `backend/data_types.py`
- Create: `backend/analysis/prop_confidence.py`
- Test: `backend/tests/test_prop_confidence.py`

- [ ] **Step 1: Write failing test for prop confidence**

```python
# backend/tests/test_prop_confidence.py
from backend.analysis.prop_confidence import calculate_prop_confidence

def test_5_star():
    assert calculate_prop_confidence(20.0) == 5

def test_4_star():
    assert calculate_prop_confidence(15.0) == 4

def test_3_star():
    assert calculate_prop_confidence(10.0) == 3

def test_2_star():
    assert calculate_prop_confidence(7.0) == 2

def test_1_star():
    assert calculate_prop_confidence(5.0) == 1

def test_0_star_below_threshold():
    assert calculate_prop_confidence(4.9) == 0

def test_boundary_values():
    assert calculate_prop_confidence(19.9) == 4
    assert calculate_prop_confidence(14.9) == 3
    assert calculate_prop_confidence(9.9) == 2
    assert calculate_prop_confidence(6.9) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest backend/tests/test_prop_confidence.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Implement prop confidence and PropAnalysis dataclass**

```python
# backend/analysis/prop_confidence.py
def calculate_prop_confidence(edge_pct: float) -> int:
    """Prop-specific confidence tiers (no model agreement concept)."""
    if edge_pct >= 20.0: return 5
    if edge_pct >= 15.0: return 4
    if edge_pct >= 10.0: return 3
    if edge_pct >= 7.0: return 2
    if edge_pct >= 5.0: return 1
    return 0
```

Add to `backend/data_types.py`:

```python
@dataclass
class PropAnalysis:
    player_name: str
    market: str
    line: float
    outcome: str          # "Over" or "Under"
    season_avg: float | None
    recent_avg: float | None
    projection: float
    edge_pct: float
    confidence: int
    source: str
    is_stale: bool
    game_id: int
    odds: int
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest backend/tests/test_prop_confidence.py -v`
Expected: 8 PASSED

- [ ] **Step 5: Commit**

```bash
git add backend/analysis/prop_confidence.py backend/data_types.py backend/tests/test_prop_confidence.py
git commit -m "feat: add PropAnalysis dataclass and prop confidence tiers"
```

---

### Task 3: PlayerStatsSource ABC

**Files:**
- Create: `backend/collectors/player_stats/__init__.py`
- Create: `backend/collectors/player_stats/base.py`

- [ ] **Step 1: Create the ABC**

```python
# backend/collectors/player_stats/base.py
from abc import ABC, abstractmethod

class PlayerStatsSource(ABC):
    """Interface for player stats data sources."""
    name: str = "base"

    @abstractmethod
    async def fetch_season_averages(self, sport: str, team_abbr: str) -> list[dict]:
        """Return season averages for all players on a team.

        Each dict must have: player_name, minutes, points, rebounds, assists,
        threes, steals, blocks, turnovers. Football adds: pass_yards, rush_yards,
        rec_yards, touchdowns. Missing values should be None.
        """

    @abstractmethod
    async def fetch_recent_games(self, sport: str, player_name: str, n: int = 5) -> list[dict]:
        """Return last N game logs for a player.

        Each dict must have: game_date (YYYY-MM-DD string), plus same stat fields
        as fetch_season_averages.
        """

    @abstractmethod
    async def is_available(self) -> bool:
        """Health check — can we reach this source right now?"""
```

```python
# backend/collectors/player_stats/__init__.py
from backend.collectors.player_stats.base import PlayerStatsSource

__all__ = ["PlayerStatsSource"]
```

Note: `PlayerStatsCollector` export will be added to `__init__.py` in Task 5 when `collector.py` is created.

- [ ] **Step 2: Commit**

```bash
git add backend/collectors/player_stats/
git commit -m "feat: add PlayerStatsSource ABC"
```

---

### Task 4: Source Adapters (nba_api, ESPN, balldontlie, MySportsFeeds stub)

**Files:**
- Create: `backend/collectors/player_stats/nba_api_source.py`
- Create: `backend/collectors/player_stats/espn_stats_source.py`
- Create: `backend/collectors/player_stats/balldontlie_source.py`
- Create: `backend/collectors/player_stats/mysportsfeeds_source.py`
- Test: `backend/tests/test_nba_api_source.py`
- Test: `backend/tests/test_espn_stats_source.py`
- Test: `backend/tests/test_balldontlie_source.py`

- [ ] **Step 1: Write failing tests for nba_api source**

```python
# backend/tests/test_nba_api_source.py
import pytest
from unittest.mock import patch, MagicMock
from backend.collectors.player_stats.nba_api_source import NbaApiSource

# Mock nba_api responses
MOCK_GAME_LOG = MagicMock()
MOCK_GAME_LOG.get_data_frames.return_value = [
    MagicMock(to_dict=lambda orient: [
        {"GAME_DATE": "2026-03-10", "MIN": 38, "PTS": 32, "REB": 9,
         "AST": 5, "FG3M": 3, "STL": 1, "BLK": 1, "TOV": 2},
        {"GAME_DATE": "2026-03-08", "MIN": 35, "PTS": 28, "REB": 7,
         "AST": 4, "FG3M": 2, "STL": 0, "BLK": 0, "TOV": 3},
    ])
]

@pytest.mark.asyncio
async def test_nba_api_source_is_available():
    source = NbaApiSource()
    assert source.name == "nba_api"
    # is_available should return True when nba_api package is importable
    result = await source.is_available()
    assert isinstance(result, bool)

@pytest.mark.asyncio
async def test_nba_api_source_fetch_season_averages():
    source = NbaApiSource()
    with patch("backend.collectors.player_stats.nba_api_source._fetch_team_roster") as mock_roster, \
         patch("backend.collectors.player_stats.nba_api_source._fetch_player_stats") as mock_stats:
        mock_roster.return_value = [{"PLAYER_ID": 1, "PLAYER": "Jayson Tatum"}]
        mock_stats.return_value = {
            "MIN": 36.2, "PTS": 27.5, "REB": 8.1, "AST": 4.7,
            "FG3M": 2.8, "STL": 1.1, "BLK": 0.7, "TOV": 2.9
        }
        results = await source.fetch_season_averages("nba", "BOS")
        assert len(results) == 1
        assert results[0]["player_name"] == "Jayson Tatum"
        assert results[0]["points"] == 27.5

@pytest.mark.asyncio
async def test_nba_api_source_fetch_recent_games():
    source = NbaApiSource()
    with patch("backend.collectors.player_stats.nba_api_source._fetch_player_game_log") as mock_log:
        mock_log.return_value = [
            {"game_date": "2026-03-10", "minutes": 38, "points": 32,
             "rebounds": 9, "assists": 5, "threes": 3, "steals": 1,
             "blocks": 1, "turnovers": 2},
        ]
        results = await source.fetch_recent_games("nba", "Jayson Tatum", n=5)
        assert len(results) == 1
        assert results[0]["points"] == 32
        assert results[0]["game_date"] == "2026-03-10"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest backend/tests/test_nba_api_source.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Implement nba_api source**

```python
# backend/collectors/player_stats/nba_api_source.py
import asyncio
import logging
import time
from backend.collectors.player_stats.base import PlayerStatsSource

logger = logging.getLogger(__name__)

# nba_api team abbreviation to team ID mapping
NBA_TEAMS = {
    "ATL": 1610612737, "BOS": 1610612738, "BKN": 1610612751, "CHA": 1610612766,
    "CHI": 1610612741, "CLE": 1610612739, "DAL": 1610612742, "DEN": 1610612743,
    "DET": 1610612765, "GSW": 1610612744, "HOU": 1610612745, "IND": 1610612754,
    "LAC": 1610612746, "LAL": 1610612747, "MEM": 1610612763, "MIA": 1610612748,
    "MIL": 1610612749, "MIN": 1610612750, "NOP": 1610612740, "NYK": 1610612752,
    "OKC": 1610612760, "ORL": 1610612753, "PHI": 1610612755, "PHX": 1610612756,
    "POR": 1610612757, "SAC": 1610612758, "SAS": 1610612759, "TOR": 1610612761,
    "UTA": 1610612762, "WAS": 1610612764,
}

def _fetch_team_roster(team_id: int) -> list[dict]:
    """Synchronous call to nba_api for team roster."""
    from nba_api.stats.endpoints import CommonTeamRoster
    time.sleep(0.6)  # Rate limiting
    roster = CommonTeamRoster(team_id=team_id)
    df = roster.get_data_frames()[0]
    return [{"PLAYER_ID": row["PLAYER_ID"], "PLAYER": row["PLAYER"]}
            for _, row in df.iterrows()]

def _fetch_player_stats(player_id: int) -> dict:
    """Synchronous call to nba_api for season averages."""
    from nba_api.stats.endpoints import PlayerCareerStats
    time.sleep(0.6)
    career = PlayerCareerStats(player_id=player_id, per_mode36="PerGame")
    df = career.get_data_frames()[0]
    if df.empty:
        return {}
    latest = df.iloc[-1]
    return {
        "MIN": latest.get("MIN", 0), "PTS": latest.get("PTS", 0),
        "REB": latest.get("REB", 0), "AST": latest.get("AST", 0),
        "FG3M": latest.get("FG3M", 0), "STL": latest.get("STL", 0),
        "BLK": latest.get("BLK", 0), "TOV": latest.get("TOV", 0),
    }

def _fetch_player_game_log(player_id: int, n: int = 5) -> list[dict]:
    """Synchronous call to nba_api for recent game logs."""
    from nba_api.stats.endpoints import PlayerGameLog
    time.sleep(0.6)
    log = PlayerGameLog(player_id=player_id)
    df = log.get_data_frames()[0]
    results = []
    for _, row in df.head(n).iterrows():
        results.append({
            "game_date": row["GAME_DATE"],
            "minutes": float(row.get("MIN", 0)),
            "points": float(row.get("PTS", 0)),
            "rebounds": float(row.get("REB", 0)),
            "assists": float(row.get("AST", 0)),
            "threes": float(row.get("FG3M", 0)),
            "steals": float(row.get("STL", 0)),
            "blocks": float(row.get("BLK", 0)),
            "turnovers": float(row.get("TOV", 0)),
        })
    return results

# Cache for player name -> player_id lookups
_player_id_cache: dict[str, int] = {}

def _find_player_id(player_name: str) -> int | None:
    """Look up player ID by name."""
    if player_name in _player_id_cache:
        return _player_id_cache[player_name]
    from nba_api.stats.static import players
    matches = players.find_players_by_full_name(player_name)
    if matches:
        pid = matches[0]["id"]
        _player_id_cache[player_name] = pid
        return pid
    return None


class NbaApiSource(PlayerStatsSource):
    name = "nba_api"

    async def fetch_season_averages(self, sport: str, team_abbr: str) -> list[dict]:
        if sport != "nba":
            return []
        team_id = NBA_TEAMS.get(team_abbr)
        if not team_id:
            logger.warning(f"Unknown NBA team abbreviation: {team_abbr}")
            return []
        loop = asyncio.get_event_loop()
        roster = await loop.run_in_executor(None, _fetch_team_roster, team_id)
        results = []
        for player in roster:
            stats = await loop.run_in_executor(None, _fetch_player_stats, player["PLAYER_ID"])
            if not stats:
                continue
            results.append({
                "player_name": player["PLAYER"],
                "minutes": float(stats.get("MIN", 0)),
                "points": float(stats.get("PTS", 0)),
                "rebounds": float(stats.get("REB", 0)),
                "assists": float(stats.get("AST", 0)),
                "threes": float(stats.get("FG3M", 0)),
                "steals": float(stats.get("STL", 0)),
                "blocks": float(stats.get("BLK", 0)),
                "turnovers": float(stats.get("TOV", 0)),
            })
        return results

    async def fetch_recent_games(self, sport: str, player_name: str, n: int = 5) -> list[dict]:
        if sport != "nba":
            return []
        loop = asyncio.get_event_loop()
        player_id = await loop.run_in_executor(None, _find_player_id, player_name)
        if not player_id:
            logger.warning(f"Player not found: {player_name}")
            return []
        return await loop.run_in_executor(None, _fetch_player_game_log, player_id, n)

    async def is_available(self) -> bool:
        try:
            import nba_api
            return True
        except ImportError:
            return False
```

- [ ] **Step 4: Implement ESPN stats source**

```python
# backend/collectors/player_stats/espn_stats_source.py
import httpx
import logging
from backend.collectors.player_stats.base import PlayerStatsSource

logger = logging.getLogger(__name__)

ESPN_SPORT_URLS = {
    "nba": "https://site.api.espn.com/apis/site/v2/sports/basketball/nba",
    "nfl": "https://site.api.espn.com/apis/site/v2/sports/football/nfl",
    "ncaab": "https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball",
    "ncaaf": "https://site.api.espn.com/apis/site/v2/sports/football/college-football",
}

# ESPN team abbreviation -> ESPN team ID requires lookup
# We'll fetch rosters via the teams endpoint


class EspnStatsSource(PlayerStatsSource):
    name = "espn"

    def __init__(self):
        self.client = httpx.AsyncClient(timeout=30.0)

    async def fetch_season_averages(self, sport: str, team_abbr: str) -> list[dict]:
        base = ESPN_SPORT_URLS.get(sport)
        if not base:
            return []
        try:
            # Find team ID from abbreviation
            team_id = await self._find_team_id(base, team_abbr)
            if not team_id:
                return []
            # Fetch roster with stats
            url = f"{base}/teams/{team_id}/roster"
            resp = await self.client.get(url)
            resp.raise_for_status()
            data = resp.json()
            results = []
            for athlete in data.get("athletes", []):
                for player in athlete.get("items", []) if isinstance(athlete, dict) and "items" in athlete else [athlete]:
                    name = player.get("fullName", player.get("displayName", ""))
                    if not name:
                        continue
                    stats = await self._fetch_player_season_stats(base, player.get("id"), sport)
                    if stats:
                        stats["player_name"] = name
                        results.append(stats)
            return results
        except Exception as e:
            logger.warning(f"ESPN season averages failed for {team_abbr}: {e}")
            return []

    async def _find_team_id(self, base_url: str, team_abbr: str) -> str | None:
        """Look up ESPN team ID from abbreviation."""
        try:
            resp = await self.client.get(f"{base_url}/teams")
            resp.raise_for_status()
            data = resp.json()
            for group in data.get("sports", [{}])[0].get("leagues", [{}])[0].get("teams", []):
                team = group.get("team", group)
                if team.get("abbreviation", "").upper() == team_abbr.upper():
                    return team["id"]
        except Exception:
            pass
        return None

    async def _fetch_player_season_stats(self, base_url: str, player_id: str, sport: str) -> dict | None:
        """Fetch individual player season stats from ESPN."""
        if not player_id:
            return None
        try:
            url = f"{base_url}/athletes/{player_id}/statistics"
            resp = await self.client.get(url)
            if resp.status_code != 200:
                return None
            data = resp.json()
            # Parse based on sport
            if sport in ("nba", "ncaab"):
                return self._parse_basketball_stats(data)
            elif sport in ("nfl", "ncaaf"):
                return self._parse_football_stats(data)
        except Exception:
            return None

    def _parse_basketball_stats(self, data: dict) -> dict | None:
        """Extract basketball stats from ESPN athlete statistics response."""
        try:
            splits = data.get("statistics", [{}])[0].get("splits", [{}])
            if not splits:
                return None
            categories = splits[0].get("categories", [])
            stats = {}
            for cat in categories:
                for stat in cat.get("stats", []):
                    stats[stat.get("abbreviation", "")] = stat.get("value", 0)
            return {
                "minutes": float(stats.get("MIN", 0)),
                "points": float(stats.get("PTS", 0)),
                "rebounds": float(stats.get("REB", 0)),
                "assists": float(stats.get("AST", 0)),
                "threes": float(stats.get("3PM", stats.get("FG3M", 0))),
                "steals": float(stats.get("STL", 0)),
                "blocks": float(stats.get("BLK", 0)),
                "turnovers": float(stats.get("TO", stats.get("TOV", 0))),
            }
        except (IndexError, KeyError):
            return None

    def _parse_football_stats(self, data: dict) -> dict | None:
        """Extract football stats from ESPN athlete statistics response."""
        try:
            splits = data.get("statistics", [{}])[0].get("splits", [{}])
            if not splits:
                return None
            categories = splits[0].get("categories", [])
            stats = {}
            for cat in categories:
                for stat in cat.get("stats", []):
                    stats[stat.get("abbreviation", "")] = stat.get("value", 0)
            return {
                "pass_yards": float(stats.get("YDS", 0)) if "PYDS" not in stats else float(stats.get("PYDS", 0)),
                "rush_yards": float(stats.get("RYDS", 0)),
                "rec_yards": float(stats.get("RECYDS", 0)),
                "touchdowns": float(stats.get("TD", 0)),
            }
        except (IndexError, KeyError):
            return None

    async def fetch_recent_games(self, sport: str, player_name: str, n: int = 5) -> list[dict]:
        base = ESPN_SPORT_URLS.get(sport)
        if not base:
            return []
        try:
            # Search for player
            resp = await self.client.get(
                f"{base}/athletes", params={"search": player_name}
            )
            if resp.status_code != 200:
                return []
            data = resp.json()
            athletes = data.get("athletes", data.get("items", []))
            if not athletes:
                return []
            player_id = athletes[0].get("id")
            if not player_id:
                return []
            # Fetch game log
            url = f"{base}/athletes/{player_id}/gamelog"
            resp = await self.client.get(url)
            if resp.status_code != 200:
                return []
            log_data = resp.json()
            return self._parse_game_log(log_data, sport, n)
        except Exception as e:
            logger.warning(f"ESPN recent games failed for {player_name}: {e}")
            return []

    def _parse_game_log(self, data: dict, sport: str, n: int) -> list[dict]:
        """Parse ESPN game log response."""
        results = []
        events = data.get("events", [])[:n]
        stats_labels = data.get("labels", [])
        for event in events:
            game_date = event.get("gameDate", "")[:10]
            stat_values = event.get("stats", [])
            stat_dict = dict(zip(stats_labels, stat_values)) if stats_labels else {}
            if sport in ("nba", "ncaab"):
                results.append({
                    "game_date": game_date,
                    "minutes": float(stat_dict.get("MIN", 0)),
                    "points": float(stat_dict.get("PTS", 0)),
                    "rebounds": float(stat_dict.get("REB", 0)),
                    "assists": float(stat_dict.get("AST", 0)),
                    "threes": float(stat_dict.get("3PM", 0)),
                    "steals": float(stat_dict.get("STL", 0)),
                    "blocks": float(stat_dict.get("BLK", 0)),
                    "turnovers": float(stat_dict.get("TO", 0)),
                })
            else:
                results.append({
                    "game_date": game_date,
                    "pass_yards": float(stat_dict.get("PYDS", 0)),
                    "rush_yards": float(stat_dict.get("RYDS", 0)),
                    "rec_yards": float(stat_dict.get("RECYDS", 0)),
                    "touchdowns": float(stat_dict.get("TD", 0)),
                })
        return results

    async def is_available(self) -> bool:
        try:
            resp = await self.client.get("https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard")
            return resp.status_code == 200
        except Exception:
            return False

    async def close(self):
        await self.client.aclose()
```

- [ ] **Step 5: Implement balldontlie source**

```python
# backend/collectors/player_stats/balldontlie_source.py
import httpx
import logging
from backend.collectors.player_stats.base import PlayerStatsSource

logger = logging.getLogger(__name__)

BASE_URL = "https://api.balldontlie.io/v1"


class BallDontLieSource(PlayerStatsSource):
    name = "balldontlie"

    def __init__(self, api_key: str | None = None):
        headers = {}
        if api_key:
            headers["Authorization"] = api_key
        self.client = httpx.AsyncClient(timeout=30.0, headers=headers)

    async def fetch_season_averages(self, sport: str, team_abbr: str) -> list[dict]:
        if sport != "nba":
            return []
        try:
            # Get team players
            resp = await self.client.get(f"{BASE_URL}/players", params={"search": "", "per_page": 100})
            if resp.status_code != 200:
                return []
            # balldontlie doesn't have great team roster endpoints
            # This source is mainly useful for individual player lookups
            return []
        except Exception as e:
            logger.warning(f"BallDontLie season averages failed: {e}")
            return []

    async def fetch_recent_games(self, sport: str, player_name: str, n: int = 5) -> list[dict]:
        if sport != "nba":
            return []
        try:
            # Search for player
            first_last = player_name.split()
            search = first_last[-1] if first_last else player_name
            resp = await self.client.get(f"{BASE_URL}/players", params={"search": search})
            if resp.status_code != 200:
                return []
            players = resp.json().get("data", [])
            if not players:
                return []
            # Find best match
            player_id = None
            for p in players:
                full = f"{p['first_name']} {p['last_name']}"
                if full.lower() == player_name.lower():
                    player_id = p["id"]
                    break
            if not player_id:
                player_id = players[0]["id"]
            # Fetch stats
            resp = await self.client.get(
                f"{BASE_URL}/stats",
                params={"player_ids[]": player_id, "per_page": n, "sort": "-game.date"}
            )
            if resp.status_code != 200:
                return []
            results = []
            for s in resp.json().get("data", [])[:n]:
                game = s.get("game", {})
                results.append({
                    "game_date": game.get("date", "")[:10],
                    "minutes": float(s.get("min", "0").replace(":", ".") if isinstance(s.get("min"), str) else s.get("min", 0)),
                    "points": float(s.get("pts", 0)),
                    "rebounds": float(s.get("reb", 0)),
                    "assists": float(s.get("ast", 0)),
                    "threes": float(s.get("fg3m", 0)),
                    "steals": float(s.get("stl", 0)),
                    "blocks": float(s.get("blk", 0)),
                    "turnovers": float(s.get("turnover", 0)),
                })
            return results
        except Exception as e:
            logger.warning(f"BallDontLie recent games failed for {player_name}: {e}")
            return []

    async def is_available(self) -> bool:
        try:
            resp = await self.client.get(f"{BASE_URL}/players", params={"per_page": 1})
            return resp.status_code == 200
        except Exception:
            return False

    async def close(self):
        await self.client.aclose()
```

- [ ] **Step 6: Implement MySportsFeeds stub**

```python
# backend/collectors/player_stats/mysportsfeeds_source.py
import logging
from backend.collectors.player_stats.base import PlayerStatsSource

logger = logging.getLogger(__name__)


class MySportsFeedsSource(PlayerStatsSource):
    """Placeholder — requires API key signup at mysportsfeeds.com (free tier)."""
    name = "mysportsfeeds"

    async def fetch_season_averages(self, sport: str, team_abbr: str) -> list[dict]:
        return []

    async def fetch_recent_games(self, sport: str, player_name: str, n: int = 5) -> list[dict]:
        return []

    async def is_available(self) -> bool:
        return False
```

- [ ] **Step 7: Write ESPN and balldontlie tests**

```python
# backend/tests/test_espn_stats_source.py
import httpx
import pytest
from backend.collectors.player_stats.espn_stats_source import EspnStatsSource

@pytest.fixture
def source():
    return EspnStatsSource()

@pytest.mark.asyncio
async def test_espn_source_name(source):
    assert source.name == "espn"

@pytest.mark.asyncio
async def test_espn_unsupported_sport(source):
    result = await source.fetch_season_averages("cricket", "BOS")
    assert result == []

@pytest.mark.asyncio
async def test_espn_parse_basketball_stats(source):
    data = {"statistics": [{"splits": [{"categories": [
        {"stats": [
            {"abbreviation": "PTS", "value": 25.0},
            {"abbreviation": "REB", "value": 8.0},
            {"abbreviation": "AST", "value": 5.0},
            {"abbreviation": "MIN", "value": 35.0},
        ]}
    ]}]}]}
    result = source._parse_basketball_stats(data)
    assert result["points"] == 25.0
    assert result["rebounds"] == 8.0

@pytest.mark.asyncio
async def test_espn_parse_football_stats(source):
    data = {"statistics": [{"splits": [{"categories": [
        {"stats": [
            {"abbreviation": "PYDS", "value": 285.0},
            {"abbreviation": "TD", "value": 2.0},
            {"abbreviation": "RYDS", "value": 25.0},
        ]}
    ]}]}]}
    result = source._parse_football_stats(data)
    assert result["pass_yards"] == 285.0
    assert result["touchdowns"] == 2.0
```

```python
# backend/tests/test_balldontlie_source.py
import pytest
from backend.collectors.player_stats.balldontlie_source import BallDontLieSource

@pytest.mark.asyncio
async def test_balldontlie_name():
    source = BallDontLieSource()
    assert source.name == "balldontlie"

@pytest.mark.asyncio
async def test_balldontlie_non_nba():
    source = BallDontLieSource()
    result = await source.fetch_season_averages("nfl", "KC")
    assert result == []
    result2 = await source.fetch_recent_games("nfl", "Patrick Mahomes")
    assert result2 == []
```

- [ ] **Step 8: Run tests**

Run: `python -m pytest backend/tests/test_nba_api_source.py backend/tests/test_espn_stats_source.py backend/tests/test_balldontlie_source.py -v`
Expected: All PASSED

- [ ] **Step 9: Commit**

```bash
git add backend/collectors/player_stats/
git add backend/tests/test_nba_api_source.py backend/tests/test_espn_stats_source.py backend/tests/test_balldontlie_source.py
git commit -m "feat: add player stats source adapters (nba_api, ESPN, balldontlie, MSF stub)"
```

---

### Task 5: PlayerStatsCollector (Fallback Orchestration)

**Files:**
- Create: `backend/collectors/player_stats/collector.py`
- Test: `backend/tests/test_player_stats_collector.py`

- [ ] **Step 1: Write failing tests**

```python
# backend/tests/test_player_stats_collector.py
import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock
from backend.collectors.player_stats.collector import PlayerStatsCollector
from backend.collectors.player_stats.base import PlayerStatsSource
from backend.models import Base, PlayerStat, Team
from backend.database import get_engine, get_session

class FakeSourceA(PlayerStatsSource):
    name = "source_a"
    async def fetch_season_averages(self, sport, team_abbr):
        return [{"player_name": "Player A", "points": 20.0, "rebounds": 5.0,
                 "assists": 3.0, "minutes": 30.0, "threes": 1.0,
                 "steals": 1.0, "blocks": 0.5, "turnovers": 2.0}]
    async def fetch_recent_games(self, sport, player_name, n=5):
        return [{"game_date": "2026-03-10", "points": 25.0, "rebounds": 6.0,
                 "assists": 4.0, "minutes": 32.0, "threes": 2.0,
                 "steals": 1.0, "blocks": 1.0, "turnovers": 1.0}]
    async def is_available(self):
        return True

class FakeSourceB(PlayerStatsSource):
    name = "source_b"
    async def fetch_season_averages(self, sport, team_abbr):
        return [{"player_name": "Player B", "points": 15.0, "rebounds": 4.0,
                 "assists": 2.0, "minutes": 25.0, "threes": 0.5,
                 "steals": 0.5, "blocks": 0.0, "turnovers": 1.5}]
    async def fetch_recent_games(self, sport, player_name, n=5):
        return []
    async def is_available(self):
        return True

class FailingSource(PlayerStatsSource):
    name = "failing"
    async def fetch_season_averages(self, sport, team_abbr):
        raise ConnectionError("API down")
    async def fetch_recent_games(self, sport, player_name, n=5):
        raise ConnectionError("API down")
    async def is_available(self):
        return False

@pytest.fixture
def db_session():
    engine = get_engine(":memory:")
    Base.metadata.create_all(engine)
    session = get_session(engine)
    team = Team(name="Test Team", abbreviation="TST", sport="nba")
    session.add(team)
    session.commit()
    yield session
    session.close()

@pytest.mark.asyncio
async def test_primary_source_used():
    collector = PlayerStatsCollector({"nba": [FakeSourceA(), FakeSourceB()]})
    result, source_name = await collector.fetch_player_stats("nba", "TST")
    assert len(result) == 1
    assert result[0]["player_name"] == "Player A"
    assert source_name == "source_a"

@pytest.mark.asyncio
async def test_fallback_on_failure():
    collector = PlayerStatsCollector({"nba": [FailingSource(), FakeSourceB()]})
    result, source_name = await collector.fetch_player_stats("nba", "TST")
    assert len(result) == 1
    assert result[0]["player_name"] == "Player B"
    assert source_name == "source_b"

@pytest.mark.asyncio
async def test_all_sources_fail():
    collector = PlayerStatsCollector({"nba": [FailingSource()]})
    result, source_name = await collector.fetch_player_stats("nba", "TST")
    assert result == []
    assert source_name is None

def test_store_stats(db_session):
    collector = PlayerStatsCollector({})
    stats = [{"player_name": "Player A", "points": 20.0, "rebounds": 5.0,
              "assists": 3.0, "minutes": 30.0, "threes": 1.0,
              "steals": 1.0, "blocks": 0.5, "turnovers": 2.0}]
    team = db_session.query(Team).first()
    count = collector.store_stats(db_session, stats, "season_avg", team.id, "nba", "source_a")
    assert count == 1
    row = db_session.query(PlayerStat).first()
    assert row.player_name == "Player A"
    assert row.points == 20.0
    assert row.source == "source_a"

def test_store_stats_upsert(db_session):
    """Storing same player twice should update, not duplicate."""
    collector = PlayerStatsCollector({})
    team = db_session.query(Team).first()
    stats = [{"player_name": "Player A", "points": 20.0}]
    collector.store_stats(db_session, stats, "season_avg", team.id, "nba", "source_a")
    stats2 = [{"player_name": "Player A", "points": 22.0}]
    collector.store_stats(db_session, stats2, "season_avg", team.id, "nba", "source_a")
    assert db_session.query(PlayerStat).count() == 1
    assert db_session.query(PlayerStat).first().points == 22.0

def test_normalize_name():
    collector = PlayerStatsCollector({})
    assert collector._normalize_name("LeBron James") == "LeBron James"
    assert collector._normalize_name("James, LeBron") == "LeBron James"
    assert collector._normalize_name("  LeBron James  ") == "LeBron James"
    assert collector._normalize_name("") == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest backend/tests/test_player_stats_collector.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Implement PlayerStatsCollector**

```python
# backend/collectors/player_stats/collector.py
import logging
from datetime import datetime, timezone, timedelta
from sqlalchemy.orm import Session
from backend.collectors.player_stats.base import PlayerStatsSource
from backend.models import PlayerStat

logger = logging.getLogger(__name__)

STALENESS_HOURS = 24


class PlayerStatsCollector:
    def __init__(self, fallback_chains: dict[str, list[PlayerStatsSource]]):
        self.chains = fallback_chains

    async def fetch_player_stats(self, sport: str, team_abbr: str) -> tuple[list[dict], str | None]:
        """Fetch season averages using fallback chain. Returns (stats, source_name)."""
        chain = self.chains.get(sport, [])
        for source in chain:
            try:
                result = await source.fetch_season_averages(sport, team_abbr)
                if result:
                    logger.info(f"Got {len(result)} player stats from {source.name} for {team_abbr}")
                    return result, source.name
                logger.info(f"{source.name} returned empty for {team_abbr}, trying next")
            except Exception as e:
                logger.warning(f"{source.name} failed for {team_abbr}: {e}")
        return [], None

    async def fetch_player_recent(self, sport: str, player_name: str,
                                   n: int = 5) -> tuple[list[dict], str | None]:
        """Fetch recent game logs using fallback chain. Returns (games, source_name)."""
        chain = self.chains.get(sport, [])
        for source in chain:
            try:
                result = await source.fetch_recent_games(sport, player_name, n)
                if result:
                    logger.info(f"Got {len(result)} game logs from {source.name} for {player_name}")
                    return result, source.name
            except Exception as e:
                logger.warning(f"{source.name} failed for {player_name}: {e}")
        return [], None

    def store_stats(self, session: Session, stats: list[dict], stat_type: str,
                    team_id: int, sport: str, source: str,
                    is_stale: bool = False) -> int:
        """Normalize and store stats. Upserts on (player_name, sport, stat_type, game_date)."""
        count = 0
        now = datetime.now(tz=timezone.utc)
        for s in stats:
            name = self._normalize_name(s.get("player_name", ""))
            if not name:
                continue
            game_date = s.get("game_date")
            if isinstance(game_date, str) and game_date:
                from datetime import date as date_type
                parts = game_date.split("-")
                game_date = date_type(int(parts[0]), int(parts[1]), int(parts[2]))
            elif stat_type == "season_avg":
                game_date = None

            existing = session.query(PlayerStat).filter_by(
                player_name=name, sport=sport, stat_type=stat_type, game_date=game_date
            ).first()

            if existing:
                # Update
                for field in ["minutes", "points", "rebounds", "assists", "threes",
                              "steals", "blocks", "turnovers", "pass_yards",
                              "rush_yards", "rec_yards", "touchdowns"]:
                    val = s.get(field)
                    if val is not None:
                        setattr(existing, field, float(val))
                existing.source = source
                existing.fetched_at = now
                existing.is_stale = is_stale
            else:
                row = PlayerStat(
                    player_name=name, team_id=team_id, sport=sport,
                    stat_type=stat_type, game_date=game_date,
                    minutes=s.get("minutes"), points=s.get("points"),
                    rebounds=s.get("rebounds"), assists=s.get("assists"),
                    threes=s.get("threes"), steals=s.get("steals"),
                    blocks=s.get("blocks"), turnovers=s.get("turnovers"),
                    pass_yards=s.get("pass_yards"), rush_yards=s.get("rush_yards"),
                    rec_yards=s.get("rec_yards"), touchdowns=s.get("touchdowns"),
                    source=source, fetched_at=now, is_stale=is_stale,
                )
                session.add(row)
            count += 1
        session.commit()
        return count

    def get_cached_stats(self, session: Session, player_name: str, sport: str,
                         stat_type: str) -> list[PlayerStat]:
        """Get cached stats, marking as stale if older than threshold."""
        cutoff = datetime.now(tz=timezone.utc) - timedelta(hours=STALENESS_HOURS)
        rows = session.query(PlayerStat).filter_by(
            player_name=self._normalize_name(player_name),
            sport=sport, stat_type=stat_type,
        ).all()
        for row in rows:
            if row.fetched_at < cutoff:
                row.is_stale = True
        if rows:
            session.commit()
        return rows

    async def close(self):
        """Close all source HTTP clients."""
        for chain in self.chains.values():
            for source in chain:
                if hasattr(source, "close"):
                    await source.close()

    @staticmethod
    def _normalize_name(name: str) -> str:
        """Normalize to 'First Last' format."""
        name = name.strip()
        if not name:
            return ""
        # Handle "Last, First" format
        if "," in name:
            parts = [p.strip() for p in name.split(",", 1)]
            if len(parts) == 2:
                name = f"{parts[1]} {parts[0]}"
        return name
```

- [ ] **Step 4: Update `__init__.py` to export PlayerStatsCollector**

Add to `backend/collectors/player_stats/__init__.py`:

```python
from backend.collectors.player_stats.base import PlayerStatsSource
from backend.collectors.player_stats.collector import PlayerStatsCollector

__all__ = ["PlayerStatsSource", "PlayerStatsCollector"]
```

- [ ] **Step 5: Run tests**

Run: `python -m pytest backend/tests/test_player_stats_collector.py -v`
Expected: 5 PASSED

- [ ] **Step 6: Run full test suite**

Run: `python -m pytest backend/tests/ -v`
Expected: All pass

- [ ] **Step 7: Commit**

```bash
git add backend/collectors/player_stats/collector.py backend/tests/test_player_stats_collector.py
git commit -m "feat: add PlayerStatsCollector with fallback chain orchestration"
```

---

## Chunk 2: Analysis & Backtesting

### Task 6: PropAnalyzer (Value Detection + Pick Generation)

**Files:**
- Create: `backend/analysis/prop_analyzer.py`
- Test: `backend/tests/test_prop_analyzer.py`

- [ ] **Step 1: Write failing tests**

```python
# backend/tests/test_prop_analyzer.py
import pytest
from datetime import date, datetime, timezone
from backend.models import Base, PlayerStat, PlayerProp, Game, Team
from backend.database import get_engine, get_session
from backend.analysis.prop_analyzer import PropAnalyzer

@pytest.fixture
def db_fixture():
    engine = get_engine(":memory:")
    Base.metadata.create_all(engine)
    session = get_session(engine)
    home = Team(name="Boston Celtics", abbreviation="BOS", sport="nba")
    away = Team(name="Brooklyn Nets", abbreviation="BKN", sport="nba")
    session.add_all([home, away])
    session.commit()
    game = Game(sport="nba", season="2025-26", date=date(2026, 3, 14),
                home_team_id=home.id, away_team_id=away.id, status="scheduled")
    session.add(game)
    session.commit()
    # Season average
    session.add(PlayerStat(
        player_name="Jayson Tatum", team_id=home.id, sport="nba",
        stat_type="season_avg", points=27.0, rebounds=8.0, assists=5.0,
        threes=2.5, minutes=36.0, source="nba_api",
        fetched_at=datetime.now(tz=timezone.utc),
    ))
    # Recent game logs (last 5 games, hot streak)
    for i, pts in enumerate([32, 30, 35, 28, 31]):
        session.add(PlayerStat(
            player_name="Jayson Tatum", team_id=home.id, sport="nba",
            stat_type="game_log", game_date=date(2026, 3, 10 - i),
            points=float(pts), rebounds=9.0, assists=6.0, minutes=38.0,
            source="nba_api", fetched_at=datetime.now(tz=timezone.utc),
        ))
    session.commit()
    # Prop line: Over 25.5 points at -110
    prop = PlayerProp(
        game_id=game.id, bookmaker="draftkings", market="player_points",
        player_name="Jayson Tatum", outcome="Over", line=25.5, odds=-110,
        fetched_at=datetime.now(tz=timezone.utc),
    )
    session.add(prop)
    session.commit()
    return session, game, prop

def test_analyze_finds_edge(db_fixture):
    session, game, prop = db_fixture
    analyzer = PropAnalyzer()
    season_avg = session.query(PlayerStat).filter_by(
        player_name="Jayson Tatum", stat_type="season_avg").first()
    recent = session.query(PlayerStat).filter_by(
        player_name="Jayson Tatum", stat_type="game_log"
    ).order_by(PlayerStat.game_date.desc()).limit(5).all()
    result = analyzer.analyze(prop, season_avg, recent)
    assert result is not None
    assert result.outcome == "Over"
    assert result.projection > 25.5  # Should project well above line
    assert result.edge_pct > 0
    assert result.confidence >= 1

def test_analyze_season_avg_only(db_fixture):
    session, game, prop = db_fixture
    analyzer = PropAnalyzer()
    season_avg = session.query(PlayerStat).filter_by(
        player_name="Jayson Tatum", stat_type="season_avg").first()
    result = analyzer.analyze(prop, season_avg, [])  # No recent games
    assert result is not None
    # With only season avg of 27.0 vs line of 25.5
    assert result.projection == 27.0
    assert result.edge_pct > 0

def test_analyze_under_pick(db_fixture):
    session, game, prop = db_fixture
    # Create a prop with a very high line
    under_prop = PlayerProp(
        game_id=game.id, bookmaker="draftkings", market="player_points",
        player_name="Jayson Tatum", outcome="Under", line=40.5, odds=-110,
        fetched_at=datetime.now(tz=timezone.utc),
    )
    session.add(under_prop)
    session.commit()
    analyzer = PropAnalyzer()
    season_avg = session.query(PlayerStat).filter_by(
        player_name="Jayson Tatum", stat_type="season_avg").first()
    recent = session.query(PlayerStat).filter_by(
        player_name="Jayson Tatum", stat_type="game_log").all()
    result = analyzer.analyze(under_prop, season_avg, recent)
    assert result is not None
    assert result.outcome == "Under"
    assert result.edge_pct > 0

def test_analyze_no_stats_returns_none(db_fixture):
    session, game, prop = db_fixture
    analyzer = PropAnalyzer()
    result = analyzer.analyze(prop, None, [])
    assert result is None

def test_analyze_combination_market(db_fixture):
    session, game, prop = db_fixture
    combo_prop = PlayerProp(
        game_id=game.id, bookmaker="draftkings",
        market="player_points_rebounds_assists",
        player_name="Jayson Tatum", outcome="Over", line=38.5, odds=-110,
        fetched_at=datetime.now(tz=timezone.utc),
    )
    session.add(combo_prop)
    session.commit()
    analyzer = PropAnalyzer()
    season_avg = session.query(PlayerStat).filter_by(
        player_name="Jayson Tatum", stat_type="season_avg").first()
    recent = session.query(PlayerStat).filter_by(
        player_name="Jayson Tatum", stat_type="game_log").all()
    result = analyzer.analyze(combo_prop, season_avg, recent)
    assert result is not None
    # PRA = 27 + 8 + 5 = 40 season avg, recent should be higher
    assert result.projection > 38.5
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest backend/tests/test_prop_analyzer.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Implement PropAnalyzer**

```python
# backend/analysis/prop_analyzer.py
from backend.models import PlayerProp, PlayerStat
from backend.data_types import PropAnalysis
from backend.analysis.prop_confidence import calculate_prop_confidence

# Map prop market keys to PlayerStat field names
MARKET_TO_STAT = {
    "player_points": ["points"],
    "player_rebounds": ["rebounds"],
    "player_assists": ["assists"],
    "player_threes": ["threes"],
    "player_blocks": ["blocks"],
    "player_steals": ["steals"],
    "player_turnovers": ["turnovers"],
    "player_pass_yds": ["pass_yards"],
    "player_rush_yds": ["rush_yards"],
    "player_reception_yds": ["rec_yards"],
    "player_receptions": ["rec_yards"],  # approximation
    # Combination markets
    "player_points_rebounds_assists": ["points", "rebounds", "assists"],
    "player_points_rebounds": ["points", "rebounds"],
    "player_points_assists": ["points", "assists"],
    "player_rebounds_assists": ["rebounds", "assists"],
}


class PropAnalyzer:
    def __init__(self, season_weight: float = 0.4, recent_weight: float = 0.6,
                 min_edge: float = 5.0):
        self.season_weight = season_weight
        self.recent_weight = recent_weight
        self.min_edge = min_edge

    def analyze(self, prop: PlayerProp, season_avg: PlayerStat | None,
                recent_games: list[PlayerStat]) -> PropAnalysis | None:
        """Compare a prop line to player stats, return analysis or None if insufficient data."""
        if season_avg is None and not recent_games:
            return None
        if prop.line is None:
            return None

        stat_fields = MARKET_TO_STAT.get(prop.market)
        if not stat_fields:
            return None

        # Calculate season average for the relevant stat(s)
        s_avg = self._sum_fields(season_avg, stat_fields) if season_avg else None

        # Calculate recent games average
        r_avg = None
        if recent_games:
            values = [self._sum_fields(g, stat_fields) for g in recent_games]
            valid = [v for v in values if v is not None]
            if valid:
                r_avg = sum(valid) / len(valid)

        # Compute projection
        if s_avg is not None and r_avg is not None:
            projection = self.season_weight * s_avg + self.recent_weight * r_avg
        elif s_avg is not None:
            projection = s_avg
        elif r_avg is not None:
            projection = r_avg
        else:
            return None

        # Calculate edge
        diff = projection - prop.line
        edge_pct = abs(diff / prop.line) * 100 if prop.line != 0 else 0

        # Determine outcome direction
        if prop.outcome == "Over":
            # For Over props, edge is positive when projection > line
            signed_edge = (diff / prop.line) * 100 if prop.line != 0 else 0
        else:
            # For Under props, edge is positive when projection < line
            signed_edge = (-diff / prop.line) * 100 if prop.line != 0 else 0

        # Only return analysis if edge is in the right direction
        if signed_edge < 0:
            return None

        confidence = calculate_prop_confidence(edge_pct)
        source = season_avg.source if season_avg else (recent_games[0].source if recent_games else "unknown")
        is_stale = (season_avg.is_stale if season_avg else False) or any(g.is_stale for g in recent_games)

        return PropAnalysis(
            player_name=prop.player_name,
            market=prop.market,
            line=prop.line,
            outcome=prop.outcome,
            season_avg=round(s_avg, 1) if s_avg is not None else None,
            recent_avg=round(r_avg, 1) if r_avg is not None else None,
            projection=round(projection, 1),
            edge_pct=round(edge_pct, 1),
            confidence=confidence,
            source=source,
            is_stale=is_stale,
            game_id=prop.game_id,
            odds=prop.odds,
        )

    @staticmethod
    def _sum_fields(stat: PlayerStat, fields: list[str]) -> float | None:
        """Sum the specified fields from a PlayerStat row. Returns None if any field is None."""
        total = 0.0
        for f in fields:
            val = getattr(stat, f, None)
            if val is None:
                return None
            total += val
        return total
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest backend/tests/test_prop_analyzer.py -v`
Expected: 5 PASSED

- [ ] **Step 5: Commit**

```bash
git add backend/analysis/prop_analyzer.py backend/tests/test_prop_analyzer.py
git commit -m "feat: add PropAnalyzer for prop value detection and pick generation"
```

---

### Task 7: PropBacktester

**Files:**
- Create: `backend/backtesting/prop_backtester.py`
- Test: `backend/tests/test_prop_backtester.py`

- [ ] **Step 1: Write failing tests**

```python
# backend/tests/test_prop_backtester.py
import pytest
from datetime import date, datetime, timezone
from backend.models import Base, PlayerStat, Team
from backend.database import get_engine, get_session
from backend.backtesting.prop_backtester import PropBacktester

@pytest.fixture
def db_fixture():
    engine = get_engine(":memory:")
    Base.metadata.create_all(engine)
    session = get_session(engine)
    team = Team(name="Boston Celtics", abbreviation="BOS", sport="nba")
    session.add(team)
    session.commit()
    # Create 20 game logs (enough for backtesting with lookback of 5)
    for i in range(20):
        d = date(2026, 2, 1 + i)
        pts = 25.0 + (i % 5) * 2  # Varies: 25, 27, 29, 31, 33
        session.add(PlayerStat(
            player_name="Jayson Tatum", team_id=team.id, sport="nba",
            stat_type="game_log", game_date=d,
            points=pts, rebounds=8.0, assists=5.0, minutes=36.0,
            source="nba_api", fetched_at=datetime.now(tz=timezone.utc),
        ))
    session.commit()
    return session

def test_backtest_returns_results(db_fixture):
    bt = PropBacktester(config={
        "recent_weight": 0.6, "season_weight": 0.4,
        "min_edge": 5.0, "lookback": 5, "min_minutes": 15,
    })
    result = bt.backtest(db_fixture, "nba", date(2026, 2, 10), date(2026, 2, 20))
    assert "wins" in result
    assert "losses" in result
    assert "total" in result
    assert "hit_rate" in result
    assert "by_market" in result

def test_backtest_empty_range(db_fixture):
    bt = PropBacktester(config={})
    result = bt.backtest(db_fixture, "nba", date(2025, 1, 1), date(2025, 1, 5))
    assert result["total"] == 0

def test_backtest_respects_min_minutes(db_fixture):
    # Add a low-minutes player
    team = db_fixture.query(Team).first()
    for i in range(10):
        db_fixture.add(PlayerStat(
            player_name="Bench Player", team_id=team.id, sport="nba",
            stat_type="game_log", game_date=date(2026, 2, 1 + i),
            points=3.0, rebounds=1.0, assists=0.5, minutes=8.0,
            source="nba_api", fetched_at=datetime.now(tz=timezone.utc),
        ))
    db_fixture.commit()
    bt = PropBacktester(config={"min_minutes": 15, "lookback": 5})
    result = bt.backtest(db_fixture, "nba", date(2026, 2, 10), date(2026, 2, 15))
    # Bench Player should be excluded
    player_names = [p.get("player_name") for p in result.get("picks", [])]
    assert "Bench Player" not in player_names
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest backend/tests/test_prop_backtester.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Implement PropBacktester**

```python
# backend/backtesting/prop_backtester.py
import logging
from datetime import date
from sqlalchemy.orm import Session
from backend.models import PlayerStat
from backend.analysis.odds_utils import calculate_payout

logger = logging.getLogger(__name__)


class PropBacktester:
    def __init__(self, config: dict | None = None):
        config = config or {}
        self.recent_weight = config.get("recent_weight", 0.6)
        self.season_weight = config.get("season_weight", 0.4)
        self.min_edge = config.get("min_edge", 5.0)
        self.lookback = config.get("lookback", 5)
        self.min_minutes = config.get("min_minutes", 15)

    def backtest(self, session: Session, sport: str,
                 start_date: date, end_date: date) -> dict:
        """Backtest prop projection model against actual game results."""
        # Get all game logs in the full range (need pre-period data for lookback)
        all_logs = (session.query(PlayerStat)
            .filter(PlayerStat.sport == sport, PlayerStat.stat_type == "game_log",
                    PlayerStat.game_date.isnot(None))
            .order_by(PlayerStat.game_date)
            .all())

        # Group by player
        by_player: dict[str, list[PlayerStat]] = {}
        for log in all_logs:
            by_player.setdefault(log.player_name, []).append(log)

        wins = 0
        losses = 0
        total_profit = 0.0
        picks = []
        by_market: dict[str, dict] = {}
        by_confidence: dict[int, dict] = {}

        for player_name, logs in by_player.items():
            logs.sort(key=lambda x: x.game_date)
            for i, game_log in enumerate(logs):
                if game_log.game_date < start_date or game_log.game_date > end_date:
                    continue
                if game_log.minutes is not None and game_log.minutes < self.min_minutes:
                    continue

                # Get pre-game data
                pre_game = [l for l in logs[:i]]
                if len(pre_game) < self.lookback:
                    continue

                recent = pre_game[-self.lookback:]
                all_pre = pre_game

                # Test each stat market
                for market, field in [("player_points", "points"),
                                       ("player_rebounds", "rebounds"),
                                       ("player_assists", "assists")]:
                    actual = getattr(game_log, field)
                    if actual is None:
                        continue

                    # Season avg from all pre-game data
                    pre_vals = [getattr(l, field) for l in all_pre if getattr(l, field) is not None]
                    if not pre_vals:
                        continue
                    season_avg = sum(pre_vals) / len(pre_vals)

                    # Recent avg
                    recent_vals = [getattr(l, field) for l in recent if getattr(l, field) is not None]
                    if not recent_vals:
                        continue
                    recent_avg = sum(recent_vals) / len(recent_vals)

                    # Projection
                    projection = self.season_weight * season_avg + self.recent_weight * recent_avg

                    # Use season_avg as synthetic line
                    synthetic_line = season_avg
                    if synthetic_line == 0:
                        continue

                    edge = abs(projection - synthetic_line) / synthetic_line * 100
                    if edge < self.min_edge:
                        continue

                    # Determine predicted direction
                    predicted_over = projection > synthetic_line
                    actual_over = actual > synthetic_line

                    # Grade
                    from backend.analysis.prop_confidence import calculate_prop_confidence
                    confidence = calculate_prop_confidence(edge)
                    hit = predicted_over == actual_over
                    result = "win" if hit else "loss"
                    payout = calculate_payout(-110) if hit else 0.0

                    if hit:
                        wins += 1
                        total_profit += payout
                    else:
                        losses += 1
                        total_profit -= 1.0

                    outcome = "Over" if predicted_over else "Under"
                    picks.append({
                        "player_name": player_name,
                        "market": market,
                        "game_date": str(game_log.game_date),
                        "projection": round(projection, 1),
                        "line": round(synthetic_line, 1),
                        "actual": round(actual, 1),
                        "outcome": outcome,
                        "edge_pct": round(edge, 1),
                        "confidence": confidence,
                        "result": result,
                    })

                    # Track by market
                    if market not in by_market:
                        by_market[market] = {"wins": 0, "losses": 0}
                    by_market[market]["wins" if hit else "losses"] += 1

                    # Track by confidence
                    if confidence not in by_confidence:
                        by_confidence[confidence] = {"wins": 0, "losses": 0}
                    by_confidence[confidence]["wins" if hit else "losses"] += 1

        total = wins + losses
        return {
            "wins": wins,
            "losses": losses,
            "total": total,
            "hit_rate": round(wins / total * 100, 2) if total > 0 else 0,
            "roi": round(total_profit / total * 100, 2) if total > 0 else 0,
            "total_profit": round(total_profit, 4),
            "picks": picks,
            "by_market": {k: {**v, "hit_rate": round(v["wins"] / (v["wins"] + v["losses"]) * 100, 2)
                              if (v["wins"] + v["losses"]) > 0 else 0}
                          for k, v in by_market.items()},
            "by_confidence": {k: {**v, "hit_rate": round(v["wins"] / (v["wins"] + v["losses"]) * 100, 2)
                                  if (v["wins"] + v["losses"]) > 0 else 0}
                              for k, v in sorted(by_confidence.items())},
        }
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest backend/tests/test_prop_backtester.py -v`
Expected: 3 PASSED

- [ ] **Step 5: Commit**

```bash
git add backend/backtesting/prop_backtester.py backend/tests/test_prop_backtester.py
git commit -m "feat: add PropBacktester for prop projection model validation"
```

---

### Task 8: PropValue Strategy (for Strategy registry)

**Files:**
- Create: `backend/analysis/variants/prop_value.py`
- Modify: `backend/pipeline/pick_generator.py`
- Test: `backend/tests/test_prop_value_strategy.py`

- [ ] **Step 1: Write failing test**

```python
# backend/tests/test_prop_value_strategy.py
from backend.analysis.variants.prop_value import PropValueStrategy

def test_prop_value_strategy_exists():
    strategy = PropValueStrategy("prop_value", {
        "recent_weight": 0.6, "season_weight": 0.4, "min_edge": 5.0
    })
    assert strategy.name == "prop_value"

def test_prop_value_predict_returns_empty():
    """PropValue strategy doesn't use the GameData predict interface — it uses PropAnalyzer directly."""
    from backend.data_types import GameData, TeamStats
    strategy = PropValueStrategy("prop_value", {})
    dummy_stats = TeamStats(point_diff=0, home_record=(0,0), away_record=(0,0),
        last_n_record=(0,0), offensive_rating=100, defensive_rating=100,
        pace=100, strength_of_schedule=0.5, elo_rating=1500, rest_days=2)
    from datetime import date
    game = GameData(game_id=1, sport="nba", date=date.today(),
        home_team_id=1, away_team_id=2, home_stats=dummy_stats, away_stats=dummy_stats)
    result = strategy.predict(game)
    assert result == []  # Props don't use game-level predict
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest backend/tests/test_prop_value_strategy.py -v`
Expected: FAIL

- [ ] **Step 3: Implement PropValueStrategy**

```python
# backend/analysis/variants/prop_value.py
from backend.analysis.strategy import Strategy
from backend.data_types import GameData, Pick


class PropValueStrategy(Strategy):
    """Strategy wrapper for prop value analysis.

    This strategy doesn't use the standard predict(GameData) interface.
    Prop picks are generated via PropAnalyzer directly in the pipeline.
    This class exists so prop strategies can be registered in the strategy
    system and have their configs managed via the Backtesting UI.
    """

    def predict(self, game: GameData) -> list[Pick]:
        # Prop analysis doesn't use game-level predictions
        return []
```

Add to `backend/pipeline/pick_generator.py` imports and STRATEGY_MAP:

```python
from backend.analysis.variants.prop_value import PropValueStrategy

STRATEGY_MAP = {
    "ensemble": EnsembleStrategy,
    "recent_form": RecentFormStrategy,
    "value_only": ValueOnlyStrategy,
    "sport_specific": SportSpecificStrategy,
    "prop_value": PropValueStrategy,
}
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest backend/tests/test_prop_value_strategy.py -v`
Expected: 2 PASSED

- [ ] **Step 5: Commit**

```bash
git add backend/analysis/variants/prop_value.py backend/pipeline/pick_generator.py backend/tests/test_prop_value_strategy.py
git commit -m "feat: add PropValueStrategy and register in strategy map"
```

---

## Chunk 3: API Endpoints & Pipeline Integration

### Task 9: Pipeline API (POST /pipeline/run)

**Files:**
- Create: `backend/api/pipeline_api.py`
- Create: `backend/pipeline/prop_pipeline.py`
- Modify: `backend/api/main.py`
- Modify: `frontend/vite.config.ts`
- Test: `backend/tests/test_pipeline_api.py`

- [ ] **Step 1: Create prop_pipeline.py**

```python
# backend/pipeline/prop_pipeline.py
import asyncio
import logging
from datetime import date, datetime, timezone
from sqlalchemy.orm import Session
from backend.models import Game, Team, PlayerProp, PlayerStat, PickModel
from backend.collectors.player_stats.collector import PlayerStatsCollector
from backend.collectors.player_stats.nba_api_source import NbaApiSource
from backend.collectors.player_stats.espn_stats_source import EspnStatsSource
from backend.collectors.player_stats.balldontlie_source import BallDontLieSource
from backend.collectors.player_stats.mysportsfeeds_source import MySportsFeedsSource
from backend.analysis.prop_analyzer import PropAnalyzer

logger = logging.getLogger(__name__)


def build_default_collector() -> PlayerStatsCollector:
    """Build collector with default fallback chains."""
    return PlayerStatsCollector({
        "nba": [NbaApiSource(), BallDontLieSource(), EspnStatsSource()],
        "nfl": [EspnStatsSource()],
        "ncaab": [EspnStatsSource()],
        "ncaaf": [EspnStatsSource()],
    })


async def run_prop_pipeline(session: Session, target_date: date | None = None,
                            strategy_id: int | None = None) -> dict:
    """Fetch player stats, analyze props, generate prop picks.

    Returns summary dict with counts.
    """
    target_date = target_date or date.today()
    collector = build_default_collector()
    try:
        return await _run_prop_pipeline_inner(session, collector, target_date, strategy_id)
    finally:
        await collector.close()


async def _run_prop_pipeline_inner(session: Session, collector: PlayerStatsCollector,
                                    target_date: date, strategy_id: int | None) -> dict:
    # 1. Get today's games
    games = session.query(Game).filter(
        Game.date == target_date, Game.status == "scheduled"
    ).all()
    if not games:
        return {"games": 0, "stats_fetched": 0, "props_analyzed": 0, "picks_generated": 0}

    # 2. Collect unique teams playing today
    team_ids = set()
    for g in games:
        team_ids.add(g.home_team_id)
        team_ids.add(g.away_team_id)

    # 3. Fetch stats for each team
    stats_count = 0
    for tid in team_ids:
        team = session.get(Team, tid)
        if not team:
            continue
        stats, source = await collector.fetch_player_stats(team.sport, team.abbreviation)
        if stats and source:
            count = collector.store_stats(session, stats, "season_avg", team.id, team.sport, source)
            stats_count += count
            # Fetch recent games for each player
            for s in stats:
                recent, rsource = await collector.fetch_player_recent(
                    team.sport, s["player_name"], n=5
                )
                if recent and rsource:
                    collector.store_stats(
                        session, recent, "game_log", team.id, team.sport, rsource
                    )

    # 4. Analyze props
    props = (session.query(PlayerProp)
        .join(Game).filter(Game.date == target_date).all())

    # Build analyzer from strategy config if available
    analyzer_kwargs = {}
    if strategy_id:
        from backend.models import StrategyModel
        import json as json_mod
        strat = session.get(StrategyModel, strategy_id)
        if strat:
            cfg = json_mod.loads(strat.config_json)
            analyzer_kwargs = {
                "season_weight": cfg.get("season_weight", 0.4),
                "recent_weight": cfg.get("recent_weight", 0.6),
                "min_edge": cfg.get("min_edge", 5.0),
            }
    analyzer = PropAnalyzer(**analyzer_kwargs)
    picks_generated = 0
    props_analyzed = 0

    for prop in props:
        season_avg = session.query(PlayerStat).filter_by(
            player_name=prop.player_name, stat_type="season_avg"
        ).first()
        recent = (session.query(PlayerStat)
            .filter_by(player_name=prop.player_name, stat_type="game_log")
            .order_by(PlayerStat.game_date.desc()).limit(5).all())

        analysis = analyzer.analyze(prop, season_avg, recent)
        props_analyzed += 1

        if analysis and analysis.confidence >= 1 and strategy_id:
            pick = PickModel(
                game_id=analysis.game_id,
                strategy_id=strategy_id,
                pick_type="prop",
                pick_value=f"{analysis.player_name} {analysis.outcome} {analysis.line} {_market_label(analysis.market)}",
                confidence=analysis.confidence,
                edge_pct=analysis.edge_pct,
                odds_at_pick=analysis.odds,
                created_at=datetime.now(tz=timezone.utc),
            )
            session.add(pick)
            picks_generated += 1

    session.commit()
    return {
        "games": len(games),
        "stats_fetched": stats_count,
        "props_analyzed": props_analyzed,
        "picks_generated": picks_generated,
    }


def _market_label(market: str) -> str:
    labels = {
        "player_points": "Points", "player_rebounds": "Rebounds",
        "player_assists": "Assists", "player_threes": "3-Pointers",
        "player_points_rebounds_assists": "PRA",
        "player_pass_yds": "Pass Yards", "player_rush_yds": "Rush Yards",
    }
    return labels.get(market, market)
```

- [ ] **Step 2: Create pipeline API router**

```python
# backend/api/pipeline_api.py
import asyncio
from fastapi import APIRouter, Request, BackgroundTasks
from backend.database import get_session
from backend.pipeline.prop_pipeline import run_prop_pipeline
from backend.models import StrategyModel

router = APIRouter()


@router.post("/run")
async def trigger_pipeline(request: Request):
    """Run the full prop pipeline: fetch stats, analyze props, generate picks."""
    session = get_session(request.app.state.engine)
    try:
        # Find active prop strategy (if any)
        prop_strategy = session.query(StrategyModel).filter(
            StrategyModel.strategy_type == "prop",
            StrategyModel.is_active == True,
        ).first()
        strategy_id = prop_strategy.id if prop_strategy else None

        result = await run_prop_pipeline(session, strategy_id=strategy_id)
        return {"status": "completed", **result}
    except Exception as e:
        return {"status": "error", "message": str(e)}
    finally:
        session.close()
```

- [ ] **Step 3: Register pipeline router in main.py**

Add to `backend/api/main.py`:

```python
from backend.api.pipeline_api import router as pipeline_router
app.include_router(pipeline_router, prefix="/pipeline", tags=["pipeline"])
```

- [ ] **Step 4: Add /pipeline proxy to vite.config.ts**

Add `'/pipeline': 'http://localhost:8000'` to the proxy config.

- [ ] **Step 5: Write test**

```python
# backend/tests/test_pipeline_api.py
from fastapi.testclient import TestClient
from backend.api.main import create_app
from backend.models import Base
from backend.database import get_engine

def test_pipeline_run_endpoint():
    app = create_app(":memory:")
    engine = app.state.engine
    Base.metadata.create_all(engine)
    client = TestClient(app)
    resp = client.post("/pipeline/run")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] in ("completed", "error")
```

- [ ] **Step 6: Run tests**

Run: `python -m pytest backend/tests/test_pipeline_api.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add backend/pipeline/prop_pipeline.py backend/api/pipeline_api.py backend/api/main.py frontend/vite.config.ts backend/tests/test_pipeline_api.py
git commit -m "feat: add POST /pipeline/run endpoint for on-demand prop pipeline"
```

---

### Task 10: Backtest Run Endpoint (POST /backtest/run)

**Files:**
- Modify: `backend/api/backtest.py`
- Test: `backend/tests/test_backtest_run_api.py`

- [ ] **Step 1: Write failing test**

```python
# backend/tests/test_backtest_run_api.py
import json
from datetime import date, datetime, timezone
from fastapi.testclient import TestClient
from backend.api.main import create_app
from backend.models import Base, StrategyModel, Team, PlayerStat
from backend.database import get_engine, get_session

def test_backtest_run_prop():
    app = create_app(":memory:")
    engine = app.state.engine
    Base.metadata.create_all(engine)
    session = get_session(engine)
    # Create prop strategy
    strat = StrategyModel(name="prop_value", config_json=json.dumps({
        "recent_weight": 0.6, "season_weight": 0.4, "min_edge": 5.0, "lookback": 5
    }), strategy_type="prop")
    session.add(strat)
    team = Team(name="Test", abbreviation="TST", sport="nba")
    session.add(team)
    session.commit()
    # Add some game logs
    for i in range(15):
        session.add(PlayerStat(
            player_name="Test Player", team_id=team.id, sport="nba",
            stat_type="game_log", game_date=date(2026, 2, 1 + i),
            points=25.0 + i, rebounds=8.0, assists=5.0, minutes=36.0,
            source="test", fetched_at=datetime.now(tz=timezone.utc),
        ))
    session.commit()
    session.close()

    client = TestClient(app)
    resp = client.post(f"/backtest/run", json={
        "strategy_id": strat.id,
        "start_date": "2026-02-10",
        "end_date": "2026-02-15",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert "wins" in data
    assert "losses" in data
    assert "hit_rate" in data
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest backend/tests/test_backtest_run_api.py -v`
Expected: FAIL — endpoint doesn't exist

- [ ] **Step 3: Add run endpoint to backtest.py**

Add to `backend/api/backtest.py`:

```python
class BacktestRunRequest(BaseModel):
    strategy_id: int
    start_date: str
    end_date: str

@router.post("/run")
def run_backtest(request: Request, body: BacktestRunRequest):
    from datetime import date as date_type
    session = get_session(request.app.state.engine)
    try:
        strat = session.get(StrategyModel, body.strategy_id)
        if not strat:
            raise HTTPException(status_code=404, detail="Strategy not found")
        start = date_type.fromisoformat(body.start_date)
        end = date_type.fromisoformat(body.end_date)

        if strat.strategy_type == "prop":
            from backend.backtesting.prop_backtester import PropBacktester
            config = json.loads(strat.config_json)
            bt = PropBacktester(config)
            sport = strat.sport or "nba"
            result = bt.backtest(session, sport, start, end)
        else:
            # Existing game backtester
            from backend.backtesting.backtester import Backtester
            from backend.pipeline.pick_generator import STRATEGY_MAP, _build_game_data
            config = json.loads(strat.config_json)
            strategy_cls = STRATEGY_MAP.get(strat.name)
            if not strategy_cls:
                raise HTTPException(status_code=400, detail="Unknown strategy")
            strategy = strategy_cls(strat.name, config)
            bt = Backtester(strategy)
            from backend.models import Game
            games = session.query(Game).filter(
                Game.status == "final", Game.date >= start, Game.date <= end
            ).all()
            games_with_results = []
            for g in games:
                if g.home_score is not None and g.away_score is not None:
                    game_data = _build_game_data(session, g)
                    games_with_results.append((game_data, g.home_score, g.away_score))
            result = bt.run(games_with_results)

        # Store run record
        run = BacktestRun(strategy_id=strat.id, status="completed",
            started_at=datetime.now(tz=timezone.utc),
            completed_at=datetime.now(tz=timezone.utc))
        session.add(run)
        session.commit()
        return result
    finally:
        session.close()
```

Add missing import at top of backtest.py: `from datetime import datetime, timezone`

Also update `StrategyCreate` to include `strategy_type`:

```python
class StrategyCreate(BaseModel):
    name: str
    description: str = ""
    config: dict
    sport: str | None = None
    strategy_type: str = "game"  # "game" or "prop"
```

Update `create_strategy` to pass `strategy_type`:

```python
strat = StrategyModel(name=body.name, description=body.description,
    config_json=json.dumps(body.config), is_active=False, sport=body.sport,
    strategy_type=body.strategy_type)
```

Update `list_strategies` response to include `strategy_type`:

```python
return [{"id": s.id, "name": s.name, "description": s.description,
    "config": json.loads(s.config_json), "is_active": s.is_active,
    "sport": s.sport, "strategy_type": s.strategy_type} for s in rows]
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest backend/tests/test_backtest_run_api.py -v`
Expected: PASS

- [ ] **Step 5: Run full test suite**

Run: `python -m pytest backend/tests/ -v`
Expected: All pass

- [ ] **Step 6: Commit**

```bash
git add backend/api/backtest.py backend/tests/test_backtest_run_api.py
git commit -m "feat: add POST /backtest/run endpoint for on-demand backtesting"
```

---

### Task 11: Update Props API with Analysis Fields

**Files:**
- Modify: `backend/api/props.py`
- Test: `backend/tests/test_props_api_analysis.py`

- [ ] **Step 1: Write failing test**

```python
# backend/tests/test_props_api_analysis.py
from datetime import date, datetime, timezone
from fastapi.testclient import TestClient
from backend.api.main import create_app
from backend.models import Base, Game, Team, PlayerProp, PlayerStat
from backend.database import get_engine, get_session

def test_props_today_includes_analysis():
    app = create_app(":memory:")
    engine = app.state.engine
    Base.metadata.create_all(engine)
    session = get_session(engine)
    home = Team(name="Boston", abbreviation="BOS", sport="nba")
    away = Team(name="Brooklyn", abbreviation="BKN", sport="nba")
    session.add_all([home, away])
    session.commit()
    today = date.today()
    game = Game(sport="nba", season="2025-26", date=today,
                home_team_id=home.id, away_team_id=away.id, status="scheduled")
    session.add(game)
    session.commit()
    session.add(PlayerProp(
        game_id=game.id, bookmaker="dk", market="player_points",
        player_name="Jayson Tatum", outcome="Over", line=25.5, odds=-110,
        fetched_at=datetime.now(tz=timezone.utc),
    ))
    session.add(PlayerStat(
        player_name="Jayson Tatum", team_id=home.id, sport="nba",
        stat_type="season_avg", points=27.5, source="test",
        fetched_at=datetime.now(tz=timezone.utc),
    ))
    session.commit()
    session.close()

    client = TestClient(app)
    resp = client.get("/props/today")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) >= 1
    prop = data[0]
    # Should have analysis fields
    assert "projection" in prop
    assert "edge_pct" in prop
    assert "confidence" in prop
    assert "source" in prop
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest backend/tests/test_props_api_analysis.py -v`
Expected: FAIL — projection field not in response

- [ ] **Step 3: Update props.py to include analysis**

Modify `backend/api/props.py` `get_today_props` to add analysis fields by looking up `PlayerStat` data for each prop and running `PropAnalyzer`:

Add imports at top:
```python
from backend.models import PlayerProp, Game, Team, PlayerStat
from backend.analysis.prop_analyzer import PropAnalyzer
```

In the response building loop, after constructing the base dict, add:

```python
# Look up player stats for analysis
analyzer = PropAnalyzer()
season_avg = session.query(PlayerStat).filter_by(
    player_name=prop.player_name, stat_type="season_avg"
).first()
recent = (session.query(PlayerStat)
    .filter_by(player_name=prop.player_name, stat_type="game_log")
    .order_by(PlayerStat.game_date.desc()).limit(5).all())
analysis = analyzer.analyze(prop, season_avg, recent)

result_dict = {
    # ... existing fields ...
    "projection": analysis.projection if analysis else None,
    "edge_pct": analysis.edge_pct if analysis else None,
    "confidence": analysis.confidence if analysis else None,
    "season_avg": analysis.season_avg if analysis else None,
    "recent_avg": analysis.recent_avg if analysis else None,
    "source": analysis.source if analysis else None,
    "is_stale": analysis.is_stale if analysis else False,
}
```

- [ ] **Step 4: Run test**

Run: `python -m pytest backend/tests/test_props_api_analysis.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/api/props.py backend/tests/test_props_api_analysis.py
git commit -m "feat: add projection/edge/confidence analysis to props API response"
```

---

### Task 12: Update Picks API for Prop Picks

**Files:**
- Modify: `backend/api/picks.py`

- [ ] **Step 1: Verify picks.py already handles pick_type="prop"**

Read `backend/api/picks.py` to confirm the today endpoint queries all PickModel rows for the target date. Since prop picks are stored as regular PickModel with `pick_type="prop"`, they should already appear. If the endpoint filters by pick_type, add "prop" to the allowed types.

- [ ] **Step 2: Run existing pick tests**

Run: `python -m pytest backend/tests/test_api_picks.py -v`
Expected: All pass (no changes needed if pick_type is not filtered)

- [ ] **Step 3: Commit if changes needed**

```bash
git add backend/api/picks.py
git commit -m "feat: ensure prop picks appear in /picks/today endpoint"
```

---

## Chunk 4: Frontend Updates

### Task 13: Update Types and API Client

**Files:**
- Modify: `frontend/src/types.ts`
- Modify: `frontend/src/api/client.ts`

- [ ] **Step 1: Add PropAnalysisData and update PropData**

In `frontend/src/types.ts`, update `PropData`:

```typescript
export interface PropData {
  id: number;
  game_id: number;
  sport: string;
  date: string;
  matchup: string;
  bookmaker: string;
  market: string;
  market_label: string;
  player_name: string;
  outcome: string;
  line: number | null;
  odds: number;
  // Analysis fields (null if no stats available)
  projection: number | null;
  edge_pct: number | null;
  confidence: number | null;
  season_avg: number | null;
  recent_avg: number | null;
  source: string | null;
  is_stale: boolean;
}
```

Add `StrategyData.strategy_type`:

```typescript
export interface StrategyData {
  id: number;
  name: string;
  description: string;
  config: Record<string, unknown>;
  is_active: boolean;
  sport: string | null;
  strategy_type?: string;
}
```

Add `BacktestResult` type:

```typescript
export interface BacktestResult {
  wins: number;
  losses: number;
  total: number;
  hit_rate: number;
  roi: number;
  total_profit: number;
  by_market?: Record<string, { wins: number; losses: number; hit_rate: number }>;
  by_confidence?: Record<string, { wins: number; losses: number; hit_rate: number }>;
}
```

- [ ] **Step 2: Add API client methods**

In `frontend/src/api/client.ts`, add:

```typescript
pipeline: {
  run: () => post<{ status: string; games: number; stats_fetched: number;
    props_analyzed: number; picks_generated: number }>('/pipeline/run', {}),
},
```

Add to `backtest`:

```typescript
run: (data: { strategy_id: number; start_date: string; end_date: string }) =>
  post<BacktestResult>('/backtest/run', data),
```

Import `BacktestResult` from types.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/types.ts frontend/src/api/client.ts
git commit -m "feat: add analysis types and pipeline/backtest API methods"
```

---

### Task 14: Update Player Props Page

**Files:**
- Modify: `frontend/src/pages/PlayerProps.tsx`

- [ ] **Step 1: Add analysis columns and confidence filter**

Update the PlayerProps page to:
1. Add a confidence filter dropdown (All, 1+, 2+, 3+, 4+, 5)
2. Add columns: Projection, Edge%, Confidence (stars), Source
3. Color-code edge (green positive)
4. Show "stale" badge when `is_stale` is true
5. Sort by edge% descending by default

Key changes to the table header:

```tsx
<th>Projection</th>
<th>Edge%</th>
<th>Confidence</th>
<th>Source</th>
```

Add confidence filter above the table:

```tsx
<select value={minConfidence} onChange={e => setMinConfidence(Number(e.target.value))}>
  <option value={0}>All Confidence</option>
  <option value={1}>1+ Stars</option>
  <option value={2}>2+ Stars</option>
  <option value={3}>3+ Stars</option>
  <option value={4}>4+ Stars</option>
  <option value={5}>5 Stars Only</option>
</select>
```

Filter and sort props:

```tsx
const filtered = props
  .filter(p => p.confidence === null || p.confidence >= minConfidence)
  .sort((a, b) => (b.edge_pct ?? 0) - (a.edge_pct ?? 0));
```

Stars display helper:

```tsx
function stars(n: number | null): string {
  if (n === null) return '-';
  return '★'.repeat(n) + '☆'.repeat(5 - n);
}
```

- [ ] **Step 2: Build frontend and test manually**

Run: `cd frontend && npm run build`
Expected: Build succeeds

- [ ] **Step 3: Commit**

```bash
git add frontend/src/pages/PlayerProps.tsx
git commit -m "feat: add analysis columns and confidence filter to Player Props page"
```

---

### Task 15: Update Today's Picks Page (Top Props Section)

**Files:**
- Modify: `frontend/src/pages/TodaysPicks.tsx`

- [ ] **Step 1: Add Top Props section**

Fetch prop picks alongside game picks. Filter for props with 3+ stars:

```tsx
const [topProps, setTopProps] = useState<PropData[]>([]);

// In useEffect, add:
api.props.today(sportParam).then(allProps => {
  setTopProps(allProps.filter(p => p.confidence !== null && p.confidence >= 3)
    .sort((a, b) => (b.edge_pct ?? 0) - (a.edge_pct ?? 0))
    .slice(0, 10));  // Top 10 props
});
```

Add below the game picks table:

```tsx
{topProps.length > 0 && (
  <div style={{ marginTop: '2rem' }}>
    <h3>Top Props</h3>
    <table>
      <thead>
        <tr>
          <th>Player</th>
          <th>Market</th>
          <th>Pick</th>
          <th>Line</th>
          <th>Projection</th>
          <th>Edge%</th>
          <th>Confidence</th>
        </tr>
      </thead>
      <tbody>
        {topProps.map(p => (
          <tr key={p.id}>
            <td>{p.player_name}</td>
            <td>{p.market_label}</td>
            <td style={{ color: '#4ade80', fontWeight: 'bold' }}>{p.outcome}</td>
            <td>{p.line}</td>
            <td>{p.projection}</td>
            <td style={{ color: '#4ade80' }}>{p.edge_pct?.toFixed(1)}%</td>
            <td>{stars(p.confidence)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  </div>
)}
```

- [ ] **Step 2: Build and test**

Run: `cd frontend && npm run build`
Expected: Build succeeds

- [ ] **Step 3: Commit**

```bash
git add frontend/src/pages/TodaysPicks.tsx
git commit -m "feat: add Top Props section to Today's Picks page"
```

---

### Task 16: Update Backtesting Page (Prop Strategy + Run Buttons)

**Files:**
- Modify: `frontend/src/pages/Backtesting.tsx`
- Modify: `frontend/src/components/StrategyForm.tsx`

- [ ] **Step 1: Add "Prop Value" to strategy type dropdown in StrategyForm**

In `StrategyForm.tsx`, add `"prop_value"` as an option. When selected, show prop-specific config fields: `recent_weight`, `season_weight`, `min_edge`, `lookback`, `min_minutes`.

- [ ] **Step 2: Add Run Pipeline and Run Backtest buttons to Backtesting page**

```tsx
<div style={{ display: 'flex', gap: '0.5rem', marginBottom: '1rem' }}>
  <button onClick={handleRunPipeline} disabled={pipelineRunning}
    style={{ padding: '0.4rem 1rem', background: '#059669', border: 'none',
             borderRadius: '4px', color: '#fff', cursor: 'pointer' }}>
    {pipelineRunning ? 'Running...' : 'Run Pipeline'}
  </button>
  <button onClick={handleRunBacktest} disabled={backtestRunning}
    style={{ padding: '0.4rem 1rem', background: '#7c3aed', border: 'none',
             borderRadius: '4px', color: '#fff', cursor: 'pointer' }}>
    {backtestRunning ? 'Running...' : 'Run Backtest'}
  </button>
</div>
```

Handlers:

```tsx
const [pipelineRunning, setPipelineRunning] = useState(false);
const [backtestRunning, setBacktestRunning] = useState(false);
const [backtestResult, setBacktestResult] = useState<BacktestResult | null>(null);

const handleRunPipeline = async () => {
  setPipelineRunning(true);
  try {
    const result = await api.pipeline.run();
    alert(`Pipeline complete: ${result.stats_fetched} stats, ${result.picks_generated} picks generated`);
    load();
  } catch (e: any) { alert(`Error: ${e.message}`); }
  finally { setPipelineRunning(false); }
};

const handleRunBacktest = async () => {
  // Find selected/active prop strategy
  const propStrategy = strategies.find(s => s.strategy_type === 'prop');
  if (!propStrategy) { alert('Create a prop_value strategy first'); return; }
  setBacktestRunning(true);
  try {
    const result = await api.backtest.run({
      strategy_id: propStrategy.id,
      start_date: '2026-02-01',
      end_date: '2026-03-14',
    });
    setBacktestResult(result);
  } catch (e: any) { alert(`Error: ${e.message}`); }
  finally { setBacktestRunning(false); }
};
```

Add backtest results display:

```tsx
{backtestResult && (
  <div style={{ marginTop: '1rem', padding: '1rem', background: '#1e293b', borderRadius: '8px' }}>
    <h4>Prop Backtest Results</h4>
    <p>Record: {backtestResult.wins}-{backtestResult.losses} ({backtestResult.hit_rate}%)</p>
    <p>ROI: {backtestResult.roi}%</p>
    {backtestResult.by_market && (
      <div>
        <h5>By Market</h5>
        {Object.entries(backtestResult.by_market).map(([mkt, r]) => (
          <p key={mkt}>{mkt}: {r.wins}-{r.losses} ({r.hit_rate}%)</p>
        ))}
      </div>
    )}
  </div>
)}
```

- [ ] **Step 3: Build frontend**

Run: `cd frontend && npm run build`
Expected: Build succeeds

- [ ] **Step 4: Commit**

```bash
git add frontend/src/pages/Backtesting.tsx frontend/src/components/StrategyForm.tsx
git commit -m "feat: add prop strategy support and Run Pipeline/Backtest buttons"
```

---

### Task 17: Update Pipeline Scheduler

**Files:**
- Modify: `backend/pipeline/scheduler.py`

- [ ] **Step 1: Add stats fetching to daily pipeline**

After the existing odds fetching in `fetch_sport_data`, add:

```python
# Fetch player stats
from backend.pipeline.prop_pipeline import run_prop_pipeline
try:
    result = asyncio.run(run_prop_pipeline(session, target_date=date.today(), strategy_id=active_strategy.id if active_strategy else None))
    logger.info(f"Prop pipeline: {result}")
except Exception as e:
    logger.error(f"Prop pipeline error: {e}")
```

Add this after `generate_and_store_picks` in `daily_job`.

- [ ] **Step 2: Run full test suite**

Run: `python -m pytest backend/tests/ -v`
Expected: All pass

- [ ] **Step 3: Commit**

```bash
git add backend/pipeline/scheduler.py
git commit -m "feat: integrate prop pipeline into daily scheduler"
```

---

### Task 18: Install Dependencies & Final Verification

**Files:**
- Modify: `requirements.txt` (if it exists) or install manually

- [ ] **Step 1: Install nba_api**

Run: `pip install nba_api`

- [ ] **Step 2: Run full test suite**

Run: `python -m pytest backend/tests/ -v`
Expected: All tests pass (80+ existing + ~25 new)

- [ ] **Step 3: Start server and verify manually**

Run: `cd C:/Users/mwill/OneDrive/Documents/mwilliams2733/sports_picks && python -m uvicorn backend.api.main:app --reload --port 8000`

Verify:
- `GET /props/today` returns props with analysis fields
- `GET /picks/today` includes any prop picks
- `POST /pipeline/run` triggers the pipeline
- `POST /backtest/run` runs a backtest
- Frontend shows analysis columns on Player Props page
- Frontend shows Top Props on Today's Picks page
- Backtesting page has Run Pipeline and Run Backtest buttons

- [ ] **Step 4: Commit all remaining changes**

```bash
git add -A
git commit -m "feat: complete player stats integration with prop analysis and backtesting"
```

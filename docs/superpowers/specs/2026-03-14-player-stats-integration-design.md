# Player Stats Integration Design

## Goal

Integrate free player stats APIs to power prop bet value analysis, prop pick generation with confidence ratings, and prop backtesting. Primary source is `nba_api` for NBA, with ESPN as a universal fallback for all four sports. Fallback chain with staleness tracking ensures the system always has data to work with.

## Architecture

Source-per-file adapter pattern with a registry-based fallback chain. Each data source implements a common `PlayerStatsSource` ABC. A `PlayerStatsCollector` orchestrates fetching, fallback, caching, and staleness. A `PropAnalyzer` compares player projections to bookmaker lines to find edges. A `PropBacktester` validates the projection model against historical results.

## Tech Stack

- `nba_api` Python package (NBA primary)
- ESPN hidden API (all sports fallback)
- `balldontlie.io` API (NBA secondary fallback — verify v2 auth requirements at implementation time)
- MySportsFeeds (future, NFL/NCAA primary — placeholder)
- SQLAlchemy for `PlayerStat` model
- Existing FastAPI endpoints extended

---

## 1. Data Model

### PlayerStat Table

Stores per-player stats — both season averages and individual game logs.

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
    # Football-specific
    pass_yards = Column(Float, nullable=True)
    rush_yards = Column(Float, nullable=True)
    rec_yards = Column(Float, nullable=True)
    touchdowns = Column(Float, nullable=True)
    # Metadata
    source = Column(String, nullable=False)       # "nba_api", "espn", "balldontlie"
    fetched_at = Column(DateTime, nullable=False)
    is_stale = Column(Boolean, default=False)
```

Design choices:
- Wide table with nullable sport-specific columns (simpler queries than EAV)
- `stat_type` distinguishes season averages from game logs
- `source` tracks which API provided the data
- `is_stale` flag set when all live sources fail and cached data is served
- Unique constraint on `(player_name, sport, stat_type, game_date)` — upsert on conflict to prevent duplicates across repeated fetches or different sources

### Player Name Normalization

Different sources format names differently ("LeBron James" vs "L. James" vs "James, LeBron"). The `PlayerStatsCollector.store_stats()` method normalizes all names to **"First Last"** format before storage:
- "L. James" → lookup via team roster context to expand → "LeBron James"
- "James, LeBron" → reversed to "LeBron James"
- Accented characters preserved (e.g., "Nikola Jokic" stays as-is)
- When matching props to stats, use case-insensitive comparison

---

## 2. Source Interface & Adapters

### ABC

```python
class PlayerStatsSource(ABC):
    name: str

    @abstractmethod
    async def fetch_season_averages(self, sport: str, team_abbr: str) -> list[dict]:
        """Season averages for all players on a team."""

    @abstractmethod
    async def fetch_recent_games(self, sport: str, player_name: str, n: int = 5) -> list[dict]:
        """Last N game logs for a player."""

    @abstractmethod
    async def is_available(self) -> bool:
        """Health check."""
```

Both methods return plain dicts. The collector handles normalization and storage.

### Source Implementations

| Source | File | Sports | Auth | Rate Limit |
|--------|------|--------|------|------------|
| `nba_api` | `nba_api_source.py` | NBA | None | ~1 req/sec |
| ESPN hidden API | `espn_stats_source.py` | NBA, NFL, NCAAB, NCAAF | None | ~1 req/sec |
| balldontlie.io | `balldontlie_source.py` | NBA | Check v2 requirements | 30 req/min |
| MySportsFeeds | `mysportsfeeds_source.py` | NFL, NCAAB, NCAAF | API key (free tier) | Varies |

MySportsFeeds is a placeholder — implemented as a stub that returns empty and `is_available() = False` until signup is completed.

### Fallback Chains

```python
FALLBACK_CHAINS = {
    "nba":   [NbaApiSource, BallDontLieSource, EspnStatsSource],
    "nfl":   [EspnStatsSource],
    "ncaab": [EspnStatsSource],
    "ncaaf": [EspnStatsSource],
}
```

### Fallback Logic

1. Try first source in chain
2. If exception or empty result, log warning, try next source
3. If all sources fail, check for cached `PlayerStat` rows < 24 hours old, mark `is_stale = True`
4. If no cache either, return empty with warning logged

---

## 3. PlayerStatsCollector

Orchestrates fetching, fallback, and storage.

```python
class PlayerStatsCollector:
    async def fetch_player_stats(self, sport: str, team_abbr: str) -> list[dict]:
        """Fetch season averages with fallback chain."""

    async def fetch_player_recent(self, sport: str, player_name: str, n: int = 5) -> list[dict]:
        """Fetch recent game logs with fallback chain."""

    def store_stats(self, session, stats: list[dict], stat_type: str) -> int:
        """Normalize and store stats, return count stored."""
```

Logs which source succeeded for monitoring.

---

## 4. Prop Value Analysis

### PropAnalyzer

Compares bookmaker prop lines to player stat projections.

```python
class PropAnalyzer:
    def analyze(self, prop: PlayerProp, season_avg: PlayerStat | None,
                recent_games: list[PlayerStat]) -> PropAnalysis:
        """Compare a prop line to player stats, return edge analysis.

        Args:
            prop: The bookmaker prop line (from PlayerProp model)
            season_avg: PlayerStat row with stat_type="season_avg" (or None)
            recent_games: List of PlayerStat rows with stat_type="game_log"
        """
```

The sport for each prop is resolved via `prop.game → Game.sport` (existing relationship).

### Projection Model

For a prop like "player_points Over 22.5":

1. Pull season average (e.g., 25.2 PPG)
2. Pull last 5 games average (e.g., 28.4 PPG)
3. Weighted projection: `0.4 * season_avg + 0.6 * recent_avg` = 27.1
4. Compare to line: `27.1 - 22.5 = +4.6` (Over signal)
5. Edge: `(projection - line) / line` = 20.4%
6. Confidence: 1-5 stars using prop-specific thresholds (no "models agreeing" concept for props — single projection model):

```
5 stars: edge >= 20%
4 stars: edge >= 15%
3 stars: edge >= 10%
2 stars: edge >= 7%
1 star:  edge >= 5%
```

Recent form weighted heavier (60%) because prop lines are often set off season averages — streaks are where the edge is.

### Combination Markets

Combination prop markets (e.g., `player_points_rebounds_assists`) are handled by summing the individual stat projections. For example, for a PRA line of 45.5, sum the player's projected points + rebounds + assists. If any component stat is unavailable, skip that combination market.

### PropAnalysis Output

```python
@dataclass
class PropAnalysis:
    player_name: str
    market: str           # "player_points"
    line: float           # 22.5
    outcome: str          # "Over" or "Under"
    season_avg: float     # 25.2
    recent_avg: float     # 28.4
    projection: float     # 27.1
    edge_pct: float       # 20.4
    confidence: int       # 1-5
    source: str           # which stats source was used
    is_stale: bool        # true if stats data is cached/old
```

### Pick Generation

1. For each bookmaker prop line, run analysis
2. Filter to props with edge >= 5% (configurable)
3. Store as `PickModel` with `pick_type = "prop"`, `pick_value = "LeBron James Over 25.5 Points"`, `game_id` from `PlayerProp.game_id`
4. Top props (3+ stars) appear on Today's Picks
5. All analyzed props appear on Player Props page

---

## 5. Prop Backtesting

### Approach

Cannot backtest against historical bookmaker lines (not available). Instead, backtest the projection model against actual game results.

For each completed game with player stats and game logs:

1. Use stats available *before* the game (season avg up to that date + last 5 pre-game logs) to generate projection
2. Use season average as synthetic prop line
3. Compare projection direction (Over/Under) to actual performance
4. Track hit rate

### PropBacktester

```python
class PropBacktester:
    def backtest(self, sport: str, start_date: date, end_date: date,
                 config: dict) -> BacktestResult:
```

### Configurable Parameters (via Strategy config)

- `recent_weight` / `season_weight` (default 0.6 / 0.4)
- `min_edge` threshold (default 5%)
- `lookback` window for recent games (default 5)
- `min_minutes` filter (default 15)

### Results Tracked

- Hit rate by market (points, rebounds, assists, etc.)
- Hit rate by confidence tier (1-5 stars)
- Hit rate by edge bucket (5-10%, 10-15%, 15%+)
- ROI using standard -110 odds

Reuses existing `BacktestRun` and `BacktestPick` models. Each `BacktestPick` gets its `game_id` from the game log entry being evaluated.

### StrategyModel Schema Change

Add a `strategy_type` column to `StrategyModel`:

```python
strategy_type = Column(String, nullable=False, default="game")  # "game" or "prop"
```

Existing strategies default to `"game"`. New prop strategies use `"prop"`. The backtesting page filters strategies by type to show the correct config fields.

---

## 6. Pipeline Integration

### Daily Pipeline Addition

After existing game pick generation:

1. For each sport in season, fetch player stats via `PlayerStatsCollector`
2. Fetch player props via existing `OddsAPICollector.fetch_player_props()`
3. Run `PropAnalyzer` on all props with available stats
4. Store prop picks in `PickModel`

### API Endpoints

**Modified:**
- `GET /picks/today` — includes prop picks (pick_type="prop") alongside game picks
- `GET /props/today` — adds projection, edge_pct, confidence, source, is_stale fields to response

**New:**
- `POST /pipeline/run` — triggers full pipeline on demand (fetch stats, fetch props, generate picks). Separate from backtest namespace since it's a data pipeline operation, not a backtest.
- `POST /backtest/run` — triggers a backtest (game or prop) for a given strategy ID

---

## 7. Frontend Changes

### Player Props Page

- New columns: Projection, Edge%, Confidence (stars), Source
- Edge color-coded: green (positive), red (negative)
- "Stale data" badge when `is_stale` is true
- Confidence filter dropdown (e.g., "Show 3+ stars only")
- Default sort by edge% descending

### Today's Picks Page

- "Top Props" section below game picks
- Shows prop picks with 3+ star confidence
- Same table format: Player, Market, Pick, Edge%, Confidence
- "Prop" type badge to distinguish from ML/Spread/O/U

### Backtesting Page

- "Prop Value" option in strategy type dropdown
- Prop-specific config fields: recent_weight, season_weight, min_edge, lookback, min_minutes
- Results display: hit rate by market and confidence tier
- **"Run Pipeline" button** — triggers stats fetch + prop fetch + pick generation
- **"Run Backtest" button** — triggers prop backtest for selected strategy

---

## 8. File Structure

```
backend/
  collectors/
    player_stats/
      __init__.py
      base.py              # PlayerStatsSource ABC
      collector.py          # PlayerStatsCollector (fallback orchestration)
      nba_api_source.py     # nba_api adapter
      espn_stats_source.py  # ESPN hidden API adapter
      balldontlie_source.py # balldontlie.io adapter
      mysportsfeeds_source.py  # Placeholder stub
  analysis/
    prop_analyzer.py        # PropAnalyzer
    variants/
      prop_value.py         # PropValue strategy for backtesting
  backtesting/
    prop_backtester.py      # PropBacktester
  models.py                 # + PlayerStat model
  api/
    props.py                # Modified with analysis fields
    picks.py                # Modified to include prop picks
    backtest.py             # + run backtest endpoint
    pipeline_api.py         # POST /pipeline/run endpoint
  pipeline/
    scheduler.py            # Modified to include stats fetching

frontend/
  src/
    pages/
      PlayerProps.tsx        # Modified with analysis columns
      TodaysPicks.tsx        # Modified with Top Props section
      Backtesting.tsx        # Modified with prop strategy + buttons
    types.ts                 # + PropAnalysis type
    api/client.ts            # + new endpoint methods
```

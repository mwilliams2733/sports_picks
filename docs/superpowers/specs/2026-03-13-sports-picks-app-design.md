# Sports Picks App — Design Spec

## Overview

A web-based sports betting analysis tool for a small group of friends. The app collects sports stats from free APIs, runs them through configurable analysis strategies, backtests those strategies against historical data, and surfaces the highest-probability picks for NBA, NFL, NCAAB, and NCAAF.

## Architecture

**Two-Process design:**

1. **Data Pipeline** — a scheduled Python process that collects stats and odds, runs analysis models, generates picks, and grades results.
2. **Web App** — a FastAPI backend serving a React frontend dashboard. Both processes share a SQLite database.

SQLite is configured with **WAL mode** for safe concurrent reads/writes between the two processes. The pipeline acquires short write transactions; the web app is read-heavy.

This separation ensures data crunching never blocks the UI, while keeping deployment simple (two processes + one database on a single machine).

## Data Sources

All free-tier:

- **The Odds API** (free tier, 500 req/month) — live odds from multiple bookmakers for all four sports. Provides moneyline, spread, and over/under lines.
- **ESPN API** (public endpoints) — schedules, scores, team stats, standings, and game results. Also the primary source for historical game data (past 3+ seasons available).
- **Sports Reference / Basketball Reference** — historical stats via web scraping as a supplemental source. Rate-limited to 1 request/3 seconds with local caching to avoid bans.

### API Budget Management

The Odds API free tier allows 500 requests/month. To stay within budget:

- Odds are fetched **once daily per active sport** (not every 2 hours).
- With 4 sports, that's ~120 requests/month during peak overlap, well within limits.
- If only 1-2 sports are in season, frequency can increase to twice daily.
- A request counter tracks usage and pauses fetching at 450 requests to preserve a buffer.

## Data Pipeline

### Collection Schedule

| Task | Frequency | Notes |
|------|-----------|-------|
| Odds refresh | Once daily per active sport | Budget-managed, see API Budget section |
| Stats update | Daily at 6 AM ET | After previous day's games are final |
| Pick generation | After each odds/stats refresh | Runs the active strategy variant |
| Result grading | Daily at 6 AM ET | Auto-grades previous day's picks |

**Active season detection**: configured per sport with start/end dates (e.g., NBA: Oct 22 – Jun 20). Updated manually each year in a config file.

### Core Data Types

```python
@dataclass
class GameData:
    game_id: int
    sport: str            # "nba", "nfl", "ncaab", "ncaaf"
    date: date
    home_team_id: int
    away_team_id: int
    home_stats: TeamStats  # rolling stats at game time
    away_stats: TeamStats
    odds: list[OddsSnapshot]
    week: int | None       # NFL/NCAAF week number

@dataclass
class TeamStats:
    point_diff: float       # avg point differential
    home_record: tuple[int, int]
    away_record: tuple[int, int]
    last_n_record: tuple[int, int]  # last N games (N from strategy config)
    offensive_rating: float
    defensive_rating: float
    pace: float
    strength_of_schedule: float
    elo_rating: float
    rest_days: int          # days since last game (NBA)
    # Sport-specific fields populated when applicable
    turnover_margin: float | None      # NFL
    red_zone_pct: float | None         # NFL
    conference_strength: float | None  # NCAA

@dataclass
class Pick:
    game_id: int
    pick_type: str          # "moneyline", "spread", "over_under"
    pick_value: str         # e.g., "BOS -4.5", "Over 218.5", "DEN ML"
    confidence: int         # 1-5
    edge_pct: float         # model prob - implied prob
    model_probability: float
    implied_probability: float
    odds_at_pick: int       # american odds at time of pick (e.g., -110)
```

### Analysis Models

Each game is scored across multiple dimensions by a pluggable strategy system:

1. **Win Probability Model** — logistic regression on current season data using features from `TeamStats`. Retrained weekly with latest data. Minimum 20 games per team before generating picks for that team.
2. **Value Detection** — compares model probability to implied probability from betting odds. Default minimum edge threshold: 5% (configurable per strategy variant).
3. **Spread Analysis** — tracks ATS (against-the-spread) performance, identifies teams that consistently cover or fail to cover.
4. **Over/Under Model** — pace of play, offensive/defensive ratings, recent scoring trends.
5. **ELO Rating System** — maintained per team per sport, updated after each game. Initial rating 1500, K-factor configurable per strategy.
6. **Confidence Scoring** — maps edge size to star rating:
   - 5 stars: edge >= 12% and 3+ models agree
   - 4 stars: edge >= 8% and 2+ models agree
   - 3 stars: edge >= 5% and 2+ models agree
   - 2 stars: edge >= 5%, single model signal
   - 1 star: edge >= 3%, weak signal (not recommended by default)

"No edge" = edge below the strategy's minimum threshold (default 5%). These games appear dimmed in the UI.

### Sport-Specific Adjustments

- **NBA**: Rest days, back-to-backs, home/away splits weighted heavily.
- **NFL**: Turnover margin, red zone efficiency. Injury data sourced from ESPN injury reports.
- **NCAAB/NCAAF**: Conference strength adjustments, higher variance accounted for.

### Error Handling

- **API failures**: Retry up to 3 times with exponential backoff. If still failing, skip that data source for this cycle and log a warning. Picks are still generated from available data but flagged with a "stale data" indicator if odds are >24 hours old.
- **Postponed/cancelled games**: Detected via ESPN game status. Picks for these games are voided (excluded from record). Valid statuses: scheduled, in_progress, final, postponed, cancelled.
- **Push results**: Pushes are excluded from win/loss counts. ROI treats pushes as a return of the unit (0 profit/loss).

## Backtesting Framework

### Strategy Registry

Each analysis strategy is a pluggable Python class implementing a standard interface:

```python
class Strategy(ABC):
    name: str
    config: dict  # tunable parameters (weights, thresholds, lookback windows)

    @abstractmethod
    def predict(self, game: GameData) -> list[Pick]:
        """Returns picks for a game (may return multiple: ML, spread, O/U)."""

    @classmethod
    def from_config(cls, config: dict) -> "Strategy":
        """Create a strategy variant from a config dict."""
```

### Backtester

Replays historical games through each strategy variant and tracks per-pick results:

- Win rate (straight up, ATS, O/U)
- ROI (return on investment assuming flat unit betting)
- Accuracy by confidence level (do 5-star picks actually hit more?)
- Performance by sport, by month, home/away, favorites vs. underdogs
- Cumulative ROI over time (for the performance chart)

### Variant Tuning Workflow

1. Load historical data (2-3+ seasons per sport from ESPN endpoints).
2. Define strategy variants via the web UI — a structured form with fields for weights, thresholds, lookback window, and model selection (not raw JSON).
3. Run backtests across all variants.
4. Compare results on the backtesting dashboard.
5. Promote the best-performing variant to "active" (one active variant per sport, or one global).
6. Periodically re-backtest with new data to detect strategy drift.

### Example Variants

- **Variant A — Recent Form**: Heavy weight on last 5 games, light on season averages.
- **Variant B — Value Only**: Only pick when implied probability edge exceeds 10%.
- **Variant C — Ensemble**: Combines logistic regression + ELO rating system.
- **Variant D — Sport-Specific**: Independently tuned weights per sport rather than one unified model.

## Web App

### Tech Stack

- **Backend**: FastAPI (Python)
- **Frontend**: React (TypeScript), built with Vite, served as static files by FastAPI in production
- **Database**: SQLite (WAL mode)
- **Charts**: Recharts for performance visualizations

### Dashboard Views

#### Today's Picks (Main View)

- Sport tabs: NBA, NFL, NCAAB, NCAAF
- Summary cards: active strategy name + win rate, today's pick count, season ROI + W-L record
- Picks table: game, pick, bet type (ML/spread/O-U), edge %, confidence (1-5 stars), odds at time of pick
- Games with no edge (below threshold) shown dimmed
- Filterable by confidence level, sport, and bet type
- Data staleness indicator if odds are >24 hours old
- Empty state: "No games today" when no games are scheduled
- Loading/error states for all data fetches

#### Backtesting View

- Strategy variant list with win rate and ROI per variant
- Active/promoted variant highlighted
- Performance-over-time chart (cumulative ROI line graph per variant)
- Breakdown panels: by sport, by bet type, by confidence level
- Structured form to create/edit strategy variants (weights, thresholds, model toggles)
- Run backtest button with progress indicator (polling for completion)

#### Track Record View

- Headline stats: overall win rate, total ROI, W-L record
- Calendar heatmap: green/red squares for daily pick results (data from `/stats/daily` endpoint)
- Scrollable pick history table: date, game, pick, result, confidence
- Filterable by sport, bet type, date range

## Data Model (SQLite, WAL mode)

### Tables

- **teams** — id, name, abbreviation, sport, conference, division
- **games** — id, sport, season, week (nullable, for NFL/NCAAF), date, home_team_id, away_team_id, home_score, away_score, status (scheduled/in_progress/final/postponed/cancelled)
- **team_stats** — id, team_id, game_id, stat_type, value (point diff, off rating, def rating, pace, etc.)
- **elo_ratings** — id, team_id, sport, rating, updated_at (current ELO per team, updated after each game)
- **odds** — id, game_id, bookmaker, moneyline_home, moneyline_away, spread_home, spread_away, over_under, timestamp (multiple snapshots per game retained for line movement analysis)
- **strategies** — id, name, description, config_json, is_active, sport (nullable — null means global)
- **backtest_runs** — id, strategy_id, status (pending/running/completed/failed), started_at, completed_at
- **backtest_picks** — id, run_id, game_id, pick_type, pick_value, confidence, edge_pct, result (win/loss/push), odds_at_pick
- **picks** — id, game_id, strategy_id, pick_type (ML/spread/OU), pick_value, confidence, edge_pct, odds_at_pick, created_at
- **pick_results** — id, pick_id, result (win/loss/push), payout (calculated from odds_at_pick)
- **api_usage** — id, source, request_count, month, updated_at (tracks API budget)

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | /picks/today | Today's picks, filterable by sport/confidence/bet type |
| GET | /picks/history | Historical picks with results, paginated |
| GET | /games/{id} | Individual game detail with stats, odds history, analysis |
| GET | /stats/record | Win rate, ROI, breakdowns by sport/type/confidence |
| GET | /stats/daily | Daily aggregated results for calendar heatmap |
| GET | /backtest/strategies | List all strategy variants with their backtest results |
| POST | /backtest/strategies | Create a new strategy variant |
| PUT | /backtest/strategies/{id} | Update a strategy variant's config |
| PATCH | /backtest/strategies/{id}/promote | Set a strategy as the active variant |
| POST | /backtest/run | Trigger a backtest: `{strategy_id, sports?, date_range?}` |
| GET | /backtest/runs/{id} | Check backtest run status and progress |
| GET | /backtest/compare | Side-by-side comparison of variant performance |

## Deployment

- Single machine — both processes launched via a start script
- Share with friends via local network IP or a free tunnel (ngrok / Cloudflare Tunnel)
- SQLite database file stored in the project directory, easily backed up

## Out of Scope (For Now)

- User accounts / authentication
- Real-time live game updates
- Paid API integrations
- Mobile app
- Automated betting (placing bets programmatically)

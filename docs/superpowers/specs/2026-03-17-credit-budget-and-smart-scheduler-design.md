# Credit Budget & Smart Scheduler Design

**Date**: 2026-03-17
**Status**: Approved

## Problem

The Odds API key was hardcoded in `config.yaml` (a security risk), the free tier's 500 monthly credits were exhausted silently (no budget enforcement existed), the `/pipeline/run` endpoint never called `run_prop_pipeline` (so manual triggers never analyzed props), and the pipeline fetched props for every event regardless of relevance — burning credits on games that had already started or weren't needed.

## Goals

1. Secure the API key in a `.env` file
2. Track and enforce a 20K credit/month budget with daily visibility
3. Schedule pipeline runs dynamically based on actual game times, ~2 hours before tip-off/kickoff
4. Separate game windows so each team/player is fetched exactly once per window
5. Support on-demand runs for any sport, budget-aware
6. Fix the missing `run_prop_pipeline` call in the API endpoint

## Design

### 1. Environment & Config

**`.env` file** (gitignored, already in `.gitignore`):

```
ODDS_API_KEY=<real_key>
```

**`.env.example`** (committed, template for setup):

```
ODDS_API_KEY=your_odds_api_key_here
```

**`config.yaml`** changes:

- Remove `odds_api_key` field entirely
- Update `odds_budget`:

```yaml
odds_budget:
  monthly_limit: 20000
  daily_target: 600
  reserve: 2000
```

- `monthly_limit`: hard cap, pipeline refuses calls beyond this
- `daily_target`: soft daily ceiling (~20K / 31 days with buffer)
- `reserve`: keep 2K credits for late-month on-demand runs

**`backend/config.py`** changes:

- Use `python-dotenv` to load `.env` (add `python-dotenv` to `pyproject.toml` dependencies)
- `load_config()` reads `ODDS_API_KEY` from environment and injects it into the config dict as `odds_api_key`

### 2. Credit Tracking (DB + API)

**Replace existing `ApiUsage` model** in `backend/models.py`:

The current `ApiUsage` model uses an aggregate-per-month design (`source`, `request_count`, `month`). Replace it with a per-call tracking model that supports the budget gate and credit visibility:

```python
class ApiUsage(Base):
    __tablename__ = "api_usage"
    id: int  # primary key
    endpoint: str  # "odds", "events", "player_props"
    sport: str  # "nba", "nfl", etc.
    credits_used: int  # 1 per call
    requests_remaining: int | None  # from X-Requests-Remaining header
    created_at: datetime  # UTC timestamp
```

**Migration strategy**: Since this is SQLite and the existing table only contains operational counters (no historical data worth preserving), drop and recreate the table. Add a one-time migration check in `create_all` startup: if the old schema is detected (has `source` column but not `endpoint`), drop the table so `create_all` recreates it with the new schema.

**Budget gate — separate from `OddsAPICollector`**:

The budget logic lives in the pipeline functions (`full_pipeline.py`), not inside the HTTP client. This keeps `OddsAPICollector` as a pure HTTP client and avoids mixing DB concerns into it. The pipeline functions check the budget before calling the collector and log usage after:

**`BudgetExhaustedError`**: Define in `backend/exceptions.py` (new file). The pipeline API endpoint catches this and returns HTTP 429 with a JSON body containing the current credit state (`monthly_used`, `monthly_remaining`, `daily_used`).

Before every Odds API call in the pipeline functions:

1. Query `ApiUsage` for current month's total
2. If `total >= monthly_limit`: raise `BudgetExhaustedError`, log warning
3. Query `ApiUsage` for today's total
4. If `today_total >= daily_target` and `monthly_remaining <= reserve`: raise `BudgetExhaustedError`
5. If `today_total >= daily_target` but monthly reserve is fine: log warning but allow (soft limit)

After every successful HTTP call:

1. Read `collector.requests_remaining` (already stored as instance attribute on `OddsAPICollector`)
2. Insert `ApiUsage` row with endpoint, sport, 1 credit, and that `requests_remaining` value
3. Log credit usage

Add an index on `ApiUsage.created_at` for efficient monthly/daily queries.

**New API endpoint `GET /credits`** (mounted at `/credits` prefix in `main.py`):

Returns:

```json
{
  "monthly_used": 550,
  "monthly_limit": 20000,
  "monthly_remaining": 19450,
  "daily_used": 8,
  "daily_target": 600,
  "api_requests_remaining": 19450
}
```

`api_requests_remaining` is the most recent `requests_remaining` value from the latest `ApiUsage` row — reflects what The Odds API itself reports. `monthly_remaining` is computed from `monthly_limit - monthly_used` and should closely match, but `api_requests_remaining` is the provider's source of truth.

**Frontend `CreditUsage` component**: Small widget showing monthly and daily usage. Mounted on the dashboard or pipeline section.

### 3. Game Start Time Storage

**Add `start_time` column to `Game` model**: `start_time = Column(DateTime, nullable=True)` — stores the full UTC datetime from ESPN's `commence_time` / game start time. The existing `date` column (Date only) stays for backward compatibility and efficient date-based queries.

**Update `_store_games` in `full_pipeline.py`**: The ESPN dict's `g["date"]` field already contains the full ISO datetime string (e.g., `"2026-03-17T23:30:00Z"`). Parse it via `datetime.fromisoformat()` for `start_time`. The existing `_parse_date` continues to strip it to a date-only value for the `date` column.

This column is essential for the window system — without it, the scheduler cannot determine when games tip off.

### 4. Smart Scheduler — Game Window System

Replaces the old `daily_job` + fixed 6 AM cron.

**Timezone handling**: Configure APScheduler with `timezone='America/New_York'` explicitly. All window calculations use ET. The `Game.start_time` column stores UTC; conversions to ET happen in the scheduler logic.

**Morning scout** — runs daily at 8:00 AM ET:

1. Grade pending picks (same as before)
2. Fetch today's game schedule from ESPN for NBA (and NFL when in season)
3. Group games into time windows: sort by `start_time`, then greedily cluster — start a new window when a game's start time exceeds the first game in the current window by more than 30 minutes
4. For each window: schedule a one-shot job ~2 hours before the earliest game in that cluster
5. Log all scheduled jobs: `"Scheduled NBA window: 5 games tipping off ~7:00 PM ET, pipeline run at 5:00 PM ET"`
6. If no games found for a sport, log info: `"No NBA games scheduled for today, no windows created"`
7. **Scout failure fallback**: If ESPN is unreachable, retry at 9 AM and 10 AM ET. If all retries fail, log an error — the user can still trigger on-demand runs manually

**NBA window logic**:

- Most days: one window (evening games 7-7:30 PM ET)
- Occasionally: two windows if there's a matinee (1 PM) plus evening games
- Each window run fetches odds + props only for games in that window

**NFL window logic** (active during NFL season, Sep–Feb):

Detected dynamically from ESPN schedule, but typically produces these windows:

| Window | Typical Kickoff (ET) | Pipeline Run |
|--------|---------------------|--------------|
| Thursday Night | 8:15 PM | ~6:15 PM |
| Sunday Early | 1:00 PM | ~11:00 AM |
| Sunday Late | 4:25 PM | ~2:25 PM |
| Sunday Night | 8:20 PM | ~6:20 PM |
| Monday Night | 8:15 PM | ~6:15 PM |

Saturday games (weeks 15-18), international games, holiday specials, and playoff scheduling are all handled automatically because windows are detected from the actual schedule, not hardcoded.

**Each window run**:

1. **Build window game set**: Query DB for games where `Game.start_time` falls within this window's time range. Collect their IDs into a `window_game_ids: set[int]`.
2. `fetch_and_store_odds` — only for the window's sport
3. `fetch_events` — 1 credit to get the full event list from the Odds API
4. **Filter events against the window game set before fetching props**: For each event, call `_find_game_for_event` to get the matching DB game. If `game is None` or `game.id not in window_game_ids`, skip it — do NOT call `fetch_player_props`. Only call `fetch_player_props` for events whose matched game is in the window set. This is critical — the Odds API returns ALL upcoming events (not just today's), so without pre-filtering, credits are wasted on games days away.
5. `run_prop_pipeline` — analyze the fetched props and generate picks
6. Log credit usage for the run

**No duplicate pulls**: Sunday Early fetches 1 PM games only. Sunday Late fetches 4:25 PM games only. A player on a 1 PM team is never re-fetched at 4:25 PM.

**Other sports** (NCAAB, NCAAF, boxing, MMA): No scheduled runs. On-demand only via the API endpoint.

### 5. Pipeline API Changes

**`POST /pipeline/run`** updated:

- New optional query params as FastAPI query parameters: `sport: str | None = None` (e.g., `?sport=ncaab`), `window_start: str | None = None`, `window_end: str | None = None` (ISO datetimes to scope a time window)
- If `sport` is provided, only fetches for that sport
- If `window_start`/`window_end` provided without `sport`, applies the time filter to all active-season sports
- If no filters, fetches for all active-season sports (existing behavior, but budget-aware)
- **Adds `run_prop_pipeline` call** (currently missing — the bug)
- Returns credit info in response:

```json
{
  "status": "completed",
  "active_sports": ["nba"],
  "games_stored": 6,
  "odds_stored": 12,
  "props_stored": 148,
  "props_analyzed": 148,
  "picks_generated": 5,
  "credits_used": 8,
  "credits_remaining_today": 592,
  "credits_remaining_month": 19450
}
```

**Recalibration job at 3 AM**: Preserved as-is within the new scheduler setup in `scheduler.py`. The job itself is unchanged, but the surrounding scheduler code is restructured.

### 6. Credit Budget Math

Estimated daily usage for typical game days:

Note: The `fetch_events` endpoint returns ALL upcoming events for a sport, not just today's. With pre-filtering (Section 4, step 4), we only call `fetch_player_props` for games in the current window. Without pre-filtering, a single `fetch_events("nba")` might return 20+ events and burn 20+ credits on props for future games.

**NBA regular season** (1 window, ~8 games, with pre-filtering):
- 1 `fetch_odds("nba")` = 1 credit
- 1 `fetch_events("nba")` = 1 credit
- ~8 `fetch_player_props` calls (only window games) = 8 credits
- **Total: ~10 credits/day**

**NFL game week** (5 windows: Thu + Sun×3 + Mon):
- 5 `fetch_odds("nfl")` calls = 5 credits
- 5 `fetch_events("nfl")` calls = 5 credits
- ~16 games × 1 `fetch_player_props` = 16 credits
- **Total: ~26 credits/week**

**Monthly estimate** (NBA daily + NFL weekly during overlap):
- NBA: 10 × 30 = 300 credits
- NFL: 26 × 4 = 104 credits
- On-demand (NCAAB, etc.): ~200 credits buffer
- **Total: ~604 credits/month** — well within 20K, leaving massive headroom

### 7. File Changes

| File | Change |
|------|--------|
| **New: `.env`** | `ODDS_API_KEY=...` |
| **New: `.env.example`** | Template without real key |
| **`pyproject.toml`** | Add `python-dotenv` dependency |
| **`config.yaml`** | Remove `odds_api_key`, update `odds_budget` |
| **`backend/config.py`** | Load `.env` via `python-dotenv`, inject `ODDS_API_KEY` |
| **`backend/models.py`** | Replace `ApiUsage` model (drop+recreate), add `start_time` to `Game` |
| **New: `backend/exceptions.py`** | `BudgetExhaustedError` exception class |
| **`backend/collectors/odds_api.py`** | No changes needed (`requests_remaining` already stored as instance attr) |
| **`backend/pipeline/full_pipeline.py`** | Add budget gate wrapper, `sport`/time-window filtering, pre-filter events before prop fetch, store `Game.start_time` |
| **`backend/pipeline/scheduler.py`** | Replace `daily_job` + 6 AM cron with morning scout (8 AM ET) + dynamic one-shot window jobs. Configure `timezone='America/New_York'`. Preserve recalibration job. |
| **`backend/api/pipeline_api.py`** | Add `sport`/`window` query params, add `run_prop_pipeline`, return credit info |
| **`backend/api/main.py`** | Register new credits router |
| **New: `backend/api/credits.py`** | `GET /credits` endpoint |
| **New: `frontend/src/components/CreditUsage.tsx`** | Credit counter widget |
| **Frontend dashboard** | Mount `CreditUsage` widget |

### 8. Not Changed

- `backend/analysis/prop_analyzer.py` — works correctly once it has data
- `backend/pipeline/prop_pipeline.py` — works correctly, just wasn't being called from API
- `backend/collectors/player_stats/*` — stats sources unchanged
- `backend/pipeline/grader.py` — grading unchanged
- `frontend/src/pages/PlayerProps.tsx` — works correctly once props exist
- `backend/api/props.py` — already does on-the-fly analysis from stored data

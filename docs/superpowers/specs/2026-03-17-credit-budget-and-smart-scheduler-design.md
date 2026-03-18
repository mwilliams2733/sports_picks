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

- Use `python-dotenv` to load `.env`
- `load_config()` reads `ODDS_API_KEY` from environment and injects it into the config dict as `odds_api_key`

### 2. Credit Tracking (DB + API)

**New model `ApiUsage`** in `backend/models.py`:

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

**Budget gate in `OddsAPICollector`**:

The collector receives a `session` and `budget_config` dict. Before every HTTP call:

1. Query `ApiUsage` for current month's total
2. If `total >= monthly_limit`: raise `BudgetExhaustedError`, log warning
3. Query `ApiUsage` for today's total
4. If `today_total >= daily_target` and `monthly_remaining <= reserve`: raise `BudgetExhaustedError`
5. If `today_total >= daily_target` but monthly reserve is fine: log warning but allow (soft limit)

After every successful HTTP call:

1. Insert `ApiUsage` row with endpoint, sport, 1 credit, and `requests_remaining` from response header
2. Log credit usage

**New API endpoint `GET /api/credits`**:

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

**Frontend `CreditUsage` component**: Small widget showing monthly and daily usage. Mounted on the dashboard or pipeline section.

### 3. Smart Scheduler — Game Window System

Replaces the old `daily_job` + fixed 6 AM cron.

**Morning scout** — runs daily at 8:00 AM ET:

1. Grade pending picks (same as before)
2. Fetch today's game schedule from ESPN for NBA (and NFL when in season)
3. Group games into time windows by clustering tip-off/kickoff times within 30 minutes of each other
4. For each window: schedule a one-shot job ~2 hours before the earliest game in that cluster
5. Log all scheduled jobs: `"Scheduled NBA window: 5 games tipping off ~7:00 PM ET, pipeline run at 5:00 PM ET"`

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

1. Filter today's games to those in this window's time range
2. `fetch_and_store_odds` — only for the window's sport
3. `fetch_and_store_props` — only for events matching the window's games
4. `run_prop_pipeline` — analyze the fetched props and generate picks
5. Log credit usage for the run

**No duplicate pulls**: Sunday Early fetches 1 PM games only. Sunday Late fetches 4:25 PM games only. A player on a 1 PM team is never re-fetched at 4:25 PM.

**Other sports** (NCAAB, NCAAF, boxing, MMA): No scheduled runs. On-demand only via the API endpoint.

### 4. Pipeline API Changes

**`POST /pipeline/run`** updated:

- New optional query params: `sport` (e.g., `?sport=ncaab`), `window_start`/`window_end` (ISO times to scope a time window)
- If `sport` is provided, only fetches for that sport
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

**Recalibration job at 3 AM**: Unchanged.

### 5. Credit Budget Math

Estimated daily usage for typical game days:

**NBA regular season** (1 window, ~8 games):
- 1 `fetch_odds("nba")` = 1 credit
- 1 `fetch_events("nba")` = 1 credit
- 8 `fetch_player_props` calls = 8 credits
- **Total: ~10 credits/day**

**NFL game week** (3 windows across Thu/Sun/Mon):
- 3 `fetch_odds("nfl")` calls = 3 credits
- 3 `fetch_events("nfl")` calls = 3 credits
- ~16 games × 1 `fetch_player_props` = 16 credits
- **Total: ~22 credits/week**

**Monthly estimate** (NBA daily + NFL weekly during overlap):
- NBA: 10 × 30 = 300 credits
- NFL: 22 × 4 = 88 credits
- On-demand (NCAAB, etc.): ~100 credits buffer
- **Total: ~488 credits/month** — well within 20K, leaving massive headroom

### 6. File Changes

| File | Change |
|------|--------|
| **New: `.env`** | `ODDS_API_KEY=...` |
| **New: `.env.example`** | Template without real key |
| **`config.yaml`** | Remove `odds_api_key`, update `odds_budget` |
| **`backend/config.py`** | Load `.env` via `python-dotenv`, inject `ODDS_API_KEY` |
| **`backend/models.py`** | Add `ApiUsage` model |
| **`backend/collectors/odds_api.py`** | Add budget gate, log `ApiUsage` after each call, accept session + budget config |
| **`backend/pipeline/scheduler.py`** | Replace `daily_job` + 6 AM cron with morning scout (8 AM ET) + dynamic one-shot window jobs |
| **`backend/pipeline/full_pipeline.py`** | Add `sport` and time-window filtering to `fetch_and_store_odds` / `fetch_and_store_props` |
| **`backend/api/pipeline_api.py`** | Add `sport`/`window` params, add `run_prop_pipeline`, return credit info |
| **New: `backend/api/credits.py`** | `GET /api/credits` endpoint |
| **New: `frontend/src/components/CreditUsage.tsx`** | Credit counter widget |
| **Frontend dashboard** | Mount `CreditUsage` widget |

### 7. Not Changed

- `backend/analysis/prop_analyzer.py` — works correctly once it has data
- `backend/pipeline/prop_pipeline.py` — works correctly, just wasn't being called from API
- `backend/collectors/player_stats/*` — stats sources unchanged
- `backend/pipeline/grader.py` — grading unchanged
- `frontend/src/pages/PlayerProps.tsx` — works correctly once props exist
- `backend/api/props.py` — already does on-the-fly analysis from stored data

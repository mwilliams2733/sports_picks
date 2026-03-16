# Sports Picks — Full Rebuild: ROI & UI Overhaul

**Date:** 2026-03-16
**Status:** Approved
**Scope:** ML prediction pipeline, social paper trading, mobile-first UI, real-time infrastructure

---

## Context

Sports Picks is a betting analysis app for a friend group. It runs a FastAPI + React + SQLite stack with ELO ratings, logistic regression, and multiple strategy variants to generate game picks and player prop recommendations.

### Current State

- **1,012 completed games** (overwhelmingly NBA), 4,760 player props, 255 teams
- **Prediction engine** uses hardcoded thresholds, 5-feature logistic regression, and magic-number weights with no empirical validation
- **Spread edge calculation is mathematically incorrect** in the ensemble strategy
- **Paper trading** exists but hasn't been shared with the friend group yet — first impressions matter
- **Frontend** has no mobile responsiveness, no global state, no real-time updates, and complex multi-step workflows

### Goals

1. Replace heuristic-driven predictions with a data-driven ML pipeline that recalibrates itself
2. Make paper trading a social hub that drives engagement from day one
3. Deliver a mobile-first experience where placing a pick takes two taps
4. Add real-time updates so the app feels alive during game nights

---

## Section 1: Prediction Engine Overhaul

### 1a. Model Architecture — LightGBM with Walk-Forward Validation

Replace the current logistic regression (`calibrated_model.py`) with a LightGBM gradient boosted tree model.

**Training approach:** Walk-forward validation. Train on all games before date X, predict games on date X, slide the window forward. This prevents future data leakage and simulates real-world usage.

**Sport strategy:**
- **NBA (1,012 games):** Full ML pipeline — enough data for LightGBM with walk-forward validation
- **Other sports (<200 completed games):** Fall back to improved heuristics (data-driven threshold tuning from Section 1d) until data accumulates past the 200-game threshold, then automatically graduate to ML

**File changes:** New `backend/analysis/ml_model.py` replacing `calibrated_model.py` as the primary model. Keep `calibrated_model.py` as the heuristic fallback.

### 1b. Expanded Feature Set (NBA)

Current features (5): elo_diff, point_diff, net_rating_diff, rest_days_diff, pace_diff

**Expanded features:**
- **Keep:** elo_diff, point_diff, net_rating_diff
- **Modify:** rest_days as actual integer (not binary diff), split into home_rest_days and away_rest_days
- **Add:**
  - home_flag (binary)
  - pace_diff
  - offensive_rating_home, offensive_rating_away (individual, not just diff)
  - defensive_rating_home, defensive_rating_away
  - recent_form_home, recent_form_away (weighted win% over last 5 and 10 games)
  - strength_of_schedule_home, strength_of_schedule_away
  - back_to_back_home, back_to_back_away (binary flags)
  - travel_distance_proxy (timezone difference between teams' home cities)

**Feature selection:** Use LightGBM's built-in feature importance (gain-based) and SHAP values. After initial training, drop features contributing less than 1% of total importance. Log feature importances to `model_metrics` table for tracking over time.

### 1c. Fix Spread & Over/Under Edge Calculation

**Current bug:** `ensemble.py` calculates spread edge as `abs(predicted_diff - (-spread_line))` — this is a raw point difference, not a probability of covering.

**Fix:** The model predicts a point differential distribution (mean from LightGBM prediction, standard deviation from residuals of walk-forward validation).

- **Spread:** `P(cover) = P(home_margin > spread_line)` using normal CDF with predicted mean and std
- **Over/Under:** `P(over) = P(total_points > ou_line)` — predict total from sum of team offensive models adjusted for pace and defensive matchup
- **Edge:** `model_prob - no_vig_implied_prob` (see Section 1f)

**File changes:** Modify `backend/analysis/variants/ensemble.py` to use distribution-based edge calculations.

### 1d. Confidence Recalibration Loop

**Current problem:** Confidence tiers in `confidence.py` use hardcoded edge thresholds (3%, 5%, 8%, 12%) with no empirical justification. These never update based on actual performance.

**Solution:** Nightly recalibration job that:

1. Queries all graded picks from last 30/60/90 days, grouped by confidence tier
2. Calculates actual win rate and ROI per tier
3. If actual win rate for tier N deviates from expected by more than 5 percentage points, adjusts the edge threshold for that tier
4. Stores adjustments in `calibration_history` table
5. Logs warnings if any tier has fewer than 20 picks (insufficient sample size for recalibration)

**Threshold adjustment logic:**
- If confidence-5 picks (expected ~70% win rate) are actually winning at 58%, tighten the threshold (require higher edge to qualify)
- If confidence-3 picks are winning at 68%, loosen the threshold (current edge cutoff is too conservative)
- Adjustment step size: 1 percentage point per recalibration cycle to avoid oscillation

**File changes:** New `backend/analysis/recalibrator.py`. Modify `confidence.py` and `prop_confidence.py` to read thresholds from DB instead of hardcoded values.

### 1e. Dynamic Kelly Sizing

**Current problem:** Fixed quarter-Kelly (0.25 fraction) with arbitrary 0.5-3.0 unit clamp.

**Solution:**

- **Adaptive fraction:** Base fraction starts at 0.25. If model calibration has been accurate over last 30 days (predicted probabilities within 3% of actual), increase to 0.35. If miscalibrated (>5% deviation), decrease to 0.15.
- **Drawdown protection:** If a user's paper bankroll drops 15% from peak, halve the Kelly fraction until bankroll recovers to within 10% of peak.
- **Correlation discount:** When multiple picks in the same game exist (moneyline + prop), reduce Kelly fraction by 20% on each to account for correlation.
- **Unit range:** Keep 0.5-3.0 clamp but make it configurable per strategy.

**File changes:** Modify `backend/analysis/kelly.py`.

### 1f. Vig-Adjusted Implied Probabilities

**Current problem:** `odds_utils.py` treats bookmaker odds as true probabilities, but they include 4-5% vig (overround).

**Solution:**

1. Calculate overround: `overround = implied_prob_home + implied_prob_away - 1`
2. Remove vig proportionally: `no_vig_prob_home = implied_prob_home / (1 + overround)`
3. Edge calculation becomes: `model_prob - no_vig_prob` (not `model_prob - raw_implied_prob`)

This typically shifts edges by 2-3 percentage points, which significantly affects which picks pass the confidence threshold.

**File changes:** Modify `backend/analysis/odds_utils.py` to add `remove_vig()` and `no_vig_implied_prob()` functions. Update all edge calculations across strategy variants.

---

## Section 2: Frontend Architecture & Navigation

### 2a. State Management — Zustand

Add Zustand as a lightweight global store. Three stores:

- **userStore:** Current user profile, selected user for viewing, auth state
- **appStore:** Active sport filter, active strategy, UI preferences (persisted to localStorage)
- **feedStore:** Activity feed events, WebSocket connection state

This eliminates redundant API calls (e.g., every page currently fetches sport data independently) and enables URL state restoration.

**File changes:** New `frontend/src/stores/` directory with `userStore.ts`, `appStore.ts`, `feedStore.ts`.

### 2b. Data Fetching — React Query (TanStack Query)

Replace raw `fetch` calls in `api/client.ts` consumers with React Query hooks. Benefits:

- Automatic caching and deduplication (two components requesting same data = one API call)
- Background refetch on window focus
- Loading, error, and stale states built in
- Optimistic updates for pick placement (show pick in feed immediately, roll back if API fails)

**File changes:** New `frontend/src/hooks/` directory with query hooks (e.g., `useTodaysPicks.ts`, `useLeaderboard.ts`). Existing `api/client.ts` remains as the base fetch layer.

### 2c. Mobile-Responsive Navigation

- **Desktop (>768px):** Horizontal top nav with active strategy dropdown and user avatar. Current layout improved.
- **Mobile (<768px):** Hamburger menu for secondary pages (Backtesting, FAQ). Fixed bottom tab bar for primary pages: Today's Picks, Props, Paper Trading, Track Record.
- **Active sport filter** in nav bar (persistent across pages via Zustand)

**File changes:** Modify `frontend/src/components/Layout.tsx`. New `frontend/src/components/BottomNav.tsx` and `frontend/src/components/MobileMenu.tsx`.

### 2d. URL State Management

Encode filters in URL search params using React Router's `useSearchParams`:

- `?sport=nba` — sport filter
- `?confidence=3` — minimum confidence filter
- `?user=mike` — selected user on paper trading
- `?range=7d` — date range on track record

Benefits: shareable links, browser back/forward works with filters, page refreshes preserve state.

**File changes:** Modify all page components to read/write search params instead of local React state.

### 2e. WebSocket Client

React hook (`useWebSocket`) that connects to `/ws` on mount and dispatches events to Zustand stores:

- `score_update` → update game scores in picks/props views
- `pick_graded` → update pick results, trigger toast notification
- `feed_event` → prepend to activity feed
- `leaderboard_update` → update rankings

Auto-reconnect with exponential backoff. Fallback to polling every 30s if WebSocket fails.

**File changes:** New `frontend/src/hooks/useWebSocket.ts`. New `frontend/src/stores/feedStore.ts`.

### 2f. PWA Support

- `manifest.json` with app name, icons, theme color, display: standalone
- Service worker (Vite PWA plugin) for offline caching of shell + last-fetched data
- Web Push API integration for notifications:
  - "Your pick on Lakers ML just won! +$4,200"
  - "New daily picks are ready"
  - "You dropped to #2 on the leaderboard"

**File changes:** New `frontend/public/manifest.json`, Vite PWA plugin config in `vite.config.ts`.

---

## Section 3: Paper Trading — Social Hub

### 3a. Hybrid Layout: Leaderboard + Activity Feed

**Page structure (top to bottom):**

1. **Compact leaderboard bar** — always visible at top of Paper Trading page. Horizontal layout: rank, name, ROI, balance, streak indicator. On mobile: horizontal scroll if >4 users.
2. **Activity feed** — chronological list of events: picks placed, picks graded (won/lost), streak milestones. Each event shows: user avatar/color, action, pick details, timestamp.
3. **User profile section** — when a user is selected (from leaderboard or URL), shows their stats cards (ROI, W-L, best streak) and pick history.

**Feed event types:**
- `pick_placed` — "Mike bet Lakers ML +150 — $5,000 (Edge: +8.2%)"
- `pick_won` — "Sarah hit LeBron Over 25.5 pts — Won +$2,850"
- `pick_lost` — "Dave lost Celtics -4.5 — Lost $10,000"
- `streak` — "Sarah is on a 5-pick win streak!"

### 3b. "Bet This" Button — Embedded Everywhere

Not just on the Paper Trading page. Every game card (Today's Picks) and every prop row (Player Props) gets a "Bet This" button.

**Flow (2 taps):**
1. Tap "Bet This" on any game card or prop row
2. Modal appears pre-filled with: pick details, current odds, AI-suggested stake (from Kelly sizing), edge %
3. User adjusts stake if desired (slider or input)
4. Tap "Confirm" → pick is placed, toast notification, event pushed to activity feed via WebSocket

**If no user is logged in:** Modal prompts "Enter your name to start paper trading" with a single text field. One-time setup, then the pick flow continues.

**File changes:** New `frontend/src/components/BetModal.tsx`. Modify `PicksTable.tsx`, `PlayerProps.tsx`, and new game card components to include Bet This buttons.

### 3c. Streaks & Badges

- **Win streak** tracked in real-time (consecutive wins across all pick types)
- **Hot/cold indicator** on leaderboard: fire icon for 3+ win streak, ice for 3+ loss streak
- **Best streak** shown on user profile
- **Future consideration (not in v1):** Achievement badges (e.g., "Sharp Shooter" for 10+ consecutive wins, "Prop King" for most profitable prop bettor)

**File changes:** Streak calculation in `backend/api/users.py` (computed from PaperPick results, not a separate table — keeps it simple). Frontend displays streak in leaderboard and user profile.

### 3d. Auto-Grading

**Current problem:** Manual "Grade Picks" button grades all users' picks globally.

**Solution:** Picks are auto-graded when game results come in:
- Pipeline fetches final scores → grader runs → marks PaperPicks as won/lost → WebSocket pushes `pick_graded` events
- No manual button needed
- Grading happens per-game as results come in (not all at once)

**File changes:** Modify `backend/pipeline/grader.py` to also grade PaperPicks. Add WebSocket event emission after grading.

---

## Section 4: Today's Picks & Player Props

### 4a. Today's Picks — Game Cards

Replace the current table layout with **game cards**:

- Each card: home team vs away team, game time/status, spread/moneyline/O/U odds, AI pick with confidence stars and edge %, "Bet This" button
- Cards stack vertically on mobile, 2-column grid on desktop
- Sport tabs get pill-style design with game count badges: "NBA (8)" "NCAAB (2)"
- **Collapsible prop picks per game:** Below each game card, "3 prop picks for this game" expands to show top player props for that matchup

**Summary bar (replaces current SummaryCards):**
- Single row at top: strategy dropdown | today's record (W-L) | today's ROI | total season record
- Compact and actionable — strategy is switchable, not static text

**File changes:** New `frontend/src/components/GameCard.tsx`. Modify `frontend/src/pages/TodaysPicks.tsx`. Modify `frontend/src/components/SummaryCards.tsx` into `SummaryBar.tsx`.

### 4b. Player Props — Flat Searchable List

Replace nested matchup→player→market grouping with a flat, searchable, sortable table:

- **Search bar** at top: type player name to filter instantly
- **Sort options:** Edge % (default), confidence, player name, market
- **Each row:** Player name, team, market (Points/Rebounds/etc), line, Over odds, Under odds, projection, edge %, confidence stars, "Bet This" button
- **Stale data indicator:** If data is >7 days old, show age: "Source: season_avg (9 days old)"
- **Pagination:** 25 per page, infinite scroll on mobile

**File changes:** Modify `frontend/src/pages/PlayerProps.tsx`.

### 4c. Track Record Improvements

- **Date range picker** + sport filter at top (URL-persisted)
- **Cumulative P&L chart:** Running total curve (not just daily). Option to overlay multiple strategies.
- **Confidence tier breakdown table:** Win rate, ROI, avg edge, and sample size for each confidence level (1-5 stars). Key diagnostic: are higher-confidence picks actually more profitable?
- **Calendar heatmap fixes:** 24x24px cells, month separators, color legend below, tap cell to show that day's picks in a popover

**File changes:** Modify `frontend/src/pages/TrackRecord.tsx`, `CalendarHeatmap.tsx`, `PerformanceChart.tsx`.

---

## Section 5: Backtesting & Pipeline

### 5a. Backtesting Overhaul

- **Side-by-side variant comparison:** Select 2+ variants, see overlaid cumulative ROI curves on the same chart (color-coded lines with legend)
- **Progress bar:** WebSocket pushes backtest progress. UI shows "Processing game 342/1012..." with estimated time remaining
- **Persistent results:** Backtest results saved to `BacktestRun` table (already exists). Changing filters doesn't wipe results — previous runs are accessible in a "Run History" dropdown
- **Chart shows backtest data:** The performance chart renders the actual backtest results, not unrelated historical daily stats
- **Confidence breakdown:** Table with win rate, ROI, avg edge, sample size per confidence tier

### 5b. Walk-Forward Validation Visualization

New chart: model accuracy (or log-loss) over time, where each data point represents the model's prediction accuracy for that week using only data available at prediction time.

Reveals whether the model is improving with more data, and identifies stretches where it underperforms (e.g., early season when rosters are new).

### 5c. Auto-Tune UI

- "Auto-Tune" button on backtesting page
- Runs the recalibration loop (Section 1d) and displays: before/after thresholds, projected ROI change, sample sizes per tier
- Results stored with timestamp — tuning history viewable as a table

### 5d. Pipeline Dashboard

Replace manual "Run Pipeline" button with a status dashboard:

- **Last run:** timestamp, games fetched count, odds updated count, props collected count
- **Next scheduled run:** countdown timer
- **Error log:** Failed API calls with timestamps and error messages
- **Manual trigger:** "Run Now" button still available but secondary

**File changes:** Modify `frontend/src/pages/Backtesting.tsx`. New `frontend/src/components/PipelineDashboard.tsx`. Backend: modify `backend/api/pipeline_api.py` to expose pipeline status and history.

---

## Section 6: Database & Infrastructure

### 6a. New Database Tables

```sql
-- Activity feed for social features
CREATE TABLE activity_feed (
    id INTEGER PRIMARY KEY,
    user_id INTEGER REFERENCES user_profiles(id),
    event_type TEXT NOT NULL,  -- pick_placed, pick_won, pick_lost, streak
    payload TEXT NOT NULL,      -- JSON with event details
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Confidence recalibration tracking
CREATE TABLE calibration_history (
    id INTEGER PRIMARY KEY,
    date DATE NOT NULL,
    sport TEXT NOT NULL,
    confidence_tier INTEGER NOT NULL,
    predicted_win_rate REAL,
    actual_win_rate REAL,
    sample_size INTEGER,
    old_threshold REAL,
    new_threshold REAL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- ML model performance tracking
CREATE TABLE model_metrics (
    id INTEGER PRIMARY KEY,
    date DATE NOT NULL,
    sport TEXT NOT NULL,
    model_version TEXT NOT NULL,
    accuracy REAL,
    log_loss REAL,
    feature_importances TEXT,  -- JSON
    training_games INTEGER,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);
```

**Modifications to existing tables:**
- `paper_picks`: Add `graded_at DATETIME` column for auto-grading timestamp
- `user_profiles`: Add `current_streak INTEGER DEFAULT 0`, `best_streak INTEGER DEFAULT 0`, `streak_type TEXT DEFAULT 'none'`

### 6b. WebSocket Server

FastAPI WebSocket endpoint at `/ws`:

```
Client connects → subscribes to channels
Server pushes events:
  - scores: live game score updates
  - feed: activity feed events (pick placed, graded, streak)
  - leaderboard: rank changes after picks are graded
  - backtest_progress: progress updates during backtest runs
```

Connection manager tracks active connections. Fallback: SSE endpoint at `/sse` for environments where WebSocket is unreliable.

**File changes:** New `backend/api/websocket.py` with ConnectionManager class. Modify `backend/api/main.py` to mount WebSocket endpoint.

### 6c. Nightly Recalibration Job

Added to existing APScheduler in `backend/pipeline/scheduler.py`:

**Schedule:** Runs daily at 3:00 AM ET (after all games are typically final)

**Steps:**
1. Grade any remaining ungraded picks/paper picks
2. Retrain LightGBM model using walk-forward on all completed games
3. Calculate actual win rate per confidence tier (last 30/60/90 days)
4. Adjust confidence thresholds if deviation > 5 percentage points
5. Update Kelly fractions based on model calibration accuracy
6. Log all metrics to `calibration_history` and `model_metrics` tables
7. Push summary to activity feed: "Model recalibrated: 5-star accuracy improved from 62% to 67%"

**File changes:** New `backend/pipeline/recalibration_job.py`. Modify `backend/pipeline/scheduler.py` to add the job.

### 6d. CSS Approach

Keep the current custom CSS design system in `index.css`. Additions:
- Utility classes for common spacing patterns (reduce inline styles)
- New component classes for game cards, bottom nav, bet modal
- Responsive breakpoints: add 1024px (tablet) alongside existing 768px and 380px
- Increase base touch targets to 44x44px minimum

No Tailwind migration — the current approach is clean and a full migration would be high churn for marginal benefit.

---

## Implementation Phases

**Phase 1 — Prediction Engine (backend-only, no UI changes)**
- LightGBM model, expanded features, fix spread/OU math, vig adjustment
- Recalibration loop and confidence threshold from DB
- Dynamic Kelly sizing
- Deliverable: improved pick accuracy, measurable via backtesting

**Phase 2 — Frontend Architecture (infrastructure, no new features)**
- Zustand stores, React Query hooks, WebSocket client
- Mobile responsive nav (hamburger + bottom tabs)
- URL state management
- Deliverable: faster, more reliable UI with mobile support

**Phase 3 — Paper Trading Social Hub**
- Hybrid leaderboard + activity feed layout
- "Bet This" modal embedded across all pages
- Auto-grading, streaks, WebSocket feed events
- Deliverable: shareable app ready for friends

**Phase 4 — Pages Overhaul**
- Today's Picks game cards
- Player Props flat searchable list
- Track Record improvements (date picker, cumulative chart, confidence breakdown)
- Backtesting comparison, progress, persistence
- Pipeline dashboard

**Phase 5 — PWA & Polish**
- Service worker, manifest, push notifications
- Walk-forward visualization
- Auto-tune UI
- Accessibility pass (touch targets, contrast, aria labels)

---

## Success Criteria

1. **ROI improvement:** Backtesting shows measurable improvement in ROI and win rate vs. current heuristic model (target: +3% ROI improvement)
2. **Confidence calibration:** 5-star picks win at 65%+ rate, with monotonically decreasing win rate from 5→1 stars
3. **Mobile usability:** All core flows (view picks, place paper bet, check leaderboard) work smoothly on a phone
4. **Pick placement:** Two taps from any page to place a paper trade
5. **Friend adoption:** Friends can open the app, create a profile, and place their first pick within 60 seconds
6. **Real-time feel:** Score updates, pick grading, and leaderboard changes appear within 5 seconds of the underlying event

# ROI Overhaul Phase 2: Frontend Architecture

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Zustand state management, React Query data fetching, WebSocket real-time updates, mobile-responsive navigation, and URL state management to the frontend.

**Architecture:** Zustand for global state (user, app prefs, feed), React Query for server state with caching/dedup, WebSocket for real-time events, bottom tab bar for mobile nav.

**Tech Stack:** Zustand, @tanstack/react-query, React Router useSearchParams, WebSocket API

**Spec:** `docs/superpowers/specs/2026-03-16-roi-and-ui-overhaul-design.md` (Section 2 + Section 6b)

---

## File Structure

```
frontend/src/
  stores/
    appStore.ts          # Create: sport filter, active strategy, UI prefs (persisted)
    userStore.ts         # Create: selected user, auth state
    feedStore.ts         # Create: activity feed events, WS connection state
  hooks/
    useWebSocket.ts      # Create: WS connection with auto-reconnect
    useTodaysPicks.ts    # Create: React Query hook for today's picks
    useLeaderboard.ts    # Create: React Query hook for user leaderboard
    useProps.ts          # Create: React Query hook for player props
    useRecord.ts         # Create: React Query hook for track record
  components/
    Layout.tsx           # Modify: add mobile nav, sport filter from Zustand
    BottomNav.tsx        # Create: fixed bottom tab bar for mobile
    MobileMenu.tsx       # Create: hamburger slide-out menu for secondary pages
  pages/
    TodaysPicks.tsx      # Modify: use React Query + Zustand sport filter
    PlayerProps.tsx       # Modify: use React Query + Zustand
    PaperTrading.tsx     # Modify: use React Query + Zustand
    TrackRecord.tsx      # Modify: use React Query + URL state
  App.tsx                # Modify: wrap with QueryClientProvider
  main.tsx               # Modify: wrap with QueryClientProvider

backend/
  api/
    websocket.py         # Create: WebSocket endpoint with ConnectionManager
    main.py              # Modify: mount WebSocket endpoint
```

---

## Chunk 1: Dependencies & State Management

### Task 1: Install frontend dependencies

- [ ] Install zustand and @tanstack/react-query
- [ ] Verify imports work

### Task 2: Zustand stores

- [ ] Create appStore (sport filter, active strategy)
- [ ] Create userStore (selected user)
- [ ] Create feedStore (activity events, WS state)

### Task 3: React Query setup

- [ ] Add QueryClientProvider to App
- [ ] Create query hooks for picks, props, leaderboard, record

## Chunk 2: WebSocket Infrastructure

### Task 4: WebSocket backend endpoint

- [ ] Create ConnectionManager class
- [ ] Create /ws endpoint in FastAPI
- [ ] Mount in main.py, add /ws proxy in vite.config.ts

### Task 5: WebSocket frontend hook

- [ ] Create useWebSocket hook with auto-reconnect
- [ ] Dispatch events to Zustand feedStore

## Chunk 3: Mobile Navigation

### Task 6: Mobile-responsive navigation

- [ ] Create BottomNav component (fixed bottom tabs)
- [ ] Create MobileMenu component (hamburger slide-out)
- [ ] Update Layout to show bottom nav on mobile, top nav on desktop
- [ ] Add CSS for mobile nav, 44px touch targets

## Chunk 4: URL State & Page Migration

### Task 7: Migrate pages to React Query + Zustand

- [ ] Update TodaysPicks to use useTodaysPicks hook + appStore sport filter
- [ ] Update PlayerProps to use useProps hook
- [ ] Update TrackRecord to use URL state for date range/sport filter
- [ ] Update PaperTrading to use useLeaderboard hook

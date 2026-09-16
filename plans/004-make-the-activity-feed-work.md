# Plan 004: Make the activity feed actually work (route order + WebSocket dispatch)

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md`.
>
> **Drift check (run first)**:
> `git diff --stat 943cf25..HEAD -- backend/api/users.py backend/api/main.py backend/api/websocket.py`
> If any in-scope file changed since this plan was written, compare the
> "Current state" excerpts against the live code before proceeding; on a
> mismatch, treat it as a STOP condition.

## Status

- **Priority**: P1
- **Effort**: M
- **Risk**: MED
- **Depends on**: 001 — **MERGED**. `test_feed_route_is_shadowed` exists at
  `backend/tests/test_api_users.py:489` asserting the current broken `422`.
  Inverting it is now MANDATORY, not conditional.
- **Category**: bug
- **Planned at**: commit `5c2e0d0`, 2026-09-16
- **Refreshed at**: commit `943cf25`, 2026-09-16 — plans 001/002/003/005/006
  merged. **None of this plan's in-scope source files changed** (`users.py`,
  `main.py`, `websocket.py` are byte-identical to `5c2e0d0`), so every excerpt
  below is still accurate. What changed: `backend/tests/test_api_users.py` now
  exists, and the baseline suite is **409 passed**, not 360.

## Why this matters

The activity feed — leaderboard events, "X bet Lakers ML", "Y is on a 5-pick
win streak" — is a designed, built, front-end-rendered feature that **cannot
deliver a single event**. It is broken in both of its two delivery paths
simultaneously, which is why neither failure was ever noticed:

1. **The REST path returns 422.** `GET /users/{user_id}` is registered before
   `GET /users/feed`, and FastAPI matches routes in registration order — not
   most-specific-first. So `/users/feed` is captured by the `{user_id}` route
   and fails integer parsing. Verified:
   `GET /users/feed -> 422 {"detail":[{"type":"int_parsing", ... "input":"feed"}]}`
2. **The WebSocket path silently no-ops.** `_log_feed_event` dispatches the
   broadcast with `asyncio.get_event_loop().create_task(...)`, but every caller
   is a **synchronous** FastAPI handler, which runs in an `anyio` worker
   thread. There is no event loop in that thread, so `get_event_loop()` raises
   `RuntimeError`, which a bare `except RuntimeError: pass` swallows — with a
   comment claiming this only happens in tests. It happens on every single call
   in production.

The rows *are* written to the `activity_feed` table. Both ways of reading them
back are broken. The frontend connects, holds the connection open with its
ping/pong heartbeat, and therefore looks perfectly healthy while receiving
nothing.

## Current state

### Route registration order in `backend/api/users.py`

The router is mounted at prefix `/users` (`backend/api/main.py:101`:
`app.include_router(users_router, prefix="/users", tags=["users"])`).

Registration order within the file:

| Line | Decorator |
|---|---|
| 61 | `@router.get("/")` |
| 103 | `@router.post("/")` |
| 119 | `@router.delete("/{user_id}")` |
| **130** | **`@router.get("/{user_id}")`** ← captures `/feed` |
| 171 | `@router.post("/{user_id}/picks")` |
| 275 | `@router.get("/{user_id}/picks")` |
| 318 | `@router.post("/{user_id}/parlay")` |
| 458 | `@router.post("/grade")` |
| **634** | **`@router.get("/feed")`** ← never reached |

`POST /users/grade` at :458 is **not** affected — it differs from
`POST /users/{user_id}/picks` in segment count, so there is no collision. Only
the `GET` pair collides.

The handler itself at `users.py:634-660` is correct and needs no logic change:

```python
@router.get("/feed")
def get_activity_feed(request: Request, limit: int = 50):
    """Get recent activity feed events."""
    session = get_session(request.app.state.engine)
    try:
        events = (
            session.query(ActivityFeed)
            .order_by(ActivityFeed.created_at.desc())
            .limit(limit)
            .all()
        )
        return [
            {
                "id": e.id,
                "user_id": e.user_id,
                "event_type": e.event_type,
                "payload": json.loads(e.payload),
                "created_at": e.created_at.isoformat() if e.created_at else None,
            }
            for e in events
        ]
    finally:
        session.close()
```

### The broadcast dispatch

`backend/api/users.py:15-30`:

```python
def _log_feed_event(session, user_id: int | None, event_type: str, payload: dict):
    """Save activity event to DB and broadcast via WebSocket."""
    session.add(ActivityFeed(
        user_id=user_id,
        event_type=event_type,
        payload=json.dumps(payload),
    ))
    session.commit()
    # Broadcast via WebSocket (fire-and-forget)
    try:
        from backend.api.websocket import manager
        asyncio.get_event_loop().create_task(
            manager.broadcast(event_type, payload)
        )
    except RuntimeError:
        pass  # No event loop running (e.g., in tests)
```

Every caller is a `def` (not `async def`) handler:
- `users.py:171` `def place_pick`
- `users.py:318` `def place_parlay`
- `users.py:458` `def grade_paper_picks`
- `users.py:529` `def _update_streaks`

### The app lifespan, where the loop must be captured

`backend/api/main.py:27-56` — the lifespan already exists and runs on the main
thread inside the event loop:

```python
def create_app(db_path: str = "sports_picks.db") -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        scheduler = None
        if os.environ.get("ENABLE_SCHEDULER", "0") == "1":
            ...
        else:
            app.state.scheduler = None
        try:
            yield
        finally:
            if scheduler is not None and scheduler.running:
                scheduler.shutdown(wait=False)
```

Note the app object is also reachable from handlers via `request.app.state`,
which is how the engine is accessed everywhere in this codebase
(`get_session(request.app.state.engine)`).

### The connection manager

`backend/api/websocket.py:9-58` — `ConnectionManager.broadcast` is an
`async def` that sends to every socket in `self.active_connections` and prunes
failures. The endpoint only cleans up in `except WebSocketDisconnect:` with no
`finally`, and `disconnect()` calls `list.remove()` which raises `ValueError`
if the socket was already pruned by `broadcast`:

```python
    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)
        logger.info("WebSocket disconnected. Active: %d", len(self.active_connections))
```

```python
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint at /ws."""
    await manager.connect(websocket)
    try:
        while True:
            data = await websocket.receive_text()
            try:
                msg = json.loads(data)
                if msg.get("type") == "ping":
                    await websocket.send_text(json.dumps({"type": "pong"}))
            except json.JSONDecodeError:
                pass
    except WebSocketDisconnect:
        manager.disconnect(websocket)
```

### Frontend contract (do not change, just satisfy)

`frontend/src/api/client.ts:121` calls `GET /users/feed?limit=N` and expects a
list of `{ id, user_id, event_type, payload, created_at }`.
`frontend/src/hooks/useWebSocket.ts` expects frames shaped
`{"type": <event_type>, "data": <payload>}` — which is exactly what
`ConnectionManager.broadcast` already produces.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Full suite | `.venv/Scripts/python.exe -m pytest backend/tests -q` | 0 failed |
| Feed tests | `.venv/Scripts/python.exe -m pytest backend/tests/test_api_users.py backend/tests/test_websocket.py -q` | all pass |
| Route probe | see step 2 | `200` |

Run from repo root.

## Scope

**In scope**:
- `backend/api/users.py` — **only** the route-declaration order and
  `_log_feed_event`. Do not touch balance math, parlay logic, or grading.
- `backend/api/main.py` — capture the running loop in the lifespan.
- `backend/api/websocket.py` — idempotent disconnect + `finally` cleanup.
- `backend/tests/test_api_users.py` (invert the characterization assertion)
- New test file for the WebSocket integration.

**Out of scope** (do NOT touch, even though they look related):
- The balance / parlay / bankroll bugs in `users.py` — separate plans. You are
  editing this file; stay inside `_log_feed_event` and the decorators.
- `backend/pipeline/scheduler.py` — its `grade_pending_picks` does **not** call
  `_log_feed_event` at all (a separate known drift). Do not "fix" that here;
  it changes grading behavior and belongs with the grading-consolidation work.
- Adding authentication or connection caps to the WebSocket — separate
  security finding.
- `frontend/` — the client already speaks this protocol correctly.

## Git workflow

- Branch: `advisor/004-activity-feed`
- Conventional commits:
  - `fix(api): register /users/feed before /users/{user_id}`
  - `fix(ws): dispatch broadcasts to the app event loop from sync handlers`
  - `fix(ws): make disconnect idempotent and always clean up`

## Steps

### Step 1: Baseline

**Verify**: `.venv/Scripts/python.exe -m pytest backend/tests -q` → 0 failed.
Record the count.

### Step 2: Fix the route order

Move the entire `@router.get("/feed")` handler (`users.py:634-660`) so it is
declared **above** `@router.delete("/{user_id}")` at :119. Placing it directly
after the `POST /` handler is the clearest spot.

Move the function body verbatim — no logic changes.

While you are there, add a bound to the `limit` parameter so the endpoint can't
be asked for the entire table:
`limit: int = Query(50, ge=1, le=200)` (import `Query` from `fastapi`).

**Verify** — this must print `200` and a list:

```
.venv/Scripts/python.exe -c "
from fastapi.testclient import TestClient
from backend.api.main import create_app
c = TestClient(create_app(':memory:'))
r = c.get('/users/feed')
print(r.status_code, r.json())
"
```

Before the change this prints `422` and an `int_parsing` error. After, it must
print `200 []`.

Also confirm you didn't break the sibling route:
`c.get('/users/1')` must still return `404` (user not found), **not** 422.

### Step 3: Add a route-ordering regression test

Add to `backend/tests/test_api_users.py`:

- `test_feed_route_not_shadowed` — `GET /users/feed` → 200, returns a list.
- `test_feed_limit_is_capped` — `GET /users/feed?limit=99999` → 422.
- `test_get_user_by_id_still_works` — `GET /users/{id}` for a real user → 200.

**Plan 001 has landed**, so this step is required. It contains `test_feed_route_is_shadowed` at `backend/tests/test_api_users.py:489` asserting
`status_code == 422`, marked `# CHARACTERIZATION`. **Invert it now** — change
it to assert 200, remove the CHARACTERIZATION marker, and delete the comment
about plan 004. Do not delete the test.

**Verify**: `.venv/Scripts/python.exe -m pytest backend/tests/test_api_users.py -q`
→ all pass, and
`grep -c "CHARACTERIZATION" backend/tests/test_api_users.py` returns one fewer
than before (5 if 001 wrote 6).

### Step 4: Capture the event loop at startup

In `backend/api/main.py`, inside the `lifespan` async context manager and
**before** the `yield`, store the running loop on app state:

```python
        app.state.loop = asyncio.get_running_loop()
```

Add `import asyncio` at the top of the file. Set `app.state.loop = None` in
`create_app` before the routers are registered, so the attribute always exists
even when the lifespan hasn't run (which is the case for some tests).

**Verify**: `.venv/Scripts/python.exe -m pytest backend/tests -q` → 0 failed
(this step alone changes no behavior).

### Step 5: Dispatch broadcasts onto that loop

Rewrite the dispatch half of `_log_feed_event` in `backend/api/users.py`. It
must:

- Take the loop from app state rather than calling `asyncio.get_event_loop()`.
  `_log_feed_event` currently receives only `session` — add a parameter for the
  loop (or for the `request`/`app`) and update all four call sites
  (`place_pick`, `place_parlay`, `grade_paper_picks`, `_update_streaks`).
  `_update_streaks` is itself called from `place_pick` and `grade_paper_picks`,
  so it needs the value threaded through too.
- Use `asyncio.run_coroutine_threadsafe(manager.broadcast(event_type, payload), loop)`
  when a loop is available. This is the thread-safe way to schedule work on a
  loop owned by another thread, which is exactly the situation here.
- When no loop is available (`app.state.loop is None`, e.g. unit tests
  constructing the router directly), skip the broadcast **and log at debug**,
  not silently.
- Replace the bare `except RuntimeError: pass` with
  `except Exception: logger.warning("Feed broadcast failed", exc_info=True)`
  so a future regression is visible. Add a module-level
  `logger = logging.getLogger(__name__)` if `users.py` doesn't have one
  (check first — most modules in this repo do).

Do **not** make `broadcast` block the request; `run_coroutine_threadsafe`
returns a `concurrent.futures.Future` and you should not call `.result()` on it.

**Verify**: `.venv/Scripts/python.exe -m pytest backend/tests -q` → 0 failed.

### Step 6: Prove a frame actually arrives

This is the load-bearing verification for this plan. Per this repo's evidence
rules, *"a tool reporting 'connected' is not a working tool"* — the feed has
looked connected for months while delivering nothing. You must verify with a
real frame.

Create `backend/tests/test_websocket.py` with an integration test that:

1. builds the app via `create_app(":memory:")` and wraps it in `TestClient`
2. seeds a user and a `final` game (reuse the seed helpers from
   `backend/tests/test_api_users.py`; import or duplicate them following the
   pattern in `test_api_picks.py`)
3. opens `with client.websocket_connect("/ws") as ws:`
4. inside that block, POSTs a pick to `/users/{id}/picks`
5. asserts `ws.receive_json()` returns a frame with
   `["type"] == "pick_placed"` and a `["data"]["message"]` containing the
   user's name

Note: `TestClient` runs the app with a real event loop via `portal`, so
`app.state.loop` will be populated. If the frame does not arrive, the dispatch
is still broken — do not weaken the assertion to a timeout-tolerant one.

**Prove the guard is real**: temporarily revert step 5's dispatch to
`asyncio.get_event_loop().create_task(...)`. The test **must fail**. Then
restore.

**Verify**: `.venv/Scripts/python.exe -m pytest backend/tests/test_websocket.py -q`
→ passes; and fails when the dispatch is reverted.

### Step 7: Make disconnect idempotent and always run

In `backend/api/websocket.py`:

- Change `disconnect` to guard membership before removing, so double-removal
  (already pruned by `broadcast`) cannot raise `ValueError`.
- Wrap the receive loop in `try/except WebSocketDisconnect` **plus** a
  `finally:` that calls `manager.disconnect(websocket)`, so a connection reset
  or protocol error can't leak a dead socket into `active_connections` forever.
  Make sure `disconnect` is not called twice in the disconnect path — with the
  membership guard it is now safe either way.

**Verify** — add tests to `test_websocket.py`:
- `test_disconnect_is_idempotent` — call `manager.disconnect(ws)` twice on a
  fake socket; no exception.
- `test_connection_removed_after_close` — open and close a
  `websocket_connect`, then assert `len(manager.active_connections) == 0`.

`.venv/Scripts/python.exe -m pytest backend/tests/test_websocket.py -q` → all pass.

### Step 8: Full suite

**Verify**:
- `.venv/Scripts/python.exe -m pytest backend/tests -q` → 0 failed
- `git diff --name-only` → only in-scope files
- `git diff backend/api/users.py | grep -E "^\+.*(current_balance|starting_balance|parlay|stake)"`
  → **no output** (proves you didn't touch the money math)

## Test plan

New tests:
1. `test_feed_route_not_shadowed` (step 3)
2. `test_feed_limit_is_capped` (step 3)
3. `test_get_user_by_id_still_works` (step 3)
4. `backend/tests/test_websocket.py::test_pick_placed_broadcasts_frame` (step 6)
   — **the load-bearing test**
5. `test_disconnect_is_idempotent` (step 7)
6. `test_connection_removed_after_close` (step 7)

Updated: `test_feed_route_is_shadowed` in `test_api_users.py`, inverted (step 3).

Pattern to follow: `backend/tests/test_api_picks.py` for structure;
`TestClient.websocket_connect` for the WS tests.

## Done criteria

ALL must hold:

- [ ] `.venv/Scripts/python.exe -m pytest backend/tests -q` exits 0, 0 failed
- [ ] `GET /users/feed` returns 200 (step 2 probe)
- [ ] `GET /users/1` still returns 404, not 422
- [ ] `grep -n "get_event_loop" backend/api/users.py` returns **no matches**
- [ ] `grep -n "except RuntimeError" backend/api/users.py` returns no matches
- [ ] The step-6 WebSocket test passes, and was demonstrated to fail when the
      old dispatch is restored
- [ ] `git diff backend/api/users.py` shows no change to any balance, stake, or
      parlay expression
- [ ] `plans/README.md` status row for 004 updated

## STOP conditions

Stop and report back (do not improvise) if:

- The baseline suite doesn't pass cleanly.
- `GET /users/feed` already returns 200 before you change anything — this plan
  has already been applied; report rather than re-applying.
- The step-6 test cannot receive a frame even after the `run_coroutine_threadsafe`
  change. Do **not** relax the assertion, add a sleep-and-retry, or mark it
  `xfail`. Report what you observe: whether `app.state.loop` is populated,
  whether `broadcast` is entered, and whether `active_connections` is non-empty
  at dispatch time.
- Threading the loop through `_update_streaks` appears to require changing its
  signature in a way that touches grading logic. Report the signature you need.
- You find yourself editing `backend/pipeline/scheduler.py` — out of scope.

## Maintenance notes

- **Route ordering is now load-bearing in `users.py`.** Any future
  `GET /users/<literal>` route must be declared above `GET /{user_id}`. The
  regression test in step 3 only covers `/feed`; a reviewer adding a new static
  segment should add a matching test.
- **This turns on a broadcast path that has never executed in production.**
  Expect latent payload-shape bugs on first real run — the `_log_feed_event`
  payloads have only ever been serialized into the DB, never sent over the
  wire. Watch the first day of real events.
- The WebSocket endpoint remains **unauthenticated and uncapped**. This plan
  deliberately does not address that (separate security finding), but note that
  making the feed functional increases the value of connecting to it.
- Deferred: the client-side reconnect in `frontend/src/hooks/useWebSocket.ts`
  schedules a new timer from `onclose` even during unmount cleanup, and uses
  pure exponential backoff with no jitter. Neither blocks this plan.

# Sportsbook Phase 5 — Leaders, Feed and Tail — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the league social.
- A **Leaders** tab shows the ranked board with streaks, plus a live feed of everyone's bets and results.
- Any placed bet in the feed can be **tailed** onto your own slip at today's price.
- The Lobby carries a one-line ticker of the latest activity.
- Settled bets announce themselves whether they were graded automatically or by the owner's button, exactly once each.

**Architecture:**
- **Backend:**
  - A new `backend/paper/feed.py` owns the activity feed:
    - `log_feed_event`, moved from `api/users.py`;
    - `update_streaks`, moved;
    - `announce_settlements`, which writes de-duplicated won/lost/pushed events per bet;
    - `settle_and_announce`, the one function both `POST /users/grade` and the scheduler's `grade_pending_picks` now call.
  - `paper_settlement.settle_parlays_list` returns the parlays it settled; `settle_parlays` keeps returning a count.
  - Placed-bet events gain `bet_id`, `kind` and `legs`. Each leg is exactly the bet request a friend's Tail sends, plus display fields.
- **Frontend:**
  - A `FeedItem` type and a `useFeed` hook, which the WebSocket invalidates.
  - `lib/tail.ts` re-prices a feed bet's legs at the **current** quote.
  - A `Leaders` page at `/leaders`, a `FeedTicker` on the Lobby, and a Leaders tab.
  - `/paper-trading` redirects to `/bets`. The PaperTrading page and the components only it used are deleted.

**Tech Stack:** FastAPI, SQLAlchemy (SQLite `json_extract`), pytest; React 19, react-router 7, react-query 5, zustand 5, vitest.

**Spec:** `docs/superpowers/specs/2026-10-07-sportsbook-ui-design.md`. This plan implements §10, §4's Leaders tab and `/paper-trading` redirect, and the Phase 5 row of §13. Cash outs on Leaders are Phase 6.

## Global Constraints

- **Leaderboard ranking.** The rules are unchanged: `/users/leaderboard`, shrunk ROI, `MIN_RANKED_BETS = 10`, straight bets only.
- **Settlement events are de-duplicated per bet.** A straight bet or a parlay gets **at most one** won/lost/pushed event, keyed by `bet_key` (`straight-<id>` / `parlay-<id>`) in the payload and checked with `json_extract`.
  - Parlay legs never get their own settlement event.
  - Both grading paths call `settle_and_announce`, so they cannot drift (spec §10).
- **Feed payload shape.** A placed-bet payload's `legs` holds, per leg:
  - exactly the fields `POST /users/{id}/picks` accepts as a leg (`game_id`, `pick_type`, plus `side`, or `prop_player`, `prop_market`, `outcome` and `line`);
  - plus `label`, `game_label`, `start_time`, `home_team`, `away_team`, `odds` and `line`.

  Older events have no `legs`; they show no Tail button and never crash.
- **Tail prices.** Tail never uses the price the friend got. Each leg is re-priced from `/paper/quotes` or `/paper/prop-quotes` now. A leg the server refuses goes on the slip carrying the refusal as its error (spec §10: "shows as locked"), and the server re-checks on placement.
- **Visibility.** All bets and stakes stay visible to everyone in the league, as they are today.
- **No database migration in this phase.** `bet_key` lives in the existing JSON `payload`.
- **Never run `npm run build` in the main working tree on a branch** (memory `sports-picks-dist-is-live`). Build only in the Task 7 worktree, and on master at merge.
- **Write code with the Write and Edit tools.** Anything containing backslashes goes through them, never through Bash heredocs (memory `sports-picks-heredoc-backslash`).
- **Commits.**
  - Messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
  - Never stage `config.yaml`. Stage only the named files.
- **Mutation checks.** Mutation-check each guard. Delete the touched `.pyc` after every Python write and every restore.

## Review Focus

1. **The owner presses Grade while the scheduler is grading.** Each bet still gets exactly one settlement event. Test is in Task 2.
2. **An old feed event without `legs` or `bet_id`, or a malformed one.** It renders as text with no Tail button and never crashes the feed. Tests are in Tasks 3 and 5.
3. **Tailing a bet whose game has started, or whose price is stale.** The leg lands on the slip locked, with the server's message, never at the friend's old price. A live leg is added at **today's** price. Test is in Task 4.
4. **Tailing a bet whose market is already on your slip.** It swaps (one leg per market), never duplicates. Test is in Task 4.
5. **The Model row and unranked players on Leaders.** The Model has no id, no streak and no avatar link. Unranked players read "Unranked (n/10)", and your own row stays pinned when it's off-screen. Test is in Task 5.

---

### Task 1: Placed-bet events carry `bet_id`, `kind` and Tail-ready `legs`

**Files:**
- Modify: `backend/api/users.py` (`place_pick` and `place_parlay` payloads, plus a `_feed_leg` helper)
- Test: `backend/tests/test_paper_feed.py` (new)

**Interfaces:**
- **Consumes:** the request leg models (`GameLeg`, `PropLeg` and the `*BetRequest`s), `pricing.Quote`, `api.picks._resolve_pick_value`, `time_utils.game_start_utc`.
- **Produces:**
  - **`pick_placed` payload:** `{user_name, message, pick_value, odds, stake, bet_id, kind: "straight"|"parlay", legs: [FeedLeg]}`. A parlay payload keeps its existing `parlay` and `legs` count keys, renamed `leg_count`.
  - **`FeedLeg`:** the request leg fields, plus `label`, `game_label`, `start_time`, `home_team`, `away_team`, `odds` and `line`.

- [ ] **Step 1: Write the failing tests, `backend/tests/test_paper_feed.py`**

```python
"""The league feed (sportsbook spec 2026-10-07 §10): Tail-ready placed-bet
events, and settlement events shared by both grading paths, once per bet."""
import json

from fastapi.testclient import TestClient

from backend.api.main import create_app
from backend.database import get_session
from backend.models import ActivityFeed, Base, Game, PaperPick, Team
from backend.tests.auth_helpers import ALL_HEADERS, TEST_PIN
from backend.tests.pricing_helpers import seed_fresh_odds, seed_fresh_prop
from backend.time_utils import et_today


def _client():
    client = TestClient(create_app(":memory:"), headers=ALL_HEADERS)
    Base.metadata.create_all(client.app.state.engine)
    return client


def _games(client, n=1):
    s = get_session(client.app.state.engine)
    ids = []
    for i in range(n):
        h = Team(name=f"H{i}", abbreviation=f"H{i}", sport="nfl")
        a = Team(name=f"A{i}", abbreviation=f"A{i}", sport="nfl")
        s.add_all([h, a])
        s.flush()
        g = Game(sport="nfl", season="2026", date=et_today(), home_team_id=h.id, away_team_id=a.id,
                 status="scheduled")
        s.add(g)
        s.flush()
        ids.append(g.id)
    s.commit()
    s.close()
    for gid in ids:
        seed_fresh_odds(client.app.state.engine, gid)
    return ids


def _user(client, name):
    r = client.post("/users/", json={"name": name, "pin": TEST_PIN})
    assert r.status_code == 200
    return r.json()["id"]


def _events(client, event_type=None):
    s = get_session(client.app.state.engine)
    try:
        q = s.query(ActivityFeed).order_by(ActivityFeed.id)
        if event_type:
            q = q.filter(ActivityFeed.event_type == event_type)
        return [json.loads(e.payload) for e in q]
    finally:
        s.close()


DISPLAY = {"label", "game_label", "start_time", "home_team", "away_team", "odds", "line"}


def test_a_placed_straight_bet_event_is_tail_ready():
    client = _client()
    [gid] = _games(client)
    uid, friend = _user(client, "sam"), _user(client, "jo")
    pid = client.post(f"/users/{uid}/picks", json={"game_id": gid, "pick_type": "spread",
                                                   "side": "AWAY", "stake": 50}).json()["id"]
    [ev] = _events(client, "pick_placed")
    assert (ev["bet_id"], ev["kind"], len(ev["legs"])) == (pid, "straight", 1)
    leg = ev["legs"][0]
    assert {k: leg[k] for k in DISPLAY} == {"label": "A0 +3.5", "game_label": "A0 @ H0", "start_time": None,
                                            "home_team": "H0", "away_team": "A0", "odds": -110, "line": 3.5}
    request = {k: v for k, v in leg.items() if k not in DISPLAY}
    assert request == {"game_id": gid, "pick_type": "spread", "side": "AWAY"}
    # A friend's Tail sends exactly that request.
    r = client.post(f"/users/{friend}/picks", json={**request, "stake": 10})
    assert r.status_code == 200, r.text


def test_a_placed_parlay_event_lists_every_leg():
    client = _client()
    g1, g2 = _games(client, 2)
    uid = _user(client, "sam")
    pl = client.post(f"/users/{uid}/parlay", json={"legs": [
        {"game_id": g1, "pick_type": "moneyline", "side": "HOME"},
        {"game_id": g2, "pick_type": "over_under", "side": "Over"}], "stake": 20}).json()
    [ev] = _events(client, "pick_placed")
    assert (ev["bet_id"], ev["kind"], ev["leg_count"]) == (pl["id"], "parlay", 2)
    assert [leg["label"] for leg in ev["legs"]] == ["H0 ML", "Over 220.5"]


def test_a_placed_prop_event_carries_the_prop_request():
    client = _client()
    [gid] = _games(client)
    seed_fresh_prop(client.app.state.engine, gid)
    uid = _user(client, "sam")
    client.post(f"/users/{uid}/picks", json={"game_id": gid, "pick_type": "prop", "prop_player": "QB One",
                                             "prop_market": "player_pass_yds", "outcome": "Over",
                                             "line": 225.5, "stake": 10})
    [ev] = _events(client, "pick_placed")
    request = {k: v for k, v in ev["legs"][0].items() if k not in DISPLAY}
    assert request == {"game_id": gid, "pick_type": "prop", "prop_player": "QB One",
                       "prop_market": "player_pass_yds", "outcome": "Over", "line": 225.5}
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `.venv/Scripts/python -m pytest backend/tests/test_paper_feed.py -q -p no:warnings`

Expected: all three FAIL with `KeyError: 'bet_id'`.

- [ ] **Step 3: Implement in `backend/api/users.py`**

1. Add `from backend.api.picks import _resolve_pick_value` and `from backend.time_utils import et_today, game_start_utc`, replacing the existing `et_today` import.
2. Add, after `_check_expected`:

```python
#: What a feed leg adds to the bet request for display; everything else in a
#: feed leg is exactly the request a friend's Tail sends back.
_FEED_DISPLAY_KEYS = ("label", "game_label", "start_time", "home_team", "away_team", "odds", "line")


def _feed_leg(leg, quote: pricing.Quote, game) -> dict:
    """A placed leg as the feed carries it (spec §10): the bet request itself,
    so Tail re-places it at today's price, plus what the feed shows."""
    home, away = game.home_team.abbreviation, game.away_team.abbreviation
    start = game_start_utc(game)
    request = leg.model_dump(exclude={"stake", "expected_odds", "expected_line"})
    label = quote.pick_value if quote.pick_type == "prop" else _resolve_pick_value(quote.pick_value, home, away)
    return {**request, "label": label, "game_label": f"{away} @ {home}",
            "start_time": start.isoformat() if start else None,
            "home_team": home, "away_team": away, "odds": quote.odds, "line": quote.line}
```

3. In `place_pick`, in the `_log_feed_event(... "pick_placed", {...})` payload, add after `"stake": bet.stake,`:

```python
            "bet_id": pick.id,
            "kind": "straight",
            "legs": [_feed_leg(bet, quote, game)],
```

4. In `place_parlay`, the pricing loop already holds each `game`. Keep the games: change `quotes.append((leg, quote))` to `quotes.append((leg, quote, game))`, and every later `for leg, quote in quotes` / `for _, q in quotes` / `[q.odds for _, q in quotes]` / `q.pick_value for _, q in quotes` to the three-tuple form (`for leg, quote, _ in quotes`, `for _, q, _ in quotes`, and so on). Then replace the `pick_placed` payload's `"parlay": True, "legs": len(body.legs),` with:

```python
            "parlay": True,
            "leg_count": len(body.legs),
            "bet_id": parlay.id,
            "kind": "parlay",
            "legs": [_feed_leg(leg, q, g) for leg, q, g in quotes],
```

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `.venv/Scripts/python -m pytest backend/tests/test_paper_feed.py backend/tests/test_paper_price_move.py backend/tests/test_paper_bet_odds_guard.py backend/tests/test_paper_bets.py -q -p no:warnings`

Expected: all pass.

- [ ] **Step 5: Mutation-check**

Make each change, confirm a test fails, then restore. Delete `backend/api/__pycache__/users.*.pyc` after every write and every restore.
1. In `_feed_leg`, don't exclude `stake`. The straight test must fail: the request gains a key.
2. Drop `"legs"` from the parlay payload. The parlay test must fail.

- [ ] **Step 6: Commit**

```bash
git checkout feat/sportsbook-phase-5    # created with the plan commit
git add backend/api/users.py backend/tests/test_paper_feed.py
git commit -m "feat(feed): placed-bet events carry bet_id, kind and Tail-ready legs

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: One settlement path that announces each bet once

**Files:**
- Create: `backend/paper/feed.py`
- Modify:
  - `backend/pipeline/paper_settlement.py` (`settle_parlays_list`)
  - `backend/api/users.py`: `_log_feed_event` and `_update_streaks` move to `feed.py`, and `POST /users/grade` calls `settle_and_announce`
  - `backend/pipeline/scheduler.py` (`grade_pending_picks` calls `settle_and_announce`)
- Test: `backend/tests/test_paper_feed.py` (append)

**Interfaces:**
- **Consumes:** `paper_settlement.grade_paper_picks`, the websocket `manager.broadcast`.
- **Produces:**
  - **`feed.py`:**
    - `log_feed_event(session, loop, user_id, event_type, payload) -> None`
    - `update_streaks(session, loop, user_id) -> None`
    - `announce_settlements(session, loop, graded: list[PaperPick], parlays: list[Parlay]) -> int`
    - `settle_and_announce(session, loop=None) -> {"graded": int, "parlays_settled": int, "events": int}`
  - **`paper_settlement.py`:** `settle_parlays_list(session) -> list[Parlay]`, while `settle_parlays` still returns a count.
  - **Events:** `pick_won`, `pick_lost` and `pick_pushed`, with payload `{user_name, message, bet_key, bet_id, kind, result, payout}`.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_paper_feed.py`:

```python
from backend.paper import feed as feed_mod                                   # noqa: E402
from backend.pipeline.paper_settlement import grade_paper_picks             # noqa: E402,F401
from backend.models import Parlay                                            # noqa: E402


def _finish(client, gid, home, away):
    s = get_session(client.app.state.engine)
    g = s.get(Game, gid)
    g.status, g.home_score, g.away_score = "final", home, away
    s.commit()
    s.close()


def _settle(client):
    s = get_session(client.app.state.engine)
    try:
        return feed_mod.settle_and_announce(s)
    finally:
        s.close()


def test_a_settled_straight_bet_is_announced_once_with_its_result():
    client = _client()
    [gid] = _games(client)
    uid = _user(client, "sam")
    pid = client.post(f"/users/{uid}/picks", json={"game_id": gid, "pick_type": "moneyline",
                                                   "side": "HOME", "stake": 110}).json()["id"]
    _finish(client, gid, 24, 17)
    assert _settle(client)["events"] == 1
    [ev] = _events(client, "pick_won")
    assert (ev["bet_key"], ev["bet_id"], ev["kind"], ev["result"], ev["payout"]) == \
        (f"straight-{pid}", pid, "straight", "win", 100.0)
    assert ev["message"] == "sam won H0 ML — +$100.00"
    assert _settle(client)["events"] == 0                    # nothing new, nothing re-announced


def test_a_parlay_is_announced_once_and_its_legs_never():
    client = _client()
    g1, g2 = _games(client, 2)
    uid = _user(client, "sam")
    pl = client.post(f"/users/{uid}/parlay", json={"legs": [
        {"game_id": g1, "pick_type": "moneyline", "side": "HOME"},
        {"game_id": g2, "pick_type": "moneyline", "side": "HOME"}], "stake": 20}).json()
    _finish(client, g1, 24, 17)
    _finish(client, g2, 10, 13)                              # second leg loses
    _settle(client)
    assert _events(client, "pick_won") == []
    [ev] = _events(client, "pick_lost")
    assert (ev["bet_key"], ev["kind"], ev["message"]) == (f"parlay-{pl['id']}", "parlay",
                                                          "sam lost a 2-leg parlay — −$20.00")


def test_a_push_is_announced():
    client = _client()
    [gid] = _games(client)
    uid = _user(client, "sam")
    client.post(f"/users/{uid}/picks", json={"game_id": gid, "pick_type": "moneyline", "side": "HOME", "stake": 10})
    _finish(client, gid, 20, 20)
    _settle(client)
    [ev] = _events(client, "pick_pushed")
    assert ev["message"] == "sam pushed H0 ML — $0.00"


def test_the_same_bet_is_never_announced_twice_review_focus_1():
    """The owner's Grade button and the scheduler can both see a bet graded;
    whichever announces second must find the first announcement."""
    client = _client()
    [gid] = _games(client)
    uid = _user(client, "sam")
    client.post(f"/users/{uid}/picks", json={"game_id": gid, "pick_type": "moneyline", "side": "HOME", "stake": 10})
    _finish(client, gid, 24, 17)
    s = get_session(client.app.state.engine)
    graded = grade_paper_picks(s)
    assert feed_mod.announce_settlements(s, None, graded, []) == 1
    assert feed_mod.announce_settlements(s, None, graded, []) == 0
    s.close()
    assert len(_events(client, "pick_won")) == 1


def test_the_owner_button_and_the_scheduler_announce_the_same_way():
    """Before this, the scheduler's automatic grading wrote no feed events and
    updated no streaks; only POST /users/grade did."""
    from backend.pipeline.scheduler import grade_pending_picks
    client = _client()
    g1, g2 = _games(client, 2)
    uid = _user(client, "sam")
    for gid in (g1, g2):
        client.post(f"/users/{uid}/picks", json={"game_id": gid, "pick_type": "moneyline", "side": "HOME", "stake": 10})
    _finish(client, g1, 24, 17)
    assert client.post("/users/grade").json()["graded"] == 1        # the owner's path
    _finish(client, g2, 30, 3)
    s = get_session(client.app.state.engine)
    grade_pending_picks(s)                                            # the scheduler's path
    s.close()
    assert len(_events(client, "pick_won")) == 2
    s = get_session(client.app.state.engine)
    from backend.models import UserProfile
    u = s.get(UserProfile, uid)
    assert (u.current_streak, u.streak_type) == (2, "win")
    s.close()
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `.venv/Scripts/python -m pytest backend/tests/test_paper_feed.py -q -p no:warnings`

Expected: collection fails with `ImportError: backend.paper.feed`.

- [ ] **Step 3: Add `settle_parlays_list` to `backend/pipeline/paper_settlement.py`**

Rename `def settle_parlays(session) -> int:` to `def settle_parlays_list(session) -> list[Parlay]:`.
- In its body, replace `settled = 0` with `settled: list[Parlay] = []` and `settled += 1` with `settled.append(parlay)`; keep `return settled`.
- Then add below it:

```python
def settle_parlays(session) -> int:
    """How many parlays settled -- the count older callers and tests read."""
    return len(settle_parlays_list(session))
```

- [ ] **Step 4: Write `backend/paper/feed.py`**

```python
"""The league activity feed (sportsbook spec 2026-10-07 §10).

Writing events and broadcasting them, the per-player streaks, and the
settlement announcements. Both grading paths -- the owner's POST /users/grade
and the scheduler's grade_pending_picks -- call settle_and_announce, so a bet
announces itself the same way however it was graded, and at most once.
"""
from __future__ import annotations

import asyncio
import json
import logging

from sqlalchemy import func

from backend.models import ActivityFeed, Game, PaperPick, Parlay, Team, UserProfile
from backend.pipeline import paper_settlement

logger = logging.getLogger(__name__)

#: result -> (event type, verb).
SETTLED = {"win": ("pick_won", "won"), "loss": ("pick_lost", "lost"), "push": ("pick_pushed", "pushed")}


def log_feed_event(session, loop, user_id: int | None, event_type: str, payload: dict) -> None:
    """Save an event, then broadcast it over the WebSocket when an event loop
    is available (the API process; the scheduler has none, and the frontend
    falls back to a 60 s refetch)."""
    session.add(ActivityFeed(user_id=user_id, event_type=event_type, payload=json.dumps(payload)))
    session.commit()
    if loop is None:
        return
    try:
        from backend.api.websocket import manager
        asyncio.run_coroutine_threadsafe(manager.broadcast(event_type, payload), loop)
    except Exception:
        logger.warning("Feed broadcast failed", exc_info=True)


def update_streaks(session, loop, user_id: int) -> None:
    """Recompute a player's streak from their graded picks, newest first."""
    picks = (session.query(PaperPick)
             .filter(PaperPick.user_id == user_id, PaperPick.result.isnot(None))
             .order_by(PaperPick.created_at.desc())
             .all())
    if not picks:
        return
    current = picks[0].result
    if current == "push":
        current = picks[1].result if len(picks) > 1 else "none"
    streak = 0
    for p in picks:
        if p.result == "push":
            continue
        if p.result == current:
            streak += 1
        else:
            break
    user = session.get(UserProfile, user_id)
    if not user:
        return
    user.current_streak = streak
    user.streak_type = "win" if current == "win" else "loss" if current == "loss" else "none"
    if current == "win" and streak > (user.best_streak or 0):
        user.best_streak = streak
    if streak >= 3:
        log_feed_event(session, loop, user_id, "streak", {
            "user_name": user.name,
            "message": f"{user.name} is on a {streak}-pick {'win' if current == 'win' else 'loss'} streak!",
            "streak": streak, "streak_type": user.streak_type,
        })


def _money(x: float) -> str:
    if x > 0:
        return f"+${x:,.2f}"
    if x < 0:
        return f"−${abs(x):,.2f}"
    return "$0.00"


def _announced(session, bet_key: str) -> bool:
    types = [t for t, _ in SETTLED.values()]
    return (session.query(ActivityFeed.id)
            .filter(ActivityFeed.event_type.in_(types),
                    func.json_extract(ActivityFeed.payload, "$.bet_key") == bet_key)
            .first()) is not None


def _straight_label(session, pick: PaperPick) -> str:
    if pick.pick_type == "prop":
        return pick.pick_value
    from backend.api.picks import _resolve_pick_value
    game = session.get(Game, pick.game_id)
    home, away = session.get(Team, game.home_team_id), session.get(Team, game.away_team_id)
    return _resolve_pick_value(pick.pick_value, home.abbreviation, away.abbreviation)


def announce_settlements(session, loop, graded: list[PaperPick], parlays: list[Parlay]) -> int:
    """One won/lost/pushed event per newly settled straight bet and parlay --
    never for a parlay's legs, and never twice for the same bet."""
    written = 0
    names: dict[int, str] = {}

    def name(uid: int) -> str:
        if uid not in names:
            u = session.get(UserProfile, uid)
            names[uid] = u.name if u else "Unknown"
        return names[uid]

    def announce(uid, kind, bet_id, result, payout, what):
        nonlocal written
        if result not in SETTLED:
            return
        key = f"{kind}-{bet_id}"
        if _announced(session, key):
            return
        event_type, verb = SETTLED[result]
        log_feed_event(session, loop, uid, event_type, {
            "user_name": name(uid), "message": f"{name(uid)} {verb} {what} — {_money(payout or 0.0)}",
            "bet_key": key, "bet_id": bet_id, "kind": kind, "result": result, "payout": payout,
        })
        written += 1

    for pick in graded:
        if pick.parlay_id is None:
            announce(pick.user_id, "straight", pick.id, pick.result, pick.payout, _straight_label(session, pick))
    for parlay in parlays:
        legs = session.query(PaperPick).filter(PaperPick.parlay_id == parlay.id).count()
        announce(parlay.user_id, "parlay", parlay.id, parlay.result, parlay.payout, f"a {legs}-leg parlay")
    return written


def settle_and_announce(session, loop=None) -> dict:
    """Grade every pending paper bet, settle parlays, announce each newly
    settled bet once and refresh streaks. The one path both graders use."""
    graded = paper_settlement.grade_paper_picks(session)
    parlays = paper_settlement.settle_parlays_list(session)
    events = announce_settlements(session, loop, graded, parlays)
    for uid in {p.user_id for p in graded} | {p.user_id for p in parlays}:
        update_streaks(session, loop, uid)
    session.commit()
    return {"graded": len(graded), "parlays_settled": len(parlays), "events": events}
```

- [ ] **Step 5: Point both graders at it**

In `backend/api/users.py`:
1. Delete `_log_feed_event` and `_update_streaks`.
2. Add `from backend.paper.feed import log_feed_event as _log_feed_event, settle_and_announce`. The placed-bet calls keep the name `_log_feed_event`.
3. Replace the body of `grade_paper_picks` (the `POST /users/grade` handler) with:

```python
    """Grade all pending paper picks for games that are final -- the same
    path the scheduler's automatic grading runs (backend/paper/feed.py)."""
    session = get_session(request.app.state.engine)
    try:
        return settle_and_announce(session, request.app.state.loop)
    finally:
        session.close()
```

4. Remove any now-unused imports (`settle_parlays`, `paper_settlement`, `asyncio`, `ActivityFeed`), but **only** the ones a grep shows unused.

In `backend/pipeline/scheduler.py` `grade_pending_picks`, replace:

```python
    # Also grade pending PaperPicks -- the same code /users/grade runs.
    from backend.pipeline import paper_settlement
    paper_graded = len(paper_settlement.grade_paper_picks(session))
    logger.info("Auto-graded %d paper picks", paper_graded)

    parlays = paper_settlement.settle_parlays(session)
    logger.info("Settled %d paper parlays", parlays)
```

with:

```python
    # Paper bets: the same path the owner's /users/grade runs, so automatic
    # grading announces results and updates streaks too (spec §10).
    from backend.paper.feed import settle_and_announce
    paper = settle_and_announce(session)
    paper_graded, parlays = paper["graded"], paper["parlays_settled"]
    logger.info("Auto-graded %d paper picks, settled %d parlays, %d feed events",
                paper_graded, parlays, paper["events"])
```

- [ ] **Step 6: Run the tests and confirm they pass**

Run: `.venv/Scripts/python -m pytest backend/tests/test_paper_feed.py backend/tests/test_paper_settlement.py backend/tests/test_paper_void.py backend/tests/test_paper_bet_odds_guard.py backend/tests/test_paper_bets.py -q -p no:warnings`

Then run `grep -rn "_update_streaks\|grade_pending_picks" backend/tests | head` and run every test file it names.

Expected: all pass.

- [ ] **Step 7: Mutation-check**

Make each change, confirm a test fails, then restore. Delete the `.pyc` after every write and every restore.
1. Make `_announced` always return False. The never-twice test must fail.
2. In `announce_settlements`, drop the `if pick.parlay_id is None` check. The parlay test must fail.
3. Revert the scheduler to the old paper-grading lines. The both-paths test must fail.

- [ ] **Step 8: Commit**

```bash
git add backend/paper/feed.py backend/pipeline/paper_settlement.py backend/api/users.py backend/pipeline/scheduler.py backend/tests/test_paper_feed.py
git commit -m "feat(feed): one settlement path for both graders — each bet announced once, streaks kept

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Feed types, `useFeed` and live WebSocket refresh

**Files:**
- Modify: `frontend/src/types.ts`, `frontend/src/api/client.ts` (the `feed` signature), `frontend/src/hooks/useWebSocket.ts`
- Create: `frontend/src/hooks/useFeed.ts`
- Test: `frontend/src/hooks/useWebSocket.test.ts` (new)

**Interfaces:**
- **Produces:**
  - **Types:**
    - `FeedLeg = BetLeg & { label: string; game_label: string; start_time: string | null; home_team: string; away_team: string; odds: number; line: number | null }`
    - `FeedPayload`
    - `FeedItem { id; user_id: number | null; event_type; payload: FeedPayload; created_at }`
  - **API:** `api.users.feed(limit?): Promise<FeedItem[]>`
  - **Hook:** `useFeed(limit = 50)`, with key `['users','feed',limit]`, a 60 s refetch, and invalidation on any WebSocket feed message.

- [ ] **Step 1: Add the types and the hook**

Append to `frontend/src/types.ts`:

```ts
/** A placed bet's leg as the feed carries it: the bet request plus display. */
export type FeedLeg = BetLeg & {
  label: string; game_label: string; start_time: string | null;
  home_team: string; away_team: string; odds: number; line: number | null;
}
/** Older events carry only user_name/message; every other field is optional. */
export interface FeedPayload {
  user_name?: string; message?: string; bet_id?: number; kind?: 'straight' | 'parlay';
  legs?: FeedLeg[]; result?: string; payout?: number; stake?: number;
}
export interface FeedItem {
  id: number; user_id: number | null; event_type: string; payload: FeedPayload; created_at: string;
}
```

In `frontend/src/api/client.ts`, change the `feed` signature to `feed: (limit?: number) => get<FeedItem[]>(`/users/feed${limit ? `?limit=${limit}` : ''}`),` and add `FeedItem` to the `../types` import.

Create `frontend/src/hooks/useFeed.ts`:

```ts
import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'

/** The league feed. The WebSocket invalidates ['users','feed'] on every feed
 *  message, so this refreshes live; the 60 s refetch covers events the
 *  scheduler writes (it has no socket) and a dropped connection. */
export function useFeed(limit = 50) {
  return useQuery({
    queryKey: ['users', 'feed', limit],
    queryFn: () => api.users.feed(limit),
    refetchInterval: 60_000,
    refetchOnWindowFocus: true,
  })
}
```

- [ ] **Step 2: Write the failing WebSocket test, `frontend/src/hooks/useWebSocket.test.ts`**

```ts
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { createElement, type ReactNode } from 'react'
import { useWebSocket } from './useWebSocket'

class FakeSocket {
  static last: FakeSocket
  readyState = 1
  onopen: (() => void) | null = null
  onmessage: ((e: { data: string }) => void) | null = null
  onclose: (() => void) | null = null
  onerror: (() => void) | null = null
  constructor() { FakeSocket.last = this }
  send() {}
  close() {}
}

beforeEach(() => { vi.stubGlobal('WebSocket', FakeSocket as unknown as typeof WebSocket) })
afterEach(() => vi.unstubAllGlobals())

describe('useWebSocket', () => {
  it('refreshes the feed and the money queries on a feed message (settled bets included)', () => {
    const client = new QueryClient()
    const spy = vi.spyOn(client, 'invalidateQueries')
    const wrapper = ({ children }: { children: ReactNode }) => createElement(QueryClientProvider, { client }, children)
    renderHook(() => useWebSocket(), { wrapper })
    FakeSocket.last.onmessage?.({ data: JSON.stringify({ type: 'pick_pushed', data: { message: 'x' } }) })
    expect(spy).toHaveBeenCalledWith({ queryKey: ['users'] })
  })
})
```

Run: `cd frontend && npx vitest run src/hooks/useWebSocket.test.ts`

Expected: FAIL, because `invalidateQueries` is not called.

- [ ] **Step 3: Implement in `frontend/src/hooks/useWebSocket.ts`**

1. Add `import { useQueryClient } from '@tanstack/react-query'`.
2. Add `const queryClient = useQueryClient()` inside the hook.
3. In `onmessage`, change the type list to `['pick_placed', 'pick_won', 'pick_lost', 'pick_pushed', 'streak', 'feed_event']`. Inside that `if`, after `addEvent(feedEvent)`, add:

```ts
          // A feed event can mean money moved (a settlement) as well as a new
          // feed line: refresh the feed, balances, My Bets and the board.
          queryClient.invalidateQueries({ queryKey: ['users'] })
```

4. Add `queryClient` to the dependency array of the `useCallback` that defines `connect`, if eslint asks.

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `cd frontend && npx vitest run src/hooks/useWebSocket.test.ts src/components/Layout.test.tsx && npx tsc -p tsconfig.app.json --noEmit && npx eslint .`

Expected: all pass. Layout's test mocks `useWebSocket`, so it's unaffected.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/types.ts frontend/src/api/client.ts frontend/src/hooks/useFeed.ts frontend/src/hooks/useWebSocket.ts frontend/src/hooks/useWebSocket.test.ts
git commit -m "feat(feed): feed types and hook; WebSocket feed messages refresh feed and money

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Tail — re-price a feed bet's legs at today's quote

**Files:**
- Create: `frontend/src/lib/tail.ts`
- Test: `frontend/src/lib/tail.test.ts`

**Interfaces:**
- **Consumes:** `FeedLeg`, `findQuote` and `resolveQuoteLabel` (`lib/quotes.ts`), `api.paper.quotes` / `propQuotes`, and `useSlip` (`add`, `setError`, `setOpen`).
- **Produces:**
  - `tailLegs(legs, fetchers?) -> Promise<{ selection: SlipSelection; error: string | null }[]>`
  - `tailBet(legs) -> Promise<number>`: puts them on the slip, opens it, and returns how many legs are live.

- [ ] **Step 1: Write the failing tests, `frontend/src/lib/tail.test.ts`**

```ts
import { describe, it, expect, beforeEach } from 'vitest'
import { tailLegs, tailBet } from './tail'
import { useSlip, SLIP_DEFAULTS } from '../stores/slipStore'
import type { FeedLeg, GameQuote, PropQuote } from '../types'

const leg = (over: Partial<FeedLeg> = {}): FeedLeg => ({
  game_id: 7, pick_type: 'spread', side: 'AWAY', label: 'TB +9', game_label: 'TB @ DAL',
  start_time: '2099-01-01T00:00:00+00:00', home_team: 'DAL', away_team: 'TB', odds: -109, line: 9, ...over,
} as FeedLeg)
const quote = (over: Partial<GameQuote> = {}): GameQuote => ({
  pick_type: 'spread', side: 'AWAY', available: true, pick_value: 'AWAY +8.5', odds: -115, line: 8.5,
  quoted_at: 'x', prop_player: null, prop_market: null, ...over,
} as GameQuote)
const fetchers = (game: GameQuote[], props: PropQuote[] = []) => ({
  quotes: async () => ({ game_id: 7, quotes: game }),
  propQuotes: async () => ({ game_id: 7, quotes: props }),
})

beforeEach(() => useSlip.setState({ ...SLIP_DEFAULTS }))

describe('tailLegs', () => {
  it("re-prices at today's quote, not the friend's price (Review Focus 3)", async () => {
    const [r] = await tailLegs([leg()], fetchers([quote()]))
    expect(r.error).toBeNull()
    expect(r.selection).toMatchObject({ leg: { game_id: 7, pick_type: 'spread', side: 'AWAY' },
      label: 'TB +8.5', odds: -115, line: 8.5, gameLabel: 'TB @ DAL', homeTeam: 'DAL', awayTeam: 'TB' })
  })
  it('locks a leg the server now refuses, with its message', async () => {
    const refused = { pick_type: 'spread', side: 'AWAY', available: false, reason: 'game_started',
      message: 'Betting has closed: this game has already started' } as GameQuote
    const [r] = await tailLegs([leg()], fetchers([refused]))
    expect(r.error).toBe('Betting has closed: this game has already started')
    expect(r.selection.odds).toBe(-109)                 // shown for reference only; the server re-prices
  })
  it('locks a leg no book quotes any more', async () => {
    const [r] = await tailLegs([leg()], fetchers([]))
    expect(r.error).toBe('No book is quoting this bet right now.')
  })
})

describe('tailBet', () => {
  it('puts the legs on your slip, swapping a market already there (Review Focus 4)', async () => {
    useSlip.getState().add({ leg: { game_id: 7, pick_type: 'spread', side: 'HOME' }, label: 'DAL -9',
      gameLabel: 'TB @ DAL', startTime: null, odds: -110, line: -9, homeTeam: 'DAL', awayTeam: 'TB' })
    const live = await tailBet([leg()], fetchers([quote()]))
    expect(live).toBe(1)
    expect(useSlip.getState().legs.map(l => l.label)).toEqual(['TB +8.5'])
    expect(useSlip.getState().open).toBe(true)
  })
})
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `cd frontend && npx vitest run src/lib/tail.test.ts`

Expected: FAIL, "Failed to resolve import './tail'".

- [ ] **Step 3: Write `frontend/src/lib/tail.ts`**

```ts
import { api } from '../api/client'
import { useSlip } from '../stores/slipStore'
import { findQuote, resolveQuoteLabel } from './quotes'
import type { FeedLeg, GameQuote, PropQuote, SlipSelection } from '../types'

type Fetchers = {
  quotes: (gameId: number) => Promise<{ game_id: number; quotes: GameQuote[] }>
  propQuotes: (gameId: number) => Promise<{ game_id: number; quotes: PropQuote[] }>
}
const LIVE: Fetchers = { quotes: api.paper.quotes, propQuotes: api.paper.propQuotes }

/** A friend's bet re-priced for you now (spec §10): today's quote for each
 *  leg, never the price they got. A leg the server refuses keeps its
 *  refusal as `error` -- it shows locked on the slip. */
export async function tailLegs(legs: FeedLeg[], fetchers: Fetchers = LIVE) {
  return Promise.all(legs.map(async fl => {
    const { label, game_label, start_time, home_team, away_team, odds, line, ...leg } = fl
    const isProp = leg.pick_type === 'prop'
    const quotes = isProp
      ? (await fetchers.propQuotes(leg.game_id)).quotes
      : (await fetchers.quotes(leg.game_id)).quotes
    const q = findQuote(isProp ? [] : (quotes as GameQuote[]), isProp ? (quotes as PropQuote[]) : [], leg)
    const base = { leg, gameLabel: game_label, startTime: start_time, homeTeam: home_team, awayTeam: away_team }
    if (q && q.available) {
      const shown = !isProp && q.pick_type === 'moneyline'
        ? `${('side' in q && q.side === 'HOME') ? home_team : away_team} ML`
        : isProp ? label : resolveQuoteLabel(q, home_team, away_team)
      const selection: SlipSelection = { ...base, label: shown, odds: q.odds, line: q.line }
      return { selection, error: null as string | null }
    }
    const selection: SlipSelection = { ...base, label, odds, line }
    return { selection, error: q && !q.available ? q.message : 'No book is quoting this bet right now.' }
  }))
}

/** Tail a feed bet onto your slip and open it. Returns how many legs are live. */
export async function tailBet(legs: FeedLeg[], fetchers: Fetchers = LIVE): Promise<number> {
  const results = await tailLegs(legs, fetchers)
  const slip = useSlip.getState()
  for (const r of results) {
    slip.add(r.selection)
    if (r.error) useSlip.getState().setError(r.selection.leg, r.error)
  }
  useSlip.getState().setOpen(true)
  return results.filter(r => r.error === null).length
}
```

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `cd frontend && npx vitest run src/lib/tail.test.ts && npx tsc -p tsconfig.app.json --noEmit && npx eslint .`

Expected: all pass. If eslint flags the unused destructured names (`odds` and `line` in the rest pattern), keep them: they are used in the refused branch.

- [ ] **Step 5: Mutation-check**

Make each change, confirm a test fails, then restore:
1. Use `odds` (the friend's price) in the available branch. The re-price test must fail.
2. Drop the `setError` call. Add an assertion run with a refused leg: `tailBet` then `useSlip.getState().legs[0].error`. This is covered by extending the locked test, if the mutation survives, with:

```ts
    await tailBet([leg()], fetchers([refused]))
    expect(useSlip.getState().legs[0].error).toBe('Betting has closed: this game has already started')
```

- [ ] **Step 6: Commit**

```bash
git add frontend/src/lib/tail.ts frontend/src/lib/tail.test.ts
git commit -m "feat(feed): Tail — a friend's bet on your slip at today's price, refused legs locked

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: The Leaders page and the feed list

**Files:**
- Create: `frontend/src/components/FeedList.tsx`, `frontend/src/pages/Leaders.tsx`
- Modify: `frontend/src/sportsbook.css` (append)
- Test: `frontend/src/pages/Leaders.test.tsx`

**Interfaces:**
- **Consumes:** `useRankings` (`/users/leaderboard`), the users list (streaks), `useFeed`, `useCurrentPlayer`, `tailBet`, `signedMoney`.
- **Produces:**
  - `FeedList({ items, meId, limit? })`: messages, a time-ago, and a Tail button on placed bets with legs that aren't yours.
  - `Leaders` (default export, routed in Task 6).

- [ ] **Step 1: Write the failing tests, `frontend/src/pages/Leaders.test.tsx`**

```tsx
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import Leaders from './Leaders'
import { api } from '../api/client'
import { useUserStore } from '../stores/userStore'
import { useSlip, SLIP_DEFAULTS } from '../stores/slipStore'
import type { FeedItem, LeaderboardRow, UserProfile } from '../types'

vi.mock('../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/client')>()
  return { ...actual, api: { ...actual.api,
    users: { ...actual.api.users, list: vi.fn(), leaderboard: vi.fn(), feed: vi.fn() },
    paper: { ...actual.api.paper, quotes: vi.fn(), propQuotes: vi.fn() } } }
})

const row = (over: Partial<LeaderboardRow>): LeaderboardRow => ({ id: 1, name: 'x', is_model: false, wins: 0,
  losses: 0, pushes: 0, pending: 0, n: 0, n_eff: 0, win_rate: null, roi: null, shrunk_roi: null, profit: 0,
  ranked: false, ...over })
const user = (id: number, name: string, streak = 0, type = 'none') =>
  ({ id, name, available_balance: 1000, current_streak: streak, streak_type: type }) as UserProfile
const placed: FeedItem = { id: 9, user_id: 2, event_type: 'pick_placed', created_at: new Date().toISOString(),
  payload: { user_name: 'Sam', message: 'Sam bet AWAY +9 -109 — $100', bet_id: 6, kind: 'straight',
    legs: [{ game_id: 7, pick_type: 'spread', side: 'AWAY', label: 'TB +9', game_label: 'TB @ DAL',
      start_time: '2099-01-01T00:00:00+00:00', home_team: 'DAL', away_team: 'TB', odds: -109, line: 9 }] } }
const old: FeedItem = { id: 3, user_id: 2, event_type: 'pick_placed', created_at: new Date().toISOString(),
  payload: { user_name: 'Sam', message: 'Sam bet HOME ML -110 — $50' } }

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={client}><MemoryRouter><Leaders /></MemoryRouter></QueryClientProvider>)
}

beforeEach(() => {
  vi.clearAllMocks()
  useSlip.setState({ ...SLIP_DEFAULTS })
  useUserStore.setState({ currentUserName: 'Me' })
  vi.mocked(api.users.list).mockResolvedValue([user(1, 'Me'), user(2, 'Sam', 4, 'win')])
  vi.mocked(api.users.leaderboard).mockResolvedValue([
    row({ id: 2, name: 'Sam', ranked: true, n: 12, wins: 8, losses: 4, profit: 240.5, roi: 0.12 }),
    row({ id: null, name: 'Model', is_model: true, ranked: true, n: 30, wins: 16, losses: 14, profit: 1.2, roi: 0.04 }),
    ...Array.from({ length: 9 }, (_, i) => row({ id: 100 + i, name: `P${i}`, n: 2 })),
    row({ id: 1, name: 'Me', n: 3, wins: 2, losses: 1, profit: 50 }),
  ])
  vi.mocked(api.users.feed).mockResolvedValue([placed, old])
})

describe('Leaders', () => {
  it('ranks the board with streaks, the Model and unranked players (Review Focus 5)', async () => {
    renderPage()
    const sam = await screen.findByRole('row', { name: /Sam/ })
    expect(sam).toHaveTextContent('1')
    expect(sam).toHaveTextContent('+$240.50')
    expect(sam).toHaveTextContent('12.0%')
    expect(sam).toHaveTextContent('8-4')
    expect(sam).toHaveTextContent('🔥 4')
    expect(screen.getAllByRole('row', { name: /Model/ })[0]).not.toHaveTextContent('🔥')
    expect(screen.getAllByRole('row', { name: /Me/ })[0]).toHaveTextContent('Unranked (3/10)')
  })
  it("pins your own row when it is off the top of the board", async () => {
    renderPage()
    expect(await screen.findByLabelText('Your position')).toHaveTextContent('Me')
  })
  it('shows the feed, with Tail only on placed bets that carry legs (Review Focus 2)', async () => {
    renderPage()
    expect(await screen.findByText('Sam bet HOME ML -110 — $50')).toBeInTheDocument()
    expect(screen.getAllByRole('button', { name: /Tail/ })).toHaveLength(1)
  })
  it('tails a bet onto your slip at the current price', async () => {
    vi.mocked(api.paper.quotes).mockResolvedValue({ game_id: 7, quotes: [{ pick_type: 'spread', side: 'AWAY',
      available: true, pick_value: 'AWAY +8.5', odds: -115, line: 8.5, quoted_at: 'x', prop_player: null,
      prop_market: null }] })
    renderPage()
    fireEvent.click(await screen.findByRole('button', { name: /Tail/ }))
    await waitFor(() => expect(useSlip.getState().legs.map(l => [l.label, l.odds])).toEqual([['TB +8.5', -115]]))
  })
})
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `cd frontend && npx vitest run src/pages/Leaders.test.tsx`

Expected: FAIL, "Failed to resolve import './Leaders'".

- [ ] **Step 3: Write `frontend/src/components/FeedList.tsx`**

```tsx
import { useState } from 'react'
import { tailBet } from '../lib/tail'
import type { FeedItem } from '../types'

function ago(iso: string, now = Date.now()): string {
  const m = Math.max(0, Math.floor((now - new Date(iso).getTime()) / 60000))
  if (m < 1) return 'now'
  if (m < 60) return `${m}m`
  const h = Math.floor(m / 60)
  return h < 24 ? `${h}h` : `${Math.floor(h / 24)}d`
}

const DOT: Record<string, string> = { pick_won: 'won', pick_lost: 'lost', pick_pushed: 'push' }

export default function FeedList({ items, meId, limit }: { items: FeedItem[]; meId?: number; limit?: number }) {
  const [tailing, setTailing] = useState<number | null>(null)
  const shown = limit ? items.slice(0, limit) : items
  if (shown.length === 0) return <p className="sb-empty">No activity yet — place a bet to start the feed.</p>
  return (
    <ul className="sb-feed">
      {shown.map(e => {
        const legs = e.event_type === 'pick_placed' && Array.isArray(e.payload?.legs) ? e.payload.legs : []
        const canTail = legs.length > 0 && e.user_id !== meId
        return (
          <li key={e.id} className="sb-feed-item">
            <span className={`sb-dot sb-dot-${DOT[e.event_type] ?? 'pending'}`} />
            <span className="sb-feed-msg">{e.payload?.message ?? ''}</span>
            <span className="sb-feed-time">{ago(e.created_at)}</span>
            {canTail && (
              <button type="button" className="sb-tail" disabled={tailing === e.id}
                onClick={async () => { setTailing(e.id); try { await tailBet(legs) } finally { setTailing(null) } }}>
                {tailing === e.id ? 'Tailing…' : 'Tail'}
              </button>
            )}
          </li>
        )
      })}
    </ul>
  )
}
```

- [ ] **Step 4: Write `frontend/src/pages/Leaders.tsx`**

```tsx
import FeedList from '../components/FeedList'
import { useCurrentPlayer } from '../hooks/useCurrentPlayer'
import { useFeed } from '../hooks/useFeed'
import { useRankings } from '../hooks/useRankings'
import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'
import { signedMoney } from '../lib/bets'
import type { LeaderboardRow } from '../types'

const MIN_RANKED = 10            // backend MIN_RANKED_BETS
const TOP = 10                   // rows shown before "your position" is pinned

function Row({ r, rank, streak }: { r: LeaderboardRow; rank: number | null; streak: number }) {
  return (
    <tr aria-label={r.name} className={r.is_model ? 'sb-lead-model' : undefined}>
      <td className="sb-lead-rank">{rank ?? '—'}</td>
      <td><span className="sb-avatar" aria-hidden="true">{r.name.charAt(0).toUpperCase()}</span>{r.name}</td>
      <td className={r.profit > 0 ? 'sb-up' : r.profit < 0 ? 'sb-down' : undefined}>{signedMoney(r.profit)}</td>
      <td>{r.roi === null ? '—' : `${(r.roi * 100).toFixed(1)}%`}</td>
      <td>{r.wins}-{r.losses}{r.pushes ? `-${r.pushes}` : ''}</td>
      <td>{r.ranked ? (streak >= 2 ? `🔥 ${streak}` : '') : `Unranked (${r.n}/${MIN_RANKED})`}</td>
    </tr>
  )
}

export default function Leaders() {
  const board = useRankings()
  const users = useQuery({ queryKey: ['users', 'list'], queryFn: api.users.list, refetchInterval: 60_000 })
  const feed = useFeed(50)
  const me = useCurrentPlayer()
  const rows = board.data ?? []
  const streakOf = (id: number | null) => {
    const u = id === null ? undefined : users.data?.find(x => x.id === id)
    return u && u.streak_type === 'win' ? u.current_streak : 0
  }
  let ranked = 0
  const ranks = rows.map(r => (r.ranked ? ++ranked : null))
  const myIndex = me ? rows.findIndex(r => r.id === me.id) : -1

  return (
    <div className="sb-leaders">
      <h1 className="sb-page-title">Leaders</h1>
      <table className="sb-lead-table">
        <thead><tr><th>#</th><th>Player</th><th>Profit</th><th>ROI</th><th>W-L</th><th /></tr></thead>
        <tbody>
          {rows.slice(0, TOP).map((r, i) => <Row key={`${r.id}-${r.name}`} r={r} rank={ranks[i]} streak={streakOf(r.id)} />)}
        </tbody>
      </table>
      {myIndex >= TOP && (
        <table className="sb-lead-table sb-lead-pinned" aria-label="Your position">
          <tbody><Row r={rows[myIndex]} rank={ranks[myIndex]} streak={streakOf(rows[myIndex].id)} /></tbody>
        </table>
      )}
      <h2 className="sb-day">League feed</h2>
      <FeedList items={feed.data ?? []} meId={me?.id} />
    </div>
  )
}
```

- [ ] **Step 5: Append the styles to `frontend/src/sportsbook.css`**

```css
/* ─── Leaders and feed (spec §10) ───────────────────────────── */
.sb-leaders { max-width: 760px; margin: 0 auto; padding: 0.75rem; }
.sb-page-title { font-size: 1.15rem; font-weight: 800; margin: 0.25rem 0 0.75rem; }
.sb-lead-table { width: 100%; border-collapse: collapse; font-variant-numeric: tabular-nums; font-size: 0.85rem; }
.sb-lead-table th { text-align: left; color: var(--text-muted); font-size: 0.7rem; text-transform: uppercase;
  letter-spacing: 0.05em; padding: 0.3rem 0.4rem; }
.sb-lead-table td { padding: 0.5rem 0.4rem; border-top: 1px solid var(--border-subtle); white-space: nowrap; }
.sb-lead-rank { font-weight: 800; color: var(--text-muted); width: 2rem; }
.sb-lead-model td { color: var(--text-secondary); font-style: italic; }
.sb-lead-pinned { position: sticky; bottom: 76px; background: var(--bg-active); border-radius: var(--radius-md); }
.sb-avatar { display: inline-grid; place-items: center; width: 1.6rem; height: 1.6rem; margin-right: 0.45rem;
  border-radius: 50%; background: var(--bg-elevated); border: 1px solid var(--border-strong); font-weight: 800; font-size: 0.75rem; }
.sb-feed { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; }
.sb-feed-item { display: flex; align-items: center; gap: 0.55rem; padding: 0.55rem 0.2rem; border-top: 1px solid var(--border-subtle); }
.sb-feed-msg { flex: 1; min-width: 0; font-size: 0.85rem; }
.sb-feed-time { color: var(--text-muted); font-size: 0.75rem; }
.sb-tail { min-height: 30px; padding: 0.2rem 0.7rem; border-radius: 999px; border: 1px solid var(--accent);
  background: none; color: var(--accent); font: inherit; font-size: 0.75rem; font-weight: 800; cursor: pointer; }
.sb-tail:disabled { opacity: 0.5; cursor: wait; }
@media (min-width: 900px) { .sb-lead-pinned { bottom: 0.5rem; } }
```

- [ ] **Step 6: Run the tests and confirm they pass**

Run: `cd frontend && npx vitest run src/pages/Leaders.test.tsx && npx tsc -p tsconfig.app.json --noEmit && npx eslint .`

Expected: all pass.

- [ ] **Step 7: Mutation-check**

Make each change, confirm a test fails, then restore:
1. In `FeedList`, set `canTail = true`. The Tail-count test must fail.
2. In `Leaders`, always render the pinned table. Add `expect(screen.queryByLabelText('Your position')).toBeNull()` to a new tiny test where Me ranks first (leaderboard mock with Me at index 0). If the mutation survives the existing tests, keep that added test.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/components/FeedList.tsx frontend/src/pages/Leaders.tsx frontend/src/pages/Leaders.test.tsx frontend/src/sportsbook.css
git commit -m "feat(feed): Leaders page — ranked board with streaks, pinned position, league feed with Tail

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Wire it in — Leaders tab, Lobby ticker, `/paper-trading` redirect, retire PaperTrading

**Files:**
- Create: `frontend/src/components/FeedTicker.tsx`
- Modify: `frontend/src/components/MainTabs.tsx` (Leaders tab), `frontend/src/lib/nav.ts` (drop the Leaderboard link), `frontend/src/App.tsx` (the `/leaders` route and the `/paper-trading` redirect), `frontend/src/pages/Lobby.tsx` (ticker), `frontend/src/sportsbook.css`
- Delete: `frontend/src/pages/PaperTrading.tsx`, `frontend/src/pages/PaperTrading.test.tsx`, plus any of `LeaderboardBar.tsx` (and its test), `hooks/useLeaderboard.ts`, `hooks/usePaperTrading.ts` and `stores/feedStore.ts` that Step 1's grep shows only PaperTrading used.
- Test: `frontend/src/components/Layout.test.tsx`, `frontend/src/pages/Lobby.test.tsx`

**Interfaces:**
- **Produces:**
  - **Routes:** `/leaders` is Leaders; `/paper-trading` redirects to `/bets`.
  - **Tabs:** Lobby, My Bets, Leaders, Research.
  - `FeedTicker`: the latest feed line, linking to `/leaders`.

- [ ] **Step 1: Check who uses PaperTrading's helpers**

Run: `cd frontend && grep -rln "LeaderboardBar\|useLeaderboard\b\|usePaperTrading\|useFeedStore\|feedStore" src`

Expected:
- PaperTrading's files, plus `useWebSocket.ts` for `feedStore`, which keeps `feedStore`.
- Anything else that uses a helper keeps it. Record a ruling for each one kept.

- [ ] **Step 2: Write the failing tests**

In `frontend/src/components/Layout.test.tsx`:
1. Replace `'lists the Leaderboard under Research until Phase 5 builds /leaders'` with:

```tsx
  it('has a Leaders tab, and Research no longer lists the old leaderboard', () => {
    renderAt('/')
    expect(screen.getAllByRole('link', { name: /Leaders/ })[0]).toHaveAttribute('href', '/leaders')
    fireEvent.click(screen.getAllByRole('button', { name: /Research/ })[0])
    expect(screen.queryByRole('link', { name: 'Leaderboard' })).not.toBeInTheDocument()
  })
```

2. Add a test that the redirect exists in the route tree, by rendering it directly:

```tsx
  it('sends the old /paper-trading link to My Bets', async () => {
    const { Navigate } = await import('react-router-dom')
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(<QueryClientProvider client={client}><MemoryRouter initialEntries={['/paper-trading']}>
      <Routes><Route path="/paper-trading" element={<Navigate to="/bets" replace />} />
        <Route path="/bets" element={<p>bets page</p>} /></Routes></MemoryRouter></QueryClientProvider>)
    expect(screen.getByText('bets page')).toBeInTheDocument()
  })
```

This pins the redirect's form. Step 4 adds the identical element to `App.tsx`, and Task 7's visual check confirms it there.

In `frontend/src/pages/Lobby.test.tsx`:
1. Add `feed: vi.fn()` to the mocked `users` object.
2. In `beforeEach`, add `vi.mocked(api.users.feed).mockResolvedValue([])`.
3. Add:

```tsx
  it('runs a one-line ticker of the latest league activity', async () => {
    vi.mocked(api.users.feed).mockResolvedValue([{ id: 1, user_id: 2, event_type: 'pick_won',
      created_at: new Date().toISOString(), payload: { message: 'Sam won TB +9 — +$91.74' } }])
    vi.mocked(api.paper.board).mockResolvedValue({ games: [] })
    renderLobby()
    expect(await screen.findByRole('link', { name: /Sam won TB \+9/ })).toHaveAttribute('href', '/leaders')
  })
```

Run: `cd frontend && npx vitest run src/components/Layout.test.tsx src/pages/Lobby.test.tsx`

Expected: the Leaders-tab and ticker tests FAIL. The redirect-form test passes on its own.

- [ ] **Step 3: Write `frontend/src/components/FeedTicker.tsx`**

```tsx
import { Link } from 'react-router-dom'
import { useFeed } from '../hooks/useFeed'

/** The Lobby's one-line ticker: the latest league activity, to /leaders. */
export default function FeedTicker() {
  const feed = useFeed(5)
  const latest = (feed.data ?? []).find(e => typeof e.payload?.message === 'string' && e.payload.message)
  if (!latest) return null
  return (
    <Link to="/leaders" className="sb-ticker">
      <span className="sb-ticker-tag">LEAGUE</span>
      <span className="sb-ticker-msg">{latest.payload.message}</span>
    </Link>
  )
}
```

Append this to `frontend/src/sportsbook.css`:

```css
.sb-ticker { display: flex; align-items: center; gap: 0.5rem; margin-bottom: 0.6rem; padding: 0.45rem 0.7rem;
  border-radius: var(--radius-md); background: var(--bg-surface); border: 1px solid var(--border-default);
  color: var(--text-secondary); text-decoration: none; font-size: 0.8rem; min-height: 0; }
.sb-ticker-tag { color: var(--accent); font-weight: 800; font-size: 0.65rem; letter-spacing: 0.06em; }
.sb-ticker-msg { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
```

- [ ] **Step 4: Wire the routes, tabs and Lobby**

1. **`MainTabs.tsx`:** add `{ path: '/leaders', label: 'Leaders', icon: '🏆' },` after My Bets in `TABS`.
2. **`lib/nav.ts`:** delete the comment line and `['/paper-trading', 'Leaderboard'],`.
3. **`App.tsx`:**
   1. Add `Navigate` to the `react-router-dom` import and `import Leaders from './pages/Leaders';`.
   2. Remove the `PaperTrading` import.
   3. Replace `<Route path="paper-trading" element={<PaperTrading />} />` with:

```tsx
                <Route path="leaders" element={<Leaders />} />
                <Route path="paper-trading" element={<Navigate to="/bets" replace />} />
```

4. **`Lobby.tsx`:** add `import FeedTicker from '../components/FeedTicker'`, and render `<FeedTicker />` as the first child of `<div className="sb-lobby">`.

- [ ] **Step 5: Delete PaperTrading and its now-unused helpers**

```bash
git rm frontend/src/pages/PaperTrading.tsx frontend/src/pages/PaperTrading.test.tsx
```

Then `git rm` each file Step 1 showed only PaperTrading used. That is likely `components/LeaderboardBar.tsx` and its test, `hooks/useLeaderboard.ts` and `hooks/usePaperTrading.ts`. Keep `stores/feedStore.ts`, which `useWebSocket` uses.

- [ ] **Step 6: Run the whole frontend suite**

Run: `cd frontend && npm test && npx tsc -p tsconfig.app.json --noEmit && npx eslint .`

Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/components/FeedTicker.tsx frontend/src/components/MainTabs.tsx frontend/src/lib/nav.ts frontend/src/App.tsx frontend/src/pages/Lobby.tsx frontend/src/pages/Lobby.test.tsx frontend/src/components/Layout.test.tsx frontend/src/sportsbook.css
git commit -m "feat(nav): Leaders tab, Lobby ticker, /paper-trading redirects to My Bets; retire PaperTrading

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Verify in a worktree, then merge

**Files:** none are created.

- [ ] **Step 1: Run the full suites**

```bash
cd /c/Users/mwill/Documents/mwilliams2733/sports_picks
.venv/Scripts/python -m pytest -q -p no:warnings
cd frontend && npm test && npx tsc -p tsconfig.app.json --noEmit && npx eslint .
```

Expected:
- The backend gives the Phase 4 merge baseline, plus 8 new tests, with 0 failures.
- The frontend passes, clean.

- [ ] **Step 2: Check it in Chrome in a worktree, on a db snapshot**

Use the Phase 3/4 recipe: a detached worktree, `npm ci --legacy-peer-deps`, `npm run build`, and :8001 on a snapshot with `ENABLE_SCHEDULER=0`. Check at desktop and phone width:
1. The Leaders tab shows the board with Claude, the Model and unranked players, plus streaks.
2. The feed lists real past events. Old events have no Tail.
3. Join as a test player and place a bet. The new event appears with a Tail button for a second test player, and **Tail** puts it on that player's slip at the current price.
4. The Lobby ticker shows the latest line.
5. `/paper-trading` lands on My Bets.
6. On the **snapshot**, finish a test bet's game and run `settle_and_announce` once. The won/lost event appears once. Running it again adds nothing.

Save screenshots, then remove the worktree, using the long-path fallback if needed.

- [ ] **Step 3: Report to the owner and wait for "merge"**

- [ ] **Step 4: Merge and restart, on the owner's OK**

This phase changes `users.py` (API), and `scheduler.py` plus `paper_settlement.py` (scheduler), so **both** restart. There is no migration.
1. **Check ET.** Read ET with python `zoneinfo`, never `TZ=… date` (memory `sports-picks-scheduler-operations`). Pick a moment at least 30 minutes from any window, the scout, recalibration and the price refreshes, and with no game live.
2. **Merge:**

```bash
git checkout master
git merge --no-ff feat/sportsbook-phase-5 -m "Merge: sportsbook phase 5 — leaders, feed and Tail

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
npm --prefix frontend run build
```

3. **Restart uvicorn on :8000** as `share.ps1` does (old log to `app.log.prev`).
4. **Restart the scheduler.** Save its logs aside, stop both processes by command line, and start detached with `scripts\start_scheduler.ps1`.
5. **Confirm:**
   1. `/users/feed` returns 200.
   2. `/users/leaderboard` returns 200.
   3. `/paper-trading` on the tunnel lands on My Bets.
   4. `scheduler.log` shows "Scheduler started" with all jobs.
6. **Update memory:** `sports-picks-sportsbook-ui.md` → "Phase 5 merged <sha>". Note that both graders now share `backend/paper/feed.settle_and_announce`.

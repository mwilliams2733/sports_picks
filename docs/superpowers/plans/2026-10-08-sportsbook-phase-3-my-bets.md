# Sportsbook Phase 3 — My Bets — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give every player a My Bets page.
- It shows open and settled tickets: one per straight bet and one per parlay, each listing its games.
- A money strip shows Available, Settled balance, Open stakes and Today's P/L.
- A "WINNER 🎉" banner shows the first time a win appears on a device, and the My Bets tab carries a badge with the open-bet count.

**Architecture:**
- **Backend:**
  - A read-only `GET /users/{id}/bets` builds tickets in a new `backend/paper/bets.py`.
  - Its money strip reuses the functions the balance and stats already use: `available_of`, `balance_of`, `open_stakes`, and `player_bets` with `_compute_period_stats`.
  - "To win" uses settlement's own formula. The parlay product moves into `paper_settlement.parlay_win_payout`, which `settle_parlays` then calls.
- **Frontend:**
  - Pure helpers in `lib/bets.ts`.
  - A `useMyBets` hook, plus `useCurrentPlayer`.
  - `TicketCard`, the `MyBets` page at `/bets`, and a `Celebrations` banner mounted in `Layout`.
- **Folded in from Phase 2's deferred list:**
  - The slip refreshes board prices after a placement attempt, so a moved price shows on the tile.
  - The receipt counts only legs that were actually refused.

**Tech Stack:** FastAPI, SQLAlchemy, pytest; React 19, react-router 7, @tanstack/react-query 5, zustand 5, vitest and Testing Library; plain CSS.

**Spec:** `docs/superpowers/specs/2026-10-07-sportsbook-ui-design.md`. This plan implements §7 (without `cash_out`, which is Phase 6), §4's `/bets` route and the My Bets badge, and the Phase 3 row of §13.

## Global Constraints

- **Server fields only.** Every money figure on My Bets comes from the server.
  - **Available, Settled balance and Open stakes** come from `available_of`, `balance_of` and `open_stakes`.
  - **Today's P/L** is `/users/{id}/stats`'s `today.profit`, computed the same way.
  - **"To win"** comes from `payout_for` / `parlay_win_payout`.
  - The frontend never computes a balance.
- **Old shapes stay.** `GET /users/{id}/picks` keeps its flat shape exactly (spec §7). The new endpoint is additive and read-only, an open read like `/picks`.
- **No cash out yet.** A ticket has no `cash_out` field until Phase 6. The Settled filter is All / Won / Lost; "Cashed out" arrives with cash out.
- **Celebrations.**
  - Seen bet ids are kept per player in `localStorage` (key `sp-seen-wins.<userId>`), with every read and write in try/catch.
  - The first visit on a device seeds the store silently. A cleared browser at worst replays a banner (spec), and a fresh device never replays every past win.
  - A loss gets no animation. Animations are off under `prefers-reduced-motion`.
- **Route-timing deviation from spec §4, decided while planning.** Spec §4 redirects `/paper-trading` to `/bets`. That page still holds the only leaderboard and feed until Phase 5 builds `/leaders`, so in this phase:
  - the My Bets tab moves to `/bets`;
  - `/paper-trading` stays, listed in the Research menu as "Leaderboard";
  - the redirect lands with Phase 5.
- **Never run `npm run build` in the main working tree on a branch.** The live app serves `frontend/dist` from disk (memory `sports-picks-dist-is-live`).
  - Type-check with `npx tsc -p tsconfig.app.json --noEmit`.
  - Build only in the Task 7 worktree, and on master at merge.
- **Write code with the Write and Edit tools.** Any code containing backslashes (regexes) must go through them, not through Bash heredoc scripts, which drop backslashes in this shell (memory `sports-picks-heredoc-backslash`).
- **Styles** go in `frontend/src/sportsbook.css`.
- **Commits.**
  - Messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
  - Never stage `config.yaml`. Stage only the named files.
- **Mutation checks.** Mutation-check each guard. Delete the touched module's `.pyc` after every Python write and every restore.

## Review Focus

1. **No current player, a brand-new player, or a player with no bets.** My Bets shows a plain message or an empty state. The badge is hidden and nothing crashes. Test is in Task 4.
2. **A fresh device or corrupt seen-storage must not replay every past win.** The first load seeds silently; only a win that settles after that triggers the banner, once. Test is in Task 3.
3. **A partly graded parlay** (one leg won, one pending) stays Open, with mixed leg dots. A parlay with a pushed leg settles as "Push" (the house rule in `settle_parlays`). Tests are in Task 1 (API) and Task 2 (dots).
4. **Negative money:** a lost ticket shows "Lost −$25.00", and a losing day shows a negative Today's P/L. Neither shows "+−". Tests are in Tasks 2 and 4.
5. **A leg whose game was postponed or canceled** shows that status instead of a stale kickoff time. Test is in Task 2 (`gameLine`).

---

### Task 1: `GET /users/{id}/bets` — tickets and the money strip

**Files:**
- Create: `backend/paper/bets.py`
- Modify: `backend/pipeline/paper_settlement.py`. Add `parlay_win_payout` and use it in `settle_parlays`.
- Modify: `backend/api/users.py` (the route)
- Test: `backend/tests/test_paper_bets.py`

**Interfaces:**
- **Consumes:**
  - `grader.payout_for(result, odds)` and `time_utils.as_utc` / `game_start_utc`.
  - In `users.py`: `available_of`, `balance_of`, `open_stakes`, `player_bets`, `_compute_period_stats` and `et_today`.
- **Produces:**
  - `paper_settlement.parlay_win_payout(stake: float, leg_odds: list[int]) -> float`.
  - `bets.tickets(session, user_id: int) -> list[dict]`.
  - **HTTP:** `GET /users/{id}/bets` returns `{"summary": {available, balance, open_stakes, today_pl}, "tickets": [Ticket]}`.
  - **`Ticket`:** `{kind: "straight"|"parlay", id, stake, odds, to_win, result, payout, created_at (ISO, +00:00), sgp: bool, legs: [Leg]}`, newest first.
  - **`Leg`:** `{pick_type, pick_value, odds, prop_player, prop_market, result, game: {id, sport, home_team, away_team, start_time (ISO|null), status, home_score, away_score, live_detail: null}}`.

- [ ] **Step 1: Write the failing tests, `backend/tests/test_paper_bets.py`**

```python
"""My Bets tickets, GET /users/{id}/bets (sportsbook spec 2026-10-07 §7)."""
from fastapi.testclient import TestClient

from backend.api.main import create_app
from backend.database import get_session
from backend.models import Base, Game, PaperPick, Team
from backend.pipeline.paper_settlement import parlay_win_payout, settle_parlays
from backend.tests.auth_helpers import ALL_HEADERS, TEST_PIN
from backend.tests.pricing_helpers import seed_fresh_odds
from backend.time_utils import et_today


def _client(headers=ALL_HEADERS):
    client = TestClient(create_app(":memory:"), headers=headers)
    Base.metadata.create_all(client.app.state.engine)
    return client


def _games(client, n=1):
    """n scheduled NFL games dated today with fresh odds (ML -110/-110,
    spread -3.5/+3.5, total 220.5, all -110)."""
    s = get_session(client.app.state.engine)
    ids = []
    for i in range(n):
        h = Team(name=f"H{i}", abbreviation=f"H{i}", sport="nfl")
        a = Team(name=f"A{i}", abbreviation=f"A{i}", sport="nfl")
        s.add_all([h, a])
        s.flush()
        g = Game(sport="nfl", season="2026", date=et_today(), home_team_id=h.id,
                 away_team_id=a.id, status="scheduled")
        s.add(g)
        s.flush()
        ids.append(g.id)
    s.commit()
    s.close()
    for gid in ids:
        seed_fresh_odds(client.app.state.engine, gid)
    return ids


def _user(client, name="friend"):
    r = client.post("/users/", json={"name": name, "pin": TEST_PIN})
    assert r.status_code == 200
    return r.json()["id"]


def _bet(client, uid, gid, pick_type="moneyline", side="HOME", stake=110):
    r = client.post(f"/users/{uid}/picks", json={"game_id": gid, "pick_type": pick_type,
                                                 "side": side, "stake": stake})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _parlay(client, uid, legs, stake=50):
    r = client.post(f"/users/{uid}/parlay", json={"legs": legs, "stake": stake})
    assert r.status_code == 200, r.text
    return r.json()


def _grade(client, pick_id, result, payout):
    s = get_session(client.app.state.engine)
    p = s.get(PaperPick, pick_id)
    p.result, p.payout = result, payout
    s.commit()
    s.close()


def _leg_ids(client, parlay_id):
    s = get_session(client.app.state.engine)
    try:
        return [p.id for p in s.query(PaperPick).filter(PaperPick.parlay_id == parlay_id)
                .order_by(PaperPick.id)]
    finally:
        s.close()


def _bets(client, uid):
    r = client.get(f"/users/{uid}/bets")
    assert r.status_code == 200, r.text
    return r.json()


ML = lambda gid, side="HOME": {"game_id": gid, "pick_type": "moneyline", "side": side}
OVER = lambda gid: {"game_id": gid, "pick_type": "over_under", "side": "Over"}


def test_a_straight_bet_is_one_ticket_with_its_game():
    client = _client()
    [gid] = _games(client)
    uid = _user(client)
    pid = _bet(client, uid, gid)
    [t] = _bets(client, uid)["tickets"]
    assert {k: t[k] for k in ("kind", "id", "stake", "odds", "to_win", "result", "payout", "sgp")} == \
        {"kind": "straight", "id": pid, "stake": 110, "odds": -110, "to_win": 100.0,
         "result": None, "payout": None, "sgp": False}
    assert t["created_at"].endswith("+00:00")
    [leg] = t["legs"]
    assert (leg["pick_value"], leg["result"]) == ("HOME ML", None)
    assert leg["game"] == {"id": gid, "sport": "nfl", "home_team": "H0", "away_team": "A0",
                           "start_time": None, "status": "scheduled", "home_score": None,
                           "away_score": None, "live_detail": None}


def test_a_parlay_is_one_ticket_not_its_legs():
    client = _client()
    g1, g2 = _games(client, 2)
    uid = _user(client)
    placed = _parlay(client, uid, [ML(g1), OVER(g2)])
    [t] = _bets(client, uid)["tickets"]
    assert (t["kind"], t["id"], t["odds"], len(t["legs"]), t["sgp"]) == \
        ("parlay", placed["id"], placed["combined_odds"], 2, False)
    assert t["to_win"] == placed["potential_payout"]


def test_a_same_game_parlay_is_flagged_sgp():
    client = _client()
    [gid] = _games(client)
    uid = _user(client)
    _parlay(client, uid, [ML(gid), OVER(gid)])
    assert _bets(client, uid)["tickets"][0]["sgp"] is True


def test_newest_first_and_only_this_players_bets():
    client = _client()
    g1, g2 = _games(client, 2)
    me, other = _user(client, "me"), _user(client, "other")
    first = _bet(client, me, g1)
    second = _bet(client, me, g2)
    _bet(client, other, g1)
    assert [t["id"] for t in _bets(client, me)["tickets"]] == [second, first]


def test_a_partly_graded_parlay_stays_open_with_its_leg_results():
    client = _client()
    g1, g2 = _games(client, 2)
    uid = _user(client)
    placed = _parlay(client, uid, [ML(g1), OVER(g2)])
    first_leg, _ = _leg_ids(client, placed["id"])
    _grade(client, first_leg, "win", 0.0)
    [t] = _bets(client, uid)["tickets"]
    assert t["result"] is None
    assert [leg["result"] for leg in t["legs"]] == ["win", None]


def test_to_win_is_exactly_what_settlement_pays():
    client = _client()
    g1, g2 = _games(client, 2)
    uid = _user(client)
    placed = _parlay(client, uid, [ML(g1), OVER(g2)])
    to_win = _bets(client, uid)["tickets"][0]["to_win"]
    for leg in _leg_ids(client, placed["id"]):
        _grade(client, leg, "win", 0.0)
    s = get_session(client.app.state.engine)
    settle_parlays(s)
    s.close()
    [t] = _bets(client, uid)["tickets"]
    assert (t["result"], round(t["payout"], 2)) == ("win", to_win)
    assert round(parlay_win_payout(50, [-110, -110]), 2) == to_win


def test_summary_matches_the_balance_and_todays_stats():
    client = _client()
    g1, g2 = _games(client, 2)
    uid = _user(client)
    won = _bet(client, uid, g1)                      # 110 at -110
    _grade(client, won, "win", 100.0)
    _bet(client, uid, g2, stake=50)                  # still open
    summary = _bets(client, uid)["summary"]
    [row] = [u for u in client.get("/users/").json() if u["id"] == uid]
    today = client.get(f"/users/{uid}/stats").json()["today"]
    assert summary == {"available": row["available_balance"], "balance": row["current_balance"],
                       "open_stakes": 50.0, "today_pl": today["profit"]}
    assert summary["today_pl"] == 100.0


def test_unknown_player_is_404_and_bets_are_an_open_read():
    client = _client(headers={})
    assert client.get("/users/999/bets").status_code == 404
    uid = client.post("/users/", json={"name": "x", "pin": TEST_PIN}).json()["id"]
    assert _bets(client, uid) == {"summary": {"available": 10000.0, "balance": 10000.0,
                                              "open_stakes": 0.0, "today_pl": 0.0}, "tickets": []}
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `.venv/Scripts/python -m pytest backend/tests/test_paper_bets.py -q -p no:warnings`

Expected:
- Collection fails on `ImportError: parlay_win_payout`. Once that exists, every test fails: the route doesn't exist yet, so the SPA catch-all answers with 200 HTML and `r.json()` raises.

- [ ] **Step 3: Add `parlay_win_payout` to `backend/pipeline/paper_settlement.py`**

Add it above `settle_parlays`:

```python
def parlay_win_payout(stake: float, leg_odds: list[int]) -> float:
    """What a winning parlay pays: the product of its legs' win multipliers.
    Settlement and My Bets' "to win" both call this, so they cannot differ."""
    decimal = 1.0
    for odds in leg_odds:
        decimal *= 1 + payout_for("win", odds)
    return stake * (decimal - 1)
```

In `settle_parlays`, replace:

```python
        else:
            decimal = 1.0
            for leg in legs:
                decimal *= 1 + payout_for("win", leg.odds)
            parlay.result, parlay.payout = "win", parlay.stake * (decimal - 1)
```

with:

```python
        else:
            parlay.result, parlay.payout = "win", parlay_win_payout(parlay.stake, [leg.odds for leg in legs])
```

- [ ] **Step 4: Write `backend/paper/bets.py`**

```python
"""A player's bets as sportsbook tickets (spec 2026-10-07 §7).

One ticket per straight bet and one per parlay -- never a parlay's legs as
separate bets -- each carrying its legs' games, newest first. Read-only: the
"to win" figure is settlement's own formula, so a ticket promises exactly
what grading will pay.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import aliased

from backend.models import Game, PaperPick, Parlay, Team
from backend.pipeline.grader import payout_for
from backend.pipeline.paper_settlement import parlay_win_payout
from backend.time_utils import as_utc, game_start_utc

_NO_KICKOFF = datetime.max.replace(tzinfo=timezone.utc)


def _game(game, home, away) -> dict:
    start = game_start_utc(game)
    return {"id": game.id, "sport": game.sport, "home_team": home.abbreviation,
            "away_team": away.abbreviation, "start_time": start.isoformat() if start else None,
            "status": game.status, "home_score": game.home_score, "away_score": game.away_score,
            "live_detail": None}                     # Phase 4 fills this in


def _leg(pick, game, home, away) -> dict:
    return {"pick_type": pick.pick_type, "pick_value": pick.pick_value, "odds": pick.odds,
            "prop_player": pick.prop_player, "prop_market": pick.prop_market,
            "result": pick.result, "game": _game(game, home, away)}


def tickets(session, user_id: int) -> list[dict]:
    Home, Away = aliased(Team), aliased(Team)
    rows = (session.query(PaperPick, Game, Home, Away)
            .join(Game, Game.id == PaperPick.game_id)
            .join(Home, Home.id == Game.home_team_id)
            .join(Away, Away.id == Game.away_team_id)
            .filter(PaperPick.user_id == user_id)
            .all())
    out: list[tuple[datetime, int, dict]] = []
    parlay_legs: dict[int, list[tuple[datetime, PaperPick, dict]]] = {}
    for pick, game, home, away in rows:
        leg = _leg(pick, game, home, away)
        if pick.parlay_id is not None:
            parlay_legs.setdefault(pick.parlay_id, []).append(
                (game_start_utc(game) or _NO_KICKOFF, pick, leg))
            continue
        placed = as_utc(pick.created_at)
        out.append((placed, pick.id, {
            "kind": "straight", "id": pick.id, "stake": pick.stake, "odds": pick.odds,
            "to_win": round(pick.stake * payout_for("win", pick.odds), 2),
            "result": pick.result, "payout": pick.payout, "created_at": placed.isoformat(),
            "sgp": False, "legs": [leg]}))
    for parlay in session.query(Parlay).filter(Parlay.user_id == user_id).all():
        legs = sorted(parlay_legs.get(parlay.id, []), key=lambda x: (x[0], x[1].id))
        placed = as_utc(parlay.created_at)
        out.append((placed, parlay.id, {
            "kind": "parlay", "id": parlay.id, "stake": parlay.stake, "odds": parlay.combined_odds,
            "to_win": round(parlay_win_payout(parlay.stake, [p.odds for _, p, _ in legs]), 2),
            "result": parlay.result, "payout": parlay.payout, "created_at": placed.isoformat(),
            "sgp": len({p.game_id for _, p, _ in legs}) < len(legs),
            "legs": [leg for _, _, leg in legs]}))
    out.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return [t for _, _, t in out]
```

- [ ] **Step 5: Add the route in `backend/api/users.py`**

Add `from backend.paper import bets as bets_mod` beside `from backend.paper import pricing`. Then add, just after `get_user_picks`:

```python
@router.get("/{user_id}/bets")
def get_user_bets(request: Request, user_id: int):
    """A player's bets as tickets for My Bets (spec §7), with the money strip.

    Every figure is the one the rest of the app uses: available/balance/open
    stakes from the bankroll functions, Today's P/L from the same scorecard as
    /stats' "today". /{user_id}/picks keeps its flat shape for older callers.
    """
    session = get_session(request.app.state.engine)
    try:
        user = session.get(UserProfile, user_id)
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        today = et_today()
        todays = [b for b in player_bets(session, user_id) if b.day == today]
        return {
            "summary": {
                "available": round(available_of(session, user), 2),
                "balance": round(balance_of(session, user), 2),
                "open_stakes": round(open_stakes(session, user), 2),
                "today_pl": _compute_period_stats(todays)["profit"],
            },
            "tickets": bets_mod.tickets(session, user_id),
        }
    finally:
        session.close()
```

- [ ] **Step 6: Run the tests and confirm they pass**

Run: `.venv/Scripts/python -m pytest backend/tests/test_paper_bets.py backend/tests/test_paper_settlement.py backend/tests/test_paper_bet_odds_guard.py -q -p no:warnings`

Expected: all pass. `test_paper_settlement.py` proves the `settle_parlays` refactor changed nothing.

- [ ] **Step 7: Mutation-check**

Make each change, run `test_paper_bets.py`, confirm a test fails, then restore. Delete the touched `.pyc` after every write and every restore.
1. Drop the `continue` after collecting a parlay leg, so legs also become straight tickets. The parlay test must fail.
2. Sort ascending (`reverse=False`). The ordering test must fail.
3. In `parlay_win_payout`, use `decimal *= 1 + payout_for("win", odds) * 0.95`. The to-win test must fail.
4. Return `"open_stakes": 0.0`. The summary test must fail.
5. Set `"sgp": False` for parlays. The SGP test must fail.

- [ ] **Step 8: Commit**

```bash
git checkout feat/sportsbook-phase-3    # created with the plan commit
git add backend/paper/bets.py backend/pipeline/paper_settlement.py backend/api/users.py backend/tests/test_paper_bets.py
git commit -m "feat(paper): GET /users/{id}/bets — tickets and the money strip for My Bets

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Ticket types, API call, hooks and pure helpers

**Files:**
- Modify: `frontend/src/types.ts` (append), `frontend/src/api/client.ts` (the `users` block)
- Create: `frontend/src/hooks/useMyBets.ts`, `frontend/src/hooks/useCurrentPlayer.ts`, `frontend/src/lib/bets.ts`
- Test: `frontend/src/lib/bets.test.ts`

**Interfaces:**
- **Consumes:** the Task 1 response, plus `resolveLabel` from `lib/quotes.ts` and `startLabel` from `lib/board.ts`.
- **Produces:**
  - **Types:** `TicketGame`, `TicketLeg`, `Ticket`, `BetsSummary`, `MyBetsData`.
  - **API and hooks:**
    - `api.users.bets(id): Promise<MyBetsData>`
    - `useMyBets(userId?: number)`, with key `['users','bets',userId]`
    - `useCurrentPlayer(): UserProfile | undefined`
  - **`lib/bets.ts`:**
    - **Ticket helpers:** `ticketKey`, `ticketCode`, `ticketType`, `splitTickets` and `filterSettled` (with type `SettledFilter`).
    - **Leg helpers:** `legLabel`, `legStatus` (with type `LegStatus`) and `gameLine`.
    - **Money:** `signedMoney`.
    - **Celebrations:** `newWins`, `loadSeen` and `saveSeen`.

- [ ] **Step 1: Append the types to `frontend/src/types.ts`**

```ts
export interface TicketGame {
  id: number; sport: string; home_team: string; away_team: string; start_time: string | null;
  status: string; home_score: number | null; away_score: number | null; live_detail: string | null;
}
export interface TicketLeg {
  pick_type: string; pick_value: string; odds: number; prop_player: string | null;
  prop_market: string | null; result: string | null; game: TicketGame;
}
export interface Ticket {
  kind: 'straight' | 'parlay'; id: number; stake: number; odds: number; to_win: number;
  result: string | null; payout: number | null; created_at: string; sgp: boolean; legs: TicketLeg[];
}
export interface BetsSummary { available: number; balance: number; open_stakes: number; today_pl: number }
export interface MyBetsData { summary: BetsSummary; tickets: Ticket[] }
```

- [ ] **Step 2: Add the API call and the hooks**

In `frontend/src/api/client.ts`:
1. Add `MyBetsData` to the `../types` import.
2. Add this inside `users: { ... }`, after `picks`:

```ts
    bets: (id: number) => get<MyBetsData>(`/users/${id}/bets`),
```

`frontend/src/hooks/useMyBets.ts`:

```ts
import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'

// Under ['users', ...] so a placed bet (which invalidates ['users']) refreshes
// My Bets, the badge and the celebration check at once.
export function useMyBets(userId: number | undefined) {
  return useQuery({
    queryKey: ['users', 'bets', userId],
    queryFn: () => api.users.bets(userId as number),
    enabled: userId !== undefined,
    refetchInterval: 60_000,
    refetchOnWindowFocus: true,
  })
}
```

`frontend/src/hooks/useCurrentPlayer.ts`:

```ts
import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'
import { useUserStore } from '../stores/userStore'

/** The player chosen in the top bar, from the same cached users list the chip reads. */
export function useCurrentPlayer() {
  const { currentUserName } = useUserStore()
  const users = useQuery({ queryKey: ['users', 'list'], queryFn: api.users.list, refetchInterval: 60_000 })
  return users.data?.find(u => u.name === currentUserName)
}
```

- [ ] **Step 3: Write the failing tests, `frontend/src/lib/bets.test.ts`**

```ts
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import type { Ticket, TicketGame, TicketLeg } from '../types'
import { filterSettled, gameLine, legLabel, legStatus, loadSeen, newWins, saveSeen, signedMoney,
  splitTickets, ticketCode, ticketKey, ticketType } from './bets'

const game = (over: Partial<TicketGame> = {}): TicketGame => ({
  id: 1, sport: 'nfl', home_team: 'DAL', away_team: 'TB', start_time: '2026-10-09T00:15:00+00:00',
  status: 'scheduled', home_score: null, away_score: null, live_detail: null, ...over,
})
const leg = (over: Partial<TicketLeg> = {}): TicketLeg => ({
  pick_type: 'spread', pick_value: 'AWAY +9', odds: -109, prop_player: null, prop_market: null,
  result: null, game: game(), ...over,
})
const ticket = (over: Partial<Ticket> = {}): Ticket => ({
  kind: 'straight', id: 6, stake: 100, odds: -109, to_win: 91.74, result: null, payout: null,
  created_at: '2026-10-08T22:24:27+00:00', sgp: false, legs: [leg()], ...over,
})

beforeEach(() => localStorage.clear())
afterEach(() => vi.restoreAllMocks())

describe('ticket labels', () => {
  it('codes and types a ticket the way the receipt does', () => {
    expect(ticketCode(ticket())).toBe('#P-6')
    expect(ticketCode(ticket({ kind: 'parlay', id: 4 }))).toBe('#PL-4')
    expect(ticketType(ticket())).toBe('Straight')
    expect(ticketType(ticket({ kind: 'parlay' }))).toBe('Parlay')
    expect(ticketType(ticket({ kind: 'parlay', sgp: true }))).toBe('SGP')
    expect(ticketKey(ticket({ kind: 'parlay', id: 4 }))).toBe('parlay-4')
  })
})

describe('splitTickets / filterSettled', () => {
  const open = ticket({ id: 1 })
  const won = ticket({ id: 2, result: 'win', payout: 91.74 })
  const lost = ticket({ id: 3, result: 'loss', payout: -100 })
  const push = ticket({ id: 4, result: 'push', payout: 0 })
  it('splits open from settled, keeping order', () => {
    expect(splitTickets([open, won, lost])).toEqual({ open: [open], settled: [won, lost] })
  })
  it('filters settled by result', () => {
    expect(filterSettled([won, lost, push], 'all')).toEqual([won, lost, push])
    expect(filterSettled([won, lost, push], 'won')).toEqual([won])
    expect(filterSettled([won, lost, push], 'lost')).toEqual([lost])
  })
})

describe('legs', () => {
  it('names a game leg by team and a prop leg as stored', () => {
    expect(legLabel(leg())).toBe('TB +9')
    expect(legLabel(leg({ pick_type: 'moneyline', pick_value: 'HOME ML' }))).toBe('DAL ML')
    expect(legLabel(leg({ pick_type: 'prop', pick_value: 'Dak Prescott Over 255.5 Pass Yards' })))
      .toBe('Dak Prescott Over 255.5 Pass Yards')
  })
  it('maps a leg result to a status dot', () => {
    expect([null, 'win', 'loss', 'push'].map(r => legStatus(leg({ result: r }))))
      .toEqual(['pending', 'won', 'lost', 'push'])
  })
  it('shows a final score, the kickoff, or an unusual status (Review Focus 5)', () => {
    expect(gameLine(game({ status: 'final', home_score: 27, away_score: 20 }))).toBe('TB 20 – DAL 27 · Final')
    expect(gameLine(game())).toMatch(/^TB @ DAL · \d{1,2}:\d{2}\s?[AP]M$/)
    expect(gameLine(game({ status: 'postponed' }))).toBe('TB @ DAL · Postponed')
  })
})

describe('signedMoney', () => {
  it('never prints +− (Review Focus 4)', () => {
    expect(signedMoney(91.74)).toBe('+$91.74')
    expect(signedMoney(-100)).toBe('−$100.00')
    expect(signedMoney(0)).toBe('$0.00')
  })
})

describe('celebrations: newWins / seen storage (Review Focus 2)', () => {
  const won = ticket({ id: 2, result: 'win', payout: 91.74 })
  const lost = ticket({ id: 3, result: 'loss', payout: -100 })
  it('seeds silently on a first visit, so past wins never replay', () => {
    expect(newWins([won, lost, ticket()], null)).toEqual({ wins: [], seen: ['straight-2', 'straight-3'] })
  })
  it('reports a win that settles after the first visit, once', () => {
    const later = ticket({ id: 7, result: 'win', payout: 50 })
    const first = newWins([won, later], ['straight-2'])
    expect(first.wins).toEqual([later])
    expect(newWins([won, later], first.seen).wins).toEqual([])
  })
  it('never celebrates a loss', () => {
    expect(newWins([lost], []).wins).toEqual([])
  })
  it('reads corrupt or missing storage as a first visit', () => {
    expect(loadSeen(1)).toBeNull()
    localStorage.setItem('sp-seen-wins.1', '{bad')
    expect(loadSeen(1)).toBeNull()
    saveSeen(1, ['straight-2'])
    expect(loadSeen(1)).toEqual(['straight-2'])
  })
  it('survives storage that throws', () => {
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('blocked') })
    expect(() => saveSeen(1, ['x'])).not.toThrow()
  })
})
```

- [ ] **Step 4: Run the tests and confirm they fail**

Run: `cd frontend && npx vitest run src/lib/bets.test.ts`

Expected: FAIL, "Failed to resolve import './bets'".

- [ ] **Step 5: Write `frontend/src/lib/bets.ts`**

```ts
import type { Ticket, TicketGame, TicketLeg } from '../types'
import { resolveLabel } from './quotes'
import { formatMoney, startLabel } from './board'

export type SettledFilter = 'all' | 'won' | 'lost'
export type LegStatus = 'pending' | 'won' | 'lost' | 'push'

export const ticketKey = (t: Ticket) => `${t.kind}-${t.id}`
export const ticketCode = (t: Ticket) => (t.kind === 'parlay' ? `#PL-${t.id}` : `#P-${t.id}`)
export const ticketType = (t: Ticket) => (t.kind === 'straight' ? 'Straight' : t.sgp ? 'SGP' : 'Parlay')

export function splitTickets(tickets: Ticket[]): { open: Ticket[]; settled: Ticket[] } {
  return { open: tickets.filter(t => t.result === null), settled: tickets.filter(t => t.result !== null) }
}

export function filterSettled(tickets: Ticket[], f: SettledFilter): Ticket[] {
  if (f === 'won') return tickets.filter(t => t.result === 'win')
  if (f === 'lost') return tickets.filter(t => t.result === 'loss')
  return tickets
}

export function legLabel(l: TicketLeg): string {
  return l.pick_type === 'prop' ? l.pick_value : resolveLabel(l.pick_value, l.game.home_team, l.game.away_team)
}

export function legStatus(l: TicketLeg): LegStatus {
  return l.result === 'win' ? 'won' : l.result === 'loss' ? 'lost' : l.result === 'push' ? 'push' : 'pending'
}

/** "TB 20 – DAL 27 · Final", "TB @ DAL · 8:15 PM", or "TB @ DAL · Postponed". */
export function gameLine(g: TicketGame): string {
  if (g.status === 'final' && g.home_score !== null && g.away_score !== null) {
    return `${g.away_team} ${g.away_score} – ${g.home_team} ${g.home_score} · Final`
  }
  const when = g.status === 'scheduled' ? startLabel(g.start_time)
    : g.status.charAt(0).toUpperCase() + g.status.slice(1).replace(/_/g, ' ')
  return `${g.away_team} @ ${g.home_team} · ${when}`
}

/** "+$91.74", "−$100.00" (a real minus sign), "$0.00". */
export function signedMoney(n: number): string {
  if (n > 0) return `+${formatMoney(n)}`
  if (n < 0) return `−${formatMoney(Math.abs(n))}`
  return formatMoney(0)
}

/** Wins to celebrate and the new seen set. `seen === null` is a first visit
 *  on this device: everything already settled is marked seen silently, so a
 *  new phone never replays a player's whole history (spec §7). */
export function newWins(tickets: Ticket[], seen: string[] | null): { wins: Ticket[]; seen: string[] } {
  const settled = tickets.filter(t => t.result !== null).map(ticketKey)
  if (seen === null) return { wins: [], seen: settled }
  const known = new Set(seen)
  return {
    wins: tickets.filter(t => t.result === 'win' && !known.has(ticketKey(t))),
    seen: [...new Set([...seen, ...settled])],
  }
}

const seenKey = (userId: number) => `sp-seen-wins.${userId}`

export function loadSeen(userId: number): string[] | null {
  try {
    const raw = localStorage.getItem(seenKey(userId))
    if (raw === null) return null
    const v: unknown = JSON.parse(raw)
    return Array.isArray(v) ? v.filter((x): x is string => typeof x === 'string') : null
  } catch {
    return null
  }
}

export function saveSeen(userId: number, keys: string[]): void {
  try { localStorage.setItem(seenKey(userId), JSON.stringify(keys)) } catch { /* unavailable */ }
}
```

- [ ] **Step 6: Run the tests and confirm they pass**

Run: `cd frontend && npx vitest run src/lib/bets.test.ts && npx tsc -p tsconfig.app.json --noEmit && npx eslint .`

Expected: all pass, and both tsc and eslint are clean.

- [ ] **Step 7: Mutation-check**

Make each change, confirm a test fails, then restore:
1. In `newWins`, return `{ wins: tickets.filter(t => t.result === 'win'), seen: settled }` when `seen === null`.
2. In `signedMoney`, use `+${formatMoney(n)}` for every n.
3. In `gameLine`, drop the `status === 'scheduled'` branch and always use `startLabel`.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/types.ts frontend/src/api/client.ts frontend/src/hooks/useMyBets.ts frontend/src/hooks/useCurrentPlayer.ts frontend/src/lib/bets.ts frontend/src/lib/bets.test.ts
git commit -m "feat(bets): ticket types, bets API call, hooks and pure helpers

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: The win celebration banner

**Files:**
- Create: `frontend/src/components/Celebrations.tsx`
- Modify: `frontend/src/sportsbook.css` (append)
- Test: `frontend/src/components/Celebrations.test.tsx`

**Interfaces:**
- **Consumes:** `useCurrentPlayer`, `useMyBets`, `newWins`, `loadSeen`, `saveSeen`, `formatMoney`.
- **Produces:** `Celebrations` (default export). It renders `null` unless a new win is showing, and is mounted in `Layout` in Task 5.

- [ ] **Step 1: Write the failing tests, `frontend/src/components/Celebrations.test.tsx`**

```tsx
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import Celebrations from './Celebrations'
import { api } from '../api/client'
import { useUserStore } from '../stores/userStore'
import type { Ticket, UserProfile } from '../types'

vi.mock('../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/client')>()
  return { ...actual, api: { ...actual.api, users: { ...actual.api.users, list: vi.fn(), bets: vi.fn() } } }
})

const me = { id: 1, name: 'Marcus', available_balance: 1000 } as UserProfile
const t = (id: number, result: string | null, payout: number | null, kind: Ticket['kind'] = 'straight'): Ticket => ({
  kind, id, stake: 100, odds: -110, to_win: 90.91, result, payout, created_at: '2026-10-08T22:00:00+00:00',
  sgp: false, legs: [],
})
const summary = { available: 1000, balance: 1000, open_stakes: 0, today_pl: 0 }

function renderIt() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={client}><Celebrations /></QueryClientProvider>)
}

beforeEach(() => {
  vi.clearAllMocks()
  localStorage.clear()
  useUserStore.setState({ currentUserName: 'Marcus' })
  vi.mocked(api.users.list).mockResolvedValue([me])
})

describe('Celebrations', () => {
  it('stays quiet on a first visit and marks past wins seen (Review Focus 2)', async () => {
    vi.mocked(api.users.bets).mockResolvedValue({ summary, tickets: [t(1, 'win', 90.91)] })
    renderIt()
    await waitFor(() => expect(localStorage.getItem('sp-seen-wins.1')).toBe('["straight-1"]'))
    expect(screen.queryByText(/WINNER/)).not.toBeInTheDocument()
  })

  it('celebrates a win that settled since this device last looked', async () => {
    localStorage.setItem('sp-seen-wins.1', '[]')
    vi.mocked(api.users.bets).mockResolvedValue({ summary, tickets: [t(2, 'win', 90.91), t(3, 'loss', -100)] })
    renderIt()
    expect(await screen.findByText('WINNER 🎉 +$90.91')).toBeInTheDocument()
    expect(localStorage.getItem('sp-seen-wins.1')).toBe('["straight-2","straight-3"]')
  })

  it('never celebrates a loss', async () => {
    localStorage.setItem('sp-seen-wins.1', '[]')
    vi.mocked(api.users.bets).mockResolvedValue({ summary, tickets: [t(3, 'loss', -100)] })
    renderIt()
    await waitFor(() => expect(localStorage.getItem('sp-seen-wins.1')).toBe('["straight-3"]'))
    expect(screen.queryByText(/WINNER/)).not.toBeInTheDocument()
  })

  it('makes a parlay win bigger', async () => {
    localStorage.setItem('sp-seen-wins.1', '[]')
    vi.mocked(api.users.bets).mockResolvedValue({ summary, tickets: [t(4, 'win', 264, 'parlay')] })
    renderIt()
    const banner = (await screen.findByText('WINNER 🎉 +$264.00')).closest('.sb-win')
    expect(banner).toHaveClass('sb-win-big')
    expect(screen.getByText('Parlay hit!')).toBeInTheDocument()
  })
})
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `cd frontend && npx vitest run src/components/Celebrations.test.tsx`

Expected: FAIL, "Failed to resolve import './Celebrations'".

- [ ] **Step 3: Write `frontend/src/components/Celebrations.tsx`**

```tsx
import { useEffect, useState, type CSSProperties } from 'react'
import { useCurrentPlayer } from '../hooks/useCurrentPlayer'
import { useMyBets } from '../hooks/useMyBets'
import { loadSeen, newWins, saveSeen } from '../lib/bets'
import { formatMoney } from '../lib/board'

const COLORS = ['#2fe37a', '#ffb020', '#f3f5f8', '#8b5cf6', '#ff4d5e']
// Fixed pseudo-random spread so the confetti is deterministic (and testable).
const CONFETTI: CSSProperties[] = Array.from({ length: 28 }, (_, i) => ({
  left: `${(i * 37) % 100}%`,
  animationDelay: `${((i * 13) % 10) / 20}s`,
  background: COLORS[i % COLORS.length],
}))

/** "WINNER 🎉 +$X" the first time a settled win appears on this device. */
export default function Celebrations() {
  const me = useCurrentPlayer()
  const bets = useMyBets(me?.id)
  const [win, setWin] = useState<{ amount: number; parlay: boolean } | null>(null)
  const meId = me?.id

  useEffect(() => {
    if (meId === undefined || !bets.data) return
    const { wins, seen } = newWins(bets.data.tickets, loadSeen(meId))
    saveSeen(meId, seen)
    if (wins.length === 0) return
    setWin({ amount: wins.reduce((a, t) => a + (t.payout ?? 0), 0), parlay: wins.some(t => t.kind === 'parlay') })
  }, [meId, bets.data])

  useEffect(() => {
    if (!win) return
    const timer = setTimeout(() => setWin(null), 6000)
    return () => clearTimeout(timer)
  }, [win])

  if (!win) return null
  return (
    <div className={`sb-win${win.parlay ? ' sb-win-big' : ''}`} role="status" onClick={() => setWin(null)}>
      <div className="sb-confetti" aria-hidden="true">
        {CONFETTI.map((style, i) => <span key={i} style={style} />)}
      </div>
      <strong>WINNER 🎉 +{formatMoney(win.amount)}</strong>
      {win.parlay && <small>Parlay hit!</small>}
    </div>
  )
}
```

- [ ] **Step 4: Append the styles to `frontend/src/sportsbook.css`**

```css
/* ─── Win celebration (spec §7) ─────────────────────────────── */
.sb-win {
  position: fixed; left: 50%; top: 72px; z-index: 300; transform: translateX(-50%);
  display: flex; flex-direction: column; align-items: center; gap: 0.2rem;
  min-width: 260px; padding: 1rem 1.5rem; border-radius: var(--radius-lg); overflow: hidden;
  background: var(--accent); color: var(--on-accent); box-shadow: var(--shadow-lg); cursor: pointer;
  animation: sb-drop 300ms ease;
}
.sb-win strong { position: relative; font-size: 1.25rem; font-weight: 800; letter-spacing: 0.02em; }
.sb-win small { position: relative; font-weight: 700; }
.sb-win-big { min-width: 320px; padding: 1.5rem 2rem; }
.sb-win-big strong { font-size: 1.6rem; }
.sb-confetti { position: absolute; inset: 0; pointer-events: none; }
.sb-confetti span {
  position: absolute; top: -10px; width: 7px; height: 12px; border-radius: 2px; opacity: 0.9;
  animation: sb-confetti 1.6s ease-in forwards;
}
@keyframes sb-confetti { to { transform: translateY(140px) rotate(540deg); opacity: 0; } }
@media (prefers-reduced-motion: reduce) {
  .sb-win { animation: none; }
  .sb-confetti { display: none; }
}
```

- [ ] **Step 5: Run the tests and confirm they pass**

Run: `cd frontend && npx vitest run src/components/Celebrations.test.tsx && npx tsc -p tsconfig.app.json --noEmit && npx eslint .`

Expected: all pass.

- [ ] **Step 6: Mutation-check**

Make each change, confirm a test fails, then restore:
1. Remove `saveSeen(meId, seen)`. The first-visit test must fail.
2. Replace `loadSeen(meId)` with `[]`. The first-visit test must fail, because it celebrates.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/components/Celebrations.tsx frontend/src/components/Celebrations.test.tsx frontend/src/sportsbook.css
git commit -m "feat(bets): WINNER banner with confetti, once per win per device

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: The My Bets page and ticket cards

**Files:**
- Create: `frontend/src/components/TicketCard.tsx`, `frontend/src/pages/MyBets.tsx`
- Modify: `frontend/src/sportsbook.css` (append)
- Test: `frontend/src/pages/MyBets.test.tsx`

**Interfaces:**
- **Consumes:** the Task 2 helpers and hooks, plus `formatMoney` and `formatOdds`.
- **Produces:** the `MyBets` default export, routed at `/bets` in Task 5, and `TicketCard({ t })`.

- [ ] **Step 1: Write the failing tests, `frontend/src/pages/MyBets.test.tsx`**

```tsx
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import MyBets from './MyBets'
import { api } from '../api/client'
import { useUserStore } from '../stores/userStore'
import type { Ticket, TicketLeg, UserProfile } from '../types'

vi.mock('../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/client')>()
  return { ...actual, api: { ...actual.api, users: { ...actual.api.users, list: vi.fn(), bets: vi.fn() } } }
})

const me = { id: 1, name: 'Marcus', available_balance: 9850 } as UserProfile
const leg = (over: Partial<TicketLeg> = {}): TicketLeg => ({
  pick_type: 'spread', pick_value: 'AWAY +9', odds: -109, prop_player: null, prop_market: null, result: null,
  game: { id: 1, sport: 'nfl', home_team: 'DAL', away_team: 'TB', start_time: '2026-10-09T00:15:00+00:00',
    status: 'scheduled', home_score: null, away_score: null, live_detail: null }, ...over,
})
const t = (over: Partial<Ticket>): Ticket => ({
  kind: 'straight', id: 1, stake: 100, odds: -109, to_win: 91.74, result: null, payout: null,
  created_at: '2026-10-08T22:24:27+00:00', sgp: false, legs: [leg()], ...over,
})
const open = t({ id: 6 })
const sgp = t({ kind: 'parlay', id: 4, odds: 264, to_win: 132, sgp: true,
  legs: [leg({ result: 'win' }), leg({ pick_type: 'over_under', pick_value: 'Over 49' })] })
const won = t({ id: 5, result: 'win', payout: 91.74 })
const lost = t({ id: 3, result: 'loss', payout: -100 })

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={client}><MemoryRouter><MyBets /></MemoryRouter></QueryClientProvider>)
}

beforeEach(() => {
  vi.clearAllMocks()
  useUserStore.setState({ currentUserName: 'Marcus' })
  vi.mocked(api.users.list).mockResolvedValue([me])
  vi.mocked(api.users.bets).mockResolvedValue({
    summary: { available: 9850, balance: 10091.74, open_stakes: 150, today_pl: -8.26 },
    tickets: [open, sgp, won, lost],
  })
})

describe('MyBets', () => {
  it('opens on Open bets with the money strip from the server', async () => {
    renderPage()
    expect(await screen.findByRole('tab', { name: 'Open (2)' })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByLabelText('Your money')).toHaveTextContent('$9,850.00')
    expect(screen.getByLabelText('Your money')).toHaveTextContent('$10,091.74')
    expect(screen.getByLabelText('Your money')).toHaveTextContent('$150.00')
    expect(screen.getByLabelText('Your money')).toHaveTextContent('−$8.26')     // Review Focus 4
    expect(screen.getByRole('article', { name: 'Bet #P-6' })).toHaveTextContent('TB +9')
    expect(screen.getByRole('article', { name: 'Bet #P-6' })).toHaveTextContent('To win $91.74')
    expect(screen.queryByRole('article', { name: 'Bet #P-5' })).not.toBeInTheDocument()
  })

  it('shows a same-game parlay with a dot per leg (Review Focus 3)', async () => {
    renderPage()
    const card = await screen.findByRole('article', { name: 'Bet #PL-4' })
    expect(card).toHaveTextContent('SGP')
    expect(card.querySelectorAll('.sb-dot-won')).toHaveLength(1)
    expect(card.querySelectorAll('.sb-dot-pending')).toHaveLength(1)
  })

  it('lists settled bets with their result, filterable', async () => {
    renderPage()
    fireEvent.click(await screen.findByRole('tab', { name: 'Settled' }))
    const wonCard = screen.getByRole('article', { name: 'Bet #P-5' })
    expect(wonCard).toHaveTextContent('Won +$91.74')
    expect(wonCard).toHaveClass('sb-bet-won')
    expect(screen.getByRole('article', { name: 'Bet #P-3' })).toHaveTextContent('Lost −$100.00')
    fireEvent.click(screen.getByRole('button', { name: 'Won' }))
    expect(screen.queryByRole('article', { name: 'Bet #P-3' })).not.toBeInTheDocument()
  })

  it('says so when there is no player, or no bets (Review Focus 1)', async () => {
    useUserStore.setState({ currentUserName: null })
    const { unmount } = renderPage()
    expect(await screen.findByText(/Choose a player in the top bar/)).toBeInTheDocument()
    unmount()
    useUserStore.setState({ currentUserName: 'Marcus' })
    vi.mocked(api.users.bets).mockResolvedValue({
      summary: { available: 10000, balance: 10000, open_stakes: 0, today_pl: 0 }, tickets: [] })
    renderPage()
    expect(await screen.findByText(/No open bets/)).toBeInTheDocument()
  })
})
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `cd frontend && npx vitest run src/pages/MyBets.test.tsx`

Expected: FAIL, "Failed to resolve import './MyBets'".

- [ ] **Step 3: Write `frontend/src/components/TicketCard.tsx`**

```tsx
import { gameLine, legLabel, legStatus, signedMoney, ticketCode, ticketType } from '../lib/bets'
import { formatMoney } from '../lib/board'
import { formatOdds } from '../lib/quotes'
import type { Ticket } from '../types'

const RESULT: Record<string, string> = { win: 'Won', loss: 'Lost', push: 'Push' }

export default function TicketCard({ t }: { t: Ticket }) {
  const edge = t.result === 'win' ? ' sb-bet-won' : t.result === 'loss' ? ' sb-bet-lost' : ''
  return (
    <article className={`sb-bet${edge}`} aria-label={`Bet ${ticketCode(t)}`}>
      <header className="sb-bet-head">
        <span className="sb-bet-type">{ticketType(t)}</span>
        <span>{ticketCode(t)}</span>
        <span className="sb-bet-time">
          {new Date(t.created_at).toLocaleString('en-US', { dateStyle: 'medium', timeStyle: 'short' })}
        </span>
      </header>
      <ul className="sb-bet-legs">
        {t.legs.map((l, i) => (
          <li key={i} className="sb-bet-leg">
            <span className={`sb-dot sb-dot-${legStatus(l)}`} title={legStatus(l)} />
            <div>
              <strong>{legLabel(l)}</strong>
              <small>{gameLine(l.game)}</small>
            </div>
            <span className="sb-bet-odds">{formatOdds(l.odds)}</span>
          </li>
        ))}
      </ul>
      <footer className="sb-bet-foot">
        <span>Stake {formatMoney(t.stake)}{t.kind === 'parlay' && ` at ${formatOdds(t.odds)}`}</span>
        {t.result === null
          ? <strong>To win {formatMoney(t.to_win)}</strong>
          : <strong>{RESULT[t.result] ?? t.result} {signedMoney(t.payout ?? 0)}</strong>}
      </footer>
    </article>
  )
}
```

- [ ] **Step 4: Write `frontend/src/pages/MyBets.tsx`**

```tsx
import { useState } from 'react'
import TicketCard from '../components/TicketCard'
import { useCurrentPlayer } from '../hooks/useCurrentPlayer'
import { useMyBets } from '../hooks/useMyBets'
import { filterSettled, signedMoney, splitTickets, ticketKey, type SettledFilter } from '../lib/bets'
import { formatMoney } from '../lib/board'

const FILTERS: [SettledFilter, string][] = [['all', 'All'], ['won', 'Won'], ['lost', 'Lost']]

export default function MyBets() {
  const me = useCurrentPlayer()
  const bets = useMyBets(me?.id)
  const [tab, setTab] = useState<'open' | 'settled'>('open')
  const [filter, setFilter] = useState<SettledFilter>('all')

  if (!me) {
    return <div className="sb-bets"><p className="sb-empty">Choose a player in the top bar, or join the league, to see your bets.</p></div>
  }
  const data = bets.data
  const { open, settled } = splitTickets(data?.tickets ?? [])
  const shown = tab === 'open' ? open : filterSettled(settled, filter)
  const pl = data?.summary.today_pl ?? 0

  return (
    <div className="sb-bets">
      {data && (
        <dl className="sb-money" aria-label="Your money">
          <div><dt>Available</dt><dd>{formatMoney(data.summary.available)}</dd></div>
          <div><dt>Settled balance</dt><dd>{formatMoney(data.summary.balance)}</dd></div>
          <div><dt>Open stakes</dt><dd>{formatMoney(data.summary.open_stakes)}</dd></div>
          <div><dt>Today's P/L</dt>
            <dd className={pl > 0 ? 'sb-up' : pl < 0 ? 'sb-down' : undefined}>{signedMoney(pl)}</dd></div>
        </dl>
      )}
      {bets.isError && <div role="alert" className="sb-offline">Couldn't load your bets — retrying.</div>}
      <div className="sb-sport-tabs" role="tablist" aria-label="Bets">
        <button role="tab" className="sb-sport-tab" aria-selected={tab === 'open'} onClick={() => setTab('open')}>
          Open ({open.length})
        </button>
        <button role="tab" className="sb-sport-tab" aria-selected={tab === 'settled'} onClick={() => setTab('settled')}>
          Settled
        </button>
      </div>
      {tab === 'settled' && (
        <div className="sb-filter" role="group" aria-label="Filter settled bets">
          {FILTERS.map(([f, label]) => (
            <button key={f} type="button" aria-pressed={filter === f} onClick={() => setFilter(f)}>{label}</button>
          ))}
        </div>
      )}
      {bets.isLoading && <p className="sb-empty">Loading your bets…</p>}
      {data && shown.length === 0 && (
        <p className="sb-empty">
          {tab === 'open' ? 'No open bets — tap a price in the Lobby to start.' : 'No settled bets here yet.'}
        </p>
      )}
      {shown.map(t => <TicketCard key={ticketKey(t)} t={t} />)}
    </div>
  )
}
```

- [ ] **Step 5: Append the styles to `frontend/src/sportsbook.css`**

```css
/* ─── My Bets (spec §7) ─────────────────────────────────────── */
.sb-bets { max-width: 760px; margin: 0 auto; padding: 0.75rem; }
.sb-money {
  display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 0.5rem; margin: 0 0 0.75rem;
}
.sb-money > div { padding: 0.6rem 0.7rem; border-radius: var(--radius-md); background: var(--bg-surface); border: 1px solid var(--border-default); }
.sb-money dt { color: var(--text-muted); font-size: 0.7rem; font-weight: 700; text-transform: uppercase; letter-spacing: 0.05em; }
.sb-money dd { margin: 0.15rem 0 0; font-weight: 800; font-size: 1.05rem; font-variant-numeric: tabular-nums; }
.sb-up { color: var(--green); }
.sb-down { color: var(--red); }
.sb-filter { display: flex; gap: 0.4rem; margin: 0.25rem 0 0.75rem; }
.sb-filter button {
  min-height: 32px; padding: 0.3rem 0.8rem; border-radius: 999px; border: 1px solid var(--border-strong);
  background: var(--bg-elevated); color: var(--text-secondary); font: inherit; font-size: 0.8rem; font-weight: 700; cursor: pointer;
}
.sb-filter button[aria-pressed="true"] { background: var(--text-primary); color: var(--bg-base); }
.sb-bet {
  margin-bottom: 0.6rem; border-radius: var(--radius-lg); background: var(--bg-surface);
  border: 1px solid var(--border-default); border-left: 4px solid var(--border-strong); overflow: hidden;
}
.sb-bet-won { border-left-color: var(--green); }
.sb-bet-lost { border-left-color: var(--red); }
.sb-bet-head { display: flex; gap: 0.6rem; align-items: center; padding: 0.55rem 0.8rem 0; font-size: 0.75rem; color: var(--text-muted); font-weight: 700; }
.sb-bet-type { color: var(--accent); letter-spacing: 0.05em; text-transform: uppercase; }
.sb-bet-time { margin-left: auto; font-weight: 500; }
.sb-bet-legs { list-style: none; margin: 0; padding: 0.4rem 0.8rem; display: flex; flex-direction: column; gap: 0.45rem; }
.sb-bet-leg { display: flex; gap: 0.6rem; align-items: center; }
.sb-bet-leg > div { flex: 1; min-width: 0; }
.sb-bet-leg strong { display: block; }
.sb-bet-leg small { color: var(--text-muted); font-size: 0.75rem; }
.sb-bet-odds { font-weight: 700; font-variant-numeric: tabular-nums; color: var(--text-secondary); }
.sb-dot { flex: none; width: 10px; height: 10px; border-radius: 50%; background: var(--text-muted); }
.sb-dot-won { background: var(--green); }
.sb-dot-lost { background: var(--red); }
.sb-dot-push { background: var(--yellow); }
.sb-bet-foot {
  display: flex; justify-content: space-between; padding: 0.5rem 0.8rem; border-top: 1px solid var(--border-subtle);
  font-size: 0.85rem; font-variant-numeric: tabular-nums;
}
.sb-bet-won .sb-bet-foot strong { color: var(--green); }
.sb-bet-lost .sb-bet-foot strong { color: var(--red); }
@media (min-width: 900px) { .sb-money { grid-template-columns: repeat(4, minmax(0, 1fr)); } }
```

- [ ] **Step 6: Run the tests and confirm they pass**

Run: `cd frontend && npx vitest run src/pages/MyBets.test.tsx && npx tsc -p tsconfig.app.json --noEmit && npx eslint .`

Expected: all pass.

- [ ] **Step 7: Mutation-check**

Make each change, confirm a test fails, then restore:
1. Default the tab to `'settled'`. The open-bets test must fail.
2. In `TicketCard`, always render `To win`. The settled test must fail.
3. In `MyBets`, show `formatMoney(pl)` instead of `signedMoney(pl)`. The money-strip test must fail, because it expects "−$8.26".

- [ ] **Step 8: Commit**

```bash
git add frontend/src/components/TicketCard.tsx frontend/src/pages/MyBets.tsx frontend/src/pages/MyBets.test.tsx frontend/src/sportsbook.css
git commit -m "feat(bets): My Bets page — open/settled tickets, money strip, leg status dots

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Wire it in — `/bets` route, My Bets badge, celebrations, Leaderboard link

**Files:**
- Modify: `frontend/src/App.tsx` (route), `frontend/src/components/MainTabs.tsx` (path and badge), `frontend/src/components/Layout.tsx` (mount `Celebrations`, open count), `frontend/src/lib/nav.ts` (the Leaderboard link), `frontend/src/sportsbook.css` (badge)
- Test: `frontend/src/components/Layout.test.tsx`

**Interfaces:**
- **Consumes:** `MyBets` (Task 4), `Celebrations` (Task 3), `useCurrentPlayer` / `useMyBets` / `splitTickets` (Task 2).
- **Produces:**
  - **Routes:** `/bets` is My Bets; `/paper-trading` stays.
  - **Props:** `MainTabs` takes `openCount: number`.
  - **Links:** `RESEARCH_LINKS` gains `['/paper-trading', 'Leaderboard']`.

- [ ] **Step 1: Update and add the Layout tests**

In `frontend/src/components/Layout.test.tsx`:
1. Add `bets: vi.fn()` to the mocked `users` object.
2. In `beforeEach`, after the `list` mock, add:

```tsx
    vi.mocked(api.users.bets).mockResolvedValue({
      summary: { available: 10240.5, balance: 10240.5, open_stakes: 0, today_pl: 0 }, tickets: [] })
```

3. Change the My Bets assertion in `'has Lobby and My Bets tabs and the METRIC EDGE wordmark'` to `toHaveAttribute('href', '/bets')`.
4. Add:

```tsx
  it("badges My Bets with the player's open count", async () => {
    const open = { kind: 'straight' as const, id: 1, stake: 10, odds: -110, to_win: 9.09, result: null, payout: null,
      created_at: '2026-10-08T22:00:00+00:00', sgp: false, legs: [] }
    vi.mocked(api.users.bets).mockResolvedValue({
      summary: { available: 1, balance: 1, open_stakes: 20, today_pl: 0 },
      tickets: [open, { ...open, id: 2 }, { ...open, id: 3, result: 'win', payout: 9.09 }] })
    renderAt('/')
    expect((await screen.findAllByLabelText('2 open'))[0]).toHaveTextContent('2')
  })

  it('lists the Leaderboard under Research until Phase 5 builds /leaders', () => {
    renderAt('/')
    fireEvent.click(screen.getAllByRole('button', { name: /Research/ })[0])
    expect(screen.getByRole('link', { name: 'Leaderboard' })).toHaveAttribute('href', '/paper-trading')
  })
```

Run: `cd frontend && npx vitest run src/components/Layout.test.tsx`

Expected: the three changed or new tests FAIL.

- [ ] **Step 2: `MainTabs` — `/bets` and the badge**

Replace `frontend/src/components/MainTabs.tsx` with:

```tsx
import { NavLink } from 'react-router-dom'

const TABS = [
  { path: '/', label: 'Lobby', icon: '🏟️' },
  { path: '/bets', label: 'My Bets', icon: '🎟️' },
]

export default function MainTabs({ className, researchActive, onResearch, openCount = 0 }: {
  className: string; researchActive: boolean; onResearch: () => void; openCount?: number
}) {
  return (
    <nav className={className} aria-label="Main navigation">
      {TABS.map(t => (
        <NavLink key={t.path} to={t.path} end={t.path === '/'}
          className={({ isActive }) => `sb-tab ${isActive ? 'active' : ''}`}>
          <span aria-hidden="true">{t.icon}</span><span>{t.label}</span>
          {t.path === '/bets' && openCount > 0 && (
            <span className="sb-badge" aria-label={`${openCount} open`}>{openCount}</span>
          )}
        </NavLink>
      ))}
      <button type="button" className={`sb-tab ${researchActive ? 'active' : ''}`} onClick={onResearch}>
        <span aria-hidden="true">📚</span><span>Research</span>
      </button>
    </nav>
  )
}
```

Append this to `frontend/src/sportsbook.css`:

```css
.sb-tab { position: relative; }
.sb-badge {
  display: inline-grid; place-items: center; min-width: 1.15rem; height: 1.15rem; padding: 0 0.25rem;
  border-radius: 999px; background: var(--red); color: #fff; font-size: 0.65rem; font-weight: 800;
}
.sb-tabbar .sb-badge { position: absolute; top: 6px; left: calc(50% + 6px); }
.sb-topnav .sb-badge { margin-left: 0.35rem; }
```

- [ ] **Step 3: `Layout` — open count and celebrations; route; Research link**

In `frontend/src/components/Layout.tsx`:
1. Add the imports: `import Celebrations from './Celebrations'`, `import { useCurrentPlayer } from '../hooks/useCurrentPlayer'`, `import { useMyBets } from '../hooks/useMyBets'` and `import { splitTickets } from '../lib/bets'`.
2. Inside `Layout`, after `slipVisible`, add:

```tsx
  const me = useCurrentPlayer()
  const myBets = useMyBets(me?.id)
  const openCount = splitTickets(myBets.data?.tickets ?? []).open.length
```

3. Pass `openCount={openCount}` to both `<MainTabs ... />`.
4. Render `<Celebrations />` right after `<BetSlip />`.

In `frontend/src/App.tsx`, add `import MyBets from './pages/MyBets';` and the route `<Route path="bets" element={<MyBets />} />` after `game/:id`.

In `frontend/src/lib/nav.ts`, add `['/paper-trading', 'Leaderboard'],` after the `'/track-record'` entry, with the comment `// Until Phase 5's /leaders (then /paper-trading redirects to /bets).`

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `cd frontend && npx vitest run src/components/Layout.test.tsx && npm test && npx tsc -p tsconfig.app.json --noEmit && npx eslint .`

Expected: all pass, and the whole suite passes.

- [ ] **Step 5: Mutation-check**

Make each change, confirm a test fails, then restore:
1. In `Layout`, pass `openCount={0}`. The badge test must fail.
2. In `MainTabs`, change the My Bets path back to `/paper-trading`. The tab test must fail.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/App.tsx frontend/src/components/MainTabs.tsx frontend/src/components/Layout.tsx frontend/src/components/Layout.test.tsx frontend/src/lib/nav.ts frontend/src/sportsbook.css
git commit -m "feat(nav): My Bets at /bets with an open-count badge; win banner on every page

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Slip follow-ups from the Phase 2 review

**Files:**
- Modify: `frontend/src/components/BetSlip.tsx`
- Test: `frontend/src/components/BetSlip.test.tsx`

**Interfaces:**
- **Consumes:** the existing `BetSlip` and `BetReceipt`.
- **Produces:**
  - Board and quote prices refresh after every placement attempt (`invalidateQueries({ queryKey: ['paper'] })`).
  - The receipt's "couldn't be placed" count covers only refused or moved legs.

- [ ] **Step 1: Write the failing tests**

In `frontend/src/components/BetSlip.test.tsx`, change `renderSlip` to accept a client:

```tsx
function renderSlip(client = new QueryClient({ defaultOptions: { queries: { retry: false } } })) {
  return render(<QueryClientProvider client={client}><BetSlip /></QueryClientProvider>)
}
```

Add:

```tsx
  it('refreshes board prices after a placement attempt, so a moved price shows on its tile', async () => {
    useSlip.setState({ legs: [ml(1, 'H1 ML')] })
    vi.mocked(api.users.placePick).mockRejectedValueOnce(new ApiError(409, { detail: { reason: 'price_moved',
      message: 'm', odds: -125, line: null, pick_value: 'HOME ML' } }))
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const spy = vi.spyOn(client, 'invalidateQueries')
    renderSlip(client)
    fireEvent.click(await enabledButton('Place Bet'))
    await screen.findByText(/Odds changed/)
    expect(spy).toHaveBeenCalledWith({ queryKey: ['paper'] })
  })
```

In `'skips a leg whose game has started (Review Focus 3)'`, after `await screen.findByText('#P-12')`, add:

```tsx
    expect(screen.queryByText(/couldn't be placed/)).not.toBeInTheDocument()   // never attempted
```

Run: `cd frontend && npx vitest run src/components/BetSlip.test.tsx`

Expected: both FAIL.

- [ ] **Step 2: Implement**

In `BetSlip.tsx`'s `handlePlace`, after `queryClient.invalidateQueries({ queryKey: ['users'] })`, add:

```tsx
      // Board and quote prices: a moved price must show on its tile too.
      queryClient.invalidateQueries({ queryKey: ['paper'] })
```

Replace `<BetReceipt receipt={s.receipt} remaining={s.legs.length} />` with:

```tsx
<BetReceipt receipt={s.receipt} remaining={s.legs.filter(l => l.error || l.movedFrom).length} />
```

- [ ] **Step 3: Run the tests and confirm they pass**

Run: `cd frontend && npx vitest run src/components/BetSlip.test.tsx && npx tsc -p tsconfig.app.json --noEmit && npx eslint .`

Expected: all pass, including the partial-failure test, whose refused leg still counts.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/BetSlip.tsx frontend/src/components/BetSlip.test.tsx
git commit -m "fix(slip): refresh board prices after placing; receipt counts only refused legs

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Verify end to end in a worktree, then hand off for merge

**Files:** none are created.

- [ ] **Step 1: Run the full suites**

```bash
cd /c/Users/mwill/Documents/mwilliams2733/sports_picks
.venv/Scripts/python -m pytest -q -p no:warnings
cd frontend && npm test && npx tsc -p tsconfig.app.json --noEmit && npx eslint .
```

Expected:
- The backend gives 2318, the Phase 2 merge baseline, plus 8 new tests, with 0 failures.
- The frontend passes, clean.

- [ ] **Step 2: Build and serve the branch from a worktree**

Same as Phase 2:
1. `git worktree add --detach "$WT" HEAD`, then `npm ci --legacy-peer-deps` and `npm run build` in `$WT/frontend`.
2. Take a db snapshot with the sqlite backup API.
3. Run uvicorn on :8001 from `$WT`, using the main `.venv` python with `DATABASE_PATH=<snapshot>` and `ENABLE_SCHEDULER=0`.
4. Confirm that :8001 serves the worktree's `index-*.js`, that :8000 is unchanged, and that `GET /users/3/bets` on :8001 returns Claude's tickets.

- [ ] **Step 3: Check it in Chrome at phone (390px iframe) and desktop width, on the snapshot only**

1. Choose player "Claude" in the chip. No PIN is needed to view. Then check:
   1. My Bets opens on Open with the money strip.
   2. The tab carries its badge.
   3. Settled shows TB +9 and the earlier tickets with green or red edges and status dots; the filters work.
2. **Celebration:**
   1. Load My Bets once. Nothing celebrates, because it's a first visit.
   2. In the **snapshot**, set one of Claude's open straight bets to `result='win', payout=91.74` with `sqlite3`.
   3. Wait for the 60 s refetch, or refocus the page. The WINNER banner should show once, and not again after a reload.
3. **Slip:** a price-moved attempt refreshes the board tile. Test it the same way as in Phase 2, editing the snapshot's odds.
4. **Leaderboard:** Research → Leaderboard opens the old page.
5. Save screenshots.

- [ ] **Step 4: Clean up the worktree**

1. Stop :8001.
2. Run `git worktree remove --force "$WT"`. If that fails on long paths, use `Remove-Item -LiteralPath "\\?\<path>" -Recurse -Force` and `git worktree prune`.
3. Confirm `git worktree list` shows only the main tree.

- [ ] **Step 5: Report to the owner and wait for "merge"**

Do not merge or restart until the owner says so.

- [ ] **Step 6: Merge and restart, on the owner's OK**

```bash
git checkout master
git merge --no-ff feat/sportsbook-phase-3 -m "Merge: sportsbook phase 3 — My Bets

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
npm --prefix frontend run build
```

1. **Restart uvicorn on :8000 straight away,** the same way as in Phase 2: stop the two `--port 8000` processes, move `app.log` to `app.log.prev`, then `Start-Process` with `ENABLE_SCHEDULER=0` and `DATABASE_PATH`.
2. **Restart the scheduler too.** This phase changed `paper_settlement.py`, which the scheduler runs.
   1. Do it outside any window's ±30 minutes.
   2. Check `scheduler.log` for the next scheduled window first.
   3. Save `scheduler.log.prev` aside.
   4. Stop both scheduler processes.
   5. Start with `Start-Process powershell -ArgumentList '-NoProfile','-File','scripts\start_scheduler.ps1' -WindowStyle Hidden`, never piped (memory `sports-picks-scheduler-operations`).
   6. Confirm today's windows were re-scheduled in the new log.
3. **Confirm with real calls:**
   1. `GET /users/3/bets` on :8000 returns tickets.
   2. The tunnel URL returns 200 and serves the new `index-*.js`.
4. **Update memory:** `sports-picks-sportsbook-ui.md` → "Phase 3 merged <sha>".

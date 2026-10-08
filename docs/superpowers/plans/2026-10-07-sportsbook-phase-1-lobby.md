# Sportsbook Phase 1 — Theme, Navigation, Lobby — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace Today's Picks as the home screen with a sportsbook Lobby.
The Lobby shows sport tabs, game cards with tappable odds tiles, a Model Picks
strip and a per-game page. It comes with the new dark/green theme, a top bar
with a player chip, and a phone tab bar. It is fed by one new read-only
endpoint, `GET /paper/board`.

**Architecture:**
- **Backend:** `backend/paper/board.py` lists bettable games and prices them
  through the existing `pricing.game_quotes` / `pricing.prop_quotes`, so the
  board price and the bet price can never differ. `backend/api/paper.py`
  exposes it.
- **Frontend:** pure helpers go in `src/lib/board.ts` and are unit-tested.
  Presentational components (`OddsTile`, `BoardGameCard`, `ModelPicksStrip`)
  sit on top of them, and two pages (`Lobby`, `GameDetail`) use those.
- **Placing a bet:** in this phase, tapping a tile opens the existing
  `BetModal`. Phase 2 replaces that with the slip.

**Tech Stack:** FastAPI, SQLAlchemy, pytest; React 19, react-router 7,
@tanstack/react-query 5, zustand 5, vitest and Testing Library; plain CSS.

**Spec:** `docs/superpowers/specs/2026-10-07-sportsbook-ui-design.md`. This plan
implements §3, §4, §5 and the Phase 1 row of §13.

## Global Constraints

- Every price shown comes from `pricing.game_quotes` / `pricing.prop_quotes`.
  No other code may compute a price.
- A refused side is shown as a locked tile (🔒) carrying the server's
  `message`. A refused side is never hidden and never given a made-up price.
- Branding is "METRIC EDGE" only. No DraftKings or FanDuel names, logos or
  exact colours.
- The colours are:
  - base `#0d0f12`
  - surface `#181b20`
  - elevated `#22262d`
  - accent `#2fe37a`, with `#0d0f12` text on it
  - loss `#ff4d5e`
  - live `#ffb020`
  - muted text `#8a93a3`
- The board covers today through `days` days ahead, ET. `days` defaults to 7
  and is clamped to 1..14.
- The board refetches every 60 s and on window focus.
- Phone layout is below 900px, desktop is 900px and up. Animations are off
  under `prefers-reduced-motion`.
- Styles go in CSS files imported from `src/main.tsx`; `App.css` is never
  imported.
- **Route-name deviation from spec §4, decided while planning:** Model Picks
  lives at **`/model-picks`**, not `/picks`. FastAPI mounts the API at
  `/picks` before the SPA catch-all (`backend/api/main.py:105,140`), so a
  page at `/picks` would get API JSON on reload.
- The `/paper-trading` page stays as it is in this phase. The **My Bets** tab
  points to it until Phase 3 adds `/bets`. The **Leaders** tab arrives in
  Phase 5.
- Commit messages end with
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Never stage `config.yaml`, which carries the owner's uncommitted recipient
  edit. Stage only the named files.

## Review Focus

1. **A game with no `start_time`.** It must be listed under its ET date, show
   "TBD", and sort after timed games on that date without crashing. Tests are
   in Task 1 (ordering) and Task 2 (`startLabel`).
2. **Every side refused.** A game whose prices are all stale is still listed,
   with every tile locked and showing the reason. It is never silently
   dropped. Tests are in Task 1 (game present with refusals) and Task 4.
3. **A board refetch fails while old data is on screen.** React Query keeps
   the old data, so the banner must show and every tile must lock, rather
   than leaving an old price tappable. Test is in Task 5.
4. **A model pick whose stored label can't be mapped to a side**
   (`legFromPick` returns null), **or whose side is currently refused.** The
   first shows no chip; the second shows a locked chip; neither crashes.
   Tests are in Task 2 and Task 5.
5. **The ET day boundary.** A game at 11:30 pm ET on Oct 10 is 03:30Z on
   Oct 11. It belongs under its stored ET `date`, and "Today" means today in
   ET, not in UTC or the browser's zone. Test is in Task 2 (`etToday`).

---

### Task 1: `GET /paper/board`

**Files:**
- Create: `backend/paper/board.py`
- Modify: `backend/api/paper.py` (add the route)
- Test: `backend/tests/test_paper_board.py`

**Interfaces:**
- Consumes: `pricing.open_for_betting(game, now)`,
  `pricing.game_quotes(session, game, now)`,
  `pricing.prop_quotes(session, game, now)`, `PickModel.published()`,
  `PickModel.by_model()`, and `time_utils.game_start_utc`, `ET`.
- Produces:
  - `build_board(session, *, sport: str | None = None, days: int = 7, now: datetime | None = None) -> list[dict]`
  - `clamp_days(days: int) -> int`
  - **HTTP:** `GET /paper/board?sport=&days=` returns `{"games": [BoardGame]}`.
  - **`BoardGame`:** `{id, sport, date: "YYYY-MM-DD", start_time: ISO-with-offset | null, home_team, away_team, quotes: <game_quotes list>, prop_count: int, model_pick: {pick_type, pick_value, odds, edge_pct} | null}`

- [ ] **Step 1: Write the failing tests**

```python
"""The sportsbook board, GET /paper/board (spec 2026-10-07 §5)."""
import itertools
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from backend.api.main import create_app
from backend.database import get_session
from backend.models import (WEATHER_RAIN_UNDER_STRATEGY_ID, Base, Game, PickModel, PlayerProp,
                            Strategy, Team)
from backend.tests.auth_helpers import ALL_HEADERS
from backend.tests.pricing_helpers import seed_fresh_odds, seed_fresh_prop
from backend.time_utils import et_today

_n = itertools.count()


def _client(headers=ALL_HEADERS):
    client = TestClient(create_app(":memory:"), headers=headers)
    Base.metadata.create_all(client.app.state.engine)
    return client


def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _game(client, *, sport="nfl", day_offset=0, start=None, status="scheduled"):
    s = get_session(client.app.state.engine)
    i = next(_n)
    h = Team(name=f"Home{i}", abbreviation=f"Home{i}", sport=sport)
    a = Team(name=f"Away{i}", abbreviation=f"Away{i}", sport=sport)
    s.add_all([h, a])
    s.flush()
    g = Game(sport=sport, season="2026", date=et_today() + timedelta(days=day_offset),
             start_time=start, home_team_id=h.id, away_team_id=a.id, status=status)
    s.add(g)
    s.commit()
    gid = g.id
    s.close()
    return gid


def _ids(client, query=""):
    r = client.get(f"/paper/board{query}")
    assert r.status_code == 200
    return [g["id"] for g in r.json()["games"]]


def test_board_quotes_are_exactly_the_bet_quotes():
    client = _client()
    gid = _game(client)
    seed_fresh_odds(client.app.state.engine, gid)
    [game] = client.get("/paper/board").json()["games"]
    assert game["quotes"] == client.get(f"/paper/quotes?game_id={gid}").json()["quotes"]
    s = get_session(client.app.state.engine)
    expected_home = s.get(Team, s.get(Game, gid).home_team_id).abbreviation
    s.close()
    assert (game["home_team"], game["sport"]) == (expected_home, "nfl")


def test_an_unpriced_game_is_listed_with_every_side_refused():
    client = _client()
    gid = _game(client)                                    # no odds at all
    [game] = client.get("/paper/board").json()["games"]
    assert game["id"] == gid
    assert len(game["quotes"]) == 6
    assert all(q["available"] is False and q["message"] for q in game["quotes"])


def test_started_and_finished_games_are_off_the_board():
    client = _client()
    open_ = _game(client, start=_utcnow() + timedelta(hours=2))
    _game(client, start=_utcnow() - timedelta(hours=1))   # kicked off
    _game(client, status="final")
    _game(client, status="in_progress")
    assert _ids(client) == [open_]


def test_days_window_is_today_onward_and_clamped_to_1_through_14():
    client = _client()
    _game(client, day_offset=-1)                           # yesterday, never shown
    g0 = _game(client, day_offset=0)
    g3 = _game(client, day_offset=3)
    g10 = _game(client, day_offset=10)
    _game(client, day_offset=20)
    assert _ids(client) == [g0, g3]                        # default 7
    assert _ids(client, "?days=99") == [g0, g3, g10]       # clamped to 14
    assert _ids(client, "?days=0") == [g0]                 # clamped to 1


def test_order_is_date_then_kickoff_with_unknown_kickoff_last():
    client = _client()
    late = _game(client, start=_utcnow() + timedelta(hours=5))
    tbd = _game(client, start=None)
    early = _game(client, start=_utcnow() + timedelta(hours=2))
    tomorrow = _game(client, day_offset=1, start=_utcnow() + timedelta(hours=1))
    games = client.get("/paper/board").json()["games"]
    assert [g["id"] for g in games] == [early, late, tbd, tomorrow]
    by_id = {g["id"]: g for g in games}
    assert by_id[tbd]["start_time"] is None
    assert by_id[early]["start_time"].endswith("+00:00")   # UTC, offset explicit


def test_sport_filter():
    client = _client()
    nfl = _game(client, sport="nfl")
    _game(client, sport="nba")
    assert _ids(client, "?sport=nfl") == [nfl]


def test_model_pick_is_the_highest_edge_published_model_game_pick():
    client = _client()
    gid = _game(client)
    other = _game(client)
    s = get_session(client.app.state.engine)
    s.add_all([Strategy(id=1, name="ensemble", config_json="{}"),
               Strategy(id=WEATHER_RAIN_UNDER_STRATEGY_ID, name="rain", config_json="{}")])
    s.flush()

    def pick(ptype, value, edge, **kw):
        s.add(PickModel(game_id=gid, strategy_id=kw.pop("strategy_id", 1), pick_type=ptype,
                        pick_value=value, confidence=3, edge_pct=edge, odds_at_pick=-110, **kw))

    pick("moneyline", "HOME ML", 3.0)
    pick("over_under", "Over 47.5", 4.0)
    pick("spread", "AWAY +3.5", 5.0, tracking_only=True)
    pick("spread", "HOME -3.5", 7.0, withdrawn_at=_utcnow())
    pick("over_under", "Under 47.5", 8.0, strategy_id=WEATHER_RAIN_UNDER_STRATEGY_ID)
    pick("prop", "QB Over 225.5", 9.0, prop_player="QB", prop_market="player_pass_yds")
    s.commit()
    s.close()
    by_id = {g["id"]: g for g in client.get("/paper/board").json()["games"]}
    assert by_id[gid]["model_pick"] == {"pick_type": "over_under", "pick_value": "Over 47.5",
                                        "odds": -110, "edge_pct": 4.0}
    assert by_id[other]["model_pick"] is None


def test_prop_count_counts_only_priced_props():
    client = _client()
    gid = _game(client)
    engine = client.app.state.engine
    seed_fresh_prop(engine, gid, outcome="Over")
    seed_fresh_prop(engine, gid, outcome="Under")
    s = get_session(engine)
    s.add(PlayerProp(game_id=gid, bookmaker="testbook", market="player_pass_yds",
                     player_name="Stale QB", outcome="Over", line=200.5, odds=-110,
                     fetched_at=_utcnow() - timedelta(hours=7)))
    s.commit()
    s.close()
    [game] = client.get("/paper/board").json()["games"]
    assert game["prop_count"] == 2


def test_board_is_an_open_read():
    client = _client(headers={})                           # no owner key, no PIN
    _game(client)
    assert client.get("/paper/board").status_code == 200
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `.venv/Scripts/python -m pytest backend/tests/test_paper_board.py -q`
Expected: every test fails with 404 (the route does not exist).

- [ ] **Step 3: Write `backend/paper/board.py`**

```python
"""The sportsbook lobby's board: every bettable game in a date window, priced.

Each side is priced by ``pricing.game_quotes`` -- the function a bet is
charged by -- so the board and the bet cannot disagree. A game whose sides
are all refused (stale, unquoted) is still listed: the lobby shows its tiles
locked with the refusal message rather than hiding it.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import aliased

from backend.models import Game, PickModel, Team
from backend.paper import pricing
from backend.time_utils import ET, game_start_utc

MAX_DAYS = 14
GAME_PICK_TYPES = ("moneyline", "spread", "over_under")
_NO_KICKOFF = datetime.max.replace(tzinfo=timezone.utc)


def clamp_days(days: int) -> int:
    return max(1, min(MAX_DAYS, days))


def _model_picks(session, game_ids: list[int]) -> dict[int, dict]:
    """The highest-edge published model pick on a game market, per game."""
    if not game_ids:
        return {}
    rows = (session.query(PickModel)
            .filter(PickModel.game_id.in_(game_ids), PickModel.published(),
                    PickModel.by_model(), PickModel.pick_type.in_(GAME_PICK_TYPES))
            .order_by(PickModel.edge_pct.desc(), PickModel.id)
            .all())
    best: dict[int, dict] = {}
    for p in rows:
        best.setdefault(p.game_id, {"pick_type": p.pick_type, "pick_value": p.pick_value,
                                    "odds": p.odds_at_pick, "edge_pct": p.edge_pct})
    return best


def build_board(session, *, sport: str | None = None, days: int = 7,
                now: datetime | None = None) -> list[dict]:
    now = now or datetime.now(timezone.utc)
    first = now.astimezone(ET).date()
    last = first + timedelta(days=clamp_days(days))
    Away = aliased(Team)
    q = (session.query(Game, Team, Away)
         .join(Team, Game.home_team_id == Team.id)
         .join(Away, Game.away_team_id == Away.id)
         .filter(Game.date >= first, Game.date < last))
    if sport:
        q = q.filter(Game.sport == sport)
    rows = [(g, h, a) for g, h, a in q.all() if pricing.open_for_betting(g, now)]
    rows.sort(key=lambda r: (r[0].date, game_start_utc(r[0]) or _NO_KICKOFF, r[0].id))
    picks = _model_picks(session, [g.id for g, _, _ in rows])
    board = []
    for game, home, away in rows:
        start = game_start_utc(game)
        board.append({
            "id": game.id,
            "sport": game.sport,
            "date": str(game.date),
            "start_time": start.isoformat() if start else None,
            "home_team": home.abbreviation,
            "away_team": away.abbreviation,
            "quotes": pricing.game_quotes(session, game, now),
            "prop_count": sum(1 for p in pricing.prop_quotes(session, game, now)
                              if p["available"]),
            "model_pick": picks.get(game.id),
        })
    return board
```

- [ ] **Step 4: Add the route in `backend/api/paper.py`**

Add `from backend.paper import board as board_mod` beside the existing
`from backend.paper import pricing`. Then append:

```python
@router.get("/board")
def board(request: Request, sport: str | None = None, days: int = 7):
    """Every game open for betting from today through ``days`` days ahead (ET,
    clamped 1..14), each side priced or refused exactly as a bet would be."""
    session = get_session(request.app.state.engine)
    try:
        return {"games": board_mod.build_board(session, sport=sport, days=days)}
    finally:
        session.close()
```

- [ ] **Step 5: Run the tests and confirm they pass**

Run: `.venv/Scripts/python -m pytest backend/tests/test_paper_board.py backend/tests/test_paper_quotes_api.py -q`
Expected: all pass.

- [ ] **Step 6: Mutation-check the guards**

Make each mutation, run the board tests, confirm at least one test fails,
then restore. After every write and every restore, delete
`backend/paper/__pycache__/board.*.pyc`; see the memory note
`sports-picks-mutation-pyc-trap`.
1. Remove `if pricing.open_for_betting(g, now)`. Expect the "started" test to
   fail.
2. Change `clamp_days` to `return days`. Expect the window test to fail.
3. Drop `PickModel.by_model()`. Expect the model-pick test to fail.
4. Replace `game_start_utc(r[0]) or _NO_KICKOFF` with
   `game_start_utc(r[0]) or datetime.min.replace(tzinfo=timezone.utc)`.
   Expect the order test to fail.
5. Count all prop quotes, not only the available ones. Expect the prop_count
   test to fail.

- [ ] **Step 7: Measure the board's cost on a live-db snapshot**

The spec requires a cheaper `prop_count` if a full board takes more than 1 s.

```bash
cd /c/Users/mwill/Documents/mwilliams2733/sports_picks
SNAP="$TMPDIR/board-snapshot.db"
.venv/Scripts/python -c "import sqlite3,sys; s=sqlite3.connect('sports_picks.db'); d=sqlite3.connect(sys.argv[1]); s.backup(d); d.close()" "$SNAP"
.venv/Scripts/python - "$SNAP" <<'EOF'
import sys, time
from sqlalchemy import create_engine
from backend.database import get_session
from backend.paper.board import build_board
s = get_session(create_engine(f"sqlite:///{sys.argv[1]}"))
for days in (1, 7):
    t = time.perf_counter(); b = build_board(s, days=days); dt = time.perf_counter() - t
    print(f"days={days} games={len(b)} props={sum(g['prop_count'] for g in b)} seconds={dt:.2f}")
EOF
```

- **If `days=7` is at or below 1.0 s:** keep the code and record the numbers
  in the commit message.
- **If it is above 1.0 s:**
  1. Set `"prop_count": None` in `build_board`.
  2. Change the test to `assert game["prop_count"] is None`.
  3. Record the measured time in the commit message.

  The frontend (Task 4) already shows "Props ›" with no count when the value
  is null.

Sample both ends: run it once on a Sunday-sized slate if the snapshot has
one. If not, state in the commit message that only the current slate was
measured.

- [ ] **Step 8: Commit**

```bash
git checkout -b feat/sportsbook-phase-1
git add backend/paper/board.py backend/api/paper.py backend/tests/test_paper_board.py
git commit -m "feat(paper): GET /paper/board — every bettable game, priced like a bet

<measured timing line from Step 7>

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Board types, API call, hook and pure helpers

**Files:**
- Modify: `frontend/src/types.ts` (append the types)
- Modify: `frontend/src/api/client.ts` (the `paper` block)
- Create: `frontend/src/hooks/useBoard.ts`
- Create: `frontend/src/lib/board.ts`
- Test: `frontend/src/lib/board.test.ts`

**Interfaces:**
- Consumes: `GET /paper/board` (Task 1), and from `lib/quotes.ts`:
  `formatOdds`, `legFromPick`, `resolveQuoteLabel`.
- Produces:
  - **Types:**
    - `BoardGame` and `ModelPick` (types)
    - `BetTarget`: exactly `BetModal`'s props minus `open` / `onClose`
    - `PriceMove = 'better' | 'worse' | 'moved' | null`
    - `PropRow = { player, line, over?: PropQuote, under?: PropQuote }`
  - **API and hook:** `api.paper.board(): Promise<{ games: BoardGame[] }>` and
    `useBoard()` (react-query, key `['paper','board']`)
  - **Display helpers:**
    - `etToday(now: Date): string`
    - `formatDay(date: string): string`
    - `startLabel(iso: string | null): string`
    - `formatMoney(n: number): string`
  - **Board helpers:**
    - `groupByDay(games, today): { date, label, games }[]`
    - `sportTabs(games): string[]`
    - `findGameQuote(quotes, pickType, side): GameQuote | undefined`
    - `tileTop(q: AvailableGameQuote): string | null`
    - `priceMove(prev, next): PriceMove`
    - `groupProps(quotes: PropQuote[]): [marketLabel, PropRow[]][]`
  - **Bet-target builders:**
    - `gameTarget(game, q: AvailableGameQuote, edgePct?)`
    - `propTarget(gameId, q: AvailablePropQuote)`
    - `modelPickQuote(game): GameQuote | null`

- [ ] **Step 1: Append the types to `frontend/src/types.ts`**

```ts
export interface ModelPick { pick_type: GamePickType; pick_value: string; odds: number | null; edge_pct: number }

export interface BoardGame {
  id: number; sport: string; date: string; start_time: string | null;
  home_team: string; away_team: string; quotes: GameQuote[];
  prop_count: number | null; model_pick: ModelPick | null;
}

/** What BetModal needs to open on one side -- its props minus open/onClose. */
export interface BetTarget {
  pickType: string; pickValue: string; betValue: string; odds: number; gameId: number;
  homeTeam: string; awayTeam: string; edgePct?: number; propMarket?: string; propPlayer?: string;
}
```

- [ ] **Step 2: Add the API call and the hook**

In `frontend/src/api/client.ts`, add `BoardGame` to the `../types` import, then
add this inside `paper: { ... }` after `propQuotes`:

```ts
    board: () => get<{ games: BoardGame[] }>('/paper/board?days=7'),
```

Create `frontend/src/hooks/useBoard.ts`:

```ts
import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'

// Same cadence as useQuotes: a price on screen is never far from the one the
// server will charge. `retry: false` so an unreachable server shows as
// offline at once instead of after silent retries.
export function useBoard() {
  return useQuery({
    queryKey: ['paper', 'board'],
    queryFn: api.paper.board,
    refetchInterval: 60_000,
    refetchOnWindowFocus: true,
    retry: false,
  })
}
```

- [ ] **Step 3: Write the failing tests, `frontend/src/lib/board.test.ts`**

```ts
import { describe, it, expect } from 'vitest'
import type { AvailableGameQuote, BoardGame, GameQuote, PropQuote } from '../types'
import { etToday, formatDay, formatMoney, gameTarget, groupByDay, groupProps, modelPickQuote,
  priceMove, propTarget, sportTabs, startLabel, tileTop, findGameQuote } from './board'

const q = (pick_type: string, side: string, extra: Partial<AvailableGameQuote> = {}): GameQuote => ({
  pick_type, side, available: true, pick_value: `${side} x`, odds: -110, line: null,
  quoted_at: '2026-10-10T12:00:00+00:00', prop_player: null, prop_market: null, ...extra,
} as GameQuote)

const game = (over: Partial<BoardGame> = {}): BoardGame => ({
  id: 1, sport: 'nfl', date: '2026-10-11', start_time: '2026-10-11T17:00:00+00:00',
  home_team: 'Buccaneers', away_team: 'Cowboys', prop_count: 3, model_pick: null,
  quotes: [
    q('spread', 'HOME', { line: -3, pick_value: 'HOME -3' }),
    q('spread', 'AWAY', { line: 3, pick_value: 'AWAY +3' }),
    q('moneyline', 'HOME', { odds: -160, pick_value: 'HOME ML' }),
    q('moneyline', 'AWAY', { odds: 135, pick_value: 'AWAY ML' }),
    q('over_under', 'Over', { line: 47.5, pick_value: 'Over 47.5' }),
    { pick_type: 'over_under', side: 'Under', available: false, reason: 'stale',
      message: 'The price is stale — ask Marcus to refresh.' },
  ],
  ...over,
})

describe('etToday', () => {
  it('is the ET date, not the UTC date, across midnight', () => {
    expect(etToday(new Date('2026-10-11T03:30:00Z'))).toBe('2026-10-10')
    expect(etToday(new Date('2026-10-11T16:00:00Z'))).toBe('2026-10-11')
  })
})

describe('formatDay / groupByDay', () => {
  it('formats a date as weekday month day', () => {
    expect(formatDay('2026-10-11')).toBe('Sun Oct 11')
  })
  it('labels today "Today" and keeps board order within a day', () => {
    const a = game({ id: 1, date: '2026-10-10' })
    const b = game({ id: 2, date: '2026-10-11' })
    const c = game({ id: 3, date: '2026-10-11' })
    expect(groupByDay([a, b, c], '2026-10-10')).toEqual([
      { date: '2026-10-10', label: 'Today', games: [a] },
      { date: '2026-10-11', label: 'Sun Oct 11', games: [b, c] },
    ])
  })
})

describe('startLabel', () => {
  it('is TBD with no kickoff', () => expect(startLabel(null)).toBe('TBD'))
  it('is a clock time otherwise', () => expect(startLabel('2026-10-11T17:00:00+00:00')).toMatch(/\d{1,2}:\d{2}\s?[AP]M/))
})

describe('formatMoney', () => {
  it('uses dollars, thousands separators and cents', () => expect(formatMoney(10240.5)).toBe('$10,240.50'))
})

describe('sportTabs', () => {
  it('lists each sport once, in board order', () => {
    expect(sportTabs([game({ sport: 'nba' }), game({ sport: 'nfl' }), game({ sport: 'nba' })]))
      .toEqual(['nba', 'nfl'])
  })
})

describe('tileTop', () => {
  const g = game()
  const avail = (pt: string, side: string) => findGameQuote(g.quotes, pt as never, side as never) as AvailableGameQuote
  it('signs a spread line', () => {
    expect(tileTop(avail('spread', 'AWAY'))).toBe('+3')
    expect(tileTop(avail('spread', 'HOME'))).toBe('-3')
  })
  it('prefixes a total with O/U', () => expect(tileTop(avail('over_under', 'Over'))).toBe('O 47.5'))
  it('has no top line for a moneyline', () => expect(tileTop(avail('moneyline', 'HOME'))).toBeNull())
})

describe('priceMove', () => {
  it('is null with nothing to compare or no change', () => {
    expect(priceMove(null, { odds: -110, line: 3 })).toBeNull()
    expect(priceMove({ odds: -110, line: 3 }, { odds: -110, line: 3 })).toBeNull()
  })
  it('higher American odds are better for the bettor', () => {
    expect(priceMove({ odds: -110, line: null }, { odds: -105, line: null })).toBe('better')
    expect(priceMove({ odds: 130, line: null }, { odds: 120, line: null })).toBe('worse')
  })
  it('a line change is a move with no direction', () => {
    expect(priceMove({ odds: -110, line: 3 }, { odds: -120, line: 3.5 })).toBe('moved')
  })
})

describe('gameTarget / modelPickQuote', () => {
  it('builds BetModal props from a quote, team-resolved', () => {
    const g = game()
    const t = gameTarget(g, findGameQuote(g.quotes, 'spread', 'AWAY') as AvailableGameQuote, 4.1)
    expect(t).toEqual({ pickType: 'spread', pickValue: 'Cowboys +3', betValue: 'AWAY +3', odds: -110,
      gameId: 1, homeTeam: 'Buccaneers', awayTeam: 'Cowboys', edgePct: 4.1 })
  })
  it("finds the model pick's live quote, refused or not", () => {
    expect(modelPickQuote(game({ model_pick: { pick_type: 'over_under', pick_value: 'Under 47.5', odds: -110, edge_pct: 3 } })))
      .toMatchObject({ side: 'Under', available: false })
  })
  it('is null when the stored label maps to no side', () => {
    expect(modelPickQuote(game({ model_pick: { pick_type: 'moneyline', pick_value: 'Cowboys ML', odds: 135, edge_pct: 3 } })))
      .toBeNull()
    expect(modelPickQuote(game())).toBeNull()
  })
})

describe('groupProps / propTarget', () => {
  const p = (player: string, market_label: string, outcome: 'Over' | 'Under', line: number, odds = -110): PropQuote => ({
    available: true, pick_type: 'prop', pick_value: `${player} ${outcome} ${line} ${market_label}`, odds,
    quoted_at: 'x', prop_player: player, prop_market: 'player_pass_yds', market_label, outcome, line,
  })
  it('groups by market, pairs Over and Under on the same player and line', () => {
    const over = p('QB One', 'Pass Yds', 'Over', 245.5, -115)
    const under = p('QB One', 'Pass Yds', 'Under', 245.5, -105)
    const rec = p('WR Two', 'Rec Yds', 'Over', 60.5)
    expect(groupProps([over, rec, under])).toEqual([
      ['Pass Yds', [{ player: 'QB One', line: 245.5, over, under }]],
      ['Rec Yds', [{ player: 'WR Two', line: 60.5, over: rec, under: undefined }]],
    ])
  })
  it('builds a prop BetModal target that legFromPick can parse', () => {
    const over = p('QB One', 'Pass Yds', 'Over', 245.5, -115)
    expect(propTarget(9, over as never)).toEqual({ pickType: 'prop', pickValue: 'QB One Over 245.5',
      betValue: 'QB One Over 245.5', odds: -115, gameId: 9, homeTeam: '', awayTeam: '',
      propMarket: 'player_pass_yds', propPlayer: 'QB One' })
  })
})
```

- [ ] **Step 4: Run the tests and confirm they fail**

Run: `cd frontend && npx vitest run src/lib/board.test.ts`
Expected: FAIL, "Failed to resolve import './board'".

- [ ] **Step 5: Write `frontend/src/lib/board.ts`**

```ts
import type { AvailableGameQuote, AvailablePropQuote, BetTarget, BoardGame, GamePickType, GameQuote,
  GameSide, PropQuote } from '../types'
import { legFromPick, resolveQuoteLabel } from './quotes'

const ET = 'America/New_York'

/** Today's date in ET as YYYY-MM-DD -- the convention Game.date is stored in. */
export function etToday(now: Date): string {
  return new Intl.DateTimeFormat('en-CA', { timeZone: ET }).format(now)
}

/** "2026-10-11" -> "Sun Oct 11". Noon local keeps the date from shifting. */
export function formatDay(date: string): string {
  return new Date(`${date}T12:00:00`)
    .toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' })
    .replace(',', '')
}

export function startLabel(iso: string | null): string {
  if (!iso) return 'TBD'
  return new Date(iso).toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' })
}

export function formatMoney(n: number): string {
  return n.toLocaleString('en-US', { style: 'currency', currency: 'USD' })
}

export function groupByDay(games: BoardGame[], today: string) {
  const days: { date: string; label: string; games: BoardGame[] }[] = []
  for (const g of games) {
    let day = days.find(d => d.date === g.date)
    if (!day) {
      day = { date: g.date, label: g.date === today ? 'Today' : formatDay(g.date), games: [] }
      days.push(day)
    }
    day.games.push(g)
  }
  return days
}

export function sportTabs(games: BoardGame[]): string[] {
  return [...new Set(games.map(g => g.sport))]
}

export function findGameQuote(quotes: GameQuote[], pickType: GamePickType, side: GameSide): GameQuote | undefined {
  return quotes.find(q => q.pick_type === pickType && q.side === side)
}

/** The small line above the price on a tile: "+3", "O 47.5", or none (ML). */
export function tileTop(q: AvailableGameQuote): string | null {
  if (q.line === null || q.pick_type === 'moneyline') return null
  if (q.pick_type === 'over_under') return `${q.side === 'Over' ? 'O' : 'U'} ${q.line}`
  return q.line > 0 ? `+${q.line}` : `${q.line}`
}

export type PriceMove = 'better' | 'worse' | 'moved' | null
type Price = { odds: number; line: number | null }

/** How a tile's price changed between two refetches. Higher American odds
 *  always pay more, so they are better for the bettor; a line change has no
 *  single direction across sides, so it is just "moved". */
export function priceMove(prev: Price | null, next: Price | null): PriceMove {
  if (!prev || !next) return null
  if (prev.line !== next.line) return 'moved'
  if (next.odds > prev.odds) return 'better'
  if (next.odds < prev.odds) return 'worse'
  return null
}

export function gameTarget(game: BoardGame, q: AvailableGameQuote, edgePct?: number): BetTarget {
  const t: BetTarget = {
    pickType: q.pick_type, pickValue: resolveQuoteLabel(q, game.home_team, game.away_team),
    betValue: q.pick_value, odds: q.odds, gameId: game.id,
    homeTeam: game.home_team, awayTeam: game.away_team,
  }
  if (edgePct !== undefined) t.edgePct = edgePct
  return t
}

export function propTarget(gameId: number, q: AvailablePropQuote): BetTarget {
  const label = `${q.prop_player} ${q.outcome} ${q.line}`
  return { pickType: 'prop', pickValue: label, betValue: label, odds: q.odds, gameId,
    homeTeam: '', awayTeam: '', propMarket: q.prop_market, propPlayer: q.prop_player }
}

/** The live quote for a game's model pick -- available or refused -- or null
 *  when there is no pick or its stored label maps to no priced side. */
export function modelPickQuote(game: BoardGame): GameQuote | null {
  const mp = game.model_pick
  if (!mp) return null
  const leg = legFromPick(mp.pick_type, mp.pick_value, game.id)
  if (!leg || leg.pick_type === 'prop') return null
  return findGameQuote(game.quotes, leg.pick_type, leg.side) ?? null
}

export interface PropRow { player: string; line: number; over?: PropQuote; under?: PropQuote }

export function groupProps(quotes: PropQuote[]): [string, PropRow[]][] {
  const markets = new Map<string, PropRow[]>()
  for (const q of quotes) {
    const rows = markets.get(q.market_label) ?? []
    let row = rows.find(r => r.player === q.prop_player && r.line === q.line)
    if (!row) {
      row = { player: q.prop_player, line: q.line, over: undefined, under: undefined }
      rows.push(row)
    }
    if (q.outcome === 'Over') row.over = q
    else row.under = q
    markets.set(q.market_label, rows)
  }
  return [...markets.entries()]
}
```

- [ ] **Step 6: Run the tests and confirm they pass**

Run: `cd frontend && npx vitest run src/lib/board.test.ts`
Expected: PASS.

If `startLabel` fails in jsdom because of a narrow no-break space before
AM/PM, the regex `\s?` already allows ordinary whitespace. Widen it to
`[\s ]?` rather than changing the code.

- [ ] **Step 7: Mutation-check**

Make each change, confirm a test fails, then restore:
1. In `etToday`, drop `timeZone: ET`.
2. In `priceMove`, swap `>` and `<`.
3. In `modelPickQuote`, drop the `if (!leg ...)` guard.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/types.ts frontend/src/api/client.ts frontend/src/hooks/useBoard.ts frontend/src/lib/board.ts frontend/src/lib/board.test.ts
git commit -m "feat(lobby): board types, API call, hook and pure helpers

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Theme tokens, font and sportsbook stylesheet

**Files:**
- Modify: `frontend/src/index.css:1-42` (the `:root` token block), and the
  accent-on-white rules found in Step 2
- Create: `frontend/src/sportsbook.css`
- Modify: `frontend/src/main.tsx` (import it after `index.css`)
- Modify: `frontend/index.html` (Inter font, theme-color)

**Interfaces:**
- Produces: CSS classes that Tasks 4–7 use, all prefixed `sb-`:
  - **Layout:** `sb-topbar`, `sb-wordmark`, `sb-topnav`, `sb-tabbar`,
    `sb-tab`, `sb-chip`, `sb-chip-menu`, `sb-research`
  - **Lobby:** `sb-lobby`, `sb-offline`, `sb-empty`, `sb-sport-tabs`,
    `sb-sport-tab`, `sb-day`
  - **Model Picks strip:** `sb-strip`, `sb-strip-card`
  - **Game card:** `sb-card`, `sb-card-head`, `sb-card-grid`, `sb-col-head`,
    `sb-team`, `sb-card-foot`
  - **Odds tile:** `sb-tile`, `sb-tile-top`, `sb-tile-price`,
    `sb-tile-locked`, `sb-flash-better`, `sb-flash-worse`, `sb-flash-moved`
  - **Game page:** `sb-detail`, `sb-detail-tabs`, `sb-prop-market`,
    `sb-prop-row`
  - **Token:** `--on-accent`

- [ ] **Step 1: Replace the token values in `index.css` `:root`**

Keep every existing token name. Set these values and add `--on-accent`:

```css
  --bg-base: #0d0f12;
  --bg-surface: #181b20;
  --bg-elevated: #22262d;
  --bg-hover: #2b3038;
  --bg-active: #1d3a2a;

  --border-subtle: rgba(255, 255, 255, 0.05);
  --border-default: rgba(255, 255, 255, 0.08);
  --border-strong: rgba(255, 255, 255, 0.14);

  --text-primary: #f3f5f8;
  --text-secondary: #b6bdc9;
  --text-muted: #8a93a3;

  --accent: #2fe37a;
  --accent-hover: #26c96a;
  --accent-glow: rgba(47, 227, 122, 0.15);
  --on-accent: #0d0f12;

  --green: #2fe37a;
  --green-dim: rgba(47, 227, 122, 0.14);
  --red: #ff4d5e;
  --red-dim: rgba(255, 77, 94, 0.14);
  --yellow: #ffb020;
  --yellow-dim: rgba(255, 176, 32, 0.14);
```

Leave `--purple*`, the fonts, radii, shadows and `--transition` unchanged.

- [ ] **Step 2: Fix white text on the new bright accent**

Run: `grep -n "color: white\|color: #fff" frontend/src/index.css`

For every match whose rule also sets `background: var(--accent)` (or sits in
an `.active` / primary-button rule on the accent), change it to
`color: var(--on-accent);`. Leave white text on red and purple backgrounds
alone.

- [ ] **Step 3: Load Inter and set the theme colour in `frontend/index.html`**

Replace `<meta name="theme-color" content="#3b82f6" />` with:

```html
    <meta name="theme-color" content="#0d0f12" />
    <link rel="preconnect" href="https://fonts.googleapis.com" />
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet" />
```

- [ ] **Step 4: Create `frontend/src/sportsbook.css`**

```css
/* ─── Sportsbook shell (spec 2026-10-07 §3-§5) ─────────────── */
.sb-topbar {
  position: sticky; top: 0; z-index: 100;
  display: flex; align-items: center; gap: 1.5rem;
  height: 56px; padding: 0 1rem;
  background: var(--bg-base); border-bottom: 1px solid var(--border-default);
}
.sb-wordmark {
  font-weight: 800; letter-spacing: 0.06em; font-size: 1rem;
  color: var(--text-primary); text-decoration: none; white-space: nowrap;
}
.sb-wordmark span { color: var(--accent); }
.sb-topnav { display: none; gap: 0.25rem; flex: 1; }
.sb-topnav .sb-tab {
  padding: 0.4rem 0.8rem; border-radius: 999px; font-weight: 600;
  color: var(--text-secondary); text-decoration: none; background: none; border: 0; cursor: pointer;
  font: inherit; font-weight: 600;
}
.sb-topnav .sb-tab.active { color: var(--on-accent); background: var(--accent); }

.sb-chip {
  margin-left: auto; position: relative;
  display: flex; align-items: center; gap: 0.4rem;
  padding: 0.35rem 0.75rem; border-radius: 999px;
  background: var(--bg-elevated); border: 1px solid var(--border-strong);
  color: var(--text-primary); font: inherit; font-weight: 700; cursor: pointer;
  font-variant-numeric: tabular-nums; white-space: nowrap;
}
.sb-chip-menu {
  position: absolute; right: 0; top: calc(100% + 6px); min-width: 200px; z-index: 210;
  background: var(--bg-surface); border: 1px solid var(--border-strong); border-radius: var(--radius-lg);
  box-shadow: var(--shadow-lg); padding: 0.35rem;
}
.sb-chip-menu button {
  display: flex; justify-content: space-between; width: 100%; gap: 1rem;
  padding: 0.55rem 0.7rem; border: 0; border-radius: var(--radius-md);
  background: none; color: var(--text-primary); font: inherit; cursor: pointer; text-align: left;
}
.sb-chip-menu button:hover, .sb-chip-menu button[aria-current="true"] { background: var(--bg-hover); }

.sb-tabbar {
  position: fixed; left: 0; right: 0; bottom: 0; z-index: 100;
  display: flex; height: 60px; padding-bottom: env(safe-area-inset-bottom);
  background: var(--bg-surface); border-top: 1px solid var(--border-default);
}
.sb-tabbar .sb-tab {
  flex: 1; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 2px;
  color: var(--text-muted); text-decoration: none; font-size: 0.7rem; font-weight: 700;
  background: none; border: 0; cursor: pointer; font-family: inherit;
}
.sb-tabbar .sb-tab.active { color: var(--accent); }
.sb-tabbar .sb-tab span:first-child { font-size: 1.25rem; }

.sb-research {
  position: fixed; z-index: 220; left: 0; right: 0; bottom: 60px;
  background: var(--bg-surface); border-top: 1px solid var(--border-strong);
  border-radius: 16px 16px 0 0; padding: 0.75rem; box-shadow: var(--shadow-lg);
}
.sb-research a {
  display: block; padding: 0.8rem 0.75rem; border-radius: var(--radius-md);
  color: var(--text-primary); text-decoration: none; font-weight: 600;
}
.sb-research a.active, .sb-research a:hover { background: var(--bg-hover); }
.sb-research-overlay { position: fixed; inset: 0; z-index: 215; background: rgba(0, 0, 0, 0.5); }

.main-content { padding-bottom: 76px; }

@media (min-width: 900px) {
  .sb-topnav { display: flex; }
  .sb-tabbar { display: none; }
  .main-content { padding-bottom: 2rem; }
  .sb-research {
    left: auto; right: 1rem; bottom: auto; top: 60px; width: 260px;
    border-radius: var(--radius-lg); border: 1px solid var(--border-strong);
  }
}

/* ─── Lobby ─────────────────────────────────────────────────── */
.sb-lobby { max-width: 760px; margin: 0 auto; padding: 0.75rem 0.75rem 1rem; }
.sb-offline {
  margin-bottom: 0.75rem; padding: 0.7rem 0.9rem; border-radius: var(--radius-md);
  background: var(--yellow-dim); color: var(--yellow); font-weight: 700;
}
.sb-empty { padding: 2rem 0; text-align: center; color: var(--text-muted); }
.sb-sport-tabs { display: flex; gap: 0.5rem; overflow-x: auto; padding-bottom: 0.5rem; scrollbar-width: none; }
.sb-sport-tab {
  flex: none; padding: 0.45rem 1rem; border-radius: 999px; cursor: pointer;
  background: var(--bg-elevated); border: 1px solid var(--border-default);
  color: var(--text-secondary); font: inherit; font-weight: 800; letter-spacing: 0.04em;
}
.sb-sport-tab[aria-selected="true"] { background: var(--accent); color: var(--on-accent); border-color: var(--accent); }
.sb-day { margin: 1rem 0 0.5rem; font-size: 0.8rem; font-weight: 800; letter-spacing: 0.06em;
  text-transform: uppercase; color: var(--text-muted); }

.sb-strip { display: flex; gap: 0.5rem; overflow-x: auto; margin: 0.5rem 0; scrollbar-width: none; }
.sb-strip-card {
  flex: none; min-width: 170px; padding: 0.6rem 0.7rem; border-radius: var(--radius-lg);
  background: linear-gradient(135deg, var(--bg-active), var(--bg-surface));
  border: 1px solid var(--border-strong);
}
.sb-strip-card small { display: block; color: var(--accent); font-weight: 800; font-size: 0.7rem; letter-spacing: 0.05em; }
.sb-strip-card strong { display: block; margin: 0.15rem 0 0.4rem; }

.sb-card {
  margin-bottom: 0.6rem; border-radius: var(--radius-lg);
  background: var(--bg-surface); border: 1px solid var(--border-default); overflow: hidden;
}
.sb-card-head { display: flex; justify-content: space-between; padding: 0.55rem 0.8rem 0;
  font-size: 0.75rem; color: var(--text-muted); font-weight: 600; }
.sb-card-head a { color: var(--text-muted); text-decoration: none; }
.sb-card-grid {
  display: grid; grid-template-columns: minmax(0, 1fr) repeat(3, 76px);
  gap: 0.35rem; align-items: center; padding: 0.4rem 0.8rem 0.7rem;
}
.sb-col-head { font-size: 0.65rem; font-weight: 800; color: var(--text-muted);
  text-align: center; letter-spacing: 0.05em; text-transform: uppercase; }
.sb-team { font-weight: 700; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.sb-card-foot {
  display: block; padding: 0.5rem 0.8rem; border-top: 1px solid var(--border-subtle);
  color: var(--accent); font-size: 0.8rem; font-weight: 700; text-decoration: none;
}

/* ─── Odds tile ─────────────────────────────────────────────── */
.sb-tile {
  display: flex; flex-direction: column; align-items: center; justify-content: center;
  min-height: 48px; padding: 0.3rem 0.2rem; border-radius: var(--radius-md); cursor: pointer;
  background: var(--bg-elevated); border: 1px solid var(--border-strong);
  color: var(--text-primary); font: inherit; font-variant-numeric: tabular-nums;
  transition: background var(--transition), transform 120ms ease;
}
.sb-tile:hover:not(:disabled) { background: var(--bg-hover); }
.sb-tile:active:not(:disabled) { transform: scale(0.96); }
.sb-tile-top { font-size: 0.75rem; color: var(--text-secondary); font-weight: 600; }
.sb-tile-price { font-size: 0.9rem; font-weight: 800; color: var(--accent); }
.sb-tile-locked { cursor: not-allowed; opacity: 0.55; }
.sb-tile-locked .sb-tile-price { color: var(--text-muted); }
.sb-flash-better { animation: sb-flash-g 1s ease; }
.sb-flash-worse { animation: sb-flash-r 1s ease; }
.sb-flash-moved { animation: sb-flash-y 1s ease; }
@keyframes sb-flash-g { 0% { background: var(--green-dim); border-color: var(--green); } }
@keyframes sb-flash-r { 0% { background: var(--red-dim); border-color: var(--red); } }
@keyframes sb-flash-y { 0% { background: var(--yellow-dim); border-color: var(--yellow); } }

/* ─── Game page ─────────────────────────────────────────────── */
.sb-detail { max-width: 760px; margin: 0 auto; padding: 0.75rem; }
.sb-detail h1 { font-size: 1.15rem; font-weight: 800; margin: 0.25rem 0 0.75rem; }
.sb-detail-tabs { display: flex; gap: 0.5rem; margin-bottom: 0.75rem; }
.sb-detail .sb-card-grid { grid-template-columns: minmax(0, 1fr) repeat(3, 92px); }
.sb-detail .sb-tile { min-height: 56px; }
.sb-prop-market { margin-bottom: 0.75rem; }
.sb-prop-market h2 { font-size: 0.8rem; font-weight: 800; text-transform: uppercase;
  letter-spacing: 0.05em; color: var(--text-muted); margin-bottom: 0.4rem; }
.sb-prop-row { display: grid; grid-template-columns: minmax(0, 1fr) 96px 96px; gap: 0.35rem;
  align-items: center; padding: 0.3rem 0; }

@media (prefers-reduced-motion: reduce) {
  .sb-tile, .sb-flash-better, .sb-flash-worse, .sb-flash-moved { animation: none; transition: none; }
}
```

- [ ] **Step 5: Import it in `frontend/src/main.tsx`**

Add `import './sportsbook.css'` on the line after `import './index.css'`.

- [ ] **Step 6: Build to check that the CSS parses**

Run: `cd frontend && npm run build`
Expected: the build succeeds.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/index.css frontend/src/sportsbook.css frontend/src/main.tsx frontend/index.html
git commit -m "feat(theme): Metric Edge sportsbook palette, Inter, sb- stylesheet

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: `OddsTile`, `BoardGameCard` and `ModelPicksStrip`

**Files:**
- Create: `frontend/src/components/OddsTile.tsx`
- Create: `frontend/src/components/BoardGameCard.tsx`
- Create: `frontend/src/components/ModelPicksStrip.tsx`
- Test: `frontend/src/components/OddsTile.test.tsx`
- Test: `frontend/src/components/BoardGameCard.test.tsx`

**Interfaces:**
- Consumes: from `lib/board.ts`: `findGameQuote`, `tileTop`, `priceMove`,
  `gameTarget`, `modelPickQuote`, `startLabel`; from `lib/quotes.ts`:
  `formatOdds`, `resolveQuoteLabel`.
- Produces:
  - `OddsTile({ top, price, line, lockedReason, offline, onSelect, label })`
  - `TeamRow({ game, team, side, totalSide, offline, onPick })`, where `side`
    is `'HOME' | 'AWAY'` and `totalSide` is `'Over' | 'Under'`
  - `BoardGameCard({ game, offline, onPick })`
  - `ModelPicksStrip({ games, offline, onPick })`
  - `onPick` is always `(t: BetTarget) => void`.

- [ ] **Step 1: Write the failing tests**

`frontend/src/components/OddsTile.test.tsx`:

```tsx
import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import OddsTile from './OddsTile'

describe('OddsTile', () => {
  it('shows the line and price and calls onSelect', () => {
    const onSelect = vi.fn()
    render(<OddsTile label="Cowboys +3" top="+3" price={-110} line={3} onSelect={onSelect} />)
    const tile = screen.getByRole('button', { name: /Cowboys \+3 -110/ })
    expect(tile).toHaveTextContent('+3')
    fireEvent.click(tile)
    expect(onSelect).toHaveBeenCalledOnce()
  })
  it('is locked, disabled and explains why when the side is refused', () => {
    render(<OddsTile label="Under" top={null} price={null} line={null} lockedReason="The price is stale" onSelect={() => {}} />)
    const tile = screen.getByRole('button', { name: /Under locked/ })
    expect(tile).toBeDisabled()
    expect(tile).toHaveAttribute('title', 'The price is stale')
    expect(tile).toHaveTextContent('🔒')
  })
  it('locks a priced side when the board is offline', () => {
    render(<OddsTile label="X" top={null} price={-110} line={null} offline onSelect={() => {}} />)
    expect(screen.getByRole('button')).toBeDisabled()
    expect(screen.getByRole('button')).toHaveAttribute('title', 'Board offline — prices unavailable')
  })
  it('flashes green when the price improves and red when it worsens', () => {
    const { rerender } = render(<OddsTile label="X" top={null} price={-110} line={null} onSelect={() => {}} />)
    rerender(<OddsTile label="X" top={null} price={-105} line={null} onSelect={() => {}} />)
    expect(screen.getByRole('button')).toHaveClass('sb-flash-better')
    rerender(<OddsTile label="X" top={null} price={-120} line={null} onSelect={() => {}} />)
    expect(screen.getByRole('button')).toHaveClass('sb-flash-worse')
  })
})
```

`frontend/src/components/BoardGameCard.test.tsx`:

```tsx
import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import BoardGameCard from './BoardGameCard'
import ModelPicksStrip from './ModelPicksStrip'
import type { BoardGame, GameQuote } from '../types'

const ok = (pick_type: string, side: string, odds: number, line: number | null, pick_value: string): GameQuote => ({
  pick_type, side, available: true, odds, line, pick_value, quoted_at: 'x', prop_player: null, prop_market: null,
} as GameQuote)
const game: BoardGame = {
  id: 7, sport: 'nfl', date: '2026-10-11', start_time: null, home_team: 'Buccaneers', away_team: 'Cowboys',
  prop_count: 42, model_pick: { pick_type: 'spread', pick_value: 'AWAY +3', odds: -110, edge_pct: 4.1 },
  quotes: [
    ok('spread', 'HOME', -110, -3, 'HOME -3'), ok('spread', 'AWAY', -110, 3, 'AWAY +3'),
    ok('moneyline', 'HOME', -160, null, 'HOME ML'), ok('moneyline', 'AWAY', 135, null, 'AWAY ML'),
    ok('over_under', 'Over', -110, 47.5, 'Over 47.5'),
    { pick_type: 'over_under', side: 'Under', available: false, reason: 'stale', message: 'stale!' },
  ],
}

describe('BoardGameCard', () => {
  it('puts the away team first with its spread, moneyline and Over tiles', () => {
    render(<MemoryRouter><BoardGameCard game={game} offline={false} onPick={() => {}} /></MemoryRouter>)
    const rows = screen.getAllByTestId('team-row')
    expect(rows[0]).toHaveTextContent('Cowboys')
    expect(rows[0]).toHaveTextContent('+3')
    expect(rows[0]).toHaveTextContent('+135')
    expect(rows[0]).toHaveTextContent('O 47.5')
    expect(rows[1]).toHaveTextContent('Buccaneers')
    expect(screen.getByText('TBD')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '+42 props ›' })).toHaveAttribute('href', '/game/7')
  })
  it('locks a refused side and still lists the game', () => {
    render(<MemoryRouter><BoardGameCard game={game} offline={false} onPick={() => {}} /></MemoryRouter>)
    expect(screen.getByRole('button', { name: /Buccaneers Under locked/ })).toBeDisabled()
  })
  it('hands the tapped side to onPick as BetModal props', () => {
    const onPick = vi.fn()
    render(<MemoryRouter><BoardGameCard game={game} offline={false} onPick={onPick} /></MemoryRouter>)
    fireEvent.click(screen.getByRole('button', { name: /Cowboys ML \+135/ }))
    expect(onPick).toHaveBeenCalledWith(expect.objectContaining({ pickType: 'moneyline', betValue: 'AWAY ML', odds: 135, gameId: 7 }))
  })
  it('shows "Props ›" with no count when the board sends none', () => {
    render(<MemoryRouter><BoardGameCard game={{ ...game, prop_count: null }} offline={false} onPick={() => {}} /></MemoryRouter>)
    expect(screen.getByRole('link', { name: 'Props ›' })).toBeInTheDocument()
  })
})

describe('ModelPicksStrip', () => {
  it('shows a card per mappable model pick with its edge, tappable', () => {
    const onPick = vi.fn()
    render(<ModelPicksStrip games={[game]} offline={false} onPick={onPick} />)
    expect(screen.getByText('Cowboys +3')).toBeInTheDocument()
    expect(screen.getByText(/Model edge 4.1%/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /Cowboys \+3 -110/ }))
    expect(onPick).toHaveBeenCalledWith(expect.objectContaining({ betValue: 'AWAY +3', edgePct: 4.1 }))
  })
  it('renders nothing when no game has a usable model pick', () => {
    const { container } = render(<ModelPicksStrip games={[{ ...game, model_pick: null }]} offline={false} onPick={() => {}} />)
    expect(container).toBeEmptyDOMElement()
  })
  it('shows a locked chip when the picked side is refused', () => {
    const g = { ...game, model_pick: { pick_type: 'over_under' as const, pick_value: 'Under 47.5', odds: -110, edge_pct: 3 } }
    render(<ModelPicksStrip games={[g]} offline={false} onPick={() => {}} />)
    expect(screen.getByRole('button', { name: /locked/ })).toBeDisabled()
  })
})
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `cd frontend && npx vitest run src/components/OddsTile.test.tsx src/components/BoardGameCard.test.tsx`
Expected: FAIL, the modules do not exist.

- [ ] **Step 3: Write `frontend/src/components/OddsTile.tsx`**

```tsx
import { useEffect, useRef, useState } from 'react'
import { formatOdds } from '../lib/quotes'
import { priceMove, type PriceMove } from '../lib/board'

const OFFLINE = 'Board offline — prices unavailable'

interface Props {
  /** Accessible name: the side as a person reads it ("Cowboys +3"). */
  label: string
  top: string | null
  /** null = the server refused this side: the tile is locked. */
  price: number | null
  line: number | null
  lockedReason?: string
  offline?: boolean
  onSelect: () => void
}

export default function OddsTile({ label, top, price, line, lockedReason, offline = false, onSelect }: Props) {
  const locked = offline || price === null
  const prev = useRef<{ odds: number; line: number | null } | null>(null)
  const [flash, setFlash] = useState<PriceMove>(null)

  useEffect(() => {
    const next = price === null ? null : { odds: price, line }
    const move = priceMove(prev.current, next)
    prev.current = next
    if (!move) return
    setFlash(move)
    const t = setTimeout(() => setFlash(null), 1000)
    return () => clearTimeout(t)
  }, [price, line])

  const cls = ['sb-tile', locked && 'sb-tile-locked', flash && `sb-flash-${flash}`].filter(Boolean).join(' ')
  return (
    <button
      type="button"
      className={cls}
      disabled={locked}
      title={offline ? OFFLINE : locked ? lockedReason : undefined}
      aria-label={locked ? `${label} locked` : `${label} ${formatOdds(price as number)}`}
      onClick={onSelect}
    >
      {top && <span className="sb-tile-top">{top}</span>}
      <span className="sb-tile-price">{locked ? '🔒' : formatOdds(price as number)}</span>
    </button>
  )
}
```

- [ ] **Step 4: Write `frontend/src/components/BoardGameCard.tsx`**

```tsx
import { Link } from 'react-router-dom'
import OddsTile from './OddsTile'
import { findGameQuote, gameTarget, startLabel, tileTop } from '../lib/board'
import { resolveQuoteLabel } from '../lib/quotes'
import type { BetTarget, BoardGame, GamePickType, GameSide } from '../types'

interface RowProps {
  game: BoardGame; team: string; side: 'HOME' | 'AWAY'; totalSide: 'Over' | 'Under';
  offline: boolean; onPick: (t: BetTarget) => void;
}

function Tile({ game, pickType, side, offline, onPick }: {
  game: BoardGame; pickType: GamePickType; side: GameSide; offline: boolean; onPick: (t: BetTarget) => void
}) {
  const q = findGameQuote(game.quotes, pickType, side)
  const team = side === 'HOME' ? game.home_team : side === 'AWAY' ? game.away_team : ''
  if (!q || !q.available) {
    const name = pickType === 'over_under' ? `${side === 'Over' ? game.away_team : game.home_team} ${side}` : team
    return <OddsTile label={name} top={null} price={null} line={null} offline={offline}
      lockedReason={q && !q.available ? q.message : 'Not offered'} onSelect={() => {}} />
  }
  return <OddsTile label={pickType === 'moneyline' ? `${team} ML` : resolveQuoteLabel(q, game.home_team, game.away_team)}
    top={tileTop(q)} price={q.odds} line={q.line} offline={offline} onSelect={() => onPick(gameTarget(game, q))} />
}

export function TeamRow({ game, team, side, totalSide, offline, onPick }: RowProps) {
  return (
    <div className="sb-card-row" data-testid="team-row" style={{ display: 'contents' }}>
      <span className="sb-team">{team}</span>
      <Tile game={game} pickType="spread" side={side} offline={offline} onPick={onPick} />
      <Tile game={game} pickType="moneyline" side={side} offline={offline} onPick={onPick} />
      <Tile game={game} pickType="over_under" side={totalSide} offline={offline} onPick={onPick} />
    </div>
  )
}

export function GameLines({ game, offline, onPick }: { game: BoardGame; offline: boolean; onPick: (t: BetTarget) => void }) {
  return (
    <div className="sb-card-grid">
      <span />
      <span className="sb-col-head">Spread</span>
      <span className="sb-col-head">Money</span>
      <span className="sb-col-head">Total</span>
      <TeamRow game={game} team={game.away_team} side="AWAY" totalSide="Over" offline={offline} onPick={onPick} />
      <TeamRow game={game} team={game.home_team} side="HOME" totalSide="Under" offline={offline} onPick={onPick} />
    </div>
  )
}

export default function BoardGameCard({ game, offline, onPick }: { game: BoardGame; offline: boolean; onPick: (t: BetTarget) => void }) {
  return (
    <article className="sb-card">
      <header className="sb-card-head">
        <span>{startLabel(game.start_time)}</span>
        <Link to={`/game/${game.id}`}>More ›</Link>
      </header>
      <GameLines game={game} offline={offline} onPick={onPick} />
      <Link className="sb-card-foot" to={`/game/${game.id}`}>
        {game.prop_count ? `+${game.prop_count} props ›` : 'Props ›'}
      </Link>
    </article>
  )
}
```

`display: contents` on the row wrapper keeps the four cells in the parent
grid, while `data-testid` still lets the tests read the row's text.

- [ ] **Step 5: Write `frontend/src/components/ModelPicksStrip.tsx`**

```tsx
import OddsTile from './OddsTile'
import { gameTarget, modelPickQuote, tileTop } from '../lib/board'
import { resolveQuoteLabel } from '../lib/quotes'
import type { BetTarget, BoardGame } from '../types'

export default function ModelPicksStrip({ games, offline, onPick }: {
  games: BoardGame[]; offline: boolean; onPick: (t: BetTarget) => void
}) {
  const cards = games.flatMap(g => {
    const q = modelPickQuote(g)
    return q && g.model_pick ? [{ g, q, edge: g.model_pick.edge_pct }] : []
  })
  if (cards.length === 0) return null
  return (
    <section className="sb-strip" aria-label="Metric Edge model picks">
      {cards.map(({ g, q, edge }) => {
        const label = q.available ? resolveQuoteLabel(q, g.home_team, g.away_team) : `${g.away_team} @ ${g.home_team}`
        return (
          <div className="sb-strip-card" key={g.id}>
            <small>MODEL PICK · Model edge {edge.toFixed(1)}%</small>
            <strong>{label}</strong>
            <OddsTile label={label} top={q.available ? tileTop(q) : null}
              price={q.available ? q.odds : null} line={q.available ? q.line : null}
              lockedReason={q.available ? undefined : q.message} offline={offline}
              onSelect={() => { if (q.available) onPick(gameTarget(g, q, edge)) }} />
          </div>
        )
      })}
    </section>
  )
}
```

- [ ] **Step 6: Run the tests and confirm they pass**

Run: `cd frontend && npx vitest run src/components/OddsTile.test.tsx src/components/BoardGameCard.test.tsx`
Expected: PASS.

The `TBD` assertion needs `start_time: null`, which the fixture has. If the
"Cowboys ML +135" name query fails, check that the label is `${team} ML` and
that `formatOdds(135)` is `+135`.

- [ ] **Step 7: Mutation-check**

Make each change, confirm a test fails, then restore:
1. In `OddsTile`, compute `locked` as `price === null`, dropping `offline`.
2. In `GameLines`, swap `totalSide="Over"` and `totalSide="Under"`.
3. In `ModelPicksStrip`, render the tile with `price={q.available ? q.odds : -110}`.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/components/OddsTile.tsx frontend/src/components/OddsTile.test.tsx frontend/src/components/BoardGameCard.tsx frontend/src/components/BoardGameCard.test.tsx frontend/src/components/ModelPicksStrip.tsx
git commit -m "feat(lobby): odds tiles, game cards and the model picks strip

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: The Lobby page

**Files:**
- Create: `frontend/src/pages/Lobby.tsx`
- Test: `frontend/src/pages/Lobby.test.tsx`

**Interfaces:**
- Consumes: `useBoard` and `etToday` / `groupByDay` / `sportTabs` (Task 2);
  `BoardGameCard` and `ModelPicksStrip` (Task 4); `BetModal` (existing; it
  takes `BetTarget` keys plus `open` / `onClose`).
- Produces: the `Lobby` default export, routed at `/` in Task 7.

- [ ] **Step 1: Write the failing tests**

```tsx
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import Lobby from './Lobby'
import { ToastProvider } from '../components/Toast'
import { api } from '../api/client'
import type { BoardGame, GameQuote } from '../types'

vi.mock('../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/client')>()
  return { ...actual, api: { ...actual.api,
    paper: { board: vi.fn(), quotes: vi.fn(), propQuotes: vi.fn() },
    users: { ...actual.api.users, list: vi.fn() } } }
})

const ml = (side: string, odds: number): GameQuote => ({ pick_type: 'moneyline', side, available: true, odds,
  line: null, pick_value: `${side} ML`, quoted_at: 'x', prop_player: null, prop_market: null } as GameQuote)
const g = (id: number, sport: string, date: string): BoardGame => ({
  id, sport, date, start_time: null, home_team: `H${id}`, away_team: `A${id}`, prop_count: 0, model_pick: null,
  quotes: [ml('HOME', -150), ml('AWAY', 130)],
})

function renderLobby(client = new QueryClient({ defaultOptions: { queries: { retry: false } } })) {
  return render(
    <QueryClientProvider client={client}><ToastProvider><MemoryRouter><Lobby /></MemoryRouter></ToastProvider></QueryClientProvider>)
}

describe('Lobby', () => {
  beforeEach(() => {
    vi.mocked(api.users.list).mockResolvedValue([])
    vi.mocked(api.paper.quotes).mockResolvedValue({ game_id: 1, quotes: [] })
  })

  it('shows sport tabs in board order and the first sport by default', async () => {
    vi.mocked(api.paper.board).mockResolvedValue({ games: [g(1, 'nba', '2026-10-20'), g(2, 'nfl', '2026-10-21')] })
    renderLobby()
    expect(await screen.findByRole('tab', { name: 'NBA' })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByText('A1')).toBeInTheDocument()
    expect(screen.queryByText('A2')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('tab', { name: 'NFL' }))
    expect(screen.getByText('A2')).toBeInTheDocument()
  })

  it('says so when the board is empty', async () => {
    vi.mocked(api.paper.board).mockResolvedValue({ games: [] })
    renderLobby()
    expect(await screen.findByText(/No games on the board/)).toBeInTheDocument()
  })

  it('opens the bet modal on a tapped price', async () => {
    vi.mocked(api.paper.board).mockResolvedValue({ games: [g(1, 'nfl', '2026-10-20')] })
    renderLobby()
    fireEvent.click(await screen.findByRole('button', { name: /A1 ML \+130/ }))
    expect(await screen.findByText('Place Paper Bet')).toBeInTheDocument()
  })

  it('locks every tile and shows the banner when a refetch fails over old data', async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    client.setQueryData(['paper', 'board'], { games: [g(1, 'nfl', '2026-10-20')] })
    vi.mocked(api.paper.board).mockRejectedValue(new Error('offline'))
    renderLobby(client)
    expect(await screen.findByRole('alert')).toHaveTextContent('Board offline — prices unavailable')
    expect(screen.getByText('A1')).toBeInTheDocument()            // old games still shown
    await waitFor(() => {
      for (const b of screen.getAllByRole('button', { name: /locked/ })) expect(b).toBeDisabled()
    })
    expect(screen.queryByRole('button', { name: /\+130/ })).not.toBeInTheDocument()
  })
})
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `cd frontend && npx vitest run src/pages/Lobby.test.tsx`
Expected: FAIL, the module does not exist.

- [ ] **Step 3: Write `frontend/src/pages/Lobby.tsx`**

```tsx
import { useState } from 'react'
import BetModal from '../components/BetModal'
import BoardGameCard from '../components/BoardGameCard'
import ModelPicksStrip from '../components/ModelPicksStrip'
import { useBoard } from '../hooks/useBoard'
import { etToday, groupByDay, sportTabs } from '../lib/board'
import type { BetTarget } from '../types'

export default function Lobby() {
  const board = useBoard()
  const games = board.data?.games ?? []
  // Any failed fetch locks the board, even with old data still on screen:
  // a price the server can't confirm must not be tappable.
  const offline = board.isError
  const tabs = sportTabs(games)
  const [chosen, setChosen] = useState<string | null>(null)
  const sport = chosen && tabs.includes(chosen) ? chosen : tabs[0] ?? null
  const shown = games.filter(g => g.sport === sport)
  const [target, setTarget] = useState<BetTarget | null>(null)

  return (
    <div className="sb-lobby">
      {offline && <div role="alert" className="sb-offline">Board offline — prices unavailable</div>}
      {board.isLoading && <p className="sb-empty">Loading the board…</p>}
      {board.isSuccess && games.length === 0 &&
        <p className="sb-empty">No games on the board in the next 7 days.</p>}
      {tabs.length > 0 && (
        <div className="sb-sport-tabs" role="tablist" aria-label="Sports">
          {tabs.map(s => (
            <button key={s} role="tab" className="sb-sport-tab" aria-selected={s === sport}
              onClick={() => setChosen(s)}>{s.toUpperCase()}</button>
          ))}
        </div>
      )}
      <ModelPicksStrip games={shown} offline={offline} onPick={setTarget} />
      {groupByDay(shown, etToday(new Date())).map(day => (
        <section key={day.date} aria-label={day.label}>
          <h2 className="sb-day">{day.label}</h2>
          {day.games.map(game => <BoardGameCard key={game.id} game={game} offline={offline} onPick={setTarget} />)}
        </section>
      ))}
      {target && <BetModal open onClose={() => setTarget(null)} {...target} />}
    </div>
  )
}
```

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `cd frontend && npx vitest run src/pages/Lobby.test.tsx`
Expected: PASS.

- [ ] **Step 5: Mutation-check**

Make each change, confirm a test fails, then restore:
1. Change `const offline = board.isError` to
   `board.isError && !board.data`. The offline test must fail.
2. Change `tabs[0]` to `tabs[tabs.length - 1]`. The default-tab test must
   fail.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/pages/Lobby.tsx frontend/src/pages/Lobby.test.tsx
git commit -m "feat(lobby): the Lobby page — sport tabs, day groups, offline lock

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: The game page, `/game/:id`

**Files:**
- Create: `frontend/src/pages/GameDetail.tsx`
- Test: `frontend/src/pages/GameDetail.test.tsx`

**Interfaces:**
- Consumes: `useBoard` (Task 2); `usePropQuotes(gameId)` (existing,
  `hooks/useQuotes.ts`); `GameLines` (Task 4); `OddsTile`; `groupProps` and
  `propTarget` (Task 2); `BetModal`.
- Produces: the `GameDetail` default export, routed at `/game/:id` in Task 7.

- [ ] **Step 1: Write the failing tests**

```tsx
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import GameDetail from './GameDetail'
import { ToastProvider } from '../components/Toast'
import { api } from '../api/client'
import type { BoardGame, PropQuote } from '../types'

vi.mock('../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/client')>()
  return { ...actual, api: { ...actual.api,
    paper: { board: vi.fn(), quotes: vi.fn(), propQuotes: vi.fn() },
    users: { ...actual.api.users, list: vi.fn() } } }
})

const game: BoardGame = { id: 5, sport: 'nfl', date: '2026-10-11', start_time: null, home_team: 'Bucs',
  away_team: 'Cowboys', prop_count: 1, model_pick: null, quotes: [] }
const prop: PropQuote = { available: true, pick_type: 'prop', pick_value: 'QB One Over 245.5 Pass Yds', odds: -115,
  quoted_at: 'x', prop_player: 'QB One', prop_market: 'player_pass_yds', market_label: 'Pass Yds', outcome: 'Over', line: 245.5 }

function renderAt(path: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={client}><ToastProvider><MemoryRouter initialEntries={[path]}>
    <Routes><Route path="/game/:id" element={<GameDetail />} /></Routes></MemoryRouter></ToastProvider></QueryClientProvider>)
}

describe('GameDetail', () => {
  beforeEach(() => {
    vi.mocked(api.paper.board).mockResolvedValue({ games: [game] })
    vi.mocked(api.paper.propQuotes).mockResolvedValue({ game_id: 5, quotes: [prop] })
    vi.mocked(api.users.list).mockResolvedValue([])
  })

  it('shows the matchup and every locked line when nothing is priced', async () => {
    renderAt('/game/5')
    expect(await screen.findByRole('heading', { name: 'Cowboys @ Bucs' })).toBeInTheDocument()
    expect(screen.getAllByRole('button', { name: /locked/ })).toHaveLength(6)
  })

  it('lists props by market with Over and Under tiles', async () => {
    renderAt('/game/5')
    fireEvent.click(await screen.findByRole('tab', { name: 'Player Props' }))
    expect(await screen.findByRole('heading', { name: 'Pass Yds' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /QB One Over 245.5 -115/ })).toBeEnabled()
    expect(screen.getByRole('button', { name: /QB One Under 245.5 locked/ })).toBeDisabled()
  })

  it('says the game is off the board when it is not on it', async () => {
    renderAt('/game/999')
    expect(await screen.findByText(/off the board/)).toBeInTheDocument()
  })
})
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `cd frontend && npx vitest run src/pages/GameDetail.test.tsx`
Expected: FAIL, the module does not exist.

- [ ] **Step 3: Write `frontend/src/pages/GameDetail.tsx`**

```tsx
import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import BetModal from '../components/BetModal'
import OddsTile from '../components/OddsTile'
import { GameLines } from '../components/BoardGameCard'
import { useBoard } from '../hooks/useBoard'
import { usePropQuotes } from '../hooks/useQuotes'
import { groupProps, propTarget, startLabel } from '../lib/board'
import type { BetTarget, PropQuote } from '../types'

function PropTile({ gameId, player, line, outcome, q, offline, onPick }: {
  gameId: number; player: string; line: number; outcome: 'Over' | 'Under'; q?: PropQuote;
  offline: boolean; onPick: (t: BetTarget) => void
}) {
  const label = `${player} ${outcome} ${line}`
  const top = `${outcome === 'Over' ? 'O' : 'U'} ${line}`
  if (!q || !q.available) {
    return <OddsTile label={label} top={top} price={null} line={line} offline={offline}
      lockedReason={q && !q.available ? q.message : 'Not offered'} onSelect={() => {}} />
  }
  return <OddsTile label={label} top={top} price={q.odds} line={q.line} offline={offline}
    onSelect={() => onPick(propTarget(gameId, q))} />
}

export default function GameDetail() {
  const id = Number(useParams().id)
  const board = useBoard()
  const game = board.data?.games.find(g => g.id === id)
  const [tab, setTab] = useState<'lines' | 'props'>('lines')
  const props = usePropQuotes(game && tab === 'props' ? id : null)
  const offline = board.isError || props.isError
  const [target, setTarget] = useState<BetTarget | null>(null)

  if (board.isLoading) return <div className="sb-detail"><p className="sb-empty">Loading…</p></div>
  if (!game) {
    return (
      <div className="sb-detail">
        <p className="sb-empty">This game is off the board — it has started or isn't priced in the next 7 days.</p>
        <Link to="/">‹ Back to the lobby</Link>
      </div>
    )
  }
  return (
    <div className="sb-detail">
      <Link to="/">‹ Lobby</Link>
      <h1>{game.away_team} @ {game.home_team}</h1>
      <p className="sb-card-head" style={{ padding: 0 }}>{startLabel(game.start_time)}</p>
      {offline && <div role="alert" className="sb-offline">Board offline — prices unavailable</div>}
      <div className="sb-detail-tabs" role="tablist">
        <button role="tab" className="sb-sport-tab" aria-selected={tab === 'lines'} onClick={() => setTab('lines')}>Game Lines</button>
        <button role="tab" className="sb-sport-tab" aria-selected={tab === 'props'} onClick={() => setTab('props')}>Player Props</button>
      </div>
      {tab === 'lines' && <div className="sb-card"><GameLines game={game} offline={offline} onPick={setTarget} /></div>}
      {tab === 'props' && (
        props.isLoading ? <p className="sb-empty">Loading props…</p> :
        groupProps(props.data?.quotes ?? []).length === 0 ? <p className="sb-empty">No props priced for this game.</p> :
        groupProps(props.data?.quotes ?? []).map(([market, rows]) => (
          <section className="sb-prop-market" key={market}>
            <h2>{market}</h2>
            {rows.map(r => (
              <div className="sb-prop-row" key={`${r.player}|${r.line}`}>
                <span className="sb-team">{r.player}</span>
                <PropTile gameId={id} player={r.player} line={r.line} outcome="Over" q={r.over} offline={offline} onPick={setTarget} />
                <PropTile gameId={id} player={r.player} line={r.line} outcome="Under" q={r.under} offline={offline} onPick={setTarget} />
              </div>
            ))}
          </section>
        ))
      )}
      {target && <BetModal open onClose={() => setTarget(null)} {...target} />}
    </div>
  )
}
```

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `cd frontend && npx vitest run src/pages/GameDetail.test.tsx`
Expected: PASS. With `quotes: []`, all 6 game tiles render locked
("Not offered").

- [ ] **Step 5: Commit**

```bash
git add frontend/src/pages/GameDetail.tsx frontend/src/pages/GameDetail.test.tsx
git commit -m "feat(lobby): game page with lines and player props

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Navigation — top bar, player chip, tab bar, Research menu, routes

**Files:**
- Create: `frontend/src/components/MainTabs.tsx`
- Create: `frontend/src/components/PlayerChip.tsx`
- Create: `frontend/src/components/ResearchMenu.tsx`
- Modify: `frontend/src/components/Layout.tsx` (full rewrite below)
- Modify: `frontend/src/App.tsx` (routes)
- Delete: `frontend/src/components/BottomNav.tsx` and
  `frontend/src/components/MobileMenu.tsx`. Step 1 checks that nothing else
  imports them.
- Test: `frontend/src/components/Layout.test.tsx`

**Interfaces:**
- Consumes: `useUserStore` (`currentUserName`, `setCurrentUserName`);
  `api.users.list(): UserProfile[]` (it has `available_balance`);
  `formatMoney` (Task 2); `Lobby` (Task 5); `GameDetail` (Task 6).
- Produces:
  - **Routes:**
    - `/` is Lobby, `/game/:id` is GameDetail, `/model-picks` is
      TodaysPicks.
    - The existing routes keep working: `/props`, `/backtesting`,
      `/track-record`, `/paper-trading`, `/faq`, `/admin`.
  - **`RESEARCH_LINKS`**, exported from `ResearchMenu.tsx`.

- [ ] **Step 1: Check who imports the files being replaced**

Run: `cd frontend && grep -rn "BottomNav\|MobileMenu\|to=\"/\"\|navigate('/')" src --include=*.tsx`

Expected: only `Layout.tsx` imports BottomNav and MobileMenu.
- If any other file links to `/` meaning Today's Picks, change that link to
  `/model-picks` in this task.
- A link to `/` meaning "home" stays as it is.

- [ ] **Step 2: Write the failing tests, `frontend/src/components/Layout.test.tsx`**

```tsx
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import Layout from './Layout'
import { api } from '../api/client'
import { useUserStore } from '../stores/userStore'
import type { UserProfile } from '../types'

vi.mock('../hooks/useWebSocket', () => ({ useWebSocket: () => {} }))
vi.mock('../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/client')>()
  return { ...actual, api: { ...actual.api, users: { ...actual.api.users, list: vi.fn() } } }
})

const u = (id: number, name: string, available_balance: number) =>
  ({ id, name, available_balance, starting_balance: 10000, current_balance: 10000 }) as UserProfile

function renderAt(path: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={client}><MemoryRouter initialEntries={[path]}>
    <Routes><Route path="/" element={<Layout />}>
      <Route index element={<p>lobby page</p>} />
      <Route path="model-picks" element={<p>picks page</p>} />
    </Route></Routes></MemoryRouter></QueryClientProvider>)
}

describe('Layout', () => {
  beforeEach(() => {
    vi.mocked(api.users.list).mockResolvedValue([u(1, 'Marcus', 10240.5), u(2, 'Sam', 9800)])
    useUserStore.getState().setCurrentUserName('Marcus')
  })

  it('has Lobby and My Bets tabs and the METRIC EDGE wordmark', () => {
    renderAt('/')
    expect(screen.getByText('lobby page')).toBeInTheDocument()
    expect(screen.getAllByRole('link', { name: /Lobby/ })[0]).toHaveAttribute('href', '/')
    expect(screen.getAllByRole('link', { name: /My Bets/ })[0]).toHaveAttribute('href', '/paper-trading')
    expect(screen.getByRole('link', { name: /METRIC EDGE/ })).toBeInTheDocument()
  })

  it('opens the Research menu with Model Picks', () => {
    renderAt('/')
    fireEvent.click(screen.getAllByRole('button', { name: /Research/ })[0])
    expect(screen.getByRole('link', { name: 'Model Picks' })).toHaveAttribute('href', '/model-picks')
  })

  it('shows the sport filter only on research pages', () => {
    const { unmount } = renderAt('/')
    expect(screen.queryByLabelText('Sport filter')).not.toBeInTheDocument()
    unmount()
    renderAt('/model-picks')
    expect(screen.getByLabelText('Sport filter')).toBeInTheDocument()
  })

  it("shows the player's available balance and switches player", async () => {
    renderAt('/')
    const chip = await screen.findByRole('button', { name: 'Marcus · $10,240.50' })
    fireEvent.click(chip)
    fireEvent.click(screen.getByRole('button', { name: /Sam/ }))
    expect(useUserStore.getState().currentUserName).toBe('Sam')
  })
})
```

- [ ] **Step 3: Run the tests and confirm they fail**

Run: `cd frontend && npx vitest run src/components/Layout.test.tsx`
Expected: FAIL. The wordmark, tabs and chip don't exist yet.

- [ ] **Step 4: Write `ResearchMenu.tsx`, `MainTabs.tsx` and `PlayerChip.tsx`**

`frontend/src/components/ResearchMenu.tsx`:

```tsx
import { NavLink } from 'react-router-dom'

export const RESEARCH_LINKS: [string, string][] = [
  ['/model-picks', 'Model Picks'],
  ['/props', 'Player Props'],
  ['/track-record', 'Track Record'],
  ['/backtesting', 'Backtesting'],
  ['/faq', 'FAQ'],
  ['/admin', 'Admin'],
]

export default function ResearchMenu({ open, onClose }: { open: boolean; onClose: () => void }) {
  if (!open) return null
  return (
    <>
      <div className="sb-research-overlay" onClick={onClose} />
      <nav className="sb-research" aria-label="Research">
        {RESEARCH_LINKS.map(([path, label]) => (
          <NavLink key={path} to={path} onClick={onClose}
            className={({ isActive }) => (isActive ? 'active' : '')}>{label}</NavLink>
        ))}
      </nav>
    </>
  )
}
```

`frontend/src/components/MainTabs.tsx`:

```tsx
import { NavLink } from 'react-router-dom'

const TABS = [
  { path: '/', label: 'Lobby', icon: '🏟️' },
  // Phase 3 moves My Bets to /bets; until then it is the paper-trading page.
  { path: '/paper-trading', label: 'My Bets', icon: '🎟️' },
]

export default function MainTabs({ className, researchActive, onResearch }: {
  className: string; researchActive: boolean; onResearch: () => void
}) {
  return (
    <nav className={className} aria-label="Main navigation">
      {TABS.map(t => (
        <NavLink key={t.path} to={t.path} end={t.path === '/'}
          className={({ isActive }) => `sb-tab ${isActive ? 'active' : ''}`}>
          <span aria-hidden="true">{t.icon}</span><span>{t.label}</span>
        </NavLink>
      ))}
      <button type="button" className={`sb-tab ${researchActive ? 'active' : ''}`} onClick={onResearch}>
        <span aria-hidden="true">📚</span><span>Research</span>
      </button>
    </nav>
  )
}
```

Add one rule to `sportsbook.css`, `.sb-topnav .sb-tab span[aria-hidden] { display: none; }`,
so that desktop pills show the label only.

`frontend/src/components/PlayerChip.tsx`:

```tsx
import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'
import { useUserStore } from '../stores/userStore'
import { formatMoney } from '../lib/board'

export default function PlayerChip() {
  const { currentUserName, setCurrentUserName } = useUserStore()
  const users = useQuery({ queryKey: ['users', 'list'], queryFn: api.users.list, refetchInterval: 60_000 })
  const [open, setOpen] = useState(false)
  const me = users.data?.find(u => u.name === currentUserName)
  return (
    <div style={{ marginLeft: 'auto', position: 'relative' }}>
      <button type="button" className="sb-chip" onClick={() => setOpen(o => !o)} aria-haspopup="menu">
        {me ? `${me.name} · ${formatMoney(me.available_balance)}` : 'Choose player'}
      </button>
      {open && (
        <div className="sb-chip-menu" role="menu">
          {(users.data ?? []).map(u => (
            <button key={u.id} type="button" aria-current={u.name === currentUserName}
              onClick={() => { setCurrentUserName(u.name); setOpen(false) }}>
              <span>{u.name}</span><span>{formatMoney(u.available_balance)}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
```

The player rows are plain buttons with no `role="menuitem"`, so the test's
`getByRole('button', { name: /Sam/ })` finds them.

- [ ] **Step 5: Rewrite `frontend/src/components/Layout.tsx`**

```tsx
import { useState } from 'react'
import { Link, Outlet, useLocation } from 'react-router-dom'
import { useAppStore } from '../stores/appStore'
import { useWebSocket } from '../hooks/useWebSocket'
import MainTabs from './MainTabs'
import PlayerChip from './PlayerChip'
import ResearchMenu, { RESEARCH_LINKS } from './ResearchMenu'

const SPORTS = ['all', 'nba', 'nfl', 'ncaab', 'ncaaf', 'mlb']

export default function Layout() {
  const [researchOpen, setResearchOpen] = useState(false)
  const { sport, setSport } = useAppStore()
  const { pathname } = useLocation()
  const onResearch = RESEARCH_LINKS.some(([path]) => pathname.startsWith(path))
  const toggle = () => setResearchOpen(o => !o)
  useWebSocket()

  return (
    <>
      <header className="sb-topbar">
        <Link to="/" className="sb-wordmark" aria-label="METRIC EDGE home">METRIC <span>EDGE</span></Link>
        <MainTabs className="sb-topnav" researchActive={onResearch} onResearch={toggle} />
        <PlayerChip />
      </header>
      {onResearch && (
        <div className="header-sport-filter" aria-label="Sport filter" style={{ display: 'flex', padding: '0.5rem 1rem' }}>
          {SPORTS.map((s) => (
            <button key={s} className={`sport-filter-btn ${sport === s ? 'active' : ''}`} onClick={() => setSport(s)}>
              {s.toUpperCase()}
            </button>
          ))}
        </div>
      )}
      <ResearchMenu open={researchOpen} onClose={() => setResearchOpen(false)} />
      <main className="main-content"><Outlet /></main>
      <MainTabs className="sb-tabbar" researchActive={onResearch} onResearch={toggle} />
    </>
  )
}
```

The inline `display: flex` overrides the old `.header-sport-filter
{ display: none }` at 768px. Research pages keep their sport filter on
phones, which they lost before.

The test queries `getByRole('link', { name: /METRIC EDGE/ })`. The
`aria-label` "METRIC EDGE home" matches it.

- [ ] **Step 6: Update the routes in `frontend/src/App.tsx`**

Add imports for `Lobby` and `GameDetail`, then change the route block to:

```tsx
              <Route path="/" element={<Layout />}>
                <Route index element={<Lobby />} />
                <Route path="game/:id" element={<GameDetail />} />
                <Route path="model-picks" element={<TodaysPicks />} />
                <Route path="props" element={<PlayerProps />} />
                <Route path="backtesting" element={<Backtesting />} />
                <Route path="track-record" element={<TrackRecord />} />
                <Route path="paper-trading" element={<PaperTrading />} />
                <Route path="faq" element={<FAQ />} />
                <Route path="admin" element={<Admin />} />
              </Route>
```

Delete `frontend/src/components/BottomNav.tsx` and
`frontend/src/components/MobileMenu.tsx`.

- [ ] **Step 7: Run the Layout tests, then the whole frontend suite and build**

Run: `cd frontend && npx vitest run src/components/Layout.test.tsx && npm test && npm run build`
Expected: all pass, and the build succeeds.

Fix any existing test that asserted on the old header, for example a
"Today's Picks" nav link, so that it asserts the new navigation. Never delete
an assertion without a replacement.

- [ ] **Step 8: Mutation-check**

Make each change, confirm a test fails, then restore:
1. Render the sport filter unconditionally.
2. In `PlayerChip`, show `current_balance` instead of `available_balance`.

- [ ] **Step 9: Commit**

```bash
git add frontend/src/components/MainTabs.tsx frontend/src/components/PlayerChip.tsx frontend/src/components/ResearchMenu.tsx frontend/src/components/Layout.tsx frontend/src/components/Layout.test.tsx frontend/src/App.tsx frontend/src/sportsbook.css
git rm frontend/src/components/BottomNav.tsx frontend/src/components/MobileMenu.tsx
git commit -m "feat(nav): sportsbook top bar, player chip, tab bar, Research menu; Lobby is home

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

Also add any test files fixed in Step 7 to this commit, each by name.

---

### Task 8: Verify end to end, then hand off for merge

**Files:** none are created. This task checks the whole branch.

- [ ] **Step 1: Run the full suites**

```bash
cd /c/Users/mwill/Documents/mwilliams2733/sports_picks
.venv/Scripts/python -m pytest backend -q
cd frontend && npm test && npm run build
```

Expected: the backend matches its last baseline, 2302, plus the new board
tests, with 0 failures. The frontend passes all tests, and the build
succeeds.

- [ ] **Step 2: Do a visual check against a db snapshot**

Follow the memory note `sports-picks-frontend-css`: a `:8001` build with a db
copy made with the sqlite backup API, never `cp`.
1. Serve the built `frontend/dist` through the API on :8001 against the
   snapshot from Task 1, Step 7.
2. Open it with claude-in-chrome at 390×844 (phone) and 1280×800 (desktop).
3. Check each of these:
   1. The Lobby is home. The sport tabs, day headers and game cards show
      their spread, moneyline and total tiles.
   2. Locked tiles show 🔒, and hovering one shows its reason.
   3. Tapping a price opens the bet modal with the right side and price.
      **Do not place a bet.**
   4. `/game/<id>` shows the lines tab, and the props tab shows the
      Over/Under tiles.
   5. On a phone, the tab bar is at the bottom, and the Research sheet opens
      and its links work. On desktop, the pills are in the top bar.
   6. The player chip shows the available balance.
   7. The Research pages (Model Picks, Track Record) are legible in the new
      palette, with no white-on-green.
   8. Stopping the :8001 server shows the offline banner and locks the
      tiles.
4. Save screenshots for the owner.

- [ ] **Step 3: Report to the owner and wait for "merge"**

Summarise for the owner:
- what shipped;
- the board timing from Task 1, Step 7;
- the screenshots;
- the `/model-picks` route change.

Do **not** merge or restart until the owner says so.

- [ ] **Step 4: Merge and restart, on the owner's OK**

```bash
git checkout master
git merge --no-ff feat/sportsbook-phase-1 -m "Merge: sportsbook phase 1 — lobby

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
cd frontend && npm run build
```

1. **Restart the app server** so it serves the new build and the new route.
   Per the memory note `sports-picks-scheduler-operations`, save
   `scheduler.log.prev` first, then restart with
   `Start-Process powershell -File scripts\start_scheduler.ps1` (detached).
2. **Confirm with a real call:** `GET /paper/board` on :8000 returns games,
   and the tunnel URL loads the Lobby.
3. **Update memory:** set `sports-picks-sportsbook-ui.md` to
   "Phase 1 merged <sha>".

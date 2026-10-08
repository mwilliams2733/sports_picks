# Sportsbook Phase 2 — Bet Slip — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the one-bet pop-up with a sportsbook bet slip.
- Tapping prices builds a slip of singles or a parlay, with quick stakes, an inline PIN and a ticket receipt.
- The server refuses a bet whose price moved since the player saw it, unless they ticked "Accept any odds changes".

**Architecture:**
- **Backend:** the two bet endpoints in `backend/api/users.py` gain optional `expected_odds` / `expected_line` on each leg. A mismatch against the freshly priced quote raises 409 `price_moved`, and nothing is written. Without the fields, a request is priced exactly as today.
- **Frontend:**
  - `lib/slip.ts` holds the pure helpers.
  - `stores/slipStore.ts` is a zustand store persisted to `localStorage`.
  - `components/BetSlip.tsx` and `components/BetReceipt.tsx` are mounted once in `Layout`.
  - Every bet entry point feeds the slip instead of `BetModal`, which is deleted: the Lobby tiles, the game page, the Model Picks strip, and Research's "Bet This" buttons.
  - The PaperTrading page's pick form and parlay builder are retired.

**Tech Stack:** FastAPI, pydantic v2, SQLAlchemy, pytest; React 19, react-router 7, @tanstack/react-query 5, zustand 5 (`persist`), vitest and Testing Library; plain CSS.

**Spec:** `docs/superpowers/specs/2026-10-07-sportsbook-ui-design.md`. This plan implements §6, the "join" half of §4's player chip, §11 for the slip, and the Phase 2 row of §13.

## Global Constraints

- **Prices.** Every price a bet is charged comes from `pricing.price` on the server. The slip shows the tile's price and sends it as `expected_odds`. The server decides the price, the balance, whether the game has started, and whether the PIN is right.
- **Old clients keep working.** A request without `expected_odds` / `expected_line` behaves exactly as before. The Claude-picks script (`logs-archive/`) uses that shape and must keep working unchanged.
- **Comparison.** It is exact on odds and on line (spec §6). A prop sends no `expected_line`, because the line is part of a prop bet's identity and a prop leg that sends one is refused with 422.
- **Defaults.**
  - Quick stakes are $25 / $50 / $100 / Max, where Max is the available balance.
  - "Accept any odds changes" is off by default and kept in `localStorage`.
  - A new leg's stake defaults to $25 (the smallest chip).
- **PIN.** The identity model is unchanged: a name plus a 4–6 digit PIN, kept in `sessionStorage` per tab (`lib/secrets.ts`). A 401 clears the stored PIN.
- **Branding and styles.**
  - Branding is "METRIC EDGE" only.
  - Styles go in `frontend/src/sportsbook.css`. `App.css` is never imported.
  - Animations are off under `prefers-reduced-motion`.
- **Never run `npm run build` in the main working tree on a branch.** The live app on :8000 serves `frontend/dist` straight from disk, so a branch build goes live to friends at once (memory `sports-picks-dist-is-live`).
  - Type-check per task with `npx tsc -p tsconfig.app.json --noEmit`.
  - Build only in the Task 7 worktree, and on master at merge.
- **Commits.**
  - Messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
  - Never stage `config.yaml`, which holds the owner's uncommitted edit. Stage only the named files.
- **Mutation checks.** Mutation-check each guard. After every Python write and restore, delete the touched module's `.pyc` (memory `sports-picks-mutation-pyc-trap`).

## Review Focus

1. **A price moves between the tap and Place.** A model pick added from Research at the model's price is the common case. The server refuses with 409 and writes nothing. The leg shows "Odds changed −110 → −125", the button reads **Accept & Place**, and the next attempt sends the new price. Tests are in Task 1 (409, nothing written) and Task 3 (UI).
2. **One single of several is refused** (stale price, insufficient balance). The others are placed and receipted, and the refused leg stays on the slip with the server's own message. It must never all-or-nothing, and never show a generic "Failed". Test is in Task 3.
3. **A game starts while its leg sits on the slip.** The leg shows "Betting closed". Singles skip it, and a parlay containing it can't be placed. The server refuses anyway. Tests are in Task 2 (`isClosed`) and Task 3.
4. **A fresh tab, or a wrong PIN.** A 401 clears the stored PIN, the PIN field appears, and the slip keeps its legs. The loop **stops** at the first 401, so one wrong PIN doesn't burn several attempts toward the 429 lockout. Test is in Task 3.
5. **Storage blocked or corrupt** (private mode, a hand-edited value). The slip still works in memory, and a corrupt saved slip is ignored rather than crashing the app on load. Tests are in Task 2.

---

### Task 1: Price-move protection on both bet endpoints

**Files:**
- Modify: `backend/api/users.py`. This covers `GameLeg`, `PropLeg`, a new `_check_expected`, `place_pick`, and `place_parlay`'s pricing loop.
- Test: `backend/tests/test_paper_price_move.py`

**Interfaces:**
- **Consumes:** `pricing.Quote` (`odds`, `line`, `pick_value`) and the existing `_priced`.
- **Produces:**
  - **Request fields:**
    - Each game leg, and the straight game bet, accepts optional `expected_odds: int` and `expected_line: float | null`.
    - Each prop leg, and the straight prop bet, accepts optional `expected_odds: int` only.
  - **409 body:** `{"detail": {"reason": "price_moved", "message": str, "odds": int, "line": float | null, "pick_value": str}}`. A parlay's body also carries `"leg": <0-based index>`.

- [ ] **Step 1: Write the failing tests**

```python
"""Price-move protection on bet placement (sportsbook spec 2026-10-07 §6).

The slip sends the price and line the player saw. A different fresh quote is
refused with 409 ``price_moved`` and nothing is written; a request without
them is priced exactly as before (the Claude-picks script sends neither).
"""
from fastapi.testclient import TestClient

from backend.api.main import create_app
from backend.database import get_session
from backend.models import ActivityFeed, Base, Game, PaperPick, Parlay, Team
from backend.tests.auth_helpers import ALL_HEADERS, TEST_PIN
from backend.tests.pricing_helpers import seed_fresh_odds, seed_fresh_prop
from backend.time_utils import et_today


def _client():
    client = TestClient(create_app(":memory:"), headers=ALL_HEADERS)
    Base.metadata.create_all(client.app.state.engine)
    return client


def _games(client, n=1):
    """n scheduled NFL games, each with fresh odds: ML -110/-110, spread
    HOME -3.5 / AWAY +3.5 at -110, total 220.5 at -110."""
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


def _user(client):
    r = client.post("/users/", json={"name": "friend", "pin": TEST_PIN})
    assert r.status_code == 200
    return r.json()["id"]


def _written(client):
    """(straight + leg rows, parlays, pick_placed feed events)."""
    s = get_session(client.app.state.engine)
    try:
        return (s.query(PaperPick).count(), s.query(Parlay).count(),
                s.query(ActivityFeed).filter(ActivityFeed.event_type == "pick_placed").count())
    finally:
        s.close()


def _available(client, uid):
    return client.get(f"/users/{uid}").json()["available_balance"]


def test_a_matching_expected_price_and_line_places_the_bet():
    client = _client()
    [gid] = _games(client)
    uid = _user(client)
    r = client.post(f"/users/{uid}/picks", json={
        "game_id": gid, "pick_type": "spread", "side": "HOME", "stake": 50,
        "expected_odds": -110, "expected_line": -3.5})
    assert r.status_code == 200, r.text
    assert (r.json()["odds"], r.json()["line"]) == (-110, -3.5)


def test_a_moved_price_is_refused_with_the_new_price_and_nothing_written():
    client = _client()
    [gid] = _games(client)
    uid = _user(client)
    before = _available(client, uid)
    r = client.post(f"/users/{uid}/picks", json={
        "game_id": gid, "pick_type": "moneyline", "side": "HOME", "stake": 50,
        "expected_odds": -105, "expected_line": None})
    assert r.status_code == 409
    assert r.json()["detail"] == {"reason": "price_moved", "message": "The price moved to HOME ML -110.",
                                  "odds": -110, "line": None, "pick_value": "HOME ML"}
    assert _written(client) == (0, 0, 0)
    assert _available(client, uid) == before


def test_a_moved_line_is_refused_even_at_the_same_price():
    client = _client()
    [gid] = _games(client)
    uid = _user(client)
    r = client.post(f"/users/{uid}/picks", json={
        "game_id": gid, "pick_type": "spread", "side": "HOME", "stake": 50,
        "expected_odds": -110, "expected_line": -3.0})
    assert r.status_code == 409
    assert (r.json()["detail"]["line"], r.json()["detail"]["pick_value"]) == (-3.5, "HOME -3.5")
    assert _written(client) == (0, 0, 0)


def test_a_moneyline_expects_no_line():
    client = _client()
    [gid] = _games(client)
    uid = _user(client)
    base = {"game_id": gid, "pick_type": "moneyline", "side": "AWAY", "stake": 10, "expected_odds": -110}
    assert client.post(f"/users/{uid}/picks", json={**base, "expected_line": 1.5}).status_code == 409
    assert client.post(f"/users/{uid}/picks", json={**base, "expected_line": None}).status_code == 200


def test_without_expected_fields_the_bet_is_priced_as_before():
    """The Claude-picks script's request shape (logs-archive/)."""
    client = _client()
    [gid] = _games(client)
    uid = _user(client)
    r = client.post(f"/users/{uid}/picks", json={
        "game_id": gid, "pick_type": "moneyline", "side": "HOME", "stake": 100})
    assert r.status_code == 200, r.text
    assert r.json()["odds"] == -110


def test_a_prop_compares_odds_only_and_refuses_an_expected_line():
    client = _client()
    [gid] = _games(client)
    seed_fresh_prop(client.app.state.engine, gid)          # QB One Over 225.5 pass yds at -110
    uid = _user(client)
    base = {"game_id": gid, "pick_type": "prop", "prop_player": "QB One",
            "prop_market": "player_pass_yds", "outcome": "Over", "line": 225.5, "stake": 20}
    assert client.post(f"/users/{uid}/picks", json={**base, "expected_odds": -120}).status_code == 409
    assert client.post(f"/users/{uid}/picks", json={**base, "expected_line": 225.5}).status_code == 422
    assert client.post(f"/users/{uid}/picks", json={**base, "expected_odds": -110}).status_code == 200


def test_a_moved_parlay_leg_is_named_and_nothing_is_written():
    client = _client()
    g1, g2 = _games(client, 2)
    uid = _user(client)
    legs = [{"game_id": g1, "pick_type": "moneyline", "side": "HOME",
             "expected_odds": -110, "expected_line": None},
            {"game_id": g2, "pick_type": "over_under", "side": "Over",
             "expected_odds": -110, "expected_line": 219.5}]
    r = client.post(f"/users/{uid}/parlay", json={"legs": legs, "stake": 25})
    assert r.status_code == 409
    assert (r.json()["detail"]["leg"], r.json()["detail"]["line"]) == (1, 220.5)
    assert _written(client) == (0, 0, 0)
    legs[1]["expected_line"] = 220.5
    assert client.post(f"/users/{uid}/parlay", json={"legs": legs, "stake": 25}).status_code == 200
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `.venv/Scripts/python -m pytest backend/tests/test_paper_price_move.py -q -p no:warnings`

Expected: most tests FAIL with 422, because the strict models forbid the unknown `expected_*` keys. `test_without_expected_fields_the_bet_is_priced_as_before` PASSES; it pins existing behaviour.

- [ ] **Step 3: Add the fields and the check in `backend/api/users.py`**

In `class GameLeg(_Strict)`, after `side: Literal[...]`, add:

```python
    # Price-move protection (sportsbook spec §6): the price and line the
    # player saw. Present -> a different fresh quote is refused with 409
    # price_moved and nothing is written; absent -> priced as before (the
    # Claude-picks script sends neither).
    expected_odds: int | None = None
    expected_line: float | None = Field(default=None, allow_inf_nan=False)
```

In `class PropLeg(_Strict)`, after `line: float = Field(allow_inf_nan=False)`, add:

```python
    # A prop's line is part of the bet itself, so only the price is checked;
    # an expected_line is refused (extra="forbid") rather than ignored.
    expected_odds: int | None = None
```

After `def _priced(...)`, add:

```python
def _check_expected(leg, quote: pricing.Quote, index: int | None = None) -> None:
    """Refuse with 409 ``price_moved`` when the player saw a different price
    or line than the fresh quote. Only the fields the request sent are
    compared: an explicit ``expected_line: null`` (a moneyline) must find no
    line, and a request with neither field is priced as before."""
    moved = leg.expected_odds is not None and leg.expected_odds != quote.odds
    if "expected_line" in leg.model_fields_set and leg.expected_line != quote.line:
        moved = True
    if not moved:
        return
    detail = {"reason": "price_moved",
              "message": f"The price moved to {quote.pick_value} {quote.odds:+d}.",
              "odds": quote.odds, "line": quote.line, "pick_value": quote.pick_value}
    if index is not None:
        detail["leg"] = index
    raise HTTPException(status_code=409, detail=detail)
```

In `place_pick`, change `quote = _priced(session, game, bet)` to:

```python
        quote = _priced(session, game, bet)
        _check_expected(bet, quote)
```

In `place_parlay`, replace the pricing loop:

```python
        quotes = []
        for leg in body.legs:
            game = session.get(Game, leg.game_id)
            if not game:
                raise HTTPException(status_code=404, detail=f"Game {leg.game_id} not found")
            if not _open_for_betting(game):
                raise HTTPException(
                    status_code=400,
                    detail=f"Betting has closed: game {leg.game_id} has already started")
            quotes.append((leg, _priced(session, game, leg)))
```

with:

```python
        quotes = []
        for i, leg in enumerate(body.legs):
            game = session.get(Game, leg.game_id)
            if not game:
                raise HTTPException(status_code=404, detail=f"Game {leg.game_id} not found")
            if not _open_for_betting(game):
                raise HTTPException(
                    status_code=400,
                    detail=f"Betting has closed: game {leg.game_id} has already started")
            quote = _priced(session, game, leg)
            _check_expected(leg, quote, i)
            quotes.append((leg, quote))
```

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `.venv/Scripts/python -m pytest backend/tests/test_paper_price_move.py backend/tests/test_paper_bet_odds_guard.py backend/tests/test_bet_placement_race.py backend/tests/test_player_pins.py -q -p no:warnings`

Expected: all pass.

- [ ] **Step 5: Mutation-check**

Make each change, run `test_paper_price_move.py`, confirm the named test fails, then restore. After every write and every restore, delete `backend/api/__pycache__/users.*.pyc`.
1. Remove `_check_expected(bet, quote)` from `place_pick`. The moved-price test must fail.
2. Remove `_check_expected(leg, quote, i)` from `place_parlay`. The parlay test must fail.
3. Delete the `expected_line` clause (both lines). The moved-line test must fail.
4. Delete `detail["leg"] = index`. The parlay test must fail.

- [ ] **Step 6: Commit**

```bash
git checkout feat/sportsbook-phase-2    # created with the plan commit
git add backend/api/users.py backend/tests/test_paper_price_move.py
git commit -m "feat(paper): refuse a bet whose price or line moved (expected_odds/expected_line, 409 price_moved)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Slip helpers, store, types and the structured-error message

**Files:**
- Modify: `frontend/src/types.ts` (append the types)
- Modify: `frontend/src/api/client.ts`. This covers `ApiError` for a structured `detail`, and the `placePick` / `placeParlay` request types.
- Create: `frontend/src/lib/slip.ts`
- Create: `frontend/src/stores/slipStore.ts`
- Test: `frontend/src/lib/slip.test.ts`, `frontend/src/stores/slipStore.test.ts`, and `frontend/src/api/client.test.ts` (append)

**Interfaces:**
- **Consumes:** the Task 1 409 body, plus `legFromPick`, `marketKey`, `addOrReplaceLeg` and `resolveLabel` from `lib/quotes.ts`.
- **Produces:**
  - **Types:** `BetRequest`, `SlipSelection`, `SlipLeg`, `ReceiptBet` and `Receipt`.
  - **`lib/slip.ts`:**
    - **Constants:** `QUICK_STAKES` and `DEFAULT_STAKE = 25`.
    - **Leg helpers:** `sameLeg(a, b)`, `lineFromPick(pickType, value)` and `selectionFromPick(p: PickInput): SlipSelection | null`.
    - **Bet helpers:** `toWin(stake, odds)`, `isClosed(l, now?)`, `betRequest(l, acceptAnyOdds): BetRequest` and `hasSameGame(legs)`.
    - **Errors:** `refusal(e): Refusal`.
  - **`stores/slipStore.ts`:** `useSlip` with
    - **State:** `legs`, `mode`, `parlayStake`, `sameStake`, `acceptAnyOdds`, `open`, `receipt`.
    - **Leg actions:** `add`, `toggle`, `remove`, `setStake`, `priceMoved`, `setError`, `refill`, `clear`.
    - **Settings:** `setMode`, `setParlayStake`, `setSameStake`, `setAcceptAnyOdds`.
    - **Sheet and receipt:** `setOpen`, `setReceipt`.
  - It also exports `SLIP_DEFAULTS`, used to reset the store in tests.

- [ ] **Step 1: Append the types to `frontend/src/types.ts`**

```ts
/** A bet as the slip sends it: the leg plus the price and line the player saw
 *  (both omitted under "Accept any odds changes"). Props send no line -- the
 *  line is part of a prop bet. */
export type BetRequest = BetLeg & { expected_odds?: number; expected_line?: number | null }

/** A side a tile or a model pick puts on the slip. `odds`/`line` are the
 *  price the player saw, sent as expected_odds/expected_line. */
export interface SlipSelection {
  leg: BetLeg; label: string; gameLabel: string; startTime: string | null;
  odds: number; line: number | null; homeTeam: string; awayTeam: string;
}

export interface SlipLeg extends SlipSelection {
  stake: number
  /** The price the player saw before the server answered price_moved. */
  movedFrom?: { odds: number; line: number | null } | null
  /** The server's refusal for this leg on the last attempt. */
  error?: string | null
}

export interface ReceiptBet {
  id: number; kind: 'single' | 'parlay'; labels: string[]; stake: number;
  /** What the server charged, and what the slip showed when that differed. */
  odds: number; shownOdds: number | null; toWin: number;
}

export interface Receipt { bets: ReceiptBet[]; legs: SlipLeg[]; placedAt: string }
```

- [ ] **Step 2: Write the failing tests**

Append to `frontend/src/api/client.test.ts`:

```ts
describe('ApiError', () => {
  it('reads the message of a structured detail (409 price_moved)', () => {
    const e = new ApiError(409, { detail: { reason: 'price_moved', message: 'The price moved to HOME ML -110.',
      odds: -110, line: null, pick_value: 'HOME ML' } })
    expect(e.message).toBe('The price moved to HOME ML -110.')
  })
})
```

Create `frontend/src/lib/slip.test.ts`:

```ts
import { describe, it, expect } from 'vitest'
import { ApiError } from '../api/client'
import type { SlipLeg } from '../types'
import { betRequest, hasSameGame, isClosed, lineFromPick, refusal, sameLeg, selectionFromPick, toWin } from './slip'

const slipLeg = (over: Partial<SlipLeg> = {}): SlipLeg => ({
  leg: { game_id: 1, pick_type: 'spread', side: 'AWAY' }, label: 'Cowboys +3', gameLabel: 'Cowboys @ Bucs',
  startTime: null, odds: -110, line: 3, homeTeam: 'Bucs', awayTeam: 'Cowboys', stake: 25, ...over,
})

describe('sameLeg', () => {
  it('is the same market and the same side', () => {
    const a = { game_id: 1, pick_type: 'spread' as const, side: 'AWAY' as const }
    expect(sameLeg(a, { ...a })).toBe(true)
    expect(sameLeg(a, { ...a, side: 'HOME' })).toBe(false)
    expect(sameLeg(a, { ...a, game_id: 2 })).toBe(false)
  })
  it('compares a prop by outcome and line', () => {
    const p = { game_id: 1, pick_type: 'prop' as const, prop_player: 'QB', prop_market: 'player_pass_yds',
      outcome: 'Over' as const, line: 225.5 }
    expect(sameLeg(p, { ...p })).toBe(true)
    expect(sameLeg(p, { ...p, line: 230.5 })).toBe(false)
    expect(sameLeg(p, { ...p, outcome: 'Under' })).toBe(false)
  })
})

describe('lineFromPick', () => {
  it('reads the line a stored model pick label carries', () => {
    expect(lineFromPick('spread', 'AWAY +6.5')).toBe(6.5)
    expect(lineFromPick('spread', 'HOME -1.5')).toBe(-1.5)
    expect(lineFromPick('over_under', 'Over 7')).toBe(7)
    expect(lineFromPick('moneyline', 'HOME ML')).toBeNull()
  })
})

describe('selectionFromPick', () => {
  it('maps a stored label to a side at the price the model saw', () => {
    expect(selectionFromPick({ pickType: 'moneyline', value: 'HOME ML', label: 'NYY ML', gameId: 1, odds: -131,
      homeTeam: 'NYY', awayTeam: 'BOS', startTime: '2099-01-01T00:00:00Z' })).toEqual({
      leg: { game_id: 1, pick_type: 'moneyline', side: 'HOME' }, label: 'NYY ML', gameLabel: 'BOS @ NYY',
      startTime: '2099-01-01T00:00:00Z', odds: -131, line: null, homeTeam: 'NYY', awayTeam: 'BOS' })
  })
  it('is null without a stored label, or when the label maps to no side', () => {
    expect(selectionFromPick({ pickType: 'moneyline', value: undefined, label: 'NYY ML', gameId: 1, odds: -131 })).toBeNull()
    expect(selectionFromPick({ pickType: 'moneyline', value: 'NYY ML', label: 'NYY ML', gameId: 1, odds: -131 })).toBeNull()
  })
  it('takes a prop line from the label', () => {
    expect(selectionFromPick({ pickType: 'prop', value: 'Jayson Tatum Over 27.5', label: 'Jayson Tatum Over 27.5 Points',
      gameId: 4, odds: -110, propMarket: 'player_points', propPlayer: 'Jayson Tatum', gameLabel: 'BOS @ NYY' }))
      .toMatchObject({ leg: { pick_type: 'prop', line: 27.5, outcome: 'Over' }, line: 27.5, gameLabel: 'BOS @ NYY' })
  })
})

describe('toWin / isClosed / hasSameGame', () => {
  it('pays American odds', () => {
    expect(toWin(100, 150)).toBe(150)
    expect(toWin(110, -110)).toBe(100)
  })
  it('closes a leg at its start time', () => {
    const now = new Date('2026-10-11T17:00:00Z')
    expect(isClosed({ startTime: '2026-10-11T17:00:00Z' }, now)).toBe(true)
    expect(isClosed({ startTime: '2026-10-11T17:01:00Z' }, now)).toBe(false)
    expect(isClosed({ startTime: null }, now)).toBe(false)
  })
  it('spots two legs on one game (an SGP)', () => {
    expect(hasSameGame([slipLeg(), slipLeg({ leg: { game_id: 1, pick_type: 'moneyline', side: 'HOME' } })])).toBe(true)
    expect(hasSameGame([slipLeg(), slipLeg({ leg: { game_id: 2, pick_type: 'moneyline', side: 'HOME' } })])).toBe(false)
  })
})

describe('betRequest', () => {
  it('sends the price and line the player saw', () => {
    expect(betRequest(slipLeg(), false)).toEqual({ game_id: 1, pick_type: 'spread', side: 'AWAY',
      expected_odds: -110, expected_line: 3 })
    expect(betRequest(slipLeg({ leg: { game_id: 1, pick_type: 'moneyline', side: 'HOME' }, line: null }), false))
      .toEqual({ game_id: 1, pick_type: 'moneyline', side: 'HOME', expected_odds: -110, expected_line: null })
  })
  it('sends no line for a prop', () => {
    const leg = { game_id: 1, pick_type: 'prop' as const, prop_player: 'QB', prop_market: 'player_pass_yds',
      outcome: 'Over' as const, line: 225.5 }
    expect(betRequest(slipLeg({ leg, line: 225.5 }), false)).toEqual({ ...leg, expected_odds: -110 })
  })
  it('sends the bare leg when any odds change is accepted', () => {
    expect(betRequest(slipLeg(), true)).toEqual({ game_id: 1, pick_type: 'spread', side: 'AWAY' })
  })
})

describe('refusal', () => {
  it('reads a price_moved 409', () => {
    const e = new ApiError(409, { detail: { reason: 'price_moved', message: 'm', odds: -125, line: 3.5,
      pick_value: 'AWAY +3.5', leg: 1 } })
    expect(refusal(e)).toEqual({ kind: 'moved', odds: -125, line: 3.5, pickValue: 'AWAY +3.5', leg: 1 })
  })
  it('tells a PIN failure from other refusals', () => {
    expect(refusal(new ApiError(401, { detail: 'Wrong PIN' }))).toEqual({ kind: 'pin', message: 'Wrong PIN' })
    expect(refusal(new ApiError(409, { detail: 'The price is stale — ask Marcus to refresh.' })))
      .toEqual({ kind: 'error', message: 'The price is stale — ask Marcus to refresh.' })
  })
  it('keeps the slip on a network failure', () => {
    expect(refusal(new TypeError('Failed to fetch')))
      .toEqual({ kind: 'error', message: "Couldn't reach the server — your slip is kept." })
  })
})
```

Create `frontend/src/stores/slipStore.test.ts`:

```ts
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { useSlip, SLIP_DEFAULTS } from './slipStore'
import type { SlipSelection } from '../types'

const sel = (side: 'HOME' | 'AWAY', game_id = 1): SlipSelection => ({
  leg: { game_id, pick_type: 'spread', side }, label: side === 'HOME' ? 'Bucs -3' : 'Cowboys +3',
  gameLabel: 'Cowboys @ Bucs', startTime: null, odds: -110, line: side === 'HOME' ? -3 : 3,
  homeTeam: 'Bucs', awayTeam: 'Cowboys',
})

beforeEach(() => {
  localStorage.clear()
  useSlip.setState({ ...SLIP_DEFAULTS })
})
afterEach(() => vi.restoreAllMocks())

describe('slip store', () => {
  it('toggle adds at the default stake, and the same tile again removes it', () => {
    useSlip.getState().toggle(sel('AWAY'))
    expect(useSlip.getState().legs).toEqual([{ ...sel('AWAY'), stake: 25, movedFrom: null, error: null }])
    useSlip.getState().toggle(sel('AWAY'))
    expect(useSlip.getState().legs).toEqual([])
  })

  it('the other side of a market already on the slip swaps it', () => {
    useSlip.getState().toggle(sel('AWAY'))
    useSlip.getState().toggle(sel('HOME'))
    expect(useSlip.getState().legs.map(l => l.label)).toEqual(['Bucs -3'])
  })

  it('add never removes, and clears a showing receipt', () => {
    useSlip.setState({ receipt: { bets: [], legs: [], placedAt: 'x' } })
    useSlip.getState().add(sel('AWAY'))
    useSlip.getState().add(sel('AWAY'))
    expect(useSlip.getState().legs).toHaveLength(1)
    expect(useSlip.getState().receipt).toBeNull()
  })

  it('same stake for all moves every leg together', () => {
    useSlip.getState().add(sel('AWAY', 1))
    useSlip.getState().add(sel('AWAY', 2))
    useSlip.getState().setSameStake(true)
    useSlip.getState().setStake(sel('AWAY', 1).leg, 80)
    expect(useSlip.getState().legs.map(l => l.stake)).toEqual([80, 80])
  })

  it('a price move keeps the first price seen and relabels from the server label', () => {
    useSlip.getState().add(sel('AWAY'))
    useSlip.getState().priceMoved(sel('AWAY').leg, -120, 3.5, 'AWAY +3.5')
    useSlip.getState().priceMoved(sel('AWAY').leg, -125, 3.5, 'AWAY +3.5')
    expect(useSlip.getState().legs[0]).toMatchObject({
      odds: -125, line: 3.5, label: 'Cowboys +3.5', movedFrom: { odds: -110, line: 3 } })
  })

  it('persists legs and the odds-change setting, not the open sheet', () => {
    useSlip.getState().add(sel('AWAY'))
    useSlip.getState().setAcceptAnyOdds(true)
    useSlip.getState().setOpen(true)
    const saved = JSON.parse(localStorage.getItem('sp-slip')!).state
    expect(saved.legs).toHaveLength(1)
    expect(saved.acceptAnyOdds).toBe(true)
    expect(saved.open).toBeUndefined()
  })

  it('keeps working in memory when storage throws (Review Focus 5)', () => {
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('blocked') })
    expect(() => useSlip.getState().add(sel('AWAY'))).not.toThrow()
    expect(useSlip.getState().legs).toHaveLength(1)
  })

  it('ignores a corrupt saved slip (Review Focus 5)', async () => {
    localStorage.setItem('sp-slip', '{not json')
    await useSlip.persist.rehydrate()
    expect(useSlip.getState().legs).toEqual([])
  })
})
```

- [ ] **Step 3: Run the tests and confirm they fail**

Run: `cd frontend && npx vitest run src/lib/slip.test.ts src/stores/slipStore.test.ts src/api/client.test.ts`

Expected:
- The slip and store files FAIL to resolve `./slip` / `./slipStore`.
- The ApiError test FAILS with `"[object Object]"`.

- [ ] **Step 4: Make `ApiError` read a structured detail, and type the bet requests**

In `frontend/src/api/client.ts`, inside `ApiError`'s constructor, replace:

```ts
      : rawDetail != null ? String(rawDetail) : undefined;
```

with:

```ts
      : typeof rawDetail === 'object' && rawDetail !== null && 'message' in rawDetail
        // A structured refusal (409 price_moved) carries its own message.
        ? String((rawDetail as { message: unknown }).message)
      : rawDetail != null ? String(rawDetail) : undefined;
```

Add `BetRequest` to the `../types` import. Then change the two bet signatures:

```ts
    placePick: (userId: number, data: BetRequest & { stake: number }, pin: string) =>
```

```ts
    placeParlay: (userId: number, data: { legs: BetRequest[]; stake: number }, pin: string) => post<{
```

- [ ] **Step 5: Write `frontend/src/lib/slip.ts`**

```ts
import { ApiError } from '../api/client'
import type { BetLeg, BetRequest, SlipLeg, SlipSelection } from '../types'
import { legFromPick, marketKey } from './quotes'

export const QUICK_STAKES = [25, 50, 100] as const
export const DEFAULT_STAKE = 25

/** Two legs are the same bet: the same market and the same side (for a prop,
 *  the same outcome and line). */
export function sameLeg(a: BetLeg, b: BetLeg): boolean {
  if (marketKey(a) !== marketKey(b)) return false
  if (a.pick_type === 'prop' && b.pick_type === 'prop') return a.outcome === b.outcome && a.line === b.line
  return a.pick_type !== 'prop' && b.pick_type !== 'prop' && a.side === b.side
}

/** The line a stored model pick label carries ("AWAY +3.5" -> 3.5,
 *  "Over 7" -> 7); null for a moneyline. */
export function lineFromPick(pickType: string, value: string): number | null {
  if (pickType !== 'spread' && pickType !== 'over_under') return null
  const m = value.match(/([+-]?\d+(?:\.\d+)?)\s*$/)
  return m ? Number(m[1]) : null
}

export interface PickInput {
  pickType: string
  /** The stored label legFromPick maps ("HOME ML"); undefined when the page
   *  only has a display label ("NYY ML"), which can't be bet. */
  value: string | undefined
  label: string
  gameId: number
  odds: number
  propMarket?: string
  propPlayer?: string
  homeTeam?: string
  awayTeam?: string
  gameLabel?: string
  startTime?: string | null
}

/** A Research-page model pick as a slip selection, at the price the model saw
 *  -- the server's price_moved refusal tells the player if it has moved. */
export function selectionFromPick(p: PickInput): SlipSelection | null {
  if (p.value === undefined) return null
  const leg = legFromPick(p.pickType, p.value, p.gameId, p.propMarket, p.propPlayer)
  if (!leg) return null
  const home = p.homeTeam ?? ''
  const away = p.awayTeam ?? ''
  return {
    leg,
    label: p.label,
    gameLabel: p.gameLabel ?? (home && away ? `${away} @ ${home}` : ''),
    startTime: p.startTime ?? null,
    odds: p.odds,
    line: leg.pick_type === 'prop' ? leg.line : lineFromPick(p.pickType, p.value),
    homeTeam: home,
    awayTeam: away,
  }
}

/** Profit on a winning bet at American odds. */
export function toWin(stake: number, odds: number): number {
  return odds > 0 ? (stake * odds) / 100 : (stake * 100) / Math.abs(odds)
}

export function isClosed(l: { startTime: string | null }, now: Date = new Date()): boolean {
  return l.startTime !== null && new Date(l.startTime).getTime() <= now.getTime()
}

export function hasSameGame(legs: SlipLeg[]): boolean {
  const ids = legs.map(l => l.leg.game_id)
  return new Set(ids).size !== ids.length
}

/** The request for one leg. Props send no expected_line: the line is part of
 *  a prop bet, and the server refuses one with 422. */
export function betRequest(l: SlipLeg, acceptAnyOdds: boolean): BetRequest {
  if (acceptAnyOdds) return { ...l.leg }
  if (l.leg.pick_type === 'prop') return { ...l.leg, expected_odds: l.odds }
  return { ...l.leg, expected_odds: l.odds, expected_line: l.line }
}

export type Refusal =
  | { kind: 'moved'; odds: number; line: number | null; pickValue: string; leg?: number }
  | { kind: 'pin'; message: string }
  | { kind: 'error'; message: string }

/** What a failed bet POST means for the slip. Known refusals carry the
 *  server's own message; a network failure keeps the slip. */
export function refusal(e: unknown): Refusal {
  if (e instanceof ApiError) {
    const d = (e.body as { detail?: unknown } | null)?.detail
    if (e.status === 409 && typeof d === 'object' && d !== null
        && (d as { reason?: unknown }).reason === 'price_moved') {
      const m = d as { odds: number; line: number | null; pick_value: string; leg?: number }
      return { kind: 'moved', odds: m.odds, line: m.line, pickValue: m.pick_value, leg: m.leg }
    }
    if (e.status === 401) return { kind: 'pin', message: e.message }
    return { kind: 'error', message: e.message }
  }
  return { kind: 'error', message: "Couldn't reach the server — your slip is kept." }
}
```

- [ ] **Step 6: Write `frontend/src/stores/slipStore.ts`**

```ts
import { create } from 'zustand'
import { createJSONStorage, persist, type StateStorage } from 'zustand/middleware'
import type { BetLeg, Receipt, SlipLeg, SlipSelection } from '../types'
import { addOrReplaceLeg, marketKey, resolveLabel } from '../lib/quotes'
import { DEFAULT_STAKE, sameLeg } from '../lib/slip'

// Storage can throw (private mode, blocked site data): the slip then lives
// for this page only instead of breaking (spec §6, Review Focus 5).
const safeStorage: StateStorage = {
  getItem: (k) => { try { return localStorage.getItem(k) } catch { return null } },
  setItem: (k, v) => { try { localStorage.setItem(k, v) } catch { /* unavailable */ } },
  removeItem: (k) => { try { localStorage.removeItem(k) } catch { /* unavailable */ } },
}

export type SlipMode = 'singles' | 'parlay'

interface SlipData {
  legs: SlipLeg[]
  mode: SlipMode
  parlayStake: number
  sameStake: boolean
  acceptAnyOdds: boolean
  open: boolean
  receipt: Receipt | null
}

interface SlipActions {
  add: (s: SlipSelection) => void
  toggle: (s: SlipSelection) => void
  remove: (leg: BetLeg) => void
  setStake: (leg: BetLeg, stake: number) => void
  setMode: (mode: SlipMode) => void
  setParlayStake: (stake: number) => void
  setSameStake: (on: boolean) => void
  setAcceptAnyOdds: (on: boolean) => void
  setOpen: (open: boolean) => void
  priceMoved: (leg: BetLeg, odds: number, line: number | null, pickValue: string) => void
  setError: (leg: BetLeg, message: string | null) => void
  setReceipt: (receipt: Receipt | null) => void
  refill: (legs: SlipLeg[]) => void
  clear: () => void
}

export const SLIP_DEFAULTS: SlipData = {
  legs: [], mode: 'singles', parlayStake: DEFAULT_STAKE, sameStake: false,
  acceptAnyOdds: false, open: false, receipt: null,
}

const onMarket = (leg: BetLeg) => (l: SlipLeg) => marketKey(l.leg) === marketKey(leg)

function newLeg(st: SlipData, s: SlipSelection): SlipLeg {
  const stake = st.sameStake && st.legs[0] ? st.legs[0].stake : DEFAULT_STAKE
  return { ...s, stake, movedFrom: null, error: null }
}

export const useSlip = create<SlipData & SlipActions>()(persist((set) => ({
  ...SLIP_DEFAULTS,
  add: (s) => set(st => ({ legs: addOrReplaceLeg(st.legs, newLeg(st, s)), receipt: null })),
  // A tap on a tile already on the slip takes it off, as on a real book;
  // the other side of that market swaps in (addOrReplaceLeg).
  toggle: (s) => set(st => (st.legs.some(l => sameLeg(l.leg, s.leg))
    ? { legs: st.legs.filter(l => !sameLeg(l.leg, s.leg)) }
    : { legs: addOrReplaceLeg(st.legs, newLeg(st, s)), receipt: null })),
  remove: (leg) => set(st => ({ legs: st.legs.filter(l => !onMarket(leg)(l)) })),
  setStake: (leg, stake) => set(st => ({
    legs: st.legs.map(l => (st.sameStake || onMarket(leg)(l) ? { ...l, stake } : l)) })),
  setMode: (mode) => set({ mode }),
  setParlayStake: (parlayStake) => set({ parlayStake }),
  setSameStake: (sameStake) => set(st => ({
    sameStake,
    legs: sameStake && st.legs[0] ? st.legs.map(l => ({ ...l, stake: st.legs[0].stake })) : st.legs })),
  setAcceptAnyOdds: (acceptAnyOdds) => set({ acceptAnyOdds }),
  setOpen: (open) => set({ open }),
  priceMoved: (leg, odds, line, pickValue) => set(st => ({
    legs: st.legs.map(l => (onMarket(leg)(l) ? {
      ...l,
      movedFrom: l.movedFrom ?? { odds: l.odds, line: l.line },
      odds, line, error: null,
      // A game label is rebuilt from the server's ("AWAY +3.5" -> "Cowboys
      // +3.5"); a prop's line can't move, so its label stands.
      label: l.leg.pick_type === 'prop' || !l.homeTeam ? l.label : resolveLabel(pickValue, l.homeTeam, l.awayTeam),
    } : l)) })),
  setError: (leg, error) => set(st => ({ legs: st.legs.map(l => (onMarket(leg)(l) ? { ...l, error } : l)) })),
  setReceipt: (receipt) => set({ receipt }),
  refill: (legs) => set(st => ({
    legs: legs.reduce((acc, l) => addOrReplaceLeg(acc, { ...l, movedFrom: null, error: null }), st.legs) })),
  clear: () => set({ legs: [] }),
}), {
  name: 'sp-slip',
  storage: createJSONStorage(() => safeStorage),
  partialize: (st) => ({ legs: st.legs, mode: st.mode, parlayStake: st.parlayStake,
    sameStake: st.sameStake, acceptAnyOdds: st.acceptAnyOdds }),
}))
```

- [ ] **Step 7: Run the tests and confirm they pass**

Run: `cd frontend && npx vitest run src/lib/slip.test.ts src/stores/slipStore.test.ts src/api/client.test.ts && npx tsc -p tsconfig.app.json --noEmit && npx eslint .`

Expected: all pass, and both tsc and eslint are clean.

**If the corrupt-slip test fails** because `rehydrate()` rejects: make `safeStorage.getItem` return `null` when `JSON.parse` of the stored value throws. Do not loosen the test.

- [ ] **Step 8: Mutation-check**

Make each change, confirm a test fails, then restore:
1. In `betRequest`, drop the prop branch, so a prop sends `expected_line`.
2. In `refusal`, drop the `price_moved` branch.
3. In `safeStorage.setItem`, remove the try/catch.
4. In `toggle`, always add.
5. In `priceMoved`, set `movedFrom: { odds: l.odds, line: l.line }` unconditionally.

- [ ] **Step 9: Commit**

```bash
git add frontend/src/types.ts frontend/src/api/client.ts frontend/src/api/client.test.ts frontend/src/lib/slip.ts frontend/src/lib/slip.test.ts frontend/src/stores/slipStore.ts frontend/src/stores/slipStore.test.ts
git commit -m "feat(slip): slip helpers, persisted slip store, structured refusal messages

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: The bet slip and receipt UI, mounted in the layout

**Files:**
- Create: `frontend/src/components/BetSlip.tsx`
- Create: `frontend/src/components/BetReceipt.tsx`
- Modify: `frontend/src/components/Layout.tsx`. Mount `<BetSlip />`, and add the `with-slip` class on `<main>`.
- Modify: `frontend/src/sportsbook.css` (append the slip styles)
- Test: `frontend/src/components/BetSlip.test.tsx`

**Interfaces:**
- **Consumes:**
  - Task 2: `useSlip` and the slip helpers.
  - `api.users.list`, `placePick` and `placeParlay`.
  - `getPin` / `setPin`, `formatOdds`, `parlayEstimate` and `formatMoney`.
- **Produces:**
  - `BetSlip` (default export). It renders `null` when the slip is empty and no receipt is showing.
  - `BetReceipt({ receipt, remaining })`.

- [ ] **Step 1: Write the failing tests, `frontend/src/components/BetSlip.test.tsx`**

```tsx
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import BetSlip from './BetSlip'
import { api, ApiError } from '../api/client'
import { useSlip, SLIP_DEFAULTS } from '../stores/slipStore'
import { useUserStore } from '../stores/userStore'
import { getPin, setPin } from '../lib/secrets'
import type { SlipLeg, UserProfile } from '../types'

vi.mock('../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/client')>()
  return { ...actual, api: { ...actual.api,
    users: { ...actual.api.users, list: vi.fn(), placePick: vi.fn(), placeParlay: vi.fn() } } }
})

const FUTURE = '2099-01-01T00:00:00+00:00'
const marcus = { id: 1, name: 'Marcus', available_balance: 1000 } as UserProfile
const ml = (game_id: number, label: string, over: Partial<SlipLeg> = {}): SlipLeg => ({
  leg: { game_id, pick_type: 'moneyline', side: 'HOME' }, label, gameLabel: `A${game_id} @ H${game_id}`,
  startTime: FUTURE, odds: -110, line: null, homeTeam: `H${game_id}`, awayTeam: `A${game_id}`, stake: 50, ...over,
})
const placed = (id: number, odds = -110) =>
  ({ id, result: null, payout: null, new_balance: 900, pick_value: 'HOME ML', odds, line: null, quoted_at: 'x' })

function renderSlip() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={client}><BetSlip /></QueryClientProvider>)
}

async function enabledButton(name: string | RegExp) {
  const b = await screen.findByRole('button', { name })
  await waitFor(() => expect(b).toBeEnabled())
  return b
}

beforeEach(() => {
  vi.clearAllMocks()
  window.localStorage.clear()
  window.sessionStorage.clear()
  useSlip.setState({ ...SLIP_DEFAULTS, open: true })
  useUserStore.setState({ currentUserName: 'Marcus' })
  vi.mocked(api.users.list).mockResolvedValue([marcus])
  setPin(1, '1234')
})

describe('BetSlip', () => {
  it('places each single at the price it showed and shows a receipt', async () => {
    useSlip.setState({ legs: [ml(1, 'H1 ML'), ml(2, 'H2 ML', { stake: 25 })] })
    vi.mocked(api.users.placePick).mockResolvedValueOnce(placed(11)).mockResolvedValueOnce(placed(12))
    renderSlip()
    fireEvent.click(await enabledButton('Place Bets'))
    expect(await screen.findByText('BET PLACED ✓')).toBeInTheDocument()
    expect(api.users.placePick).toHaveBeenNthCalledWith(1, 1, { game_id: 1, pick_type: 'moneyline', side: 'HOME',
      expected_odds: -110, expected_line: null, stake: 50 }, '1234')
    expect(screen.getByText('#P-11')).toBeInTheDocument()
    expect(screen.getByText('#P-12')).toBeInTheDocument()
    expect(useSlip.getState().legs).toEqual([])
  })

  it("keeps a refused single on the slip with the server's message (Review Focus 2)", async () => {
    useSlip.setState({ legs: [ml(1, 'H1 ML'), ml(2, 'H2 ML')] })
    vi.mocked(api.users.placePick)
      .mockResolvedValueOnce(placed(11))
      .mockRejectedValueOnce(new ApiError(409, { detail: 'The price is stale — ask Marcus to refresh.' }))
    renderSlip()
    fireEvent.click(await enabledButton('Place Bets'))
    expect(await screen.findByText('#P-11')).toBeInTheDocument()
    expect(screen.getByText(/1 bet couldn't be placed/)).toBeInTheDocument()
    expect(useSlip.getState().legs).toEqual([expect.objectContaining({
      label: 'H2 ML', error: 'The price is stale — ask Marcus to refresh.' })])
    fireEvent.click(screen.getByRole('button', { name: 'Done' }))
    expect(screen.getByRole('alert')).toHaveTextContent('The price is stale — ask Marcus to refresh.')
  })

  it('shows a moved price and places at it on Accept & Place (Review Focus 1)', async () => {
    useSlip.setState({ legs: [ml(1, 'H1 ML')] })
    vi.mocked(api.users.placePick)
      .mockRejectedValueOnce(new ApiError(409, { detail: { reason: 'price_moved', message: 'm',
        odds: -125, line: null, pick_value: 'HOME ML' } }))
      .mockResolvedValueOnce(placed(11, -125))
    renderSlip()
    fireEvent.click(await enabledButton('Place Bet'))
    expect(await screen.findByText(/Odds changed -110 → -125/)).toBeInTheDocument()
    expect(screen.queryByText('BET PLACED ✓')).not.toBeInTheDocument()
    fireEvent.click(await enabledButton('Accept & Place'))
    expect(await screen.findByText('#P-11')).toBeInTheDocument()
    expect(vi.mocked(api.users.placePick).mock.calls[1][1]).toMatchObject({ expected_odds: -125 })
  })

  it('sends no expected price when any odds change is accepted', async () => {
    useSlip.setState({ legs: [ml(1, 'H1 ML')], acceptAnyOdds: true })
    vi.mocked(api.users.placePick).mockResolvedValueOnce(placed(11))
    renderSlip()
    fireEvent.click(await enabledButton('Place Bet'))
    await screen.findByText('#P-11')
    expect(vi.mocked(api.users.placePick).mock.calls[0][1])
      .toEqual({ game_id: 1, pick_type: 'moneyline', side: 'HOME', stake: 50 })
  })

  it('places a same-game parlay as one bet at the estimated price', async () => {
    const total: SlipLeg = { ...ml(7, 'Over 47.5'), leg: { game_id: 7, pick_type: 'over_under', side: 'Over' }, line: 47.5 }
    useSlip.setState({ legs: [ml(7, 'H7 ML'), total], mode: 'parlay', parlayStake: 20 })
    vi.mocked(api.users.placeParlay).mockResolvedValueOnce({ id: 4, legs: [], combined_odds: 264,
      potential_payout: 52.89, result: null, payout: null, new_balance: 980 })
    renderSlip()
    expect(screen.getByText('SGP')).toBeInTheDocument()
    expect(screen.getByText('+264')).toBeInTheDocument()
    fireEvent.click(await enabledButton('Place Parlay'))
    expect(await screen.findByText('#PL-4')).toBeInTheDocument()
    expect(api.users.placeParlay).toHaveBeenCalledWith(1, { legs: [
      { game_id: 7, pick_type: 'moneyline', side: 'HOME', expected_odds: -110, expected_line: null },
      { game_id: 7, pick_type: 'over_under', side: 'Over', expected_odds: -110, expected_line: 47.5 },
    ], stake: 20 }, '1234')
  })

  it('stops at a wrong PIN, forgets it and asks again (Review Focus 4)', async () => {
    setPin(1, '9999')
    useSlip.setState({ legs: [ml(1, 'H1 ML'), ml(2, 'H2 ML')] })
    vi.mocked(api.users.placePick).mockRejectedValue(new ApiError(401, { detail: 'Wrong PIN' }))
    renderSlip()
    fireEvent.click(await enabledButton('Place Bets'))
    expect(await screen.findByLabelText('PIN')).toBeInTheDocument()
    expect(api.users.placePick).toHaveBeenCalledTimes(1)
    expect(getPin(1)).toBeNull()
    expect(useSlip.getState().legs).toHaveLength(2)
  })

  it('will not place more than the available balance', async () => {
    useSlip.setState({ legs: [ml(1, 'H1 ML', { stake: 2000 })] })
    renderSlip()
    expect(await screen.findByText(/Over your available balance/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Place Bet' })).toBeDisabled()
  })

  it('skips a leg whose game has started (Review Focus 3)', async () => {
    useSlip.setState({ legs: [ml(1, 'H1 ML', { startTime: '2000-01-01T00:00:00Z' }), ml(2, 'H2 ML')] })
    vi.mocked(api.users.placePick).mockResolvedValueOnce(placed(12))
    renderSlip()
    expect(screen.getByText('Betting closed')).toBeInTheDocument()
    fireEvent.click(await enabledButton('Place Bets'))
    await screen.findByText('#P-12')
    expect(api.users.placePick).toHaveBeenCalledTimes(1)
    expect(vi.mocked(api.users.placePick).mock.calls[0][1]).toMatchObject({ game_id: 2 })
  })

  it('Keep picks refills the slip with the placed legs', async () => {
    useSlip.setState({ legs: [ml(1, 'H1 ML')] })
    vi.mocked(api.users.placePick).mockResolvedValueOnce(placed(11))
    renderSlip()
    fireEvent.click(await enabledButton('Place Bet'))
    fireEvent.click(await screen.findByRole('button', { name: 'Keep picks' }))
    expect(useSlip.getState().legs.map(l => l.label)).toEqual(['H1 ML'])
    expect(useSlip.getState().receipt).toBeNull()
  })
})
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `cd frontend && npx vitest run src/components/BetSlip.test.tsx`

Expected: FAIL, "Failed to resolve import './BetSlip'".

- [ ] **Step 3: Write `frontend/src/components/BetReceipt.tsx`**

```tsx
import { useSlip } from '../stores/slipStore'
import { formatOdds } from '../lib/quotes'
import { formatMoney } from '../lib/board'
import type { Receipt } from '../types'

export default function BetReceipt({ receipt, remaining }: { receipt: Receipt; remaining: number }) {
  const refill = useSlip(s => s.refill)
  const setReceipt = useSlip(s => s.setReceipt)
  const setOpen = useSlip(s => s.setOpen)
  return (
    <section className="sb-receipt" aria-label="Bet receipt">
      <h3>BET PLACED ✓</h3>
      {receipt.bets.map(b => (
        <div className="sb-ticket" key={`${b.kind}-${b.id}`}>
          <div className="sb-ticket-id">
            <span>{b.kind === 'parlay' ? `#PL-${b.id}` : `#P-${b.id}`}</span>
            {b.kind === 'parlay' && <span> · {b.labels.length}-leg parlay</span>}
          </div>
          <ul>{b.labels.map((label, i) => <li key={i}>{label}</li>)}</ul>
          <dl>
            <dt>Stake</dt><dd>{formatMoney(b.stake)}</dd>
            <dt>Odds</dt>
            <dd>{formatOdds(b.odds)}{b.shownOdds !== null && ` (was ${formatOdds(b.shownOdds)})`}</dd>
            <dt>To win</dt><dd>{formatMoney(b.toWin)}</dd>
          </dl>
        </div>
      ))}
      <p className="sb-ticket-time">
        Placed {new Date(receipt.placedAt).toLocaleString('en-US', { dateStyle: 'medium', timeStyle: 'short' })}
      </p>
      {remaining > 0 && (
        <p className="sb-slip-error">
          {remaining} {remaining === 1 ? 'bet' : 'bets'} couldn't be placed — still on your slip.
        </p>
      )}
      <div className="sb-receipt-actions">
        <button type="button" onClick={() => { refill(receipt.legs); setReceipt(null) }}>Keep picks</button>
        <button type="button" className="sb-place"
          onClick={() => { setReceipt(null); if (remaining === 0) setOpen(false) }}>Done</button>
      </div>
    </section>
  )
}
```

- [ ] **Step 4: Write `frontend/src/components/BetSlip.tsx`**

```tsx
import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../api/client'
import { useUserStore } from '../stores/userStore'
import { useSlip } from '../stores/slipStore'
import { getPin, setPin } from '../lib/secrets'
import { formatOdds, marketKey, parlayEstimate } from '../lib/quotes'
import { formatMoney } from '../lib/board'
import { QUICK_STAKES, betRequest, hasSameGame, isClosed, refusal, toWin } from '../lib/slip'
import type { ReceiptBet, SlipLeg } from '../types'
import BetReceipt from './BetReceipt'

function StakeInput({ value, max, label, onChange }: {
  value: number; max: number; label: string; onChange: (n: number) => void
}) {
  return (
    <div className="sb-stake">
      <input type="number" inputMode="decimal" min={0} step="any" aria-label={label} placeholder="Stake"
        value={value || ''} onChange={e => onChange(Math.max(0, Number(e.target.value) || 0))} />
      {QUICK_STAKES.map(n => (
        <button key={n} type="button" className="sb-stake-chip" onClick={() => onChange(n)}>${n}</button>
      ))}
      <button type="button" className="sb-stake-chip" onClick={() => onChange(Math.floor(max * 100) / 100)}>Max</button>
    </div>
  )
}

function LegRow({ l, singles, available, now }: { l: SlipLeg; singles: boolean; available: number; now: Date }) {
  const remove = useSlip(s => s.remove)
  const setStake = useSlip(s => s.setStake)
  const closed = isClosed(l, now)
  return (
    <li className={`sb-slip-leg${l.error ? ' has-error' : ''}`}>
      <div className="sb-slip-leg-head">
        <div>
          <strong>{l.label}</strong>
          <small>{l.gameLabel}</small>
        </div>
        <span className="sb-slip-odds">{formatOdds(l.odds)}</span>
        <button type="button" className="sb-slip-remove" aria-label={`Remove ${l.label}`}
          onClick={() => remove(l.leg)}>✕</button>
      </div>
      {l.movedFrom && (
        <p className="sb-slip-moved">
          Odds changed {formatOdds(l.movedFrom.odds)} → {formatOdds(l.odds)}
          {l.movedFrom.line !== l.line && ` (line ${l.movedFrom.line} → ${l.line})`}
        </p>
      )}
      {closed && <p className="sb-slip-error">Betting closed</p>}
      {l.error && <p className="sb-slip-error" role="alert">{l.error}</p>}
      {singles && !closed && (
        <>
          <StakeInput value={l.stake} max={available} label={`Stake for ${l.label}`} onChange={n => setStake(l.leg, n)} />
          <small className="sb-slip-towin">To win {formatMoney(toWin(l.stake, l.odds))}</small>
        </>
      )}
    </li>
  )
}

type Placed = { bets: ReceiptBet[]; placedLegs: SlipLeg[] }

export default function BetSlip() {
  const s = useSlip()
  const queryClient = useQueryClient()
  const { currentUserName } = useUserStore()
  const users = useQuery({ queryKey: ['users', 'list'], queryFn: api.users.list, refetchInterval: 60_000 })
  const me = users.data?.find(u => u.name === currentUserName)
  const [pinInput, setPinInput] = useState('')
  const [placing, setPlacing] = useState(false)
  const [slipError, setSlipError] = useState<string | null>(null)

  if (s.legs.length === 0 && s.receipt === null) return null

  const now = new Date()
  const storedPin = me ? getPin(me.id) : null
  const pin = storedPin ?? pinInput
  const available = me?.available_balance ?? 0
  const parlay = s.mode === 'parlay'
  const openLegs = s.legs.filter(l => !isClosed(l, now))
  const estimate = parlayEstimate(s.legs.map(l => l.odds))
  const total = parlay ? s.parlayStake : openLegs.reduce((a, l) => a + l.stake, 0)
  const totalToWin = parlay
    ? (estimate ? s.parlayStake * (estimate.decimal - 1) : 0)
    : openLegs.reduce((a, l) => a + toWin(l.stake, l.odds), 0)
  const moved = s.legs.some(l => l.movedFrom)
  const blocked = !me ? 'Choose a player in the top bar, or join the league, to bet.'
    : parlay && s.legs.length < 2 ? 'A parlay needs at least 2 legs.'
    : parlay && openLegs.length !== s.legs.length ? 'Remove the closed legs to place this parlay.'
    : total > available ? `Over your available balance (${formatMoney(available)}).`
    : null
  const canPlace = !!me && !placing && !blocked && total > 0 && /^\d{4,6}$/.test(pin)

  const forgetPin = () => {
    if (me) setPin(me.id, null)
    setPinInput('')
  }

  // Singles go one at a time, each with its own outcome (spec §6). A 401
  // stops the loop: the same wrong PIN would fail every leg and count
  // toward the lockout.
  const placeSingles = async (uid: number): Promise<Placed> => {
    const out: Placed = { bets: [], placedLegs: [] }
    for (const l of openLegs.filter(x => x.stake > 0)) {
      try {
        const res = await api.users.placePick(uid, { ...betRequest(l, s.acceptAnyOdds), stake: l.stake }, pin)
        out.bets.push({ id: res.id, kind: 'single', labels: [l.label], stake: l.stake, odds: res.odds,
          shownOdds: res.odds === l.odds ? null : l.odds, toWin: toWin(l.stake, res.odds) })
        out.placedLegs.push(l)
        s.remove(l.leg)
      } catch (e) {
        const r = refusal(e)
        if (r.kind === 'moved') {
          s.priceMoved(l.leg, r.odds, r.line, r.pickValue)
        } else {
          s.setError(l.leg, r.message)
          if (r.kind === 'pin') { forgetPin(); break }
        }
      }
    }
    return out
  }

  const placeParlay = async (uid: number): Promise<Placed> => {
    const legs = s.legs
    const stake = s.parlayStake
    try {
      const res = await api.users.placeParlay(uid,
        { legs: legs.map(l => betRequest(l, s.acceptAnyOdds)), stake }, pin)
      const shown = parlayEstimate(legs.map(l => l.odds))
      s.clear()
      return { bets: [{ id: res.id, kind: 'parlay', labels: legs.map(l => l.label), stake, odds: res.combined_odds,
        shownOdds: shown && shown.american !== res.combined_odds ? shown.american : null,
        toWin: res.potential_payout }], placedLegs: legs }
    } catch (e) {
      const r = refusal(e)
      if (r.kind === 'moved') {
        const hit = r.leg !== undefined ? legs[r.leg] : undefined
        if (hit) s.priceMoved(hit.leg, r.odds, r.line, r.pickValue)
        else setSlipError('A price moved — check the legs and try again.')
      } else {
        setSlipError(r.message)
        if (r.kind === 'pin') forgetPin()
      }
      return { bets: [], placedLegs: [] }
    }
  }

  const handlePlace = async () => {
    if (!me || !canPlace) return
    setPlacing(true)
    setSlipError(null)
    try {
      const { bets, placedLegs } = parlay ? await placeParlay(me.id) : await placeSingles(me.id)
      if (bets.length > 0) {
        setPin(me.id, pin)
        setPinInput('')
        s.setReceipt({ bets, legs: placedLegs, placedAt: new Date().toISOString() })
      }
      queryClient.invalidateQueries({ queryKey: ['users'] })
    } finally {
      setPlacing(false)
    }
  }

  const placeLabel = placing ? 'Placing…'
    : moved ? 'Accept & Place'
    : parlay ? 'Place Parlay'
    : s.legs.length > 1 ? 'Place Bets' : 'Place Bet'

  return (
    <>
      {s.legs.length > 0 && !s.open && (
        <button type="button" className="sb-slip-bar" onClick={() => s.setOpen(true)}>
          <span className="sb-slip-count">{s.legs.length}</span> Bet Slip
          <span className="sb-slip-bar-total">{formatMoney(total)}</span>
        </button>
      )}
      <aside className={`sb-slip${s.open ? ' open' : ''}`} aria-label="Bet slip">
        <header className="sb-slip-head">
          <h2>Bet Slip <span className="sb-slip-count">{s.legs.length}</span></h2>
          <button type="button" className="sb-slip-close" aria-label="Close bet slip"
            onClick={() => s.setOpen(false)}>✕</button>
        </header>
        {s.receipt ? <BetReceipt receipt={s.receipt} remaining={s.legs.length} /> : (
          <>
            <div className="sb-slip-tabs" role="tablist" aria-label="Bet type">
              <button type="button" role="tab" aria-selected={!parlay} onClick={() => s.setMode('singles')}>Singles</button>
              <button type="button" role="tab" aria-selected={parlay} disabled={s.legs.length < 2}
                onClick={() => s.setMode('parlay')}>Parlay</button>
            </div>
            {!parlay && s.legs.length > 1 && (
              <label className="sb-slip-check">
                <input type="checkbox" checked={s.sameStake} onChange={e => s.setSameStake(e.target.checked)} />
                Same stake for all
              </label>
            )}
            <ul className="sb-slip-legs">
              {s.legs.map(l => (
                <LegRow key={marketKey(l.leg)} l={l} singles={!parlay} available={available} now={now} />
              ))}
            </ul>
            {parlay && (
              <div className="sb-slip-parlay">
                <div className="sb-slip-parlay-odds">
                  <span>{s.legs.length}-leg parlay</span>
                  {hasSameGame(s.legs) && <span className="sb-sgp">SGP</span>}
                  <strong>{estimate ? formatOdds(estimate.american) : '—'}</strong>
                </div>
                <small>Estimate — the server prices every leg again when you place it.</small>
                <StakeInput value={s.parlayStake} max={available} label="Parlay stake" onChange={s.setParlayStake} />
              </div>
            )}
            <label className="sb-slip-check">
              <input type="checkbox" checked={s.acceptAnyOdds} onChange={e => s.setAcceptAnyOdds(e.target.checked)} />
              Accept any odds changes
            </label>
            {me && !storedPin && (
              <input className="input" aria-label="PIN" type="password" inputMode="numeric" autoComplete="off"
                maxLength={6} placeholder="PIN (4–6 digits)" value={pinInput}
                onChange={e => setPinInput(e.target.value)} />
            )}
            {(slipError ?? blocked) && <p className="sb-slip-error" role="alert">{slipError ?? blocked}</p>}
            <footer className="sb-slip-foot">
              <div><small>Total stake</small><strong>{formatMoney(total)}</strong></div>
              <div><small>To win</small><strong>{formatMoney(totalToWin)}</strong></div>
            </footer>
            <button type="button" className="sb-place" disabled={!canPlace} onClick={handlePlace}>{placeLabel}</button>
            <button type="button" className="sb-slip-clear" onClick={s.clear}>Clear slip</button>
          </>
        )}
      </aside>
    </>
  )
}
```

- [ ] **Step 5: Mount it in `frontend/src/components/Layout.tsx`**

- Add `import BetSlip from './BetSlip'` and `import { useSlip } from '../stores/slipStore'`.
- Inside `Layout`, add `const slipVisible = useSlip(s => s.legs.length > 0 || s.receipt !== null)`.
- Replace `<main className="main-content"><Outlet /></main>` with:

```tsx
      <main className={`main-content${slipVisible ? ' with-slip' : ''}`}><Outlet /></main>
      <BetSlip />
```

- [ ] **Step 6: Append the slip styles to `frontend/src/sportsbook.css`**

```css
/* ─── Bet slip (spec §6) ────────────────────────────────────── */
.sb-tile-selected { background: var(--accent); border-color: var(--accent); }
.sb-tile-selected .sb-tile-price, .sb-tile-selected .sb-tile-top { color: var(--on-accent); }
.sb-tile-selected:hover:not(:disabled) { background: var(--accent-hover); }
/* The 120 ms pop when a tile goes on the slip (spec §3). */
.sb-tile-selected { animation: sb-pop 120ms ease; }
@keyframes sb-pop { 50% { transform: scale(1.06); } }

.sb-slip-bar {
  position: fixed; left: 0.75rem; right: 0.75rem; bottom: 68px; z-index: 150;
  display: flex; align-items: center; gap: 0.6rem; padding: 0.8rem 1rem;
  border: 0; border-radius: var(--radius-lg); background: var(--accent); color: var(--on-accent);
  font: inherit; font-weight: 800; cursor: pointer; box-shadow: var(--shadow-lg);
}
.sb-slip-bar-total { margin-left: auto; font-variant-numeric: tabular-nums; }
.sb-slip-count {
  display: inline-grid; place-items: center; min-width: 1.4rem; height: 1.4rem; padding: 0 0.3rem;
  border-radius: 999px; background: var(--bg-base); color: var(--accent); font-size: 0.75rem;
}
.sb-slip {
  display: none; position: fixed; inset: 0; z-index: 230; flex-direction: column; gap: 0.6rem;
  padding: 0.75rem; overflow-y: auto; background: var(--bg-surface);
}
.sb-slip.open { display: flex; animation: sb-slide-up 200ms ease; }
@keyframes sb-slide-up { from { transform: translateY(24px); opacity: 0; } }
.sb-slip-head { display: flex; align-items: center; justify-content: space-between; }
.sb-slip-head h2 { display: flex; gap: 0.5rem; align-items: center; font-size: 1rem; font-weight: 800; margin: 0; }
.sb-slip-close, .sb-slip-remove {
  background: none; border: 0; color: var(--text-muted); font-size: 1rem; cursor: pointer; min-height: 32px;
}
.sb-slip-tabs { display: flex; gap: 0.4rem; }
.sb-slip-tabs button {
  flex: 1; min-height: 36px; padding: 0.5rem; border-radius: 999px; border: 1px solid var(--border-strong);
  background: var(--bg-elevated); color: var(--text-secondary); font: inherit; font-weight: 700; cursor: pointer;
}
.sb-slip-tabs button[aria-selected="true"] { background: var(--accent); color: var(--on-accent); border-color: var(--accent); }
.sb-slip-tabs button:disabled { opacity: 0.45; cursor: not-allowed; }
.sb-slip-legs { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 0.5rem; }
.sb-slip-leg {
  padding: 0.6rem 0.7rem; border-radius: var(--radius-md);
  background: var(--bg-elevated); border: 1px solid var(--border-default);
}
.sb-slip-leg.has-error { border-color: var(--red); }
.sb-slip-leg-head { display: flex; gap: 0.5rem; align-items: flex-start; }
.sb-slip-leg-head > div { flex: 1; min-width: 0; }
.sb-slip-leg-head strong { display: block; }
.sb-slip-leg-head small, .sb-slip-towin { color: var(--text-muted); font-size: 0.75rem; }
.sb-slip-odds { font-weight: 800; color: var(--accent); font-variant-numeric: tabular-nums; }
.sb-slip-moved { margin: 0.35rem 0 0; color: var(--yellow); font-size: 0.8rem; font-weight: 700; }
.sb-slip-error { margin: 0.35rem 0 0; color: var(--red); font-size: 0.8rem; font-weight: 600; }
.sb-stake { display: flex; flex-wrap: wrap; gap: 0.3rem; margin-top: 0.45rem; }
.sb-stake input {
  width: 5.5rem; padding: 0.35rem 0.5rem; border-radius: var(--radius-md); border: 1px solid var(--border-strong);
  background: var(--bg-base); color: var(--text-primary); font: inherit; font-variant-numeric: tabular-nums;
}
.sb-stake-chip {
  min-height: 32px; padding: 0.3rem 0.6rem; border-radius: 999px; border: 1px solid var(--border-strong);
  background: var(--bg-surface); color: var(--text-primary); font: inherit; font-size: 0.8rem; font-weight: 700; cursor: pointer;
}
.sb-slip-parlay { padding: 0.6rem 0.7rem; border-radius: var(--radius-md); background: var(--bg-elevated); }
.sb-slip-parlay-odds { display: flex; align-items: center; gap: 0.5rem; font-weight: 700; }
.sb-slip-parlay-odds strong { margin-left: auto; color: var(--accent); }
.sb-slip-parlay small { color: var(--text-muted); font-size: 0.75rem; }
.sb-sgp {
  padding: 0.1rem 0.4rem; border-radius: var(--radius-sm); background: var(--yellow-dim);
  color: var(--yellow); font-size: 0.7rem; font-weight: 800;
}
.sb-slip-check { display: flex; gap: 0.45rem; align-items: center; font-size: 0.85rem; color: var(--text-secondary); }
.sb-slip-foot { display: flex; justify-content: space-between; font-variant-numeric: tabular-nums; }
.sb-slip-foot small { display: block; color: var(--text-muted); font-size: 0.7rem; }
.sb-place {
  width: 100%; min-height: 48px; border: 0; border-radius: var(--radius-lg); background: var(--accent);
  color: var(--on-accent); font: inherit; font-weight: 800; font-size: 1rem; cursor: pointer;
}
.sb-place:disabled { opacity: 0.45; cursor: not-allowed; }
.sb-slip-clear { background: none; border: 0; color: var(--text-muted); font: inherit; font-size: 0.8rem; cursor: pointer; }

.sb-receipt { display: flex; flex-direction: column; gap: 0.6rem; animation: sb-drop 300ms ease; }
@keyframes sb-drop { from { transform: translateY(-16px); opacity: 0; } }
.sb-receipt h3 { margin: 0; color: var(--accent); font-weight: 800; letter-spacing: 0.06em; }
.sb-ticket { padding: 0.7rem; border-radius: var(--radius-md); background: var(--bg-elevated); border: 1px dashed var(--border-strong); }
.sb-ticket-id { font-weight: 800; font-size: 0.8rem; color: var(--text-muted); }
.sb-ticket ul { margin: 0.35rem 0; padding-left: 1rem; }
.sb-ticket dl { display: grid; grid-template-columns: auto 1fr; gap: 0.15rem 0.75rem; margin: 0; font-variant-numeric: tabular-nums; }
.sb-ticket dt { color: var(--text-muted); }
.sb-ticket dd { margin: 0; text-align: right; font-weight: 700; }
.sb-ticket-time { color: var(--text-muted); font-size: 0.75rem; }
.sb-receipt-actions { display: flex; gap: 0.5rem; }
.sb-receipt-actions button:first-child {
  flex: 1; min-height: 48px; border-radius: var(--radius-lg); border: 1px solid var(--border-strong);
  background: var(--bg-elevated); color: var(--text-primary); font: inherit; font-weight: 700; cursor: pointer;
}
.sb-receipt-actions .sb-place { flex: 1; }

@media (min-width: 900px) {
  .sb-slip-bar { display: none; }
  .sb-slip { display: flex; inset: 56px 0 0 auto; width: 340px; border-left: 1px solid var(--border-default); }
  .sb-slip.open { animation: none; }
  .sb-slip-close { display: none; }
  .main-content.with-slip { padding-right: 340px; }
}
@media (prefers-reduced-motion: reduce) {
  .sb-slip.open, .sb-receipt, .sb-tile-selected { animation: none; }
}
```

- [ ] **Step 7: Run the tests and confirm they pass**

Run: `cd frontend && npx vitest run src/components/BetSlip.test.tsx src/components/Layout.test.tsx && npx tsc -p tsconfig.app.json --noEmit && npx eslint .`

Expected: all pass, and both tsc and eslint are clean.

- [ ] **Step 8: Mutation-check**

Make each change, confirm a test fails, then restore:
1. Remove the `break` after `forgetPin()` in `placeSingles`. The PIN test must fail, because the call count becomes 2.
2. Replace `s.priceMoved(l.leg, ...)` with `s.setError(l.leg, 'moved')`. The moved-price test must fail.
3. Iterate `s.legs` instead of `openLegs` in `placeSingles`. The closed-leg test must fail.
4. Drop `total > available` from `blocked`. The balance test must fail.

- [ ] **Step 9: Commit**

```bash
git add frontend/src/components/BetSlip.tsx frontend/src/components/BetReceipt.tsx frontend/src/components/BetSlip.test.tsx frontend/src/components/Layout.tsx frontend/src/sportsbook.css
git commit -m "feat(slip): bet slip (singles, parlay, quick stakes, inline PIN, odds-change accept) and ticket receipt

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Every bet button feeds the slip; BetModal is retired

**Files:**
- Modify: `frontend/src/components/OddsTile.tsx` (the `selected` prop)
- Modify: `frontend/src/lib/board.ts`. Replace `gameTarget` / `propTarget` / `modelPickTarget` with `gameSelection` / `propSelection` / `modelPickSelection`.
- Modify: `frontend/src/components/BoardGameCard.tsx`, `frontend/src/components/ModelPicksStrip.tsx`, `frontend/src/pages/Lobby.tsx`, `frontend/src/pages/GameDetail.tsx`
- Modify: `frontend/src/pages/TodaysPicks.tsx`, `frontend/src/pages/PlayerProps.tsx`
- Modify: `frontend/src/types.ts` (delete `BetTarget`)
- Delete: `frontend/src/components/BetModal.tsx`, `frontend/src/components/BetModal.test.tsx`
- Test, modify: `OddsTile.test.tsx`, `BoardGameCard.test.tsx`, `lib/board.test.ts`, `pages/Lobby.test.tsx`, `pages/GameDetail.test.tsx`, `pages/TodaysPicks.test.tsx` and `pages/PlayerProps.test.tsx`

**Interfaces:**
- **Consumes:** `useSlip` (`add`, `toggle`, `legs`), plus `sameLeg`, `lineFromPick` and `selectionFromPick` (Task 2).
- **Produces:**
  - `onPick: (s: SlipSelection) => void` on `TeamRow`, `GameLines`, `BoardGameCard` and `ModelPicksStrip`.
  - `OddsTile` gains `selected?: boolean`, which renders as `aria-pressed`.
  - The selection builders: `gameSelection(game, q)`, `propSelection(game, q)` and `modelPickSelection(game, q)`.

- [ ] **Step 1: Update the tests to the slip, and confirm they fail**

`frontend/src/components/OddsTile.test.tsx`: add inside the `describe`:

```tsx
  it('shows a tile on the slip as selected and pressed', () => {
    render(<OddsTile label="X" top={null} price={-110} line={null} selected onSelect={() => {}} />)
    expect(screen.getByRole('button')).toHaveClass('sb-tile-selected')
    expect(screen.getByRole('button')).toHaveAttribute('aria-pressed', 'true')
  })
```

`frontend/src/lib/board.test.ts`:
1. In the import from `./board`, replace `gameTarget, modelPickTarget, propTarget` with `gameSelection, modelPickSelection, propSelection`.
2. Replace every test in `describe('gameTarget / modelPickQuote')` that calls `gameTarget` or `modelPickTarget` with the tests below. Keep the two `modelPickQuote` tests.

```ts
  it('builds a slip selection from a quote, team-resolved', () => {
    const g = game()
    expect(gameSelection(g, findGameQuote(g.quotes, 'spread', 'AWAY') as AvailableGameQuote)).toEqual({
      leg: { game_id: 1, pick_type: 'spread', side: 'AWAY' }, label: 'Cowboys +3', gameLabel: 'Cowboys @ Buccaneers',
      startTime: '2026-10-11T17:00:00+00:00', odds: -110, line: 3, homeTeam: 'Buccaneers', awayTeam: 'Cowboys' })
  })
  it('puts a model pick on the slip at the price and line the model evaluated', () => {
    const g = game({ model_pick: { pick_type: 'spread', pick_value: 'AWAY +3.5', odds: 120, edge_pct: 4.1 } })
    expect(modelPickSelection(g, findGameQuote(g.quotes, 'spread', 'AWAY') as AvailableGameQuote)).toMatchObject({
      leg: { game_id: 1, pick_type: 'spread', side: 'AWAY' }, label: 'Cowboys +3.5', odds: 120, line: 3.5 })
  })
```

3. Replace the `propTarget` test in `describe('groupProps / propTarget')` with:

```ts
  it('builds a prop slip selection', () => {
    const over = p('QB One', 'Pass Yds', 'Over', 245.5, -115)
    expect(propSelection(game({ id: 9 }), over as never)).toEqual({
      leg: { game_id: 9, pick_type: 'prop', prop_player: 'QB One', prop_market: 'player_pass_yds', outcome: 'Over', line: 245.5 },
      label: 'QB One Over 245.5 Pass Yds', gameLabel: 'Cowboys @ Buccaneers', startTime: '2026-10-11T17:00:00+00:00',
      odds: -115, line: 245.5, homeTeam: 'Buccaneers', awayTeam: 'Cowboys' })
  })
```

`frontend/src/components/BoardGameCard.test.tsx`:
1. Replace the `onPick` assertion in `'hands the tapped side to onPick as BetModal props'`, and rename that test to `'hands the tapped side to onPick as a slip selection'`:

```tsx
    expect(onPick).toHaveBeenCalledWith(expect.objectContaining({
      leg: { game_id: 7, pick_type: 'moneyline', side: 'AWAY' }, label: 'Cowboys ML', odds: 135 }))
```

2. In `'shows a card per mappable model pick with its edge, tappable'`, replace the `onPick` assertion with:

```tsx
    expect(onPick).toHaveBeenCalledWith(expect.objectContaining({
      leg: { game_id: 7, pick_type: 'spread', side: 'AWAY' }, odds: -110, line: 3 }))
```

3. In `'shows the price the edge was computed at and bets at it, not at the live price'`, replace the `onPick` assertion with:

```tsx
    expect(onPick).toHaveBeenCalledWith(expect.objectContaining({
      leg: { game_id: 7, pick_type: 'moneyline', side: 'AWAY' }, odds: 150 }))
```

`frontend/src/pages/Lobby.test.tsx`:
1. Add `import { useSlip, SLIP_DEFAULTS } from '../stores/slipStore'`.
2. Add `useSlip.setState({ ...SLIP_DEFAULTS })` to `beforeEach`.
3. Replace `'opens the bet modal on a tapped price'` with:

```tsx
  it('adds a tapped price to the bet slip and marks it selected', async () => {
    vi.mocked(api.paper.board).mockResolvedValue({ games: [g(1, 'nfl', '2026-10-20')] })
    renderLobby()
    const tile = await screen.findByRole('button', { name: /A1 ML \+130/ })
    fireEvent.click(tile)
    expect(useSlip.getState().legs).toEqual([expect.objectContaining({
      leg: { game_id: 1, pick_type: 'moneyline', side: 'AWAY' }, label: 'A1 ML', odds: 130 })])
    expect(tile).toHaveAttribute('aria-pressed', 'true')
  })
```

`frontend/src/pages/GameDetail.test.tsx`:
1. Add the same import and reset in `beforeEach`.
2. Add this test:

```tsx
  it('adds a tapped prop to the bet slip', async () => {
    renderAt('/game/5')
    fireEvent.click(await screen.findByRole('tab', { name: 'Player Props' }))
    fireEvent.click(await screen.findByRole('button', { name: /QB One Over 245.5 -115/ }))
    expect(useSlip.getState().legs).toEqual([expect.objectContaining({ odds: -115, line: 245.5,
      leg: { game_id: 5, pick_type: 'prop', prop_player: 'QB One', prop_market: 'player_pass_yds', outcome: 'Over', line: 245.5 } })])
  })
```

`frontend/src/pages/TodaysPicks.test.tsx`:
1. Add `import { useSlip, SLIP_DEFAULTS } from '../stores/slipStore'` and `import { ToastProvider } from '../components/Toast'`.
2. Wrap `renderPage`'s tree in `<ToastProvider>` inside the `QueryClientProvider`.
3. Replace the whole `describe('TodaysPicks -> GameCard -> BetModal wiring', ...)` block with:

```tsx
describe('TodaysPicks -> bet slip wiring', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    window.localStorage.clear()
    useSlip.setState({ ...SLIP_DEFAULTS })
  })

  // A moneyline pick's pick_value has already been rewritten to team names
  // ("NYY ML") for display; only stored_pick_value ("HOME ML") maps to a side.
  it('adds a moneyline pick displayed under a team name, at the price the model saw', async () => {
    picksData = [makePick({ pick_value: 'NYY ML', stored_pick_value: 'HOME ML', odds_at_pick: -131 })]
    gamesData = [makeGame()]
    const user = userEvent.setup()
    renderPage()
    await user.click((await screen.findAllByRole('button', { name: 'Bet This' }))[0])
    expect(useSlip.getState().legs).toEqual([expect.objectContaining({
      leg: { game_id: 1, pick_type: 'moneyline', side: 'HOME' }, label: 'NYY ML', odds: -131, line: null,
      gameLabel: 'BOS @ NYY', startTime: '2099-01-01T00:00:00Z' })])
  })

  it('adds a fighter-name moneyline pick', async () => {
    picksData = [makePick({ id: 2, pick_value: 'Erick Visconde ML', stored_pick_value: 'AWAY ML' })]
    gamesData = [makeGame({ sport: 'mma', home_team: 'Kleydson Rodrigues', away_team: 'Erick Visconde' })]
    const user = userEvent.setup()
    renderPage()
    await user.click((await screen.findAllByRole('button', { name: 'Bet This' }))[0])
    expect(useSlip.getState().legs).toEqual([expect.objectContaining({
      leg: { game_id: 1, pick_type: 'moneyline', side: 'AWAY' }, label: 'Erick Visconde ML' })])
  })

  // The GameCard's button is first in the DOM; the picks-table row's is
  // second -- a separate wiring path with its own stored-label plumbing.
  it('adds a spread pick from the picks-table row, with its line', async () => {
    picksData = [makePick({ pick_type: 'spread', pick_value: 'BOS +3.5', stored_pick_value: 'AWAY +3.5',
      odds_at_pick: -110, home_team: 'NYY', away_team: 'BOS' })]
    gamesData = [makeGame()]
    const user = userEvent.setup()
    renderPage()
    const betButtons = await screen.findAllByRole('button', { name: 'Bet This' })
    expect(betButtons.length).toBeGreaterThan(1)
    await user.click(betButtons[1])
    expect(useSlip.getState().legs).toEqual([expect.objectContaining({
      leg: { game_id: 1, pick_type: 'spread', side: 'AWAY' }, label: 'BOS +3.5', line: 3.5, odds: -110 })])
  })

  it('cannot add a pick without its stored label (mutation check)', async () => {
    picksData = [makePick({ pick_value: 'NYY ML', stored_pick_value: undefined })]
    gamesData = [makeGame()]
    const user = userEvent.setup()
    renderPage()
    await user.click((await screen.findAllByRole('button', { name: 'Bet This' }))[0])
    expect(await screen.findByText("This pick can't be bet here.")).toBeInTheDocument()
    expect(useSlip.getState().legs).toEqual([])
  })
})
```

`frontend/src/pages/PlayerProps.test.tsx`: add `fireEvent` to the Testing Library import, add `import { useSlip, SLIP_DEFAULTS } from '../stores/slipStore'`, and add:

```tsx
  it("adds a prop to the bet slip at the model's price", () => {
    useSlip.setState({ ...SLIP_DEFAULTS })
    renderAt('/player-props')
    fireEvent.click(screen.getByRole('button', { name: 'Bet This' }))
    expect(useSlip.getState().legs).toEqual([expect.objectContaining({
      leg: { game_id: 1, pick_type: 'prop', prop_player: 'Jayson Tatum', prop_market: 'player_points', outcome: 'Over', line: 27.5 },
      label: 'Jayson Tatum Over 27.5 Points', gameLabel: 'BOS @ NYY', odds: -110 })])
  })
```

Run: `cd frontend && npx vitest run src/components/OddsTile.test.tsx src/components/BoardGameCard.test.tsx src/lib/board.test.ts src/pages/Lobby.test.tsx src/pages/GameDetail.test.tsx src/pages/TodaysPicks.test.tsx src/pages/PlayerProps.test.tsx`

Expected:
- `board.test.ts` FAILS to import the selection builders.
- The rewritten tests FAIL: the clicks still open `BetModal`, and `onPick` receives `BetTarget`.

- [ ] **Step 2: `OddsTile` — the `selected` prop**

In `frontend/src/components/OddsTile.tsx`:
1. Add `selected?: boolean` to `Props`, under `offline`, with the doc comment `/** On the bet slip: shown green and pressed. */`.
2. Destructure it as `selected = false`.
3. Change `cls` to:

```tsx
  const cls = ['sb-tile', locked && 'sb-tile-locked', selected && !locked && 'sb-tile-selected',
    flash && `sb-flash-${flash}`].filter(Boolean).join(' ')
```

4. Add `aria-pressed={locked ? undefined : selected}` to the `<button>`.

- [ ] **Step 3: `lib/board.ts` — the selection builders replace the `BetTarget` builders**

1. Delete `gameTarget`, `propTarget` and `modelPickTarget`.
2. In the `../types` import, swap `BetTarget` for `SlipSelection`.
3. Change the quotes import to `import { legFromPick, legFromQuote, resolveLabel, resolveQuoteLabel } from './quotes'`.
4. Add `import { lineFromPick } from './slip'`.
5. Add:

```ts
export function gameLabelOf(game: BoardGame): string {
  return `${game.away_team} @ ${game.home_team}`
}

export function gameSelection(game: BoardGame, q: AvailableGameQuote): SlipSelection {
  const team = q.side === 'HOME' ? game.home_team : game.away_team
  return {
    leg: legFromQuote(game.id, q),
    label: q.pick_type === 'moneyline' ? `${team} ML` : resolveQuoteLabel(q, game.home_team, game.away_team),
    gameLabel: gameLabelOf(game), startTime: game.start_time, odds: q.odds, line: q.line,
    homeTeam: game.home_team, awayTeam: game.away_team,
  }
}

export function propSelection(game: BoardGame, q: AvailablePropQuote): SlipSelection {
  return {
    leg: legFromQuote(game.id, q), label: `${q.prop_player} ${q.outcome} ${q.line} ${q.market_label}`,
    gameLabel: gameLabelOf(game), startTime: game.start_time, odds: q.odds, line: q.line,
    homeTeam: game.home_team, awayTeam: game.away_team,
  }
}

/** A model pick on the slip at the label and price the model evaluated (its
 *  edge is only true there); the server's price_moved refusal tells the
 *  player if the live price `q` has moved since. */
export function modelPickSelection(game: BoardGame, q: AvailableGameQuote): SlipSelection {
  const mp = game.model_pick
  if (!mp) return gameSelection(game, q)
  return { ...gameSelection(game, q), label: resolveLabel(mp.pick_value, game.home_team, game.away_team),
    odds: mp.odds ?? q.odds, line: lineFromPick(mp.pick_type, mp.pick_value) }
}
```

If `legFromPick` is now unused in `board.ts`, drop it from the import. `modelPickQuote` still uses it, so keep whatever tsc and eslint say is used.

- [ ] **Step 4: The board components take `SlipSelection` and show selection**

In `frontend/src/components/BoardGameCard.tsx`:
1. Replace the `gameTarget` import with `gameSelection`.
2. Change `BetTarget` to `SlipSelection` in every `onPick` type.
3. Add `import { useSlip } from '../stores/slipStore'` and `import { sameLeg } from '../lib/slip'`.
4. Replace `Tile` with:

```tsx
function Tile({ game, pickType, side, offline, onPick }: {
  game: BoardGame; pickType: GamePickType; side: GameSide; offline: boolean; onPick: (s: SlipSelection) => void
}) {
  const q = findGameQuote(game.quotes, pickType, side)
  const sel = q && q.available ? gameSelection(game, q) : null
  const selected = useSlip(s => sel !== null && s.legs.some(l => sameLeg(l.leg, sel.leg)))
  const team = side === 'HOME' ? game.home_team : side === 'AWAY' ? game.away_team : ''
  if (!q || !q.available || !sel) {
    const name = pickType === 'over_under' ? `${side === 'Over' ? game.away_team : game.home_team} ${side}` : team
    return <OddsTile label={name} top={null} price={null} line={null} offline={offline}
      lockedReason={q && !q.available ? q.message : 'Not offered'} onSelect={() => {}} />
  }
  return <OddsTile label={pickType === 'moneyline' ? `${team} ML` : resolveQuoteLabel(q, game.home_team, game.away_team)}
    top={tileTop(q)} price={q.odds} line={q.line} offline={offline} selected={selected}
    onSelect={() => onPick(sel)} />
}
```

In `frontend/src/components/ModelPicksStrip.tsx`:
1. Import `modelPickSelection` instead of `modelPickTarget`.
2. Type `onPick` as `(s: SlipSelection) => void`.
3. Inside the card map, compute the selection and selection state through a small child component, because hooks can't run inside `map` callbacks:

```tsx
function StripTile({ g, q, label, offline, onPick }: {
  g: BoardGame; q: GameQuote; label: string; offline: boolean; onPick: (s: SlipSelection) => void
}) {
  const sel = q.available ? modelPickSelection(g, q) : null
  const selected = useSlip(s => sel !== null && s.legs.some(l => sameLeg(l.leg, sel.leg)))
  return <OddsTile label={label} top={q.available ? tileTop(q) : null}
    price={q.available ? q.odds : null} line={q.available ? q.line : null}
    lockedReason={q.available ? undefined : q.message} offline={offline} selected={selected}
    onSelect={() => { if (sel) onPick(sel) }} />
}
```

Then render `<StripTile g={g} q={q} label={label} offline={offline} onPick={onPick} />` in place of the existing `<OddsTile .../>`. Import `useSlip`, `sameLeg`, `GameQuote` and `SlipSelection`.

- [ ] **Step 5: Lobby and GameDetail put taps on the slip**

In `frontend/src/pages/Lobby.tsx`:
1. Delete the `BetModal` import, the `target` state and the `{target && <BetModal .../>}` line.
2. Delete the `BetTarget` import.
3. Add `import { useSlip } from '../stores/slipStore'` and `const toggle = useSlip(s => s.toggle)`.
4. Pass `onPick={toggle}` to `ModelPicksStrip` and `BoardGameCard`.

In `frontend/src/pages/GameDetail.tsx`:
1. Delete the `BetModal` import, the `target` state and its render.
2. Add `const toggle = useSlip(s => s.toggle)`, and pass `onPick={toggle}` to `GameLines`.
3. Replace `PropTile` with:

```tsx
function PropTile({ game, player, line, outcome, q, offline, onPick }: {
  game: BoardGame; player: string; line: number; outcome: 'Over' | 'Under'; q?: PropQuote;
  offline: boolean; onPick: (s: SlipSelection) => void
}) {
  const label = `${player} ${outcome} ${line}`
  const top = `${outcome === 'Over' ? 'O' : 'U'} ${line}`
  const sel = q && q.available ? propSelection(game, q) : null
  const selected = useSlip(s => sel !== null && s.legs.some(l => sameLeg(l.leg, sel.leg)))
  if (!q || !q.available || !sel) {
    return <OddsTile label={label} top={top} price={null} line={line} offline={offline}
      lockedReason={q && !q.available ? q.message : 'Not offered'} onSelect={() => {}} />
  }
  return <OddsTile label={label} top={top} price={q.odds} line={q.line} offline={offline} selected={selected}
    onSelect={() => onPick(sel)} />
}
```

4. Pass `game={game}` and `onPick={toggle}` where `PropTile` is rendered, in place of `gameId={id}` and `onPick={setTarget}`.
5. Fix the imports: `propSelection` from `../lib/board`, `sameLeg` from `../lib/slip`, `useSlip`, and `BoardGame` / `SlipSelection` from `../types`. Remove `propTarget` and `BetTarget`.

- [ ] **Step 6: TodaysPicks and PlayerProps "Bet This" add to the slip**

In `frontend/src/pages/TodaysPicks.tsx`:
1. Delete the `BetModal` import, the `betModalOpen` / `betModalData` state and the `{betModalData && <BetModal .../>}` block.
2. Add `import { useSlip } from '../stores/slipStore'`, `import { useToast } from '../hooks/useToast'` and `import { selectionFromPick, type PickInput } from '../lib/slip'`.
3. Replace the three handlers with:

```tsx
  const addToSlip = useSlip(s => s.add);
  const { toast } = useToast();
  const startOf = (gameId: number) => gamesData.find(g => g.id === gameId)?.start_time ?? null;
  // A model pick goes on the slip at the price the model saw; if the book has
  // moved since, the server's price_moved answer shows the new price.
  const addPick = (p: PickInput) => {
    const sel = selectionFromPick({ ...p, startTime: startOf(p.gameId) });
    if (!sel) { toast("This pick can't be bet here.", 'error'); return; }
    addToSlip(sel);
    toast(`Added to bet slip: ${sel.label}`, 'success');
  };

  const handleBetPick = (pick: PickData) => addPick({
    pickType: pick.pick_type,
    value: pick.stored_pick_value ?? (pick.pick_type === 'prop' ? pick.pick_value : undefined),
    label: pick.pick_value, gameId: pick.game_id, odds: pick.odds_at_pick,
    propMarket: pick.prop_market, propPlayer: pick.prop_player,
    homeTeam: pick.home_team, awayTeam: pick.away_team, gameLabel: pick.matchup,
  });

  const handleBetProp = (p: PropData) => addPick({
    pickType: 'prop', value: `${p.player_name} ${p.outcome} ${p.line}`,
    label: `${p.player_name} ${p.outcome} ${p.line} ${p.market_label}`, gameId: p.game_id, odds: p.odds,
    propMarket: p.market, propPlayer: p.player_name, gameLabel: p.matchup,
  });

  const handleBetFromCard = (bet: { pickValue: string; betValue?: string; pickType: string; odds: number; gameId: number;
    edgePct?: number; homeTeam?: string; awayTeam?: string }) => addPick({
    pickType: bet.pickType, value: bet.betValue, label: bet.pickValue, gameId: bet.gameId, odds: bet.odds,
    homeTeam: bet.homeTeam, awayTeam: bet.awayTeam,
  });
```

`gamesData` is declared further down the component, but the handlers only read it when a button is clicked, after render. That is fine at runtime. If eslint's `no-use-before-define` objects, move `startOf` below the `gamesData` declaration.

In `frontend/src/pages/PlayerProps.tsx`:
1. Delete the `BetModal` import, its state and its render block.
2. Replace `handleBetProp` with:

```tsx
  const addToSlip = useSlip(s => s.add);
  const { toast } = useToast();
  const handleBetProp = (p: PropData) => {
    const sel = selectionFromPick({ pickType: 'prop', value: `${p.player_name} ${p.outcome} ${p.line}`,
      label: `${p.player_name} ${p.outcome} ${p.line} ${p.market_label}`, gameId: p.game_id, odds: p.odds,
      propMarket: p.market, propPlayer: p.player_name, gameLabel: p.matchup });
    if (!sel) { toast("This prop can't be bet here.", 'error'); return; }
    addToSlip(sel);
    toast(`Added to bet slip: ${sel.label}`, 'success');
  };
```

with the imports `useSlip`, `useToast` and `selectionFromPick`.

- [ ] **Step 7: Delete BetModal and BetTarget**

```bash
git rm frontend/src/components/BetModal.tsx frontend/src/components/BetModal.test.tsx
```

Delete the `BetTarget` interface from `frontend/src/types.ts`. Then run `grep -rn "BetModal\|BetTarget\|priceSource" frontend/src`.

Expected: no matches outside comments. Reword any comment that still names `BetModal` so that it names the slip.

`BetModal.test.tsx`'s PIN and user-creation behaviours are now covered by `BetSlip.test.tsx` (PIN, 401) and Task 5 (join).

- [ ] **Step 8: Run the tests and confirm they pass**

Run: `cd frontend && npx vitest run src/components src/lib src/pages && npx tsc -p tsconfig.app.json --noEmit && npx eslint .`

Expected: all pass, and both tsc and eslint are clean.

- [ ] **Step 9: Mutation-check**

Make each change, confirm a test fails, then restore:
1. In `modelPickSelection`, use `odds: q.odds`. The board test and the strip test must fail.
2. In `Tile`, pass `selected={false}`. The Lobby `aria-pressed` test must fail.
3. In `TodaysPicks`'s `handleBetPick`, pass `value: pick.pick_value`. The mutation-check test must fail, because 'NYY ML' doesn't map.

- [ ] **Step 10: Commit**

```bash
git add frontend/src/components/OddsTile.tsx frontend/src/components/OddsTile.test.tsx frontend/src/lib/board.ts frontend/src/lib/board.test.ts frontend/src/components/BoardGameCard.tsx frontend/src/components/BoardGameCard.test.tsx frontend/src/components/ModelPicksStrip.tsx frontend/src/pages/Lobby.tsx frontend/src/pages/Lobby.test.tsx frontend/src/pages/GameDetail.tsx frontend/src/pages/GameDetail.test.tsx frontend/src/pages/TodaysPicks.tsx frontend/src/pages/TodaysPicks.test.tsx frontend/src/pages/PlayerProps.tsx frontend/src/pages/PlayerProps.test.tsx frontend/src/types.ts
git commit -m "feat(slip): every bet button feeds the slip; retire BetModal

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Join the league from the player chip

**Files:**
- Modify: `frontend/src/components/PlayerChip.tsx`
- Test: `frontend/src/components/Layout.test.tsx`

**Interfaces:**
- **Consumes:** `api.users.create(name, pin): { id, name }`, `setPin`, `getErrorMessage` and `useUserStore.setCurrentUserName`.
- **Produces:** a "Join the league" form in the chip menu (spec §4).

- [ ] **Step 1: Write the failing tests**

In `frontend/src/components/Layout.test.tsx`:
1. Add `create: vi.fn()` to the mocked `users` object.
2. Add `waitFor` to the Testing Library import, and `import { getPin } from '../lib/secrets'`.
3. Add:

```tsx
  it('joins the league from the chip and becomes the current player', async () => {
    vi.mocked(api.users.create).mockResolvedValue({ id: 3, name: 'Jo' })
    renderAt('/')
    fireEvent.click(await screen.findByRole('button', { name: 'Marcus · $10,240.50' }))
    fireEvent.click(screen.getByRole('button', { name: /Join the league/ }))
    fireEvent.change(screen.getByLabelText('Your name'), { target: { value: 'Jo' } })
    fireEvent.change(screen.getByLabelText('New PIN'), { target: { value: '4321' } })
    fireEvent.click(screen.getByRole('button', { name: 'Join' }))
    await waitFor(() => expect(useUserStore.getState().currentUserName).toBe('Jo'))
    expect(api.users.create).toHaveBeenCalledWith('Jo', '4321')
    expect(getPin(3)).toBe('4321')
  })

  it('refuses a malformed PIN before calling the server', async () => {
    renderAt('/')
    fireEvent.click(await screen.findByRole('button', { name: 'Marcus · $10,240.50' }))
    fireEvent.click(screen.getByRole('button', { name: /Join the league/ }))
    fireEvent.change(screen.getByLabelText('Your name'), { target: { value: 'Jo' } })
    fireEvent.change(screen.getByLabelText('New PIN'), { target: { value: '12' } })
    fireEvent.click(screen.getByRole('button', { name: 'Join' }))
    expect(screen.getByRole('alert')).toHaveTextContent('PIN must be 4–6 digits.')
    expect(api.users.create).not.toHaveBeenCalled()
  })
```

Run: `cd frontend && npx vitest run src/components/Layout.test.tsx`

Expected: the two new tests FAIL, because there is no "Join the league" button.

- [ ] **Step 2: Add the form to `frontend/src/components/PlayerChip.tsx`**

```tsx
import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { api, getErrorMessage } from '../api/client'
import { useUserStore } from '../stores/userStore'
import { formatMoney } from '../lib/board'
import { setPin } from '../lib/secrets'

export default function PlayerChip() {
  const { currentUserName, setCurrentUserName } = useUserStore()
  const queryClient = useQueryClient()
  const users = useQuery({ queryKey: ['users', 'list'], queryFn: api.users.list, refetchInterval: 60_000 })
  const [open, setOpen] = useState(false)
  const [joining, setJoining] = useState(false)
  const [name, setName] = useState('')
  const [newPin, setNewPin] = useState('')
  const [error, setError] = useState<string | null>(null)
  const me = users.data?.find(u => u.name === currentUserName)

  const join = async () => {
    const n = name.trim()
    if (!n) { setError('Enter a name.'); return }
    if (!/^\d{4,6}$/.test(newPin)) { setError('PIN must be 4–6 digits.'); return }
    try {
      const res = await api.users.create(n, newPin)
      setPin(res.id, newPin)
      setCurrentUserName(res.name)
      queryClient.invalidateQueries({ queryKey: ['users'] })
      setJoining(false); setOpen(false); setName(''); setNewPin(''); setError(null)
    } catch (e) {
      setError(getErrorMessage(e))
    }
  }

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
          {joining ? (
            <div className="sb-join">
              <input className="input" aria-label="Your name" placeholder="Your name" value={name}
                onChange={e => setName(e.target.value)} />
              <input className="input" aria-label="New PIN" type="password" inputMode="numeric" autoComplete="off"
                maxLength={6} placeholder="PIN (4–6 digits)" value={newPin} onChange={e => setNewPin(e.target.value)}
                onKeyDown={e => e.key === 'Enter' && join()} />
              {error && <p className="sb-slip-error" role="alert">{error}</p>}
              <button type="button" className="sb-place" onClick={join}>Join</button>
            </div>
          ) : (
            <button type="button" onClick={() => setJoining(true)}>+ Join the league</button>
          )}
        </div>
      )}
    </div>
  )
}
```

Append this to `frontend/src/sportsbook.css`:

```css
.sb-join { display: flex; flex-direction: column; gap: 0.4rem; padding: 0.4rem; }
```

- [ ] **Step 3: Run the tests and confirm they pass**

Run: `cd frontend && npx vitest run src/components/Layout.test.tsx && npx tsc -p tsconfig.app.json --noEmit && npx eslint .`

Expected: all pass.

- [ ] **Step 4: Mutation-check**

Make each change, confirm a test fails, then restore:
1. Delete `setPin(res.id, newPin)`. The join test must fail.
2. Delete the PIN-format check. The malformed-PIN test must fail.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/PlayerChip.tsx frontend/src/components/Layout.test.tsx frontend/src/sportsbook.css
git commit -m "feat(nav): join the league from the player chip

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Retire the PaperTrading pick form and parlay builder

**Files:**
- Modify: `frontend/src/pages/PaperTrading.tsx`
- Delete: `frontend/src/components/QuotePicker.tsx`, `frontend/src/components/QuotePicker.test.tsx`, `frontend/src/components/PropQuotePicker.tsx` and `frontend/src/components/PropQuotePicker.test.tsx`, but only once Step 1's grep shows no other users.
- Test: `frontend/src/pages/PaperTrading.test.tsx`

**Interfaces:**
- **Consumes:** nothing new.
- **Produces:** the PaperTrading page without its own bet placement. It keeps Join, the leaderboard, the feed, the player stats and the pick history until Phases 3 and 5 replace them. A card points to the Lobby.

- [ ] **Step 1: Check who else uses the pickers**

Run: `cd frontend && grep -rln "QuotePicker\|PropQuotePicker" src`

Expected: only `PaperTrading.tsx` and the pickers' own files and tests. If anything else imports them, keep that picker and record a ruling.

- [ ] **Step 2: Write the failing test**

In `frontend/src/pages/PaperTrading.test.tsx`, add this test inside the `describe`. It uses the file's existing mocks and `user` helper. The page is already rendered inside `MemoryRouter`, which the new `<Link>` needs.

```tsx
  it('sends a player to the Lobby to bet instead of its own pick form', async () => {
    useUserStore.setState({ selectedUser: user(10000) })
    vi.mocked(api.users.list).mockResolvedValue([user(10000)])
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(
      <QueryClientProvider client={qc}>
        <MemoryRouter><ToastProvider><PaperTrading /></ToastProvider></MemoryRouter>
      </QueryClientProvider>)
    expect(await screen.findByRole('link', { name: 'Lobby' })).toHaveAttribute('href', '/')
    expect(screen.queryByText('Parlay Builder')).not.toBeInTheDocument()
    expect(screen.queryByText('Place a Pick')).not.toBeInTheDocument()
  })
```

Run: `cd frontend && npx vitest run src/pages/PaperTrading.test.tsx`

Expected: the new test FAILS, because "Parlay Builder" is present.

- [ ] **Step 3: Remove the form and the builder from `PaperTrading.tsx`**

1. **JSX.** Delete everything from `{/* Place Pick Form */}` down to, but not including, `{/* Pick History */}`. That is the "Place a Pick" card and the "Parlay Builder" card. Put this in their place:

```tsx
          <div className="card" style={{ marginBottom: '1rem' }}>
            <div className="input-label">Place bets from the Lobby</div>
            <p className="text-muted" style={{ margin: '0.35rem 0 0' }}>
              Tap any price in the <Link to="/">Lobby</Link> to add it to your bet slip — singles and parlays
              are both placed from there.
            </p>
          </div>
```

2. **State and handlers.** Delete everything that only served those cards:
   - **State:** `betPin` / `setBetPin` with the `pinFor` block; `selectedGame`, `pickType`, `chosen`, `stake`, `gameQuotes`, `propQuotes`, `game` and `newestQuote`; and the `ParlayLeg` type with `parlayLegs` and `parlayStake`.
   - **Handlers:** `forgetPinOn401`, `handlePlacePick`, `addParlayLeg`, `removeParlayLeg`, `estimate`, `handlePlaceParlay`, `handleGameSelect` and `handlePickTypeChange`.

3. **Imports and data.**
   - Delete the now-unused imports: `QuotePicker`, `PropQuotePicker`, `useGameQuotes` / `usePropQuotes`, the `../lib/quotes` import, `ApiError`, `getPin`, and the types `AvailableGameQuote`, `AvailablePropQuote`, `BetLeg` and `GamePickType`.
   - Keep `setPin`, because `handleCreateUser` uses it.
   - Delete `const games = gamesQuery.data ?? []` and the `games:` destructure if nothing else reads them.
   - Add `import { Link } from 'react-router-dom'`.

Let `npx tsc -p tsconfig.app.json --noEmit` and `npx eslint .` list anything still unused, and remove exactly those.

- [ ] **Step 4: Delete the pickers**

```bash
git rm frontend/src/components/QuotePicker.tsx frontend/src/components/QuotePicker.test.tsx frontend/src/components/PropQuotePicker.tsx frontend/src/components/PropQuotePicker.test.tsx
```

- [ ] **Step 5: Run the whole frontend suite**

Run: `cd frontend && npm test && npx tsc -p tsconfig.app.json --noEmit && npx eslint .`

Expected: all pass, and both tsc and eslint are clean.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/pages/PaperTrading.tsx frontend/src/pages/PaperTrading.test.tsx
git commit -m "refactor(paper): retire the PaperTrading pick form and parlay builder; bets go through the slip

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Verify end to end in a worktree, then hand off for merge

**Files:** none are created.

- [ ] **Step 1: Run the full suites**

```bash
cd /c/Users/mwill/Documents/mwilliams2733/sports_picks
.venv/Scripts/python -m pytest -q -p no:warnings    # bare pytest: backend/tests + tests/
cd frontend && npm test && npx tsc -p tsconfig.app.json --noEmit && npx eslint .
```

Expected:
- The backend gives 2311, the Phase 1 merge baseline, plus 7 new tests, with 0 failures.
- The frontend passes. It is lint-clean and type-clean.

- [ ] **Step 2: Build and serve the branch from a worktree, never from the main tree**

```bash
cd /c/Users/mwill/Documents/mwilliams2733/sports_picks
SCR="C:/Users/mwill/AppData/Local/Temp/claude/C--Users-mwill-Documents-mwilliams2733-sports-picks/67e9eb83-b058-4787-8473-179481446ea3/scratchpad"
WT="$SCR/wt-phase2"
git worktree add --detach "$WT" HEAD
(cd "$WT/frontend" && npm ci --legacy-peer-deps && npm run build)
.venv/Scripts/python -c "import sqlite3,sys; s=sqlite3.connect('sports_picks.db'); d=sqlite3.connect(sys.argv[1]); s.backup(d); d.close()" "$SCR/phase2-snapshot.db"
```

Start the server in the background, with `run_in_background`. The `cd` makes the worktree's `backend` the one imported:

```bash
cd "$WT" && DATABASE_PATH="$SCR/phase2-snapshot.db" ENABLE_SCHEDULER=0 /c/Users/mwill/Documents/mwilliams2733/sports_picks/.venv/Scripts/python -m uvicorn backend.api.main:app --port 8001
```

Confirm three things:
- :8001 serves the worktree's build: the `index-*.js` name in `curl -s http://127.0.0.1:8001/` matches `ls "$WT/frontend/dist/assets"`.
- :8000 still serves master's build.
- The schema has the new field: `curl -s http://127.0.0.1:8001/openapi.json | grep -c expected_odds` returns at least 1.

- [ ] **Step 3: Check it in Chrome at phone and desktop width**

This uses the snapshot only, so bets placed here touch nothing live.
1. **Desktop:** use the window. **Phone:** use a same-origin 390px iframe, because the Chrome window won't go below 980px.
2. **Test player:** join the league from the chip as a test player, with a PIN generated for this check. Don't echo it.
3. **Check each of these:**
   1. **Selecting:** tapping a price turns it green, and the slip appears: the right-hand column on desktop, the green bar above the tab bar on a phone. Tapping it again removes it, and tapping the other side swaps it.
   2. **Singles:** stake chips and "To win" work, and placing two singles shows the ticket with `#P-` ids.
   3. **Parlay:** two legs on one game show the SGP tag and the estimate. Placing it shows `#PL-`.
   4. **Price moved:**
      1. Add a leg.
      2. Change that game's price in the **snapshot** db with `sqlite3`: update one `odds` row's price and set its `timestamp` to now.
      3. Press Place. The leg should show "Odds changed …" and the button should read Accept & Place.
      4. Accept places the bet.
   5. **Model Picks:** "Bet This" on Model Picks adds to the slip at the model's price.
   6. **Persistence:** reloading the page keeps the slip.
   7. **PaperTrading:** the page shows the Lobby card and has no pick form.
   8. **No white-on-green:** check the new slip controls for white text on green.
4. Save screenshots for the owner.

- [ ] **Step 4: Clean up the worktree**

1. Stop the :8001 server.
2. Run `git worktree remove --force "$WT"`.
3. Confirm `git worktree list` shows only the main tree. `npm ci` gave the worktree its own `node_modules`, so removing it touches nothing in the main tree.

- [ ] **Step 5: Report to the owner and wait for "merge"**

Summarise:
- what shipped;
- the screenshots;
- the rulings;
- that the Claude-picks request shape still works (the no-expected-fields test).

Do **not** merge or restart until the owner says so.

- [ ] **Step 6: Merge and restart, on the owner's OK**

```bash
git checkout master
git merge --no-ff feat/sportsbook-phase-2 -m "Merge: sportsbook phase 2 — bet slip

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
npm --prefix frontend run build
grep -rn "backend.api" backend/pipeline | head    # expect nothing: the scheduler never loads the API code
```

1. **Restart uvicorn on :8000 only, the way `scripts/share.ps1` starts it.** The tunnel stays up, so its URL is unchanged.
   1. Stop the two `uvicorn backend.api.main:app --port 8000` processes. Find them first with `Get-CimInstance Win32_Process`.
   2. Move `app.log` to `app.log.prev`.
   3. Use `Start-Process` with `ENABLE_SCHEDULER=0`, `DATABASE_PATH=<repo>\sports_picks.db`, `-WindowStyle Hidden`, and stderr to `app.log`.
   4. Restore the environment variables afterwards.
2. **Leave the scheduler alone,** because it loads no Phase 2 code. If the grep above prints anything, stop and restart the scheduler too, per memory `sports-picks-scheduler-operations`.
3. **Confirm with real calls. Place no live bets.**
   1. `/openapi.json` on :8000 lists `expected_odds`.
   2. The tunnel URL returns 200.
   3. Its `index-*.js` matches the new `frontend/dist`.
4. **Update memory:** `sports-picks-sportsbook-ui.md` → "Phase 2 merged <sha>".

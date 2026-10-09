# Sportsbook Phase 6 — Cash Out Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A player can cash out an open straight bet or parlay before kickoff
for `stake × D_placed × p_now × 0.95`. The cash out settles the bet as
`cashed_out`, frees the stake, posts a feed event and counts in leaderboard
ROI but not in W-L.

**Architecture:**
- A new `backend/paper/cashout.py` is the only place an offer is computed.
  It re-prices each leg and its opposite side through `pricing.price`, the
  same function that prices bets, de-vigs the pair, and refuses with a
  stable reason code.
- `POST /users/{id}/cashout` and the My Bets tickets both call it, so the
  offer shown on a ticket comes from the same function that pays it.
- Readers that branch on `result` are audited. The scorecard gains a
  `cashed_out` count, and streaks skip cashed-out bets the way they skip
  pushes.
- No schema change: the new result value fits the existing `result` columns.

**Tech Stack:** FastAPI, SQLAlchemy and SQLite, pytest; React 19,
react-query 5 and vitest.

**Spec:** `docs/superpowers/specs/2026-10-07-sportsbook-ui-design.md`, §9
(cash out) and the §13 Phase 6 row. Read §9 before Task 1.

## Global Constraints

- Cash out is **pre-game only**, at a **5% house margin**, and it **counts
  toward the leaderboard ROI**.
- `offer = stake × D_placed × p_now × (1 − 0.05)`, rounded **down** to the cent.
- `CASH_OUT_MARGIN = 0.05` is a named constant.
- `D_placed` is the decimal price of the bet as placed. For a parlay it is the
  combined decimal price.
- `p_now` is the **no-vig** probability of the *same side at the same line*,
  from the current consensus pair priced by `pricing` (that side and the
  opposite side). For a parlay it is the product over the legs.
- Refusal reason codes: `game_started`, `line_moved`, `stale` / `not_quoted`,
  `settled`, plus `offer_changed` on the endpoint.
- `POST /users/{id}/cashout`, with body `{bet_id, kind: "straight"|"parlay",
  expected_offer}`. It requires the player PIN and takes `hold_bankroll`.
- On success it sets `result = "cashed_out"` and `payout = offer − stake`
  (plus `graded_at = now` on a straight bet), and logs a `cashed_out` feed
  event.
- For a parlay the result goes on the `Parlay` row. The legs stay ungraded
  and are graded normally later, at stake 0, with no money effect.
- On the leaderboard, a cash out is a settled bet in ROI with
  profit = payout, and counts in neither wins nor losses.
- `docs/data-dictionary.md` gets a dated entry for the new result value.
- UI:
  - An open ticket shows **Cash out $87.40**, or a greyed button with the
    reason.
  - Tapping it asks for confirmation.
  - On success the ticket is marked "CASHED OUT".
- Paper money only; never DraftKings/FanDuel names, logos or exact colours.
- Stage only named files. Never stage `config.yaml`, which holds the
  owner's uncommitted edit.
- Commit trailer: `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. **Kickoff passes between viewing the offer and confirming.** The server
   refuses with `game_started` and pays nothing (Task 2,
   `test_an_offer_gone_by_confirm_time_is_refused_with_its_reason`).
2. **Double-tap, or two tabs, confirm the same cash out.** Exactly one
   cash out happens; the second gets 409 `settled` and money moves once
   (Task 2, `test_a_bet_cashes_out_once`).
3. **The offer drops between view and confirm.** The player is never paid
   less than shown without being asked again. The server returns
   `offer_changed` with the new figure and the UI asks again (Task 2
   `test_an_offer_below_the_one_shown_is_refused_with_the_new_offer`; Task 4
   `asks again at the new offer when it changed`).
4. **A cashed-out parlay's legs are graded later.** No money moves, there is
   no won/lost announcement, and the streak doesn't change (Task 2
   `test_a_cashed_out_parlay_stays_cashed_out_when_its_legs_grade`; Task 3
   `test_a_cash_out_neither_extends_nor_breaks_a_streak`).
5. **A legacy or unparseable stored `pick_value`** shows no offer
   (`not_quoted`), and My Bets still loads (Task 1
   `test_an_unparseable_stored_bet_has_no_offer`; Task 2
   `test_my_bets_carries_the_offer_on_open_tickets_only`).

## Rulings made while planning (carry into the ledger)

- **`Parlay` has no `graded_at` column.** Phase 6 adds no migration. A
  parlay's cash-out time is the `cashed_out` feed event's `created_at`.
  - Cost if wrong: one nullable column and a migration later.
- **The opposite side must quote the mirror line.**
  - For a spread, the opposite line is −line; for a total it is the same
    line. If it isn't, the reason is `line_moved`.
  - For a prop, a bet whose line no longer exists while the same
    player/market/outcome is quoted at another line is `line_moved`;
    otherwise `not_quoted`.
  - Cost if wrong: a few offers withheld when books disagree.
- **`D_placed` is computed through settlement's own `payout_for("win", odds)`**,
  so a cash out and a win use one definition of the price. For a parlay it
  is the product of the leg decimals (exactly `parlay_win_payout`'s), not
  the rounded `combined_odds`.
- **A cash out counts toward the 10 settled bets a player needs to be
  ranked.** `Summary.n` includes it, since it is a settled bet.
  - Cost if wrong: a player ranks a few bets sooner.
- **A cashed-out bet's P/L day stays its game's date.** That is the
  existing `player_bets` convention, so "Today's P/L" shows a Thursday cash
  out of a Sunday game on Sunday.
  - Cost if wrong: a cash out appears a few days later in Today's P/L.
- **The `cashed_out` feed event carries `bet_key = "<kind>-<id>"`.** Phase 5's
  unique index then guarantees one cash-out event per bet. Such a bet is
  never announced as won or lost, because it is never graded.

---

## File Structure

| File | Responsibility |
|---|---|
| `backend/paper/cashout.py` (new) | Offer math, the stored-bet parser, refusal codes, `cash_out` (the write) |
| `backend/tests/cashout_helpers.py` (new) | Test client, games, users, `reprice`, `place`, `offer_of` |
| `backend/tests/test_cashout.py` (new) | Offer formula and each refusal |
| `backend/tests/test_cashout_api.py` (new) | Endpoint, money effects, settlement skip, feed, tickets |
| `backend/api/users.py` | `CashOutRequest` and the `POST /{user_id}/cashout` route |
| `backend/paper/bets.py` | `cash_out` view on each ticket |
| `backend/paper/feed.py` | `_straight_label` renamed to public `straight_label`; streaks skip `cashed_out` |
| `backend/analysis/scorecard.py` | `cashed_out` count in `Summary`; `n` includes it |
| `docs/data-dictionary.md` | Dated entry for `cashed_out` |
| `frontend/src/types.ts`, `api/client.ts` | `CashOutView`, `Ticket.cash_out`, `api.users.cashOut` |
| `frontend/src/lib/cashout.ts` (new) | `cashOutRefusal` |
| `frontend/src/components/CashOutButton.tsx` (new) | Offer button, confirm, PIN, refusals |
| `frontend/src/components/TicketCard.tsx`, `lib/bets.ts` | CASHED OUT stamp and label; button on open tickets |
| `frontend/src/pages/MyBets.tsx` | Passes the player id to tickets |
| `frontend/src/components/FeedList.tsx`, `hooks/useWebSocket.ts` | `cashed_out` events |
| `frontend/src/sportsbook.css`, `pages/FAQ.tsx` | Styles; one FAQ sentence |

---

### Task 1: The offer engine

**Files:**
- Create: `backend/paper/cashout.py`
- Create: `backend/tests/cashout_helpers.py`
- Test: `backend/tests/test_cashout.py`

**Interfaces:**
- Consumes:
  - `pricing.price(session, game, bet, now) -> Quote`, which raises
    `PricingError(reason)`;
  - `pricing.GameBet(game_id, pick_type, side)` and
    `pricing.PropBet(game_id, prop_player, prop_market, outcome, line)`,
    both frozen dataclasses;
  - `pricing.GAME_MARKETS`;
  - `odds_utils.american_to_implied_prob`, `odds_utils.remove_vig(a, b) -> (a_fair, b_fair)`
    and `odds_utils.parse_pick_line`;
  - `grader.payout_for("win", odds)`.
- Produces:
  - `CASH_OUT_MARGIN = 0.05`;
  - `class CashOutUnavailable(Exception)`, with `.reason` and `.message`;
  - `@dataclass(frozen=True) class Offer: amount: float; p_now: float; d_placed: float`;
  - `offer(session, kind: str, row, now: datetime | None = None) -> Offer`,
    where `kind` is `"straight"` (row is a `PaperPick`) or `"parlay"` (row
    is a `Parlay`). It raises `CashOutUnavailable`;
  - `offer_view(session, kind, row, now=None) -> dict | None`:
    - `None` for a settled row;
    - `{"available": True, "offer": float}`;
    - `{"available": False, "reason": str, "message": str}`.

- [ ] **Step 1: Write the test helpers**

`backend/tests/cashout_helpers.py`:

```python
"""Fixtures for the cash-out tests (sportsbook spec 2026-10-07 §9).

Games are scheduled NFL games dated today with no start time, so they are
open for betting, quoted by one fresh book: ML -110/-110, spread -3.5/+3.5,
total 220.5, all -110 (pricing_helpers.ODDS_DEFAULTS)."""
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from backend.api.main import create_app
from backend.database import get_session
from backend.models import Base, Game, Odds, PaperPick, Parlay, PlayerProp, Team
from backend.paper import cashout
from backend.tests.auth_helpers import ALL_HEADERS, TEST_PIN
from backend.tests.pricing_helpers import seed_fresh_odds
from backend.time_utils import et_today


def client():
    c = TestClient(create_app(":memory:"), headers=ALL_HEADERS)
    Base.metadata.create_all(c.app.state.engine)
    return c


def games(c, n=1, **odds):
    s = get_session(c.app.state.engine)
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
        seed_fresh_odds(c.app.state.engine, gid, **odds)
    return ids


def user(c, name="sam"):
    r = c.post("/users/", json={"name": name, "pin": TEST_PIN})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def place(c, uid, gid, stake=100, **leg):
    r = c.post(f"/users/{uid}/picks", json={"game_id": gid, "stake": stake, **leg})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def place_parlay(c, uid, legs, stake=20):
    r = c.post(f"/users/{uid}/parlay", json={"legs": legs, "stake": stake})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def reprice(c, gid, **fields):
    """Change what the game's book quotes now (every Odds row for it)."""
    s = get_session(c.app.state.engine)
    for o in s.query(Odds).filter(Odds.game_id == gid):
        for k, v in fields.items():
            setattr(o, k, v)
    s.commit()
    s.close()


def set_game(c, gid, **fields):
    s = get_session(c.app.state.engine)
    g = s.get(Game, gid)
    for k, v in fields.items():
        setattr(g, k, v)
    s.commit()
    s.close()


def set_row(c, model, row_id, **fields):
    s = get_session(c.app.state.engine)
    r = s.get(model, row_id)
    for k, v in fields.items():
        setattr(r, k, v)
    s.commit()
    s.close()


def offer_of(c, kind, bet_id):
    s = get_session(c.app.state.engine)
    try:
        row = s.get(PaperPick if kind == "straight" else Parlay, bet_id)
        return cashout.offer(s, kind, row)
    finally:
        s.close()


def reason_of(c, kind, bet_id):
    try:
        offer_of(c, kind, bet_id)
    except cashout.CashOutUnavailable as e:
        return e.reason
    return None


def stale(c, gid):
    reprice(c, gid, timestamp=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=7))


def props(c, gid):
    """Both sides of QB One's passing-yards prop at 225.5, -110 each."""
    s = get_session(c.app.state.engine)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    for outcome in ("Over", "Under"):
        s.add(PlayerProp(game_id=gid, bookmaker="testbook", market="player_pass_yds",
                         player_name="QB One", outcome=outcome, line=225.5, odds=-110, fetched_at=now))
    s.commit()
    s.close()
```

- [ ] **Step 2: Write the failing tests**

`backend/tests/test_cashout.py`:

```python
"""The cash-out offer (sportsbook spec 2026-10-07 §9):
stake x D_placed x p_now x (1 - 0.05), rounded down to the cent."""
import pytest

from backend.database import get_session
from backend.models import Odds, PaperPick, PlayerProp
from backend.paper import cashout
from backend.tests import cashout_helpers as h

AWAY_SPREAD = {"pick_type": "spread", "side": "AWAY"}
HOME_SPREAD = {"pick_type": "spread", "side": "HOME"}
OVER = {"pick_type": "over_under", "side": "Over"}
QB_OVER = {"pick_type": "prop", "prop_player": "QB One", "prop_market": "player_pass_yds",
           "outcome": "Over", "line": 225.5}


def test_the_margin_is_five_percent():
    assert cashout.CASH_OUT_MARGIN == 0.05


def test_an_even_market_offers_stake_times_price_times_half_less_the_margin():
    c = h.client()
    [g] = h.games(c)
    pid = h.place(c, h.user(c), g, **AWAY_SPREAD)
    o = h.offer_of(c, "straight", pid)
    # 100 x 1.90909 x 0.5 x 0.95 = 90.6818... -> rounded DOWN to 90.68
    assert (o.amount, o.p_now) == (90.68, 0.5)


def test_the_offer_rounds_down_to_the_cent_not_to_the_nearest():
    c = h.client()
    [g] = h.games(c)
    pid = h.place(c, h.user(c), g, stake=10, **AWAY_SPREAD)
    # 10 x 1.90909 x 0.5 x 0.95 = 9.0682 -> 9.06 (the nearest cent would be 9.07)
    assert h.offer_of(c, "straight", pid).amount == 9.06


def test_the_chance_now_is_the_no_vig_pair_not_the_raw_price():
    c = h.client()
    [g] = h.games(c, moneyline_home=-170, moneyline_away=150)
    pid = h.place(c, h.user(c), g, pick_type="moneyline", side="AWAY")
    h.reprice(c, g, moneyline_home=-200, moneyline_away=170)
    o = h.offer_of(c, "straight", pid)
    # p_now = (100/270) / (100/270 + 200/300) = 0.35714; 100 x 2.5 x 0.35714 x 0.95 = 84.82
    assert o.p_now == pytest.approx(0.357142857)
    assert o.amount == 84.82


def test_a_parlay_offer_multiplies_its_legs():
    c = h.client()
    g1, g2 = h.games(c, 2)
    plid = h.place_parlay(c, h.user(c), [{"game_id": g1, "pick_type": "moneyline", "side": "HOME"},
                                         {"game_id": g2, "pick_type": "moneyline", "side": "HOME"}])
    o = h.offer_of(c, "parlay", plid)
    # 20 x (1.90909^2 = 3.64463) x 0.25 x 0.95 = 17.3119... -> 17.31
    assert (o.amount, o.p_now) == (17.31, 0.25)


def test_a_prop_is_offered_against_its_other_side_at_the_same_line():
    c = h.client()
    [g] = h.games(c)
    h.props(c, g)
    pid = h.place(c, h.user(c), g, **QB_OVER)
    assert h.offer_of(c, "straight", pid).amount == 90.68


@pytest.mark.parametrize("change, reason", [
    (lambda c, g: h.set_game(c, g, status="in_progress"), "game_started"),
    (lambda c, g: h.reprice(c, g, spread_home=-4.5, spread_away=4.5), "line_moved"),
    (lambda c, g: h.stale(c, g), "stale"),
    (lambda c, g: _delete_odds(c, g), "not_quoted"),
])
def test_each_refusal_has_its_reason(change, reason):
    c = h.client()
    [g] = h.games(c)
    pid = h.place(c, h.user(c), g, **AWAY_SPREAD)
    change(c, g)
    assert h.reason_of(c, "straight", pid) == reason


def _delete_odds(c, g):
    s = get_session(c.app.state.engine)
    s.query(Odds).filter(Odds.game_id == g).delete()
    s.commit()
    s.close()


def test_a_moved_total_is_line_moved():
    c = h.client()
    [g] = h.games(c)
    pid = h.place(c, h.user(c), g, **OVER)
    h.reprice(c, g, over_under=221.5)
    assert h.reason_of(c, "straight", pid) == "line_moved"


def test_the_other_side_must_quote_the_mirror_line():
    c = h.client()
    [g] = h.games(c)
    pid = h.place(c, h.user(c), g, **HOME_SPREAD)
    h.reprice(c, g, spread_away=4.0)            # HOME still -3.5, AWAY now +4
    assert h.reason_of(c, "straight", pid) == "line_moved"


def test_a_settled_bet_has_no_offer():
    c = h.client()
    [g] = h.games(c)
    pid = h.place(c, h.user(c), g, **AWAY_SPREAD)
    h.set_row(c, PaperPick, pid, result="win", payout=90.91)
    assert h.reason_of(c, "straight", pid) == "settled"


def test_a_parlay_with_one_started_leg_has_no_offer():
    c = h.client()
    g1, g2 = h.games(c, 2)
    plid = h.place_parlay(c, h.user(c), [{"game_id": g1, "pick_type": "moneyline", "side": "HOME"},
                                         {"game_id": g2, "pick_type": "moneyline", "side": "HOME"}])
    h.set_game(c, g2, status="in_progress")
    assert h.reason_of(c, "parlay", plid) == "game_started"


def test_a_prop_whose_line_moved_is_line_moved_and_one_missing_its_other_side_is_not_quoted():
    c = h.client()
    [g] = h.games(c)
    h.props(c, g)
    pid = h.place(c, h.user(c), g, **QB_OVER)
    s = get_session(c.app.state.engine)
    for p in s.query(PlayerProp):
        p.line = 230.5
    s.commit()
    s.close()
    assert h.reason_of(c, "straight", pid) == "line_moved"
    s = get_session(c.app.state.engine)
    for p in s.query(PlayerProp):
        p.line = 225.5
    s.query(PlayerProp).filter(PlayerProp.outcome == "Under").delete()
    s.commit()
    s.close()
    assert h.reason_of(c, "straight", pid) == "not_quoted"


def test_an_unparseable_stored_bet_has_no_offer():
    """Review Focus 5: a legacy row (players typed their own values before
    plan 027) must read as no offer, never raise."""
    c = h.client()
    [g] = h.games(c)
    pid = h.place(c, h.user(c), g, **AWAY_SPREAD)
    h.set_row(c, PaperPick, pid, pick_value="AWAY")
    assert h.reason_of(c, "straight", pid) == "not_quoted"


def test_offer_view_shapes():
    c = h.client()
    [g] = h.games(c)
    pid = h.place(c, h.user(c), g, **AWAY_SPREAD)
    s = get_session(c.app.state.engine)
    try:
        pick = s.get(PaperPick, pid)
        assert cashout.offer_view(s, "straight", pick) == {"available": True, "offer": 90.68}
        g_row = pick.game
        g_row.status = "in_progress"
        s.commit()
        view = cashout.offer_view(s, "straight", pick)
        assert view == {"available": False, "reason": "game_started", "message": cashout.MESSAGES["game_started"]}
        pick.result = "loss"
        s.commit()
        assert cashout.offer_view(s, "straight", pick) is None
    finally:
        s.close()
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `.venv/Scripts/python -m pytest backend/tests/test_cashout.py -q -p no:warnings`
Expected: FAIL, with `ImportError: cannot import name 'cashout' from 'backend.paper'`.

- [ ] **Step 4: Write the implementation**

`backend/paper/cashout.py`:

```python
"""Cash out (sportsbook spec 2026-10-07 §9): pre-game only, 5% margin.

``offer = stake x D_placed x p_now x (1 - CASH_OUT_MARGIN)``, rounded down to
the cent. D_placed comes from settlement's own ``payout_for`` (a parlay: the
product of its legs, exactly ``parlay_win_payout``'s). p_now is the no-vig
chance of the same side at the same line, from the consensus pair that
``pricing.price`` -- the function that prices every bet -- quotes now. A
parlay's p_now is the product over its legs.

The endpoint and the My Bets tickets both call :func:`offer`, so the offer a
ticket shows is the one the endpoint pays.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, replace
from datetime import datetime, timezone

from backend.analysis.odds_utils import american_to_implied_prob, parse_pick_line, remove_vig
from backend.models import Game, PaperPick, Parlay, PlayerProp
from backend.paper import pricing
from backend.paper.pricing import GAME_MARKETS, GameBet, PricingError, PropBet
from backend.pipeline.grader import payout_for

CASH_OUT_MARGIN = 0.05

MESSAGES = {
    "game_started": "A game in this bet has started — cash out is pre-game only.",
    "line_moved": "The line has moved since you bet, so there's no cash out offer.",
    "stale": "The price is stale — ask Marcus to refresh.",
    "not_quoted": "No book is quoting this bet right now.",
    "not_gradeable": "This market can't be graded, so it can't be cashed out.",
    "settled": "This bet has already settled.",
}

_FLIP = {"HOME": "AWAY", "AWAY": "HOME", "Over": "Under", "Under": "Over"}
_PROP = re.compile(r"\b(Over|Under) (\d+(?:\.\d+)?)\b")


class CashOutUnavailable(Exception):
    """No offer for this bet now. ``reason`` is a stable code."""

    def __init__(self, reason: str):
        self.reason = reason
        self.message = MESSAGES[reason]
        super().__init__(self.message)


@dataclass(frozen=True)
class Offer:
    amount: float
    p_now: float
    d_placed: float


def _stored_bet(pick: PaperPick) -> tuple[GameBet | PropBet, float | None]:
    """The bet a stored pick is, and its line, parsed back out of pick_value
    ("HOME -3.5", "Over 220.5", "QB One Over 225.5 Pass Yds")."""
    if pick.pick_type == "prop":
        m = _PROP.search(pick.pick_value or "")
        if not (m and pick.prop_player and pick.prop_market):
            raise CashOutUnavailable("not_quoted")
        line = float(m.group(2))
        return PropBet(pick.game_id, pick.prop_player, pick.prop_market, m.group(1), line), line
    side = (pick.pick_value or "").split(" ")[0]
    if (pick.pick_type, side) not in GAME_MARKETS:
        raise CashOutUnavailable("not_quoted")
    if pick.pick_type == "moneyline":
        return GameBet(pick.game_id, pick.pick_type, side), None
    line = parse_pick_line(pick.pick_value)
    if line is None:
        raise CashOutUnavailable("not_quoted")
    return GameBet(pick.game_id, pick.pick_type, side), line


def _opposite(bet, line):
    if isinstance(bet, PropBet):
        return replace(bet, outcome=_FLIP[bet.outcome]), line
    mirror = None if line is None else (-line if bet.pick_type == "spread" else line)
    return GameBet(bet.game_id, bet.pick_type, _FLIP[bet.side]), mirror


def _prop_moved(session, bet: PropBet) -> bool:
    return (session.query(PlayerProp.id)
            .filter(PlayerProp.game_id == bet.game_id, PlayerProp.player_name == bet.prop_player,
                    PlayerProp.market == bet.prop_market, PlayerProp.outcome == bet.outcome,
                    PlayerProp.line.isnot(None), PlayerProp.line != bet.line)
            .first()) is not None


def _quote(session, game, bet, line, now) -> pricing.Quote:
    try:
        q = pricing.price(session, game, bet, now)
    except PricingError as e:
        if e.reason == "not_quoted" and isinstance(bet, PropBet) and _prop_moved(session, bet):
            raise CashOutUnavailable("line_moved") from None
        raise CashOutUnavailable(e.reason) from None
    if line is not None and abs(q.line - line) > 1e-9:
        raise CashOutUnavailable("line_moved")
    return q


def _leg_chance(session, pick: PaperPick, now: datetime) -> float:
    game = session.get(Game, pick.game_id)
    bet, line = _stored_bet(pick)
    mine = _quote(session, game, bet, line, now)
    other_bet, other_line = _opposite(bet, line)
    theirs = _quote(session, game, other_bet, other_line, now)
    return remove_vig(american_to_implied_prob(mine.odds), american_to_implied_prob(theirs.odds))[0]


def _decimal(odds: int) -> float:
    return 1 + payout_for("win", odds)


def _round_down(x: float) -> float:
    # round() first so 90.68000000000001 stays 90.68 instead of a float
    # artefact like 90.6799999 flooring to 90.67.
    return math.floor(round(x * 100, 6)) / 100


def offer(session, kind: str, row, now: datetime | None = None) -> Offer:
    """The cash-out offer for a straight bet (PaperPick) or a parlay (Parlay)."""
    now = now or datetime.now(timezone.utc)
    if row.result is not None:
        raise CashOutUnavailable("settled")
    if kind == "straight":
        legs = [row]
    else:
        legs = (session.query(PaperPick).filter(PaperPick.parlay_id == row.id)
                .order_by(PaperPick.id).all())
    p_now = d_placed = 1.0
    for leg in legs:
        p_now *= _leg_chance(session, leg, now)
        d_placed *= _decimal(leg.odds)
    amount = _round_down(row.stake * d_placed * p_now * (1 - CASH_OUT_MARGIN))
    return Offer(amount=amount, p_now=p_now, d_placed=d_placed)


def offer_view(session, kind: str, row, now: datetime | None = None) -> dict | None:
    """A ticket's cash-out field: None once settled."""
    if row.result is not None:
        return None
    try:
        o = offer(session, kind, row, now)
    except CashOutUnavailable as e:
        return {"available": False, "reason": e.reason, "message": e.message}
    return {"available": True, "offer": o.amount}
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/Scripts/python -m pytest backend/tests/test_cashout.py -q -p no:warnings`
Expected: PASS (all tests).

- [ ] **Step 6: Mutation-check the guards** (delete the .pyc after each write and after the restore)

Make each mutation below, run `backend/tests/test_cashout.py`, confirm that
at least one named test fails, then restore:
- In `_quote`, delete the `line is not None and abs(...)` check. Expect
  `test_each_refusal_has_its_reason[...line_moved]` and
  `test_a_moved_total_is_line_moved` to fail.
- In `_opposite`, replace `-line if bet.pick_type == "spread" else line`
  with `line`. Expect `test_an_even_market_offers...` to fail, with
  line_moved on the mirror.
- In `_round_down`, replace `math.floor(...)` with `round(...)`. Expect
  `test_the_offer_rounds_down_to_the_cent_not_to_the_nearest` to fail
  (9.07). Every other reference value happens to round the same way.
- In `offer`, replace `p_now *=` with `p_now = `. Expect
  `test_a_parlay_offer_multiplies_its_legs` to fail.

- [ ] **Step 7: Commit**

```bash
git add backend/paper/cashout.py backend/tests/cashout_helpers.py backend/tests/test_cashout.py
git commit -m "feat(cashout): the offer -- stake x placed price x no-vig chance now, less 5%"
```

---

### Task 2: The endpoint, the tickets and the feed event

**Files:**
- Modify: `backend/paper/cashout.py` (add `cash_out`)
- Modify: `backend/api/users.py` (request model and route, next to `place_parlay`)
- Modify: `backend/paper/bets.py` (`cash_out` on each ticket)
- Modify: `backend/paper/feed.py` (rename `_straight_label` → `straight_label`, and its one call site)
- Test: `backend/tests/test_cashout_api.py`

**Interfaces:**
- Consumes:
  - Task 1's `offer`, `offer_view` and `CashOutUnavailable`;
  - `users.hold_bankroll(session, user_id)` and `users.available_of(session, user)`;
  - `feed.log_feed_event(session, loop, user_id, event_type, payload)`;
  - `feed.straight_label(session, pick) -> str`.
- Produces:
  - `cashout.cash_out(session, loop, user, kind, row, expected_offer) -> Offer`,
    which raises `CashOutUnavailable` or `OfferChanged(new_offer)`;
  - `POST /users/{user_id}/cashout` → `{bet_id, kind, offer, payout, available}`;
  - each ticket from `GET /users/{id}/bets` gains `cash_out` (see `offer_view`).

- [ ] **Step 1: Write the failing tests**

`backend/tests/test_cashout_api.py`:

```python
"""POST /users/{id}/cashout and what a cash out does to money, settlement,
the feed and My Bets (sportsbook spec 2026-10-07 §9)."""
import json

from backend.database import get_session
from backend.models import ActivityFeed, Game, PaperPick, Parlay
from backend.paper import feed
from backend.tests import cashout_helpers as h

AWAY_SPREAD = {"pick_type": "spread", "side": "AWAY"}


def _cash(c, uid, bet_id, expected, kind="straight", **kw):
    return c.post(f"/users/{uid}/cashout", json={"bet_id": bet_id, "kind": kind,
                                                  "expected_offer": expected}, **kw)


def _summary(c, uid):
    return c.get(f"/users/{uid}/bets").json()["summary"]


def _events(c, event_type):
    s = get_session(c.app.state.engine)
    try:
        return [json.loads(e.payload) for e in
                s.query(ActivityFeed).filter(ActivityFeed.event_type == event_type).order_by(ActivityFeed.id)]
    finally:
        s.close()


def _settle(c):
    s = get_session(c.app.state.engine)
    try:
        return feed.settle_and_announce(s)
    finally:
        s.close()


def test_a_cash_out_pays_the_offer_and_frees_the_stake():
    c = h.client()
    [g] = h.games(c)
    uid = h.user(c)
    start = _summary(c, uid)["available"]
    pid = h.place(c, uid, g, **AWAY_SPREAD)
    r = _cash(c, uid, pid, 90.68)
    assert r.status_code == 200, r.text
    assert r.json() == {"bet_id": pid, "kind": "straight", "offer": 90.68, "payout": -9.32,
                        "available": round(start - 9.32, 2)}
    s = _summary(c, uid)
    assert (s["available"], s["balance"], s["open_stakes"]) == (round(start - 9.32, 2), round(start - 9.32, 2), 0)
    session = get_session(c.app.state.engine)
    pick = session.get(PaperPick, pid)
    assert (pick.result, pick.payout, pick.graded_at is not None) == ("cashed_out", -9.32, True)
    session.close()


def test_an_offer_below_the_one_shown_is_refused_with_the_new_offer():
    """Review Focus 3: never pay less than the player saw without asking."""
    c = h.client()
    [g] = h.games(c)
    uid = h.user(c)
    pid = h.place(c, uid, g, **AWAY_SPREAD)
    r = _cash(c, uid, pid, 95.00)
    assert r.status_code == 409
    assert r.json()["detail"] == {"reason": "offer_changed", "offer": 90.68,
                                  "message": "The offer changed to $90.68."}
    assert c.get(f"/users/{uid}/bets").json()["tickets"][0]["result"] is None


def test_an_offer_above_the_one_shown_pays_the_current_offer():
    c = h.client()
    [g] = h.games(c)
    uid = h.user(c)
    pid = h.place(c, uid, g, **AWAY_SPREAD)
    assert _cash(c, uid, pid, 80.00).json()["offer"] == 90.68


def test_a_bet_cashes_out_once():
    """Review Focus 2: a double tap moves money once."""
    c = h.client()
    [g] = h.games(c)
    uid = h.user(c)
    pid = h.place(c, uid, g, **AWAY_SPREAD)
    assert _cash(c, uid, pid, 90.68).status_code == 200
    again = _cash(c, uid, pid, 90.68)
    assert (again.status_code, again.json()["detail"]["reason"]) == (409, "settled")
    assert len(_events(c, "cashed_out")) == 1


def test_an_offer_gone_by_confirm_time_is_refused_with_its_reason():
    """Review Focus 1: kickoff between view and confirm pays nothing."""
    c = h.client()
    [g] = h.games(c)
    uid = h.user(c)
    pid = h.place(c, uid, g, **AWAY_SPREAD)
    h.set_game(c, g, status="in_progress")
    r = _cash(c, uid, pid, 90.68)
    assert r.status_code == 409
    assert r.json()["detail"]["reason"] == "game_started"
    assert c.get(f"/users/{uid}/bets").json()["tickets"][0]["result"] is None


def test_cash_out_needs_the_players_pin():
    c = h.client()
    [g] = h.games(c)
    uid = h.user(c)
    pid = h.place(c, uid, g, **AWAY_SPREAD)
    assert _cash(c, uid, pid, 90.68, headers={"X-Player-Pin": "9999"}).status_code == 401


def test_only_your_own_whole_bet_can_be_cashed_out():
    c = h.client()
    g1, g2 = h.games(c, 2)
    uid, other = h.user(c), h.user(c, "jo")
    pid = h.place(c, uid, g1, **AWAY_SPREAD)
    plid = h.place_parlay(c, uid, [{"game_id": g1, "pick_type": "moneyline", "side": "HOME"},
                                   {"game_id": g2, "pick_type": "moneyline", "side": "HOME"}])
    assert _cash(c, other, pid, 1).status_code == 404                     # someone else's
    s = get_session(c.app.state.engine)
    leg_id = s.query(PaperPick.id).filter(PaperPick.parlay_id == plid).first()[0]
    s.close()
    assert _cash(c, uid, leg_id, 1).status_code == 404                    # a parlay's leg alone
    assert _cash(c, uid, pid, 1, kind="parlay").status_code == 404        # wrong kind


def test_a_cashed_out_parlay_stays_cashed_out_when_its_legs_grade():
    """Review Focus 4: the legs grade at stake 0; nothing else moves."""
    c = h.client()
    g1, g2 = h.games(c, 2)
    uid = h.user(c)
    plid = h.place_parlay(c, uid, [{"game_id": g1, "pick_type": "moneyline", "side": "HOME"},
                                   {"game_id": g2, "pick_type": "moneyline", "side": "HOME"}])
    assert _cash(c, uid, plid, 17.31, kind="parlay").json()["payout"] == -2.69
    balance = _summary(c, uid)["balance"]
    for g in (g1, g2):
        h.set_game(c, g, status="final", home_score=24, away_score=17)
    _settle(c)
    s = get_session(c.app.state.engine)
    parlay = s.get(Parlay, plid)
    assert (parlay.result, parlay.payout) == ("cashed_out", -2.69)
    assert {p.result for p in s.query(PaperPick).filter(PaperPick.parlay_id == plid)} == {"win"}
    s.close()
    assert _summary(c, uid)["balance"] == balance
    assert _events(c, "pick_won") == [] and _events(c, "pick_lost") == []


def test_settlement_skips_a_cashed_out_straight_bet():
    c = h.client()
    [g] = h.games(c)
    uid = h.user(c)
    pid = h.place(c, uid, g, **AWAY_SPREAD)
    _cash(c, uid, pid, 90.68)
    h.set_game(c, g, status="final", home_score=10, away_score=30)       # AWAY would have won
    _settle(c)
    s = get_session(c.app.state.engine)
    assert (s.get(PaperPick, pid).result, s.get(PaperPick, pid).payout) == ("cashed_out", -9.32)
    s.close()
    assert _events(c, "pick_won") == []


def test_a_cash_out_is_announced_once_by_team_name():
    c = h.client()
    [g] = h.games(c)
    uid = h.user(c)
    pid = h.place(c, uid, g, **AWAY_SPREAD)
    _cash(c, uid, pid, 90.68)
    [ev] = _events(c, "cashed_out")
    assert ev["message"] == "sam cashed out A0 +3.5 for $90.68"
    assert (ev["bet_key"], ev["bet_id"], ev["kind"], ev["payout"]) == (f"straight-{pid}", pid, "straight", -9.32)


def test_my_bets_carries_the_offer_on_open_tickets_only():
    c = h.client()
    g1, g2 = h.games(c, 2)
    uid = h.user(c)
    p1 = h.place(c, uid, g1, **AWAY_SPREAD)
    p2 = h.place(c, uid, g2, **AWAY_SPREAD)
    h.set_row(c, PaperPick, p2, pick_value="AWAY")                         # legacy row: no offer, no crash
    _cash(c, uid, p1, 90.68)
    r = c.get(f"/users/{uid}/bets")
    assert r.status_code == 200
    by_id = {t["id"]: t for t in r.json()["tickets"]}
    assert by_id[p1]["cash_out"] is None and by_id[p1]["result"] == "cashed_out"
    assert by_id[p2]["cash_out"]["available"] is False
    assert by_id[p2]["cash_out"]["reason"] == "not_quoted"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/Scripts/python -m pytest backend/tests/test_cashout_api.py -q -p no:warnings`
Expected: FAIL. The POSTs return 404/405 (no route) and the `cash_out` key is missing.

- [ ] **Step 3: Add `cash_out` to `backend/paper/cashout.py`**

Append:

```python
class OfferChanged(Exception):
    """The offer now is below the one the player confirmed."""

    def __init__(self, new_offer: float):
        self.offer = new_offer
        super().__init__(f"The offer changed to ${new_offer:,.2f}.")


def cash_out(session, loop, user, kind: str, row, expected_offer: float) -> Offer:
    """Settle ``row`` as cashed out at the current offer, and announce it.

    The caller holds the bankroll lock (users.hold_bankroll) before loading
    ``row``, so a second request for the same bet waits, then finds it
    settled. An offer below ``expected_offer`` is refused; one above it is
    paid -- the player never gets less than they saw without being asked.
    """
    from backend.paper.feed import log_feed_event, straight_label   # feed imports api helpers lazily too
    o = offer(session, kind, row)
    if o.amount < expected_offer:
        raise OfferChanged(o.amount)
    row.result = "cashed_out"
    row.payout = round(o.amount - row.stake, 2)
    if kind == "straight":
        row.graded_at = datetime.now(timezone.utc)
        what = straight_label(session, row)
    else:
        legs = session.query(PaperPick).filter(PaperPick.parlay_id == row.id).count()
        what = f"a {legs}-leg parlay"
    session.commit()
    log_feed_event(session, loop, user.id, "cashed_out", {
        "user_name": user.name, "message": f"{user.name} cashed out {what} for ${o.amount:,.2f}",
        "bet_key": f"{kind}-{row.id}", "bet_id": row.id, "kind": kind,
        "result": "cashed_out", "payout": row.payout, "offer": o.amount,
    })
    return o
```

- [ ] **Step 4: Make the label public in `backend/paper/feed.py`**

Rename `def _straight_label(` to `def straight_label(`, and its call in
`announce_settlements` (`_straight_label(session, pick)` →
`straight_label(session, pick)`). There are no other callers (`grep -rn
_straight_label backend` must return nothing afterwards).

- [ ] **Step 5: Add the route in `backend/api/users.py`**

Add `from backend.paper import cashout` to the imports, next to
`from backend.paper import pricing`.

After `class PlaceParlayRequest`, add:

```python
class CashOutRequest(_Strict):
    bet_id: int
    kind: Literal["straight", "parlay"]
    # The offer the player confirmed: a lower offer now is refused (409
    # offer_changed) rather than paid.
    expected_offer: float = Field(gt=0, allow_inf_nan=False)
```

After the `place_parlay` route function, add:

```python
@router.post("/{user_id}/cashout", dependencies=[Depends(require_player_pin)])
def cash_out(request: Request, user_id: int, body: CashOutRequest):
    """Cash out an open bet before kickoff (sportsbook spec §9)."""
    session = get_session(request.app.state.engine)
    try:
        user = session.get(UserProfile, user_id)
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        # The lock before the read: a double tap's second request waits,
        # then finds the bet settled.
        hold_bankroll(session, user_id)
        if body.kind == "straight":
            row = session.get(PaperPick, body.bet_id)
            if row is not None and row.parlay_id is not None:
                row = None                     # a parlay's leg is not a bet of its own
        else:
            row = session.get(Parlay, body.bet_id)
        if row is None or row.user_id != user_id:
            raise HTTPException(status_code=404, detail="Bet not found")
        try:
            o = cashout.cash_out(session, request.app.state.loop, user, body.kind, row, body.expected_offer)
        except cashout.CashOutUnavailable as e:
            raise HTTPException(status_code=409, detail={"reason": e.reason, "message": e.message}) from None
        except cashout.OfferChanged as e:
            raise HTTPException(status_code=409, detail={
                "reason": "offer_changed", "offer": e.offer, "message": str(e)}) from None
        return {"bet_id": row.id, "kind": body.kind, "offer": o.amount, "payout": row.payout,
                "available": round(available_of(session, user), 2)}
    finally:
        session.close()
```

- [ ] **Step 6: Put the offer on each ticket in `backend/paper/bets.py`**

Add `from backend.paper.cashout import offer_view` to the imports. In
`tickets`:
- set `now = datetime.now(timezone.utc)` once at the top;
- add `"cash_out": offer_view(session, "straight", pick, now),` to the
  straight ticket dict;
- add `"cash_out": offer_view(session, "parlay", parlay, now),` to the
  parlay ticket dict.

Add this sentence to the module docstring: "Each open ticket carries its
cash-out offer from `cashout.offer_view`, the function the endpoint pays
from."

- [ ] **Step 7: Run the tests to verify they pass**

Run: `.venv/Scripts/python -m pytest backend/tests/test_cashout_api.py backend/tests/test_cashout.py backend/tests/test_paper_bets.py backend/tests/test_paper_feed.py backend/tests/test_api_users.py -q -p no:warnings`
Expected: PASS.

- [ ] **Step 8: Mutation-check the guards** (delete the .pyc after each write and after the restore)

Make each mutation below and confirm the named test fails, then restore:
- Delete `if row.result is not None: raise CashOutUnavailable("settled")`
  from `offer`. Expect `test_a_bet_cashes_out_once` to fail: the second
  request re-settles the bet and its feed insert hits the bet_key index.
- `hold_bankroll` is a concurrency guard that a sequential test cannot see;
  the same is true of `place_pick`'s. Ledger that, and confirm by reading
  the code that it runs before the bet row is loaded.
- Change `if o.amount < expected_offer` to `if False`. Expect
  `test_an_offer_below_the_one_shown...` to fail.
- Delete `row = None` in the parlay-leg branch. Expect
  `test_only_your_own_whole_bet...` to fail.

- [ ] **Step 9: Commit**

```bash
git add backend/paper/cashout.py backend/api/users.py backend/paper/bets.py backend/paper/feed.py backend/tests/test_cashout_api.py
git commit -m "feat(cashout): POST /users/{id}/cashout, the offer on each open ticket, a cashed_out feed event"
```

---

### Task 3: Reader audit and the data dictionary

**Files:**
- Modify: `backend/analysis/scorecard.py` (`Bet.result` comment, `Summary.cashed_out`, `n`, `to_dict`, `summarize`)
- Modify: `backend/api/users.py` (`_board_row` and `_compute_period_stats` expose `cashed_out`)
- Modify: `backend/paper/feed.py` (`update_streaks` counts decided bets only)
- Modify: `docs/data-dictionary.md`
- Test: `backend/tests/test_scorecard.py`, `backend/tests/test_cashout_api.py`

**Interfaces:**
- Consumes: Task 2's endpoint.
- Produces:
  - `Summary.cashed_out: int = 0`;
  - `Summary.n = wins + losses + pushes + cashed_out`;
  - leaderboard rows and period stats gain `"cashed_out"`.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_scorecard.py`:

```python
def test_a_cash_out_counts_in_roi_but_not_in_the_record():
    """Sportsbook spec §9: a settled bet in ROI (profit = payout), neither a
    win nor a loss, and no part of break-even."""
    s = summarize([_b("win"), _b("loss"), _b("cashed_out", stake=1.0, profit=-0.0932)])
    assert (s.wins, s.losses, s.pushes, s.cashed_out, s.n, s.pending) == (1, 1, 0, 1, 3, 0)
    assert s.win_rate == 0.5
    assert s.staked == 3.0
    assert s.profit == pytest.approx(100 / 110 - 1 - 0.0932)
    assert s.break_even == pytest.approx(110 / 210)
    assert s.to_dict()["cashed_out"] == 1
```

Append to `backend/tests/test_cashout_api.py`:

```python
def test_the_board_and_stats_count_a_cash_out_in_roi_not_in_w_l():
    c = h.client()
    [g] = h.games(c)
    uid = h.user(c)
    pid = h.place(c, uid, g, **AWAY_SPREAD)
    _cash(c, uid, pid, 90.68)
    [row] = [r for r in c.get("/users/leaderboard").json() if r["id"] == uid]
    assert (row["wins"], row["losses"], row["cashed_out"], row["n"]) == (0, 0, 1, 1)
    assert (row["profit"], row["roi"]) == (-9.32, -0.0932)
    stats = c.get(f"/users/{uid}/stats").json()["all_time"]
    assert (stats["wins"], stats["losses"], stats["cashed_out"], stats["total"], stats["profit"]) == (0, 0, 1, 1, -9.32)


def test_a_cash_out_neither_extends_nor_breaks_a_streak():
    """Review Focus 4: streaks follow decided bets; a cash out is not one."""
    from datetime import datetime, timedelta
    from backend.models import UserProfile
    c = h.client()
    games = h.games(c, 4)
    uid = h.user(c)
    ids = [h.place(c, uid, g, **AWAY_SPREAD) for g in games]
    t0 = datetime(2026, 10, 1)
    for i, (pid, result) in enumerate(zip(ids, ["win", "win", "win", "cashed_out"])):
        h.set_row(c, PaperPick, pid, result=result, payout=1.0, created_at=t0 + timedelta(hours=i))
    s = get_session(c.app.state.engine)
    feed.update_streaks(s, None, uid)
    s.commit()
    u = s.get(UserProfile, uid)
    assert (u.current_streak, u.streak_type) == (3, "win")
    s.close()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/Scripts/python -m pytest backend/tests/test_scorecard.py backend/tests/test_cashout_api.py -q -p no:warnings`
Expected: FAIL. `test_a_cash_out_counts_in_roi...` fails with
`AttributeError: 'Summary' object has no attribute 'cashed_out'`, the board
test with a `KeyError: 'cashed_out'`, and the streak test with `(0, 'none')`
or `(1, 'none')` instead of `(3, 'win')`.

- [ ] **Step 3: Change the scorecard (`backend/analysis/scorecard.py`)**

- `Bet.result` comment: `# "win" | "loss" | "push" | "cashed_out" | None while pending`.
- Module docstring definitions: add the line
  `cash out   a settled bet in ROI (profit = payout); not a win, loss or push,
  and not in break-even (sportsbook spec 2026-10-07 §9)`.
- `Summary`: add the last field `cashed_out: int = 0`. Change `n` to
  `return self.wins + self.losses + self.pushes + self.cashed_out` and its
  docstring to `"""Settled bets, cash outs included."""`.
- `to_dict`: add `"cashed_out": self.cashed_out,` after `"pushes"`.
- `summarize`: add
  `cashed_out = sum(1 for b in settled if b.result == "cashed_out")` after
  `pushes`, and pass `cashed_out=cashed_out` to `Summary(...)`.

- [ ] **Step 4: Expose it (`backend/api/users.py`)**

- `_board_row`: add `"cashed_out": s.cashed_out,` after `"pushes": s.pushes,`.
- `_compute_period_stats`: add `"cashed_out": s.cashed_out,` after
  `"pushes": s.pushes,`.

- [ ] **Step 5: Streaks follow decided bets (`backend/paper/feed.py`)**

In `update_streaks`, replace the block from `current = picks[0].result`
down to the end of the `for p in picks:` loop with:

```python
    # Pushes and cash outs are neither a win nor a loss: a streak passes
    # over them (a cash out is a settled bet, not a decided one).
    decided = [p.result for p in picks if p.result in ("win", "loss")]
    current = decided[0] if decided else "none"
    streak = 0
    for result in decided:
        if result != current:
            break
        streak += 1
```

Keep the `if not picks: return` guard above it, and everything below it
unchanged (`user = session.get(...)`, `new_type`, `grew`, and the event).

- [ ] **Step 6: Run the tests to verify they pass**

Run: `.venv/Scripts/python -m pytest backend/tests/test_scorecard.py backend/tests/test_cashout_api.py backend/tests/test_paper_feed.py backend/tests/test_api_users.py backend/tests/test_digest*.py -q -p no:warnings`
Expected: PASS. The digest and emailed-record tests must be unchanged,
because the Model's bets are never cashed out.

- [ ] **Step 7: Audit every other reader of `result`, and ledger each**

Run: `grep -rn "result" backend/scripts/weekly_review.py backend/scripts/export_picks.py backend/analysis/paper_bets.py backend/pipeline/paper_settlement.py`

Write one ledger row per reader (`Task 3: audit <file> — <what it does with
cashed_out> — <evidence>`). The expected outcomes:
- `weekly_review.head_to_head` counts only `result in ("win", "loss")`, so a
  cash out is left out.
- `export_picks` excludes `paper_picks` and `parlays` entirely.
- `paper_bets.player_bets` maps it to `Bet(result="cashed_out", profit=payout)`.
- `grade_paper_picks`, `_push_ungradeable` and `settle_parlays_list` select
  `result IS NULL`, so they skip it.
- `balance_of` sums `payout` and `open_stakes` filters `result IS NULL`, so
  both are correct unchanged. Task 2's first test verifies this.

Any reader that does something else is a finding: fix it with a test before
continuing.

- [ ] **Step 8: Add the data-dictionary entry**

In `docs/data-dictionary.md`, add a new section immediately before
`## See also`:

```markdown
## Paper bets (`paper_picks`, `parlays`) -- not exported

The export leaves these tables out (see the top of this file), but the
owner's analysis can read them from the database directly.

- **From 2026-10-09 (sportsbook phase 6), `result` can be `cashed_out`.**
  A player sold an open bet back before kickoff for
  `stake x placed decimal price x no-vig chance now x 0.95`, rounded down
  to the cent. `payout` is that offer minus the stake. It is usually
  negative even on a bet that was going well, because the 5% margin and
  the fair price leave less than the stake plus the winnings.
  - On a straight bet `graded_at` is the cash-out time.
  - A parlay has no `graded_at` column; its cash-out time is the
    `cashed_out` row in `activity_feed` (`$.bet_key = "parlay-<id>"`).
  - A cashed-out parlay's legs are still graded later, at stake 0, so a leg
    shows `win`/`loss` under a `cashed_out` parlay. That is expected and
    moves no money.
  - In every figure, a cash out is a settled bet: its profit is in ROI and
    its stake in money staked. It is neither a win nor a loss, and it is
    not in break-even.
  - Before this date no row has the value.
```

If the branch merges on a later date than 2026-10-09, change the date in
the merge commit. The ledger must record the date written.

- [ ] **Step 9: Commit**

```bash
git add backend/analysis/scorecard.py backend/api/users.py backend/paper/feed.py backend/tests/test_scorecard.py backend/tests/test_cashout_api.py docs/data-dictionary.md
git commit -m "feat(cashout): a cash out counts in ROI, not W-L; streaks pass over it; data-dictionary entry"
```

---

### Task 4: Cash out on My Bets

**Files:**
- Modify: `frontend/src/types.ts`, `frontend/src/api/client.ts`
- Create: `frontend/src/lib/cashout.ts`, `frontend/src/lib/cashout.test.ts`
- Create: `frontend/src/components/CashOutButton.tsx`, `frontend/src/components/CashOutButton.test.tsx`
- Modify: `frontend/src/components/TicketCard.tsx`, `frontend/src/lib/bets.ts`, `frontend/src/lib/bets.test.ts`
- Modify: `frontend/src/pages/MyBets.tsx`, `frontend/src/pages/MyBets.test.tsx`
- Modify: `frontend/src/components/FeedList.tsx`, `frontend/src/hooks/useWebSocket.ts`
- Modify: `frontend/src/sportsbook.css`, `frontend/src/pages/FAQ.tsx`

**Interfaces:**
- Consumes:
  - Task 2's `POST /users/{id}/cashout` → `{bet_id, kind, offer, payout, available}`;
  - its 409 details `{reason, message, offer?}`;
  - `Ticket.cash_out`.
- Produces:
  - `CashOutView`;
  - `api.users.cashOut(userId, {bet_id, kind, expected_offer}, pin)`;
  - `cashOutRefusal(e): {kind: 'changed', offer, message} | {kind: 'pin', message} | {kind: 'error', message}`;
  - `<CashOutButton t userId />`;
  - `<TicketCard t userId? />`.

- [ ] **Step 1: Types and client**

In `frontend/src/types.ts`, add the following above `export interface Ticket`:

```ts
/** A ticket's cash-out field: null once settled (sportsbook spec §9). */
export interface CashOutView { available: boolean; offer?: number; reason?: string; message?: string }
```

Then add the field `cash_out?: CashOutView | null` to `Ticket`, after
`legs: TicketLeg[];`.

In `frontend/src/api/client.ts`, add the following inside `users`, after
`placeParlay`:

```ts
    cashOut: (userId: number, data: { bet_id: number; kind: 'straight' | 'parlay'; expected_offer: number },
      pin: string) => post<{ bet_id: number; kind: string; offer: number; payout: number; available: number }>(
      `/users/${userId}/cashout`, data, { 'X-Player-Pin': pin }),
```

- [ ] **Step 2: Write the failing tests**

`frontend/src/lib/cashout.test.ts`:

```ts
import { describe, it, expect } from 'vitest'
import { cashOutRefusal } from './cashout'
import { ApiError } from '../api/client'

describe('cashOutRefusal', () => {
  it('reads a changed offer', () => {
    const e = new ApiError(409, { detail: { reason: 'offer_changed', offer: 85, message: 'The offer changed to $85.00.' } })
    expect(cashOutRefusal(e)).toEqual({ kind: 'changed', offer: 85, message: 'The offer changed to $85.00.' })
  })
  it('reads a PIN failure', () => {
    expect(cashOutRefusal(new ApiError(401, { detail: 'Wrong PIN' }))).toEqual({ kind: 'pin', message: 'Wrong PIN' })
  })
  it("reads any other refusal by the server's message", () => {
    const e = new ApiError(409, { detail: { reason: 'game_started', message: 'A game in this bet has started — cash out is pre-game only.' } })
    expect(cashOutRefusal(e)).toEqual({ kind: 'error', message: 'A game in this bet has started — cash out is pre-game only.' })
  })
  it('reads a network failure', () => {
    expect(cashOutRefusal(new TypeError('fetch failed'))).toEqual({ kind: 'error', message: "Couldn't reach the server — try again." })
  })
})
```

`frontend/src/components/CashOutButton.test.tsx`:

```tsx
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import CashOutButton from './CashOutButton'
import { api, ApiError } from '../api/client'
import { getPin, setPin } from '../lib/secrets'
import type { Ticket } from '../types'

vi.mock('../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/client')>()
  return { ...actual, api: { ...actual.api, users: { ...actual.api.users, cashOut: vi.fn() } } }
})

const ticket = (over: Partial<Ticket> = {}): Ticket => ({
  kind: 'straight', id: 6, stake: 100, odds: -110, to_win: 90.91, result: null, payout: null,
  created_at: '2026-10-09T12:00:00+00:00', sgp: false, legs: [],
  cash_out: { available: true, offer: 90.68 }, ...over,
})

function renderButton(t: Ticket) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const spy = vi.spyOn(client, 'invalidateQueries')
  render(<QueryClientProvider client={client}><CashOutButton t={t} userId={1} /></QueryClientProvider>)
  return spy
}

beforeEach(() => {
  vi.clearAllMocks()
  window.localStorage.clear()
  window.sessionStorage.clear()
  setPin(1, '1234')
})

describe('CashOutButton', () => {
  it('shows the offer, asks to confirm, then cashes out at it', async () => {
    vi.mocked(api.users.cashOut).mockResolvedValue({ bet_id: 6, kind: 'straight', offer: 90.68, payout: -9.32, available: 9990.68 })
    const invalidate = renderButton(ticket())
    fireEvent.click(screen.getByRole('button', { name: 'Cash out $90.68' }))
    expect(api.users.cashOut).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: 'Confirm cash out' }))
    await waitFor(() => expect(api.users.cashOut).toHaveBeenCalledWith(1,
      { bet_id: 6, kind: 'straight', expected_offer: 90.68 }, '1234'))
    await waitFor(() => expect(invalidate).toHaveBeenCalledWith({ queryKey: ['users'] }))
  })
  it('greys the button with the reason when there is no offer', () => {
    renderButton(ticket({ cash_out: { available: false, reason: 'line_moved',
      message: "The line has moved since you bet, so there's no cash out offer." } }))
    expect(screen.getByRole('button', { name: 'Cash out' })).toBeDisabled()
    expect(screen.getByText("The line has moved since you bet, so there's no cash out offer.")).toBeInTheDocument()
  })
  it('asks again at the new offer when it changed (Review Focus 3)', async () => {
    vi.mocked(api.users.cashOut)
      .mockRejectedValueOnce(new ApiError(409, { detail: { reason: 'offer_changed', offer: 85, message: 'The offer changed to $85.00.' } }))
      .mockResolvedValueOnce({ bet_id: 6, kind: 'straight', offer: 85, payout: -15, available: 9985 })
    renderButton(ticket())
    fireEvent.click(screen.getByRole('button', { name: 'Cash out $90.68' }))
    fireEvent.click(screen.getByRole('button', { name: 'Confirm cash out' }))
    expect(await screen.findByText('The offer changed to $85.00.')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Confirm cash out' }))
    await waitFor(() => expect(api.users.cashOut).toHaveBeenLastCalledWith(1,
      { bet_id: 6, kind: 'straight', expected_offer: 85 }, '1234'))
  })
  it('forgets a wrong PIN and asks for it', async () => {
    vi.mocked(api.users.cashOut).mockRejectedValueOnce(new ApiError(401, { detail: 'Wrong PIN' }))
    renderButton(ticket())
    fireEvent.click(screen.getByRole('button', { name: 'Cash out $90.68' }))
    fireEvent.click(screen.getByRole('button', { name: 'Confirm cash out' }))
    expect(await screen.findByLabelText('PIN')).toBeInTheDocument()
    expect(getPin(1)).toBeNull()
  })
  it('needs a PIN before it can confirm when none is saved', () => {
    setPin(1, null)
    renderButton(ticket())
    fireEvent.click(screen.getByRole('button', { name: 'Cash out $90.68' }))
    expect(screen.getByRole('button', { name: 'Confirm cash out' })).toBeDisabled()
    fireEvent.change(screen.getByLabelText('PIN'), { target: { value: '1234' } })
    expect(screen.getByRole('button', { name: 'Confirm cash out' })).toBeEnabled()
  })
  it('Keep bet backs out without cashing out', () => {
    renderButton(ticket())
    fireEvent.click(screen.getByRole('button', { name: 'Cash out $90.68' }))
    fireEvent.click(screen.getByRole('button', { name: 'Keep bet' }))
    expect(screen.getByRole('button', { name: 'Cash out $90.68' })).toBeInTheDocument()
    expect(api.users.cashOut).not.toHaveBeenCalled()
  })
})
```

Append to `frontend/src/pages/MyBets.test.tsx`, inside `describe('MyBets'`:

```tsx
  it('stamps a cashed-out bet and keeps it out of Won and Lost', async () => {
    vi.mocked(api.users.bets).mockResolvedValue({
      summary: { available: 9990.68, balance: 9990.68, open_stakes: 0, today_pl: -9.32 },
      tickets: [t({ id: 8, result: 'cashed_out', payout: -9.32, cash_out: null })],
    })
    renderPage()
    fireEvent.click(await screen.findByRole('tab', { name: 'Settled' }))
    const card = await screen.findByRole('article', { name: 'Bet #P-8' })
    expect(card).toHaveTextContent('CASHED OUT')
    expect(card).toHaveTextContent('Cashed out −$9.32')
    fireEvent.click(screen.getByRole('button', { name: 'Won' }))
    expect(screen.queryByRole('article', { name: 'Bet #P-8' })).toBeNull()
  })
  it('offers a cash out on an open ticket', async () => {
    vi.mocked(api.users.bets).mockResolvedValue({
      summary: { available: 9900, balance: 10000, open_stakes: 100, today_pl: 0 },
      tickets: [t({ id: 9, cash_out: { available: true, offer: 90.68 } })],
    })
    renderPage()
    expect(await screen.findByRole('button', { name: 'Cash out $90.68' })).toBeInTheDocument()
  })
```

Append to `frontend/src/lib/bets.test.ts`, inside the describe that tests
`legStatus`:

```ts
  it('shows a cashed-out straight leg as neither won nor lost', () => {
    expect(legStatus(leg({ result: 'cashed_out' }))).toBe('push')
  })
```

`leg` is the file's existing `TicketLeg` factory.

- [ ] **Step 3: Run the tests to verify they fail**

Run: `cd frontend && npx vitest run src/lib/cashout.test.ts src/components/CashOutButton.test.tsx src/pages/MyBets.test.tsx src/lib/bets.test.ts`
Expected: FAIL. `./cashout` and `./CashOutButton` can't be resolved, and
the MyBets tests can't find "CASHED OUT" or the Cash out button.

- [ ] **Step 4: Write `frontend/src/lib/cashout.ts`**

```ts
import { ApiError } from '../api/client'

export type CashOutRefusal =
  | { kind: 'changed'; offer: number; message: string }
  | { kind: 'pin'; message: string }
  | { kind: 'error'; message: string }

/** What a refused cash out means for the button (sportsbook spec §9, §11). */
export function cashOutRefusal(e: unknown): CashOutRefusal {
  if (e instanceof ApiError) {
    const d = (e.body as { detail?: unknown } | null)?.detail as { reason?: unknown; offer?: unknown } | undefined
    if (e.status === 409 && d && d.reason === 'offer_changed' && typeof d.offer === 'number') {
      return { kind: 'changed', offer: d.offer, message: e.message }
    }
    if (e.status === 401) return { kind: 'pin', message: e.message }
    return { kind: 'error', message: e.message }
  }
  return { kind: 'error', message: "Couldn't reach the server — try again." }
}
```

- [ ] **Step 5: Write `frontend/src/components/CashOutButton.tsx`**

```tsx
import { useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { api } from '../api/client'
import { formatMoney } from '../lib/board'
import { cashOutRefusal } from '../lib/cashout'
import { getPin, setPin } from '../lib/secrets'
import type { Ticket } from '../types'

/** Cash out an open ticket (sportsbook spec §9): the offer, a confirm step,
 *  and the server's answer. Never pays less than the figure confirmed: a
 *  lower offer comes back as offer_changed and is asked again. */
export default function CashOutButton({ t, userId }: { t: Ticket; userId: number }) {
  const queryClient = useQueryClient()
  const [confirming, setConfirming] = useState(false)
  const [working, setWorking] = useState(false)
  const [changedTo, setChangedTo] = useState<number | null>(null)
  const [message, setMessage] = useState<string | null>(null)
  const [pinInput, setPinInput] = useState('')
  const view = t.cash_out
  if (!view) return null
  if (!view.available || view.offer === undefined) {
    return (
      <div className="sb-cashout">
        <button type="button" className="sb-cashout-btn" disabled>Cash out</button>
        <small className="sb-cashout-why">{view.message}</small>
      </div>
    )
  }
  const offer = changedTo ?? view.offer
  const stored = getPin(userId)
  const pin = stored ?? pinInput
  const pinOk = /^\d{4,6}$/.test(pin)

  const confirm = async () => {
    setWorking(true)
    setMessage(null)
    try {
      await api.users.cashOut(userId, { bet_id: t.id, kind: t.kind, expected_offer: offer }, pin)
      if (!stored) setPin(userId, pin)
      setConfirming(false)
      await queryClient.invalidateQueries({ queryKey: ['users'] })
    } catch (e) {
      const r = cashOutRefusal(e)
      setMessage(r.message)
      if (r.kind === 'changed') setChangedTo(r.offer)
      if (r.kind === 'pin') { setPin(userId, null); setPinInput('') }
    } finally {
      setWorking(false)
    }
  }

  if (!confirming) {
    return (
      <div className="sb-cashout">
        <button type="button" className="sb-cashout-btn" onClick={() => setConfirming(true)}>
          Cash out {formatMoney(offer)}
        </button>
      </div>
    )
  }
  return (
    <div className="sb-cashout sb-cashout-confirm" role="group" aria-label="Confirm cash out">
      <p>Cash out for <strong>{formatMoney(offer)}</strong>? Stake {formatMoney(t.stake)}.</p>
      {message && <p className="sb-slip-error" role="alert">{message}</p>}
      {!getPin(userId) && (
        <label className="sb-cashout-pin">PIN
          <input type="password" inputMode="numeric" autoComplete="off" value={pinInput}
            onChange={e => setPinInput(e.target.value)} />
        </label>
      )}
      <div className="sb-cashout-actions">
        <button type="button" className="sb-cashout-btn" disabled={working || !pinOk} onClick={confirm}
          aria-label="Confirm cash out">{working ? 'Cashing out…' : `Confirm ${formatMoney(offer)}`}</button>
        <button type="button" className="sb-cashout-keep" disabled={working}
          onClick={() => { setConfirming(false); setMessage(null) }}>Keep bet</button>
      </div>
    </div>
  )
}
```

`<label>PIN<input/></label>` gives the input the accessible name "PIN".
If `getByLabelText('PIN')` fails because the label text includes
whitespace, put `aria-label="PIN"` on the input.

- [ ] **Step 6: Wire it into the ticket and the page**

`frontend/src/lib/bets.ts`: in `legStatus`, map `'cashed_out'` to
`'push'`:

```ts
  return l.result === 'win' ? 'won' : l.result === 'loss' ? 'lost'
    : l.result === 'push' || l.result === 'cashed_out' ? 'push' : 'pending'
```

`frontend/src/components/TicketCard.tsx`:
- Change the signature to
  `export default function TicketCard({ t, userId }: { t: Ticket; userId?: number })`.
- Add `cashed_out: 'Cashed out'` to `RESULT`.
- In the header, after the LIVE pill, add
  `{t.result === 'cashed_out' && <span className="sb-cashed">CASHED OUT</span>}`.
- After `</footer>`, add
  `{t.result === null && userId !== undefined && <CashOutButton t={t} userId={userId} />}`
  and import `CashOutButton from './CashOutButton'`.

`frontend/src/pages/MyBets.tsx`: change the map to
`{shown.map(t => <TicketCard key={ticketKey(t)} t={t} userId={me.id} />)}`.

`frontend/src/components/FeedList.tsx`: add `cashed_out: 'push'` to `DOT`.

`frontend/src/hooks/useWebSocket.ts`: add `'cashed_out'` to the event-type
list in `onmessage`.

`frontend/src/sportsbook.css`, appended:

```css
/* Cash out (sportsbook spec §9) */
.sb-cashout { padding: 0.5rem 0.8rem 0.7rem; display: grid; gap: 0.4rem; }
.sb-cashout-btn {
  width: 100%; padding: 0.55rem; border-radius: 0.5rem; border: 1px solid var(--green);
  background: transparent; color: var(--green); font-weight: 700; font-variant-numeric: tabular-nums;
}
.sb-cashout-btn:disabled { border-color: var(--border-subtle); color: var(--text-muted); }
.sb-cashout-why { color: var(--text-muted); font-size: 0.75rem; }
.sb-cashout-confirm p { margin: 0; font-size: 0.85rem; }
.sb-cashout-actions { display: grid; grid-template-columns: 2fr 1fr; gap: 0.5rem; }
.sb-cashout-keep {
  padding: 0.55rem; border-radius: 0.5rem; border: 1px solid var(--border-subtle);
  background: transparent; color: var(--text-primary);
}
.sb-cashout-pin { display: grid; gap: 0.2rem; font-size: 0.75rem; color: var(--text-muted); }
.sb-cashed {
  padding: 0.05rem 0.4rem; border-radius: 0.25rem; background: var(--yellow); color: #111;
  font-size: 0.65rem; font-weight: 800; letter-spacing: 0.04em;
}
```

`--text-primary`, `--text-muted`, `--green`, `--yellow` and `--border-subtle`
are the theme tokens defined on `:root` in `frontend/src/index.css`.

`frontend/src/pages/FAQ.tsx`: append this sentence to the end of the
paper-trading answer that begins 'Bets close when the game starts.':
` Before kickoff you can cash out an open bet from My Bets for its current
value less a 5% margin; a cash out counts in your ROI but not your
won-lost record.`

- [ ] **Step 7: Run the tests and the type check**

Run: `cd frontend && npx vitest run && npx tsc -b`
Expected: all test files pass (225 existing + the new ones), and tsc prints
nothing.

- [ ] **Step 8: Mutation-check the guards**

Make each mutation, confirm the named test fails, then restore:
- In `CashOutButton`, replace `expected_offer: offer` with
  `expected_offer: view.offer`. Expect `asks again at the new offer...` to
  fail.
- Delete `setPin(userId, null);` from the pin branch. Expect
  `forgets a wrong PIN...` to fail.
- Delete `cashed_out: 'Cashed out'` from `RESULT`. Expect
  `stamps a cashed-out bet...` to fail.

- [ ] **Step 9: Commit**

```bash
git add frontend/src/types.ts frontend/src/api/client.ts frontend/src/lib/cashout.ts frontend/src/lib/cashout.test.ts frontend/src/components/CashOutButton.tsx frontend/src/components/CashOutButton.test.tsx frontend/src/components/TicketCard.tsx frontend/src/lib/bets.ts frontend/src/lib/bets.test.ts frontend/src/pages/MyBets.tsx frontend/src/pages/MyBets.test.tsx frontend/src/components/FeedList.tsx frontend/src/hooks/useWebSocket.ts frontend/src/sportsbook.css frontend/src/pages/FAQ.tsx
git commit -m "feat(cashout): Cash out on open tickets -- confirm, PIN, changed offers; CASHED OUT stamp"
```

---

### Task 5: Full suites and the visual check

**Files:** none changed unless the check finds something. A fix gets its
own failing test first and its own commit.

- [ ] **Step 1: Full suites**

Run: `.venv/Scripts/python -m pytest -q -p no:warnings`
Expected: all pass. The Phase 5 head (4216aa9) passed 2359; this should be
2359 plus the new tests.

Run: `cd frontend && npx vitest run && npm run build`
Expected: all pass; the build prints `built in`.

- [ ] **Step 2: Visual check on :8001 with a db snapshot**

Do this from the worktree, never against `sports_picks.db` itself.

1. Take a snapshot with the sqlite backup API into the scratchpad.
2. In the snapshot only, give TesterA one open straight bet and one 2-leg
   parlay on games that haven't started and have fresh odds.
3. Start uvicorn on :8001:
   `DATABASE_PATH=<snapshot> ENABLE_SCHEDULER=0 python -m uvicorn backend.api.main:app --port 8001`.
4. In claude-in-chrome, at 390px and at desktop width, as TesterA:
   1. My Bets shows **Cash out $X** on both tickets.
   2. Confirm the straight bet's cash out. The ticket moves to Settled with
      **CASHED OUT**, Available rises by the offer, and the Lobby ticker
      shows "TesterA cashed out … for $X".
   3. In the snapshot, set one parlay leg's game to `in_progress`. The
      parlay's button greys, with the game-started reason.
   4. No horizontal scroll at 390px.
5. Stop :8001 and close the tab.
6. Ledger each observation.

- [ ] **Step 3: Ledger and hand off**

Run `task-done` with the full backend command. Then generate the review
package against the Phase 6 base (4216aa9), dispatch the final reviewer and
do the fix pass, as in Phases 1–5.

**Merge notes for the owner:**
- Phase 6 has no migration, but it changes the API and the scheduler's
  grading path (streaks), so merging needs the usual scheduler restart.
- Phase 5 must merge first. Its `uq_activity_feed_bet_key` migration is
  what makes one cash-out event per bet hold across processes.

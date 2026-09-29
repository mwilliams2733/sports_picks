# Plan 027: Server-priced paper bets and an Admin "Set PIN" button

> **Executor instructions**: Follow this plan task by task, in order. Run every
> verification command and confirm the expected result before moving on. If
> anything in "STOP conditions" occurs, stop and report — do not improvise.
> When done, update this plan's row in `plans/README.md` — unless a reviewer
> dispatched you and told you they maintain the index.
>
> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.
>
> **Drift check (run first)**:
> `git diff --stat 6230d89..HEAD -- backend/api backend/analysis/strategy.py backend/analysis/prop_markets.py backend/models.py backend/time_utils.py backend/pipeline/grader.py backend/tests frontend/src frontend/vite.config.ts`
> Expected: empty (this plan file and `plans/README.md` may differ). On a
> mismatch, treat it as a STOP condition.

**Goal:** Every paper bet is priced by the server at the consensus price and
line, the price a player sees comes from the same function, stale, unquoted
and ungradeable bets are refused with a readable reason, and the owner can
set any player's PIN from the Admin page.

**Architecture:** One module, `backend/paper/pricing.py`, turns a bet
description (`GameBet` or `PropBet`) into a `Quote` or raises `PricingError`.
The bet routes, the new `GET /paper/quotes` and `GET /paper/prop-quotes`
endpoints all call it. Bet bodies no longer carry `odds` or `pick_value`;
pydantic `extra="forbid"` turns the old shape into a 422. The frontend picks
a side from the quote list instead of typing a label and a price.

**Tech Stack:** Python 3.14 locally (CI: 3.12 and 3.14), FastAPI, pydantic
v2, SQLAlchemy/SQLite, pytest; React 19 + Vite + TypeScript,
@tanstack/react-query, vitest + Testing Library.

**Spec:** `docs/superpowers/specs/2026-09-29-server-priced-paper-bets-design.md`
(commit `6230d89`, approved 2026-09-29). Read it before starting.

## Status

- **Priority**: P1 — no link is shared with friends until this ships.
- **Effort**: L (7 tasks; Task 7 is the controller's visual check)
- **Risk**: HIGH for Tasks 1 and 3 — they decide what every bet is charged.
  MED for Tasks 2, 4, 5, 6.
- **Depends on**: 026 (merged at `13c0ad5`)
- **Planned at**: commit `6230d89`, 2026-09-29
- **Executor model**: `sonnet` for every task (Agent tool `model` value).
  **Review of Tasks 1 and 3 uses the most capable model** — they carry the
  pricing and anti-tamper claims.

## Global Constraints

- No new Python or npm dependencies. No schema change.
- Consensus is `backend.analysis.strategy.average_odds` /
  `consensus_moneyline` — never a second averager.
- `MAX_QUOTE_AGE = timedelta(hours=6)`, judged per market on the newest
  `Odds.timestamp` / `PlayerProp.fetched_at` behind the price.
- Stored datetimes are naive UTC (SQLite). Treat a naive value as UTC.
- **No −110 fallback anywhere in paper pricing.** A missing price is
  `not_quoted`.
- Refusal status codes: `game_started` → 400; `not_quoted`, `stale`,
  `not_gradeable` → 409 with the message as `detail`; malformed body → 422.
- Stale message, verbatim: `The price is stale — ask Marcus to refresh.`
- `pick_value` formats are unchanged (the grader parses them): `HOME ML`,
  `AWAY +3.5`, `Over 44.5`, `Jalen Hurts Over 225.5 Pass Yards`.
- Never echo, log or commit the owner key or a PIN.
- **Never place a bet against the live `sports_picks.db`** during
  verification. Tests use `:memory:` or `tmp_path`.
- New styles go in `frontend/src/index.css` only (`App.css` is not
  imported) and get a line in `frontend/src/styles.test.ts`.
- Every guard is mutation-checked: break it, confirm a named test fails,
  restore. Record each check in the commit message.
- Backend suite: `.venv/Scripts/python.exe -m pytest backend/tests -q -p no:warnings`
  (~2.5 min), and again with `DB_PATH=/nonexistent/no.db` prefixed.
  Frontend: `cd frontend && npx tsc -b --noEmit && npx vitest run && npx eslint .`
- Commits end with the attribution line from the session instructions.

## Review Focus

Inputs the spec is silent on that are most likely to bite a real user. Each
has a test in the owning task.

1. **Anytime-TD props** — stored with `outcome="Yes"` and `line=NULL`
   (11,318 live rows). The grader only parses `Over|Under <number>`, so such
   a bet would sit pending forever. Expect them absent from
   `/paper/prop-quotes` and refused if requested. Test: Task 1.
2. **A book row with an unusable price (0, or inside −100..+100)** — expect
   it ignored by the consensus and by the freshness clock, not a crash and
   not a price. Test: Task 1.
3. **Books quoting different spread lines (−3 and −4)** — expect the bet at
   the consensus line −3.5 and the consensus of the books' prices, labelled
   `HOME -3.5`, and it grades a 27–24 home win as a loss (margin −0.5).
   Tests: Task 1 (price and label), Task 3 (graded through the API).
4. **Vite's dev proxy key `/paper` would swallow the `/paper-trading` page**
   — expect the proxy to match `^/paper/` only. Test: Task 4 (a vitest
   reading `vite.config.ts`).
5. **The price moves between display and placement** — expect the new price
   charged and the toast to say so ("Placed at −115 — was −110 when you
   looked"). Tests: Task 3 (API), Task 4 (`priceMoveNote`).

## STOP conditions

- The drift check is non-empty.
- Any task's "verify it fails" step passes before the implementation exists.
- A mutation check does not fail the named test.
- A test outside this plan's files fails and the cause is not the bet body
  change (Task 3 lists every file that sends the old shape).
- Anything would require printing, logging or committing the owner key or a PIN.
- Any step would write to the live `sports_picks.db`.

## File map

| File | Responsibility | Task |
|---|---|---|
| `backend/paper/__init__.py` (new) | package marker | 1 |
| `backend/paper/pricing.py` (new) | `GameBet`, `PropBet`, `Quote`, `PricingError`, `open_for_betting`, `price`, `combine`, `game_quotes`, `prop_quotes` | 1 |
| `backend/tests/test_paper_pricing.py` (new) | pricing unit tests | 1 |
| `backend/api/paper.py` (new) | `GET /paper/quotes`, `GET /paper/prop-quotes` | 2 |
| `backend/api/main.py` | mount `/paper` | 2 |
| `backend/api/games.py` | `/games/today` shows the consensus | 2 |
| `backend/api/users.py` | `has_pin` (2); new bodies, server pricing, `open_for_betting` import (3) | 2, 3 |
| `backend/tests/test_paper_quotes_api.py` (new) | quote endpoints, `has_pin` | 2 |
| `backend/tests/test_games_api.py` | consensus test | 2 |
| `backend/tests/pricing_helpers.py` (new) | `seed_fresh_odds`, `seed_fresh_prop` | 3 |
| `backend/tests/test_priced_bets.py` (new) | anti-tamper, price moved, parlay, round trip | 3 |
| `backend/tests/test_api_users.py`, `test_paper_bet_odds_guard.py`, `test_player_pins.py`, `test_websocket.py` | convert to the new body | 3 |
| `frontend/vite.config.ts` | proxy `^/paper/` | 4 |
| `frontend/src/types.ts`, `api/client.ts` | quote and bet types; `api.paper` | 4 |
| `frontend/src/lib/quotes.ts` (new) + test | `formatOdds`, `legFromPick`, `findQuote`, `priceMoveNote`, `parlayEstimate`, `ageLabel` | 4 |
| `frontend/src/hooks/useQuotes.ts` (new) | `useGameQuotes`, `usePropQuotes` | 4 |
| `frontend/src/components/QuotePicker.tsx`, `PropQuotePicker.tsx` (new) + tests | side buttons | 4 |
| `frontend/src/pages/PaperTrading.tsx`, `components/BetModal.tsx` (+ test) | use quotes; no Odds/Pick inputs | 5 |
| `frontend/src/components/SetPinControl.tsx` (new) + test, `pages/Admin.tsx` (+ new test) | Set PIN, No PIN badge | 6 |
| `frontend/src/index.css`, `styles.test.ts` | new classes | 4, 6 |

---

### Task 1: The pricing module

**Files:**
- Create: `backend/paper/__init__.py`, `backend/paper/pricing.py`
- Test: `backend/tests/test_paper_pricing.py`

**Interfaces:**
- Consumes: `average_odds`, `consensus_moneyline` (`backend/analysis/strategy.py`);
  `MARKET_STAT_MAP`, `market_label` (`backend/analysis/prop_markets.py`);
  `ET`, `game_start_utc` (`backend/time_utils.py`); `Odds`, `PlayerProp`, `Game`.
- Produces (used by Tasks 2 and 3):
  - `MAX_QUOTE_AGE: timedelta`
  - `class PricingError(Exception)` with `.reason: str`, `.message: str`, `.status: int`
  - `@dataclass(frozen=True) GameBet(game_id: int, pick_type: str, side: str)`
  - `@dataclass(frozen=True) PropBet(game_id: int, prop_player: str, prop_market: str, outcome: str, line: float)`
  - `@dataclass(frozen=True) Quote(pick_type, pick_value, odds: int, line: float | None, quoted_at: datetime, prop_player: str | None = None, prop_market: str | None = None)` with `.as_dict() -> dict`
  - `open_for_betting(game, now: datetime | None = None) -> bool`
  - `price(session, game, bet: GameBet | PropBet, now: datetime | None = None) -> Quote` (raises `PricingError`)
  - `combine(odds: list[int]) -> tuple[int, float]` — (American, decimal)
  - `game_quotes(session, game, now=None) -> list[dict]`
  - `prop_quotes(session, game, now=None) -> list[dict]`

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_paper_pricing.py`. Expected prices are computed
by hand in the comments, never by calling the function under test.

```python
"""The pricing module is the only source of a paper bet's price (plan 027).

Expected prices are worked by hand from implied probabilities:
  -110 -> 110/210 = 0.523810   -130 -> 130/230 = 0.565217
  -120 -> 120/220 = 0.545455   +100 -> 100/200 = 0.500000
  +110 -> 100/210 = 0.476190
and back: p >= 0.5 -> -round(100p/(1-p)); p < 0.5 -> +round(100(1-p)/p).
"""
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from backend.models import Base, Game, Odds, PlayerProp, Team
from backend.paper import pricing
from backend.paper.pricing import GameBet, PricingError, PropBet
from backend.pipeline.grader import grade_pick, grade_prop_pick

NOW = datetime(2026, 10, 4, 15, 0, tzinfo=timezone.utc)      # 11:00 ET
KICKOFF = datetime(2026, 10, 4, 17, 0)                         # naive UTC
GAME_DAY = date(2026, 10, 4)


@pytest.fixture
def session(db_engine, db_session):
    Base.metadata.create_all(db_engine)
    return db_session


def _game(session, **overrides):
    home = Team(name="Kansas City Chiefs", abbreviation="Chiefs", sport="nfl")
    away = Team(name="Buffalo Bills", abbreviation="Bills", sport="nfl")
    session.add_all([home, away])
    session.flush()
    fields = dict(sport="nfl", season="2026", date=GAME_DAY,
                  home_team_id=home.id, away_team_id=away.id,
                  status="scheduled", start_time=KICKOFF)
    fields.update(overrides)
    game = Game(**fields)
    session.add(game)
    session.commit()
    return game


def _odds(session, game, book, age=timedelta(hours=1), **fields):
    session.add(Odds(game_id=game.id, bookmaker=book,
                     timestamp=(NOW - age).replace(tzinfo=None), **fields))
    session.commit()


def _prop(session, game, book, odds, age=timedelta(hours=1), player="Jalen Hurts",
          market="player_pass_yds", outcome="Over", line=225.5):
    session.add(PlayerProp(game_id=game.id, bookmaker=book, market=market,
                           player_name=player, outcome=outcome, line=line, odds=odds,
                           fetched_at=(NOW - age).replace(tzinfo=None)))
    session.commit()


def _price(session, game, bet):
    return pricing.price(session, game, bet, now=NOW)


# --- consensus -------------------------------------------------------------

def test_moneyline_is_the_consensus_of_two_books(session):
    g = _game(session)
    _odds(session, g, "dk", moneyline_home=-110, moneyline_away=100)
    _odds(session, g, "fd", moneyline_home=-130, moneyline_away=110)
    home = _price(session, g, GameBet(g.id, "moneyline", "HOME"))
    away = _price(session, g, GameBet(g.id, "moneyline", "AWAY"))
    # home: (0.523810 + 0.565217)/2 = 0.544513 -> -round(119.55) = -120
    assert (home.odds, home.pick_value, home.line) == (-120, "HOME ML", None)
    # away: (0.500000 + 0.476190)/2 = 0.488095 -> +round(104.88) = +105
    assert (away.odds, away.pick_value) == (105, "AWAY ML")


def test_three_books(session):
    g = _game(session)
    for book, price in (("a", -110), ("b", -110), ("c", -120)):
        _odds(session, g, book, moneyline_home=price, moneyline_away=100)
    # (0.523810 + 0.523810 + 0.545455)/3 = 0.531025 -> -round(113.23) = -113
    assert _price(session, g, GameBet(g.id, "moneyline", "HOME")).odds == -113


def test_spread_uses_the_consensus_line_and_price(session):
    """Review Focus 3: books at -3 and -4 -> the bet is at -3.5."""
    g = _game(session)
    _odds(session, g, "dk", moneyline_home=-150, moneyline_away=130,
          spread_home=-3.0, spread_away=3.0,
          spread_home_price=-110, spread_away_price=100)
    _odds(session, g, "fd", moneyline_home=-150, moneyline_away=130,
          spread_home=-4.0, spread_away=4.0,
          spread_home_price=-130, spread_away_price=110)
    home = _price(session, g, GameBet(g.id, "spread", "HOME"))
    away = _price(session, g, GameBet(g.id, "spread", "AWAY"))
    assert (home.pick_value, home.line, home.odds) == ("HOME -3.5", -3.5, -120)
    assert (away.pick_value, away.line, away.odds) == ("AWAY +3.5", 3.5, 105)


def test_total_uses_the_consensus_line_and_price(session):
    g = _game(session)
    _odds(session, g, "dk", moneyline_home=-150, moneyline_away=130,
          over_under=44.5, over_price=-110, under_price=-110)
    over = _price(session, g, GameBet(g.id, "over_under", "Over"))
    under = _price(session, g, GameBet(g.id, "over_under", "Under"))
    assert (over.pick_value, over.line, over.odds) == ("Over 44.5", 44.5, -110)
    assert under.pick_value == "Under 44.5"


# --- refusals ----------------------------------------------------------------

def test_an_unquoted_market_is_refused_and_never_priced_at_minus_110(session):
    g = _game(session)
    _odds(session, g, "dk", moneyline_home=-150, moneyline_away=130,
          spread_home=-3.5, spread_away=3.5)   # lines, but no spread prices
    for bet in (GameBet(g.id, "spread", "HOME"), GameBet(g.id, "over_under", "Over")):
        with pytest.raises(PricingError) as err:
            _price(session, g, bet)
        assert (err.value.reason, err.value.status) == ("not_quoted", 409)


def test_a_game_with_no_odds_rows_is_not_quoted(session):
    g = _game(session)
    with pytest.raises(PricingError) as err:
        _price(session, g, GameBet(g.id, "moneyline", "HOME"))
    assert err.value.reason == "not_quoted"


def test_an_unusable_book_price_is_ignored(session):
    """Review Focus 2: a 0 price neither prices the bet nor crashes."""
    g = _game(session)
    _odds(session, g, "bad", moneyline_home=0, moneyline_away=0)
    with pytest.raises(PricingError) as err:
        _price(session, g, GameBet(g.id, "moneyline", "HOME"))
    assert err.value.reason == "not_quoted"

    _odds(session, g, "dk", age=timedelta(hours=2), moneyline_home=-110, moneyline_away=100)
    quote = _price(session, g, GameBet(g.id, "moneyline", "HOME"))
    assert quote.odds == -110
    # The bad row is newer (1h) but must not set the clock: 2h is the newest usable.
    assert quote.quoted_at == NOW - timedelta(hours=2)


@pytest.mark.parametrize("age, ok", [
    (timedelta(hours=5, minutes=59), True),
    (timedelta(hours=6, minutes=1), False),
])
def test_the_freshness_boundary(session, age, ok):
    g = _game(session)
    _odds(session, g, "dk", age=age, moneyline_home=-110, moneyline_away=100)
    bet = GameBet(g.id, "moneyline", "HOME")
    if ok:
        assert _price(session, g, bet).odds == -110
    else:
        with pytest.raises(PricingError) as err:
            _price(session, g, bet)
        assert (err.value.reason, err.value.status) == ("stale", 409)
        assert err.value.message == "The price is stale — ask Marcus to refresh."


def test_freshness_is_judged_per_market(session):
    g = _game(session)
    _odds(session, g, "fresh", age=timedelta(hours=1), moneyline_home=-110, moneyline_away=100)
    _odds(session, g, "old", age=timedelta(hours=7), moneyline_home=-110, moneyline_away=100,
          spread_home=-3.5, spread_away=3.5, spread_home_price=-110, spread_away_price=-110)
    assert _price(session, g, GameBet(g.id, "moneyline", "HOME")).odds == -110
    with pytest.raises(PricingError) as err:
        _price(session, g, GameBet(g.id, "spread", "HOME"))
    assert err.value.reason == "stale"


@pytest.mark.parametrize("overrides", [
    {"start_time": datetime(2026, 10, 4, 14, 0)},                  # kicked off an hour ago
    {"status": "final"},
    {"status": "in_progress"},
    {"start_time": None, "date": date(2026, 10, 3)},               # stale row from yesterday
])
def test_a_game_that_is_not_open_is_refused(session, overrides):
    g = _game(session, **overrides)
    _odds(session, g, "dk", moneyline_home=-110, moneyline_away=100)
    with pytest.raises(PricingError) as err:
        _price(session, g, GameBet(g.id, "moneyline", "HOME"))
    assert (err.value.reason, err.value.status) == ("game_started", 400)


def test_a_same_day_game_with_no_start_time_is_open(session):
    g = _game(session, start_time=None)
    _odds(session, g, "dk", moneyline_home=-110, moneyline_away=100)
    assert _price(session, g, GameBet(g.id, "moneyline", "HOME")).odds == -110


# --- props -------------------------------------------------------------------

def test_a_prop_is_the_consensus_of_the_books_quoting_that_exact_line(session):
    g = _game(session)
    _prop(session, g, "dk", -110)
    _prop(session, g, "fd", -130)
    _prop(session, g, "mgm", -200, line=226.5)       # a different line: not included
    q = _price(session, g, PropBet(g.id, "Jalen Hurts", "player_pass_yds", "Over", 225.5))
    assert q.odds == -120                             # same arithmetic as the moneyline test
    assert q.pick_value == "Jalen Hurts Over 225.5 Pass Yards"
    assert (q.pick_type, q.line, q.prop_player, q.prop_market) == (
        "prop", 225.5, "Jalen Hurts", "player_pass_yds")


def test_an_unquoted_prop_line_is_refused(session):
    g = _game(session)
    _prop(session, g, "dk", -110)
    with pytest.raises(PricingError) as err:
        _price(session, g, PropBet(g.id, "Jalen Hurts", "player_pass_yds", "Over", 230.5))
    assert err.value.reason == "not_quoted"


def test_an_ungradeable_prop_market_is_refused(session):
    g = _game(session)
    _prop(session, g, "dk", -110, player="Aaron Judge", market="batter_hits", line=0.5)
    with pytest.raises(PricingError) as err:
        _price(session, g, PropBet(g.id, "Aaron Judge", "batter_hits", "Over", 0.5))
    assert (err.value.reason, err.value.status) == ("not_gradeable", 409)


def test_a_stale_prop_is_refused(session):
    g = _game(session)
    _prop(session, g, "dk", -110, age=timedelta(hours=6, minutes=1))
    with pytest.raises(PricingError) as err:
        _price(session, g, PropBet(g.id, "Jalen Hurts", "player_pass_yds", "Over", 225.5))
    assert err.value.reason == "stale"


def test_prop_quotes_lists_only_gradeable_over_under_lines(session):
    """Review Focus 1: anytime-TD rows (outcome Yes, no line) are left out."""
    g = _game(session)
    _prop(session, g, "dk", -110)
    _prop(session, g, "dk", -110, outcome="Under")
    _prop(session, g, "dk", 150, player="A.J. Brown", market="player_anytime_td",
          outcome="Yes", line=None)
    _prop(session, g, "dk", -110, player="Aaron Judge", market="batter_hits", line=0.5)
    rows = pricing.prop_quotes(session, g, now=NOW)
    assert [(r["prop_player"], r["outcome"], r["available"]) for r in rows] == [
        ("Jalen Hurts", "Over", True), ("Jalen Hurts", "Under", True)]
    assert rows[0]["market_label"] == "Pass Yards"
    assert rows[0]["odds"] == -110


def test_game_quotes_covers_six_sides_with_reasons(session):
    g = _game(session)
    _odds(session, g, "dk", moneyline_home=-110, moneyline_away=100)
    rows = pricing.game_quotes(session, g, now=NOW)
    assert [(r["pick_type"], r["side"], r["available"]) for r in rows] == [
        ("moneyline", "HOME", True), ("moneyline", "AWAY", True),
        ("spread", "HOME", False), ("spread", "AWAY", False),
        ("over_under", "Over", False), ("over_under", "Under", False)]
    assert rows[2]["reason"] == "not_quoted" and rows[2]["message"]
    assert rows[0]["quoted_at"] == (NOW - timedelta(hours=1)).isoformat()


# --- parlay ------------------------------------------------------------------

def test_combine_two_minus_110_legs():
    # (1 + 100/110)^2 = 1.909091^2 = 3.644628 -> +round(264.46) = +264
    american, decimal = pricing.combine([-110, -110])
    assert american == 264
    assert decimal == pytest.approx(3.644628, abs=1e-6)


def test_combine_short_parlay_stays_negative():
    # (1 + 100/400) * (1 + 100/500) = 1.25 * 1.2 = 1.5 -> -round(100/0.5) = -200
    assert pricing.combine([-400, -500])[0] == -200


# --- round trip: every label the module writes, the grader reads ------------

def test_every_game_label_grades_correctly(session):
    g = _game(session)
    _odds(session, g, "dk", moneyline_home=-150, moneyline_away=130,
          spread_home=-3.5, spread_away=3.5, spread_home_price=-110, spread_away_price=-110,
          over_under=44.5, over_price=-110, under_price=-110)
    expected = {  # final 27-20: home wins by 7, total 47
        ("moneyline", "HOME"): "win", ("moneyline", "AWAY"): "loss",
        ("spread", "HOME"): "win", ("spread", "AWAY"): "loss",
        ("over_under", "Over"): "win", ("over_under", "Under"): "loss",
    }
    for (pick_type, side), result in expected.items():
        q = _price(session, g, GameBet(g.id, pick_type, side))
        graded = grade_pick(q.pick_type, q.pick_value, 27, 20, q.odds)
        assert graded is not None and graded[0] == result, (q.pick_value, graded)


def test_a_prop_label_grades_correctly(session):
    g = _game(session)
    _prop(session, g, "dk", -110)
    q = _price(session, g, PropBet(g.id, "Jalen Hurts", "player_pass_yds", "Over", 225.5))
    stat = SimpleNamespace(pass_yards=240.0)
    assert grade_prop_pick(q.pick_value, q.prop_market, stat)[0] == "win"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest backend/tests/test_paper_pricing.py -q -p no:warnings`
Expected: collection error `ModuleNotFoundError: No module named 'backend.paper'`.

- [ ] **Step 3: Write the implementation**

Create `backend/paper/__init__.py`:

```python
"""Paper trading: the server-side rules for friends' play-money bets."""
```

Create `backend/paper/pricing.py`:

```python
"""The only place a paper bet gets its price.

Players used to type their own odds and line, and ``grade_pick`` settled
whatever was typed -- a spread of "HOME +60" at -110 was accepted, so the
ROI leaderboard could be gamed through the ordinary UI (plan 026's final
review). Since plan 027 every bet route and every quote endpoint calls
:func:`price`, so the price a player is shown is produced by the same
function that prices the bet.

Consensus is :func:`backend.analysis.strategy.average_odds` /
:func:`consensus_moneyline` -- the definition the Model's own picks use --
never a second averager.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from backend.analysis.odds_utils import InvalidOddsError, american_to_implied_prob
from backend.analysis.prop_markets import MARKET_STAT_MAP, market_label
from backend.analysis.strategy import average_odds, consensus_moneyline
from backend.models import Odds, PlayerProp
from backend.time_utils import ET, game_start_utc

#: A price older than this is refused. Judged per market, on the newest
#: quote time behind the price.
MAX_QUOTE_AGE = timedelta(hours=6)

_STATUS = {"game_started": 400, "not_quoted": 409, "stale": 409, "not_gradeable": 409}
_MESSAGES = {
    "game_started": "Betting has closed: this game has already started",
    "not_quoted": "No book is quoting this bet right now.",
    "stale": "The price is stale — ask Marcus to refresh.",
    "not_gradeable": "This market can't be graded, so it can't be bet.",
}

#: (pick_type, side) -> (consensus price key, consensus line key or None).
#: The keys are both ``Odds`` columns and ``average_odds`` result keys.
GAME_MARKETS: dict[tuple[str, str], tuple[str, str | None]] = {
    ("moneyline", "HOME"): ("moneyline_home", None),
    ("moneyline", "AWAY"): ("moneyline_away", None),
    ("spread", "HOME"): ("spread_home_price", "spread_home"),
    ("spread", "AWAY"): ("spread_away_price", "spread_away"),
    ("over_under", "Over"): ("over_price", "over_under"),
    ("over_under", "Under"): ("under_price", "over_under"),
}


class PricingError(Exception):
    """A bet the server will not price. ``reason`` is a stable code."""

    def __init__(self, reason: str):
        self.reason = reason
        self.message = _MESSAGES[reason]
        self.status = _STATUS[reason]
        super().__init__(self.message)


@dataclass(frozen=True)
class GameBet:
    game_id: int
    pick_type: str   # moneyline | spread | over_under
    side: str        # HOME | AWAY (moneyline, spread); Over | Under (total)


@dataclass(frozen=True)
class PropBet:
    game_id: int
    prop_player: str
    prop_market: str
    outcome: str     # Over | Under
    line: float


@dataclass(frozen=True)
class Quote:
    pick_type: str
    pick_value: str
    odds: int
    line: float | None
    quoted_at: datetime
    prop_player: str | None = None
    prop_market: str | None = None

    def as_dict(self) -> dict:
        return {
            "pick_type": self.pick_type,
            "pick_value": self.pick_value,
            "odds": self.odds,
            "line": self.line,
            "quoted_at": self.quoted_at.isoformat(),
            "prop_player": self.prop_player,
            "prop_market": self.prop_market,
        }


def _utc(ts: datetime) -> datetime:
    """SQLite returns naive datetimes; every one this project writes is UTC."""
    return ts if ts.tzinfo is not None else ts.replace(tzinfo=timezone.utc)


def _usable(price) -> bool:
    if price is None:
        return False
    try:
        american_to_implied_prob(price)
    except InvalidOddsError:
        return False
    return True


def open_for_betting(game, now: datetime | None = None) -> bool:
    """Only games that have not started. A missing start_time is not past
    for a same-day game (the project-wide convention for unknown data), but
    ingestion never writes an in-progress status, so a 'scheduled' game
    dated before today with no start_time is a stale row for a game that
    already happened -- 82 such rows (59 MMA, 23 boxing) exist in the live
    db with public results. A status past 'scheduled' is always closed."""
    now = now or datetime.now(timezone.utc)
    if game.status != "scheduled":
        return False
    start = game_start_utc(game)
    if start is None:
        return game.date >= now.astimezone(ET).date()
    return start > now


def _check_fresh(quoted_at: datetime, now: datetime) -> None:
    if now - quoted_at > MAX_QUOTE_AGE:
        raise PricingError("stale")


def _price_game(session, game, bet: GameBet, now: datetime) -> Quote:
    try:
        price_key, line_key = GAME_MARKETS[(bet.pick_type, bet.side)]
    except KeyError:
        raise ValueError(f"not a game bet: {bet.pick_type}/{bet.side}") from None
    rows = session.query(Odds).filter(Odds.game_id == game.id).all()
    consensus = average_odds(rows) or {}
    odds = consensus.get(price_key)
    line = consensus.get(line_key) if line_key else None
    if odds is None or (line_key is not None and line is None):
        raise PricingError("not_quoted")
    contributing = [r for r in rows if _usable(getattr(r, price_key))]
    quoted_at = max(_utc(r.timestamp) for r in contributing)
    _check_fresh(quoted_at, now)
    if bet.pick_type == "moneyline":
        label = f"{bet.side} ML"
    elif bet.pick_type == "spread":
        label = f"{bet.side} {line:+g}"
    else:
        label = f"{bet.side} {line:g}"
    return Quote(bet.pick_type, label, odds, line, quoted_at)


def _price_prop(session, game, bet: PropBet, now: datetime) -> Quote:
    # The grader parses "(Over|Under) <line>" and sums MARKET_STAT_MAP's
    # columns; anything else would sit pending forever.
    if bet.prop_market not in MARKET_STAT_MAP or bet.outcome not in ("Over", "Under"):
        raise PricingError("not_gradeable")
    rows = (session.query(PlayerProp)
            .filter(PlayerProp.game_id == game.id,
                    PlayerProp.market == bet.prop_market,
                    PlayerProp.player_name == bet.prop_player,
                    PlayerProp.outcome == bet.outcome,
                    PlayerProp.line.isnot(None))
            .all())
    rows = [r for r in rows if abs(r.line - bet.line) < 1e-9 and _usable(r.odds)]
    odds = consensus_moneyline([r.odds for r in rows])
    if odds is None:
        raise PricingError("not_quoted")
    quoted_at = max(_utc(r.fetched_at) for r in rows)
    _check_fresh(quoted_at, now)
    label = f"{bet.prop_player} {bet.outcome} {bet.line:g} {market_label(bet.prop_market)}"
    return Quote("prop", label, odds, bet.line, quoted_at,
                 prop_player=bet.prop_player, prop_market=bet.prop_market)


def price(session, game, bet: GameBet | PropBet, now: datetime | None = None) -> Quote:
    """Price ``bet`` on ``game`` at the current consensus, or raise PricingError."""
    now = now or datetime.now(timezone.utc)
    if not open_for_betting(game, now):
        raise PricingError("game_started")
    if isinstance(bet, PropBet):
        return _price_prop(session, game, bet, now)
    return _price_game(session, game, bet, now)


def combine(odds: list[int]) -> tuple[int, float]:
    """A parlay's price from its legs' prices: (American, decimal)."""
    decimal = 1.0
    for o in odds:
        decimal *= (1 + 100 / abs(o)) if o < 0 else (1 + o / 100)
    if decimal >= 2.0:
        american = int(round((decimal - 1) * 100))
    else:
        american = int(round(-100 / (decimal - 1)))
    return american, decimal


def _entry(session, game, bet, now, base: dict) -> dict:
    try:
        quote = price(session, game, bet, now)
    except PricingError as e:
        return {**base, "available": False, "reason": e.reason, "message": e.message}
    return {**base, "available": True, **quote.as_dict()}


def game_quotes(session, game, now: datetime | None = None) -> list[dict]:
    """Every game market's six sides, each a quote or a refusal."""
    now = now or datetime.now(timezone.utc)
    return [_entry(session, game, GameBet(game.id, pick_type, side), now,
                   {"pick_type": pick_type, "side": side})
            for pick_type, side in GAME_MARKETS]


def prop_quotes(session, game, now: datetime | None = None) -> list[dict]:
    """Every gradeable (player, market, Over/Under, line) a book quotes on ``game``."""
    now = now or datetime.now(timezone.utc)
    keys = (session.query(PlayerProp.player_name, PlayerProp.market,
                          PlayerProp.outcome, PlayerProp.line)
            .filter(PlayerProp.game_id == game.id,
                    PlayerProp.market.in_(list(MARKET_STAT_MAP)),
                    PlayerProp.outcome.in_(("Over", "Under")),
                    PlayerProp.line.isnot(None))
            .distinct().all())
    out = []
    for player, market, outcome, line in sorted(keys, key=lambda k: (k[0], k[1], k[3], k[2])):
        base = {"prop_player": player, "prop_market": market,
                "market_label": market_label(market), "outcome": outcome, "line": line}
        out.append(_entry(session, game, PropBet(game.id, player, market, outcome, line),
                          now, base))
    return out
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest backend/tests/test_paper_pricing.py -q -p no:warnings`
Expected: all pass.

- [ ] **Step 5: Mutation checks** (restore after each)

1. In `price`, delete the `if not open_for_betting(...)` block →
   `test_a_game_that_is_not_open_is_refused` fails.
2. In `_price_game` and `_price_prop`, comment out `_check_fresh(...)` →
   `test_the_freshness_boundary[age1-False]`, `test_freshness_is_judged_per_market`
   and `test_a_stale_prop_is_refused` fail.
3. Change `_check_fresh` to `>= MAX_QUOTE_AGE + timedelta(minutes=2)` →
   `test_the_freshness_boundary[age1-False]` fails.
4. In `_price_prop`, delete the `not_gradeable` check →
   `test_an_ungradeable_prop_market_is_refused` fails.
5. In `_price_game`, replace `contributing = [... if _usable(...)]` with
   `contributing = rows` → `test_an_unusable_book_price_is_ignored` fails.

- [ ] **Step 6: Commit**

```bash
git add backend/paper backend/tests/test_paper_pricing.py
git commit -m "feat(paper): one pricing module prices every paper bet at the consensus

<mutation checks 1-5 and their failing tests>"
```

---

### Task 2: Quote endpoints, consensus on `/games/today`, and `has_pin`

**Files:**
- Create: `backend/api/paper.py`, `backend/tests/test_paper_quotes_api.py`
- Modify: `backend/api/main.py` (router mounts, near line 115),
  `backend/api/games.py` (`get_today_games`, lines 17–85),
  `backend/api/users.py` (`list_users` ~line 177, `get_user` ~line 362),
  `backend/tests/test_games_api.py`

**Interfaces:**
- Consumes: `pricing.game_quotes`, `pricing.prop_quotes` (Task 1); `average_odds`.
- Produces (used by Tasks 4–6):
  - `GET /paper/quotes?game_id=N` → `{"game_id": N, "quotes": [ {pick_type, side, available, …} ×6 ]}`; 404 for an unknown game.
  - `GET /paper/prop-quotes?game_id=N` → `{"game_id": N, "quotes": [ {prop_player, prop_market, market_label, outcome, line, available, …} ]}`.
  - Each available entry adds `pick_value, odds, line, quoted_at, prop_player, prop_market`; each unavailable adds `reason, message`.
  - `GET /users/` rows and `GET /users/{id}` gain `has_pin: bool`.
  - `/games/today` `moneyline_home/away`, `spread_home`, `over_under` are the consensus; `bookmaker` is `"consensus"` when any odds row exists.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_paper_quotes_api.py`:

```python
"""Quote endpoints and has_pin (plan 027, Task 2)."""
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from backend.api.main import create_app
from backend.database import get_session
from backend.models import Base, Game, Odds, PlayerProp, Team, UserProfile
from backend.tests.auth_helpers import ALL_HEADERS, TEST_PIN
from backend.time_utils import et_today


def _client():
    client = TestClient(create_app(":memory:"), headers=ALL_HEADERS)
    Base.metadata.create_all(client.app.state.engine)
    return client


def _game_with_odds(client):
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    s = get_session(client.app.state.engine)
    home = Team(name="Home", abbreviation="HOM", sport="nfl")
    away = Team(name="Away", abbreviation="AWY", sport="nfl")
    s.add_all([home, away])
    s.flush()
    g = Game(sport="nfl", season="2026", date=et_today(), home_team_id=home.id,
             away_team_id=away.id, status="scheduled")
    s.add(g)
    s.flush()
    s.add_all([
        Odds(game_id=g.id, bookmaker="dk", moneyline_home=-110, moneyline_away=100,
             timestamp=now - timedelta(minutes=5)),
        Odds(game_id=g.id, bookmaker="fd", moneyline_home=-130, moneyline_away=110,
             timestamp=now - timedelta(minutes=5)),
        PlayerProp(game_id=g.id, bookmaker="dk", market="player_pass_yds",
                   player_name="QB One", outcome="Over", line=225.5, odds=-110,
                   fetched_at=now - timedelta(minutes=5)),
    ])
    s.commit()
    gid = g.id
    s.close()
    return gid


def test_quotes_returns_the_consensus_and_refusals():
    client = _client()
    gid = _game_with_odds(client)
    r = client.get(f"/paper/quotes?game_id={gid}")
    assert r.status_code == 200
    quotes = {(q["pick_type"], q["side"]): q for q in r.json()["quotes"]}
    assert quotes[("moneyline", "HOME")]["odds"] == -120        # hand-computed in test_paper_pricing
    assert quotes[("moneyline", "HOME")]["pick_value"] == "HOME ML"
    assert quotes[("spread", "HOME")] == {
        "pick_type": "spread", "side": "HOME", "available": False,
        "reason": "not_quoted", "message": "No book is quoting this bet right now."}


def test_prop_quotes_lists_the_quoted_prop():
    client = _client()
    gid = _game_with_odds(client)
    [row] = client.get(f"/paper/prop-quotes?game_id={gid}").json()["quotes"]
    assert (row["prop_player"], row["outcome"], row["line"], row["odds"], row["available"]) == (
        "QB One", "Over", 225.5, -110, True)


def test_quotes_for_an_unknown_game_is_404():
    client = _client()
    assert client.get("/paper/quotes?game_id=999").status_code == 404
    assert client.get("/paper/prop-quotes?game_id=999").status_code == 404


def test_quotes_are_open_reads():
    client = TestClient(create_app(":memory:"))       # no owner key, no PIN
    Base.metadata.create_all(client.app.state.engine)
    gid = _game_with_odds(client)
    assert client.get(f"/paper/quotes?game_id={gid}").status_code == 200


def test_has_pin_is_reported_and_no_secret_leaves():
    client = _client()
    client.post("/users/", json={"name": "friend", "pin": TEST_PIN})
    s = get_session(client.app.state.engine)
    s.add(UserProfile(name="legacy"))                   # pre-026 player: no PIN
    s.commit()
    legacy_id = s.query(UserProfile).filter_by(name="legacy").one().id
    s.close()

    rows = {u["name"]: u for u in client.get("/users/").json()}
    assert rows["friend"]["has_pin"] is True
    assert rows["legacy"]["has_pin"] is False
    assert client.get(f"/users/{legacy_id}").json()["has_pin"] is False
    for body in (client.get("/users/").text, client.get(f"/users/{legacy_id}").text):
        assert "pin_hash" not in body and "pin_salt" not in body
```

Add to `backend/tests/test_games_api.py` (reuse that file's own client and
seeding helpers; if its seeding helper does not take odds rows, seed them
with `get_session` exactly as `_game_with_odds` above does):

```python
def test_today_shows_the_consensus_not_the_first_book():
    """dk -110 and fd -130 on the home side -> consensus -120 (hand-computed
    in test_paper_pricing). The first book's -110 must not be shown."""
    client = _client()          # this file's existing client helper
    gid = _seed_today_game_with_two_books(client)
    [game] = [g for g in client.get("/games/today").json() if g["id"] == gid]
    assert game["moneyline_home"] == -120
    assert game["bookmaker"] == "consensus"
    assert game["odds_count"] == 2
```

Write `_seed_today_game_with_two_books(client)` in `test_games_api.py` with a
scheduled game dated `et_today()`, `start_time=None`, and two `Odds` rows:
`dk` (−110 / +100) and `fd` (−130 / +110). If the file has no `_client()`
helper, use `TestClient(create_app(":memory:"))` plus
`Base.metadata.create_all`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest backend/tests/test_paper_quotes_api.py backend/tests/test_games_api.py -q -p no:warnings`
Expected: the quote tests fail with 404 (no route), `has_pin` with `KeyError`,
the games test with `-110 != -120`.

- [ ] **Step 3: Implement**

Create `backend/api/paper.py`:

```python
"""Read-only paper-trading quotes. Open like every GET: prices are public."""
from fastapi import APIRouter, HTTPException, Query, Request

from backend.database import get_session
from backend.models import Game
from backend.paper import pricing

router = APIRouter()


def _game_or_404(session, game_id: int):
    game = session.get(Game, game_id)
    if game is None:
        raise HTTPException(status_code=404, detail="Game not found")
    return game


@router.get("/quotes")
def quotes(request: Request, game_id: int = Query(...)):
    """The six game-market sides for one game, each priced or refused."""
    session = get_session(request.app.state.engine)
    try:
        game = _game_or_404(session, game_id)
        return {"game_id": game.id, "quotes": pricing.game_quotes(session, game)}
    finally:
        session.close()


@router.get("/prop-quotes")
def prop_quotes(request: Request, game_id: int = Query(...)):
    """Every gradeable prop line a book quotes on one game, priced or refused."""
    session = get_session(request.app.state.engine)
    try:
        game = _game_or_404(session, game_id)
        return {"game_id": game.id, "quotes": pricing.prop_quotes(session, game)}
    finally:
        session.close()
```

In `backend/api/main.py`, import it next to the other routers
(`from backend.api.paper import router as paper_router`, matching how
`users_router` is imported) and mount it next to `users_router`:

```python
    app.include_router(paper_router, prefix="/paper", tags=["paper"])
```

It must be registered before the SPA catch-all (it is, if placed beside the
other `include_router` calls).

In `backend/api/games.py`, add `from backend.analysis.strategy import average_odds`
and replace the `best = odds_rows[0] …` line and the five `best.…` fields:

```python
            odds_rows = session.query(Odds).filter(Odds.game_id == game.id).all()
            # The same consensus the paper-bet quotes use (plan 027), so every
            # price on screen agrees. It used to show whichever book's row came
            # back first.
            consensus = average_odds(odds_rows) or {}
```

```python
                "moneyline_home": consensus.get("moneyline_home"),
                "moneyline_away": consensus.get("moneyline_away"),
                "spread_home": consensus.get("spread_home"),
                "over_under": consensus.get("over_under"),
                "bookmaker": "consensus" if odds_rows else None,
                "odds_count": len(odds_rows),
```

In `backend/api/users.py`, add one key to the dict built in `list_users` and
to the dict returned by `get_user`, right after `"name"`:

```python
                "has_pin": u.pin_hash is not None,
```

(`user.pin_hash` in `get_user`.)

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest backend/tests/test_paper_quotes_api.py backend/tests/test_games_api.py backend/tests/test_write_route_coverage.py -q -p no:warnings`
Expected: all pass.

- [ ] **Step 5: Mutation check**

Change `"has_pin": u.pin_hash is not None` in `list_users` to `True` →
`test_has_pin_is_reported_and_no_secret_leaves` fails. Restore.

- [ ] **Step 6: Commit**

```bash
git add backend/api/paper.py backend/api/main.py backend/api/games.py backend/api/users.py backend/tests/test_paper_quotes_api.py backend/tests/test_games_api.py
git commit -m "feat(api): /paper quotes, consensus on /games/today, has_pin on users"
```

---

### Task 3: Bet routes priced by the server

**Files:**
- Create: `backend/tests/pricing_helpers.py`, `backend/tests/test_priced_bets.py`
- Modify: `backend/api/users.py` (imports; request models lines ~95–133;
  `_open_for_betting` lines 149–161; `place_pick` ~380–445; `place_parlay` ~489–588)
- Modify (convert to the new body): `backend/tests/test_api_users.py` (20
  call sites), `backend/tests/test_paper_bet_odds_guard.py` (5),
  `backend/tests/test_player_pins.py` (1), `backend/tests/test_websocket.py` (1)

**Interfaces:**
- Consumes: `pricing.price`, `pricing.combine`, `pricing.open_for_betting`,
  `PricingError`, `GameBet`, `PropBet` (Task 1).
- Produces (used by Tasks 4–5):
  - `POST /users/{id}/picks` body: `{game_id, pick_type: moneyline|spread|over_under, side, stake}` or `{game_id, pick_type: "prop", prop_player, prop_market, outcome: Over|Under, line, stake}`. Any other key → 422.
  - Response: `{id, result, payout, new_balance, pick_value, odds, line, quoted_at}`.
  - `POST /users/{id}/parlay` body: `{legs: [<game leg or prop leg, no stake>], stake}`.
  - Response: `{id, legs: [{pick_value, odds, line, quoted_at, result}], combined_odds, potential_payout, result, payout, new_balance}`.
  - `backend/tests/pricing_helpers.py`: `seed_fresh_odds(engine, game_id, **fields)`, `seed_fresh_prop(engine, game_id, **fields)`.

- [ ] **Step 1: Write the test helper**

Create `backend/tests/pricing_helpers.py`:

```python
"""Fresh book quotes for tests that place paper bets (plan 027).

Bets are priced by the server now, so a test game needs a quote less than
six hours old. Defaults: both moneylines -110, spread -3.5/+3.5 at -110,
total 220.5 at -110 -- so a HOME ML bet is charged -110, as the old tests
assumed.
"""
from datetime import datetime, timezone

from backend.database import get_session
from backend.models import Odds, PlayerProp

ODDS_DEFAULTS = dict(
    bookmaker="testbook", moneyline_home=-110, moneyline_away=-110,
    spread_home=-3.5, spread_away=3.5, spread_home_price=-110, spread_away_price=-110,
    over_under=220.5, over_price=-110, under_price=-110,
)


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def seed_fresh_odds(engine, game_id: int, **fields) -> None:
    s = get_session(engine)
    s.add(Odds(game_id=game_id, timestamp=_now(), **{**ODDS_DEFAULTS, **fields}))
    s.commit()
    s.close()


def seed_fresh_prop(engine, game_id: int, **fields) -> None:
    defaults = dict(bookmaker="testbook", market="player_pass_yds",
                    player_name="QB One", outcome="Over", line=225.5, odds=-110)
    s = get_session(engine)
    s.add(PlayerProp(game_id=game_id, fetched_at=_now(), **{**defaults, **fields}))
    s.commit()
    s.close()
```

- [ ] **Step 2: Write the failing tests**

Create `backend/tests/test_priced_bets.py`:

```python
"""Paper bets are priced by the server; the client cannot set a price (plan 027)."""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from backend.api.main import create_app
from backend.database import get_session
from backend.models import Base, Game, Odds, PaperPick, Parlay, Team
from backend.tests.auth_helpers import ALL_HEADERS, OWNER_HEADERS, TEST_PIN
from backend.tests.pricing_helpers import seed_fresh_odds, seed_fresh_prop
from backend.time_utils import et_today


def _client():
    client = TestClient(create_app(":memory:"), headers=ALL_HEADERS)
    Base.metadata.create_all(client.app.state.engine)
    return client


def _game(client, i=0):
    s = get_session(client.app.state.engine)
    home = Team(name=f"Home{i}", abbreviation=f"H{i}", sport="nfl")
    away = Team(name=f"Away{i}", abbreviation=f"A{i}", sport="nfl")
    s.add_all([home, away])
    s.flush()
    g = Game(sport="nfl", season="2026", date=et_today(), home_team_id=home.id,
             away_team_id=away.id, status="scheduled")
    s.add(g)
    s.commit()
    gid = g.id
    s.close()
    return gid


def _user(client):
    return client.post("/users/", json={"name": "friend", "pin": TEST_PIN}).json()["id"]


def _finish(client, gid, home, away):
    s = get_session(client.app.state.engine)
    g = s.get(Game, gid)
    g.status, g.home_score, g.away_score = "final", home, away
    s.commit()
    s.close()


# --- anti-tamper ------------------------------------------------------------

@pytest.mark.parametrize("extra", [
    {"odds": 100000},
    {"pick_value": "HOME +60"},
    {"line": 60},
])
def test_a_client_supplied_price_or_label_is_rejected(extra):
    client = _client()
    gid = _game(client)
    seed_fresh_odds(client.app.state.engine, gid)
    uid = _user(client)
    r = client.post(f"/users/{uid}/picks", json={
        "game_id": gid, "pick_type": "spread", "side": "HOME", "stake": 100, **extra})
    assert r.status_code == 422, r.text
    s = get_session(client.app.state.engine)
    assert s.query(PaperPick).count() == 0
    s.close()


def test_a_parlay_leg_with_a_price_is_rejected():
    client = _client()
    a, b = _game(client, 0), _game(client, 1)
    for gid in (a, b):
        seed_fresh_odds(client.app.state.engine, gid)
    uid = _user(client)
    r = client.post(f"/users/{uid}/parlay", json={"stake": 100, "legs": [
        {"game_id": a, "pick_type": "moneyline", "side": "HOME", "odds": 5000},
        {"game_id": b, "pick_type": "moneyline", "side": "HOME"}]})
    assert r.status_code == 422, r.text


def test_the_old_request_shape_is_rejected():
    client = _client()
    gid = _game(client)
    seed_fresh_odds(client.app.state.engine, gid)
    uid = _user(client)
    r = client.post(f"/users/{uid}/picks", json={
        "game_id": gid, "pick_type": "moneyline", "pick_value": "HOME ML",
        "odds": -110, "stake": 100})
    assert r.status_code == 422


def test_a_side_that_does_not_fit_the_market_is_rejected():
    client = _client()
    gid = _game(client)
    uid = _user(client)
    r = client.post(f"/users/{uid}/picks", json={
        "game_id": gid, "pick_type": "spread", "side": "Over", "stake": 100})
    assert r.status_code == 422


# --- the price charged --------------------------------------------------------

def test_the_bet_is_charged_the_quoted_consensus():
    client = _client()
    gid = _game(client)
    seed_fresh_odds(client.app.state.engine, gid, bookmaker="dk", spread_home_price=-110)
    seed_fresh_odds(client.app.state.engine, gid, bookmaker="fd", spread_home_price=-130)
    uid = _user(client)
    quoted = {(q["pick_type"], q["side"]): q
              for q in client.get(f"/paper/quotes?game_id={gid}").json()["quotes"]}
    r = client.post(f"/users/{uid}/picks", json={
        "game_id": gid, "pick_type": "spread", "side": "HOME", "stake": 100})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["odds"] == quoted[("spread", "HOME")]["odds"] == -120
    assert body["pick_value"] == "HOME -3.5" and body["line"] == -3.5
    s = get_session(client.app.state.engine)
    pick = s.query(PaperPick).one()
    assert (pick.pick_value, pick.odds) == ("HOME -3.5", -120)
    s.close()


def test_a_moved_price_is_charged_at_the_new_price():
    """Review Focus 5 (API half)."""
    client = _client()
    gid = _game(client)
    seed_fresh_odds(client.app.state.engine, gid)
    uid = _user(client)
    shown = client.get(f"/paper/quotes?game_id={gid}").json()["quotes"][0]["odds"]
    s = get_session(client.app.state.engine)
    s.query(Odds).update({Odds.moneyline_home: -150})
    s.commit()
    s.close()
    r = client.post(f"/users/{uid}/picks", json={
        "game_id": gid, "pick_type": "moneyline", "side": "HOME", "stake": 100})
    assert shown == -110 and r.json()["odds"] == -150


def test_a_stale_price_is_refused_with_409():
    client = _client()
    gid = _game(client)
    seed_fresh_odds(client.app.state.engine, gid)
    s = get_session(client.app.state.engine)
    seven_hours_ago = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=7)
    s.query(Odds).update({Odds.timestamp: seven_hours_ago})
    s.commit()
    s.close()
    uid = _user(client)
    r = client.post(f"/users/{uid}/picks", json={
        "game_id": gid, "pick_type": "moneyline", "side": "HOME", "stake": 100})
    assert r.status_code == 409
    assert r.json()["detail"] == "The price is stale — ask Marcus to refresh."


def test_an_unquoted_game_is_refused_with_409():
    client = _client()
    gid = _game(client)
    uid = _user(client)
    r = client.post(f"/users/{uid}/picks", json={
        "game_id": gid, "pick_type": "moneyline", "side": "HOME", "stake": 100})
    assert r.status_code == 409


def test_a_prop_bet_is_priced_from_the_books():
    client = _client()
    gid = _game(client)
    seed_fresh_prop(client.app.state.engine, gid, odds=-125)
    uid = _user(client)
    r = client.post(f"/users/{uid}/picks", json={
        "game_id": gid, "pick_type": "prop", "prop_player": "QB One",
        "prop_market": "player_pass_yds", "outcome": "Over", "line": 225.5, "stake": 50})
    assert r.status_code == 200, r.text
    assert (r.json()["odds"], r.json()["pick_value"]) == (-125, "QB One Over 225.5 Pass Yards")


# --- parlays -----------------------------------------------------------------

def test_a_parlay_is_combined_on_the_server_from_leg_quotes():
    client = _client()
    a, b = _game(client, 0), _game(client, 1)
    for gid in (a, b):
        seed_fresh_odds(client.app.state.engine, gid)
    uid = _user(client)
    r = client.post(f"/users/{uid}/parlay", json={"stake": 100, "legs": [
        {"game_id": a, "pick_type": "moneyline", "side": "HOME"},
        {"game_id": b, "pick_type": "over_under", "side": "Under"}]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["combined_odds"] == 264                   # two -110 legs, hand-computed in Task 1
    assert [leg["pick_value"] for leg in body["legs"]] == ["HOME ML", "Under 220.5"]
    s = get_session(client.app.state.engine)
    assert s.query(Parlay).one().combined_odds == 264
    s.close()


def test_a_parlay_with_one_unpriceable_leg_is_refused_whole():
    client = _client()
    a, b = _game(client, 0), _game(client, 1)
    seed_fresh_odds(client.app.state.engine, a)            # b has no quotes
    uid = _user(client)
    r = client.post(f"/users/{uid}/parlay", json={"stake": 100, "legs": [
        {"game_id": a, "pick_type": "moneyline", "side": "HOME"},
        {"game_id": b, "pick_type": "moneyline", "side": "HOME"}]})
    assert r.status_code == 409
    s = get_session(client.app.state.engine)
    assert s.query(Parlay).count() == 0 and s.query(PaperPick).count() == 0
    s.close()


# --- round trip through settlement -------------------------------------------

def test_a_consensus_spread_between_two_lines_grades_through_the_api():
    """Review Focus 3: books at -3 and -4 -> HOME -3.5; a 27-24 home win loses."""
    client = _client()
    gid = _game(client)
    seed_fresh_odds(client.app.state.engine, gid, bookmaker="dk",
                    spread_home=-3.0, spread_away=3.0)
    seed_fresh_odds(client.app.state.engine, gid, bookmaker="fd",
                    spread_home=-4.0, spread_away=4.0)
    uid = _user(client)
    placed = client.post(f"/users/{uid}/picks", json={
        "game_id": gid, "pick_type": "spread", "side": "HOME", "stake": 100}).json()
    assert placed["pick_value"] == "HOME -3.5"
    _finish(client, gid, 27, 24)
    assert client.post("/users/grade", headers=OWNER_HEADERS).status_code == 200
    s = get_session(client.app.state.engine)
    assert s.query(PaperPick).one().result == "loss"
    s.close()
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest backend/tests/test_priced_bets.py -q -p no:warnings`
Expected: the anti-tamper tests fail (today the old shape is accepted and
`odds` is a field), the pricing tests fail with 422 (`side` is unknown).

- [ ] **Step 4: Implement**

In `backend/api/users.py`:

1. Imports. Change the pydantic import and add:

```python
from typing import Annotated, Literal, Union
from pydantic import BaseModel, ConfigDict, Field, RootModel, model_validator
from backend.paper import pricing
from backend.paper.pricing import PricingError
```

Remove imports that become unused (`field_validator`, `InvalidOddsError`,
`_validate_american_odds`, `game_start_utc`, and `et_today` only if nothing
else in the file uses it — `et_today` is used at ~line 685, so keep it).
Run `.venv/Scripts/python.exe -m pyflakes backend/api/users.py` if available;
otherwise `grep -n` each name to confirm.

2. Replace `MAX_ABS_ODDS`, `_check_odds`, `PlacePickRequest`, `ParlayLeg`
and `PlaceParlayRequest` with:

```python
class _Strict(BaseModel):
    # Unknown keys are refused, not ignored: an old client sending `odds` or
    # `pick_value` gets a 422 rather than a bet at a price it did not choose.
    model_config = ConfigDict(extra="forbid")


class GameLeg(_Strict):
    game_id: int
    pick_type: Literal["moneyline", "spread", "over_under"]
    side: Literal["HOME", "AWAY", "Over", "Under"]

    @model_validator(mode="after")
    def _side_fits_market(self):
        allowed = ("Over", "Under") if self.pick_type == "over_under" else ("HOME", "AWAY")
        if self.side not in allowed:
            raise ValueError(f"side for {self.pick_type} must be {' or '.join(allowed)}")
        return self

    def to_bet(self) -> pricing.GameBet:
        return pricing.GameBet(self.game_id, self.pick_type, self.side)


class PropLeg(_Strict):
    game_id: int
    pick_type: Literal["prop"]
    prop_player: str = Field(min_length=1, max_length=100)
    prop_market: str = Field(min_length=1, max_length=64)
    outcome: Literal["Over", "Under"]
    line: float = Field(allow_inf_nan=False)

    def to_bet(self) -> pricing.PropBet:
        return pricing.PropBet(self.game_id, self.prop_player, self.prop_market,
                               self.outcome, self.line)


Leg = Annotated[Union[GameLeg, PropLeg], Field(discriminator="pick_type")]


class GameBetRequest(GameLeg):
    stake: float = Field(allow_inf_nan=False)


class PropBetRequest(PropLeg):
    stake: float = Field(allow_inf_nan=False)


class PlacePickRequest(RootModel[Annotated[Union[GameBetRequest, PropBetRequest],
                                           Field(discriminator="pick_type")]]):
    pass


class PlaceParlayRequest(_Strict):
    legs: list[Leg]
    stake: float = Field(allow_inf_nan=False)


def _priced(session, game, leg) -> pricing.Quote:
    """Price one bet or leg, turning a refusal into its HTTP status."""
    try:
        return pricing.price(session, game, leg.to_bet())
    except PricingError as e:
        raise HTTPException(status_code=e.status, detail=e.message) from None
```

3. Replace the body of `_open_for_betting` (keep the name; it has other
callers) so there is one definition:

```python
def _open_for_betting(game) -> bool:
    """See backend.paper.pricing.open_for_betting -- the one definition."""
    return pricing.open_for_betting(game)
```

4. In `place_pick`, after `body: PlacePickRequest` add `bet = body.root` as
the first line, and use `bet.` for `stake` and `game_id` everywhere `body.`
was used. Keep the balance, stake, 404 and `_open_for_betting` checks (and
their messages) as they are. After the `_open_for_betting` check, price the
bet and build the pick from the quote:

```python
        quote = _priced(session, game, bet)

        pick = PaperPick(
            user_id=user_id,
            game_id=bet.game_id,
            pick_type=quote.pick_type,
            pick_value=quote.pick_value,
            odds=quote.odds,
            stake=bet.stake,
            result=None,
            payout=None,
            prop_market=quote.prop_market,
            prop_player=quote.prop_player,
        )
```

Feed event: replace `body.odds` / `body.pick_value` / `body.stake` with
`quote.odds` / `quote.pick_value` / `bet.stake`. Return:

```python
        return {
            "id": pick.id,
            "result": None,
            "payout": None,
            "new_balance": round(current_balance, 2),
            **{k: v for k, v in quote.as_dict().items()
               if k in ("pick_value", "odds", "line", "quoted_at")},
        }
```

Delete the now-unused `result = None` / `payout = None` locals.

5. In `place_parlay`, price every leg **before** creating the `Parlay`, so
a refusal writes nothing. Replace the `combined_decimal` loop and the
American conversion with:

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

        combined_american, combined_decimal = pricing.combine([q.odds for _, q in quotes])
```

Then create the `Parlay` as today and, in place of the old per-leg loop:

```python
        leg_results = []
        for leg, quote in quotes:
            session.add(PaperPick(
                user_id=user_id,
                game_id=leg.game_id,
                pick_type=quote.pick_type,
                pick_value=quote.pick_value,
                odds=quote.odds,
                stake=0,  # Individual legs have 0 stake; parlay has the stake
                result=None,
                payout=0,
                prop_market=quote.prop_market,
                prop_player=quote.prop_player,
                parlay_id=parlay.id,
            ))
            leg_results.append({"pick_value": quote.pick_value, "odds": quote.odds,
                                "line": quote.line,
                                "quoted_at": quote.quoted_at.isoformat(), "result": None})
```

In the feed message use `" + ".join(q.pick_value for _, q in quotes)`.

- [ ] **Step 5: Convert the tests that send the old shape**

Every remaining old-shape call sends a moneyline `HOME ML` at −110. For each:
- seed a fresh quote for the game with
  `seed_fresh_odds(client.app.state.engine, game_id)` (or `app.state.engine`)
  right after the game is created;
- replace `"pick_value": "HOME ML", "odds": -110` (and `"Home ML"` in
  `test_websocket.py`) with `"side": "HOME"`; in parlay legs, drop
  `"odds": -110` and `"pick_value": "HOME ML"` and add `"side": "HOME"`.

Specific files:
- `test_api_users.py`: add `seed_fresh_odds(engine, game.id)` inside
  `_seed_games` for every `scheduled` game (finished games are refused
  before pricing, so they need none). The three "refused" tests at lines
  173–221 keep asserting 400.
- `test_player_pins.py`: `BET = {"pick_type": "moneyline", "side": "HOME", "stake": 10}`;
  in `_setup`, after the game commit, call `seed_fresh_odds(app.state.engine, 1)`.
- `test_websocket.py`: seed in `_seed_scheduled_game` and send `"side": "HOME"`.
- `test_paper_bet_odds_guard.py`: section 1 ("placement refuses unusable
  odds") tested a guard that no longer exists — a client cannot send odds.
  Replace its two parametrised placement tests with one test that proves
  the successor property: a book row priced `0` cannot price a bet.

```python
def test_a_book_price_of_zero_cannot_price_a_bet():
    """Odds now come from the books, not the client. The successor of the old
    placement guard: an unusable stored price is ignored, so the bet is
    refused as unquoted rather than stored at 0 (which crashed grading)."""
    client = _client()
    [gid] = _scheduled_games(client, 1)
    seed_fresh_odds(client.app.state.engine, gid, moneyline_home=0, moneyline_away=0)
    uid = _user(client)
    r = client.post(f"/users/{uid}/picks", json={
        "game_id": gid, "pick_type": "moneyline", "side": "HOME", "stake": 100})
    assert r.status_code == 409, r.text
```

  Leave sections 2 and 3 (grading legacy rows; the owner's grade route)
  intact, converting any bet they place as above. Update the module
  docstring's first guard sentence to say placement now takes its price
  from the books (plan 027).

Then find any stragglers:

Run: `grep -rn '"odds": -110\|pick_value": "HOME\|"Home ML"' backend/tests --include=*.py`
Expected: matches only in tests that write `PaperPick`/`Pick` rows directly
(not request bodies). Inspect each remaining match.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest backend/tests/test_priced_bets.py backend/tests/test_api_users.py backend/tests/test_paper_bet_odds_guard.py backend/tests/test_player_pins.py backend/tests/test_websocket.py backend/tests/test_write_route_coverage.py -q -p no:warnings`
Expected: all pass.

- [ ] **Step 7: Mutation checks** (restore after each)

1. Remove `model_config = ConfigDict(extra="forbid")` from `_Strict` →
   `test_a_client_supplied_price_or_label_is_rejected`,
   `test_a_parlay_leg_with_a_price_is_rejected` and
   `test_the_old_request_shape_is_rejected` fail.
   If the permission classifier blocks editing this security guard, write a
   synthetic-app meta-test instead (a throwaway FastAPI app in the test file
   with the same models minus `extra="forbid"`, asserting the anti-tamper
   request returns 200 there) and record that in the commit.
2. In `place_pick`, replace `odds=quote.odds` with `odds=-110` →
   `test_the_bet_is_charged_the_quoted_consensus` fails.
3. In `place_parlay`, replace `pricing.combine([q.odds for _, q in quotes])`
   with `pricing.combine([-100 for _ in quotes])` →
   `test_a_parlay_is_combined_on_the_server_from_leg_quotes` fails
   (+300, not +264).

- [ ] **Step 8: Full backend suite, both ways**

Run: `.venv/Scripts/python.exe -m pytest backend/tests -q -p no:warnings`
Run: `DB_PATH=/nonexistent/no.db .venv/Scripts/python.exe -m pytest backend/tests -q -p no:warnings`
Expected: both all pass.

- [ ] **Step 9: Commit**

```bash
git add backend/api/users.py backend/tests
git commit -m "feat(paper): the server prices every bet; client odds and labels are refused

<mutation checks and their failing tests>"
```

---

### Task 4: Frontend quote plumbing and side buttons

**Files:**
- Modify: `frontend/vite.config.ts`, `frontend/src/types.ts`,
  `frontend/src/api/client.ts`, `frontend/src/index.css`, `frontend/src/styles.test.ts`
- Create: `frontend/src/lib/quotes.ts`, `frontend/src/lib/quotes.test.ts`,
  `frontend/src/hooks/useQuotes.ts`, `frontend/src/components/QuotePicker.tsx`,
  `frontend/src/components/QuotePicker.test.tsx`,
  `frontend/src/components/PropQuotePicker.tsx`,
  `frontend/src/components/PropQuotePicker.test.tsx`,
  `frontend/src/viteProxy.test.ts`

**Interfaces:**
- Consumes: Task 2's `/paper/quotes`, `/paper/prop-quotes`; Task 3's bodies.
- Produces (used by Tasks 5–6):
  - types: `GamePickType`, `GameSide`, `QuoteFields`, `GameQuote`, `PropQuote`,
    `AvailableGameQuote`, `AvailablePropQuote`, `GameLeg`, `PropLeg`, `BetLeg`,
    `PlacedPick`; `UserProfile.has_pin?: boolean`
  - `api.paper.quotes(gameId)`, `api.paper.propQuotes(gameId)`
  - `lib/quotes.ts`: `formatOdds`, `legFromPick`, `legFromQuote`, `findQuote`,
    `priceMoveNote`, `parlayEstimate`, `ageLabel`
  - hooks: `useGameQuotes(gameId: number | null)`, `usePropQuotes(gameId: number | null)`
  - `<QuotePicker quotes pickType homeName awayName selected onSelect />`
  - `<PropQuotePicker quotes selected onSelect />`

- [ ] **Step 1: Write the failing tests**

`frontend/src/viteProxy.test.ts` (Review Focus 4):

```ts
/// <reference types="node" />
import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const config = readFileSync(join(here, '..', 'vite.config.ts'), 'utf-8')

describe('vite dev proxy', () => {
  it('proxies the /paper API without swallowing the /paper-trading page', () => {
    expect(config).toContain("'^/paper/'")
    expect(config).not.toMatch(/'\/paper':/)
  })
})
```

`frontend/src/lib/quotes.test.ts`:

```ts
import { describe, it, expect } from 'vitest'
import { formatOdds, legFromPick, priceMoveNote, parlayEstimate, ageLabel, findQuote } from './quotes'
import type { GameQuote, PropQuote } from '../types'

describe('legFromPick', () => {
  it('maps a moneyline label to a side', () => {
    expect(legFromPick('moneyline', 'HOME ML', 3)).toEqual({ game_id: 3, pick_type: 'moneyline', side: 'HOME' })
    expect(legFromPick('moneyline', 'AWAY ML', 3)).toEqual({ game_id: 3, pick_type: 'moneyline', side: 'AWAY' })
  })
  it('maps a spread label to a side and ignores its number', () => {
    expect(legFromPick('spread', 'AWAY +3.5', 3)).toEqual({ game_id: 3, pick_type: 'spread', side: 'AWAY' })
  })
  it('maps a total label to Over or Under', () => {
    expect(legFromPick('over_under', 'Over 220.5', 3)).toEqual({ game_id: 3, pick_type: 'over_under', side: 'Over' })
  })
  it('maps a prop label to its prop fields', () => {
    expect(legFromPick('prop', 'Jalen Hurts Over 225.5', 3, 'player_pass_yds', 'Jalen Hurts')).toEqual({
      game_id: 3, pick_type: 'prop', prop_player: 'Jalen Hurts', prop_market: 'player_pass_yds',
      outcome: 'Over', line: 225.5,
    })
  })
  it('returns null for anything it cannot map', () => {
    expect(legFromPick('moneyline', 'Chiefs', 3)).toBeNull()
    expect(legFromPick('prop', 'A.J. Brown Yes', 3, 'player_anytime_td', 'A.J. Brown')).toBeNull()
    expect(legFromPick('parlay', 'HOME ML', 3)).toBeNull()
  })
})

describe('priceMoveNote', () => {
  it('says the price when it did not move', () => {
    expect(priceMoveNote(-110, -110)).toBe('Placed at -110')
  })
  it('says what it was when it moved (Review Focus 5)', () => {
    expect(priceMoveNote(-110, -115)).toBe('Placed at -115 — was -110 when you looked')
  })
})

describe('parlayEstimate', () => {
  it('matches the server formula for two -110 legs', () => {
    expect(parlayEstimate([-110, -110])?.american).toBe(264)
  })
  it('is null with fewer than two legs', () => {
    expect(parlayEstimate([-110])).toBeNull()
  })
})

describe('formatOdds and ageLabel', () => {
  it('signs positive prices', () => {
    expect(formatOdds(150)).toBe('+150')
    expect(formatOdds(-150)).toBe('-150')
  })
  it('describes quote age', () => {
    const now = new Date('2026-10-04T15:00:00Z')
    expect(ageLabel('2026-10-04T14:59:30+00:00', now)).toBe('Prices fetched just now')
    expect(ageLabel('2026-10-04T14:20:00+00:00', now)).toBe('Prices fetched 40m ago')
    expect(ageLabel('2026-10-04T12:00:00+00:00', now)).toBe('Prices fetched 3h ago')
  })
})

describe('findQuote', () => {
  const game: GameQuote[] = [
    { pick_type: 'moneyline', side: 'HOME', available: true, pick_value: 'HOME ML', odds: -120, line: null,
      quoted_at: '2026-10-04T14:00:00+00:00', prop_player: null, prop_market: null },
    { pick_type: 'moneyline', side: 'AWAY', available: false, reason: 'stale', message: 'stale' },
  ]
  const props: PropQuote[] = [
    { prop_player: 'QB', prop_market: 'player_pass_yds', market_label: 'Pass Yards', outcome: 'Over', line: 225.5,
      available: true, pick_type: 'prop', pick_value: 'QB Over 225.5 Pass Yards', odds: -110,
      quoted_at: '2026-10-04T14:00:00+00:00' },
  ]
  it('finds the matching game side and prop line', () => {
    expect(findQuote(game, [], { game_id: 1, pick_type: 'moneyline', side: 'HOME' })?.odds).toBe(-120)
    expect(findQuote([], props, { game_id: 1, pick_type: 'prop', prop_player: 'QB',
      prop_market: 'player_pass_yds', outcome: 'Over', line: 225.5 })?.odds).toBe(-110)
  })
  it('returns the refusal for an unavailable side', () => {
    expect(findQuote(game, [], { game_id: 1, pick_type: 'moneyline', side: 'AWAY' })).toMatchObject({ available: false })
  })
})
```

`frontend/src/components/QuotePicker.test.tsx`:

```tsx
import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import QuotePicker from './QuotePicker'
import type { GameQuote } from '../types'

const quotes: GameQuote[] = [
  { pick_type: 'spread', side: 'HOME', available: true, pick_value: 'HOME -3.5', odds: -112, line: -3.5,
    quoted_at: '2026-10-04T14:00:00+00:00', prop_player: null, prop_market: null },
  { pick_type: 'spread', side: 'AWAY', available: false, reason: 'stale',
    message: 'The price is stale — ask Marcus to refresh.' },
]

describe('QuotePicker', () => {
  it('shows each side with its line and price', () => {
    render(<QuotePicker quotes={quotes} pickType="spread" homeName="Chiefs" awayName="Bills"
      selected={null} onSelect={vi.fn()} />)
    expect(screen.getByRole('button', { name: /Chiefs -3.5\s+-112/ })).toBeEnabled()
  })

  it('disables an unavailable side and shows why', () => {
    render(<QuotePicker quotes={quotes} pickType="spread" homeName="Chiefs" awayName="Bills"
      selected={null} onSelect={vi.fn()} />)
    expect(screen.getByRole('button', { name: /Bills/ })).toBeDisabled()
    expect(screen.getByText('The price is stale — ask Marcus to refresh.')).toBeInTheDocument()
  })

  it('reports the chosen quote', async () => {
    const onSelect = vi.fn()
    render(<QuotePicker quotes={quotes} pickType="spread" homeName="Chiefs" awayName="Bills"
      selected={null} onSelect={onSelect} />)
    await userEvent.setup().click(screen.getByRole('button', { name: /Chiefs/ }))
    expect(onSelect).toHaveBeenCalledWith(quotes[0])
  })

  it('has no price input', () => {
    render(<QuotePicker quotes={quotes} pickType="spread" homeName="Chiefs" awayName="Bills"
      selected={null} onSelect={vi.fn()} />)
    expect(screen.queryByRole('spinbutton')).toBeNull()
    expect(screen.queryByRole('textbox')).toBeNull()
  })
})
```

`frontend/src/components/PropQuotePicker.test.tsx`:

```tsx
import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import PropQuotePicker from './PropQuotePicker'
import type { PropQuote } from '../types'

const base = { prop_market: 'player_pass_yds', market_label: 'Pass Yards', line: 225.5,
  quoted_at: '2026-10-04T14:00:00+00:00', pick_type: 'prop' } as const
const quotes: PropQuote[] = [
  { ...base, prop_player: 'Jalen Hurts', outcome: 'Over', available: true,
    pick_value: 'Jalen Hurts Over 225.5 Pass Yards', odds: -115 },
  { ...base, prop_player: 'Jalen Hurts', outcome: 'Under', available: true,
    pick_value: 'Jalen Hurts Under 225.5 Pass Yards', odds: -105 },
  { ...base, prop_player: 'Josh Allen', outcome: 'Over', available: false,
    reason: 'stale', message: 'The price is stale — ask Marcus to refresh.' } as PropQuote,
]

describe('PropQuotePicker', () => {
  it('shows one row per player/market/line with Over and Under prices', () => {
    render(<PropQuotePicker quotes={quotes} selected={null} onSelect={vi.fn()} />)
    expect(screen.getByRole('button', { name: /Over 225.5\s+-115/ })).toBeEnabled()
    expect(screen.getByRole('button', { name: /Under 225.5\s+-105/ })).toBeEnabled()
  })

  it('filters by the search box', async () => {
    render(<PropQuotePicker quotes={quotes} selected={null} onSelect={vi.fn()} />)
    await userEvent.setup().type(screen.getByLabelText('Search props'), 'allen')
    expect(screen.queryByText('Jalen Hurts')).toBeNull()
    expect(screen.getByText('Josh Allen')).toBeInTheDocument()
  })

  it('disables an unavailable side', () => {
    render(<PropQuotePicker quotes={quotes} selected={null} onSelect={vi.fn()} />)
    const row = screen.getByText('Josh Allen').closest('.prop-quote-row') as HTMLElement
    expect(row.querySelector('button')).toBeDisabled()
  })

  it('reports the chosen quote', async () => {
    const onSelect = vi.fn()
    render(<PropQuotePicker quotes={quotes} selected={null} onSelect={onSelect} />)
    await userEvent.setup().click(screen.getByRole('button', { name: /Under 225.5/ }))
    expect(onSelect).toHaveBeenCalledWith(quotes[1])
  })
})
```

Add to `frontend/src/styles.test.ts`, as a second `describe` block:

```ts
describe('plan 027 styles live in the loaded stylesheet', () => {
  it.each([
    '.quote-sides',
    '.quote-side',
    '.quote-side.selected',
    '.quote-reason',
    '.quote-age',
    '.prop-quote-row',
  ])('index.css defines %s', (selector) => {
    expect(indexCss).toContain(selector)
  })
})
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd frontend && npx vitest run src/lib/quotes.test.ts src/components/QuotePicker.test.tsx src/components/PropQuotePicker.test.tsx src/viteProxy.test.ts src/styles.test.ts`
Expected: FAIL — modules not found; proxy and style assertions fail.

- [ ] **Step 3: Implement**

`frontend/vite.config.ts` — add inside `proxy`, next to `'/users'`:

```ts
      // A regex key: a plain '/paper' prefix would also proxy the
      // /paper-trading page to the API.
      '^/paper/': 'http://localhost:8000',
```

`frontend/src/types.ts` — append:

```ts
export type GamePickType = 'moneyline' | 'spread' | 'over_under';
export type GameSide = 'HOME' | 'AWAY' | 'Over' | 'Under';

export interface QuoteFields {
  pick_type: string;
  pick_value: string;
  odds: number;
  line: number | null;
  quoted_at: string;
  prop_player: string | null;
  prop_market: string | null;
}
type Refusal = { available: false; reason: string; message: string };
type GameKey = { pick_type: GamePickType; side: GameSide };
type PropKey = { prop_player: string; prop_market: string; market_label: string;
  outcome: 'Over' | 'Under'; line: number };

export type AvailableGameQuote = GameKey & { available: true } & Omit<QuoteFields, 'pick_type'>;
export type GameQuote = AvailableGameQuote | (GameKey & Refusal);
export type AvailablePropQuote = PropKey & { available: true; pick_type: 'prop'; pick_value: string;
  odds: number; quoted_at: string };
export type PropQuote = AvailablePropQuote | (PropKey & Refusal);

export type GameLeg = { game_id: number; pick_type: GamePickType; side: GameSide };
export type PropLeg = { game_id: number; pick_type: 'prop'; prop_player: string; prop_market: string;
  outcome: 'Over' | 'Under'; line: number };
export type BetLeg = GameLeg | PropLeg;

export interface PlacedPick {
  id: number; result: string | null; payout: number | null; new_balance: number;
  pick_value: string; odds: number; line: number | null; quoted_at: string;
}
```

and add `has_pin?: boolean;` to `UserProfile`.

`frontend/src/api/client.ts` — add `GameQuote` and `PropQuote` to the type
import and a `paper` group after `props`. (`placePick`/`placeParlay` change
in Task 5, together with their callers, so `tsc` stays green here.)

```ts
  paper: {
    quotes: (gameId: number) => get<{ game_id: number; quotes: GameQuote[] }>(`/paper/quotes?game_id=${gameId}`),
    propQuotes: (gameId: number) =>
      get<{ game_id: number; quotes: PropQuote[] }>(`/paper/prop-quotes?game_id=${gameId}`),
  },
```

`frontend/src/lib/quotes.ts`:

```ts
import type { AvailableGameQuote, AvailablePropQuote, BetLeg, GameQuote, PropQuote } from '../types'

export function formatOdds(odds: number): string {
  return odds > 0 ? `+${odds}` : `${odds}`
}

/** A model pick (Today's Picks, Player Props) as a bet request, or null when
 *  the label can't be mapped to a side the server prices. */
export function legFromPick(pickType: string, pickValue: string, gameId: number,
  propMarket?: string, propPlayer?: string): BetLeg | null {
  if (pickType === 'moneyline' || pickType === 'spread') {
    const side = /\bHOME\b/i.test(pickValue) ? 'HOME' : /\bAWAY\b/i.test(pickValue) ? 'AWAY' : null
    return side ? { game_id: gameId, pick_type: pickType, side } : null
  }
  if (pickType === 'over_under') {
    const side = /\bOver\b/.test(pickValue) ? 'Over' : /\bUnder\b/.test(pickValue) ? 'Under' : null
    return side ? { game_id: gameId, pick_type: 'over_under', side } : null
  }
  if (pickType === 'prop' && propMarket && propPlayer) {
    const m = pickValue.match(/\b(Over|Under)\s+(\d+(?:\.\d+)?)/)
    if (!m) return null
    return { game_id: gameId, pick_type: 'prop', prop_player: propPlayer, prop_market: propMarket,
      outcome: m[1] as 'Over' | 'Under', line: Number(m[2]) }
  }
  return null
}

export function legFromQuote(gameId: number, q: AvailableGameQuote | AvailablePropQuote): BetLeg {
  if ('side' in q) return { game_id: gameId, pick_type: q.pick_type, side: q.side }
  return { game_id: gameId, pick_type: 'prop', prop_player: q.prop_player, prop_market: q.prop_market,
    outcome: q.outcome, line: q.line }
}

export function findQuote(game: GameQuote[], props: PropQuote[], leg: BetLeg): GameQuote | PropQuote | undefined {
  if (leg.pick_type === 'prop') {
    return props.find(q => q.prop_player === leg.prop_player && q.prop_market === leg.prop_market
      && q.outcome === leg.outcome && q.line === leg.line)
  }
  return game.find(q => q.pick_type === leg.pick_type && q.side === leg.side)
}

export function priceMoveNote(shown: number | null, charged: number): string {
  if (shown === null || shown === charged) return `Placed at ${formatOdds(charged)}`
  return `Placed at ${formatOdds(charged)} — was ${formatOdds(shown)} when you looked`
}

/** The same formula as backend pricing.combine; only an estimate -- the server
 *  prices every leg again when the parlay is placed. */
export function parlayEstimate(odds: number[]): { american: number; decimal: number } | null {
  if (odds.length < 2) return null
  const decimal = odds.reduce((acc, o) => acc * (o < 0 ? 1 + 100 / Math.abs(o) : 1 + o / 100), 1)
  const american = decimal >= 2 ? Math.round((decimal - 1) * 100) : Math.round(-100 / (decimal - 1))
  return { american, decimal }
}

export function ageLabel(quotedAt: string, now: Date = new Date()): string {
  const minutes = Math.floor((now.getTime() - new Date(quotedAt).getTime()) / 60000)
  if (minutes < 1) return 'Prices fetched just now'
  if (minutes < 60) return `Prices fetched ${minutes}m ago`
  return `Prices fetched ${Math.floor(minutes / 60)}h ago`
}
```

Note `Math.round` rounds .5 up while Python's `round` rounds half to even;
the estimate can differ by 1 on an exact half, which is why it is labelled
an estimate.

`frontend/src/hooks/useQuotes.ts`:

```ts
import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'

// Quotes refetch every minute and on window focus so a price on screen is
// never far from the price the server will charge.
const LIVE = { refetchInterval: 60_000, refetchOnWindowFocus: true } as const

export function useGameQuotes(gameId: number | null) {
  return useQuery({
    queryKey: ['paper', 'quotes', gameId],
    queryFn: () => api.paper.quotes(gameId as number),
    enabled: gameId != null,
    ...LIVE,
  })
}

export function usePropQuotes(gameId: number | null) {
  return useQuery({
    queryKey: ['paper', 'prop-quotes', gameId],
    queryFn: () => api.paper.propQuotes(gameId as number),
    enabled: gameId != null,
    ...LIVE,
  })
}
```

`frontend/src/components/QuotePicker.tsx`:

```tsx
import type { AvailableGameQuote, GamePickType, GameQuote, GameSide } from '../types'
import { formatOdds } from '../lib/quotes'

interface Props {
  quotes: GameQuote[]
  pickType: GamePickType
  homeName: string
  awayName: string
  selected: GameSide | null
  onSelect: (quote: AvailableGameQuote) => void
}

function sideLabel(q: GameQuote, homeName: string, awayName: string): string {
  const team = q.side === 'HOME' ? homeName : q.side === 'AWAY' ? awayName : q.side
  if (!q.available || q.line === null) return team
  if (q.pick_type === 'spread') return `${team} ${q.line > 0 ? '+' : ''}${q.line}`
  return `${team} ${q.line}`
}

export default function QuotePicker({ quotes, pickType, homeName, awayName, selected, onSelect }: Props) {
  const sides = quotes.filter(q => q.pick_type === pickType)
  return (
    <div className="quote-sides">
      {sides.map(q => (
        <div key={q.side} className="quote-side-wrap">
          <button
            type="button"
            className={`quote-side${selected === q.side ? ' selected' : ''}`}
            disabled={!q.available}
            aria-pressed={selected === q.side}
            onClick={() => { if (q.available) onSelect(q) }}
          >
            <span>{sideLabel(q, homeName, awayName)}</span>{' '}
            <span className="mono">{q.available ? formatOdds(q.odds) : '—'}</span>
          </button>
          {!q.available && <div className="quote-reason">{q.message}</div>}
        </div>
      ))}
    </div>
  )
}
```

`frontend/src/components/PropQuotePicker.tsx`:

```tsx
import { useState } from 'react'
import type { AvailablePropQuote, PropQuote } from '../types'
import { formatOdds } from '../lib/quotes'

interface Props {
  quotes: PropQuote[]
  selected: AvailablePropQuote | null
  onSelect: (quote: AvailablePropQuote) => void
}

const rowKey = (q: PropQuote) => `${q.prop_player}|${q.prop_market}|${q.line}`

/** The first unavailable side's reason, or null. */
function refusal(sides: PropQuote[]): string | null {
  for (const q of sides) if (!q.available) return q.message
  return null
}

export default function PropQuotePicker({ quotes, selected, onSelect }: Props) {
  const [search, setSearch] = useState('')
  const needle = search.trim().toLowerCase()
  const rows = new Map<string, PropQuote[]>()
  for (const q of quotes) {
    if (needle && !`${q.prop_player} ${q.market_label}`.toLowerCase().includes(needle)) continue
    rows.set(rowKey(q), [...(rows.get(rowKey(q)) ?? []), q])
  }
  return (
    <div>
      <input className="input" aria-label="Search props" placeholder="Search by player or market..."
        value={search} onChange={e => setSearch(e.target.value)} />
      <div className="prop-quote-list">
        {[...rows.entries()].slice(0, 40).map(([key, sides]) => (
          <div key={key} className="prop-quote-row">
            <span className="font-medium">{sides[0].prop_player}</span>
            <span className="text-muted"> {sides[0].market_label}</span>
            <div className="quote-sides">
              {sides.map(q => (
                <button
                  key={q.outcome}
                  type="button"
                  className={`quote-side${selected && rowKey(selected) === key && selected.outcome === q.outcome ? ' selected' : ''}`}
                  disabled={!q.available}
                  title={q.available ? undefined : q.message}
                  onClick={() => { if (q.available) onSelect(q) }}
                >
                  <span>{q.outcome} {q.line}</span>{' '}
                  <span className="mono">{q.available ? formatOdds(q.odds) : '—'}</span>
                </button>
              ))}
            </div>
            {refusal(sides) && <div className="quote-reason">{refusal(sides)}</div>}
          </div>
        ))}
        {rows.size === 0 && <div className="text-muted">No props quoted for this game.</div>}
      </div>
    </div>
  )
}
```

`frontend/src/index.css` — append:

```css
/* Plan 027: server-priced bet sides */
.quote-sides {
  display: flex;
  gap: 0.5rem;
  flex-wrap: wrap;
}
.quote-side {
  display: inline-flex;
  gap: 0.5rem;
  align-items: center;
  padding: 0.45rem 0.8rem;
  border: 1px solid var(--border-default);
  border-radius: var(--radius-md);
  background: var(--bg-elevated);
  color: var(--text-primary);
  cursor: pointer;
  font-size: 0.85rem;
  transition: var(--transition);
}
.quote-side:hover:not(:disabled) {
  background: var(--bg-hover);
}
.quote-side.selected {
  border-color: var(--accent);
  background: var(--bg-active);
}
.quote-side:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.quote-reason {
  font-size: 0.75rem;
  color: var(--text-muted);
  margin-top: 0.25rem;
}
.quote-age {
  font-size: 0.75rem;
  color: var(--text-muted);
}
.prop-quote-list {
  max-height: 320px;
  overflow-y: auto;
  margin-top: 0.5rem;
}
.prop-quote-row {
  padding: 0.5rem 0;
  border-bottom: 1px solid var(--border-subtle);
}
.prop-quote-row .quote-sides {
  margin-top: 0.35rem;
}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd frontend && npx vitest run && npx tsc -b --noEmit && npx eslint .`
Expected: all clean, all tests pass.

- [ ] **Step 5: Mutation check**

Change the proxy key to `'/paper'` → `viteProxy.test.ts` fails. Restore.

- [ ] **Step 6: Commit**

```bash
git add frontend/vite.config.ts frontend/src/types.ts frontend/src/api/client.ts frontend/src/lib/quotes.ts frontend/src/lib/quotes.test.ts frontend/src/hooks/useQuotes.ts frontend/src/components/QuotePicker.tsx frontend/src/components/QuotePicker.test.tsx frontend/src/components/PropQuotePicker.tsx frontend/src/components/PropQuotePicker.test.tsx frontend/src/viteProxy.test.ts frontend/src/index.css frontend/src/styles.test.ts
git commit -m "feat(frontend): quote types, api.paper, side buttons for server-priced bets"
```

---

### Task 5: Paper Trading and the "Bet this pick" popup use quotes

**Files:**
- Modify: `frontend/src/api/client.ts`, `frontend/src/pages/PaperTrading.tsx`,
  `frontend/src/components/BetModal.tsx`, `frontend/src/components/BetModal.test.tsx`

**Interfaces:**
- Consumes: everything Task 4 produces.
- Produces: `api.users.placePick(userId, BetLeg & {stake}, pin) -> PlacedPick`,
  `api.users.placeParlay(userId, {legs: BetLeg[]; stake}, pin)`. `BetModal`'s
  props are unchanged, so `TodaysPicks.tsx` and `PlayerProps.tsx` need no edit.

- [ ] **Step 0: Change the two bet calls in the client**

In `frontend/src/api/client.ts`, add `BetLeg` and `PlacedPick` to the type
import and replace `placePick` and `placeParlay`:

```ts
    placePick: (userId: number, data: BetLeg & { stake: number }, pin: string) =>
      post<PlacedPick>(`/users/${userId}/picks`, data, { 'X-Player-Pin': pin }),
```

```ts
    placeParlay: (userId: number, data: { legs: BetLeg[]; stake: number }, pin: string) => post<{
      id: number;
      legs: Array<{ pick_value: string; odds: number; line: number | null; quoted_at: string; result: string | null }>;
      combined_odds: number; potential_payout: number;
      result: string | null; payout: number | null; new_balance: number;
    }>(`/users/${userId}/parlay`, data, { 'X-Player-Pin': pin }),
```

`tsc` now fails in `PaperTrading.tsx` and `BetModal.tsx`; Steps 2–3 fix them.

- [ ] **Step 1: Update the BetModal tests first**

In `BetModal.test.tsx`:
- extend the `vi.mock` so `api` also has
  `paper: { quotes: vi.fn(), propQuotes: vi.fn() }`;
- in `beforeEach`, resolve a quote for game 1:

```ts
    vi.mocked(api.paper.quotes).mockResolvedValue({ game_id: 1, quotes: [
      { pick_type: 'moneyline', side: 'HOME', available: true, pick_value: 'HOME ML', odds: -140,
        line: null, quoted_at: new Date().toISOString(), prop_player: null, prop_market: null },
    ] })
    vi.mocked(api.paper.propQuotes).mockResolvedValue({ game_id: 1, quotes: [] })
```

- in the in-flight test, change the expectation to the new body:

```ts
    expect(api.users.placePick).toHaveBeenCalledWith(
      1,
      { game_id: 1, pick_type: 'moneyline', side: 'HOME', stake: 100 },
      '1234'
    )
```

  and resolve with the new response shape
  `{ id: 1, result: null, payout: null, new_balance: 9900, pick_value: 'HOME ML', odds: -140, line: null, quoted_at: '' }`
  (update the local `PlacePickResult` type to match).
- add two tests:

```ts
  it('shows the current price and notes when it differs from the model price', async () => {
    useUserStore.setState({ currentUserName: 'Marcus' })
    vi.mocked(api.users.list).mockResolvedValue([makeUser()])
    renderModal()          // model price -150, quote -140
    expect(await screen.findByText('-140')).toBeInTheDocument()
    expect(screen.getByText(/model priced this at -150/i)).toBeInTheDocument()
  })

  it('cannot confirm a pick the server will not price', async () => {
    useUserStore.setState({ currentUserName: 'Marcus' })
    vi.mocked(api.users.list).mockResolvedValue([makeUser()])
    vi.mocked(api.paper.quotes).mockResolvedValue({ game_id: 1, quotes: [
      { pick_type: 'moneyline', side: 'HOME', available: false, reason: 'stale',
        message: 'The price is stale — ask Marcus to refresh.' },
    ] })
    renderModal()
    expect(await screen.findByText('The price is stale — ask Marcus to refresh.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Confirm/ })).toBeDisabled()
  })
```

Run: `cd frontend && npx vitest run src/components/BetModal.test.tsx`
Expected: FAIL (the modal does not fetch quotes yet).

- [ ] **Step 2: Rewire BetModal**

In `BetModal.tsx`:

```tsx
import { useGameQuotes, usePropQuotes } from '../hooks/useQuotes'
import { findQuote, formatOdds, legFromPick, priceMoveNote } from '../lib/quotes'
```

After the existing state, derive the leg and its current quote (hooks run
unconditionally, before the `if (!open) return null`):

```tsx
  const leg = legFromPick(pickType, pickValue, gameId, propMarket, propPlayer)
  const isProp = leg?.pick_type === 'prop'
  const gameQuotes = useGameQuotes(open && leg && !isProp ? gameId : null)
  const propQuotes = usePropQuotes(open && leg && isProp ? gameId : null)
  const quote = leg
    ? findQuote(gameQuotes.data?.quotes ?? [], propQuotes.data?.quotes ?? [], leg)
    : undefined
  const quotesLoading = gameQuotes.isLoading || propQuotes.isLoading
```

In `handlePlaceBet`, send the leg and report the charged price:

```tsx
    if (!userId || !leg || !quote?.available) return
    ...
      const res = await api.users.placePick(userId, { ...leg, stake }, pin)
      ...
      toast(priceMoveNote(quote.odds, res.odds), 'success')
```

Replace the "Odds" detail row with:

```tsx
              <div className="bet-detail-row">
                <span className="bet-detail-label">Price now</span>
                <span className="bet-detail-value mono">
                  {quote?.available ? formatOdds(quote.odds) : '—'}
                </span>
              </div>
              {quote?.available && quote.odds !== odds && (
                <div className="quote-reason">The model priced this at {formatOdds(odds)}.</div>
              )}
              {!leg && <div className="quote-reason">This pick can't be bet here.</div>}
              {leg && !quotesLoading && !quote && (
                <div className="quote-reason">No book is quoting this bet right now.</div>
              )}
              {quote && !quote.available && <div className="quote-reason">{quote.message}</div>}
```

and disable Confirm with
`disabled={submitting || stake <= 0 || !quote?.available}`. Delete
`oddsStr`. Keep the `odds` prop — it is now "the model's price".

Run: `cd frontend && npx vitest run src/components/BetModal.test.tsx`
Expected: PASS.

- [ ] **Step 3: Rewire PaperTrading**

In `PaperTrading.tsx`:

1. Imports: drop `PropData` from the types import; add
   `import type { AvailableGameQuote, AvailablePropQuote, BetLeg, GamePickType } from '../types';`,
   `import QuotePicker from '../components/QuotePicker';`,
   `import PropQuotePicker from '../components/PropQuotePicker';`,
   `import { useGameQuotes, usePropQuotes } from '../hooks/useQuotes';`,
   `import { ageLabel, formatOdds, legFromQuote, parlayEstimate, priceMoveNote } from '../lib/quotes';`.
2. `const { games: gamesQuery, feed: feedQuery } = usePaperTradingData();`
   and delete `const props = …`.
3. Replace the "Place pick form state", "Parlay builder state" and
   "Prop-specific state" blocks with:

```tsx
  // Place pick form state. The price is never typed: the player picks a side
  // the server has quoted, and the server prices it again on placement.
  const [selectedGame, setSelectedGame] = useState<number | null>(null);
  const [pickType, setPickType] = useState<GamePickType | 'prop'>('moneyline');
  const [chosen, setChosen] = useState<AvailableGameQuote | AvailablePropQuote | null>(null);
  const [stake, setStake] = useState(100);
  const gameQuotes = useGameQuotes(selectedGame);
  const propQuotes = usePropQuotes(pickType === 'prop' ? selectedGame : null);
  const game = games.find(g => g.id === selectedGame);
  const newestQuote = (gameQuotes.data?.quotes ?? [])
    .flatMap(q => (q.available ? [q.quoted_at] : []))
    .sort()
    .pop();

  // Parlay builder state
  type ParlayLeg = { leg: BetLeg; label: string; sport: string; odds: number };
  const [parlayLegs, setParlayLegs] = useState<ParlayLeg[]>([]);
  const [parlayStake, setParlayStake] = useState(100);
```

4. Replace `handlePlacePick`, `addParlayLeg`, `removeParlayLeg`,
   `parlayDecimalOdds`, `parlayAmericanOdds`, `handlePlaceParlay`,
   `handleGameSelect`, `handlePickTypeChange`, `uniqueProps`,
   `filteredProps`, `propOptions` and `selectedProp` with:

```tsx
  const forgetPinOn401 = (e: unknown) => {
    if (e instanceof ApiError && e.status === 401 && selectedUser) {
      setPin(selectedUser.id, null);
      setBetPin('');
    }
  };

  const handlePlacePick = async () => {
    if (!selectedUser || selectedGame === null || !chosen) return;
    try {
      const res = await api.users.placePick(selectedUser.id,
        { ...legFromQuote(selectedGame, chosen), stake }, betPin);
      setPin(selectedUser.id, betPin);
      toast(`${priceMoveNote(chosen.odds, res.odds)}. Balance: $${res.new_balance.toLocaleString()}`, 'success');
      queryClient.invalidateQueries({ queryKey: ['users'] });
      setChosen(null);
    } catch (e) {
      forgetPinOn401(e);
      toast(getErrorMessage(e), 'error');
    }
  };

  const addParlayLeg = () => {
    if (selectedGame === null || !chosen) return;
    const matchup = game ? `${game.away_team}@${game.home_team} ` : '';
    setParlayLegs(prev => [...prev, {
      leg: legFromQuote(selectedGame, chosen),
      label: `${matchup}${chosen.pick_value}`,
      sport: game?.sport ?? '',
      odds: chosen.odds,
    }]);
    setChosen(null);
  };

  const removeParlayLeg = (index: number) => {
    setParlayLegs(prev => prev.filter((_, i) => i !== index));
  };

  const estimate = parlayEstimate(parlayLegs.map(l => l.odds));

  const handlePlaceParlay = async () => {
    if (!selectedUser || parlayLegs.length < 2) return;
    try {
      const res = await api.users.placeParlay(selectedUser.id, {
        legs: parlayLegs.map(l => l.leg), stake: parlayStake }, betPin);
      setPin(selectedUser.id, betPin);
      toast(`${parlayLegs.length}-leg parlay placed at ${formatOdds(res.combined_odds)}. `
        + `Potential: $${res.potential_payout.toLocaleString()}`, 'success');
      queryClient.invalidateQueries({ queryKey: ['users'] });
      setParlayLegs([]);
    } catch (e) {
      forgetPinOn401(e);
      toast(getErrorMessage(e), 'error');
    }
  };

  const handleGameSelect = (gameId: number | null) => {
    setSelectedGame(gameId);
    setChosen(null);
  };

  const handlePickTypeChange = (type: GamePickType | 'prop') => {
    setPickType(type);
    setChosen(null);
  };
```

Delete the now-unused `setParlayResult` state if nothing else reads it.

5. Replace the whole "Place Pick Form" card body — from the "Pick Type
   Selector" through the end of the "Selected prop summary" block — with:

```tsx
            <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap', alignItems: 'end', marginBottom: '0.75rem' }}>
              <div style={{ flex: 2, minWidth: '180px' }}>
                <div className="input-label">Game</div>
                <select className="input" value={selectedGame ?? ''}
                  onChange={e => handleGameSelect(e.target.value ? Number(e.target.value) : null)}>
                  <option value="">Select game...</option>
                  {games.map(g => (
                    <option key={g.id} value={g.id}>
                      {g.away_team} @ {g.home_team} ({g.sport.toUpperCase()})
                    </option>
                  ))}
                </select>
              </div>
              {newestQuote && <span className="quote-age">{ageLabel(newestQuote)}</span>}
            </div>

            <div style={{ display: 'flex', gap: '0.5rem', marginBottom: '0.75rem', flexWrap: 'wrap' }}>
              {(['moneyline', 'spread', 'over_under', 'prop'] as const).map(t => (
                <button
                  key={t}
                  className={`btn ${pickType === t ? 'btn-primary' : 'btn-ghost'}`}
                  onClick={() => handlePickTypeChange(t)}
                  style={{ fontSize: '0.8rem', padding: '0.35rem 0.75rem' }}
                >
                  {t === 'over_under' ? 'Total' : t === 'prop' ? 'Prop' : t.charAt(0).toUpperCase() + t.slice(1)}
                </button>
              ))}
            </div>

            {selectedGame === null ? (
              <div className="text-muted">Choose a game to see its prices.</div>
            ) : pickType === 'prop' ? (
              <PropQuotePicker
                quotes={propQuotes.data?.quotes ?? []}
                selected={chosen && !('side' in chosen) ? chosen : null}
                onSelect={setChosen}
              />
            ) : (
              <QuotePicker
                quotes={gameQuotes.data?.quotes ?? []}
                pickType={pickType}
                homeName={game?.home_team ?? 'Home'}
                awayName={game?.away_team ?? 'Away'}
                selected={chosen && 'side' in chosen ? chosen.side : null}
                onSelect={setChosen}
              />
            )}

            {chosen && (
              <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap', alignItems: 'end', marginTop: '0.75rem' }}>
                <div style={{ minWidth: '120px' }}>
                  <div className="input-label">Stake ($)</div>
                  <input className="input" type="number" value={stake}
                    onChange={e => setStake(Number(e.target.value))} min={1} />
                </div>
                <div style={{ minWidth: '120px' }}>
                  <div className="input-label">PIN</div>
                  <input
                    aria-label="PIN"
                    className="input"
                    type="password"
                    inputMode="numeric"
                    autoComplete="off"
                    maxLength={6}
                    placeholder={'PIN (4–6 digits)'}
                    value={betPin}
                    onChange={e => setBetPin(e.target.value)}
                  />
                </div>
                <button className="btn btn-primary" onClick={handlePlacePick}>
                  Place {chosen.pick_value} {formatOdds(chosen.odds)}
                </button>
              </div>
            )}
```

6. In the Parlay Builder: the "+ Add Leg" button becomes
   `disabled={!chosen}` with hint text
   `Pick a side above, then "Add Leg" to build your parlay`; in the legs
   list render `leg.label` and `formatOdds(leg.odds)`; in the summary replace
   the "Combined Odds" value with
   `{estimate ? formatOdds(estimate.american) : '—'}` and add beneath it
   `<div className="quote-reason">estimate — confirmed when placed</div>`;
   "Potential Win" becomes
   `estimate ? `$${(parlayStake * (estimate.decimal - 1)).toLocaleString(undefined, { maximumFractionDigits: 0 })}` : '—'`.

- [ ] **Step 4: Verify**

Run: `cd frontend && npx tsc -b --noEmit && npx vitest run && npx eslint .`
Expected: clean, all tests pass.

Run: `grep -n "pickOdds\|setPickValue\|-110" frontend/src/pages/PaperTrading.tsx`
Expected: no output (no typed price, no −110 default remains).

- [ ] **Step 5: Mutation check**

In `BetModal.tsx` remove `|| !quote?.available` from the Confirm button's
`disabled` → `cannot confirm a pick the server will not price` fails. Restore.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/api/client.ts frontend/src/pages/PaperTrading.tsx frontend/src/components/BetModal.tsx frontend/src/components/BetModal.test.tsx
git commit -m "feat(frontend): bets are placed by choosing a quoted side; no typed odds or lines"
```

---

### Task 6: Admin "Set PIN" and the "No PIN" badge

**Files:**
- Create: `frontend/src/components/SetPinControl.tsx`,
  `frontend/src/components/SetPinControl.test.tsx`, `frontend/src/pages/Admin.test.tsx`
- Modify: `frontend/src/pages/Admin.tsx` (name cell ~line 164, Actions cell
  ~lines 183–201), `frontend/src/index.css`, `frontend/src/styles.test.ts`

**Interfaces:**
- Consumes: `api.users.setPin(id, pin)` (exists), `UserProfile.has_pin` (Task 4 type, Task 2 API).
- Produces: `<SetPinControl userId userName hasOwnerKey onSaved />`.

- [ ] **Step 1: Write the failing tests**

`frontend/src/components/SetPinControl.test.tsx`:

```tsx
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import SetPinControl from './SetPinControl'
import { ToastProvider } from './Toast'
import { api, ApiError } from '../api/client'

vi.mock('../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/client')>()
  return { ...actual, api: { users: { setPin: vi.fn() } } }
})

function renderControl(hasOwnerKey = true, onSaved = vi.fn()) {
  render(<ToastProvider><SetPinControl userId={2} userName="Demo" hasOwnerKey={hasOwnerKey} onSaved={onSaved} /></ToastProvider>)
  return onSaved
}

describe('SetPinControl', () => {
  beforeEach(() => vi.clearAllMocks())

  it('sends the PIN only on Save, exactly as typed', async () => {
    vi.mocked(api.users.setPin).mockResolvedValue({ id: 2, pin_set: true })
    const user = userEvent.setup()
    const onSaved = renderControl()
    await user.click(screen.getByRole('button', { name: 'Set PIN' }))
    await user.type(screen.getByLabelText('New PIN for Demo'), '0123')
    expect(api.users.setPin).not.toHaveBeenCalled()
    await user.click(screen.getByRole('button', { name: 'Save' }))
    expect(api.users.setPin).toHaveBeenCalledWith(2, '0123')
    expect(onSaved).toHaveBeenCalled()
    expect(screen.queryByLabelText('New PIN for Demo')).toBeNull()
  })

  it('will not save a malformed PIN', async () => {
    const user = userEvent.setup()
    renderControl()
    await user.click(screen.getByRole('button', { name: 'Set PIN' }))
    await user.type(screen.getByLabelText('New PIN for Demo'), '12')
    expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled()
  })

  it('asks for the owner key when none is saved', async () => {
    const user = userEvent.setup()
    renderControl(false)
    await user.click(screen.getByRole('button', { name: 'Set PIN' }))
    expect(screen.getByText(/Save the owner key above first/)).toBeInTheDocument()
    expect(screen.queryByLabelText('New PIN for Demo')).toBeNull()
  })

  it('reports a rejected owner key', async () => {
    vi.mocked(api.users.setPin).mockRejectedValue(new ApiError(403, { detail: 'Forbidden' }))
    const user = userEvent.setup()
    renderControl()
    await user.click(screen.getByRole('button', { name: 'Set PIN' }))
    await user.type(screen.getByLabelText('New PIN for Demo'), '4321')
    await user.click(screen.getByRole('button', { name: 'Save' }))
    expect(await screen.findByText(/Owner key required/)).toBeInTheDocument()
  })

  it('Cancel closes without sending', async () => {
    const user = userEvent.setup()
    renderControl()
    await user.click(screen.getByRole('button', { name: 'Set PIN' }))
    await user.type(screen.getByLabelText('New PIN for Demo'), '4321')
    await user.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(api.users.setPin).not.toHaveBeenCalled()
  })
})
```

`frontend/src/pages/Admin.test.tsx`:

```tsx
import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import Admin from './Admin'
import { ToastProvider } from '../components/Toast'
import { api } from '../api/client'
import type { UserProfile } from '../types'

vi.mock('../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/client')>()
  return { ...actual, api: { users: { list: vi.fn(), delete: vi.fn(), setPin: vi.fn() } } }
})

const user = (id: number, name: string, has_pin: boolean): UserProfile => ({
  id, name, has_pin, starting_balance: 10000, current_balance: 10000, total_wagered: 0, profit: 0,
  roi: 0, wins: 0, losses: 0, pushes: 0, pending: 0, win_rate: 0, current_streak: 0,
  best_streak: 0, streak_type: 'none',
})

describe('Admin', () => {
  it('shows No PIN exactly where a player has none', async () => {
    vi.mocked(api.users.list).mockResolvedValue([user(1, 'Marcus', false), user(3, 'Sam', true)])
    render(<ToastProvider><Admin /></ToastProvider>)
    expect(await screen.findByText('Marcus')).toBeInTheDocument()
    expect(screen.getAllByText('No PIN')).toHaveLength(1)
    expect(screen.getByText('Marcus').closest('td')).toHaveTextContent('No PIN')
    expect(screen.getAllByRole('button', { name: 'Set PIN' })).toHaveLength(2)
  })
})
```

If `Admin` needs a router context (it uses `window.location` only — check
for `useNavigate`/`Link`), wrap it in `MemoryRouter` from `react-router-dom`.

Add `'.no-pin-badge'` and `'.set-pin'` to the plan 027 `it.each` list in
`styles.test.ts`.

Run: `cd frontend && npx vitest run src/components/SetPinControl.test.tsx src/pages/Admin.test.tsx src/styles.test.ts`
Expected: FAIL.

- [ ] **Step 2: Implement**

`frontend/src/components/SetPinControl.tsx`:

```tsx
import { useState } from 'react'
import { api, ApiError, getErrorMessage } from '../api/client'
import { useToast } from '../hooks/useToast'

interface Props {
  userId: number
  userName: string
  hasOwnerKey: boolean
  onSaved: () => void
}

/** Owner-only: set or reset a player's PIN inline. No browser dialog, and the
 *  PIN is never logged or shown in a toast. */
export default function SetPinControl({ userId, userName, hasOwnerKey, onSaved }: Props) {
  const [open, setOpen] = useState(false)
  const [pin, setPinValue] = useState('')
  const [saving, setSaving] = useState(false)
  const { toast } = useToast()

  const close = () => { setOpen(false); setPinValue('') }

  const save = async () => {
    setSaving(true)
    try {
      await api.users.setPin(userId, pin)
      toast(`PIN set for ${userName}`, 'success')
      close()
      onSaved()
    } catch (e) {
      toast(e instanceof ApiError && e.status === 403
        ? 'Owner key required — add it above' : getErrorMessage(e), 'error')
    } finally {
      setSaving(false)
    }
  }

  if (!open) {
    return <button className="btn-bet set-pin" style={{ fontSize: '0.75rem' }}
      onClick={() => setOpen(true)}>Set PIN</button>
  }
  if (!hasOwnerKey) {
    return (
      <span className="set-pin">
        <span className="text-muted" style={{ fontSize: '0.75rem' }}>Save the owner key above first.</span>{' '}
        <button className="btn-bet" style={{ fontSize: '0.7rem' }} onClick={close}>Cancel</button>
      </span>
    )
  }
  return (
    <span className="set-pin">
      <input
        aria-label={`New PIN for ${userName}`}
        className="input"
        type="password"
        inputMode="numeric"
        autoComplete="off"
        maxLength={6}
        placeholder="4–6 digits"
        value={pin}
        onChange={e => setPinValue(e.target.value)}
      />
      <button className="btn-bet" style={{ fontSize: '0.7rem' }}
        disabled={saving || !/^\d{4,6}$/.test(pin)} onClick={save}>Save</button>
      <button className="btn-bet" style={{ fontSize: '0.7rem' }} onClick={close}>Cancel</button>
    </span>
  )
}
```

In `Admin.tsx`:
- `import SetPinControl from '../components/SetPinControl';`
- name cell:

```tsx
                  <td className="font-medium">
                    {u.name}
                    {u.has_pin === false && <span className="badge badge-red no-pin-badge">No PIN</span>}
                  </td>
```

- in the Actions cell, wrap the existing delete controls and the new control
  in `<div style={{ display: 'flex', gap: '0.25rem', flexWrap: 'wrap' }}>`,
  adding before the delete controls:

```tsx
                      <SetPinControl userId={u.id} userName={u.name}
                        hasOwnerKey={hasOwnerKey} onSaved={loadUsers} />
```

`frontend/src/index.css` — append:

```css
.no-pin-badge {
  margin-left: 0.5rem;
}
.set-pin {
  display: inline-flex;
  gap: 0.25rem;
  align-items: center;
}
.set-pin .input {
  width: 7rem;
  padding: 0.25rem 0.5rem;
  font-size: 0.8rem;
}
```

- [ ] **Step 3: Verify**

Run: `cd frontend && npx tsc -b --noEmit && npx vitest run && npx eslint .`
Expected: clean, all pass.

- [ ] **Step 4: Mutation check**

Change the badge condition `u.has_pin === false` to `true` →
`shows No PIN exactly where a player has none` fails (two badges). Restore.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/SetPinControl.tsx frontend/src/components/SetPinControl.test.tsx frontend/src/pages/Admin.tsx frontend/src/pages/Admin.test.tsx frontend/src/index.css frontend/src/styles.test.ts
git commit -m "feat(admin): set a player's PIN inline; badge players with no PIN"
```

---

### Task 7: Visual check (controller, not an implementer)

Never against the live db, never pressing "Spend credits and refresh".

- [ ] **Step 1:** Snapshot the live db with `sqlite3 .backup` (not `cp` —
  WAL) to the scratchpad, then on the copy only:
  `UPDATE odds SET timestamp = datetime('now'); UPDATE player_props SET fetched_at = datetime('now');`
  and, for one game, `UPDATE odds SET timestamp = datetime('now','-7 hours') WHERE game_id = <id>;`
  so fresh and stale states both show.
- [ ] **Step 2:** Build the frontend and serve the copy on :8001 with
  `ENABLE_SCHEDULER=0` and `DATABASE_PATH=<copy>`.
- [ ] **Step 3:** In Chrome (desktop and 414 px wide): Paper Trading → pick a
  game → each tab shows side buttons with line and price; the stale game
  shows disabled sides with the stale message; the Prop tab lists NFL
  yardage props and no anytime-TD or MLB rows; no Odds or Line input exists;
  the parlay shows "estimate — confirmed when placed". Today's Picks → "Bet
  this pick" shows "Price now". Admin → Set PIN opens an inline field;
  "No PIN" shows on the legacy players. Use computed styles where a
  screenshot stalls. Do not submit bets or PINs — this is a copy, but the
  rule is "look, don't place".
- [ ] **Step 4:** Stop the :8001 server; delete the copy.

**Post-merge (owner):** restart the backend so the new routes are live; in
Admin, paste the owner key, then Set PIN for Marcus (id 1) and Demo (id 2);
run `scripts/share.ps1` and verify the public URL with real requests
(`/paper/quotes?game_id=<today's game>` returns prices).

---

## Self-review

- **Spec coverage:** pricing module and refusals (T1); quote endpoints,
  `/games/today` consensus, `has_pin` (T2); new bodies, 422 for extra
  fields, 409s, server-side parlay, priced response (T3); side buttons,
  disabled reasons, no Odds/Line inputs, 60 s refetch and focus refetch,
  "Prices fetched Xh ago", prop list, parlay estimate, moved-price toast (T4,
  T5); popup maps `HOME ML`/`AWAY +3.5`/`Over 220.5`/prop labels (T4 tests,
  T5 wiring); Set PIN inline with owner-key message and No PIN badge (T6);
  visual check with bumped timestamps (T7). Testing strategy items: hand
  values, 5h59m/6h01m, not_quoted with no −110, props (unquoted, consensus,
  ungradeable), started game, round trip (T1 unit, T3 API), anti-tamper with
  mutation check, charged = quoted, moved price, parlay combine, games/today
  agrees, has_pin without secrets, write-route coverage.
- **Deviation from the spec, deliberate:** anytime-TD props are excluded as
  ungradeable (the spec named only MLB markets); the grader cannot parse
  their labels. `_open_for_betting` moved into the pricing module (the spec
  said "reused, unchanged") so there is one definition — behaviour is
  identical, now with an injectable clock.
- **Type consistency:** `GameBet/PropBet/Quote/PricingError` (T1) are what
  T2/T3 import; `AvailableGameQuote/AvailablePropQuote/BetLeg` (T4) are what
  T5 uses; `api.paper.quotes/propQuotes` and `useGameQuotes/usePropQuotes`
  names match across T4–T5.

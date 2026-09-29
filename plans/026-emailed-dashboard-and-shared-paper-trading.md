# Plan 026: Emailed-picks dashboard and shared paper trading

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
> `git diff --stat 545de44..HEAD -- backend/api backend/digest/record.py backend/models.py backend/database.py backend/pipeline/scheduler.py backend/config.py backend/tests/conftest.py backend/tests/test_api_users.py frontend/src`
> Expected: empty. On a mismatch, treat it as a STOP condition.

**Goal:** A dashboard of how the emailed picks actually did (by week, month and
stars, with honest small-sample context), a leaderboard where friends' paper
bets compete with the model, and a PIN- and owner-key-protected link that
lets friends reach the app on this laptop.

**Architecture:** One pure module, `backend/analysis/scorecard.py`, defines
every metric over a list of `Bet`s. Emailed picks and players' paper bets are
each adapted into `Bet`s and fed through it, so every page computes win rate
and ROI the same way. Writes are guarded by two FastAPI dependencies —
`require_owner` and `require_player_pin` — and a test enumerates every write
route to prove none is unguarded. The link is a Cloudflare quick tunnel to the
existing uvicorn server.

**Tech Stack:** Python 3.14 locally (CI: 3.12 and 3.14), FastAPI,
SQLAlchemy/SQLite, pytest; React 19 + Vite + TypeScript, @tanstack/react-query,
Recharts, vitest; PowerShell 7; `cloudflared` (already installed).

**Spec:** `docs/superpowers/specs/2026-09-28-dashboard-and-paper-trading-design.md`
(commit `545de44`). Read it before starting. This plan also fixes three
paper-trading defects the spec did not know about (Task 6), found while
planning and presented to the owner with this plan for approval.

## Status

- **Priority**: P1
- **Effort**: L (11 tasks)
- **Risk**: HIGH for Tasks 4, 5, 11 — they decide who can change data once the
  app is reachable from the internet. MED elsewhere.
- **Depends on**: none (all of 2026-09-28's work is merged at `545de44`)
- **Planned at**: commit `545de44`, 2026-09-28
- **Executor model**: `sonnet` for every task (Agent tool `model` value).
  Tests and several edits are written from prose; `haiku` is not enough.
  **Review of Tasks 1, 4, 5 and 11 uses the most capable model** — they carry
  the statistical and security claims.

## Global Constraints

- No new Python or npm dependencies. PIN hashing is stdlib `hashlib.pbkdf2_hmac`.
- Owner key: environment variable **`SPORTS_PICKS_OWNER_KEY`**, else the same
  name in `~/.secrets/shared.env`; sent as header **`X-Owner-Key`**. Never
  echo, log or commit its value.
- PIN: **4–6 digits**, header **`X-Player-Pin`**, kept as a string (leading
  zeros matter). **5 wrong PINs lock the player for 15 minutes.**
- Leaderboard: rank by ROI; fewer than **10** settled bets → `ranked: false`.
- Wilson interval z = **1.645** (90%).
- Rates in new API responses are **fractions 0–1**, rounded to 4 dp. Existing
  endpoints that already return percentages keep returning percentages.
- Game picks and props are never combined in `/stats/emailed*`; the
  leaderboard's Model row deliberately combines them (spec §2).
- Every guard is mutation-checked: break it, confirm a named test fails,
  restore. Record each check in the commit message.
- Backend suite: `.venv/Scripts/python.exe -m pytest backend/tests -q -p no:warnings`
  (~2.5 min). Frontend: `cd frontend && npx tsc -b --noEmit && npx vitest run && npx eslint .`
- Commits end with the attribution line from the session instructions.

## Review Focus

Inputs the spec is silent on that are most likely to bite a real user. Each
has a test in the owning task.

1. **Two friends choose names differing only in case or spacing** ("Marcus" /
   "marcus " ) — expect the second to be rejected as taken. Test: Task 5.
2. **A PIN with leading zeros ("0123")** — expect it to work exactly as typed,
   never parsed to 123. Test: Task 5.
3. **The dashboard before any emailed pick has settled** (tomorrow is day one)
   — expect empty groups and `null` rates, and an empty-state message in the
   UI, never `NaN%`. Tests: Task 3 (API), Task 10 (UI).
4. **A parlay leg whose game is canceled or postponed** — expect the parlay to
   stay pending, not settle as a loss. Test: Task 6.
5. **Several emailed picks on the same day** — expect one trend point per day
   (the day-end cumulative), not a zig-zag of intra-day points. Test: Task 1.

## STOP conditions

- The drift check is non-empty.
- Any task's "verify it fails" step passes before the implementation exists.
- A mutation check does not fail the named test.
- A test outside this plan's files fails and the cause is not obviously a
  401/403 from the new guards (Tasks 4–5 tell you how to handle those).
- Anything would require printing, logging or committing the owner key or a PIN.
- Task 11's real request through the public URL does not return real data.

## File map

| File | Responsibility | Task |
|---|---|---|
| `backend/analysis/scorecard.py` (new) | `Bet`, `Summary`, `summarize`, `group`, `trend`, `wilson` | 1 |
| `backend/models.py` | `EmailedPick.confidence`; `UserProfile.pin_hash`, `pin_salt` | 2, 5 |
| `backend/database.py` | two additive migrations | 2, 5 |
| `backend/digest/record.py` | copy confidence; `emailed_bets`; `emailed_record` via scorecard | 2, 3 |
| `backend/api/stats.py` | `GET /stats/emailed`, `GET /stats/emailed/trend` | 3 |
| `backend/config.py` | `resolve_owner_key` | 4 |
| `backend/api/auth.py` (new) | `require_owner` | 4 |
| `backend/api/pins.py` (new) | PIN hashing, lockout, `require_player_pin` | 5 |
| `backend/api/backtest.py`, `pipeline_api.py`, `users.py` | guards on write routes | 4, 5 |
| `backend/pipeline/paper_settlement.py` (new) | `settle_parlays` | 6 |
| `backend/api/users.py` | no bets after start; balance incl. parlays; leaderboard; stats via scorecard | 6, 7 |
| `backend/analysis/paper_bets.py` (new) | `player_bets` | 7 |
| `backend/pipeline/scheduler.py` | `grade_pending_picks` calls `settle_parlays` | 6 |
| `backend/tests/auth_helpers.py` (new) | test constants for owner key and PIN | 4 |
| `frontend/src/api/client.ts`, `lib/secrets.ts` (new) | owner key / PIN headers; new endpoints | 8 |
| `frontend/src/pages/Admin.tsx`, `PaperTrading.tsx`, `components/BetModal.tsx`, `pages/FAQ.tsx` | owner key + PIN fields; copy | 8 |
| `frontend/src/components/LeaderboardBar.tsx` (new), `hooks/useRankings.ts` (new) | leaderboard | 9 |
| `frontend/src/components/EmailedRecord.tsx` (new), `hooks/useEmailedRecord.ts` (new), `pages/TrackRecord.tsx` | emailed view | 10 |
| `scripts/share.ps1` (new) | current app server + tunnel + URL | 11 |

---

### Task 1: The scorecard module

**Files:**
- Create: `backend/analysis/scorecard.py`
- Test: `backend/tests/test_scorecard.py`

**Interfaces:**
- Consumes: `backend.analysis.odds_utils.american_to_implied_prob(odds: int) -> float`, `InvalidOddsError`
- Produces:
  - `Bet(result: str | None, stake: float, profit: float, odds: int, day: date, stars: int | None = None)` — frozen dataclass
  - `Summary` — frozen dataclass with `label, wins, losses, pushes, pending, win_rate, range_low, range_high, break_even, profit, staked, roi`, property `n`, property `verdict -> "above" | "below" | None`, method `to_dict() -> dict`
  - `summarize(bets: Iterable[Bet], label: str = "all") -> Summary`
  - `group(bets: Iterable[Bet], by: str) -> list[Summary]` — `by` in `{"week","month","stars"}`
  - `trend(bets: Iterable[Bet]) -> Trend`; `Trend(points: list[tuple[date, float]], max_drawdown: float, longest_losing_streak: int)`
  - `wilson(wins: int, decided: int, z: float = Z90) -> tuple[float, float] | None`

- [ ] **Step 1: Write the failing tests**

```python
"""The one definition of every pick-quality metric.

Expected values were computed independently on 2026-09-28; the z = 1.96 case
is the textbook Wilson interval for 8 of 10, (0.490, 0.943).
"""
from datetime import date

import pytest

from backend.analysis.scorecard import Bet, group, summarize, trend, wilson

D = date(2026, 9, 28)


def _b(result, odds=-110, stake=1.0, profit=None, day=D, stars=None):
    if profit is None:
        profit = {"win": stake * (100 / abs(odds) if odds < 0 else odds / 100),
                  "loss": -stake, "push": 0.0, None: 0.0}[result]
    return Bet(result=result, stake=stake, profit=profit, odds=odds, day=day,
               stars=stars)


def test_wilson_matches_the_published_95_percent_interval():
    low, high = wilson(8, 10, z=1.96)
    assert low == pytest.approx(0.4902, abs=1e-4)
    assert high == pytest.approx(0.9433, abs=1e-4)


def test_wilson_defaults_to_90_percent():
    low, high = wilson(8, 10)
    assert low == pytest.approx(0.5408, abs=1e-4)
    assert high == pytest.approx(0.9314, abs=1e-4)


def test_wilson_edges():
    assert wilson(0, 0) is None
    low, high = wilson(5, 5)
    assert low == pytest.approx(0.6488, abs=1e-4) and high == 1.0
    low, high = wilson(0, 5)
    assert low == 0.0 and high == pytest.approx(0.3512, abs=1e-4)


def test_win_rate_excludes_pushes_and_pending():
    s = summarize([_b("win"), _b("loss"), _b("push"), _b(None)])
    assert (s.wins, s.losses, s.pushes, s.pending, s.n) == (1, 1, 1, 1, 3)
    assert s.win_rate == pytest.approx(0.5)


def test_no_decided_bet_means_no_rate_not_zero():
    s = summarize([_b(None), _b("push")])
    assert s.win_rate is None and s.range_low is None and s.break_even is None


def test_break_even_is_the_mean_implied_probability_of_decided_prices():
    s = summarize([_b("win", odds=-110), _b("loss", odds=150)])
    assert s.break_even == pytest.approx(0.461905, abs=1e-6)


def test_roi_counts_a_push_as_staked_and_returned():
    s = summarize([_b("win"), _b("loss"), _b("push")])
    assert s.roi == pytest.approx(-0.030303, abs=1e-6)


def test_verdict_only_when_the_whole_range_clears_break_even():
    thin = summarize([_b("win")] * 3 + [_b("loss")] * 2)       # range spans 0.524
    assert thin.verdict is None
    strong = summarize([_b("win")] * 40 + [_b("loss")] * 5)
    assert strong.verdict == "above"
    weak = summarize([_b("win")] * 5 + [_b("loss")] * 40)
    assert weak.verdict == "below"


def test_sunday_and_monday_are_different_weeks():
    sun, mon = date(2026, 9, 27), date(2026, 9, 28)
    labels = [s.label for s in group([_b("win", day=sun), _b("win", day=mon)], "week")]
    assert labels == ["2026-09-28", "2026-09-21"]      # newest first


def test_months_newest_first():
    labels = [s.label for s in group(
        [_b("win", day=date(2026, 8, 31)), _b("win", day=date(2026, 9, 1))], "month")]
    assert labels == ["2026-09", "2026-08"]


def test_stars_ordered_high_to_low_with_unrated_last():
    labels = [s.label for s in group(
        [_b("win", stars=3), _b("win", stars=None), _b("win", stars=5)], "stars")]
    assert labels == ["5", "3", "unrated"]


def test_unknown_grouping_is_rejected():
    with pytest.raises(ValueError):
        group([_b("win")], "year")


def test_drawdown_takes_the_deeper_of_two_dips():
    days = [date(2026, 9, d) for d in range(1, 8)]
    profits = [1, 1, -1, 2, -1, -1, -1]
    t = trend([_b("win" if p > 0 else "loss", profit=p, day=d)
               for p, d in zip(profits, days)])
    assert t.max_drawdown == pytest.approx(3.0)


def test_a_push_does_not_break_a_losing_streak_and_a_win_does():
    seq = ["loss", "loss", "push", "loss", "win", "loss"]
    t = trend([_b(r, day=date(2026, 9, i + 1)) for i, r in enumerate(seq)])
    assert t.longest_losing_streak == 3


def test_trend_has_one_point_per_day():
    """Review Focus 5: several picks on one day are one day-end point."""
    t = trend([_b("win", day=D), _b("loss", day=D), _b("win", day=date(2026, 9, 29))])
    assert [d for d, _ in t.points] == [D, date(2026, 9, 29)]
    assert t.points[0][1] == pytest.approx(100 / 110 - 1)


def test_pending_bets_are_not_on_the_trend():
    assert trend([_b(None)]).points == []


def test_to_dict_rounds_and_keeps_nones():
    d = summarize([_b(None)]).to_dict()
    assert d["win_rate"] is None and d["n"] == 0 and d["pending"] == 1
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest backend/tests/test_scorecard.py -q -p no:warnings`
Expected: collection error, `ModuleNotFoundError: backend.analysis.scorecard`.

- [ ] **Step 3: Write the implementation**

```python
"""The one definition of every pick-quality metric.

Every page that reports a win rate or ROI computes it here, from a list of
settled or pending bets that says nothing about where they came from: the
emailed digest (1u per pick) and friends' paper bets (dollars) both arrive as
`Bet`s. Two code paths that must agree call one function rather than a test
asserting they match.

Definitions (spec 2026-09-28 §1):
  win rate    wins / (wins + losses); pushes and pending excluded
  90% range   Wilson score interval, z = 1.645
  break-even  mean implied probability (vig included) of the decided bets'
              prices -- the win rate those prices required
  ROI         profit / stake over settled bets; a push is staked and returned
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Iterable

from backend.analysis.odds_utils import InvalidOddsError, american_to_implied_prob

Z90 = 1.645


@dataclass(frozen=True)
class Bet:
    result: str | None      # "win" | "loss" | "push" | None while pending
    stake: float
    profit: float           # net, in the bet's own currency; 0.0 while pending
    odds: int
    day: date
    stars: int | None = None


def wilson(wins: int, decided: int, z: float = Z90) -> tuple[float, float] | None:
    """Wilson score interval for ``wins`` of ``decided``, or None with none decided."""
    if decided == 0:
        return None
    p = wins / decided
    z2 = z * z
    denom = 1 + z2 / decided
    centre = (p + z2 / (2 * decided)) / denom
    half = z * math.sqrt(p * (1 - p) / decided + z2 / (4 * decided * decided)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


def _r(x: float | None) -> float | None:
    return None if x is None else round(x, 4)


@dataclass(frozen=True)
class Summary:
    label: str
    wins: int
    losses: int
    pushes: int
    pending: int
    win_rate: float | None
    range_low: float | None
    range_high: float | None
    break_even: float | None
    profit: float
    staked: float
    roi: float | None

    @property
    def n(self) -> int:
        """Settled bets."""
        return self.wins + self.losses + self.pushes

    @property
    def verdict(self) -> str | None:
        """"above"/"below" only when the WHOLE range clears break-even."""
        if self.range_low is None or self.break_even is None:
            return None
        if self.range_low > self.break_even:
            return "above"
        if self.range_high < self.break_even:
            return "below"
        return None

    def to_dict(self) -> dict:
        return {
            "label": self.label, "wins": self.wins, "losses": self.losses,
            "pushes": self.pushes, "pending": self.pending, "n": self.n,
            "win_rate": _r(self.win_rate), "range_low": _r(self.range_low),
            "range_high": _r(self.range_high), "break_even": _r(self.break_even),
            "profit": round(self.profit, 4), "staked": round(self.staked, 4),
            "roi": _r(self.roi), "verdict": self.verdict,
        }


def summarize(bets: Iterable[Bet], label: str = "all") -> Summary:
    bets = list(bets)
    settled = [b for b in bets if b.result is not None]
    wins = sum(1 for b in settled if b.result == "win")
    losses = sum(1 for b in settled if b.result == "loss")
    pushes = sum(1 for b in settled if b.result == "push")
    decided = wins + losses
    interval = wilson(wins, decided)
    prices = []
    for b in settled:
        if b.result in ("win", "loss"):
            try:
                prices.append(american_to_implied_prob(b.odds))
            except InvalidOddsError:
                continue
    profit = sum(b.profit for b in settled)
    staked = sum(b.stake for b in settled)
    return Summary(
        label=label, wins=wins, losses=losses, pushes=pushes,
        pending=len(bets) - len(settled),
        win_rate=wins / decided if decided else None,
        range_low=interval[0] if interval else None,
        range_high=interval[1] if interval else None,
        break_even=sum(prices) / len(prices) if prices else None,
        profit=profit, staked=staked,
        roi=profit / staked if staked else None,
    )


def _week(d: date) -> str:
    return (d - timedelta(days=d.weekday())).isoformat()


def _month(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def _stars(stars: int | None) -> str:
    return str(stars) if stars else "unrated"


def group(bets: Iterable[Bet], by: str) -> list[Summary]:
    """Summaries per week (Mon-Sun, ET day), calendar month, or star level.

    Weeks and months newest first; stars 5..1 then "unrated".
    """
    if by == "week":
        key = lambda b: _week(b.day)                       # noqa: E731
    elif by == "month":
        key = lambda b: _month(b.day)                      # noqa: E731
    elif by == "stars":
        key = lambda b: _stars(b.stars)                    # noqa: E731
    else:
        raise ValueError(f"unknown grouping {by!r}")
    buckets: dict[str, list[Bet]] = {}
    for b in bets:
        buckets.setdefault(key(b), []).append(b)
    if by == "stars":
        order = sorted(buckets, key=lambda k: (k == "unrated", -int(k) if k != "unrated" else 0))
    else:
        order = sorted(buckets, reverse=True)
    return [summarize(buckets[k], label=k) for k in order]


@dataclass(frozen=True)
class Trend:
    points: list[tuple[date, float]]    # day-end cumulative profit
    max_drawdown: float
    longest_losing_streak: int


def trend(bets: Iterable[Bet]) -> Trend:
    """Cumulative profit by day, max drawdown, longest losing streak.

    Bets are taken in day order, input order within a day. A push breaks
    neither streak; a win ends a losing streak. Drawdown is measured from a
    running peak that starts at 0.
    """
    settled = sorted((b for b in bets if b.result is not None), key=lambda b: b.day)
    cum = peak = drawdown = 0.0
    streak = longest = 0
    by_day: dict[date, float] = {}
    for b in settled:
        cum += b.profit
        peak = max(peak, cum)
        drawdown = max(drawdown, peak - cum)
        if b.result == "loss":
            streak += 1
            longest = max(longest, streak)
        elif b.result == "win":
            streak = 0
        by_day[b.day] = cum
    return Trend(points=sorted(by_day.items()), max_drawdown=drawdown,
                 longest_losing_streak=longest)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest backend/tests/test_scorecard.py -q -p no:warnings`
Expected: `17 passed`.

- [ ] **Step 5: Mutation checks** — apply each, run the file, confirm the named test fails, restore:
  1. `win_rate=wins / decided` → `wins / (decided + pushes)`: `test_win_rate_excludes_pushes_and_pending` fails.
  2. In `verdict`, `self.range_low > self.break_even` → `self.win_rate > self.break_even`: `test_verdict_only_when_the_whole_range_clears_break_even` fails.
  3. In `trend`, the `elif b.result == "win": streak = 0` → `else: streak = 0`: `test_a_push_does_not_break_a_losing_streak_and_a_win_does` fails.

- [ ] **Step 6: Commit**

```bash
git add backend/analysis/scorecard.py backend/tests/test_scorecard.py
git commit -m "feat(analysis): scorecard, the one definition of every pick metric"
```

---

### Task 2: Stars travel with each emailed pick

**Files:**
- Modify: `backend/models.py` (class `EmailedPick`), `backend/database.py` (new migration + `MIGRATIONS`), `backend/digest/record.py` (`record_emailed`)
- Test: `backend/tests/test_emailed_picks.py` (extend), `backend/tests/test_emailed_confidence_migration.py` (new)

**Interfaces:**
- Consumes: `DigestPick.confidence: int` (already exists in `backend/digest/selector.py`)
- Produces: `EmailedPick.confidence: int | None`; `migrate_emailed_pick_confidence(engine) -> None`

- [ ] **Step 1: Write the failing tests**

In `backend/tests/test_emailed_picks.py`, extend `test_a_sent_digest_records_every_pick_as_emailed` — replace its body's assertion block with:

```python
    assert _rows(engine) == [
        (DAY, 10, "moneyline", "HOME ML", -120),
        (DAY, 11, "prop", "Q Back Over 220.5 Pass Yards", -110),
    ]
    s = get_session(engine)
    try:
        stars = {r.pick_id: r.confidence for r in s.query(EmailedPick)}
    finally:
        s.close()
    assert stars == {10: 4, 11: 5}     # as emailed (the DigestPicks' confidence)
```

Create `backend/tests/test_emailed_confidence_migration.py`:

```python
"""emailed_picks gains confidence; only the 2026-09-28 rows are backfilled.

Those five were verified unchanged since the send before they were recorded.
A later row's pick may have been refreshed after its email, so copying the
pick's CURRENT stars into it would be a guess.
"""
from datetime import date

from sqlalchemy import create_engine, text

from backend.database import get_session, migrate_emailed_pick_confidence
from backend.models import Base, EmailedPick, Game, PickModel, StrategyModel, Team


def _old_shape():
    """Rows written through the models (so every NOT NULL default applies),
    then the column dropped -- the shape of a database from before Task 2."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    s = get_session(engine)
    s.add(StrategyModel(id=1, name="x", config_json="{}", is_active=True))
    s.add_all([Team(id=1, name="H", abbreviation="H", sport="nfl"),
               Team(id=2, name="A", abbreviation="A", sport="nfl")])
    s.flush()
    s.add(Game(id=1, sport="nfl", season="2026", date=date(2026, 9, 28),
               home_team_id=1, away_team_id=2, status="scheduled"))
    s.flush()
    for pid, day in ((1, date(2026, 9, 28)), (2, date(2026, 9, 29))):
        s.add(PickModel(id=pid, game_id=1, strategy_id=1, pick_type="prop",
                        pick_value="v", confidence=5, edge_pct=1.0, odds_at_pick=-110))
        s.flush()
        s.add(EmailedPick(digest_date=day, pick_id=pid, game_id=1, sport="nfl",
                          pick_type="prop", pick_value="v", odds=-110))
    s.commit()
    s.close()
    with engine.begin() as c:
        c.execute(text("ALTER TABLE emailed_picks DROP COLUMN confidence"))
    return engine


def test_adds_the_column_and_backfills_only_the_verified_day():
    engine = _old_shape()
    migrate_emailed_pick_confidence(engine)
    with engine.connect() as c:
        rows = dict(c.execute(text("SELECT digest_date, confidence FROM emailed_picks")).all())
    assert rows == {"2026-09-28": 5, "2026-09-29": None}


def test_is_idempotent():
    engine = _old_shape()
    migrate_emailed_pick_confidence(engine)
    migrate_emailed_pick_confidence(engine)
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest backend/tests/test_emailed_picks.py backend/tests/test_emailed_confidence_migration.py -q -p no:warnings`
Expected: import error for `migrate_emailed_pick_confidence`, and `AttributeError: ... has no attribute 'confidence'`.

- [ ] **Step 3: Implement**

`backend/models.py`, inside `class EmailedPick`, after `prop_market`:

```python
    #: Stars as emailed, copied at send time like the price. Nullable: rows
    #: recorded before 2026-09-29 carry it only where it was verifiable.
    confidence = Column(Integer, nullable=True)
```

`backend/database.py`, after `migrate_team_box_scores`:

```python
def migrate_emailed_pick_confidence(engine):
    """Add emailed_picks.confidence; backfill the 2026-09-28 rows only.

    The five picks emailed on 2026-09-28 were verified unchanged since the
    send when they were recorded, so their stored stars ARE the emailed
    stars. For any later row that is not guaranteed -- a pick is refreshed
    in place until kickoff -- so those stay NULL rather than guessed.
    """
    from sqlalchemy import inspect as sa_inspect, text
    inspector = sa_inspect(engine)
    if "emailed_picks" not in inspector.get_table_names():
        return
    columns = [c["name"] for c in inspector.get_columns("emailed_picks")]
    if "confidence" in columns:
        return
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE emailed_picks ADD COLUMN confidence INTEGER"))
        conn.execute(text(
            "UPDATE emailed_picks SET confidence = "
            "(SELECT p.confidence FROM picks p WHERE p.id = emailed_picks.pick_id) "
            "WHERE digest_date <= '2026-09-28'"))
```

and append `migrate_emailed_pick_confidence,` as the last entry of `MIGRATIONS`.

`backend/digest/record.py`, in `record_emailed`'s `EmailedPick(...)` call, add after `prop_market=pick.prop_market`:

```python
            confidence=item.confidence,
```

- [ ] **Step 4: Run to verify they pass**

Run the Step 2 command. Expected: all pass. Then run `backend/tests/test_database.py` — expected: pass (no new table).

- [ ] **Step 5: Mutation checks**
  1. Remove `WHERE digest_date <= '2026-09-28'`: `test_adds_the_column_and_backfills_only_the_verified_day` fails.
  2. Remove `confidence=item.confidence,`: `test_a_sent_digest_records_every_pick_as_emailed` fails.

- [ ] **Step 6: Commit**

```bash
git add backend/models.py backend/database.py backend/digest/record.py backend/tests/test_emailed_picks.py backend/tests/test_emailed_confidence_migration.py
git commit -m "feat(digest): record the stars each pick was emailed with"
```

---

### Task 3: Emailed-record endpoints

**Files:**
- Modify: `backend/digest/record.py` (add `emailed_bets`; rebuild `emailed_record` on the scorecard), `backend/api/stats.py`
- Test: `backend/tests/test_api_emailed_stats.py` (new); existing `backend/tests/test_emailed_picks.py` must stay green

**Interfaces:**
- Consumes: Task 1 `Bet`, `summarize`, `group`, `trend`; existing `grade_emailed(session, row, game) -> tuple[str, float] | None`
- Produces:
  - `emailed_bets(session, kind: str | None = None) -> list[Bet]` — `kind` in `{"game","prop",None}`; stake 1.0; `stars` from `EmailedPick.confidence`
  - `GET /stats/emailed?kind=game|prop&by=week|month|stars` → `{"kind", "by", "groups": [Summary.to_dict()...], "total": Summary.to_dict()}`
  - `GET /stats/emailed/trend?kind=game|prop` → `{"kind", "points": [{"date": "YYYY-MM-DD", "units": float}], "max_drawdown": float, "longest_losing_streak": int}`

- [ ] **Step 1: Write the failing tests** — `backend/tests/test_api_emailed_stats.py`:

```python
"""The emailed record over HTTP. Numbers come from backend.analysis.scorecard."""
from datetime import date

import pytest
from fastapi.testclient import TestClient

from backend.api.main import create_app
from backend.database import get_session
from backend.models import EmailedPick, Game, PickModel, StrategyModel, Team

SUN, MON = date(2026, 9, 27), date(2026, 9, 28)


def _client(rows):
    """rows: (digest_date, pick_type, pick_value, odds, stars, home, away).
    home/away None -> the game has not finished."""
    app = create_app(":memory:")
    s = get_session(app.state.engine)
    s.add(StrategyModel(id=1, name="x", config_json="{}", is_active=True))
    s.add_all([Team(id=1, name="H", abbreviation="H", sport="nfl"),
               Team(id=2, name="A", abbreviation="A", sport="nfl")])
    s.flush()
    for i, (d, ptype, value, odds, stars, home, away) in enumerate(rows, start=1):
        final = home is not None
        s.add(Game(id=i, sport="nfl", season="2026", date=d, home_team_id=1,
                   away_team_id=2, status="final" if final else "scheduled",
                   home_score=home, away_score=away))
        s.flush()
        s.add(PickModel(id=i, game_id=i, strategy_id=1, pick_type=ptype,
                        pick_value=value, confidence=stars or 1, edge_pct=5.0,
                        odds_at_pick=odds))
        s.flush()
        s.add(EmailedPick(digest_date=d, pick_id=i, game_id=i, sport="nfl",
                          pick_type=ptype, pick_value=value, odds=odds,
                          confidence=stars))
    s.commit()
    s.close()
    return TestClient(app)


def test_no_emailed_picks_is_an_empty_record_not_an_error():
    """Review Focus 3: day one of recording."""
    body = _client([]).get("/stats/emailed?kind=game&by=week").json()
    assert body["groups"] == []
    assert body["total"]["n"] == 0 and body["total"]["win_rate"] is None


def test_by_stars():
    c = _client([
        (MON, "moneyline", "HOME ML", -110, 5, 24, 17),   # win
        (MON, "moneyline", "HOME ML", -110, 5, 10, 17),   # loss
        (MON, "moneyline", "AWAY ML", 150, 3, 10, 17),    # win
    ])
    body = c.get("/stats/emailed?kind=game&by=stars").json()
    assert [g["label"] for g in body["groups"]] == ["5", "3"]
    five = body["groups"][0]
    assert (five["wins"], five["losses"]) == (1, 1)
    assert five["win_rate"] == 0.5
    assert five["break_even"] == pytest.approx(0.5238, abs=1e-4)
    assert (body["total"]["wins"], body["total"]["losses"]) == (2, 1)


def test_by_week_newest_first():
    c = _client([(SUN, "moneyline", "HOME ML", -110, 4, 24, 17),
                 (MON, "moneyline", "HOME ML", -110, 4, 24, 17)])
    labels = [g["label"] for g in c.get("/stats/emailed?kind=game&by=week").json()["groups"]]
    assert labels == ["2026-09-28", "2026-09-21"]


def test_pending_is_counted_not_graded():
    body = _client([(MON, "moneyline", "HOME ML", -110, 4, None, None)]) \
        .get("/stats/emailed?kind=game&by=week").json()
    assert body["total"]["pending"] == 1 and body["total"]["n"] == 0


def test_props_and_game_picks_never_mix():
    c = _client([(MON, "moneyline", "HOME ML", -110, 4, 24, 17),
                 (MON, "prop", "Q Over 200.5 Pass Yards", -110, 5, None, None)])
    game = c.get("/stats/emailed?kind=game&by=week").json()["total"]
    prop = c.get("/stats/emailed?kind=prop&by=week").json()["total"]
    assert (game["wins"], game["pending"]) == (1, 0)
    assert (prop["wins"], prop["pending"]) == (0, 1)


def test_bad_parameters_are_422():
    c = _client([])
    assert c.get("/stats/emailed?kind=both").status_code == 422
    assert c.get("/stats/emailed?by=year").status_code == 422


def test_trend():
    c = _client([(SUN, "moneyline", "HOME ML", -110, 4, 10, 17),   # loss
                 (MON, "moneyline", "HOME ML", -110, 4, 24, 17)])  # win
    body = c.get("/stats/emailed/trend?kind=game").json()
    assert [p["date"] for p in body["points"]] == ["2026-09-27", "2026-09-28"]
    assert body["points"][0]["units"] == -1.0
    assert body["max_drawdown"] == 1.0
    assert body["longest_losing_streak"] == 1
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest backend/tests/test_api_emailed_stats.py -q -p no:warnings`
Expected: failures with 404 on `/stats/emailed` (the SPA catch-all may serve HTML with 200 if `frontend/dist` exists — then the JSON decode fails; either counts as failing).

- [ ] **Step 3: Implement**

In `backend/digest/record.py`: add `from backend.analysis.scorecard import Bet, summarize` to the imports, then add below `grade_emailed`:

```python
def _as_bet(session: Session, row: EmailedPick, game: Game) -> Bet:
    graded = grade_emailed(session, row, game)
    result, units = graded if graded is not None else (None, 0.0)
    return Bet(result=result, stake=1.0, profit=units, odds=row.odds,
               day=row.digest_date, stars=row.confidence)


def emailed_bets(session: Session, kind: str | None = None) -> list[Bet]:
    """Every emailed pick as a 1u Bet, oldest first. kind: "game", "prop" or None."""
    q = (session.query(EmailedPick, Game).join(Game, Game.id == EmailedPick.game_id)
         .order_by(EmailedPick.digest_date.asc(), EmailedPick.id.asc()))
    if kind == "game":
        q = q.filter(EmailedPick.pick_type != "prop")
    elif kind == "prop":
        q = q.filter(EmailedPick.pick_type == "prop")
    return [_as_bet(session, row, game) for row, game in q.all()]
```

Replace the `RecordRow` dataclass and `emailed_record` with (the CLI and existing tests read `wins, losses, pushes, pending, units, win_pct`):

```python
@dataclass
class RecordRow:
    sport: str
    kind: str          # "game" or "prop"
    wins: int
    losses: int
    pushes: int
    pending: int
    units: float
    win_pct: float | None


def emailed_record(session: Session, since: date | None = None,
                   until: date | None = None) -> list[RecordRow]:
    """The record of everything the digest sent, by sport and game/prop."""
    q = (session.query(EmailedPick, Game).join(Game, Game.id == EmailedPick.game_id)
         .order_by(EmailedPick.digest_date.asc(), EmailedPick.id.asc()))
    if since is not None:
        q = q.filter(EmailedPick.digest_date >= since)
    if until is not None:
        q = q.filter(EmailedPick.digest_date <= until)
    buckets: dict[tuple[str, str], list[Bet]] = {}
    for row, game in q.all():
        kind = "prop" if row.pick_type == "prop" else "game"
        buckets.setdefault((row.sport, kind), []).append(_as_bet(session, row, game))
    rows = []
    for (sport, kind), bets in sorted(buckets.items()):
        s = summarize(bets)
        rows.append(RecordRow(sport=sport, kind=kind, wins=s.wins, losses=s.losses,
                              pushes=s.pushes, pending=s.pending, units=s.profit,
                              win_pct=s.win_rate))
    return rows
```

In `backend/api/stats.py`, add to the imports:

```python
from typing import Literal

from backend.analysis.scorecard import group, summarize, trend
from backend.digest.record import emailed_bets
```

and add the routes:

```python
@router.get("/emailed")
def get_emailed(request: Request, kind: Literal["game", "prop"] = "game",
                by: Literal["week", "month", "stars"] = "week"):
    """The emailed picks' record, graded as sent. Rates are fractions 0-1."""
    session = get_session(request.app.state.engine)
    try:
        bets = emailed_bets(session, kind)
        return {"kind": kind, "by": by,
                "groups": [s.to_dict() for s in group(bets, by)],
                "total": summarize(bets).to_dict()}
    finally:
        session.close()


@router.get("/emailed/trend")
def get_emailed_trend(request: Request, kind: Literal["game", "prop"] = "game"):
    """Cumulative units by digest date, max drawdown, longest losing streak."""
    session = get_session(request.app.state.engine)
    try:
        t = trend(emailed_bets(session, kind))
        return {"kind": kind,
                "points": [{"date": d.isoformat(), "units": round(u, 4)} for d, u in t.points],
                "max_drawdown": round(t.max_drawdown, 4),
                "longest_losing_streak": t.longest_losing_streak}
    finally:
        session.close()
```

- [ ] **Step 4: Run to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest backend/tests/test_api_emailed_stats.py backend/tests/test_emailed_picks.py -q -p no:warnings`
Expected: all pass. Then `.venv/Scripts/python.exe -m backend.scripts.emailed_record` against the live db prints the `nfl prop` row as before.

- [ ] **Step 5: Mutation checks**
  1. In `emailed_bets`, delete the `kind == "prop"` branch: `test_props_and_game_picks_never_mix` fails.
  2. In `_as_bet`, `stars=row.confidence` → `stars=None`: `test_by_stars` fails.

- [ ] **Step 6: Commit**

```bash
git add backend/digest/record.py backend/api/stats.py backend/tests/test_api_emailed_stats.py
git commit -m "feat(api): the emailed record by week, month and stars, and its trend"
```

---

### Task 4: The owner key

**Files:**
- Modify: `backend/config.py`, `backend/api/backtest.py`, `backend/api/pipeline_api.py`, `backend/api/users.py`, `backend/tests/conftest.py`, and every existing test file that calls an owner route (see Step 5)
- Create: `backend/api/auth.py`, `backend/tests/auth_helpers.py`
- Test: `backend/tests/test_owner_key.py`, `backend/tests/test_write_route_coverage.py`

**Interfaces:**
- Produces:
  - `resolve_owner_key(shared_env_path=None) -> str | None` in `backend.config`
  - `require_owner(x_owner_key: str | None = Header(None, alias="X-Owner-Key")) -> None` in `backend.api.auth` — raises 403 on missing/wrong key, 503 when no key is configured
  - `backend.tests.auth_helpers`: `TEST_OWNER_KEY = "test-owner-key"`, `OWNER_HEADERS = {"X-Owner-Key": TEST_OWNER_KEY}`, `TEST_PIN = "1234"`, `PIN_HEADERS = {"X-Player-Pin": TEST_PIN}`, `ALL_HEADERS = {**OWNER_HEADERS, **PIN_HEADERS}`

The 9 owner routes (exact decorators to change):

| File | Route |
|---|---|
| `backend/api/backtest.py` | `POST /strategies`, `PUT /strategies/{strategy_id}`, `PATCH /strategies/{strategy_id}/promote`, `POST /run`, `POST /auto-tune`, `POST /run-all` |
| `backend/api/pipeline_api.py` | `POST /run` |
| `backend/api/users.py` | `DELETE /{user_id}`, `POST /grade` |

- [ ] **Step 1: Write the failing tests**

`backend/tests/auth_helpers.py`:

```python
"""Credentials the test suite uses. conftest sets SPORTS_PICKS_OWNER_KEY to
TEST_OWNER_KEY for every test; a test that calls a guarded route sends these."""
TEST_OWNER_KEY = "test-owner-key"
OWNER_HEADERS = {"X-Owner-Key": TEST_OWNER_KEY}
TEST_PIN = "1234"
PIN_HEADERS = {"X-Player-Pin": TEST_PIN}
ALL_HEADERS = {**OWNER_HEADERS, **PIN_HEADERS}
```

`backend/tests/test_owner_key.py`:

```python
"""Owner-only routes refuse everyone without the key -- including when no key
is configured at all (fail closed). DELETE /users/999 answers 404 once past
the guard, which proves the guard let the request through."""
from fastapi.testclient import TestClient

from backend.api.main import create_app
from backend.config import resolve_owner_key
from backend.tests.auth_helpers import OWNER_HEADERS


def _client():
    return TestClient(create_app(":memory:"))


def test_no_key_is_refused():
    assert _client().delete("/users/999").status_code == 403


def test_wrong_key_is_refused():
    r = _client().delete("/users/999", headers={"X-Owner-Key": "nope"})
    assert r.status_code == 403


def test_right_key_passes_the_guard():
    assert _client().delete("/users/999", headers=OWNER_HEADERS).status_code == 404


def test_an_unconfigured_server_refuses_owner_routes(monkeypatch, tmp_path):
    monkeypatch.delenv("SPORTS_PICKS_OWNER_KEY", raising=False)
    monkeypatch.setenv("SHARED_ENV_PATH", str(tmp_path / "missing.env"))
    r = _client().delete("/users/999", headers=OWNER_HEADERS)
    assert r.status_code == 503


def test_the_key_is_read_from_the_shared_secrets_file(monkeypatch, tmp_path):
    monkeypatch.delenv("SPORTS_PICKS_OWNER_KEY", raising=False)
    env = tmp_path / "shared.env"
    env.write_text("SPORTS_PICKS_OWNER_KEY=from-file\n", encoding="utf-8")
    monkeypatch.setenv("SHARED_ENV_PATH", str(env))
    assert resolve_owner_key() == "from-file"


def test_reads_stay_open():
    assert _client().get("/users/").status_code == 200
```

`backend/tests/test_write_route_coverage.py`:

```python
"""Every route that changes data is guarded, or explicitly allowed open.

Walks the real app's routes, so a write route added later without a guard
fails here. ALLOWED_UNPROTECTED is the only escape hatch; adding to it is a
security decision, not a fix for a failing test.
"""
from fastapi.routing import APIRoute

from backend.api.auth import require_owner
from backend.api.main import create_app

WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

#: POST /users/ is open by design (spec §6: anyone with the link may join).
#: Task 5 removes the two PIN routes from this set when it guards them.
ALLOWED_UNPROTECTED = {
    ("POST", "/users/"),
    ("POST", "/users/{user_id}/picks"),
    ("POST", "/users/{user_id}/parlay"),
}


def _guards():
    return {require_owner}


def _calls(dependant):
    for dep in dependant.dependencies:
        yield dep.call
        yield from _calls(dep)


def _write_routes():
    app = create_app(":memory:")
    for route in app.routes:
        if isinstance(route, APIRoute):
            for method in route.methods & WRITE_METHODS:
                yield method, route.path, set(_calls(route.dependant))


def test_every_write_route_is_guarded():
    guards = _guards()
    unguarded = [f"{m} {p}" for m, p, calls in _write_routes()
                 if not (calls & guards) and (m, p) not in ALLOWED_UNPROTECTED]
    assert unguarded == []


def test_the_walk_finds_the_write_routes():
    """Guards the guard: an empty walk would pass the test above vacuously."""
    assert len(list(_write_routes())) >= 12
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest backend/tests/test_owner_key.py backend/tests/test_write_route_coverage.py -q -p no:warnings`
Expected: import errors (`resolve_owner_key`, `backend.api.auth`).

- [ ] **Step 3: Implement**

`backend/config.py`, after `resolve_odds_api_key`:

```python
def resolve_owner_key(shared_env_path: str | os.PathLike | None = None) -> str | None:
    """The owner key for admin routes: environment first, then shared.env.

    Read on every call (not cached) so a key added to shared.env takes
    effect without a restart, and so tests can monkeypatch it. Never log
    the value.
    """
    from_env = os.environ.get("SPORTS_PICKS_OWNER_KEY")
    if from_env:
        return from_env
    path = (shared_env_path
            or os.environ.get("SHARED_ENV_PATH")
            or DEFAULT_SHARED_ENV)
    return _read_env_file(path).get("SPORTS_PICKS_OWNER_KEY") or None
```

(Confirm `_read_env_file` returns `{}` for a missing file: `grep -n "def _read_env_file" -A15 backend/config.py`. If it raises instead, STOP.)

`backend/api/auth.py`:

```python
"""Owner-only routes: grading, deleting players, the pipeline, strategies.

Once the app is reachable through a public link, anything not guarded here
or by a player's PIN is open to everyone who has the link. There is no
loopback exemption: cloudflared connects from localhost, so a friend's
request and the owner's are indistinguishable by address.
"""
import hmac

from fastapi import Header, HTTPException

from backend.config import resolve_owner_key

OWNER_HEADER = "X-Owner-Key"


def require_owner(x_owner_key: str | None = Header(default=None, alias=OWNER_HEADER)) -> None:
    expected = resolve_owner_key()
    if not expected:
        raise HTTPException(
            status_code=503,
            detail="No owner key is configured on this server; owner actions are disabled")
    if not x_owner_key or not hmac.compare_digest(
            x_owner_key.encode("utf-8"), expected.encode("utf-8")):
        raise HTTPException(status_code=403, detail="Owner key required")
```

Add `dependencies=[Depends(require_owner)]` to each of the 9 decorators in the table above, e.g.:

```python
@router.post("/strategies", status_code=201, dependencies=[Depends(require_owner)])
```

with `from fastapi import Depends` and `from backend.api.auth import require_owner` added to each file's imports (keep existing imports).

`backend/tests/conftest.py`, add:

```python
@pytest.fixture(autouse=True)
def _test_owner_key(monkeypatch):
    """Every test runs with a known owner key; guarded calls send OWNER_HEADERS."""
    from backend.tests.auth_helpers import TEST_OWNER_KEY
    monkeypatch.setenv("SPORTS_PICKS_OWNER_KEY", TEST_OWNER_KEY)
```

- [ ] **Step 4: Run to verify the new tests pass**

Run the Step 2 command. Expected: all pass.

- [ ] **Step 5: Give existing tests the key**

Run the full backend suite. Every new failure should be a `403` from an owner route. For each failing test, change its client construction from `TestClient(app)` to `TestClient(app, headers=OWNER_HEADERS)` (import from `backend.tests.auth_helpers`). Known sites as of `545de44`: `test_api_users.py` (use `ALL_HEADERS` there — Task 5 needs the PIN too), `test_websocket.py` (`ALL_HEADERS`), `test_pipeline_api.py` (the `client` fixture), `test_pipeline_integration.py`, `test_budget_alarm_and_redaction.py`, `test_api_backtest.py`, `test_backtest_run_api.py`. **Never** fix a failure by adding to `ALLOWED_UNPROTECTED`. A failure that is not a 403 is a STOP condition.

Run the full suite. Expected: all pass.

- [ ] **Step 6: Mutation checks**
  1. Remove `dependencies=[Depends(require_owner)]` from `POST /grade` in `users.py`: `test_every_write_route_is_guarded` fails naming `POST /users/grade`.
  2. In `require_owner`, change `if not expected:` → `if False:`: `test_an_unconfigured_server_refuses_owner_routes` fails.
  3. Replace the `compare_digest` condition with `if not x_owner_key:`: `test_wrong_key_is_refused` fails.

- [ ] **Step 7: Set the real key (operator step, never printed)**

```bash
.venv/Scripts/python.exe - <<'EOF'
import os, secrets, pathlib
p = pathlib.Path.home() / ".secrets" / "shared.env"
text = p.read_text(encoding="utf-8") if p.exists() else ""
if "SPORTS_PICKS_OWNER_KEY=" in text:
    print("already set; left unchanged")
else:
    with p.open("a", encoding="utf-8") as fh:
        fh.write(("" if text.endswith("\n") or not text else "\n")
                 + f"SPORTS_PICKS_OWNER_KEY={secrets.token_urlsafe(24)}\n")
    print("added SPORTS_PICKS_OWNER_KEY to", p)
EOF
```

Tell the owner the key is in `~/.secrets/shared.env` under that name; they paste it into the Admin page once (Task 8).

- [ ] **Step 8: Commit**

```bash
git add backend/config.py backend/api/auth.py backend/api/backtest.py backend/api/pipeline_api.py backend/api/users.py backend/tests
git commit -m "feat(api): owner key on every admin write route, fail closed"
```

---

### Task 5: Player PINs

**Files:**
- Create: `backend/api/pins.py`
- Modify: `backend/models.py` (`UserProfile`), `backend/database.py` (migration), `backend/api/users.py` (create/picks/parlay/reset), `backend/tests/conftest.py`, `backend/tests/test_write_route_coverage.py`, `backend/tests/test_api_users.py`
- Test: `backend/tests/test_player_pins.py`

**Interfaces:**
- Consumes: Task 4 `require_owner`, `auth_helpers`
- Produces:
  - `UserProfile.pin_hash: str | None`, `UserProfile.pin_salt: str | None`
  - `hash_pin(pin: str, salt: bytes | None = None) -> tuple[str, str]` (hex hash, hex salt)
  - `pin_matches(pin: str, pin_hash: str, pin_salt: str) -> bool`
  - `PinGuard(now=callable)`; module-level `guard: PinGuard`; `guard.reset()`
  - `require_player_pin(request: Request, user_id: int, x_player_pin: str | None = Header(None, alias="X-Player-Pin")) -> None` — 404 unknown player, 429 locked, 401 missing/malformed/wrong; sets the PIN on a PIN-less player's first bet
  - `POST /users/` body `{"name": str, "pin": str}`; `PUT /users/{user_id}/pin` body `{"pin": str}` (owner)

- [ ] **Step 1: Write the failing tests** — `backend/tests/test_player_pins.py`:

```python
"""A player's bets need their PIN. Five wrong PINs lock the player for 15
minutes; the lock is tested on an injected clock, not by waiting."""
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from backend.api import pins
from backend.api.main import create_app
from backend.database import get_session
from backend.models import Base, Game, Team, UserProfile
from backend.tests.auth_helpers import OWNER_HEADERS
from backend.time_utils import et_today

BET = {"pick_type": "moneyline", "pick_value": "HOME ML", "odds": -110, "stake": 10}


def _setup(pin="1234"):
    app = create_app(":memory:")
    client = TestClient(app)
    s = get_session(app.state.engine)
    s.add_all([Team(id=1, name="H", abbreviation="H", sport="nba"),
               Team(id=2, name="A", abbreviation="A", sport="nba")])
    s.flush()
    s.add(Game(id=1, sport="nba", season="2026", date=et_today(), home_team_id=1,
               away_team_id=2, status="scheduled"))
    s.commit()
    s.close()
    uid = client.post("/users/", json={"name": "friend", "pin": pin}).json()["id"]
    return client, uid


def _bet(client, uid, pin):
    headers = {} if pin is None else {"X-Player-Pin": pin}
    return client.post(f"/users/{uid}/picks", json={**BET, "game_id": 1}, headers=headers)


def test_the_right_pin_places_the_bet():
    client, uid = _setup()
    assert _bet(client, uid, "1234").status_code == 200


def test_a_missing_or_wrong_pin_is_refused():
    client, uid = _setup()
    assert _bet(client, uid, None).status_code == 401
    assert _bet(client, uid, "9999").status_code == 401


def test_a_leading_zero_pin_works_as_typed():
    """Review Focus 2."""
    client, uid = _setup(pin="0123")
    assert _bet(client, uid, "123").status_code == 401
    assert _bet(client, uid, "0123").status_code == 200


def test_five_wrong_pins_lock_even_the_right_one_until_15_minutes_pass():
    clock = [datetime(2026, 9, 29, 12, 0, tzinfo=timezone.utc)]
    pins.guard.now = lambda: clock[0]
    client, uid = _setup()
    for _ in range(5):
        assert _bet(client, uid, "9999").status_code == 401
    assert _bet(client, uid, "1234").status_code == 429
    clock[0] += timedelta(minutes=14, seconds=59)
    assert _bet(client, uid, "1234").status_code == 429
    clock[0] += timedelta(seconds=2)
    assert _bet(client, uid, "1234").status_code == 200


def test_the_pin_is_never_stored():
    client, uid = _setup(pin="482915")
    s = get_session(client.app.state.engine)
    u = s.get(UserProfile, uid)
    assert u.pin_hash and u.pin_salt
    assert "482915" not in (u.pin_hash + u.pin_salt)
    s.close()


def test_a_pinless_player_sets_a_pin_on_their_first_bet_then_needs_it():
    client, _ = _setup()
    s = get_session(client.app.state.engine)
    legacy = UserProfile(name="legacy")          # created before PINs existed
    s.add(legacy)
    s.commit()
    lid = legacy.id
    s.close()
    assert _bet(client, lid, "5555").status_code == 200
    assert _bet(client, lid, "6666").status_code == 401
    assert _bet(client, lid, "5555").status_code == 200


def test_joining_needs_a_4_to_6_digit_pin():
    client = TestClient(create_app(":memory:"))
    assert client.post("/users/", json={"name": "a"}).status_code == 422
    assert client.post("/users/", json={"name": "a", "pin": "12"}).status_code == 422
    assert client.post("/users/", json={"name": "a", "pin": "1234567"}).status_code == 422
    assert client.post("/users/", json={"name": "a", "pin": "12a4"}).status_code == 422
    assert client.post("/users/", json={"name": "a", "pin": "1234"}).status_code == 200


def test_names_are_unique_ignoring_case_and_spaces():
    """Review Focus 1."""
    client = TestClient(create_app(":memory:"))
    assert client.post("/users/", json={"name": "Marcus", "pin": "1234"}).status_code == 200
    assert client.post("/users/", json={"name": " marcus ", "pin": "1234"}).status_code == 400


def test_the_owner_can_reset_a_pin_and_nobody_else_can():
    client, uid = _setup()
    assert client.put(f"/users/{uid}/pin", json={"pin": "7777"}).status_code == 403
    assert client.put(f"/users/{uid}/pin", json={"pin": "7777"},
                      headers=OWNER_HEADERS).status_code == 200
    assert _bet(client, uid, "1234").status_code == 401
    assert _bet(client, uid, "7777").status_code == 200
```

In `backend/tests/test_write_route_coverage.py`: change `_guards()` to

```python
def _guards():
    from backend.api.pins import require_player_pin
    return {require_owner, require_player_pin}
```

and reduce `ALLOWED_UNPROTECTED` to `{("POST", "/users/")}` (delete the Task-5 comment line).

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest backend/tests/test_player_pins.py backend/tests/test_write_route_coverage.py -q -p no:warnings`
Expected: import error for `backend.api.pins`.

- [ ] **Step 3: Implement**

`backend/models.py`, in `UserProfile` after `streak_type`:

```python
    #: Salted PBKDF2 of the player's PIN (backend.api.pins). NULL for players
    #: created before PINs existed; they set one on their next bet.
    pin_hash = Column(String, nullable=True)
    pin_salt = Column(String, nullable=True)
```

`backend/database.py`, a migration appended to `MIGRATIONS` after Task 2's:

```python
def migrate_user_pin(engine):
    """Add user_profiles.pin_hash and pin_salt. Nullable: existing players
    have no PIN until their next bet sets one."""
    from sqlalchemy import inspect as sa_inspect, text
    inspector = sa_inspect(engine)
    if "user_profiles" not in inspector.get_table_names():
        return
    columns = [c["name"] for c in inspector.get_columns("user_profiles")]
    with engine.begin() as conn:
        for col in ("pin_hash", "pin_salt"):
            if col not in columns:
                conn.execute(text(f"ALTER TABLE user_profiles ADD COLUMN {col} VARCHAR"))
```

`backend/api/pins.py`:

```python
"""Player PINs: stored hashed, required to bet, locked after repeated misses.

A 4-digit PIN has 10,000 values, so without the lockout it is guessable in
minutes through a public link. The lock counter lives in memory: a restart
clears it, which is acceptable for a handful of friends.
"""
import hashlib
import hmac
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Callable

from fastapi import Header, HTTPException, Request

from backend.database import get_session
from backend.models import UserProfile

PIN_PATTERN = re.compile(r"^\d{4,6}$")
PIN_HEADER = "X-Player-Pin"
ITERATIONS = 200_000
MAX_FAILURES = 5
LOCK_FOR = timedelta(minutes=15)


def hash_pin(pin: str, salt: bytes | None = None) -> tuple[str, str]:
    salt = salt if salt is not None else os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", pin.encode("utf-8"), salt, ITERATIONS)
    return digest.hex(), salt.hex()


def pin_matches(pin: str, pin_hash: str, pin_salt: str) -> bool:
    candidate, _ = hash_pin(pin, bytes.fromhex(pin_salt))
    return hmac.compare_digest(candidate, pin_hash)


class PinGuard:
    """Counts wrong PINs per player; locks after MAX_FAILURES for LOCK_FOR."""

    def __init__(self, now: Callable[[], datetime] | None = None):
        self.now = now or (lambda: datetime.now(timezone.utc))
        self._failures: dict[int, int] = {}
        self._locked_until: dict[int, datetime] = {}

    def reset(self) -> None:
        self._failures.clear()
        self._locked_until.clear()
        self.now = lambda: datetime.now(timezone.utc)

    def locked(self, user_id: int) -> bool:
        until = self._locked_until.get(user_id)
        if until is None:
            return False
        if self.now() >= until:
            del self._locked_until[user_id]
            self._failures.pop(user_id, None)
            return False
        return True

    def fail(self, user_id: int) -> None:
        count = self._failures.get(user_id, 0) + 1
        self._failures[user_id] = count
        if count >= MAX_FAILURES:
            self._locked_until[user_id] = self.now() + LOCK_FOR

    def succeed(self, user_id: int) -> None:
        self._failures.pop(user_id, None)

    def clear(self, user_id: int) -> None:
        self._failures.pop(user_id, None)
        self._locked_until.pop(user_id, None)


guard = PinGuard()


def require_player_pin(request: Request, user_id: int,
                       x_player_pin: str | None = Header(default=None, alias=PIN_HEADER)) -> None:
    session = get_session(request.app.state.engine)
    try:
        user = session.get(UserProfile, user_id)
        if user is None:
            raise HTTPException(status_code=404, detail="User not found")
        if guard.locked(user_id):
            raise HTTPException(status_code=429,
                                detail="Too many wrong PINs; try again in 15 minutes")
        if not x_player_pin or not PIN_PATTERN.match(x_player_pin):
            raise HTTPException(status_code=401, detail="PIN required (4-6 digits)")
        if user.pin_hash is None:
            user.pin_hash, user.pin_salt = hash_pin(x_player_pin)
            session.commit()
            return
        if not pin_matches(x_player_pin, user.pin_hash, user.pin_salt):
            guard.fail(user_id)
            raise HTTPException(status_code=401, detail="Wrong PIN")
        guard.succeed(user_id)
    finally:
        session.close()
```

`backend/api/users.py`:

1. Imports: add `from fastapi import Depends` (keep existing), `from pydantic import field_validator`, `from backend.api.auth import require_owner`, `from backend.api.pins import PIN_PATTERN, guard as pin_guard, hash_pin, require_player_pin`.
2. Replace `CreateUserRequest` with:

```python
def _check_pin(value: str) -> str:
    if not PIN_PATTERN.match(value):
        raise ValueError("PIN must be 4-6 digits")
    return value


class CreateUserRequest(BaseModel):
    name: str
    pin: str

    @field_validator("pin")
    @classmethod
    def _pin(cls, v: str) -> str:
        return _check_pin(v)


class SetPinRequest(BaseModel):
    pin: str

    @field_validator("pin")
    @classmethod
    def _pin(cls, v: str) -> str:
        return _check_pin(v)
```

3. In `create_user`, replace the body between `try:` and `return` with:

```python
        name = body.name.strip()
        if not name:
            raise HTTPException(status_code=400, detail="Name required")
        taken = session.query(UserProfile).filter(
            func.lower(func.trim(UserProfile.name)) == name.lower()).first()
        if taken:
            raise HTTPException(status_code=400, detail="Username already taken")
        pin_hash, pin_salt = hash_pin(body.pin)
        user = UserProfile(name=name, pin_hash=pin_hash, pin_salt=pin_salt)
        session.add(user)
        session.commit()
```

(the existing `return {...}` line stays).

4. Add `dependencies=[Depends(require_player_pin)]` to `@router.post("/{user_id}/picks")` and `@router.post("/{user_id}/parlay")`.
5. Add the owner reset route directly after `delete_user`:

```python
@router.put("/{user_id}/pin", dependencies=[Depends(require_owner)])
def reset_pin(request: Request, user_id: int, body: SetPinRequest):
    """Owner-only: set a player's PIN (a friend forgot theirs)."""
    session = get_session(request.app.state.engine)
    try:
        user = session.get(UserProfile, user_id)
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        user.pin_hash, user.pin_salt = hash_pin(body.pin)
        session.commit()
        pin_guard.clear(user_id)
        return {"id": user_id, "pin_set": True}
    finally:
        session.close()
```

`backend/tests/conftest.py`, add:

```python
@pytest.fixture(autouse=True)
def _reset_pin_guard():
    """PIN lockouts are process-wide; no test may inherit another's."""
    from backend.api.pins import guard
    guard.reset()
    yield
    guard.reset()
```

`backend/tests/test_api_users.py`: `_make_user` posts `{"name": name, "pin": TEST_PIN}` (import `TEST_PIN`); every client there was built with `ALL_HEADERS` in Task 4, so bets carry the PIN. Apply the same to `test_websocket.py` if it creates users.

- [ ] **Step 4: Run to verify**

Run: `.venv/Scripts/python.exe -m pytest backend/tests/test_player_pins.py backend/tests/test_write_route_coverage.py backend/tests/test_api_users.py -q -p no:warnings`
Expected: all pass. Then the full backend suite — expected green.

- [ ] **Step 5: Mutation checks**
  1. Remove `dependencies=[Depends(require_player_pin)]` from the parlay route: `test_every_write_route_is_guarded` fails naming `POST /users/{user_id}/parlay`.
  2. In `PinGuard.fail`, `>= MAX_FAILURES` → `> MAX_FAILURES`: `test_five_wrong_pins_lock_even_the_right_one_until_15_minutes_pass` fails.
  3. In `require_player_pin`, move the `guard.locked` check below the `pin_matches` check: the same lockout test fails (the correct PIN would pass while locked).
  4. In `create_user`, compare `UserProfile.name == name`: `test_names_are_unique_ignoring_case_and_spaces` fails.

- [ ] **Step 6: Commit**

```bash
git add backend/api/pins.py backend/api/users.py backend/models.py backend/database.py backend/tests
git commit -m "feat(api): a PIN per player, hashed, with a 5-strike lockout"
```

---

### Task 6: Paper trading is fair and parlays settle

Three defects, each pinned by an existing characterization test in `backend/tests/test_api_users.py` (plan 001) whose comment states the fixed value:

1. `place_pick` / `place_parlay` accept bets on games that have **already finished** and grade them on the spot — anyone could bet on known results.
2. `test_parlay_on_scheduled_games_never_settles`: a parlay pending at placement is never settled later.
3. `test_parlay_payout_missing_from_balance`: a parlay's payout never reaches the balance (it lives on `Parlay.payout`; balance sums `PaperPick.payout`).

**Files:**
- Create: `backend/pipeline/paper_settlement.py`
- Modify: `backend/api/users.py` (`place_pick`, `place_parlay`, `grade_paper_picks`, balance in `list_users`/`get_user`/`place_pick`/`place_parlay`), `backend/pipeline/scheduler.py` (`grade_pending_picks`)
- Test: `backend/tests/test_paper_settlement.py` (new), `backend/tests/test_api_users.py` (flip the pinned tests; migrate tests that bet on finished games)

**Interfaces:**
- Consumes: `backend.time_utils.game_start_utc(game) -> datetime | None`; `calculate_payout(odds) -> float`
- Produces:
  - `settle_parlays(session) -> int` — settles every pending parlay whose legs are all graded; commits
  - `balance_of(session, user) -> float` in `backend/api/users.py`
  - `POST /users/grade` → `{"graded": int, "parlays_settled": int}`
  - `grade_pending_picks(session)` return dict gains `"parlays": int`

- [ ] **Step 1: Write the failing tests**

`backend/tests/test_paper_settlement.py`:

```python
"""Parlays settle once every leg is graded, from whichever grading pass runs:
the owner's POST /users/grade or the scheduler's grade_pending_picks."""
from datetime import date

import pytest
from sqlalchemy import create_engine

from backend.database import get_session
from backend.models import Base, Game, PaperPick, Parlay, Team, UserProfile
from backend.pipeline.paper_settlement import settle_parlays


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    s = get_session(engine)
    s.add(UserProfile(id=1, name="p"))
    s.add_all([Team(id=1, name="H", abbreviation="H", sport="nba"),
               Team(id=2, name="A", abbreviation="A", sport="nba")])
    s.flush()
    yield s
    s.close()


def _parlay(s, leg_results, stake=100.0, odds=-110):
    p = Parlay(user_id=1, stake=stake, combined_odds=264)
    s.add(p)
    s.flush()
    for i, r in enumerate(leg_results):
        g = Game(sport="nba", season="2026", date=date(2026, 9, 28), home_team_id=1,
                 away_team_id=2, status="final" if r else "scheduled")
        s.add(g)
        s.flush()
        s.add(PaperPick(user_id=1, game_id=g.id, pick_type="moneyline",
                        pick_value="HOME ML", odds=odds, stake=0, result=r,
                        payout=0, parlay_id=p.id))
    s.commit()
    return p


def test_all_legs_won_pays_the_combined_price(session):
    p = _parlay(session, ["win", "win"], stake=500)
    assert settle_parlays(session) == 1
    assert p.result == "win"
    assert p.payout == pytest.approx(1322.31, abs=0.01)


def test_any_losing_leg_loses_the_stake(session):
    p = _parlay(session, ["win", "loss"])
    settle_parlays(session)
    assert (p.result, p.payout) == ("loss", -100.0)


def test_a_push_with_no_loss_pushes(session):
    p = _parlay(session, ["win", "push"])
    settle_parlays(session)
    assert (p.result, p.payout) == ("push", 0.0)


def test_an_ungraded_leg_leaves_it_pending(session):
    """Review Focus 4: a canceled or postponed leg never grades; the parlay
    must stay pending, not become a loss."""
    p = _parlay(session, ["win", None])
    assert settle_parlays(session) == 0
    assert p.result is None


def test_the_scheduler_pass_settles_parlays(session):
    """Dead-wiring guard: settle_parlays working is worth nothing if the
    daily grading never calls it."""
    from backend.pipeline.scheduler import grade_pending_picks
    p = _parlay(session, ["win", "win"])
    grade_pending_picks(session)
    assert p.result == "win"
```

In `backend/tests/test_api_users.py`:

(a) Add helpers after `_make_user`:

```python
def _finish(client, game_id, home=110, away=100):
    session = get_session(client.app.state.engine)
    game = session.get(Game, game_id)
    game.status, game.home_score, game.away_score = "final", home, away
    session.commit()
    session.close()


def _grade(client):
    response = client.post("/users/grade")
    assert response.status_code == 200
    return response.json()
```

(b) Replace `test_place_pick_on_final_game_grades_immediately_win` with:

```python
def test_a_bet_on_a_finished_game_is_refused():
    app = create_app(":memory:")
    client = TestClient(app, headers=ALL_HEADERS)
    game_ids = _seed_games(client, [{"status": "final", "home_score": 110, "away_score": 100}])
    user_id = _make_user(client)
    response = client.post(f"/users/{user_id}/picks", json={
        "game_id": game_ids[0], "pick_type": "moneyline",
        "pick_value": "HOME ML", "odds": -110, "stake": 100})
    assert response.status_code == 400


def test_a_bet_on_a_started_game_is_refused():
    from datetime import datetime, timedelta, timezone
    app = create_app(":memory:")
    client = TestClient(app, headers=ALL_HEADERS)
    game_ids = _seed_games(client, [{"status": "scheduled"}])
    session = get_session(app.state.engine)
    session.get(Game, game_ids[0]).start_time = (
        datetime.now(timezone.utc) - timedelta(minutes=5)).replace(tzinfo=None)
    session.commit()
    session.close()
    user_id = _make_user(client)
    response = client.post(f"/users/{user_id}/picks", json={
        "game_id": game_ids[0], "pick_type": "moneyline",
        "pick_value": "HOME ML", "odds": -110, "stake": 100})
    assert response.status_code == 400


def test_a_settled_win_pays_at_the_price():
    app = create_app(":memory:")
    client = TestClient(app, headers=ALL_HEADERS)
    game_ids = _seed_games(client, [{"status": "scheduled"}])
    user_id = _make_user(client)
    pick = client.post(f"/users/{user_id}/picks", json={
        "game_id": game_ids[0], "pick_type": "moneyline",
        "pick_value": "HOME ML", "odds": -110, "stake": 100}).json()
    assert pick["result"] is None
    _finish(client, game_ids[0])
    _grade(client)
    body = client.get(f"/users/{user_id}").json()
    assert body["profit"] == pytest.approx(90.91, abs=0.01)
```

(c) Rewrite `test_parlay_payout_missing_from_balance` as `test_a_winning_parlay_reaches_the_balance` — seed **two scheduled** games, place the same 500-stake two-leg parlay, `_finish` both, `_grade`, then assert `body["current_balance"] == pytest.approx(11322.31, abs=0.01)` and `body["profit"] == pytest.approx(1322.31, abs=0.01)`. Delete the CHARACTERIZATION comment; replace it with one line: `# Was a pinned bug (plan 001): parlay payouts never reached the balance.`

(d) Rewrite `test_parlay_on_scheduled_games_never_settles` as `test_a_parlay_settles_when_its_last_leg_finishes` — both legs scheduled at placement; `_finish` both; `_grade`; assert the `Parlay` row's `result == "win"`. Same one-line provenance comment.

(e) `test_grade_endpoint_grades_pending_picks`: expected JSON becomes `{"graded": 1, "parlays_settled": 0}`.

(f) Every other test that seeds a `"final"` game and bets on it (as of `545de44`: `test_delete_user_removes_picks`, `test_list_users_sorted_by_balance_desc`, `test_win_streak_counted`, and any in `test_websocket.py`) now gets 400. Convert each to: seed `"scheduled"`, place the bet, `_finish(client, gid, home, away)`, `_grade(client)`, then its original assertions. Do not weaken an assertion.

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest backend/tests/test_paper_settlement.py backend/tests/test_api_users.py -q -p no:warnings`
Expected: import error for `paper_settlement`; in `test_api_users.py`, the refused-bet tests get 200, the parlay tests see `result is None` / balance 10000.

- [ ] **Step 3: Implement**

`backend/pipeline/paper_settlement.py`:

```python
"""Settle paper parlays once every leg is graded.

Legs are PaperPick rows (stake 0) graded by the ordinary paper-grading
passes; the parlay's own stake and payout live on its Parlay row. Settlement
used to happen only at placement, so a parlay placed before its games never
settled (plan 001 pinned this as a known bug).

Rules, unchanged from placement-time settlement: any losing leg loses the
stake; otherwise any push pushes the whole parlay; otherwise it wins at the
product of the legs' decimal prices.
"""
from backend.analysis.odds_utils import calculate_payout
from backend.models import PaperPick, Parlay


def settle_parlays(session) -> int:
    settled = 0
    for parlay in session.query(Parlay).filter(Parlay.result.is_(None)).all():
        legs = session.query(PaperPick).filter(PaperPick.parlay_id == parlay.id).all()
        if len(legs) < 2 or any(leg.result is None for leg in legs):
            continue
        results = {leg.result for leg in legs}
        if "loss" in results:
            parlay.result, parlay.payout = "loss", -parlay.stake
        elif "push" in results:
            parlay.result, parlay.payout = "push", 0.0
        else:
            decimal = 1.0
            for leg in legs:
                decimal *= 1 + calculate_payout(leg.odds)
            parlay.result, parlay.payout = "win", parlay.stake * (decimal - 1)
        settled += 1
    session.commit()
    return settled
```

`backend/api/users.py`:

1. Imports: `from backend.pipeline.paper_settlement import settle_parlays`, `from backend.time_utils import et_today, game_start_utc` (extend the existing `time_utils` import).
2. Add helpers above `list_users`:

```python
def balance_of(session, user) -> float:
    """Starting balance plus every settled straight bet and parlay.

    Parlay legs are excluded (stake 0, payout 0); the parlay's own payout
    is on its Parlay row, which this used to leave out entirely.
    """
    straight = (session.query(func.coalesce(func.sum(PaperPick.payout), 0.0))
                .filter(PaperPick.user_id == user.id, PaperPick.parlay_id.is_(None))
                .scalar())
    parlays = (session.query(func.coalesce(func.sum(Parlay.payout), 0.0))
               .filter(Parlay.user_id == user.id).scalar())
    return user.starting_balance + straight + parlays


def _open_for_betting(game) -> bool:
    """Only games that have not started. Unknown start time is not past
    (the project-wide convention), but a status past 'scheduled' is."""
    if game.status != "scheduled":
        return False
    start = game_start_utc(game)
    return start is None or start > datetime.now(timezone.utc)
```

3. In `list_users`, `get_user`, `place_pick` and `place_parlay`, replace each `total_payout = sum(...)` / `current_balance = ... + total_payout` computation with `current_balance = balance_of(session, user)` (`u` in `list_users`). In `list_users`/`get_user`, `profit` becomes `round(current_balance - u.starting_balance, 2)`. (Task 7 rewrites the remaining stats fields.)
4. In `place_pick`, directly after the `if not game:` 404, add:

```python
        if not _open_for_betting(game):
            raise HTTPException(status_code=400,
                                detail="Betting has closed: this game has already started")
```

   and delete the placement-time grading block (from `# If game is already final, grade immediately` through the end of its `if/else`) and the later `if result:` feed/streak block; set `result = None` and `payout = None` where the block was. It is unreachable once only unstarted games are accepted.
5. In `place_parlay`, inside the `for leg in body.legs:` loop directly after the `if not game:` 404, add the same `_open_for_betting` check (detail: `f"Betting has closed: game {leg.game_id} has already started"`), delete the per-leg grading block and the `# Grade parlay if all legs are graded` block; each leg's `result=None`; the response's `result` and `payout` are `None`. Keep `combined_odds`, `potential_payout` and the feed event.
6. In `grade_paper_picks`, after the existing grading loop commits, add `parlays_settled = settle_parlays(session)` and return `{"graded": graded, "parlays_settled": parlays_settled}`.

`backend/pipeline/scheduler.py`, in `grade_pending_picks`, after `logger.info(f"Auto-graded {paper_graded} paper picks")`:

```python
    from backend.pipeline.paper_settlement import settle_parlays
    parlays = settle_parlays(session)
    logger.info("Settled %d paper parlays", parlays)
```

and add `"parlays": parlays` to the returned dict.

- [ ] **Step 4: Run to verify**

Run the Step 2 command, then the full backend suite. Expected: all pass.

- [ ] **Step 5: Mutation checks**
  1. In `_open_for_betting`, return `True` unconditionally: `test_a_bet_on_a_finished_game_is_refused` and `test_a_bet_on_a_started_game_is_refused` fail.
  2. Remove the `settle_parlays` call from `grade_pending_picks`: `test_the_scheduler_pass_settles_parlays` fails.
  3. In `balance_of`, drop `+ parlays`: `test_a_winning_parlay_reaches_the_balance` fails.
  4. In `settle_parlays`, drop `or any(leg.result is None ...)`: `test_an_ungraded_leg_leaves_it_pending` fails.

- [ ] **Step 6: Commit**

```bash
git add backend/pipeline/paper_settlement.py backend/api/users.py backend/pipeline/scheduler.py backend/tests
git commit -m "fix(paper): no bets after kickoff; parlays settle later and reach the balance"
```

---

### Task 7: The leaderboard, and player stats on the scorecard

**Files:**
- Create: `backend/analysis/paper_bets.py`
- Modify: `backend/api/users.py` (`/leaderboard` route; `list_users`, `get_user`, `get_user_stats`, `_compute_period_stats` via scorecard)
- Test: `backend/tests/test_leaderboard.py` (new); `test_api_users.py::test_user_stats_shape` must stay green

**Interfaces:**
- Consumes: Task 1 `Bet`, `summarize`; Task 3 `emailed_bets`; Task 6 `balance_of`
- Produces:
  - `player_bets(session, user_id: int) -> list[Bet]` — straight bets (`parlay_id IS NULL`) plus each parlay once; `day` = game date (parlay: latest leg's game date)
  - `GET /users/leaderboard` → list of `{"id": int | None, "name": str, "is_model": bool, "wins", "losses", "pushes", "pending", "n": int, "win_rate": float | None, "roi": float | None, "profit": float, "ranked": bool}`; rates are fractions; ranked rows first by ROI desc, then unranked by `n` desc, then name
  - `MIN_RANKED_BETS = 10`

- [ ] **Step 1: Write the failing tests** — `backend/tests/test_leaderboard.py`:

```python
"""Friends and the model on one board, ranked by ROI; one win-rate definition."""
from datetime import date

import pytest
from fastapi.testclient import TestClient

from backend.api.main import create_app
from backend.database import get_session
from backend.models import (EmailedPick, Game, PaperPick, Parlay, PickModel,
                            StrategyModel, Team, UserProfile)

D = date(2026, 9, 28)


def _app():
    app = create_app(":memory:")
    s = get_session(app.state.engine)
    s.add(StrategyModel(id=1, name="x", config_json="{}", is_active=True))
    s.add_all([Team(id=1, name="H", abbreviation="H", sport="nfl"),
               Team(id=2, name="A", abbreviation="A", sport="nfl")])
    s.commit()
    s.close()
    return app


def _game(s, home=24, away=17):
    g = Game(sport="nfl", season="2026", date=D, home_team_id=1, away_team_id=2,
             status="final", home_score=home, away_score=away)
    s.add(g)
    s.flush()
    return g


def _player(s, name, results, stake=100.0, odds=-110):
    u = UserProfile(name=name)
    s.add(u)
    s.flush()
    for r in results:
        g = _game(s)
        payout = {"win": stake * 100 / 110, "loss": -stake, "push": 0.0, None: None}[r]
        s.add(PaperPick(user_id=u.id, game_id=g.id, pick_type="moneyline",
                        pick_value="HOME ML", odds=odds, stake=stake, result=r,
                        payout=payout))
    s.commit()
    return u.id


def _model(s, results):
    for i, r in enumerate(results):
        g = _game(s, home=24 if r == "win" else 10)
        p = PickModel(game_id=g.id, strategy_id=1, pick_type="moneyline",
                      pick_value="HOME ML", confidence=4, edge_pct=5.0, odds_at_pick=-110)
        s.add(p)
        s.flush()
        s.add(EmailedPick(digest_date=D, pick_id=p.id, game_id=g.id, sport="nfl",
                          pick_type="moneyline", pick_value="HOME ML", odds=-110,
                          confidence=4))
    s.commit()


def _board(app):
    return {r["name"]: r for r in TestClient(app).get("/users/leaderboard").json()}


def test_the_route_is_not_shadowed_by_user_id():
    assert TestClient(_app()).get("/users/leaderboard").status_code == 200


def test_the_model_is_a_player():
    app = _app()
    s = get_session(app.state.engine)
    _model(s, ["win", "loss"])
    s.close()
    row = _board(app)["Model"]
    assert row["is_model"] is True and row["id"] is None
    assert (row["wins"], row["losses"]) == (1, 1)


def test_fewer_than_ten_settled_bets_is_unranked_and_sorts_last():
    app = _app()
    s = get_session(app.state.engine)
    _player(s, "hot", ["win"] * 3)                      # 100% ROI-ish, only 3 bets
    _player(s, "steady", ["win"] * 6 + ["loss"] * 4)    # 10 bets
    s.close()
    names = [r["name"] for r in TestClient(app).get("/users/leaderboard").json()]
    board = _board(app)
    assert board["hot"]["ranked"] is False and board["steady"]["ranked"] is True
    assert names.index("steady") < names.index("hot")


def test_ranked_by_roi_not_balance():
    app = _app()
    s = get_session(app.state.engine)
    _player(s, "big", ["win"] * 6 + ["loss"] * 4, stake=1000)   # same ROI, 10x money
    _player(s, "sharp", ["win"] * 8 + ["loss"] * 2, stake=10)
    s.close()
    names = [r["name"] for r in TestClient(app).get("/users/leaderboard").json()]
    assert names.index("sharp") < names.index("big")


def test_a_parlay_counts_once_and_its_legs_never():
    app = _app()
    s = get_session(app.state.engine)
    u = UserProfile(name="parlayer")
    s.add(u)
    s.flush()
    p = Parlay(user_id=u.id, stake=100, combined_odds=264, result="win", payout=264.46)
    s.add(p)
    s.flush()
    for _ in range(2):
        g = _game(s)
        s.add(PaperPick(user_id=u.id, game_id=g.id, pick_type="moneyline",
                        pick_value="HOME ML", odds=-110, stake=0, result="win",
                        payout=0, parlay_id=p.id))
    s.commit()
    s.close()
    row = _board(app)["parlayer"]
    assert (row["wins"], row["n"]) == (1, 1)
    assert row["profit"] == pytest.approx(264.46)


def test_one_definition_of_win_rate_and_roi_everywhere():
    """The same record through three routes. All call backend.analysis.
    scorecard; this checks the wiring, not a second formula."""
    app = _app()
    s = get_session(app.state.engine)
    _model(s, ["win", "win", "loss"])
    uid = _player(s, "mirror", ["win", "win", "loss"])      # same odds, 100x stake
    s.close()
    c = TestClient(app)
    board = _board(app)
    emailed = c.get("/stats/emailed?kind=game&by=week").json()["total"]
    stats = c.get(f"/users/{uid}/stats").json()["all_time"]
    assert board["Model"]["win_rate"] == emailed["win_rate"] == board["mirror"]["win_rate"]
    assert board["Model"]["roi"] == pytest.approx(board["mirror"]["roi"], abs=1e-4)
    assert stats["win_rate"] == pytest.approx(board["mirror"]["win_rate"] * 100, abs=0.1)
    assert stats["roi"] == pytest.approx(board["mirror"]["roi"] * 100, abs=0.01)


def test_player_stats_win_rate_excludes_pushes():
    """The visible change the spec calls out: wins / (W+L), not / (W+L+P)."""
    app = _app()
    s = get_session(app.state.engine)
    uid = _player(s, "pusher", ["win", "loss", "push"])
    s.close()
    stats = TestClient(app).get(f"/users/{uid}/stats").json()["all_time"]
    assert stats["win_rate"] == 50.0 and stats["total"] == 3
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest backend/tests/test_leaderboard.py -q -p no:warnings`
Expected: `/users/leaderboard` answers 422 (parsed as `user_id`), and the win-rate test sees 33.3.

- [ ] **Step 3: Implement**

`backend/analysis/paper_bets.py`:

```python
"""A player's paper bets as scorecard Bets.

Straight bets once each, and each parlay once at its own stake and payout;
parlay legs (stake 0) never count, or a 3-leg parlay would be 3 bets.
"""
from sqlalchemy import func

from backend.analysis.scorecard import Bet
from backend.models import Game, PaperPick, Parlay


def player_bets(session, user_id: int) -> list[Bet]:
    bets: list[Bet] = []
    straight = (session.query(PaperPick, Game).join(Game, Game.id == PaperPick.game_id)
                .filter(PaperPick.user_id == user_id, PaperPick.parlay_id.is_(None))
                .all())
    for pick, game in straight:
        bets.append(Bet(result=pick.result, stake=pick.stake,
                        profit=(pick.payout or 0.0) if pick.result else 0.0,
                        odds=pick.odds, day=game.date))
    for parlay in session.query(Parlay).filter(Parlay.user_id == user_id).all():
        last_leg = (session.query(func.max(Game.date))
                    .join(PaperPick, PaperPick.game_id == Game.id)
                    .filter(PaperPick.parlay_id == parlay.id).scalar())
        day = last_leg or parlay.created_at.date()
        bets.append(Bet(result=parlay.result, stake=parlay.stake,
                        profit=(parlay.payout or 0.0) if parlay.result else 0.0,
                        odds=parlay.combined_odds, day=day))
    return sorted(bets, key=lambda b: b.day)
```

`backend/api/users.py`:

1. Imports: `from backend.analysis.paper_bets import player_bets`, `from backend.analysis.scorecard import summarize`, `from backend.digest.record import emailed_bets`.
2. Directly after `list_users` (it must precede `@router.get("/{user_id}")`):

```python
MIN_RANKED_BETS = 10


def _board_row(user_id, name, is_model, s) -> dict:
    return {"id": user_id, "name": name, "is_model": is_model,
            "wins": s.wins, "losses": s.losses, "pushes": s.pushes,
            "pending": s.pending, "n": s.n,
            "win_rate": None if s.win_rate is None else round(s.win_rate, 4),
            "roi": None if s.roi is None else round(s.roi, 4),
            "profit": round(s.profit, 2), "ranked": s.n >= MIN_RANKED_BETS}


def _board_order(row: dict):
    if row["ranked"]:
        return (0, -(row["roi"] or 0.0), row["name"].lower())
    return (1, -row["n"], row["name"].lower())


@router.get("/leaderboard")
def leaderboard(request: Request):
    """Every player plus the Model (emailed picks, 1u each), ranked by ROI.

    ROI is profit / stake, so a $1,000 bettor and a 1u model compare fairly.
    Fewer than MIN_RANKED_BETS settled bets: shown, not ranked.
    """
    session = get_session(request.app.state.engine)
    try:
        rows = [_board_row(u.id, u.name, False, summarize(player_bets(session, u.id)))
                for u in session.query(UserProfile).all()]
        rows.append(_board_row(None, "Model", True, summarize(emailed_bets(session, None))))
        rows.sort(key=_board_order)
        return rows
    finally:
        session.close()
```

3. Replace `_compute_period_stats` with a version over `Bet`s (same keys, percentages as before):

```python
def _compute_period_stats(bets) -> dict:
    """Period stats for a player's Bets, through the shared scorecard."""
    s = summarize(bets)
    return {
        "wins": s.wins, "losses": s.losses, "pushes": s.pushes, "total": s.n,
        "win_rate": round(s.win_rate * 100, 1) if s.win_rate is not None else 0,
        "profit": round(s.profit, 2),
        "roi": round(s.roi * 100, 2) if s.roi is not None else 0,
    }
```

4. In `get_user_stats`, replace the `all_picks` query and the four filtered lists with:

```python
        bets = player_bets(session, user_id)
        today = et_today()
        week_start = today - timedelta(days=today.weekday())
        month_start = today.replace(day=1)
        daily = [b for b in bets if b.day == today]
        weekly = [b for b in bets if b.day >= week_start]
        monthly = [b for b in bets if b.day >= month_start]
```

   and the breakdown loop's `day_picks` with `[b for b in bets if b.day == d and b.result is not None]`; pass `bets` for `all_time`.
5. In `list_users` and `get_user`, compute `s = summarize(player_bets(session, u.id))` and set `wins/losses/pushes/pending` from `s`, `win_rate` to `round(s.win_rate * 100, 1) if s.win_rate is not None else 0`, `roi` to `round(s.roi * 100, 2) if s.roi is not None else 0`, and `total_wagered` to `round(sum(b.stake for b in player_bets(session, u.id)), 2)` (compute the list once).

- [ ] **Step 4: Run to verify**

Run: `.venv/Scripts/python.exe -m pytest backend/tests/test_leaderboard.py backend/tests/test_api_users.py -q -p no:warnings`, then the full suite. Expected: all pass.

- [ ] **Step 5: Mutation checks**
  1. Move the `/leaderboard` route below `get_user`: `test_the_route_is_not_shadowed_by_user_id` fails.
  2. In `player_bets`, drop `PaperPick.parlay_id.is_(None)`: `test_a_parlay_counts_once_and_its_legs_never` fails.
  3. `MIN_RANKED_BETS = 10` → `3`: `test_fewer_than_ten_settled_bets_is_unranked_and_sorts_last` fails.

- [ ] **Step 6: Commit**

```bash
git add backend/analysis/paper_bets.py backend/api/users.py backend/tests/test_leaderboard.py
git commit -m "feat(paper): a leaderboard with the model on it, ranked by ROI"
```

---

### Task 8: Frontend — owner key and PINs

**Files:**
- Create: `frontend/src/lib/secrets.ts`, `frontend/src/api/client.test.ts`
- Modify: `frontend/src/api/client.ts`, `frontend/src/types.ts`, `frontend/src/pages/Admin.tsx`, `frontend/src/pages/PaperTrading.tsx`, `frontend/src/components/BetModal.tsx`, `frontend/src/components/BetModal.test.tsx`, `frontend/src/pages/FAQ.tsx`

**Every UI caller of a guarded route** (grep at `545de44`; re-run `grep -rn "api\.users\.\(delete\|grade\|placePick\|placeParlay\|create\)\|api\.backtest\.\|api\.pipeline\.run" frontend/src --include=*.tsx --include=*.ts` and STOP if it lists a caller not here): `BetModal.tsx` (create, placePick), `PaperTrading.tsx` (create, placePick ×2, placeParlay), `Admin.tsx` (delete), `Backtesting.tsx` (runAll, pipeline.run), `hooks/useRefreshData.ts` (pipeline.run). The owner key is attached centrally, so only the PIN callers change.

**Interfaces:**
- Produces:
  - `secrets.ts`: `getOwnerKey(): string | null`, `setOwnerKey(key: string | null): void`, `getPin(userId: number): string | null`, `setPin(userId: number, pin: string | null): void` — owner key in `localStorage['sp.ownerKey']`, PINs in `sessionStorage['sp.pin.<id>']`; every access in `try/catch`
  - `api.users.create(name: string, pin: string)`; `api.users.placePick(userId, data, pin: string)`; `api.users.placeParlay(userId, data, pin: string)`; `api.users.setPin(userId: number, pin: string)`; `api.users.leaderboard(): Promise<LeaderboardRow[]>`; `api.stats.emailed(kind, by): Promise<EmailedGroups>`; `api.stats.emailedTrend(kind): Promise<EmailedTrend>`
  - `types.ts`: `LeaderboardRow`, `EmailedSummary`, `EmailedGroups`, `EmailedTrend` (fields exactly as the backend returns, per Tasks 3 and 7)

- [ ] **Step 1: Write the failing test** — `frontend/src/api/client.test.ts`:

```ts
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { api } from './client'
import { setOwnerKey } from '../lib/secrets'

function lastHeaders(): Record<string, string> {
  const call = (globalThis.fetch as unknown as ReturnType<typeof vi.fn>).mock.calls.at(-1)!
  return (call[1]?.headers ?? {}) as Record<string, string>
}

beforeEach(() => {
  globalThis.fetch = vi.fn().mockResolvedValue(new Response('{}', { status: 200 })) as unknown as typeof fetch
  setOwnerKey(null)
})

describe('auth headers', () => {
  it('sends the owner key on writes once it is stored', async () => {
    await api.users.grade()
    expect(lastHeaders()['X-Owner-Key']).toBeUndefined()
    setOwnerKey('k')
    await api.users.grade()
    expect(lastHeaders()['X-Owner-Key']).toBe('k')
  })

  it('sends the player PIN on a bet, as typed', async () => {
    await api.users.placePick(3, { game_id: 1, pick_type: 'moneyline', pick_value: 'HOME ML', odds: -110, stake: 10 }, '0123')
    expect(lastHeaders()['X-Player-Pin']).toBe('0123')
  })

  it('sends the PIN in the body when joining', async () => {
    await api.users.create('amy', '4321')
    const call = (globalThis.fetch as unknown as ReturnType<typeof vi.fn>).mock.calls.at(-1)!
    expect(JSON.parse(call[1].body as string)).toEqual({ name: 'amy', pin: '4321' })
  })
})
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd frontend && npx vitest run src/api/client.test.ts`
Expected: fails to resolve `../lib/secrets`.

- [ ] **Step 3: Implement**

`frontend/src/lib/secrets.ts`:

```ts
// Owner key: remembered in this browser (localStorage). A player's PIN:
// remembered for this tab only (sessionStorage). Storage can throw (private
// mode, blocked site data), so every access is guarded.
const OWNER = 'sp.ownerKey'
const pinKey = (userId: number) => `sp.pin.${userId}`

export function getOwnerKey(): string | null {
  try { return localStorage.getItem(OWNER) } catch { return null }
}

export function setOwnerKey(key: string | null): void {
  try { if (key) localStorage.setItem(OWNER, key); else localStorage.removeItem(OWNER) } catch { /* unavailable */ }
}

export function getPin(userId: number): string | null {
  try { return sessionStorage.getItem(pinKey(userId)) } catch { return null }
}

export function setPin(userId: number, pin: string | null): void {
  try { if (pin) sessionStorage.setItem(pinKey(userId), pin); else sessionStorage.removeItem(pinKey(userId)) } catch { /* unavailable */ }
}
```

`frontend/src/api/client.ts`:

1. `import { getOwnerKey } from '../lib/secrets'` and add `LeaderboardRow, EmailedGroups, EmailedTrend` to the type import.
2. Add, above `get`:

```ts
function writeHeaders(extra: Record<string, string> = {}): Record<string, string> {
  const headers: Record<string, string> = { 'Content-Type': 'application/json', ...extra }
  const owner = getOwnerKey()
  if (owner) headers['X-Owner-Key'] = owner
  return headers
}
```

3. `post`, `put`, `patch`, `del` use `headers: writeHeaders(extra)`; `post` gains an optional third parameter `extra: Record<string, string> = {}`. (`patch`/`del` previously sent no headers; they now send `writeHeaders()`.)
4. In `api.users`: `create: (name: string, pin: string) => post<{ id: number; name: string }>('/users/', { name, pin })`; `placePick(userId, data, pin: string)` passes `{ 'X-Player-Pin': pin }` as `post`'s third argument; `placeParlay(userId, data, pin: string)` likewise; add `setPin: (id: number, pin: string) => put<{ id: number; pin_set: boolean }>(\`/users/${id}/pin\`, { pin })` and `leaderboard: () => get<LeaderboardRow[]>('/users/leaderboard')`.
5. In `api.stats`: `emailed: (kind: 'game' | 'prop', by: 'week' | 'month' | 'stars') => get<EmailedGroups>(\`/stats/emailed?kind=${kind}&by=${by}\`)` and `emailedTrend: (kind: 'game' | 'prop') => get<EmailedTrend>(\`/stats/emailed/trend?kind=${kind}\`)`.

`frontend/src/types.ts`, append:

```ts
export interface LeaderboardRow {
  id: number | null; name: string; is_model: boolean;
  wins: number; losses: number; pushes: number; pending: number; n: number;
  win_rate: number | null; roi: number | null; profit: number; ranked: boolean;
}

export interface EmailedSummary {
  label: string; wins: number; losses: number; pushes: number; pending: number; n: number;
  win_rate: number | null; range_low: number | null; range_high: number | null;
  break_even: number | null; profit: number; staked: number; roi: number | null;
  verdict: 'above' | 'below' | null;
}

export interface EmailedGroups {
  kind: 'game' | 'prop'; by: 'week' | 'month' | 'stars';
  groups: EmailedSummary[]; total: EmailedSummary;
}

export interface EmailedTrend {
  kind: 'game' | 'prop';
  points: { date: string; units: number }[];
  max_drawdown: number; longest_losing_streak: number;
}
```

`frontend/src/pages/PaperTrading.tsx`:
- Add `const [newPin, setNewPin] = useState('')` and `const [betPin, setBetPin] = useState('')`; `import { getPin, setPin } from '../lib/secrets'`.
- When `selectedUser` changes, prefill: `useEffect(() => { setBetPin(selectedUser ? getPin(selectedUser.id) ?? '' : '') }, [selectedUser])` (add `useEffect` to the react import).
- Join card: a second input `type="password" inputMode="numeric" autoComplete="off" maxLength={6} placeholder="PIN (4–6 digits)"` bound to `newPin`; `handleCreateUser` requires `/^\d{4,6}$/.test(newPin)` (toast `'PIN must be 4–6 digits'` otherwise), calls `api.users.create(newName.trim(), newPin)`, and clears `newPin` on success.
- A PIN input (same attributes, bound to `betPin`) in the Place-a-Pick form and the parlay builder, next to the stake input.
- Each of the three `placePick`/`placeParlay` calls passes `betPin` as the last argument. On success: `setPin(selectedUser.id, betPin)`. In each `catch`, if `e instanceof ApiError && e.status === 401`, call `setPin(selectedUser.id, null)` and `setBetPin('')` before the toast (import `ApiError`). The server's `detail` ("Wrong PIN", "Too many wrong PINs…", "Betting has closed…") is already the toast text.
- The placement toasts no longer need the "Pick graded" branch (bets can no longer grade at placement); simplify to `Pick placed! Balance: …` / `N-leg parlay placed! Potential: …`.

`frontend/src/components/BetModal.tsx`: add `pin` state and a PIN input shown with both the join and the bet controls; `handleCreateUser` calls `api.users.create(userName.trim(), pin)`; `handlePlaceBet` calls `api.users.placePick(userId, {...}, pin)`, stores it with `setPin(userId, pin)` on success and clears it on a 401 as above; prefill from `getPin(userId)` when `userId` resolves. Update `BetModal.test.tsx` so its `placePick` expectation includes the PIN argument and its join test enters a PIN.

`frontend/src/pages/Admin.tsx`: an "Owner key" card at the top — a password input, **Save** (`setOwnerKey(value.trim() || null)`), **Forget** (`setOwnerKey(null)`), and a status line "Owner key saved in this browser" / "No owner key in this browser — admin actions will be refused". Never render the stored value. In `handleDelete`'s catch, a 403 shows "Owner key required — add it above".

`frontend/src/pages/FAQ.tsx`: replace the three paper-trading answers quoted below (search for their opening words) with:
- "Go to the Paper Trading page…" → `'Go to the Paper Trading page, type your name and choose a 4–6 digit PIN, and click "Join". Names are unique (ignoring capitals). Your PIN is needed for every bet; five wrong tries lock betting for 15 minutes. You start with $10,000.'`
- "Click your name on the leaderboard…" → keep the text but end it with `' Bets close when the game starts.'` instead of the sentence about grading.
- "The leaderboard ranks users by current balance…" → `'The leaderboard ranks everyone by ROI (profit divided by amount staked), so a big bankroll does not beat a sharp one. "Model" is the daily email\u2019s picks at 1 unit each. You need 10 settled bets to be ranked; until then you are listed but unranked.'`

- [ ] **Step 4: Run to verify**

Run: `cd frontend && npx vitest run && npx tsc -b --noEmit && npx eslint .`
Expected: all green, the new `client.test.ts` included.

- [ ] **Step 5: Mutation check** — in `writeHeaders`, drop the `if (owner)` line: `sends the owner key on writes once it is stored` fails.

- [ ] **Step 6: Commit**

```bash
git add frontend/src
git commit -m "feat(ui): owner key on Admin, a PIN on every bet"
```

---

### Task 9: Frontend — the leaderboard bar

**Files:**
- Create: `frontend/src/hooks/useRankings.ts`, `frontend/src/components/LeaderboardBar.tsx`, `frontend/src/components/LeaderboardBar.test.tsx`
- Modify: `frontend/src/pages/PaperTrading.tsx` (the `Compact Leaderboard Bar` block), `frontend/src/App.css`

**Interfaces:**
- Consumes: Task 8 `api.users.leaderboard()`, `LeaderboardRow`
- Produces: `<LeaderboardBar rows={LeaderboardRow[]} selectedId={number | null} onSelect={(id: number) => void} />`

- [ ] **Step 1: Write the failing test** — `frontend/src/components/LeaderboardBar.test.tsx`:

```tsx
import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import LeaderboardBar from './LeaderboardBar'
import type { LeaderboardRow } from '../types'

const row = (o: Partial<LeaderboardRow>): LeaderboardRow => ({
  id: 1, name: 'x', is_model: false, wins: 0, losses: 0, pushes: 0, pending: 0,
  n: 0, win_rate: null, roi: null, profit: 0, ranked: false, ...o,
})

describe('LeaderboardBar', () => {
  it('numbers only ranked rows and marks the rest', () => {
    render(<LeaderboardBar selectedId={null} onSelect={vi.fn()} rows={[
      row({ id: 1, name: 'Amy', ranked: true, n: 12, roi: 0.051, win_rate: 0.58 }),
      row({ id: null, name: 'Model', is_model: true, ranked: true, n: 20, roi: 0.02, win_rate: 0.55 }),
      row({ id: 3, name: 'Bo', ranked: false, n: 4 }),
    ]} />)
    expect(screen.getByText('#1')).toBeInTheDocument()
    expect(screen.getByText('#2')).toBeInTheDocument()
    expect(screen.getByText('needs 10 bets')).toBeInTheDocument()
    expect(screen.getByText('+5.1%')).toBeInTheDocument()
  })

  it('selects players but not the model', async () => {
    const onSelect = vi.fn()
    render(<LeaderboardBar selectedId={null} onSelect={onSelect} rows={[
      row({ id: 7, name: 'Amy', ranked: true, n: 10, roi: 0.1 }),
      row({ id: null, name: 'Model', is_model: true, ranked: true, n: 10, roi: 0.0 }),
    ]} />)
    await userEvent.click(screen.getByText('Amy'))
    await userEvent.click(screen.getByText('Model'))
    expect(onSelect).toHaveBeenCalledTimes(1)
    expect(onSelect).toHaveBeenCalledWith(7)
  })

  it('shows a dash, never NaN, with no settled bets', () => {
    render(<LeaderboardBar selectedId={null} onSelect={vi.fn()} rows={[row({ name: 'New' })]} />)
    expect(screen.queryByText(/NaN/)).toBeNull()
    expect(screen.getAllByText('—').length).toBe(2)    // ROI and win rate
  })
})
```

- [ ] **Step 2: Run to verify it fails** — `cd frontend && npx vitest run src/components/LeaderboardBar.test.tsx`. Expected: cannot resolve `./LeaderboardBar`.

- [ ] **Step 3: Implement**

`frontend/src/hooks/useRankings.ts`:

```ts
import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'
import type { LeaderboardRow } from '../types'

export function useRankings() {
  return useQuery<LeaderboardRow[]>({
    queryKey: ['users', 'rankings'],
    queryFn: () => api.users.leaderboard(),
    refetchInterval: 30000,
  })
}
```

`frontend/src/components/LeaderboardBar.tsx`:

```tsx
import type { KeyboardEvent } from 'react'
import type { LeaderboardRow } from '../types'

const pct = (x: number | null, signed = false) =>
  x == null ? '—' : `${signed && x >= 0 ? '+' : ''}${(x * 100).toFixed(1)}%`

interface Props {
  rows: LeaderboardRow[]
  selectedId: number | null
  onSelect: (id: number) => void
}

export default function LeaderboardBar({ rows, selectedId, onSelect }: Props) {
  let rank = 0
  return (
    <div className="leaderboard-bar">
      {rows.map(r => {
        const label = r.ranked ? `#${++rank}` : 'needs 10 bets'
        const selectable = !r.is_model && r.id != null
        return (
          <div
            key={r.is_model ? 'model' : r.id}
            className={`leaderboard-entry${r.is_model ? ' model' : ''}${selectedId != null && r.id === selectedId ? ' selected' : ''}`}
            {...(selectable ? {
              role: 'button', tabIndex: 0, 'aria-label': `Select ${r.name}`,
              onClick: () => onSelect(r.id as number),
              onKeyDown: (e: KeyboardEvent) => {
                if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onSelect(r.id as number) }
              },
            } : {})}
          >
            <span className="leaderboard-rank">{label}</span>
            <span className="leaderboard-name">{r.name}</span>
            <span className="leaderboard-roi" style={{ color: (r.roi ?? 0) >= 0 ? 'var(--green)' : 'var(--red)' }}>
              {pct(r.roi, true)}
            </span>
            <span className="leaderboard-record">{r.wins}-{r.losses}-{r.pushes}</span>
            <span className="leaderboard-winrate">{pct(r.win_rate)}</span>
            {r.pending > 0 && <span className="leaderboard-pending">{r.pending} pending</span>}
          </div>
        )
      })}
    </div>
  )
}
```

`frontend/src/App.css`, append:

```css
.leaderboard-entry.model { border-style: dashed; }
.leaderboard-entry.model .leaderboard-name { font-style: italic; }
.leaderboard-record, .leaderboard-winrate, .leaderboard-pending { font-size: 0.8rem; color: var(--text-muted); }
```

`frontend/src/pages/PaperTrading.tsx`: `import LeaderboardBar from '../components/LeaderboardBar'` and `import { useRankings } from '../hooks/useRankings'`; add `const { data: rankings = [] } = useRankings()`. Replace the `<div className="leaderboard-bar">…</div>` block (the `users.map` and its empty state) with:

```tsx
      <LeaderboardBar
        rows={rankings}
        selectedId={selectedUser?.id ?? null}
        onSelect={id => { const u = users.find(x => x.id === id); if (u) selectUser(u) }}
      />
      {users.length === 0 && (
        <div style={{ color: 'var(--text-muted)', fontSize: '0.85rem', padding: '0.5rem' }}>
          No players yet. Join above!
        </div>
      )}
```

Keep `useLeaderboard` (it still supplies `users` for selection). If `getStreakIndicator` becomes unused, delete it (eslint will say).

- [ ] **Step 4: Run to verify** — `cd frontend && npx vitest run && npx tsc -b --noEmit && npx eslint .` Expected: green.

- [ ] **Step 5: Mutation check** — in `LeaderboardBar`, `r.ranked ? \`#${++rank}\`` → `\`#${++rank}\``: `numbers only ranked rows and marks the rest` fails.

- [ ] **Step 6: Commit**

```bash
git add frontend/src
git commit -m "feat(ui): leaderboard ranked by ROI, with the model on it"
```

---

### Task 10: Frontend — the Emailed picks view on Track Record

**Files:**
- Create: `frontend/src/hooks/useEmailedRecord.ts`, `frontend/src/components/EmailedRecord.tsx`, `frontend/src/components/EmailedRecord.test.tsx`
- Modify: `frontend/src/pages/TrackRecord.tsx`

**Interfaces:**
- Consumes: Task 8 `api.stats.emailed`, `api.stats.emailedTrend`, `EmailedGroups`, `EmailedTrend`
- Produces: `useEmailedRecord(kind, by) -> { groups: UseQueryResult<EmailedGroups>, trend: UseQueryResult<EmailedTrend> }`; `<EmailedRecord />` (reads/writes `kind` and `by` search params)

- [ ] **Step 1: Write the failing test** — `frontend/src/components/EmailedRecord.test.tsx`:

```tsx
import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import EmailedRecord from './EmailedRecord'
import { api } from '../api/client'
import type { EmailedSummary } from '../types'

vi.mock('../api/client', () => ({ api: { stats: { emailed: vi.fn(), emailedTrend: vi.fn() } } }))

const s = (o: Partial<EmailedSummary>): EmailedSummary => ({
  label: 'all', wins: 0, losses: 0, pushes: 0, pending: 0, n: 0, win_rate: null,
  range_low: null, range_high: null, break_even: null, profit: 0, staked: 0,
  roi: null, verdict: null, ...o,
})

function renderIt() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}><MemoryRouter><EmailedRecord /></MemoryRouter></QueryClientProvider>)
}

describe('EmailedRecord', () => {
  it('shows an empty state before anything has settled, never NaN', async () => {
    vi.mocked(api.stats.emailed).mockResolvedValue({ kind: 'game', by: 'week', groups: [], total: s({ pending: 5 }) })
    vi.mocked(api.stats.emailedTrend).mockResolvedValue({ kind: 'game', points: [], max_drawdown: 0, longest_losing_streak: 0 })
    renderIt()
    expect(await screen.findByText(/No emailed picks have settled yet/)).toBeInTheDocument()
    expect(screen.queryByText(/NaN/)).toBeNull()
  })

  it('shows win rate beside break-even and colours a clear verdict', async () => {
    vi.mocked(api.stats.emailed).mockResolvedValue({
      kind: 'game', by: 'week',
      groups: [s({ label: '2026-09-28', wins: 40, losses: 5, n: 45, win_rate: 0.889, range_low: 0.79,
                   range_high: 0.94, break_even: 0.524, roi: 0.7, verdict: 'above' })],
      total: s({ wins: 40, losses: 5, n: 45, win_rate: 0.889, break_even: 0.524, roi: 0.7, verdict: 'above' }),
    })
    vi.mocked(api.stats.emailedTrend).mockResolvedValue({ kind: 'game', points: [{ date: '2026-09-28', units: 31.4 }], max_drawdown: 2, longest_losing_streak: 1 })
    renderIt()
    const cell = await screen.findByTestId('winrate-2026-09-28')
    expect(cell).toHaveTextContent('88.9%')
    expect(cell.className).toContain('verdict-above')
    expect(screen.getByTestId('breakeven-2026-09-28')).toHaveTextContent('52.4%')
  })
})
```

- [ ] **Step 2: Run to verify it fails** — `cd frontend && npx vitest run src/components/EmailedRecord.test.tsx`. Expected: cannot resolve `./EmailedRecord`.

- [ ] **Step 3: Implement**

`frontend/src/hooks/useEmailedRecord.ts`:

```ts
import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'
import type { EmailedGroups, EmailedTrend } from '../types'

export type EmailedKind = 'game' | 'prop'
export type EmailedBy = 'week' | 'month' | 'stars'

export function useEmailedRecord(kind: EmailedKind, by: EmailedBy) {
  const groups = useQuery<EmailedGroups>({
    queryKey: ['emailed', kind, by],
    queryFn: () => api.stats.emailed(kind, by),
  })
  const trend = useQuery<EmailedTrend>({
    queryKey: ['emailed-trend', kind],
    queryFn: () => api.stats.emailedTrend(kind),
  })
  return { groups, trend }
}
```

`frontend/src/components/EmailedRecord.tsx`:

```tsx
import { useSearchParams } from 'react-router-dom'
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { useEmailedRecord, type EmailedBy, type EmailedKind } from '../hooks/useEmailedRecord'
import type { EmailedSummary } from '../types'

const pct = (x: number | null) => (x == null ? '—' : `${(x * 100).toFixed(1)}%`)
const units = (x: number) => `${x >= 0 ? '+' : ''}${x.toFixed(2)}u`
const BYS: { key: EmailedBy; label: string }[] = [
  { key: 'week', label: 'Week' }, { key: 'month', label: 'Month' }, { key: 'stars', label: 'Stars' },
]

function groupLabel(by: EmailedBy, label: string) {
  if (by === 'stars') return label === 'unrated' ? 'Unrated' : `${label}★`
  if (by === 'week') return `Week of ${label}`
  return label
}

export default function EmailedRecord() {
  const [params, setParams] = useSearchParams()
  const kind = (params.get('kind') === 'prop' ? 'prop' : 'game') as EmailedKind
  const by = (['week', 'month', 'stars'].includes(params.get('by') ?? '') ? params.get('by') : 'week') as EmailedBy
  const set = (k: string, v: string) => { const next = new URLSearchParams(params); next.set(k, v); setParams(next) }
  const { groups, trend } = useEmailedRecord(kind, by)

  if (groups.error || trend.error) {
    return <div className="empty-state"><div className="empty-state-title text-red">Error: {((groups.error || trend.error) as Error).message}</div></div>
  }
  if (groups.isLoading || trend.isLoading) return <div className="loading"><div className="spinner" /> Loading...</div>

  const total = groups.data!.total
  const rows = groups.data!.groups
  const points = trend.data!.points

  return (
    <div>
      <div className="tab-group" style={{ marginBottom: '1rem' }}>
        {(['game', 'prop'] as EmailedKind[]).map(k => (
          <button key={k} className={`tab${kind === k ? ' active' : ''}`} aria-pressed={kind === k} onClick={() => set('kind', k)}>
            {k === 'game' ? 'Game picks' : 'Props'}
          </button>
        ))}
      </div>

      {total.n === 0 ? (
        <div className="empty-state">
          <div className="empty-state-title">No emailed picks have settled yet</div>
          <div>{total.pending} pending. Recording began 2026-09-28.</div>
        </div>
      ) : (
        <>
          <div className="stat-grid">
            <div className="stat-card"><div className="stat-label">Win rate</div>
              <div className="stat-value">{pct(total.win_rate)}</div>
              <div className="stat-sub">90% range {pct(total.range_low)}–{pct(total.range_high)}</div></div>
            <div className="stat-card"><div className="stat-label">Needed to break even</div>
              <div className="stat-value">{pct(total.break_even)}</div></div>
            <div className="stat-card"><div className="stat-label">ROI</div>
              <div className="stat-value">{pct(total.roi)}</div>
              <div className="stat-sub">{units(total.profit)} on {total.n} settled</div></div>
            <div className="stat-card"><div className="stat-label">Record</div>
              <div className="stat-value">{total.wins}-{total.losses}-{total.pushes}</div>
              <div className="stat-sub">{total.pending} pending</div></div>
          </div>

          <div className="card" style={{ marginTop: '1rem' }}>
            <div className="section-header">Cumulative units</div>
            <ResponsiveContainer width="100%" height={220}>
              <LineChart data={points} margin={{ top: 8, right: 16, bottom: 0, left: 0 }}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis dataKey="date" tick={{ fontSize: 11 }} />
                <YAxis tick={{ fontSize: 11 }} />
                <Tooltip formatter={(v) => units(Number(v))} />
                <Line type="monotone" dataKey="units" stroke="var(--accent)" dot={false} strokeWidth={2} />
              </LineChart>
            </ResponsiveContainer>
            <div className="stat-sub">Max drawdown {trend.data!.max_drawdown.toFixed(2)}u · longest losing streak {trend.data!.longest_losing_streak}</div>
          </div>
        </>
      )}

      <div className="tab-group" style={{ margin: '1rem 0 0.5rem' }}>
        {BYS.map(b => (
          <button key={b.key} className={`tab${by === b.key ? ' active' : ''}`} aria-pressed={by === b.key} onClick={() => set('by', b.key)}>{b.label}</button>
        ))}
      </div>
      <div className="table-wrap">
        <table className="table">
          <thead><tr><th>Period</th><th>W-L-P</th><th>Win rate</th><th>90% range</th><th>Break-even</th><th>Units</th><th>ROI</th><th>Pending</th></tr></thead>
          <tbody>
            {rows.map((g: EmailedSummary) => (
              <tr key={g.label}>
                <td>{groupLabel(by, g.label)}</td>
                <td>{g.wins}-{g.losses}-{g.pushes}</td>
                <td data-testid={`winrate-${g.label}`} className={g.verdict ? `verdict-${g.verdict}` : ''}>{pct(g.win_rate)}</td>
                <td>{pct(g.range_low)}–{pct(g.range_high)}</td>
                <td data-testid={`breakeven-${g.label}`}>{pct(g.break_even)}</td>
                <td>{units(g.profit)}</td>
                <td>{pct(g.roi)}</td>
                <td>{g.pending}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="stat-sub">Colored only when the whole 90% range is above (green) or below (red) break-even; otherwise the sample cannot yet tell.</p>
    </div>
  )
}
```

`frontend/src/App.css`, append:

```css
.verdict-above { color: var(--green); font-weight: 600; }
.verdict-below { color: var(--red); font-weight: 600; }
```

(If `.stat-grid`, `.stat-card`, `.stat-value`, `.stat-sub` or `--accent` do not exist in `App.css`/`index.css`, use the class names `TrackRecord.tsx` already uses for its summary cards — check with `grep -n "stat-" frontend/src/pages/TrackRecord.tsx frontend/src/App.css`.)

`frontend/src/pages/TrackRecord.tsx`:
1. `import EmailedRecord from '../components/EmailedRecord'`; read `const source = searchParams.get('source') === 'emailed' ? 'emailed' : 'all'`.
2. `setSport` and `setRange` currently rebuild params from scratch and would drop `source`; in both, start `next` with `if (source === 'emailed') next.source = 'emailed'`.
3. Immediately after the hooks and **before** the `if (error)` / `if (loading)` early returns, add:

```tsx
  const sourceSwitch = (
    <div className="tab-group" style={{ marginBottom: '1rem' }}>
      {(['all', 'emailed'] as const).map(s => (
        <button key={s} className={`tab${source === s ? ' active' : ''}`} aria-pressed={source === s}
          onClick={() => setSearchParams(s === 'emailed' ? { source: 'emailed' } : {})}>
          {s === 'all' ? 'All picks' : 'Emailed picks'}
        </button>
      ))}
    </div>
  )
  if (source === 'emailed') {
    return <div><div className="page-header"><h2 className="page-title">Track Record</h2></div>{sourceSwitch}<EmailedRecord /></div>
  }
```

4. Render `{sourceSwitch}` directly under the existing page header in the all-picks view.

- [ ] **Step 4: Run to verify** — `cd frontend && npx vitest run && npx tsc -b --noEmit && npx eslint .` Expected: green.

- [ ] **Step 5: Mutation check** — in `EmailedRecord`, `className={g.verdict ? \`verdict-${g.verdict}\` : ''}` → `className=""`: `shows win rate beside break-even and colours a clear verdict` fails.

- [ ] **Step 6: Visual check in Chrome** (global CLAUDE.md: verify UI visually, not from markup). `cd frontend && npm run dev`, open `/track-record?source=emailed` with claude-in-chrome at desktop width and at 414px: the switch, the Game/Prop toggle, cards (or the empty state), chart and tables render without horizontal scroll. Then `/paper-trading`: the leaderboard shows Model dashed/italic, unranked rows say "needs 10 bets", and the join and bet forms show a PIN field. Record what was checked in the commit message.

- [ ] **Step 7: Commit**

```bash
git add frontend/src
git commit -m "feat(ui): the emailed record on Track Record, by week, month and stars"
```

---

### Task 11: Share the link — and prove it works

**Files:**
- Create: `scripts/share.ps1`
- Modify: `plans/README.md` (status row), `.gitignore` (add `app.log`, `app.out.log`, `tunnel.log` if not ignored)

**Interfaces:**
- Consumes: everything above, merged and green.
- Produces: `powershell -File scripts/share.ps1` prints the public URL; `-Stop` stops the tunnel.

- [ ] **Step 1: Write the script** — `scripts/share.ps1`:

```powershell
<#
.SYNOPSIS
    Share the app with friends: make sure the app server runs current code,
    open a Cloudflare quick tunnel to it, and print the public URL.

.DESCRIPTION
    The URL changes on every run (quick tunnels are anonymous). It works
    only while this laptop is awake. Owner routes need the owner key and bets
    need the player's PIN -- both enforced by the server, not by this script.

    -Stop stops the tunnel (the app server is left running).
#>
param([switch]$Stop)
$ErrorActionPreference = 'Stop'

$Repo = 'C:\Users\mwill\Documents\mwilliams2733\sports_picks'
$Python = Join-Path $Repo '.venv\Scripts\python.exe'
$Cloudflared = 'C:\Program Files (x86)\cloudflared\cloudflared.exe'
$AppLog = Join-Path $Repo 'app.log'
$TunnelLog = Join-Path $Repo 'tunnel.log'

$tunnels = Get-CimInstance Win32_Process -Filter "Name = 'cloudflared.exe'" |
    Where-Object { $_.CommandLine -like '*127.0.0.1:8000*' }
if ($Stop) {
    $tunnels | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
    Write-Output "Tunnel stopped ($($tunnels.Count) process(es))."
    exit 0
}

Set-Location $Repo

# 1. The built frontend must be at least as new as HEAD.
$headTime = [DateTimeOffset]::FromUnixTimeSeconds([int64](git log -1 --format=%ct)).LocalDateTime
$index = Join-Path $Repo 'frontend\dist\index.html'
if (-not (Test-Path $index) -or (Get-Item $index).LastWriteTime -lt $headTime) {
    Write-Output 'Building the frontend...'
    Push-Location (Join-Path $Repo 'frontend'); npm run build | Out-Null; Pop-Location
}

# 2. The app server must be running, and started after HEAD.
$app = Get-CimInstance Win32_Process -Filter "Name like '%python%'" |
    Where-Object { $_.CommandLine -like '*uvicorn*backend.api.main:app*' }
if ($app -and ($app | Where-Object { $_.CreationDate -lt $headTime })) {
    Write-Output 'App server predates HEAD; restarting it.'
    $app | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
    Start-Sleep -Seconds 2
    $app = $null
}
if (-not $app) {
    if (Test-Path $AppLog) { Move-Item $AppLog "$AppLog.prev" -Force }
    Start-Process -FilePath $Python -ArgumentList '-m', 'uvicorn', 'backend.api.main:app',
        '--host', '127.0.0.1', '--port', '8000' -WorkingDirectory $Repo -WindowStyle Hidden `
        -RedirectStandardError $AppLog -RedirectStandardOutput "$AppLog.out" | Out-Null
    Start-Sleep -Seconds 5
}
$health = Invoke-RestMethod -Uri 'http://127.0.0.1:8000/health' -TimeoutSec 10
if ($health.status -ne 'ok') { Write-Error 'App server is not healthy; see app.log'; exit 1 }

# 3. One tunnel at a time.
$tunnels | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
if (Test-Path $TunnelLog) { Remove-Item $TunnelLog -Force }
Start-Process -FilePath $Cloudflared -ArgumentList 'tunnel', '--url', 'http://127.0.0.1:8000', '--logfile', $TunnelLog `
    -WindowStyle Hidden | Out-Null

$url = $null
for ($i = 0; $i -lt 60 -and -not $url; $i++) {
    Start-Sleep -Seconds 1
    if (Test-Path $TunnelLog) {
        $m = Select-String -Path $TunnelLog -Pattern 'https://[a-z0-9-]+\.trycloudflare\.com' | Select-Object -First 1
        if ($m) { $url = $m.Matches[0].Value }
    }
}
if (-not $url) { Write-Error "No tunnel URL after 60s; see $TunnelLog"; exit 1 }
Write-Output "Share this link: $url/paper-trading"
```

- [ ] **Step 2: Run it**

Run: `powershell -NoProfile -ExecutionPolicy Bypass -File scripts/share.ps1`
Expected: `Share this link: https://<name>.trycloudflare.com/paper-trading`.

- [ ] **Step 3: Prove it with real requests through the public URL** (a running tunnel is not evidence). With `$U` the printed origin:

```bash
curl -s "$U/users/leaderboard" | head -c 400          # JSON list containing "Model"
curl -s -o /dev/null -w "%{http_code}\n" -X POST "$U/pipeline/run"                  # 403
curl -s -o /dev/null -w "%{http_code}\n" -X POST "$U/users/1/picks" \
  -H "Content-Type: application/json" \
  -d '{"game_id":1,"pick_type":"moneyline","pick_value":"HOME ML","odds":-110,"stake":1}'   # 401 or 400, never 200
curl -s "$U/stats/emailed?kind=prop&by=week" | head -c 300   # the 2026-09-28 props
```

Any `200` on the two POSTs is a STOP condition. Then open `$U/track-record?source=emailed` and `$U/paper-trading` in Chrome (claude-in-chrome) and confirm they render the same as locally.

- [ ] **Step 4: Restart the scheduler** — Tasks 2 and 6 change code it runs (`record_emailed`, `grade_pending_picks`): copy `scheduler.log` aside, then stop and restart it per memory `sports-picks-scheduler-operations`.

- [ ] **Step 5: Commit and record**

```bash
git add scripts/share.ps1 .gitignore plans/README.md
git commit -m "feat(ops): share.ps1 -- current app server, quick tunnel, printed link"
```

In `plans/README.md`, add row 026 with the commits, test counts, mutation checks and the verified public-URL responses.

---

## Self-review (author, 2026-09-28)

- **Spec coverage:** §1 scorecard → Task 1; §2 emailed bets + confidence → Tasks 2–3; §3 players' bets → Task 7; §4 endpoints → Tasks 3, 7; §5 screens → Tasks 8–10; §6 security → Tasks 4, 5, 11; data-model changes → Tasks 2, 5; testing strategy → every task's tests + Task 10 Step 6 + Task 11 Step 3; sequencing (security before sharing) → Task order, Task 11 last. Added beyond the spec: Task 6 (three defects found while planning, required for a fair leaderboard).
- **Placeholders:** none; every code step carries code.
- **Type consistency:** `Bet`/`Summary.to_dict()` keys match `EmailedSummary`; leaderboard dict keys match `LeaderboardRow`; `emailed_bets(session, kind)`, `player_bets(session, user_id)`, `balance_of(session, user)`, `settle_parlays(session)` are named identically wherever used.
- **Review Focus:** five lines, each with a test: Task 5 (names, leading-zero PIN), Tasks 3 and 10 (empty record), Task 6 (canceled parlay leg), Task 1 (one trend point per day).

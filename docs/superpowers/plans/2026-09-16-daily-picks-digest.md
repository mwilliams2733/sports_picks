# Daily Picks Digest Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Send one email per day at 11:00 ET listing the top 5 picks per active sport, plus a separate props section, each pick carrying a short plain-language rationale.

**Architecture:** Strategies emit structured `PickFactor` codes as they compute; those persist to `picks.rationale_json`. A pure selector ranks picks per active sport, a pure renderer turns the selection into HTML + text, and a thin sender POSTs to Resend. One APScheduler cron job triggers it. Three of the four new modules are pure functions, so the whole selection-and-rendering path tests without a network or a clock.

**Tech Stack:** Python 3.12, SQLAlchemy 2.0 (SQLite), APScheduler, httpx (already a dependency — used for the Resend REST call so no new package is added), pytest.

**Spec:** `docs/superpowers/specs/2026-09-16-daily-picks-digest-design.md`

## Global Constraints

- Python 3.12. Tests are pytest. Run from repo root with `.venv/Scripts/python.exe -m pytest backend/tests -q`.
- Baseline at time of writing: **400 passed** at commit `295af51`. Every task must leave the suite green.
- There is **no ruff/mypy/pytest config** in this repo — do not add one as part of this work.
- Follow existing test conventions: plain `def test_*` functions, no classes, `create_app(":memory:")` for API tests, module-level `_seed_*` helpers. Model after `backend/tests/test_api_picks.py`.
- Migrations follow the existing hand-rolled pattern in `backend/database.py` (`migrate_*(engine)` called from `create_app`). **Do not introduce Alembic.**
- Secrets are referenced by name only, never hardcoded and never logged. `RESEND_API_KEY` comes from the environment (populated from `C:\Users\mwill\.secrets\shared.env`).
- Never fabricate a rationale. A factor may only be emitted when the strategy actually computed the underlying signal.
- Repo convention: **derive, don't duplicate.** When two code paths must agree, make one call the other.
- A test that still passes when you break the implementation is not a test. For each behavioral guard, confirm it fails against the unfixed code before treating it as a guard.
- Conventional commits (`feat(digest): ...`, `test(digest): ...`). Commit after each task.

---

## File Structure

| File | Responsibility |
|---|---|
| `backend/data_types.py` (modify) | Add `PickFactor` dataclass; add `factors` field to `Pick` |
| `backend/analysis/rationale.py` (create) | **Only** place that holds human-readable factor prose |
| `backend/database.py` (modify) | `migrate_pick_rationale(engine)` |
| `backend/api/main.py` (modify) | Call the new migration |
| `backend/models.py` (modify) | `PickModel.rationale_json` column |
| `backend/pipeline/pick_generator.py` (modify) | Persist `rationale_json` + `model_prob` |
| `backend/analysis/variants/*.py` (modify, 5 files) | Emit factors |
| `backend/digest/selector.py` (create) | Pure: choose + rank what goes in the email |
| `backend/digest/render.py` (create) | Pure: selection → (subject, html, text) |
| `backend/digest/sender.py` (create) | Only unit that touches the network |
| `backend/digest/job.py` (create) | `send_daily_digest(config, engine)` orchestration |
| `backend/pipeline/scheduler.py` (modify) | Register the 11:00 ET cron job |
| `config.yaml` (modify) | `digest:` block |
| `.env.example` (modify) | `RESEND_API_KEY` name + placeholder |

---

### Task 1: PickFactor type and the rationale renderer

Pure functions only. No persistence, no strategy changes yet.

**Files:**
- Modify: `backend/data_types.py`
- Create: `backend/analysis/rationale.py`
- Test: `backend/tests/test_rationale.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `PickFactor(code: str, side: str, strength: str)` — frozen dataclass
  - `render_factor(factor: PickFactor, home_team: str, away_team: str) -> str`
  - `render_rationale(factors: list[PickFactor], home_team: str, away_team: str, limit: int = 2) -> str`

**Factor vocabulary** (exact codes — later tasks must use these strings):

| Code | Underlying signal |
|---|---|
| `rating_gap` | `TeamStats.elo_rating` differential |
| `recent_form` | `TeamStats.point_diff` differential |
| `net_rating` | `(offensive_rating - defensive_rating)` differential |
| `schedule_fatigue` | `TeamStats.is_schedule_fatigued` |
| `lookahead_spot` | `TeamStats.is_lookahead_spot` |
| `rest_advantage` | `TeamStats.rest_days` differential |
| `pitcher_edge` | `TeamStats.pitcher_skill_score` (MLB only) |
| `season_avg_vs_line` | prop: `PropAnalysis.season_avg` vs `line` |
| `recent_trend_vs_line` | prop: `PropAnalysis.recent_avg` vs `line` |
| `stale_stats` | prop: `PropAnalysis.is_stale` — a caveat |

`side` is one of `"home"`, `"away"`, `"over"`, `"under"`.
`strength` is one of `"slight"`, `"moderate"`, `"strong"`.

> **`side` always means THE SIDE THE FACTOR FAVORS** — never "the side the
> factor is about". This matters for the two negative factors:
> `schedule_fatigue` and `lookahead_spot` favor the side that is *not*
> affected, so their templates name `{other}`. A factor emitted with
> `side="home"` for `schedule_fatigue` means the **away** team is the tired
> one, and the rendered sentence names the away team. Getting this backwards
> names the wrong team in an email to real readers, so both Task 1's
> renderer and Task 3's `_build_factors` must follow this rule.

> **Deliberate divergence from the spec's factor table.** The spec listed
> `home_advantage` and `model_consensus`; this plan drops both and adds
> `net_rating` and `rest_advantage` instead. Reason: reading
> `EnsembleStrategy._count_agreeing_models` shows the model compares exactly
> three things — `point_diff`, `elo_rating`, and `(offensive_rating -
> defensive_rating)`. So `net_rating` is a real signal that was missing from
> the spec's list, while `model_consensus` ("3 of 3 agree") is redundant once
> the three are named individually, and `home_advantage` is a per-sport
> constant that applies to every home pick and therefore explains nothing
> about *this* pick. `rest_advantage` uses `TeamStats.rest_days`, which is
> already populated. Use the table above, not the spec's.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_rationale.py`:

```python
from backend.data_types import PickFactor
from backend.analysis.rationale import render_factor, render_rationale


def test_render_rating_gap_home_strong():
    f = PickFactor(code="rating_gap", side="home", strength="strong")
    assert render_factor(f, "Chiefs", "Bills") == "Rating gap strongly favors Chiefs"


def test_render_schedule_fatigue_names_the_tired_team_not_the_favored_one():
    # `side` is always THE SIDE THE FACTOR FAVORS. schedule_fatigue favors the
    # rested team, so side="home" means the AWAY team is the tired one, and
    # the template names that away team.
    f = PickFactor(code="schedule_fatigue", side="home", strength="moderate")
    assert render_factor(f, "Chiefs", "Bills") == "Bills on a compressed schedule"


def test_unknown_code_renders_empty_not_raises():
    f = PickFactor(code="not_a_real_code", side="home", strength="strong")
    assert render_factor(f, "Chiefs", "Bills") == ""


def test_render_rationale_joins_two_strongest():
    # All three favor the home side (Chiefs), which is what a real pick looks
    # like — factors for one pick all point the same way.
    factors = [
        PickFactor(code="rating_gap", side="home", strength="strong"),
        PickFactor(code="schedule_fatigue", side="home", strength="moderate"),
        PickFactor(code="recent_form", side="home", strength="slight"),
    ]
    out = render_rationale(factors, "Chiefs", "Bills", limit=2)
    assert out == "Rating gap strongly favors Chiefs; Bills on a compressed schedule."


def test_render_rationale_empty_factors_returns_empty_string():
    assert render_rationale([], "Chiefs", "Bills") == ""


def test_render_rationale_skips_unknown_codes():
    factors = [
        PickFactor(code="bogus", side="home", strength="strong"),
        PickFactor(code="rating_gap", side="home", strength="slight"),
    ]
    assert render_rationale(factors, "Chiefs", "Bills") == "Rating gap slightly favors Chiefs."
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest backend/tests/test_rationale.py -q`
Expected: FAIL — `ImportError: cannot import name 'PickFactor'`

- [ ] **Step 3: Add the PickFactor dataclass**

In `backend/data_types.py`, after the `OddsSnapshot` dataclass:

```python
@dataclass(frozen=True)
class PickFactor:
    """One reason a strategy favored a side.

    Only emit a factor when the strategy actually computed the underlying
    signal — these strings are shown to readers as the reasoning behind a
    pick, so a factor that was not used is a fabrication.
    """
    code: str      # see backend/analysis/rationale.py FACTOR_TEMPLATES
    side: str      # "home" | "away" | "over" | "under"
    strength: str  # "slight" | "moderate" | "strong"
```

Then add the field to `Pick` (keep it last so existing positional construction is unaffected):

```python
@dataclass
class Pick:
    game_id: int
    pick_type: str
    pick_value: str
    confidence: int
    edge_pct: float
    model_probability: float
    implied_probability: float
    odds_at_pick: int
    suggested_unit_size: float = 1.0
    factors: list[PickFactor] = field(default_factory=list)
```

`field` is already imported at the top of that file.

- [ ] **Step 4: Write the renderer**

Create `backend/analysis/rationale.py`:

```python
"""Human-readable rationale for a pick.

This module is the ONLY place that holds prose about why a pick was made.
Strategies emit structured PickFactor codes; everything a reader sees is
rendered here, so the wording stays consistent and testable.
"""
from backend.data_types import PickFactor

_STRENGTH_ADVERB = {
    "slight": "slightly",
    "moderate": "modestly",
    "strong": "strongly",
}

# {team} is the side the factor favors; {other} is the opposing team.
FACTOR_TEMPLATES = {
    "rating_gap": "Rating gap {adverb} favors {team}",
    "recent_form": "Recent scoring margin {adverb} favors {team}",
    "net_rating": "Net efficiency {adverb} favors {team}",
    "schedule_fatigue": "{other} on a compressed schedule",
    "lookahead_spot": "{other} may be looking ahead to its next game",
    "rest_advantage": "{team} has the rest advantage",
    "pitcher_edge": "Starting pitcher matchup favors {team}",
    "season_avg_vs_line": "Season average sits {adverb} on the {side} of this line",
    "recent_trend_vs_line": "Recent games trend {adverb} to the {side}",
    "stale_stats": "Caution: player stats may be out of date",
}

_STRENGTH_ORDER = {"strong": 0, "moderate": 1, "slight": 2}


def render_factor(factor: PickFactor, home_team: str, away_team: str) -> str:
    """Render one factor. Unknown codes render as "" rather than raising —
    a future strategy adding a factor must never break the digest."""
    template = FACTOR_TEMPLATES.get(factor.code)
    if template is None:
        return ""
    if factor.side == "home":
        team, other = home_team, away_team
    elif factor.side == "away":
        team, other = away_team, home_team
    else:
        team, other = home_team, away_team
    return template.format(
        adverb=_STRENGTH_ADVERB.get(factor.strength, "modestly"),
        team=team,
        other=other,
        side=factor.side,
    )


def render_rationale(
    factors: list[PickFactor],
    home_team: str,
    away_team: str,
    limit: int = 2,
) -> str:
    """Render the `limit` strongest factors as one sentence.

    Returns "" when there is nothing to say, so callers can omit the line
    entirely rather than printing an empty rationale.
    """
    ordered = sorted(factors, key=lambda f: _STRENGTH_ORDER.get(f.strength, 3))
    parts = []
    for f in ordered:
        text = render_factor(f, home_team, away_team)
        if text:
            parts.append(text)
        if len(parts) >= limit:
            break
    if not parts:
        return ""
    return "; ".join(parts) + "."
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest backend/tests/test_rationale.py -q`
Expected: PASS, 6 passed

- [ ] **Step 6: Run the full suite**

Run: `.venv/Scripts/python.exe -m pytest backend/tests -q`
Expected: `406 passed` (400 baseline + 6 new), 0 failed

- [ ] **Step 7: Commit**

```bash
git add backend/data_types.py backend/analysis/rationale.py backend/tests/test_rationale.py
git commit -m "feat(digest): add PickFactor type and the rationale renderer"
```

---

### Task 2: Persist rationale and model probability

The `picks.model_prob` column was migrated in months ago and has **never been written to**. This task fixes that and adds `rationale_json` alongside it.

**Files:**
- Modify: `backend/models.py`
- Modify: `backend/database.py`
- Modify: `backend/api/main.py`
- Modify: `backend/pipeline/pick_generator.py`
- Test: `backend/tests/test_pick_rationale_persistence.py`

**Interfaces:**
- Consumes: `PickFactor` and `Pick.factors` from Task 1.
- Produces: `PickModel.rationale_json` (TEXT, nullable) holding a JSON list of `{"code","side","strength"}` objects; `PickModel.model_prob` now populated.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_pick_rationale_persistence.py`:

```python
import json
from datetime import date

from backend.database import get_engine, get_session
from backend.models import Base, Team, Game, StrategyModel, PickModel, TeamStat, EloRating, Odds
from backend.pipeline.pick_generator import generate_and_store_picks
from datetime import datetime, timezone


def _seed(session, game_date):
    home = Team(id=1, name="Chiefs", abbreviation="KC", sport="nfl")
    away = Team(id=2, name="Bills", abbreviation="BUF", sport="nfl")
    session.add_all([home, away])
    session.flush()
    game = Game(id=1, sport="nfl", season="2026", date=game_date,
                home_team_id=1, away_team_id=2, status="scheduled")
    session.add(game)
    session.flush()
    session.add_all([
        TeamStat(team_id=1, game_id=1, stat_type="point_diff", value=6.0),
        TeamStat(team_id=2, game_id=1, stat_type="point_diff", value=0.0),
        TeamStat(team_id=1, game_id=1, stat_type="offensive_rating", value=110.0),
        TeamStat(team_id=1, game_id=1, stat_type="defensive_rating", value=100.0),
        TeamStat(team_id=2, game_id=1, stat_type="offensive_rating", value=100.0),
        TeamStat(team_id=2, game_id=1, stat_type="defensive_rating", value=105.0),
        EloRating(team_id=1, sport="nfl", rating=1650.0),
        EloRating(team_id=2, sport="nfl", rating=1500.0),
        Odds(game_id=1, bookmaker="dk", moneyline_home=-110, moneyline_away=-110,
             spread_home=0.0, spread_away=0.0, over_under=45.0,
             timestamp=datetime(2026, 3, 1, 18, 0, tzinfo=timezone.utc)),
        StrategyModel(id=1, name="value_only", config_json='{"min_edge": 0.1}', is_active=True),
    ])
    session.commit()


def test_pick_persists_rationale_and_model_prob():
    engine = get_engine(":memory:")
    Base.metadata.create_all(engine)
    session = get_session(engine)
    d = date(2026, 3, 1)
    _seed(session, d)

    assert generate_and_store_picks(session, strategy_id=1, target_date=d) >= 1

    pick = session.query(PickModel).first()
    assert pick is not None
    assert pick.model_prob is not None, "model_prob must be persisted"
    assert 0.0 < pick.model_prob < 1.0

    # Task 2 only guarantees the column round-trips valid JSON. Strategies do
    # not emit factors until Task 3, so the list may legitimately be empty
    # here; Task 3 adds the assertion that it is non-empty.
    assert pick.rationale_json is not None, "rationale_json must be persisted"
    factors = json.loads(pick.rationale_json)
    assert isinstance(factors, list)
    for f in factors:
        assert set(f) == {"code", "side", "strength"}
        assert f["side"] in ("home", "away", "over", "under")
        assert f["strength"] in ("slight", "moderate", "strong")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest backend/tests/test_pick_rationale_persistence.py -q`
Expected: FAIL — `AttributeError: 'PickModel' object has no attribute 'rationale_json'`

- [ ] **Step 3: Add the column to the model**

In `backend/models.py`, in `class PickModel`, directly after the `model_prob` line:

```python
    rationale_json = Column(Text, nullable=True)
```

`Text` is already imported at the top of that file.

- [ ] **Step 4: Add the migration**

In `backend/database.py`, following the existing pattern exactly:

```python
def migrate_pick_rationale(engine):
    """Add rationale_json column to picks if missing.

    Stores the structured PickFactor list that explains why a pick was made,
    so the daily digest can render a rationale without re-deriving (and
    possibly contradicting) the reasoning the strategy actually used.
    """
    from sqlalchemy import inspect as sa_inspect, text
    inspector = sa_inspect(engine)
    if "picks" in inspector.get_table_names():
        columns = [c["name"] for c in inspector.get_columns("picks")]
        if "rationale_json" not in columns:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE picks ADD COLUMN rationale_json TEXT"))
```

- [ ] **Step 5: Wire the migration into app startup**

In `backend/api/main.py`, add `migrate_pick_rationale` to the existing import list from `backend.database` and add the call alongside the others:

```python
    migrate_pick_rationale(engine)
```

Place it immediately after the `migrate_pick_model_prob(engine)` line.

- [ ] **Step 6: Persist both fields in pick_generator**

In `backend/pipeline/pick_generator.py`, add `import json` at the top if not present, and import the dataclass helper:

```python
from dataclasses import asdict
```

Then change the `PickModel(...)` construction inside the loop to include both fields:

```python
                db_pick = PickModel(game_id=game.id, strategy_id=strategy_id,
                    pick_type=pick.pick_type, pick_value=pick.pick_value,
                    confidence=pick.confidence, edge_pct=pick.edge_pct,
                    odds_at_pick=pick.odds_at_pick,
                    model_prob=pick.model_probability,
                    rationale_json=json.dumps([asdict(f) for f in pick.factors]),
                    created_at=datetime.now(tz=timezone.utc))
```

- [ ] **Step 7: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest backend/tests/test_pick_rationale_persistence.py -q`
Expected: PASS, 1 passed. `model_prob` is populated and `rationale_json` holds
`"[]"` — strategies do not emit factors until Task 3, and an empty list is the
correct value at this point.

- [ ] **Step 8: Run the full suite**

Run: `.venv/Scripts/python.exe -m pytest backend/tests -q`
Expected: `407 passed`, 0 failed

- [ ] **Step 9: Commit**

```bash
git add backend/models.py backend/database.py backend/api/main.py backend/pipeline/pick_generator.py backend/tests/test_pick_rationale_persistence.py
git commit -m "feat(digest): persist pick rationale and model probability"
```

---

### Task 3: Strategies emit factors

**Files:**
- Modify: `backend/analysis/variants/ensemble.py`
- Modify: `backend/analysis/variants/value_only.py`
- Modify: `backend/analysis/variants/sport_specific.py`
- Modify: `backend/analysis/variants/recent_form.py`
- Modify: `backend/analysis/variants/combat_sports.py`
- Modify: `backend/analysis/strategy.py`
- Test: `backend/tests/test_strategy_factors.py`

**Interfaces:**
- Consumes: `PickFactor` (Task 1), `Pick.factors` (Task 1).
- Produces: every moneyline `Pick` returned by a strategy carries a non-empty `factors` list.

**Where the signals live.** `GameData.home_stats` / `.away_stats` are `TeamStats`, which already carries everything needed:
`elo_rating`, `point_diff`, `offensive_rating`, `defensive_rating`, `rest_days`, `is_schedule_fatigued`, `is_lookahead_spot`, `pitcher_skill_score`.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_strategy_factors.py`:

```python
from datetime import date

from backend.data_types import GameData, TeamStats, OddsSnapshot
from backend.analysis.variants.value_only import ValueOnlyStrategy


def _stats(point_diff, elo, off, deff, rest=2, fatigued=False, lookahead=False):
    return TeamStats(
        point_diff=point_diff, home_record=(5, 2), away_record=(4, 3),
        last_n_record=(7, 3), offensive_rating=off, defensive_rating=deff,
        pace=100.0, strength_of_schedule=0.5, elo_rating=elo, rest_days=rest,
        is_schedule_fatigued=fatigued, is_lookahead_spot=lookahead,
    )


def _game():
    return GameData(
        game_id=1, sport="nfl", date=date(2026, 3, 1),
        home_team_id=1, away_team_id=2,
        home_stats=_stats(6.0, 1650.0, 110.0, 100.0, rest=7),
        away_stats=_stats(0.0, 1500.0, 100.0, 105.0, rest=3, fatigued=True),
        odds=[OddsSnapshot(bookmaker="dk", moneyline_home=-110, moneyline_away=-110,
                           spread_home=0.0, spread_away=0.0, over_under=45.0)],
    )


def test_moneyline_pick_carries_factors():
    s = ValueOnlyStrategy("value_only", {"min_edge": 0.1})
    picks = s.predict(_game())
    ml = [p for p in picks if p.pick_type == "moneyline"]
    assert ml, "expected a moneyline pick from this setup"
    assert ml[0].factors, "moneyline picks must carry factors"


def test_factors_use_known_codes_and_valid_fields():
    from backend.analysis.rationale import FACTOR_TEMPLATES
    s = ValueOnlyStrategy("value_only", {"min_edge": 0.1})
    picks = s.predict(_game())
    for p in picks:
        for f in p.factors:
            assert f.code in FACTOR_TEMPLATES, f"unknown factor code {f.code}"
            assert f.side in ("home", "away", "over", "under")
            assert f.strength in ("slight", "moderate", "strong")


def test_rating_gap_favors_the_stronger_side():
    s = ValueOnlyStrategy("value_only", {"min_edge": 0.1})
    picks = s.predict(_game())
    ml = [p for p in picks if p.pick_type == "moneyline"][0]
    gap = [f for f in ml.factors if f.code == "rating_gap"]
    assert gap, "expected a rating_gap factor"
    assert gap[0].side == "home", "home has the higher elo in this fixture"


def test_fatigue_factor_points_at_the_fatigued_team():
    s = ValueOnlyStrategy("value_only", {"min_edge": 0.1})
    picks = s.predict(_game())
    ml = [p for p in picks if p.pick_type == "moneyline"][0]
    fatigue = [f for f in ml.factors if f.code == "schedule_fatigue"]
    assert fatigue, "away team is fatigued in this fixture"
    assert fatigue[0].side == "home", (
        "schedule_fatigue favors the RESTED side; the template names the other team"
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest backend/tests/test_strategy_factors.py -q`
Expected: FAIL on `assert ml[0].factors` — factors list is empty

- [ ] **Step 3: Add the shared factor builder to the base class**

All five strategies need the same derivation, so it lives once on `Strategy`.
In `backend/analysis/strategy.py`, add this method to `Strategy` (do **not** touch `__init__`, `thresholds`, or `self.thresholds`):

```python
    def _build_factors(self, game: "GameData", side: str) -> list["PickFactor"]:
        """Derive the factors that favor `side` ("home" or "away").

        Only emits a factor when the underlying signal is actually present
        and actually favors that side — a factor the model did not use must
        never appear in a rationale.
        """
        from backend.data_types import PickFactor

        hs, aws = game.home_stats, game.away_stats
        mine, theirs = (hs, aws) if side == "home" else (aws, hs)
        factors: list[PickFactor] = []

        def _strength(diff: float, moderate: float, strong: float) -> str | None:
            if diff >= strong:
                return "strong"
            if diff >= moderate:
                return "moderate"
            if diff > 0:
                return "slight"
            return None

        s = _strength(mine.elo_rating - theirs.elo_rating, 50.0, 120.0)
        if s:
            factors.append(PickFactor(code="rating_gap", side=side, strength=s))

        s = _strength(mine.point_diff - theirs.point_diff, 3.0, 7.0)
        if s:
            factors.append(PickFactor(code="recent_form", side=side, strength=s))

        mine_net = mine.offensive_rating - mine.defensive_rating
        theirs_net = theirs.offensive_rating - theirs.defensive_rating
        s = _strength(mine_net - theirs_net, 3.0, 8.0)
        if s:
            factors.append(PickFactor(code="net_rating", side=side, strength=s))

        if theirs.is_schedule_fatigued:
            factors.append(PickFactor(code="schedule_fatigue", side=side, strength="moderate"))

        if theirs.is_lookahead_spot:
            factors.append(PickFactor(code="lookahead_spot", side=side, strength="slight"))

        s = _strength(float(mine.rest_days - theirs.rest_days), 2.0, 4.0)
        if s:
            factors.append(PickFactor(code="rest_advantage", side=side, strength=s))

        if mine.pitcher_skill_score is not None and theirs.pitcher_skill_score is not None:
            s = _strength(mine.pitcher_skill_score - theirs.pitcher_skill_score, 0.05, 0.15)
            if s:
                factors.append(PickFactor(code="pitcher_edge", side=side, strength=s))

        return factors
```

Add `PickFactor` to the existing `from backend.data_types import GameData, Pick` line so the annotation resolves:

```python
from backend.data_types import GameData, Pick, PickFactor
```

- [ ] **Step 4: Emit factors in value_only.py**

In `backend/analysis/variants/value_only.py`, add `factors=self._build_factors(game, "home")` to the `Pick(...)` construction for `"HOME ML"`, and `factors=self._build_factors(game, "away")` for `"AWAY ML"`. Add the argument as the last keyword; change nothing else in either call.

- [ ] **Step 5: Run the value_only tests**

Run: `.venv/Scripts/python.exe -m pytest backend/tests/test_strategy_factors.py -q`
Expected: PASS, 4 passed

- [ ] **Step 6: Emit factors in the remaining four variants**

Apply the identical change — `factors=self._build_factors(game, "home")` / `"away"` on the moneyline `Pick(...)` constructions — in:
- `backend/analysis/variants/sport_specific.py`
- `backend/analysis/variants/recent_form.py`
- `backend/analysis/variants/ensemble.py` (moneyline picks only; leave spread and total picks with no factors for now)
- `backend/analysis/variants/combat_sports.py`

For `combat_sports.py`, `game.home_stats` / `game.away_stats` are still populated by `_build_game_data`, so `_build_factors` works unchanged. Do **not** add fighter-specific factors in this task.

- [ ] **Step 7: Strengthen the Task 2 persistence test**

Now that strategies emit factors, tighten the assertion in
`backend/tests/test_pick_rationale_persistence.py`. Replace:

```python
    assert isinstance(factors, list)
```

with:

```python
    assert isinstance(factors, list) and len(factors) >= 1, (
        "strategies must emit at least one factor for a moneyline pick"
    )
```

and delete the three-line comment above it that says the list may be empty.

Run: `.venv/Scripts/python.exe -m pytest backend/tests/test_pick_rationale_persistence.py -q`
Expected: PASS — `rationale_json` now contains at least one factor

- [ ] **Step 8: Prove the guard is real**

Temporarily remove the `factors=` argument from the `"HOME ML"` construction in `value_only.py`, run `test_moneyline_pick_carries_factors`, and confirm it **fails**. Restore the argument and confirm it passes again.

- [ ] **Step 9: Run the full suite**

Run: `.venv/Scripts/python.exe -m pytest backend/tests -q`
Expected: `412 passed`, 0 failed

- [ ] **Step 10: Commit**

```bash
git add backend/analysis/strategy.py backend/analysis/variants/ backend/tests/test_strategy_factors.py
git commit -m "feat(digest): strategies emit structured pick factors"
```

---

### Task 4: Digest selector

Pure function. No I/O, no clock, no network.

**Files:**
- Create: `backend/digest/__init__.py` (empty)
- Create: `backend/digest/selector.py`
- Test: `backend/tests/test_digest_selector.py`

**Interfaces:**
- Consumes: `PickModel`, `PlayerProp`, `Game` rows (already loaded by the caller).
- Produces:

```python
@dataclass(frozen=True)
class DigestPick:
    sport: str
    matchup: str          # "Bills @ Chiefs"
    pick_value: str
    odds: int
    confidence: int
    edge_pct: float
    rationale: str        # already rendered, may be ""

@dataclass(frozen=True)
class DigestSection:
    sport: str
    picks: list[DigestPick]
    props: list[DigestPick]

def select_digest(session, target_date, sports, seasons, max_per_sport=5) -> list[DigestSection]
```

`seasons` is the `config["seasons"]` mapping, passed through to
`is_sport_in_season`. It is a required positional argument — the tests and
`job.py` in Task 6 both pass it.

**Rules** (from the spec — implement exactly):
- **Active sport** = `is_sport_in_season(sport, seasons, target_date)` is true **and** at least one `Game` exists on `target_date`. Inactive sports are omitted entirely.
- Rank per sport: `confidence` DESC, then `edge_pct` DESC, then `game.start_time` ASC, then `pick.id` ASC (deterministic ties).
- Take at most `max_per_sport`. **Never pad.**
- Exclude `confidence < 1`.
- Props ranked **separately** among themselves, never merged with game picks.
- A sport with zero qualifying picks and zero qualifying props produces **no section**.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_digest_selector.py`:

```python
from datetime import date

from backend.database import get_engine, get_session
from backend.models import Base, Team, Game, StrategyModel, PickModel
from backend.digest.selector import select_digest

SEASONS = {"nfl": {"start": "09-05", "end": "02-10"},
           "nba": {"start": "10-22", "end": "06-20"}}


def _mk(session, sport, gid, tid_h, tid_a, d, picks):
    session.add_all([
        Team(id=tid_h, name=f"H{gid}", abbreviation=f"H{gid}", sport=sport),
        Team(id=tid_a, name=f"A{gid}", abbreviation=f"A{gid}", sport=sport),
    ])
    session.flush()
    session.add(Game(id=gid, sport=sport, season="2026", date=d,
                     home_team_id=tid_h, away_team_id=tid_a, status="scheduled"))
    session.flush()
    for i, (conf, edge) in enumerate(picks):
        session.add(PickModel(game_id=gid, strategy_id=1, pick_type="moneyline",
                              pick_value=f"P{gid}-{i}", confidence=conf,
                              edge_pct=edge, odds_at_pick=-110))
    session.commit()


def _session():
    engine = get_engine(":memory:")
    Base.metadata.create_all(engine)
    s = get_session(engine)
    s.add(StrategyModel(id=1, name="x", config_json="{}", is_active=True))
    s.commit()
    return s


def test_ranks_by_confidence_then_edge():
    s = _session()
    d = date(2026, 11, 1)
    _mk(s, "nfl", 1, 1, 2, d, [(3, 9.0), (5, 4.0), (4, 8.0)])
    sections = select_digest(s, d, ["nfl"], SEASONS)
    vals = [p.pick_value for p in sections[0].picks]
    assert vals == ["P1-1", "P1-2", "P1-0"]


def test_caps_at_max_per_sport_and_never_pads():
    s = _session()
    d = date(2026, 11, 1)
    _mk(s, "nfl", 1, 1, 2, d, [(5, 9.0), (5, 8.0)])
    sections = select_digest(s, d, ["nfl"], SEASONS, max_per_sport=5)
    assert len(sections[0].picks) == 2, "must not pad to five"


def test_sport_with_no_games_is_omitted():
    s = _session()
    d = date(2026, 11, 1)
    _mk(s, "nfl", 1, 1, 2, d, [(5, 9.0)])
    sections = select_digest(s, d, ["nfl", "nba"], SEASONS)
    assert [sec.sport for sec in sections] == ["nfl"]


def test_out_of_season_sport_is_omitted():
    s = _session()
    d = date(2026, 7, 1)  # NFL out of season
    _mk(s, "nfl", 1, 1, 2, d, [(5, 9.0)])
    sections = select_digest(s, d, ["nfl"], SEASONS)
    assert sections == []


def test_zero_confidence_picks_excluded():
    s = _session()
    d = date(2026, 11, 1)
    _mk(s, "nfl", 1, 1, 2, d, [(0, 20.0)])
    sections = select_digest(s, d, ["nfl"], SEASONS)
    assert sections == []


def test_matchup_reads_away_at_home():
    s = _session()
    d = date(2026, 11, 1)
    _mk(s, "nfl", 1, 1, 2, d, [(5, 9.0)])
    sections = select_digest(s, d, ["nfl"], SEASONS)
    assert sections[0].picks[0].matchup == "A1 @ H1"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest backend/tests/test_digest_selector.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'backend.digest'`

- [ ] **Step 3: Create the package and the selector**

Create empty `backend/digest/__init__.py`.

Create `backend/digest/selector.py`:

```python
"""Choose and rank what goes into the daily digest.

Pure with respect to time and network: the caller supplies the target date
and a session. No sending, no formatting.
"""
import json
from dataclasses import dataclass
from datetime import datetime

from backend.config import is_sport_in_season
from backend.data_types import PickFactor
from backend.analysis.rationale import render_rationale
from backend.models import Game, PickModel, PlayerProp, Team


@dataclass(frozen=True)
class DigestPick:
    sport: str
    matchup: str
    pick_value: str
    odds: int
    confidence: int
    edge_pct: float
    rationale: str


@dataclass(frozen=True)
class DigestSection:
    sport: str
    picks: list[DigestPick]
    props: list[DigestPick]


def _matchup(session, game: Game) -> str:
    home = session.get(Team, game.home_team_id)
    away = session.get(Team, game.away_team_id)
    home_name = home.name if home else "Home"
    away_name = away.name if away else "Away"
    return f"{away_name} @ {home_name}"


def _rationale_for(session, pick: PickModel, game: Game) -> str:
    if not pick.rationale_json:
        return ""
    try:
        raw = json.loads(pick.rationale_json)
    except (ValueError, TypeError):
        return ""
    factors = [
        PickFactor(code=f.get("code", ""), side=f.get("side", "home"),
                   strength=f.get("strength", "moderate"))
        for f in raw if isinstance(f, dict)
    ]
    home = session.get(Team, game.home_team_id)
    away = session.get(Team, game.away_team_id)
    return render_rationale(factors,
                            home.name if home else "Home",
                            away.name if away else "Away")


def select_digest(session, target_date, sports, seasons, max_per_sport: int = 5):
    """Return one DigestSection per active sport that has something to show."""
    sections: list[DigestSection] = []

    for sport in sports:
        if not is_sport_in_season(sport, seasons, target_date):
            continue
        games = (
            session.query(Game)
            .filter(Game.sport == sport, Game.date == target_date)
            .all()
        )
        if not games:
            continue
        games_by_id = {g.id: g for g in games}

        picks = (
            session.query(PickModel)
            .filter(PickModel.game_id.in_(list(games_by_id)), PickModel.confidence >= 1)
            .all()
        )
        def _pick_sort_key(p):
            # start_time is nullable, and stored rows may be naive or aware.
            # Comparing a datetime to a date, or a naive to an aware datetime,
            # raises TypeError mid-sort — normalize to naive and push missing
            # start times to the end.
            st = games_by_id[p.game_id].start_time
            when = st.replace(tzinfo=None) if st is not None else datetime.max
            return (-p.confidence, -(p.edge_pct or 0.0), when, p.id or 0)

        picks.sort(key=_pick_sort_key)

        digest_picks = [
            DigestPick(
                sport=sport,
                matchup=_matchup(session, games_by_id[p.game_id]),
                pick_value=p.pick_value,
                odds=p.odds_at_pick or -110,
                confidence=p.confidence,
                edge_pct=round(p.edge_pct or 0.0, 1),
                rationale=_rationale_for(session, p, games_by_id[p.game_id]),
            )
            for p in picks[:max_per_sport]
        ]

        props = (
            session.query(PlayerProp)
            .filter(PlayerProp.game_id.in_(list(games_by_id)))
            .all()
        )
        props.sort(key=lambda pr: (pr.player_name or "", pr.id or 0))
        digest_props = [
            DigestPick(
                sport=sport,
                matchup=_matchup(session, games_by_id[pr.game_id]),
                pick_value=f"{pr.player_name} {pr.outcome} {pr.line} ({pr.market})",
                odds=pr.odds,
                confidence=0,
                edge_pct=0.0,
                rationale="",
            )
            for pr in props[:max_per_sport]
        ]

        if not digest_picks and not digest_props:
            continue
        sections.append(DigestSection(sport=sport, picks=digest_picks, props=digest_props))

    return sections
```

> **Note on props ranking:** `PlayerProp` rows carry no confidence or edge —
> those live on the analyzed output, not the raw prop. This task sorts props
> deterministically by player name so the selector is complete and testable.
> Wiring real prop confidence is deliberately deferred; see "Deferred" at the
> end of this plan.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest backend/tests/test_digest_selector.py -q`
Expected: PASS, 6 passed

- [ ] **Step 5: Prove the never-pad guard is real**

Change `picks[:max_per_sport]` to pad the list to `max_per_sport` with duplicates, run `test_caps_at_max_per_sport_and_never_pads`, confirm it **fails**, then restore.

- [ ] **Step 6: Run the full suite**

Run: `.venv/Scripts/python.exe -m pytest backend/tests -q`
Expected: `418 passed`, 0 failed

- [ ] **Step 7: Commit**

```bash
git add backend/digest/ backend/tests/test_digest_selector.py
git commit -m "feat(digest): add the digest selector"
```

---

### Task 5: Renderer

Pure function. No network, no clock.

**Files:**
- Create: `backend/digest/render.py`
- Test: `backend/tests/test_digest_render.py`

**Interfaces:**
- Consumes: `DigestSection` / `DigestPick` from Task 4.
- Produces: `render_digest(sections, target_date) -> tuple[str, str, str]` — `(subject, html, text)`.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_digest_render.py`:

```python
from datetime import date

from backend.digest.selector import DigestPick, DigestSection
from backend.digest.render import render_digest


def _section():
    return DigestSection(
        sport="nfl",
        picks=[DigestPick(sport="nfl", matchup="Bills @ Chiefs", pick_value="HOME ML",
                          odds=-110, confidence=5, edge_pct=8.1,
                          rationale="Rating gap strongly favors Chiefs.")],
        props=[DigestPick(sport="nfl", matchup="Bills @ Chiefs",
                          pick_value="Mahomes Over 275.5 (player_pass_yds)",
                          odds=-115, confidence=0, edge_pct=0.0, rationale="")],
    )


def test_subject_names_date_and_count():
    subject, _, _ = render_digest([_section()], date(2026, 9, 20))
    assert "Sep 20" in subject
    assert "1" in subject


def test_html_contains_pick_and_rationale():
    _, html, _ = render_digest([_section()], date(2026, 9, 20))
    assert "HOME ML" in html
    assert "Bills @ Chiefs" in html
    assert "Rating gap strongly favors Chiefs." in html
    assert "-110" in html


def test_html_has_no_external_resources():
    _, html, _ = render_digest([_section()], date(2026, 9, 20))
    assert "<script" not in html.lower()
    assert "<link" not in html.lower()


def test_props_render_in_their_own_section():
    _, html, _ = render_digest([_section()], date(2026, 9, 20))
    assert "Player props" in html
    assert "Mahomes Over 275.5" in html


def test_text_alternative_is_always_produced():
    _, _, text = render_digest([_section()], date(2026, 9, 20))
    assert "HOME ML" in text
    assert "<" not in text


def test_empty_sections_render_empty_subject_marker():
    subject, html, text = render_digest([], date(2026, 9, 20))
    assert subject == ""
    assert html == ""
    assert text == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest backend/tests/test_digest_render.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'backend.digest.render'`

- [ ] **Step 3: Write the renderer**

Create `backend/digest/render.py`:

```python
"""Render a digest selection into email bodies.

Table-based HTML with inline styles only — the one approach that renders
reliably across Gmail, Outlook and Apple Mail. No external CSS, no web
fonts, no JavaScript. A plain-text alternative is always produced.
"""
from datetime import date

from backend.digest.selector import DigestSection

SPORT_LABELS = {
    "nfl": "NFL", "ncaaf": "College Football", "nba": "NBA",
    "mlb": "MLB", "ncaab": "College Basketball",
    "boxing": "Boxing", "mma": "MMA",
}

_FONT = "font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;"


def _stars(confidence: int) -> str:
    return "★" * confidence + "☆" * (5 - confidence)


def _label(sport: str) -> str:
    return SPORT_LABELS.get(sport, sport.upper())


def render_digest(sections: list[DigestSection], target_date: date):
    """Return (subject, html, text). All three are "" when there is nothing
    to send — the caller must not send an empty digest."""
    if not sections:
        return "", "", ""

    total = sum(len(s.picks) for s in sections)
    # NOTE: %-d is not portable (fails on Windows). Use %d and strip the
    # leading zero, which works on every platform.
    pretty = target_date.strftime("%a %b %d").replace(" 0", " ")
    subject = f"Top picks — {pretty} ({total} across {len(sections)} sports)"

    rows = []
    text_lines = [f"TOP PICKS — {pretty}", ""]

    for section in sections:
        rows.append(
            f'<tr><td style="{_FONT}padding:18px 0 6px 0;font-size:13px;'
            f'letter-spacing:.08em;text-transform:uppercase;color:#6b7280;">'
            f'{_label(section.sport)}</td></tr>'
        )
        text_lines.append(f"-- {_label(section.sport)} --")

        for p in section.picks:
            rationale_html = (
                f'<div style="{_FONT}font-size:13px;color:#6b7280;padding-top:3px;">'
                f'{p.rationale}</div>' if p.rationale else ""
            )
            rows.append(
                f'<tr><td style="padding:10px 0;border-bottom:1px solid #e5e7eb;">'
                f'<div style="{_FONT}font-size:15px;font-weight:600;color:#111827;">'
                f'{p.pick_value} <span style="font-weight:400;color:#6b7280;">({p.odds})</span></div>'
                f'<div style="{_FONT}font-size:13px;color:#374151;padding-top:2px;">'
                f'{p.matchup} &nbsp;·&nbsp; {_stars(p.confidence)} &nbsp;·&nbsp; +{p.edge_pct}%</div>'
                f'{rationale_html}</td></tr>'
            )
            text_lines.append(f"  {p.pick_value} ({p.odds}) — {p.matchup} — {_stars(p.confidence)} +{p.edge_pct}%")
            if p.rationale:
                text_lines.append(f"      {p.rationale}")

        if section.props:
            rows.append(
                f'<tr><td style="{_FONT}padding:12px 0 4px 0;font-size:12px;'
                f'color:#6b7280;">Player props</td></tr>'
            )
            text_lines.append("  Player props:")
            for p in section.props:
                rows.append(
                    f'<tr><td style="padding:6px 0;border-bottom:1px solid #f3f4f6;">'
                    f'<div style="{_FONT}font-size:14px;color:#111827;">'
                    f'{p.pick_value} <span style="color:#6b7280;">({p.odds})</span></div></td></tr>'
                )
                text_lines.append(f"    {p.pick_value} ({p.odds})")
        text_lines.append("")

    html = (
        f'<html><body style="margin:0;padding:0;background:#f9fafb;">'
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        f'style="background:#f9fafb;padding:16px;"><tr><td align="center">'
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        f'style="max-width:560px;background:#ffffff;border-radius:10px;padding:20px;">'
        f'<tr><td style="{_FONT}font-size:18px;font-weight:700;color:#111827;'
        f'padding-bottom:2px;">Top picks — {pretty}</td></tr>'
        f'<tr><td style="{_FONT}font-size:13px;color:#6b7280;padding-bottom:6px;">'
        f'{total} picks across {len(sections)} sports</td></tr>'
        + "".join(rows)
        + f'<tr><td style="{_FONT}font-size:11px;color:#9ca3af;padding-top:18px;">'
        f'Model output for research, not betting advice.</td></tr>'
        f'</table></td></tr></table></body></html>'
    )

    text_lines.append("Model output for research, not betting advice.")
    return subject, html, "\n".join(text_lines)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest backend/tests/test_digest_render.py -q`
Expected: PASS, 6 passed

- [ ] **Step 5: Run the full suite**

Run: `.venv/Scripts/python.exe -m pytest backend/tests -q`
Expected: `424 passed`, 0 failed

- [ ] **Step 6: Commit**

```bash
git add backend/digest/render.py backend/tests/test_digest_render.py
git commit -m "feat(digest): render digest to HTML and plain text"
```

---

### Task 6: Sender, config, and the scheduled job

**Files:**
- Create: `backend/digest/sender.py`
- Create: `backend/digest/job.py`
- Modify: `backend/pipeline/scheduler.py`
- Modify: `config.yaml`
- Modify: `.env.example`
- Test: `backend/tests/test_digest_sender.py`

**Interfaces:**
- Consumes: `render_digest` (Task 5), `select_digest` (Task 4).
- Produces: `send_email(subject, html, text, sender, recipients, api_key) -> bool`; `send_daily_digest(config, engine) -> dict`.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_digest_sender.py`:

```python
import logging
from datetime import date

from backend.digest.sender import send_email
from backend.digest.job import send_daily_digest


def test_dry_run_writes_file_and_sends_nothing(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr("backend.digest.sender.httpx.post",
                        lambda *a, **k: calls.append(a) or None)
    out = tmp_path / "digest.html"
    sent = send_email("subj", "<p>hi</p>", "hi", "a@b.c", ["d@e.f"],
                      api_key=None, dry_run_path=str(out))
    assert sent is False
    assert out.read_text(encoding="utf-8") == "<p>hi</p>"
    assert calls == [], "dry run must not call the network"


def test_missing_api_key_does_not_send(tmp_path):
    sent = send_email("subj", "<p>hi</p>", "hi", "a@b.c", ["d@e.f"], api_key=None)
    assert sent is False


def test_send_failure_never_leaks_the_key(caplog, monkeypatch):
    class Boom(Exception):
        pass

    def _raise(*a, **k):
        raise Boom("failed for url https://api.resend.com/emails key=FAKEKEY123")

    monkeypatch.setattr("backend.digest.sender.httpx.post", _raise)
    caplog.set_level(logging.WARNING)
    sent = send_email("s", "<p>h</p>", "h", "a@b.c", ["d@e.f"], api_key="FAKEKEY123")
    assert sent is False
    assert "FAKEKEY123" not in caplog.text


def test_job_sends_nothing_when_no_sections(tmp_path, monkeypatch):
    from backend.database import get_engine
    from backend.models import Base
    engine = get_engine(":memory:")
    Base.metadata.create_all(engine)
    monkeypatch.setattr("backend.digest.job.send_email",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not send")))
    result = send_daily_digest(
        {"seasons": {}, "digest": {"enabled": True, "sports": ["nfl"],
                                   "from": "a@b.c", "recipients": ["d@e.f"]}},
        engine, target_date=date(2026, 11, 1))
    assert result["sent"] is False
    assert result["sections"] == 0


def test_job_never_raises(monkeypatch):
    from backend.database import get_engine
    from backend.models import Base
    engine = get_engine(":memory:")
    Base.metadata.create_all(engine)
    monkeypatch.setattr("backend.digest.job.select_digest",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    result = send_daily_digest({"seasons": {}, "digest": {"enabled": True}},
                               engine, target_date=date(2026, 11, 1))
    assert result["sent"] is False
    assert "error" in result
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest backend/tests/test_digest_sender.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'backend.digest.sender'`

- [ ] **Step 3: Write the sender**

Create `backend/digest/sender.py`:

```python
"""Deliver the digest. The only unit in backend/digest that touches the network.

Uses httpx (already a project dependency) against Resend's REST API rather
than adding a provider SDK.
"""
import logging
import re

import httpx

logger = logging.getLogger(__name__)

RESEND_URL = "https://api.resend.com/emails"

_KEY_RE = re.compile(r"(re_[A-Za-z0-9_]+|key=[^&\s'\"]+)")


def _scrub(text: str, api_key: str | None) -> str:
    """Remove any credential from text before it reaches a log record."""
    out = _KEY_RE.sub("<redacted>", text)
    if api_key:
        out = out.replace(api_key, "<redacted>")
    return out


def send_email(subject, html, text, sender, recipients,
               api_key=None, dry_run_path=None, timeout=15.0) -> bool:
    """Send one email. Returns True only if the provider accepted it.

    Never raises: a digest failure must not propagate into the scheduler.
    """
    if dry_run_path:
        with open(dry_run_path, "w", encoding="utf-8") as fh:
            fh.write(html)
        logger.info("Digest dry run written to %s (nothing sent)", dry_run_path)
        return False

    if not api_key:
        logger.warning("No RESEND_API_KEY configured; digest not sent")
        return False
    if not recipients:
        logger.warning("No digest recipients configured; nothing sent")
        return False

    try:
        response = httpx.post(
            RESEND_URL,
            headers={"Authorization": f"Bearer {api_key}",
                     "Content-Type": "application/json"},
            json={"from": sender, "to": list(recipients),
                  "subject": subject, "html": html, "text": text},
            timeout=timeout,
        )
        if response.status_code >= 400:
            logger.warning("Digest send failed: HTTP %s: %s",
                           response.status_code, _scrub(response.text, api_key))
            return False
        logger.info("Digest sent to %d recipient(s)", len(recipients))
        return True
    except Exception as e:
        logger.warning("Digest send failed: %s: %s",
                       type(e).__name__, _scrub(str(e), api_key))
        return False
```

- [ ] **Step 4: Write the job**

Create `backend/digest/job.py`:

```python
"""Orchestrate the daily digest: select, render, send.

Never raises. A failed digest must not take down the scheduler that also
owns pick generation's sibling jobs.
"""
import logging
import os
from datetime import date as _date
from zoneinfo import ZoneInfo

from backend.database import get_session
from backend.digest.selector import select_digest
from backend.digest.render import render_digest
from backend.digest.sender import send_email

logger = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")


def send_daily_digest(config: dict, engine, target_date=None) -> dict:
    cfg = config.get("digest", {}) or {}
    result = {"sent": False, "sections": 0, "picks": 0}

    if target_date is None:
        from datetime import datetime
        target_date = datetime.now(tz=ET).date()

    session = get_session(engine)
    try:
        sections = select_digest(
            session,
            target_date,
            cfg.get("sports", ["ncaaf", "nfl", "nba", "mlb"]),
            config.get("seasons", {}),
            max_per_sport=cfg.get("max_per_sport", 5),
        )
        result["sections"] = len(sections)
        result["picks"] = sum(len(s.picks) for s in sections)

        subject, html, text = render_digest(sections, target_date)
        if not subject:
            logger.info("Digest for %s is empty; nothing sent", target_date)
            return result

        dry_run_path = None
        if not cfg.get("enabled", False) or os.environ.get("DIGEST_DRY_RUN") == "1":
            dry_run_path = os.environ.get("DIGEST_DRY_RUN_PATH", "digest_preview.html")

        result["sent"] = send_email(
            subject, html, text,
            cfg.get("from", "picks@example.com"),
            cfg.get("recipients", []),
            api_key=os.environ.get("RESEND_API_KEY"),
            dry_run_path=dry_run_path,
        )
        return result
    except Exception as e:
        logger.exception("Daily digest failed for %s", target_date)
        result["error"] = type(e).__name__
        return result
    finally:
        session.close()
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest backend/tests/test_digest_sender.py -q`
Expected: PASS, 5 passed

- [ ] **Step 6: Add the config block**

In `config.yaml`, append:

```yaml
digest:
  enabled: false          # must be explicitly turned on; dry-run until then
  send_hour_et: 11
  sports: ["ncaaf", "nfl", "nba", "mlb"]
  max_per_sport: 5
  from: "picks@example.com"
  recipients: []
```

In `.env.example`, add:

```
# Resend API key for the daily picks digest (backend/digest/sender.py)
RESEND_API_KEY=your_resend_api_key_here
```

- [ ] **Step 7: Register the scheduled job**

In `backend/pipeline/scheduler.py`, inside `configure_scheduler`, immediately before `return scheduler`:

```python
    digest_cfg = config.get("digest", {}) or {}
    if digest_cfg.get("enabled") or os.environ.get("DIGEST_DRY_RUN") == "1":
        from backend.digest.job import send_daily_digest
        scheduler.add_job(
            lambda: send_daily_digest(config, engine),
            'cron', hour=digest_cfg.get("send_hour_et", 11), minute=0,
            id='daily_digest', replace_existing=True,
        )
```

`os` is already imported in that module; confirm with `grep -n "^import os" backend/pipeline/scheduler.py` and add it if absent.

- [ ] **Step 8: Test the job registration**

Append to `backend/tests/test_digest_sender.py`:

```python
def test_digest_job_absent_when_disabled():
    from backend.pipeline.scheduler import configure_scheduler
    from backend.database import get_engine
    sched = configure_scheduler({"database_path": ":memory:", "digest": {"enabled": False}},
                                get_engine(":memory:"))
    assert sched.get_job("daily_digest") is None


def test_digest_job_registered_when_enabled():
    from backend.pipeline.scheduler import configure_scheduler
    from backend.database import get_engine
    sched = configure_scheduler({"database_path": ":memory:",
                                 "digest": {"enabled": True, "send_hour_et": 11}},
                                get_engine(":memory:"))
    job = sched.get_job("daily_digest")
    assert job is not None
```

- [ ] **Step 9: Run the full suite**

Run: `.venv/Scripts/python.exe -m pytest backend/tests -q`
Expected: `431 passed`, 0 failed

- [ ] **Step 10: Manual dry-run check**

Run:

```bash
DIGEST_DRY_RUN=1 .venv/Scripts/python.exe -c "from backend.config import load_config; from backend.database import get_engine; from backend.digest.job import send_daily_digest; print(send_daily_digest(load_config('config.yaml'), get_engine('sports_picks.db')))"
```

Expected: a dict printed with `sent: False`, and — if today has qualifying picks — a `digest_preview.html` file written. Open it in a browser to eyeball the layout. If today has no picks, `sections: 0` and no file; that is correct behavior, not a failure.

- [ ] **Step 11: Commit**

```bash
git add backend/digest/ backend/pipeline/scheduler.py config.yaml .env.example backend/tests/test_digest_sender.py
git commit -m "feat(digest): send the daily digest on an 11:00 ET schedule"
```

---

## Deferred (explicitly out of scope for this plan)

Recorded so they are not rediscovered as bugs:

- **Real prop ranking.** `PlayerProp` rows carry no confidence or edge — those exist only on `PropAnalysis`, which is not persisted per prop. Task 4 sorts props deterministically by player name. Ranking props by quality requires persisting analyzed prop output first.
- **Merging props into the main ranking.** Blocked on prop edge becoming price-aware; prop `edge_pct` is `(prob - 0.5) * 200` and ignores the prop's price, so it is not comparable to a game pick's de-vigged edge.
- **Factors on spread and total picks.** Task 3 emits factors for moneyline picks only.
- **Fighter-specific factors** for boxing/MMA.
- **Duplicate-send protection.** Two 11:00 triggers would send twice.
- **A second afternoon send** for evening slates.

## Before enabling real recipients

Set `digest.enabled: true` only after plan `003` (de-vig all strategies) has merged. Until then four of five strategies report edges inflated by roughly the bookmaker's overround, so the "top rated" ordering is sorting on a number that is systematically too high. The `enabled: false` default enforces this.

# Plan 003: Remove vig in every strategy and average odds in probability space

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md`.
>
> **Drift check (run first)**:
> `git diff --stat 295af51..HEAD -- backend/analysis/ backend/pipeline/pick_generator.py`
> If any in-scope file changed since this plan was written, compare the
> "Current state" excerpts against the live code before proceeding; on a
> mismatch, treat it as a STOP condition.

## Status

- **Priority**: P1
- **Effort**: M
- **Risk**: MED
- **Depends on**: 002 — **MERGED** as `295af51`. Satisfied.
- **Category**: bug
- **Planned at**: commit `5c2e0d0`, 2026-09-16
- **Refreshed at**: commit `295af51`, 2026-09-16 — plans 001/002/005/006 merged.
  The `Strategy` base class and the `pick_generator` loop excerpts below were
  updated to current code. **All five `_average_odds` copies and every de-vig
  site are unchanged**, and the variant line numbers still hold (002's edits
  were in-place).

## Why this matters

Two defects in the same layer, both caused by the same root: the moneyline
edge calculation is copy-pasted across five strategy variants instead of
living in one place.

**1. Four of five strategies never remove the bookmaker's vig.** They compare
the model's probability against the *raw* implied probability, which includes
the overround. Two-sided raw implied probabilities sum to ~1.04–1.05, so
roughly 2 percentage points of pure vig is being counted as bettor edge on each
side. With `min_edge` defaults of 5.0, that is ~40% of the qualifying threshold.
The project's own design doc is unambiguous that this was supposed to be fixed
everywhere — `docs/superpowers/specs/2026-03-16-roi-and-ui-overhaul-design.md`
§1f says:

> *"Edge calculation becomes: `model_prob - no_vig_prob` (not
> `model_prob - raw_implied_prob`) … **Update all edge calculations across
> strategy variants.**"*

The helper (`remove_vig`) was added and wired into `ensemble.py` only. The
migration half-landed and has been silently mispricing every pick from the
other four strategies since.

**2. American odds are averaged arithmetically, which is both wrong and
crash-prone.** American odds are non-linear around ±100, so the mean of two
prices is not the average price. Worse, near-pick'em markets crash: two books
at `-108` and `+104` average to `-2`, which is not a valid American price, so
`american_to_implied_prob` raises `InvalidOddsError`. That exception propagates
out of `predict()` through an **unguarded** call in the pick-generation loop,
past the `session.commit()` at the end — so **the entire day's picks for that
window are lost** because of one pick'em game. Verified:

```
arithmetic average of [-108, 104] = -2
RAISES: InvalidOddsError: Invalid American odds: -2
probability-average implied prob = 0.5047
```

This is most likely to bite MMA/boxing and NBA pick'ems — exactly the markets
where books straddle even money.

There are **five** copies of the averaging function. Fixing them in place would
leave five copies to drift again; this plan consolidates them into the base
class, which is also what the repo's own `CLAUDE.md` requires: *"Derive, don't
duplicate. When two code paths must agree, make one call the other."*

## Current state

### The de-vig helpers already exist and are correct

`backend/analysis/odds_utils.py:28-46`:

```python
def remove_vig(home_implied: float, away_implied: float) -> tuple[float, float]:
    """Remove bookmaker vig using proportional method."""
    total = home_implied + away_implied
    return home_implied / total, away_implied / total


def no_vig_implied_prob(side: str, home_odds: int, away_odds: int) -> float:
    """Get vig-adjusted implied probability for a specific side.

    Args:
        side: "home" or "away"
        home_odds: American odds for home side
        away_odds: American odds for away side
    """
    home_raw = american_to_implied_prob(home_odds)
    away_raw = american_to_implied_prob(away_odds)
    home_fair, away_fair = remove_vig(home_raw, away_raw)
    return home_fair if side == "home" else away_fair
```

Also relevant — `odds_utils.py:12-25`:

```python
def _validate_american_odds(odds: int) -> None:
    if odds == 0 or -100 < odds < 100:
        raise InvalidOddsError(f"Invalid American odds: {odds}")


def american_to_implied_prob(odds: int) -> float:
    _validate_american_odds(odds)
    if odds < 0: return abs(odds) / (abs(odds) + 100)
    else: return 100 / (odds + 100)
```

### The one strategy that does it right

`backend/analysis/variants/ensemble.py:34-40`:

```python
        # Moneyline picks — use vig-adjusted implied probabilities
        if avg_odds["moneyline_home"] is not None:
            raw_home = american_to_implied_prob(avg_odds["moneyline_home"])
            raw_away = american_to_implied_prob(avg_odds["moneyline_away"])
            implied_home, implied_away = remove_vig(raw_home, raw_away)
            home_edge = (home_prob - implied_home) * 100
            away_edge = (away_prob - implied_away) * 100
```

### The four that don't

All four use this identical shape (no `remove_vig`):

`backend/analysis/variants/value_only.py:24-28`:
```python
        if avg_odds["moneyline_home"] is not None:
            implied_home = american_to_implied_prob(avg_odds["moneyline_home"])
            implied_away = american_to_implied_prob(avg_odds["moneyline_away"])
            home_edge = (home_prob - implied_home) * 100
            away_edge = (away_prob - implied_away) * 100
```

`backend/analysis/variants/sport_specific.py:34-38` — byte-identical block.
`backend/analysis/variants/recent_form.py:26-30` — byte-identical block.

`backend/analysis/variants/combat_sports.py:42-45` (no `if` guard, same math):
```python
        implied_home = american_to_implied_prob(avg_odds["moneyline_home"])
        implied_away = american_to_implied_prob(avg_odds["moneyline_away"])
        home_edge = (home_prob - implied_home) * 100
        away_edge = (away_prob - implied_away) * 100
```

Note each variant stores `implied_probability=round(implied_home, 4)` on the
`Pick` (e.g. `sport_specific.py:48`, `recent_form.py:39`). After this change
that field holds the **de-vigged** probability, matching what `ensemble.py`
already stores.

### The five copies of odds averaging

`backend/analysis/variants/ensemble.py:262-281` — the superset (moneyline +
spread + total):

```python
    def _average_odds(self, game: GameData) -> dict | None:
        if not game.odds: return None
        ml_home = [o.moneyline_home for o in game.odds if o.moneyline_home is not None]
        ml_away = [o.moneyline_away for o in game.odds if o.moneyline_away is not None]
        if not ml_home: return None
        result = {
            "moneyline_home": int(sum(ml_home) / len(ml_home)),
            "moneyline_away": int(sum(ml_away) / len(ml_away)),
        }
        sp_home = [o.spread_home for o in game.odds if o.spread_home is not None]
        sp_away = [o.spread_away for o in game.odds if o.spread_away is not None]
        if sp_home:
            result["spread_home"] = round(sum(sp_home) / len(sp_home), 1)
            result["spread_away"] = round(sum(sp_away) / len(sp_away), 1)
        else:
            result["spread_home"] = None
            result["spread_away"] = None
        ou = [o.over_under for o in game.odds if o.over_under is not None]
        if ou:
            result["over_under"] = round(sum(ou) / len(ou), 1)
        else:
            result["over_under"] = None
        return result
```

`value_only.py:81`, `sport_specific.py:176`, `recent_form.py:105` — three
**byte-identical** copies of a moneyline-only version (verified by md5). They
return a dict with only the two moneyline keys.

`combat_sports.py` uses a differently-named fifth copy:

```python
    def _average_h2h_odds(self, game) -> dict | None:
        ml_home = [o.moneyline_home for o in game.odds if o.moneyline_home is not None]
        ml_away = [o.moneyline_away for o in game.odds if o.moneyline_away is not None]
        if not ml_home or not ml_away:
            return None
        return {
            "moneyline_home": int(sum(ml_home) / len(ml_home)),
            "moneyline_away": int(sum(ml_away) / len(ml_away)),
        }
```

Note `combat_sports` guards `not ml_away` too; the others don't. The
consolidated version must keep the stricter guard (it is strictly safer).

### The unguarded call site that turns one bad game into zero picks

`backend/pipeline/pick_generator.py:33-54`:

```python
    count = 0
    thresholds_by_sport: dict[str, dict] = {}
    for game in games:
        if game.sport not in thresholds_by_sport:
            thresholds_by_sport[game.sport] = get_thresholds(session, game.sport)
        sport_thresholds = thresholds_by_sport[game.sport]
        if game.sport in ("mma", "boxing"):
            strategy = CombatSportsStrategy(strat_row.name, config, sport_thresholds)
        else:
            strategy = strategy_cls(strat_row.name, config, sport_thresholds)
        game_data = _build_game_data(session, game, pitcher_scores=pitcher_scores)
        if game.sport in ("mma", "boxing"):
            game_data.home_fighter = _build_fighter_stats(session, game.home_team_id, game.sport, game.date)
            game_data.away_fighter = _build_fighter_stats(session, game.away_team_id, game.sport, game.date)
        picks = strategy.predict(game_data)
        for pick in picks:
            if pick.confidence >= 1:
                db_pick = PickModel(...)
                session.add(db_pick)
                count += 1
    session.commit()
    return count
```

`strategy.predict(game_data)` is not wrapped, and `session.commit()` is after
the loop — so any exception discards every pick built so far.

### The base class (where the shared helper will live)

`backend/analysis/strategy.py` in full, **as of `295af51`** (plan 002 added the
`thresholds` parameter — leave it exactly as it is):

```python
from abc import ABC, abstractmethod
from backend.data_types import GameData, Pick

class Strategy(ABC):
    def __init__(self, name: str, config: dict, thresholds: dict | None = None):
        self.name = name
        self.config = config
        self.thresholds = thresholds

    @abstractmethod
    def predict(self, game: GameData) -> list[Pick]:
        pass

    @classmethod
    def from_config(cls, config: dict) -> "Strategy":
        return cls(name=config.get("name", cls.__name__), config=config)
```

You are **adding a method** to this class, not changing `__init__`. Do not
touch `thresholds` or `self.thresholds` anywhere.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Full suite | `.venv/Scripts/python.exe -m pytest backend/tests -q` | `400 passed` before changes, 0 failed after |
| Strategy tests | `.venv/Scripts/python.exe -m pytest backend/tests/test_ensemble.py backend/tests/test_ensemble_edges.py backend/tests/test_value_only.py backend/tests/test_sport_specific.py backend/tests/test_combat_sports_strategy.py -q` | all pass |
| Odds math | `.venv/Scripts/python.exe -m pytest backend/tests/test_odds_utils.py -q` | all pass |

Run from repo root.

## Scope

**In scope**:
- `backend/analysis/strategy.py` (add shared `_average_odds`)
- `backend/analysis/variants/ensemble.py`
- `backend/analysis/variants/value_only.py`
- `backend/analysis/variants/sport_specific.py`
- `backend/analysis/variants/recent_form.py`
- `backend/analysis/variants/combat_sports.py`
- `backend/pipeline/pick_generator.py` (guard the `predict()` call only)
- New/updated tests under `backend/tests/`

**Out of scope** (do NOT touch, even though they look related):
- `backend/analysis/odds_utils.py` — `remove_vig` / `no_vig_implied_prob` are
  already correct. Do not modify them.
- **The spread and total edge logic** (`ensemble.py:64-66, 97-99`, the
  `spread_fair = 0.5` line). This was investigated and is **not** a bug: the
  moneyline path also measures edge over de-vigged fair probability, so the two
  are in consistent units. Leave it alone. (The genuine related gap — that
  spread/total *prices* are never fetched from the API — is a separate,
  larger piece of work.)
- `backend/analysis/kelly.py` — the negative-EV sizing bug is a separate
  finding.
- `backend/analysis/confidence.py` and the `calculate_confidence` call sites —
  that is plan 002.
- The backtester (`backend/backtesting/`) — it consumes strategy output; do not
  adjust its expectations to hide a change. See STOP conditions.

## Git workflow

- Branch: `advisor/003-devig-and-odds-averaging`
- Conventional commits:
  - `refactor(strategy): move odds averaging into the Strategy base class`
  - `fix(odds): average American odds in probability space, not arithmetically`
  - `fix(strategy): remove vig before computing moneyline edge in all variants`
  - `fix(picks): don't let one bad game discard the whole batch`

## Steps

### Step 1: Baseline

**Verify**: `.venv/Scripts/python.exe -m pytest backend/tests -q` → `400 passed`. Record the number. If anything fails, STOP.

### Step 2: Add the corrected shared `_average_odds` to the base class

In `backend/analysis/strategy.py`, add a concrete `_average_odds` method to
`Strategy`. It must:

- Be the **superset** version (returns `moneyline_home`, `moneyline_away`,
  `spread_home`, `spread_away`, `over_under`, with `None` for missing
  spread/total) so it can replace all five copies.
- Return `None` if `game.odds` is falsy, **or** if either `ml_home` or
  `ml_away` is empty (adopt `combat_sports`' stricter guard).
- **Average moneyline prices in probability space**, not arithmetically:
  1. convert each book's price to implied probability via
     `american_to_implied_prob`
  2. take the arithmetic mean of those probabilities
  3. convert the mean probability back to American odds
- Convert probability back to American with:
  `-round(100 * p / (1 - p))` when `p >= 0.5`, else `round(100 * (1 - p) / p)`.
  Guard `p` away from 0 and 1 first.
- **Never return a value in the invalid band.** After converting back, if the
  result lands in `(-100, 100)` (which happens at exactly `p == 0.5`), clamp to
  `-100` or `100` as appropriate rather than returning an invalid price.
- Skip any book whose stored price is itself invalid: wrap the per-book
  `american_to_implied_prob` in try/except `InvalidOddsError` and ignore that
  book, rather than failing the whole game.
- Keep spread/total averaging arithmetic and rounded to 1 decimal — those are
  *lines* (points), not prices, and arithmetic averaging is correct for them.

Do not delete the per-variant copies yet.

**Verify** — add and run a unit test for the new method asserting:
- `[-108, +104]` → a valid price whose implied prob is
  `pytest.approx(0.5047, abs=1e-3)` (the arithmetic version returned `-2` and
  raised)
- `[-110, -110]` → `-110` (identical books round-trip unchanged)
- a game with no odds → `None`
- a book with an invalid stored price (e.g. `0`) is skipped, not fatal

`.venv/Scripts/python.exe -m pytest backend/tests/test_strategy_base.py -q`
(create that file) → all pass.

### Step 3: Delete the five duplicate copies

Remove `_average_odds` from `ensemble.py`, `value_only.py`,
`sport_specific.py`, `recent_form.py`, and remove `_average_h2h_odds` from
`combat_sports.py`. In `combat_sports.py:36`, change
`avg_odds = self._average_h2h_odds(game)` to `avg_odds = self._average_odds(game)`.

All five classes already inherit from `Strategy`, so they pick up the base
implementation automatically.

**Verify**:
- `grep -rn "def _average_odds\|def _average_h2h_odds" backend/analysis/` →
  **exactly one** match, in `backend/analysis/strategy.py`
- `.venv/Scripts/python.exe -m pytest backend/tests -q` → 0 failed

### Step 4: Remove the vig in the four remaining variants

In `value_only.py`, `sport_specific.py`, `recent_form.py`, and
`combat_sports.py`, change the implied-probability block to de-vig, matching
`ensemble.py:36-38` exactly:

```python
            raw_home = american_to_implied_prob(avg_odds["moneyline_home"])
            raw_away = american_to_implied_prob(avg_odds["moneyline_away"])
            implied_home, implied_away = remove_vig(raw_home, raw_away)
            home_edge = (home_prob - implied_home) * 100
            away_edge = (away_prob - implied_away) * 100
```

Add `remove_vig` to each file's import from `backend.analysis.odds_utils`.

Leave the downstream `Pick(...)` construction untouched — `implied_probability`
will now carry the de-vigged value, which is the intended behavior and matches
`ensemble.py`.

**Verify**:
- `grep -rLn "remove_vig" backend/analysis/variants/*.py` — should list only
  `__init__.py` and `prop_value.py` (the two files that build no moneyline
  edge). Every strategy that computes a moneyline edge must import it.
- Add a test asserting that for each of the five variants, given two books at
  `-110 / -110` and a model probability of exactly `0.5238`, the reported
  `edge_pct` is `pytest.approx(0.0, abs=0.5)` — i.e. a price-matching model
  shows ~zero edge, not ~+2.4. Before this change it showed ≈ +0.0 for the home
  side and a spurious positive on the other. Model after
  `backend/tests/test_ensemble_edges.py`, which already documents the
  ensemble-vs-others difference.

### Step 5: Update `test_ensemble_edges.py`

`backend/tests/test_ensemble_edges.py:48-52` currently documents that the
ensemble and the other variants produce *different* edges for the same input.
After step 4 that is no longer true by design. Read that test, and update it to
assert the variants now **agree**, with a comment explaining the change and
referencing design doc §1f.

This is the one place you are permitted to change an existing assertion,
because the assertion documents the bug being fixed.

**Verify**: `.venv/Scripts/python.exe -m pytest backend/tests/test_ensemble_edges.py -q`
→ all pass.

### Step 6: Stop one bad game from discarding the batch

In `backend/pipeline/pick_generator.py`, wrap the per-game body (from
`_build_game_data` through the inner pick loop) in `try/except Exception`,
logging with `logger.exception("Pick generation failed for game %s", game.id)`
and `continue`. The `session.commit()` stays outside the loop.

Match the logging style already used in this repo (`logger.exception` /
`logger.warning` with `%s` placeholders — see
`backend/pipeline/scheduler.py:240-241`).

**Verify** — add a test that monkeypatches a strategy's `predict` to raise on
the second of three games, runs `generate_and_store_picks`, and asserts picks
from games 1 and 3 **are** committed. Before this change the count would be 0.

`.venv/Scripts/python.exe -m pytest backend/tests/test_pick_generator.py -q`
→ all pass.

### Step 7: Full suite, and report the pick-volume change

**Verify**:
- `.venv/Scripts/python.exe -m pytest backend/tests -q` → 0 failed
- `git diff --name-only` → only in-scope files

Then quantify the behavioral change and **include it in your completion
report**: run the existing backtester over a fixed window before and after
(use `git stash` to compare), and report the pick count and ROI for each. Do
not tune anything to make the numbers look better — just report both.

## Test plan

New tests:

1. `backend/tests/test_strategy_base.py` — four cases for the shared
   `_average_odds` (step 2), including the `[-108, +104]` case that used to
   raise.
2. Edge-parity test across all five variants (step 4) — a price-matching model
   yields ~0 edge everywhere.
3. `test_pick_generator.py` — one failing game does not discard the others
   (step 6).

Updated test:

4. `backend/tests/test_ensemble_edges.py` — inverted to assert agreement
   (step 5).

Follow the conventions in `backend/tests/test_api_picks.py` (plain `def`
tests, explicit seed helpers, no fixtures beyond what's already used).

## Done criteria

ALL must hold:

- [ ] `.venv/Scripts/python.exe -m pytest backend/tests -q` exits 0, 0 failed
- [ ] `grep -rn "def _average_odds\|def _average_h2h_odds" backend/analysis/`
      returns exactly **one** match (`backend/analysis/strategy.py`)
- [ ] `grep -rn "american_to_implied_prob(avg_odds" backend/analysis/variants/`
      returns no line that is **not** immediately followed by a `remove_vig`
      call — i.e. no variant computes a raw-implied moneyline edge
- [ ] The `[-108, +104]` case returns a valid price instead of raising
- [ ] The one-bad-game test proves the other games' picks survive
- [ ] `git diff --name-only` contains no file outside the In-scope list —
      in particular nothing under `backend/backtesting/`
- [ ] Before/after pick-count comparison is included in the completion report
- [ ] `plans/README.md` status row for 003 updated

## STOP conditions

Stop and report back (do not improvise) if:

- The baseline suite in step 1 does not pass cleanly.
- **An existing backtest or strategy test fails with a changed number after
  step 4.** Removing the vig legitimately reduces edges by ~2pp, so fewer picks
  will clear `min_edge`. Do **not** lower `min_edge` or edit the expected
  counts to compensate — that would hide the very effect this plan exists to
  produce. Report which tests moved and by how much; whether to retune
  `min_edge` is the operator's call.
- You conclude the spread/total `spread_fair = 0.5` line needs changing. It
  does not — see Out of scope.
- Removing a duplicate `_average_odds` breaks a variant because its copy had
  behavior the superset lacks. Report the difference rather than special-casing.
- The probability→American conversion produces values in `(-100, 100)` in any
  test. That means the clamp in step 2 is missing or wrong.

## Maintenance notes

- **Expect pick volume to fall** after this lands. Edges drop by roughly the
  overround (~2pp per side), so picks previously in the 5.0–7.0 edge band may
  no longer qualify. This is the fix working, not a regression. Whether
  `min_edge` should be retuned downward is a separate calibration decision —
  and once plan 002 lands, the nightly recalibrator is the right mechanism for
  it, not a hardcoded constant.
- `Strategy._average_odds` is now the single source of truth for five
  strategies. Any change to it affects every variant and the backtester —
  a reviewer should treat edits to it as high-blast-radius.
- Historical `edge_pct` values already stored in the `picks` table were
  computed with vig included and are **not comparable** to values written after
  this change. Any trend analysis spanning the deploy date must account for
  that discontinuity.
- The remaining related gap, deliberately deferred: spread and total prices are
  never fetched from the Odds API (`collectors/odds_api.py` reads only `point`,
  never `price` for those markets), so `odds_at_pick=-110` is hardcoded for
  every spread/total pick and grading assumes it. Fixing that requires a
  collector change plus an `Odds` schema change.

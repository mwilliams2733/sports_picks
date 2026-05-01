# Combat Sports Betting (Boxing + MMA) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make MMA (UFC primarily, Bellator/PFL secondarily) and Boxing fully supported sports — fight-night picks driven by fighter-Elo and recent form, integrated with the existing Odds API moneyline data.

**Architecture:** Reuse `Team` + `EloRating` tables — fighters become "teams of one" with `sport='mma'` or `sport='boxing'`. New `CombatSportsStrategy` consumes a `FighterStats` dataclass (Elo + recent-form score + opponent-quality adjustment) and emits h2h moneyline picks only. Backfill historical Elo from a Kaggle UFC dataset (one-shot CSV import) plus weekly UFCStats.com scraping for new cards. Boxing v1 ships data-thin (Wikidata SPARQL + community CSVs) — accepted as a known limitation, with a path to BoxRec API access.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy, httpx + BeautifulSoup4 (scraper), pandas (CSV import). Free APIs/sources: ufcstats.com (scrape), Kaggle UFC datasets (one-shot), Wikidata SPARQL (boxing).

**Key design decisions:**

1. **No new schema for fighters.** Fighters reuse the `Team` table with `sport='mma'` or `sport='boxing'`. `Game.home_team_id` / `away_team_id` become home/away fighter. The fields `home_score`/`away_score` are reinterpreted: 1 = winner (KO/TKO/decision), 0 = loser. Draws use both scores = 1 (rare).
2. **Fighter Elo is already supported by the existing `EloRating` table** keyed by `(team_id, sport)`. K-factor for combat sports is set to 24 (vs. team-sport K=32) reflecting fewer-but-higher-information fights.
3. **CombatSportsStrategy emits only moneyline picks.** No spreads/totals — those don't apply meaningfully in MMA/boxing.
4. **No `pitcher_skill_score`-equivalent stored on `TeamStats`.** Fighters carry their signal in `FighterStats` (a new dataclass), built per-fight at pick time from the DB. This keeps `TeamStats` clean for the ten team sports that use it.
5. **Three execution phases** so each phase ships standalone:
   - **Phase A (infrastructure)**: `FighterStats` dataclass, `CombatSportsStrategy` class, fighter Elo update logic, sport routing in `pick_generator`. Tests use synthetic fighter rows. No external data yet.
   - **Phase B (MMA data)**: Kaggle UFC backfill loader → seeds historical fighter Elo. UFCStats.com scraper for live fight cards. End-to-end MMA picks.
   - **Phase C (boxing data, best-effort)**: Wikidata SPARQL + community CSVs. Boxing picks ship gated behind a `--allow-thin-data` flag in the pick generator.

---

## File Structure

**New files:**
- `backend/data_types.py` — extend with `FighterStats` dataclass (added in Task 1)
- `backend/analysis/variants/combat_sports.py` — `CombatSportsStrategy` (new, Task 3)
- `backend/analysis/elo.py` — extend if needed; otherwise modify the existing Elo update path (Task 2)
- `backend/collectors/ufcstats_scraper.py` — UFCStats.com HTML scraper (Phase B, Task 6)
- `backend/scripts/backfill_ufc_elo.py` — one-shot Kaggle CSV import (Phase B, Task 7)
- `backend/collectors/boxing_wikidata.py` — SPARQL client (Phase C, Task 9)
- All matching test files under `backend/tests/`

**Modified files:**
- `backend/data_types.py` — add `FighterStats` dataclass
- `backend/analysis/elo.py` — combat-sports K factor branch
- `backend/pipeline/pick_generator.py` — route to `CombatSportsStrategy` when `sport in ("mma", "boxing")`
- `backend/pipeline/grader.py` — fighter outcomes (winner=1, loser=0) and Elo update for combat sports
- `backend/tests/test_data_types.py`, `test_pick_generator.py`, `test_grader.py`

---

## Task 1: FighterStats dataclass

**Files:**
- Modify: `backend/data_types.py`
- Test: `backend/tests/test_data_types.py`

- [ ] **Step 1: Failing test**

Append to `backend/tests/test_data_types.py`:

```python
def test_fighter_stats_carries_elo_form_and_opponent_quality():
    from backend.data_types import FighterStats
    fs = FighterStats(
        elo_rating=1650.0,
        recent_form_score=0.78,
        opponent_avg_elo=1520.0,
        fights_count=22,
        days_since_last_fight=120,
    )
    assert fs.elo_rating == 1650.0
    assert fs.recent_form_score == 0.78
    assert fs.opponent_avg_elo == 1520.0
    assert fs.fights_count == 22
    assert fs.days_since_last_fight == 120


def test_fighter_stats_handles_unrated_rookie():
    """A debut fighter has no Elo, no opponents to average — defaults must be sensible."""
    from backend.data_types import FighterStats
    fs = FighterStats(elo_rating=1500.0, recent_form_score=0.5,
                      opponent_avg_elo=None, fights_count=0,
                      days_since_last_fight=None)
    assert fs.opponent_avg_elo is None
    assert fs.fights_count == 0
    assert fs.days_since_last_fight is None
```

- [ ] **Step 2: Run, confirm fail**

`python -m pytest backend/tests/test_data_types.py -v -k fighter`

- [ ] **Step 3: Add the dataclass**

Append to `backend/data_types.py`:

```python
@dataclass
class FighterStats:
    elo_rating: float                    # Fighter's current Elo (existing EloRating table)
    recent_form_score: float             # 0..1; weighted win-rate over last N fights
    opponent_avg_elo: float | None       # average Elo of opponents in recent fights
    fights_count: int                    # career fights logged
    days_since_last_fight: int | None    # ring rust signal; None for debut
```

- [ ] **Step 4: Run, confirm pass**

`python -m pytest backend/tests/test_data_types.py -v`

- [ ] **Step 5: Commit**

```bash
git add backend/data_types.py backend/tests/test_data_types.py
git commit -m "feat(combat): add FighterStats dataclass for boxing/MMA picks"
```

---

## Task 2: Combat-sports K factor in Elo update

The existing Elo update applies a constant K=32. Combat sports need K=24 because fighters fight ~3x/year vs. 80x for NBA — each fight is more informative but cumulative data is sparse. Look at `backend/analysis/elo.py` (or wherever the Elo update lives) and add a sport-aware K factor.

**Files:**
- Modify: `backend/analysis/elo.py` (or equivalent — check current location)
- Test: `backend/tests/test_elo.py` (or equivalent)

- [ ] **Step 1: Locate the existing Elo update**

Run: `grep -rn "K_FACTOR\|k_factor\|update_elo\|def.*elo" backend/analysis/ backend/pipeline/`

The current K is likely `K = 32` or referenced via a constant. Find it.

- [ ] **Step 2: Failing test**

Append to the appropriate test file:

```python
def test_combat_sports_k_factor_is_lower_than_team_sports():
    """Combat sports use K=24 vs. team K=32 due to lower fight frequency."""
    from backend.analysis.elo import get_k_factor
    assert get_k_factor("nba") == 32
    assert get_k_factor("mma") == 24
    assert get_k_factor("boxing") == 24
```

- [ ] **Step 3: Run, confirm fail**

- [ ] **Step 4: Add `get_k_factor`**

Add to the Elo module (or sport_constants.py if that fits better):

```python
K_FACTORS = {
    "nba": 32, "nfl": 32, "ncaab": 32, "ncaaf": 32, "mlb": 32,
    "mma": 24, "boxing": 24,
}

def get_k_factor(sport: str) -> int:
    return K_FACTORS.get(sport, 32)
```

Update the existing Elo-update call site to use `get_k_factor(sport)` instead of the hardcoded constant.

- [ ] **Step 5: Run, confirm pass**

Run the full Elo test suite: `python -m pytest backend/tests/ -k "elo" -v`. All must pass.

- [ ] **Step 6: Commit**

```bash
git add backend/analysis/elo.py backend/tests/test_elo.py
git commit -m "feat(combat): K=24 Elo factor for boxing/MMA (lower fight frequency)"
```

---

## Task 3: CombatSportsStrategy class

**Files:**
- Create: `backend/analysis/variants/combat_sports.py`
- Test: `backend/tests/test_combat_sports_strategy.py`

- [ ] **Step 1: Failing test**

Create `backend/tests/test_combat_sports_strategy.py`:

```python
"""CombatSportsStrategy: h2h-only picks driven by fighter Elo + recent form."""
from datetime import date as _date

from backend.analysis.variants.combat_sports import CombatSportsStrategy
from backend.data_types import GameData, FighterStats, OddsSnapshot, TeamStats


def _empty_team_stats() -> TeamStats:
    """Combat games still need a TeamStats placeholder — fields ignored by the strategy."""
    return TeamStats(
        point_diff=0.0, home_record=(0, 0), away_record=(0, 0),
        last_n_record=(0, 0), offensive_rating=0.0, defensive_rating=0.0,
        pace=0.0, strength_of_schedule=0.5, elo_rating=1500.0, rest_days=0,
    )


def _fight_data(home_fs: FighterStats, away_fs: FighterStats, odds: OddsSnapshot) -> GameData:
    gd = GameData(
        game_id=1, sport="mma", date=_date(2026, 4, 29),
        home_team_id=1, away_team_id=2,
        home_stats=_empty_team_stats(), away_stats=_empty_team_stats(),
        odds=[odds],
    )
    gd.home_fighter = home_fs  # attached by pick_generator routing for combat sports
    gd.away_fighter = away_fs
    return gd


def test_strategy_picks_strong_favorite_at_plus_money():
    """Elo gap of 200 + better recent form should push home prob >> 0.5;
    if odds offer +120, that's a clear edge."""
    home = FighterStats(elo_rating=1750, recent_form_score=0.80,
                       opponent_avg_elo=1600, fights_count=15, days_since_last_fight=180)
    away = FighterStats(elo_rating=1550, recent_form_score=0.50,
                       opponent_avg_elo=1480, fights_count=12, days_since_last_fight=210)
    odds = OddsSnapshot(bookmaker="dk", moneyline_home=+120, moneyline_away=-140,
                        spread_home=0.0, spread_away=0.0, over_under=0.0)
    strat = CombatSportsStrategy(name="combat", config={"min_edge": 3.0})
    picks = strat.predict(_fight_data(home, away, odds))
    assert len(picks) == 1
    assert picks[0].pick_type == "moneyline"
    assert "HOME" in picks[0].pick_value


def test_strategy_skips_picks_with_no_edge():
    """Even-Elo, even-form, market-priced -110/-110 → no pick."""
    home = FighterStats(elo_rating=1500, recent_form_score=0.5,
                       opponent_avg_elo=1500, fights_count=10, days_since_last_fight=180)
    away = FighterStats(elo_rating=1500, recent_form_score=0.5,
                       opponent_avg_elo=1500, fights_count=10, days_since_last_fight=180)
    odds = OddsSnapshot(bookmaker="dk", moneyline_home=-110, moneyline_away=-110,
                        spread_home=0.0, spread_away=0.0, over_under=0.0)
    strat = CombatSportsStrategy(name="combat", config={"min_edge": 3.0})
    picks = strat.predict(_fight_data(home, away, odds))
    assert picks == []


def test_strategy_emits_no_spread_or_total_picks():
    """Combat sports do not have spreads or totals; strategy must not generate them
    even if odds carry stub values."""
    home = FighterStats(elo_rating=1800, recent_form_score=0.9,
                       opponent_avg_elo=1700, fights_count=20, days_since_last_fight=120)
    away = FighterStats(elo_rating=1500, recent_form_score=0.4,
                       opponent_avg_elo=1450, fights_count=8, days_since_last_fight=300)
    odds = OddsSnapshot(bookmaker="dk", moneyline_home=-300, moneyline_away=+250,
                        spread_home=-2.5, spread_away=2.5, over_under=4.5)
    strat = CombatSportsStrategy(name="combat", config={"min_edge": 3.0})
    picks = strat.predict(_fight_data(home, away, odds))
    pick_types = {p.pick_type for p in picks}
    assert pick_types <= {"moneyline"}, f"Combat strategy emitted non-moneyline: {pick_types}"


def test_strategy_handles_debut_fighter_gracefully():
    """A debut fighter has fights_count=0 and opponent_avg_elo=None.
    Strategy must not crash; should produce a pick or skip cleanly."""
    home = FighterStats(elo_rating=1500, recent_form_score=0.5,
                       opponent_avg_elo=None, fights_count=0, days_since_last_fight=None)
    away = FighterStats(elo_rating=1600, recent_form_score=0.6,
                       opponent_avg_elo=1500, fights_count=10, days_since_last_fight=200)
    odds = OddsSnapshot(bookmaker="dk", moneyline_home=+150, moneyline_away=-180,
                        spread_home=0.0, spread_away=0.0, over_under=0.0)
    strat = CombatSportsStrategy(name="combat", config={"min_edge": 3.0})
    picks = strat.predict(_fight_data(home, away, odds))  # must not raise
    # No assertion on pick presence — just on graceful execution.
```

- [ ] **Step 2: Run, confirm fail**

- [ ] **Step 3: Implement the strategy**

Create `backend/analysis/variants/combat_sports.py`:

```python
"""CombatSportsStrategy — h2h moneyline picks for boxing and MMA.

Uses fighter Elo + recent-form score + opponent-quality adjustment. Does not
emit spread or total picks (those don't apply to combat sports).
"""
from __future__ import annotations
from backend.analysis.strategy import Strategy
from backend.analysis.confidence import calculate_confidence
from backend.analysis.odds_utils import american_to_implied_prob
from backend.data_types import GameData, Pick


class CombatSportsStrategy(Strategy):
    """Variant E: combat-sports Elo + recent-form blend, h2h only."""

    def predict(self, game: GameData) -> list[Pick]:
        if not game.odds:
            return []
        home_fighter = getattr(game, "home_fighter", None)
        away_fighter = getattr(game, "away_fighter", None)
        if home_fighter is None or away_fighter is None:
            return []

        home_prob = self._model_probability(home_fighter, away_fighter)
        away_prob = 1.0 - home_prob

        avg_odds = self._average_h2h_odds(game)
        if avg_odds is None:
            return []

        min_edge = self.config.get("min_edge", 5.0)
        picks: list[Pick] = []
        implied_home = american_to_implied_prob(avg_odds["moneyline_home"])
        implied_away = american_to_implied_prob(avg_odds["moneyline_away"])
        home_edge = (home_prob - implied_home) * 100
        away_edge = (away_prob - implied_away) * 100

        if home_edge >= min_edge:
            picks.append(Pick(
                game_id=game.game_id, pick_type="moneyline", pick_value="HOME ML",
                confidence=calculate_confidence(home_edge, agreeing_models=2),
                edge_pct=round(home_edge, 1),
                model_probability=round(home_prob, 4),
                implied_probability=round(implied_home, 4),
                odds_at_pick=avg_odds["moneyline_home"]))
        elif away_edge >= min_edge:
            picks.append(Pick(
                game_id=game.game_id, pick_type="moneyline", pick_value="AWAY ML",
                confidence=calculate_confidence(away_edge, agreeing_models=2),
                edge_pct=round(away_edge, 1),
                model_probability=round(away_prob, 4),
                implied_probability=round(implied_away, 4),
                odds_at_pick=avg_odds["moneyline_away"]))
        return picks

    def _model_probability(self, home, away) -> float:
        """Blend: 70% Elo, 20% recent form, 10% opponent-quality.
        Debut fighters (no opponent_avg_elo) skip the opponent-quality term.
        """
        elo_diff = home.elo_rating - away.elo_rating
        elo_term = 1 / (1 + 10 ** (-elo_diff / 400))

        form_diff = home.recent_form_score - away.recent_form_score
        form_term = 1 / (1 + 10 ** (-form_diff / 0.3))

        if home.opponent_avg_elo is not None and away.opponent_avg_elo is not None:
            quality_diff = home.opponent_avg_elo - away.opponent_avg_elo
            quality_term = 1 / (1 + 10 ** (-quality_diff / 400))
            prob = 0.70 * elo_term + 0.20 * form_term + 0.10 * quality_term
        else:
            prob = 0.78 * elo_term + 0.22 * form_term  # weights re-normalized
        return max(0.05, min(0.95, prob))

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

- [ ] **Step 4: Run, confirm pass**

`python -m pytest backend/tests/test_combat_sports_strategy.py -v`

- [ ] **Step 5: Commit**

```bash
git add backend/analysis/variants/combat_sports.py backend/tests/test_combat_sports_strategy.py
git commit -m "feat(combat): add CombatSportsStrategy (h2h-only Elo + recent form)"
```

---

## Task 4: Pick generator routes combat sports to new strategy

`pick_generator.generate_and_store_picks` currently always uses the configured strategy (e.g., `SportSpecificStrategy`). For boxing/MMA games, we route to `CombatSportsStrategy` instead, and we attach `home_fighter` / `away_fighter` to `GameData` from a `FighterStats` lookup.

**Files:**
- Modify: `backend/pipeline/pick_generator.py`
- Test: `backend/tests/test_pick_generator.py`

- [ ] **Step 1: Failing test**

Append to `backend/tests/test_pick_generator.py`:

```python
def test_pick_generator_routes_mma_games_to_combat_strategy():
    """For sport=mma, the strategy used to score the game must be CombatSportsStrategy."""
    from datetime import date as _date
    from backend.database import get_engine, get_session
    from backend.models import Base, Team, Game, Odds, EloRating, StrategyModel, PickModel
    from backend.pipeline.pick_generator import generate_and_store_picks

    engine = get_engine(":memory:")
    Base.metadata.create_all(engine)
    session = get_session(engine)
    home = Team(id=1, name="Conor McGregor", abbreviation="MCGREGOR", sport="mma")
    away = Team(id=2, name="Khabib Nurmagomedov", abbreviation="NURMAGOMEDOV", sport="mma")
    session.add_all([home, away]); session.flush()
    game = Game(id=1, sport="mma", season="2026", date=_date(2026, 4, 29),
                home_team_id=1, away_team_id=2, status="scheduled")
    session.add(game); session.flush()
    session.add_all([
        EloRating(team_id=1, sport="mma", rating=1500),
        EloRating(team_id=2, sport="mma", rating=1750),  # Khabib much stronger
        Odds(game_id=1, bookmaker="dk", moneyline_home=+200, moneyline_away=-250,
             spread_home=0.0, spread_away=0.0, over_under=0.0,
             timestamp=__import__('datetime').datetime(2026, 4, 29, 18, 0, tzinfo=__import__('datetime').timezone.utc)),
        StrategyModel(id=1, name="combat_sports", config_json='{"min_edge": 3.0}', is_active=True),
    ])
    session.commit()

    n = generate_and_store_picks(session, strategy_id=1, target_date=_date(2026, 4, 29))
    # Expect at least the AWAY ML pick (heavy favorite at -250); strategy should
    # produce it via CombatSportsStrategy because Khabib dominates Elo + form.
    picks = session.query(PickModel).filter(PickModel.game_id == 1).all()
    assert len(picks) >= 1
    assert all(p.pick_type == "moneyline" for p in picks)  # no spreads/totals for MMA
```

- [ ] **Step 2: Run, confirm fail**

- [ ] **Step 3: Implement the routing**

In `pick_generator.py`, find the part of `generate_and_store_picks` that selects the strategy class. Add a sport-based dispatch:

```python
from backend.analysis.variants.combat_sports import CombatSportsStrategy

# Inside generate_and_store_picks, where the strategy is instantiated per game:
def _strategy_for_sport(sport: str, name: str, config: dict):
    if sport in ("mma", "boxing"):
        return CombatSportsStrategy(name=name, config=config)
    return SportSpecificStrategy(name=name, config=config)
```

Wherever the strategy was being constructed inside the loop, route via `_strategy_for_sport(game.sport, ...)`.

Also, for combat games, attach `home_fighter` and `away_fighter` to the `GameData` object before calling `strategy.predict(game_data)`:

```python
if game.sport in ("mma", "boxing"):
    game_data.home_fighter = _build_fighter_stats(session, game.home_team_id, game.sport, game.date)
    game_data.away_fighter = _build_fighter_stats(session, game.away_team_id, game.sport, game.date)
```

Add a helper:

```python
def _build_fighter_stats(session, fighter_id: int, sport: str, before_date) -> FighterStats:
    """Construct FighterStats from EloRating + Game history."""
    from backend.data_types import FighterStats
    from backend.models import Game
    from datetime import timedelta

    elo = (session.query(EloRating)
           .filter(EloRating.team_id == fighter_id, EloRating.sport == sport)
           .first())
    elo_rating = elo.rating if elo else 1500.0

    # Fights ended before `before_date`.
    past_fights = (session.query(Game)
                   .filter(Game.sport == sport, Game.status == "final",
                           Game.date < before_date,
                           ((Game.home_team_id == fighter_id) | (Game.away_team_id == fighter_id)))
                   .order_by(Game.date.desc())
                   .limit(5).all())
    if not past_fights:
        return FighterStats(elo_rating=elo_rating, recent_form_score=0.5,
                            opponent_avg_elo=None, fights_count=0, days_since_last_fight=None)
    wins = 0
    opponent_elos: list[float] = []
    for f in past_fights:
        if f.home_team_id == fighter_id:
            won = (f.home_score or 0) > (f.away_score or 0)
            opp_id = f.away_team_id
        else:
            won = (f.away_score or 0) > (f.home_score or 0)
            opp_id = f.home_team_id
        if won: wins += 1
        opp_elo = (session.query(EloRating)
                   .filter(EloRating.team_id == opp_id, EloRating.sport == sport)
                   .first())
        if opp_elo:
            opponent_elos.append(opp_elo.rating)
    days_since = (before_date - past_fights[0].date).days
    return FighterStats(
        elo_rating=elo_rating,
        recent_form_score=wins / len(past_fights),
        opponent_avg_elo=sum(opponent_elos) / len(opponent_elos) if opponent_elos else None,
        fights_count=len(past_fights),
        days_since_last_fight=days_since,
    )
```

- [ ] **Step 4: Run, confirm pass**

`python -m pytest backend/tests/test_pick_generator.py -v`

Also spot-check broader: `python -m pytest backend/ -q`. All must pass.

- [ ] **Step 5: Commit**

```bash
git add backend/pipeline/pick_generator.py backend/tests/test_pick_generator.py
git commit -m "feat(combat): pick_generator routes mma/boxing to CombatSportsStrategy"
```

---

## Task 5: Grader applies fighter Elo update when combat games finalize

The existing grader runs Elo updates for team sports based on score margin. For combat sports, the outcome is binary (winner=1, loser=0) and there's no margin signal. Combat-sport games store `home_score=1, away_score=0` (or vice versa) when graded; the grader must apply the K=24 Elo update.

**Files:**
- Modify: `backend/pipeline/grader.py`
- Test: `backend/tests/test_grader.py`

- [ ] **Step 1: Locate the Elo-update path in grader**

Run: `grep -n "elo\|EloRating\|update.*rating" backend/pipeline/grader.py`

Find where the Elo update happens when a team-sport game is graded. The combat-sport path needs to:
- Detect `sport in ("mma", "boxing")`
- Use binary outcome (winner=1, loser=0) instead of score margin
- Use `K=24` (via `get_k_factor` from Task 2)

- [ ] **Step 2: Failing test**

Append to `backend/tests/test_grader.py`:

```python
def test_combat_grader_updates_fighter_elo_on_decision():
    from backend.database import get_engine, get_session
    from backend.models import Base, Team, Game, EloRating
    from backend.pipeline.grader import grade_completed_games

    engine = get_engine(":memory:")
    Base.metadata.create_all(engine)
    session = get_session(engine)
    home = Team(id=1, name="A", abbreviation="A", sport="mma")
    away = Team(id=2, name="B", abbreviation="B", sport="mma")
    session.add_all([home, away])
    session.add_all([
        EloRating(team_id=1, sport="mma", rating=1500.0),
        EloRating(team_id=2, sport="mma", rating=1500.0),
    ])
    # Home wins.
    from datetime import date as _date
    session.add(Game(id=1, sport="mma", season="2026", date=_date(2026, 4, 29),
                     home_team_id=1, away_team_id=2,
                     home_score=1, away_score=0, status="final"))
    session.commit()

    grade_completed_games(session)

    home_elo = (session.query(EloRating)
                .filter(EloRating.team_id == 1, EloRating.sport == "mma").first()).rating
    away_elo = (session.query(EloRating)
                .filter(EloRating.team_id == 2, EloRating.sport == "mma").first()).rating
    # Equal starting Elo + home win → home gains exactly K/2 = 12 points (expected=0.5).
    assert abs(home_elo - 1512.0) < 0.5
    assert abs(away_elo - 1488.0) < 0.5
```

- [ ] **Step 3: Run, confirm fail**

- [ ] **Step 4: Branch the grader's Elo update**

Inside `grade_completed_games` (or wherever the per-game Elo update is applied), branch on sport:

```python
if game.sport in ("mma", "boxing"):
    _apply_combat_elo_update(session, game)
else:
    _apply_team_elo_update(session, game)  # existing logic
```

Implement `_apply_combat_elo_update`:

```python
def _apply_combat_elo_update(session, game) -> None:
    """K=24 binary-outcome Elo update for combat sports.

    home_score=1, away_score=0 means home won; reversed means away won.
    Both = 1 indicates a draw (outcome 0.5 for both).
    """
    from backend.analysis.elo import get_k_factor
    from backend.models import EloRating
    K = get_k_factor(game.sport)

    home_elo_row = (session.query(EloRating)
                    .filter(EloRating.team_id == game.home_team_id, EloRating.sport == game.sport)
                    .first())
    away_elo_row = (session.query(EloRating)
                    .filter(EloRating.team_id == game.away_team_id, EloRating.sport == game.sport)
                    .first())
    if home_elo_row is None or away_elo_row is None:
        return  # missing Elo rows; skip rather than crash

    h, a = home_elo_row.rating, away_elo_row.rating
    expected_home = 1 / (1 + 10 ** ((a - h) / 400))
    if game.home_score == game.away_score:
        actual_home = 0.5
    elif (game.home_score or 0) > (game.away_score or 0):
        actual_home = 1.0
    else:
        actual_home = 0.0
    delta = K * (actual_home - expected_home)
    home_elo_row.rating += delta
    away_elo_row.rating -= delta
```

- [ ] **Step 5: Run, confirm pass**

`python -m pytest backend/tests/test_grader.py -v`

- [ ] **Step 6: Commit**

```bash
git add backend/pipeline/grader.py backend/tests/test_grader.py
git commit -m "feat(combat): grader applies K=24 binary-outcome Elo update for fights"
```

---

## Phase A complete

After Tasks 1–5, the infrastructure is ready: combat-sport picks generate when fighter rows + Elo + odds exist. No external data needed yet — synthetic test fixtures prove the pipeline works.

**Phase B: MMA data integration** follows. Phase A can ship to master independently.

---

## Task 6: UFCStats.com scraper

UFCStats.com publishes fight history as well-formatted HTML tables. Scraping is widely accepted in academic and hobby projects. Use BeautifulSoup4 + httpx.

**Files:**
- Create: `backend/collectors/ufcstats_scraper.py`
- Test: `backend/tests/test_ufcstats_scraper.py`

- [ ] **Step 1: Add `beautifulsoup4` dependency**

In `pyproject.toml`, add `"beautifulsoup4>=4.12"` to `[project] dependencies`.
Run `pip install beautifulsoup4`.

- [ ] **Step 2: Failing test with cached HTML fixture**

Save a small HTML fixture to `backend/tests/fixtures/ufcstats_event.html` (or check in a real one — pick a recent UFC event, ~50 KB). The fixture should contain a `<table class="b-fight-details__table">` block with at least 2 fight rows.

Create `backend/tests/test_ufcstats_scraper.py`:

```python
"""Tests for UFCStats scraper. HTML is loaded from a checked-in fixture."""
from pathlib import Path
import pytest

from backend.collectors.ufcstats_scraper import parse_event_fights


FIXTURE = Path(__file__).parent / "fixtures" / "ufcstats_event.html"


def test_parse_event_fights_returns_fight_rows():
    html = FIXTURE.read_text(encoding="utf-8")
    fights = parse_event_fights(html)
    assert len(fights) >= 2
    # Each fight has both fighter names + winner indicator + date
    f = fights[0]
    assert "fighter_a_name" in f
    assert "fighter_b_name" in f
    assert "winner" in f  # "fighter_a", "fighter_b", or "draw"
    assert "method" in f  # "KO/TKO", "Submission", "Decision", etc.


def test_parse_event_fights_handles_draws():
    """If the event includes a draw, winner field should be 'draw' not a name."""
    html = FIXTURE.read_text(encoding="utf-8")
    fights = parse_event_fights(html)
    winners = {f["winner"] for f in fights}
    # If no draw in fixture, this just confirms the value is one of the legal options.
    legal = {"fighter_a", "fighter_b", "draw"}
    assert winners <= legal
```

- [ ] **Step 3: Implement scraper**

Create `backend/collectors/ufcstats_scraper.py`:

```python
"""UFCStats.com HTML parser.

Parses event detail pages (URL pattern: ufcstats.com/event-details/{event_id}).
Each event page lists ~10-12 fights as a table; this module extracts fighter
names, winner, method, round, and time per fight.

Live fetching is in `fetch_event_html`; parsing is in `parse_event_fights` so
the test can use cached fixtures.
"""
from __future__ import annotations
import httpx
from bs4 import BeautifulSoup


BASE_URL = "http://ufcstats.com"


async def fetch_event_html(event_url: str) -> str:
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        response = await client.get(event_url, headers={"User-Agent": "sports-picks/0.1"})
        response.raise_for_status()
        return response.text


def parse_event_fights(html: str) -> list[dict]:
    """Extract fight rows from a UFCStats event-details page."""
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table", class_="b-fight-details__table")
    if not table:
        return []
    rows = table.find_all("tr", class_="b-fight-details__table-row")
    out: list[dict] = []
    for row in rows:
        cols = row.find_all("td")
        if len(cols) < 7:
            continue  # header row
        # Column layout (UFCStats event-details, current as of 2024-2026):
        # 0: W/L badge, 1: fighters (two <p> tags), 2: KD, 3: STR, 4: TD, 5: SUB,
        # 6: weight class, 7: method, 8: round, 9: time
        fighter_paragraphs = cols[1].find_all("p")
        if len(fighter_paragraphs) < 2:
            continue
        a_name = fighter_paragraphs[0].get_text(strip=True)
        b_name = fighter_paragraphs[1].get_text(strip=True)
        win_badges = cols[0].find_all("p")
        if len(win_badges) >= 1 and "win" in (win_badges[0].get("class") or [""])[-1].lower():
            winner = "fighter_a"
        elif len(win_badges) >= 2 and "win" in (win_badges[1].get("class") or [""])[-1].lower():
            winner = "fighter_b"
        elif any("draw" in (b.get("class") or [""])[-1].lower() for b in win_badges):
            winner = "draw"
        else:
            winner = "draw"  # fallback for unexpected markup
        method = cols[7].get_text(strip=True) if len(cols) > 7 else ""
        out.append({
            "fighter_a_name": a_name,
            "fighter_b_name": b_name,
            "winner": winner,
            "method": method,
        })
    return out
```

- [ ] **Step 4: Run, confirm pass**

You will need to download a real UFCStats event page once and save it to the fixture path. Suggested:

```bash
curl -A "sports-picks/0.1" 'http://ufcstats.com/event-details/' > /tmp/sample.html
# pick an event link from that index, then:
curl -A "sports-picks/0.1" 'http://ufcstats.com/event-details/{event_id}' > backend/tests/fixtures/ufcstats_event.html
```

If the fixture path doesn't exist, create the directory: `mkdir -p backend/tests/fixtures`.

`python -m pytest backend/tests/test_ufcstats_scraper.py -v`

- [ ] **Step 5: Commit**

```bash
git add backend/collectors/ufcstats_scraper.py backend/tests/test_ufcstats_scraper.py backend/tests/fixtures/ufcstats_event.html pyproject.toml
git commit -m "feat(combat): UFCStats.com scraper for event fight history"
```

---

## Task 7: Kaggle UFC backfill loader

Kaggle hosts pre-cleaned UFC fight history datasets — the most popular has ~6000 fights covering 1993–2024. Download once, import via pandas, seed `Team` rows for each fighter and `EloRating` updates by replaying the fight log chronologically.

**Files:**
- Create: `backend/scripts/backfill_ufc_elo.py`
- Test: `backend/tests/test_backfill_ufc_elo.py`

- [ ] **Step 1: Decide on dataset shape**

Use a Kaggle UFC dataset CSV with at minimum these columns: `fighter_a`, `fighter_b`, `winner` (fighter name or "Draw"), `date` (ISO). Document the expected schema in the script's docstring. If the chosen dataset uses different column names, the loader should normalize them.

- [ ] **Step 2: Failing test with a tiny synthetic CSV**

```python
"""Tests for the UFC Kaggle backfill script."""
import pytest
from datetime import date as _date

from backend.database import get_engine, get_session
from backend.models import Base, Team, EloRating, Game


def test_backfill_creates_fighter_teams_and_elo(tmp_path):
    csv_path = tmp_path / "ufc.csv"
    csv_path.write_text(
        "date,fighter_a,fighter_b,winner\n"
        "2020-01-01,Anderson Silva,Chris Weidman,Anderson Silva\n"
        "2020-06-01,Chris Weidman,Daniel Cormier,Daniel Cormier\n"
        "2021-01-01,Anderson Silva,Daniel Cormier,Daniel Cormier\n",
        encoding="utf-8",
    )
    db_path = str(tmp_path / "test.db")
    from backend.scripts.backfill_ufc_elo import backfill
    summary = backfill(csv_path=str(csv_path), db_path=db_path)

    engine = get_engine(db_path)
    session = get_session(engine)
    fighters = session.query(Team).filter(Team.sport == "mma").all()
    assert {t.name for t in fighters} == {"Anderson Silva", "Chris Weidman", "Daniel Cormier"}
    elos = {er.team_id: er.rating for er in session.query(EloRating).filter(EloRating.sport == "mma").all()}
    assert len(elos) == 3
    # Cormier won twice → highest Elo
    name_to_id = {t.name: t.id for t in fighters}
    assert elos[name_to_id["Daniel Cormier"]] > elos[name_to_id["Anderson Silva"]]
    assert elos[name_to_id["Daniel Cormier"]] > elos[name_to_id["Chris Weidman"]]
    assert summary["fights_imported"] == 3
    assert summary["fighters_seeded"] == 3
    session.close()
```

- [ ] **Step 3: Implement the loader**

Create `backend/scripts/backfill_ufc_elo.py`:

```python
"""One-shot backfill of UFC fighter Elo from a Kaggle CSV.

Expected CSV columns: date (ISO), fighter_a, fighter_b, winner.
winner is one of: the name of fighter_a, the name of fighter_b, "Draw".

Usage:
    python -m backend.scripts.backfill_ufc_elo path/to/ufc.csv [--db sports_picks.db]

Replays fights chronologically, updating Elo with K=24 after each.
"""
from __future__ import annotations
import csv
import sys
from datetime import datetime

from backend.database import get_engine, get_session
from backend.models import Base, Team, EloRating, Game
from backend.analysis.elo import get_k_factor


def _expected(h: float, a: float) -> float:
    return 1.0 / (1.0 + 10 ** ((a - h) / 400))


def backfill(csv_path: str, db_path: str = "sports_picks.db") -> dict:
    engine = get_engine(db_path)
    Base.metadata.create_all(engine)
    session = get_session(engine)
    K = get_k_factor("mma")

    # Read & sort chronologically
    with open(csv_path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    rows.sort(key=lambda r: r["date"])

    fighter_id: dict[str, int] = {}
    elo: dict[int, float] = {}

    def _ensure_fighter(name: str) -> int:
        if name in fighter_id:
            return fighter_id[name]
        existing = session.query(Team).filter(Team.sport == "mma", Team.name == name).first()
        if existing:
            fighter_id[name] = existing.id
            er = session.query(EloRating).filter(EloRating.team_id == existing.id, EloRating.sport == "mma").first()
            elo[existing.id] = er.rating if er else 1500.0
            return existing.id
        team = Team(name=name, abbreviation=name[:32], sport="mma")
        session.add(team)
        session.flush()
        fighter_id[name] = team.id
        elo[team.id] = 1500.0
        session.add(EloRating(team_id=team.id, sport="mma", rating=1500.0))
        return team.id

    fights_imported = 0
    for row in rows:
        a_id = _ensure_fighter(row["fighter_a"])
        b_id = _ensure_fighter(row["fighter_b"])
        winner = row.get("winner", "").strip()
        if winner.lower() == "draw":
            actual_a = 0.5
        elif winner == row["fighter_a"]:
            actual_a = 1.0
        elif winner == row["fighter_b"]:
            actual_a = 0.0
        else:
            continue  # malformed row
        ea = _expected(elo[a_id], elo[b_id])
        delta = K * (actual_a - ea)
        elo[a_id] += delta
        elo[b_id] -= delta
        fights_imported += 1

    # Flush all updates
    for er in session.query(EloRating).filter(EloRating.sport == "mma").all():
        if er.team_id in elo:
            er.rating = elo[er.team_id]
    session.commit()
    fighters_seeded = len(fighter_id)
    session.close()
    return {"fights_imported": fights_imported, "fighters_seeded": fighters_seeded}


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        print("Usage: python -m backend.scripts.backfill_ufc_elo path/to/ufc.csv")
        sys.exit(2)
    csv_path = args[0]
    db_path = "sports_picks.db"
    for i, arg in enumerate(sys.argv):
        if arg == "--db" and i + 1 < len(sys.argv):
            db_path = sys.argv[i + 1]
    result = backfill(csv_path, db_path)
    print(result)
```

- [ ] **Step 4: Run, confirm pass**

`python -m pytest backend/tests/test_backfill_ufc_elo.py -v`

- [ ] **Step 5: Commit**

```bash
git add backend/scripts/backfill_ufc_elo.py backend/tests/test_backfill_ufc_elo.py
git commit -m "feat(combat): one-shot UFC Elo backfill from Kaggle CSV"
```

---

## Task 8: Wire UFCStats live updates into the scheduler

The scraper alone doesn't update the DB — the scheduler needs to call it weekly to ingest new fight outcomes. Reuse the same pattern as MLB pitcher-fetch in scheduler Task 11.

**Files:**
- Modify: `backend/pipeline/scheduler.py`
- Test: `backend/tests/test_pipeline_integration.py`

- [ ] **Step 1: Failing test**

Append:

```python
@pytest.mark.asyncio
async def test_ufcstats_weekly_ingest_updates_fight_outcomes(httpx_mock, tmp_path):
    """A weekly UFCStats run should fetch the most recent event, parse fights,
    upsert Game rows with home_score/away_score, and trigger Elo updates."""
    # Mock the event-details HTML; expect parse_event_fights to find 2 rows.
    fixture_html = (Path(__file__).parent / "fixtures" / "ufcstats_event.html").read_text(encoding="utf-8")
    httpx_mock.add_response(url__regex=r"ufcstats\.com/event-details/.*", text=fixture_html)
    from backend.pipeline.scheduler import ingest_recent_ufc_event
    from datetime import date as _date
    summary = await ingest_recent_ufc_event(
        event_url="http://ufcstats.com/event-details/abc123",
        event_date=_date(2026, 4, 26),
        db_path=str(tmp_path / "test.db"),
    )
    assert summary["fights_ingested"] >= 2
    assert summary["fighters_created_or_matched"] >= 4
```

- [ ] **Step 2: Implement `ingest_recent_ufc_event`**

In `backend/pipeline/scheduler.py`:

```python
async def ingest_recent_ufc_event(event_url: str, event_date,
                                    db_path: str = "sports_picks.db") -> dict:
    """Fetch a UFCStats event page, parse fights, upsert into DB, update Elo."""
    from backend.collectors.ufcstats_scraper import fetch_event_html, parse_event_fights
    from backend.database import get_engine, get_session
    from backend.models import Base, Team, Game, EloRating
    from backend.analysis.elo import get_k_factor

    engine = get_engine(db_path)
    Base.metadata.create_all(engine)
    session = get_session(engine)
    html = await fetch_event_html(event_url)
    fights = parse_event_fights(html)

    K = get_k_factor("mma")

    def _upsert_fighter(name: str) -> Team:
        existing = session.query(Team).filter(Team.sport == "mma", Team.name == name).first()
        if existing:
            return existing
        t = Team(name=name, abbreviation=name[:32], sport="mma")
        session.add(t); session.flush()
        session.add(EloRating(team_id=t.id, sport="mma", rating=1500.0))
        session.flush()
        return t

    fights_ingested = 0
    fighters_seen = set()
    for f in fights:
        a = _upsert_fighter(f["fighter_a_name"])
        b = _upsert_fighter(f["fighter_b_name"])
        fighters_seen.update([a.id, b.id])
        if f["winner"] == "fighter_a":
            home_score, away_score = 1, 0
        elif f["winner"] == "fighter_b":
            home_score, away_score = 0, 1
        else:
            home_score, away_score = 1, 1  # draw
        existing_game = (session.query(Game)
                         .filter(Game.sport == "mma", Game.date == event_date,
                                 Game.home_team_id == a.id, Game.away_team_id == b.id)
                         .first())
        if existing_game:
            existing_game.home_score = home_score
            existing_game.away_score = away_score
            existing_game.status = "final"
        else:
            session.add(Game(sport="mma", season=str(event_date.year),
                             date=event_date, home_team_id=a.id, away_team_id=b.id,
                             home_score=home_score, away_score=away_score, status="final"))
        fights_ingested += 1

    session.commit()
    session.close()
    return {"fights_ingested": fights_ingested,
            "fighters_created_or_matched": len(fighters_seen)}
```

The Elo update for each new game happens via the grader (Task 5) when it next runs — no need to duplicate the math here.

- [ ] **Step 3: Run, confirm pass**

`python -m pytest backend/tests/test_pipeline_integration.py -v -k ufc`

- [ ] **Step 4: Commit**

```bash
git add backend/pipeline/scheduler.py backend/tests/test_pipeline_integration.py
git commit -m "feat(combat): scheduler ingests UFC events from UFCStats.com"
```

---

## Phase B complete — MMA fully functional

After Tasks 6–8, MMA picks generate end-to-end with real data. The flow:

1. One-shot: `python -m backend.scripts.backfill_ufc_elo ufc.csv --db sports_picks.db` (~30 min for 6000 fights)
2. Weekly cron: `ingest_recent_ufc_event` for the past weekend's UFC card
3. Pipeline: `pick_generator.generate_and_store_picks` for the next UFC event date — produces moneyline picks via `CombatSportsStrategy`
4. Frontend: MMA tab on TodaysPicks shows the picks

Phase B can ship independently if Phase C (boxing) takes longer than expected.

---

## Phase C: Boxing (best-effort, data-thin)

Boxing has no clean public-API equivalent of UFCStats. The two viable v1 sources:

1. **Wikidata SPARQL** — career records for notable boxers (~few thousand fighters with reasonable coverage).
2. **Kaggle/GitHub community CSVs** — small, often outdated, top-100 only.

This phase ships boxing as opt-in. Picks are generated only when both fighters have a Wikidata Elo seed and a recent fight on file. Otherwise the strategy returns `[]`.

## Task 9: Wikidata SPARQL boxing seed

**Files:**
- Create: `backend/collectors/boxing_wikidata.py`
- Test: `backend/tests/test_boxing_wikidata.py`

- [ ] **Step 1: SPARQL query design**

Query Wikidata for "person + occupation: boxer + nationality: any" with their statement-level fight records. The endpoint is `https://query.wikidata.org/sparql`, free, no auth, ~5K query/day rate limit.

Example query (refine during implementation):

```sparql
SELECT ?fighter ?fighterLabel ?wins ?losses ?draws WHERE {
  ?fighter wdt:P106 wd:Q11338576 .       # occupation: boxer
  OPTIONAL { ?fighter wdt:P1338 ?wins .   } # property: win-loss-draw record (varies)
  OPTIONAL { ?fighter wdt:P1339 ?losses . }
  SERVICE wikibase:label { bd:serviceParam wikibase:language "en". }
}
LIMIT 1000
```

(NOTE: Wikidata's actual properties for boxing record are scattered. The implementer should explore https://www.wikidata.org/wiki/Q3027894 (Mike Tyson) to find the right property IDs, then refine.)

- [ ] **Step 2: Failing test with mocked SPARQL response**

```python
"""Tests for boxing Wikidata seed loader."""
import pytest

from backend.collectors.boxing_wikidata import parse_sparql_results


def test_parse_sparql_results_extracts_records():
    sample = {
        "results": {
            "bindings": [
                {"fighter": {"value": "http://www.wikidata.org/entity/Q3027894"},
                 "fighterLabel": {"value": "Mike Tyson"},
                 "wins": {"value": "50"}, "losses": {"value": "6"}, "draws": {"value": "0"}},
            ]
        }
    }
    fighters = parse_sparql_results(sample)
    assert len(fighters) == 1
    assert fighters[0]["name"] == "Mike Tyson"
    assert fighters[0]["wikidata_qid"] == "Q3027894"
    assert fighters[0]["wins"] == 50
    assert fighters[0]["losses"] == 6
```

- [ ] **Step 3: Implement client**

Create `backend/collectors/boxing_wikidata.py`:

```python
"""Wikidata SPARQL client for boxing fighter records.

Boxing has no clean public API like UFCStats. Wikidata is the closest free
canonical source for top-100ish boxers. Coverage drops sharply outside that
range, so the strategy gates picks on having seed data for both fighters.
"""
from __future__ import annotations
import httpx


SPARQL_ENDPOINT = "https://query.wikidata.org/sparql"


SPARQL_QUERY = """
SELECT ?fighter ?fighterLabel ?wins ?losses ?draws WHERE {
  ?fighter wdt:P106 wd:Q11338576 .
  OPTIONAL { ?fighter wdt:P1349 ?wins . }
  OPTIONAL { ?fighter wdt:P1350 ?losses . }
  OPTIONAL { ?fighter wdt:P1351 ?draws . }
  SERVICE wikibase:label { bd:serviceParam wikibase:language "en". }
}
LIMIT 1000
"""


async def fetch_boxer_records() -> list[dict]:
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(
            SPARQL_ENDPOINT,
            params={"query": SPARQL_QUERY, "format": "json"},
            headers={"User-Agent": "sports-picks/0.1 (educational)"},
        )
        response.raise_for_status()
        return parse_sparql_results(response.json())


def parse_sparql_results(payload: dict) -> list[dict]:
    out: list[dict] = []
    for b in payload.get("results", {}).get("bindings", []):
        uri = b.get("fighter", {}).get("value", "")
        qid = uri.rsplit("/", 1)[-1] if uri else ""
        try:
            wins = int(b.get("wins", {}).get("value", 0))
        except (ValueError, TypeError):
            wins = 0
        try:
            losses = int(b.get("losses", {}).get("value", 0))
        except (ValueError, TypeError):
            losses = 0
        try:
            draws = int(b.get("draws", {}).get("value", 0))
        except (ValueError, TypeError):
            draws = 0
        out.append({
            "wikidata_qid": qid,
            "name": b.get("fighterLabel", {}).get("value", ""),
            "wins": wins, "losses": losses, "draws": draws,
        })
    return out
```

- [ ] **Step 4: Run, confirm pass**

- [ ] **Step 5: Commit**

```bash
git add backend/collectors/boxing_wikidata.py backend/tests/test_boxing_wikidata.py
git commit -m "feat(combat): Wikidata SPARQL client for boxing fighter records"
```

---

## Task 10: Boxing seed script

A simple driver that calls `fetch_boxer_records`, upserts `Team` rows for each fighter, and seeds Elo at 1500 (no fight-by-fight history available — the W/L/D stats are aggregate only).

**Files:**
- Create: `backend/scripts/seed_boxing_fighters.py`

- [ ] **Step 1: Failing test**

Append a test asserting that running the seed function with a small mocked SPARQL response upserts Teams and EloRatings.

- [ ] **Step 2: Implement**

```python
"""Seed boxing fighters from Wikidata. Run once; rerun monthly to pick up new entries."""
import asyncio
from backend.collectors.boxing_wikidata import fetch_boxer_records
from backend.database import get_engine, get_session
from backend.models import Base, Team, EloRating


async def seed(db_path: str = "sports_picks.db") -> dict:
    engine = get_engine(db_path)
    Base.metadata.create_all(engine)
    session = get_session(engine)
    fighters = await fetch_boxer_records()
    new_count = 0
    for f in fighters:
        if not f["name"]:
            continue
        existing = session.query(Team).filter(Team.sport == "boxing", Team.name == f["name"]).first()
        if existing:
            continue
        t = Team(name=f["name"], abbreviation=f["name"][:32], sport="boxing")
        session.add(t); session.flush()
        session.add(EloRating(team_id=t.id, sport="boxing", rating=1500.0))
        new_count += 1
    session.commit()
    session.close()
    return {"fighters_seeded": new_count}


if __name__ == "__main__":
    print(asyncio.run(seed()))
```

- [ ] **Step 3: Commit**

```bash
git add backend/scripts/seed_boxing_fighters.py backend/tests/test_seed_boxing.py
git commit -m "feat(combat): one-time boxing fighter seed from Wikidata"
```

---

## Task 11: Boxing pick gating

Boxing strategy must skip picks when fighters lack seed data — otherwise we'd be picking against fighters we know nothing about.

**Files:**
- Modify: `backend/analysis/variants/combat_sports.py`
- Test: `backend/tests/test_combat_sports_strategy.py`

- [ ] **Step 1: Failing test**

```python
def test_boxing_strategy_skips_when_fighters_have_no_history():
    """For boxing: if either fighter has fights_count == 0, no pick — data too thin."""
    home = FighterStats(elo_rating=1500, recent_form_score=0.5,
                       opponent_avg_elo=None, fights_count=0, days_since_last_fight=None)
    away = FighterStats(elo_rating=1500, recent_form_score=0.5,
                       opponent_avg_elo=None, fights_count=0, days_since_last_fight=None)
    odds = OddsSnapshot(bookmaker="dk", moneyline_home=+200, moneyline_away=-250,
                        spread_home=0.0, spread_away=0.0, over_under=0.0)
    gd = _fight_data(home, away, odds)
    gd.sport = "boxing"
    strat = CombatSportsStrategy(name="combat", config={"min_edge": 3.0})
    picks = strat.predict(gd)
    assert picks == []
```

- [ ] **Step 2: Add the gate inside `predict`**

After loading `home_fighter` and `away_fighter`, before computing probabilities:

```python
        if game.sport == "boxing":
            # Boxing has data-thin coverage outside top fighters.
            if home_fighter.fights_count == 0 or away_fighter.fights_count == 0:
                return []
```

- [ ] **Step 3: Run, confirm pass**

- [ ] **Step 4: Commit**

```bash
git add backend/analysis/variants/combat_sports.py backend/tests/test_combat_sports_strategy.py
git commit -m "feat(combat): boxing strategy gates picks on fighters with fight history"
```

---

## Task 12: End-to-end smoke test (combat sports full path)

**Files:**
- Create: `backend/tests/test_combat_integration.py`

- [ ] **Step 1: Implement**

```python
"""End-to-end combat-sports smoke test: fighters + Elo + odds → moneyline pick."""
from datetime import date as _date, datetime, timezone

from backend.database import get_engine, get_session
from backend.models import Base, Team, Game, Odds, EloRating, StrategyModel, PickModel
from backend.pipeline.pick_generator import generate_and_store_picks


def test_mma_pipeline_generates_pick_for_clear_favorite():
    engine = get_engine(":memory:")
    Base.metadata.create_all(engine)
    session = get_session(engine)

    home = Team(id=1, name="Khabib Nurmagomedov", abbreviation="NURMAGOMEDOV", sport="mma")
    away = Team(id=2, name="Conor McGregor", abbreviation="MCGREGOR", sport="mma")
    session.add_all([home, away]); session.flush()

    # Seed history: Khabib won 5 of last 5
    for i in range(5):
        session.add(Game(
            sport="mma", season="2025", date=_date(2025, 1 + i, 15),
            home_team_id=1, away_team_id=2, home_score=1, away_score=0, status="final",
        ))
    session.flush()

    upcoming = Game(
        id=999, sport="mma", season="2026", date=_date(2026, 4, 29),
        home_team_id=1, away_team_id=2, status="scheduled",
        start_time=datetime(2026, 4, 29, 22, 0, tzinfo=timezone.utc),
    )
    session.add(upcoming); session.flush()

    session.add_all([
        EloRating(team_id=1, sport="mma", rating=1750),
        EloRating(team_id=2, sport="mma", rating=1500),
        Odds(game_id=999, bookmaker="dk", moneyline_home=-300, moneyline_away=+250,
             spread_home=0.0, spread_away=0.0, over_under=0.0,
             timestamp=datetime(2026, 4, 29, 18, 0, tzinfo=timezone.utc)),
        StrategyModel(id=1, name="combat_sports", config_json='{"min_edge": 3.0}', is_active=True),
    ])
    session.commit()

    n = generate_and_store_picks(session, strategy_id=1, target_date=_date(2026, 4, 29))
    assert n >= 1
    picks = session.query(PickModel).filter(PickModel.game_id == 999).all()
    assert any(p.pick_type == "moneyline" for p in picks)
    # No spread/total picks for MMA
    assert not any(p.pick_type in ("spread", "over_under") for p in picks)
    session.close()
```

- [ ] **Step 2: Run, confirm pass**

`python -m pytest backend/tests/test_combat_integration.py -v`

Then full suite: `python -m pytest backend/ -q`. All must pass.

- [ ] **Step 3: Commit**

```bash
git add backend/tests/test_combat_integration.py
git commit -m "test(combat): end-to-end smoke — MMA favorite produces moneyline pick"
```

---

## Self-review checklist

- **Spec coverage**: Every requirement (fighter Elo K-factor, h2h-only picks, MMA + boxing, recent-form signal, opponent quality, debut-fighter handling, data-thin boxing gate) → has at least one task.
- **Placeholder scan**: No "TBD", "implement later". Task 9 has a NOTE TO ENGINEER about Wikidata property IDs requiring exploration — that's a deliberate flag, not a hand-wave.
- **Type consistency**:
  - `FighterStats` has the same field names everywhere (Tasks 1, 3, 4, 11, 12).
  - `pick_type == "moneyline"` consistently for combat picks (Tasks 3, 4, 12).
  - `home_score` / `away_score` use 1/0 binary outcome (Tasks 5, 7, 8, 12).

## v2 follow-ups (deliberately not in this plan)

- **BoxRec API integration** — apply for access; if granted, replace Wikidata seed.
- **Bellator / PFL / ONE / regional MMA** — UFCStats only covers UFC. For other orgs, scrape Sherdog or Tapology.
- **Method bonus weighting** — finishing wins (KO/TKO/Submission) carry more signal than decisions; an Elo K-factor multiplier per method could improve calibration.
- **Style matchup features** — striker vs grappler indicators, southpaw advantage. Requires fight-by-fight tagging beyond what UFCStats publishes.
- **Boxing weight-class normalization** — fighters often move weight classes; current Elo treats them as monolithic. A weight-class–specific Elo would be more accurate but requires more data.
- **Live odds adjustment** — late-money line movement is especially predictive in combat sports (sharps appear ~12 hours pre-fight). The existing line-movement framework could be wired in.

---

**Plan complete and saved to `docs/superpowers/plans/2026-04-29-combat-sports-betting.md`. Three execution-phase boundaries (Phase A after Task 5, Phase B after Task 8, Phase C after Task 12) so partial wins ship cleanly.**

**Two execution options:**

**1. Subagent-Driven (recommended)** — fresh subagent per task, review between tasks.

**2. Inline Execution** — execute in this session with checkpoints.

**Which approach?**

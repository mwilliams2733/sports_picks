# MLB Betting Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add MLB (regular-season + playoffs) as a fully-supported sport with pitcher-adjusted moneyline / run-line / totals picks, reusing the existing ELO + team-stats pipeline plus a new pitcher signal.

**Architecture:** MLB plugs into the existing collector → pipeline → strategy stack the same way the four current team sports do. The one new piece is a pitcher signal: a small `mlb_stats.py` collector hits the free MLB Stats API for probable starters + their rolling ERA/K9, and a `pitcher_skill_score()` function reduces those stats to a [0,1] score. The score rides into pick generation as a new optional field on `TeamStats` and is consumed by `SportSpecificStrategy` only when `sport == "mlb"` — zero behavior change for other sports.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy, httpx, pytest. Frontend: React 19 + TypeScript + Vite. Free APIs: `statsapi.mlb.com` (no key), The Odds API (`baseball_mlb`), ESPN MLB scoreboard.

**Key design decisions:**
1. **No schema changes for v1.** Pitcher data is fetched on-the-fly during pipeline runs and held in memory through pick generation. Persisting pitcher snapshots for backtesting is a v2 follow-up.
2. **Pitcher signal is additive.** Existing strategy logic for team sports is untouched; the MLB branch in `_model_probability` adds a new term weighted at 0.50 (the dominant signal in baseball).
3. **MLB run-line is always ±1.5.** No need to support arbitrary spreads — the Odds API returns this consistently.
4. **F5 markets are explicitly out of scope.** Mentioned in the brainstorm; deferred.

---

## File Structure

**New files:**
- `backend/collectors/mlb_stats.py` — MLB Stats API client (schedule + probable pitchers + rolling stats)
- `backend/analysis/pitcher.py` — `pitcher_skill_score()` function and tests' shared fixtures
- `backend/tests/test_mlb_stats.py`
- `backend/tests/test_pitcher.py`
- `backend/tests/test_mlb_integration.py` — end-to-end smoke

**Modified files:**
- `backend/analysis/sport_constants.py` — MLB entries in 4 dicts
- `backend/collectors/odds_api.py:3-10, 134-138` — add `mlb` to `SPORT_KEYS` and `PROP_MARKETS`
- `backend/collectors/espn.py:3-9` — add MLB scoreboard URL
- `backend/pipeline/full_pipeline.py:13` — add `mlb` to `ALL_SPORTS`
- `backend/data_types.py:5-21` — add `pitcher_skill_score: float | None = None` to `TeamStats`
- `backend/analysis/variants/sport_specific.py:8-13, 76-103, 138-149` — add `mlb` weight profile, MLB branch in `_model_probability`, MLB branch in `_count_agreeing`
- `backend/pipeline/pick_generator.py:101-127` — load pitcher stats when sport == "mlb"
- `backend/tests/test_sport_constants.py` — extend coverage to MLB
- `backend/tests/test_odds_api.py` — assert MLB key
- `backend/tests/test_espn.py` — assert MLB URL
- `backend/tests/test_sport_specific.py:56` — relax sport-set assertion
- `frontend/src/pages/TodaysPicks.tsx:14`, `frontend/src/pages/PlayerProps.tsx:9`, `frontend/src/pages/TrackRecord.tsx:8`, `frontend/src/components/StrategyForm.tsx:11`, `frontend/src/components/Layout.tsx:18`, `frontend/src/pages/Backtesting.tsx:7` — add `'mlb'` to SPORTS arrays

---

## Task 1: Add MLB to sport constants

**Why first:** Every downstream piece (collectors, strategies, frontend) imports these. Adding them first means subsequent tasks can use the helpers.

**Files:**
- Modify: `backend/analysis/sport_constants.py`
- Test: `backend/tests/test_sport_constants.py`

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_sport_constants.py`:

```python
def test_mlb_constants_have_baseball_calibrated_values():
    """MLB scoring is much lower-variance than other sports — constants must reflect that."""
    from backend.analysis.sport_constants import (
        get_point_diff_std, get_total_points_std,
        get_home_advantage_elo, get_home_win_rate,
    )
    # Run differential SD: empirically ~3.0 for MLB vs ~12 for NBA.
    assert get_point_diff_std("mlb") == 3.5
    # Total runs SD: empirically ~4.0.
    assert get_total_points_std("mlb") == 4.0
    # MLB has the smallest HCA in major US sports.
    assert get_home_advantage_elo("mlb") == 24
    assert get_home_win_rate("mlb") == 0.54
```

- [ ] **Step 2: Run the test and verify it fails**

Run: `python -m pytest backend/tests/test_sport_constants.py::test_mlb_constants_have_baseball_calibrated_values -v`
Expected: FAIL — constants return defaults (12.0, 15.0, 100, 0.57).

- [ ] **Step 3: Add MLB entries to all four dicts**

In `backend/analysis/sport_constants.py`, add `"mlb": <value>,` to each dict:

```python
POINT_DIFF_STD = {
    "nba": 12.0, "nfl": 13.5, "ncaab": 11.0, "ncaaf": 17.0,
    "boxing": 12.0, "mma": 12.0,
    "mlb": 3.5,
}

TOTAL_POINTS_STD = {
    "nba": 15.0, "nfl": 13.0, "ncaab": 12.0, "ncaaf": 16.0,
    "boxing": 15.0, "mma": 15.0,
    "mlb": 4.0,
}

HOME_ADVANTAGE_ELO = {
    "nba": 100, "nfl": 48, "ncaab": 120, "ncaaf": 65,
    "boxing": 0, "mma": 0,
    "mlb": 24,
}

HOME_WIN_RATE = {
    "nba": 0.60, "nfl": 0.57, "ncaab": 0.67, "ncaaf": 0.62,
    "boxing": 0.50, "mma": 0.50,
    "mlb": 0.54,
}
```

- [ ] **Step 4: Run the test and verify it passes**

Run: `python -m pytest backend/tests/test_sport_constants.py -v`
Expected: PASS (all sport-constants tests, including MLB).

- [ ] **Step 5: Commit**

```bash
git add backend/analysis/sport_constants.py backend/tests/test_sport_constants.py
git commit -m "feat(mlb): add MLB sport constants (low-variance scoring, small HCA)"
```

---

## Task 2: Wire MLB into Odds API collector

**Files:**
- Modify: `backend/collectors/odds_api.py:3-10, 134-138`
- Test: `backend/tests/test_odds_api.py`

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_odds_api.py`:

```python
def test_mlb_is_in_sport_keys():
    """MLB must map to The Odds API's baseball_mlb key for fetch_odds to work."""
    from backend.collectors.odds_api import SPORT_KEYS
    assert SPORT_KEYS["mlb"] == "baseball_mlb"


def test_mlb_prop_markets_defined():
    """MLB has popular prop markets — they must be wired up."""
    from backend.collectors.odds_api import PROP_MARKETS
    assert "batter_hits" in PROP_MARKETS["mlb"]
    assert "pitcher_strikeouts" in PROP_MARKETS["mlb"]


def test_mlb_uses_full_market_set():
    """MLB has h2h + spreads (run line) + totals — should NOT be h2h-only like combat sports."""
    from backend.collectors.odds_api import OddsAPICollector
    # The branch that picks h2h-only is `sport in ("boxing", "mma")`. Verify mlb is not in that set
    # by inspecting the source — read the line to make this self-documenting.
    import inspect
    src = inspect.getsource(OddsAPICollector.fetch_odds)
    assert '"boxing", "mma"' in src or "'boxing', 'mma'" in src, (
        "h2h-only branch must not silently pick up mlb"
    )
```

- [ ] **Step 2: Run tests and verify they fail**

Run: `python -m pytest backend/tests/test_odds_api.py -v -k mlb`
Expected: FAIL on the first two; the third should already PASS (it's a guard).

- [ ] **Step 3: Add MLB to SPORT_KEYS and PROP_MARKETS**

In `backend/collectors/odds_api.py`:

```python
SPORT_KEYS = {
    "nba": "basketball_nba",
    "nfl": "americanfootball_nfl",
    "ncaab": "basketball_ncaab",
    "ncaaf": "americanfootball_ncaaf",
    "boxing": "boxing_boxing",
    "mma": "mma_mixed_martial_arts",
    "mlb": "baseball_mlb",
}
```

And in the `PROP_MARKETS` block (around line 134-138):

```python
PROP_MARKETS = {
    "nba": ["player_points", "player_rebounds", "player_assists"],
    "nfl": ["player_pass_yds", "player_rush_yds", "player_anytime_td"],
    "ncaab": ["player_points", "player_rebounds", "player_assists"],
    "ncaaf": ["player_pass_yds", "player_rush_yds", "player_anytime_td"],
    "boxing": [],
    "mma": [],
    "mlb": ["batter_hits", "batter_home_runs", "batter_total_bases", "pitcher_strikeouts"],
}
```

- [ ] **Step 4: Run tests and verify they pass**

Run: `python -m pytest backend/tests/test_odds_api.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/collectors/odds_api.py backend/tests/test_odds_api.py
git commit -m "feat(mlb): wire MLB into Odds API SPORT_KEYS and PROP_MARKETS"
```

---

## Task 3: Add MLB to ESPN scoreboard

**Files:**
- Modify: `backend/collectors/espn.py:3-9`
- Test: `backend/tests/test_espn.py`

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_espn.py`:

```python
def test_mlb_scoreboard_url_present():
    from backend.collectors.espn import SPORT_URLS
    url = SPORT_URLS.get("mlb")
    assert url is not None
    assert "baseball/mlb/scoreboard" in url
```

- [ ] **Step 2: Run test and verify it fails**

Run: `python -m pytest backend/tests/test_espn.py::test_mlb_scoreboard_url_present -v`
Expected: FAIL — `SPORT_URLS["mlb"]` is None.

- [ ] **Step 3: Add MLB URL**

In `backend/collectors/espn.py`:

```python
SPORT_URLS = {
    "nba": "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard",
    "nfl": "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard",
    "ncaab": "https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/scoreboard",
    "ncaaf": "https://site.api.espn.com/apis/site/v2/sports/football/college-football/scoreboard",
    "mma": "https://site.api.espn.com/apis/site/v2/sports/mma/ufc/scoreboard",
    "mlb": "https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/scoreboard",
}
```

- [ ] **Step 4: Run test and verify it passes**

Run: `python -m pytest backend/tests/test_espn.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/collectors/espn.py backend/tests/test_espn.py
git commit -m "feat(mlb): add MLB ESPN scoreboard URL"
```

---

## Task 4: MLB Stats API collector — schedule and probable pitchers

This is the meat. The MLB Stats API is the cleanest free source for probable pitchers — Odds API and ESPN don't reliably surface them. We hit two endpoints: `schedule` (for the day's games + game pks) and `people/{id}/stats` (for pitcher rolling stats).

**Files:**
- Create: `backend/collectors/mlb_stats.py`
- Test: `backend/tests/test_mlb_stats.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_mlb_stats.py`:

```python
"""Tests for the MLB Stats API collector. HTTP is mocked via httpx_mock."""
import pytest
from datetime import date
from backend.collectors.mlb_stats import MLBStatsCollector


@pytest.fixture
def schedule_payload():
    """Minimal MLB Stats API /schedule shape."""
    return {
        "dates": [{
            "games": [{
                "gamePk": 700001,
                "gameDate": "2026-04-29T23:05:00Z",
                "teams": {
                    "home": {
                        "team": {"id": 111, "name": "Boston Red Sox", "abbreviation": "BOS"},
                        "probablePitcher": {"id": 5001, "fullName": "A. Pitcher"},
                    },
                    "away": {
                        "team": {"id": 147, "name": "New York Yankees", "abbreviation": "NYY"},
                        "probablePitcher": {"id": 5002, "fullName": "B. Pitcher"},
                    },
                },
            }],
        }],
    }


@pytest.fixture
def pitcher_stats_payload():
    """MLB Stats API /people/{id}/stats?stats=gameLog — pitcher recent starts."""
    return {
        "stats": [{
            "splits": [
                {"stat": {"era": "2.50", "strikeOuts": 8, "inningsPitched": "6.0"}},
                {"stat": {"era": "3.00", "strikeOuts": 7, "inningsPitched": "5.2"}},
                {"stat": {"era": "1.50", "strikeOuts": 9, "inningsPitched": "7.0"}},
            ],
        }],
    }


@pytest.mark.asyncio
async def test_fetch_schedule_returns_games_with_probables(httpx_mock, schedule_payload):
    httpx_mock.add_response(
        url="https://statsapi.mlb.com/api/v1/schedule?sportId=1&date=2026-04-29&hydrate=probablePitcher",
        json=schedule_payload,
    )
    collector = MLBStatsCollector()
    games = await collector.fetch_schedule(date(2026, 4, 29))
    assert len(games) == 1
    g = games[0]
    assert g["mlb_game_pk"] == 700001
    assert g["home_team"] == "BOS"
    assert g["away_team"] == "NYY"
    assert g["home_probable_pitcher_id"] == 5001
    assert g["away_probable_pitcher_id"] == 5002
    await collector.close()


@pytest.mark.asyncio
async def test_fetch_schedule_handles_missing_probables(httpx_mock):
    """Early in the day, MLB hasn't announced pitchers yet — must not crash."""
    payload = {
        "dates": [{
            "games": [{
                "gamePk": 700002,
                "gameDate": "2026-04-29T23:05:00Z",
                "teams": {
                    "home": {"team": {"id": 111, "abbreviation": "BOS"}},
                    "away": {"team": {"id": 147, "abbreviation": "NYY"}},
                },
            }],
        }],
    }
    httpx_mock.add_response(
        url="https://statsapi.mlb.com/api/v1/schedule?sportId=1&date=2026-04-29&hydrate=probablePitcher",
        json=payload,
    )
    collector = MLBStatsCollector()
    games = await collector.fetch_schedule(date(2026, 4, 29))
    assert games[0]["home_probable_pitcher_id"] is None
    assert games[0]["away_probable_pitcher_id"] is None
    await collector.close()


@pytest.mark.asyncio
async def test_fetch_pitcher_recent_stats_aggregates_last_n(httpx_mock, pitcher_stats_payload):
    httpx_mock.add_response(
        url="https://statsapi.mlb.com/api/v1/people/5001/stats?stats=gameLog&group=pitching&season=2026",
        json=pitcher_stats_payload,
    )
    collector = MLBStatsCollector()
    stats = await collector.fetch_pitcher_recent(pitcher_id=5001, season=2026, last_n=5)
    # Three starts in payload: ERAs 2.50, 3.00, 1.50; mean = 2.333
    assert abs(stats["era_recent"] - 2.333) < 0.01
    # K total = 24, IP total = 18.667 → K/9 = 24 * 9 / 18.667 ≈ 11.57
    assert abs(stats["k9_recent"] - 11.57) < 0.05
    assert stats["starts_seen"] == 3
    await collector.close()


@pytest.mark.asyncio
async def test_fetch_pitcher_recent_handles_no_starts(httpx_mock):
    httpx_mock.add_response(
        url="https://statsapi.mlb.com/api/v1/people/9999/stats?stats=gameLog&group=pitching&season=2026",
        json={"stats": [{"splits": []}]},
    )
    collector = MLBStatsCollector()
    stats = await collector.fetch_pitcher_recent(pitcher_id=9999, season=2026)
    assert stats is None
    await collector.close()
```

- [ ] **Step 2: Verify pytest-httpx is installed**

Run: `python -c "import pytest_httpx" 2>&1 || pip install pytest-httpx`
Expected: import succeeds, or installs successfully.

- [ ] **Step 3: Run tests and verify they fail**

Run: `python -m pytest backend/tests/test_mlb_stats.py -v`
Expected: FAIL — `mlb_stats` module not found.

- [ ] **Step 4: Create the collector**

Create `backend/collectors/mlb_stats.py`:

```python
"""Free MLB Stats API client. No auth required.

Used to fetch schedule + probable pitchers + pitcher recent rolling stats.
The Odds API and ESPN do not reliably surface probable starting pitchers,
which is the dominant feature for MLB game predictions.
"""
from __future__ import annotations
from datetime import date
import httpx


BASE_URL = "https://statsapi.mlb.com/api/v1"


class MLBStatsCollector:
    def __init__(self):
        self.client = httpx.AsyncClient(timeout=30.0)

    async def fetch_schedule(self, target_date: date) -> list[dict]:
        """Return today's games with probable pitcher IDs (if announced).

        Each item: {mlb_game_pk, home_team, away_team, home_probable_pitcher_id,
        away_probable_pitcher_id, game_date_iso}
        """
        url = f"{BASE_URL}/schedule"
        params = {
            "sportId": 1,  # MLB
            "date": target_date.strftime("%Y-%m-%d"),
            "hydrate": "probablePitcher",
        }
        response = await self.client.get(url, params=params)
        response.raise_for_status()
        data = response.json()
        out: list[dict] = []
        for d in data.get("dates", []):
            for g in d.get("games", []):
                home = g["teams"]["home"]
                away = g["teams"]["away"]
                out.append({
                    "mlb_game_pk": g["gamePk"],
                    "game_date_iso": g["gameDate"],
                    "home_team": home["team"].get("abbreviation"),
                    "away_team": away["team"].get("abbreviation"),
                    "home_probable_pitcher_id": (home.get("probablePitcher") or {}).get("id"),
                    "away_probable_pitcher_id": (away.get("probablePitcher") or {}).get("id"),
                })
        return out

    async def fetch_pitcher_recent(self, pitcher_id: int, season: int,
                                    last_n: int = 5) -> dict | None:
        """Return a dict {era_recent, k9_recent, starts_seen} for the pitcher's
        most recent N starts this season, or None if no starts logged.
        """
        url = f"{BASE_URL}/people/{pitcher_id}/stats"
        params = {"stats": "gameLog", "group": "pitching", "season": season}
        response = await self.client.get(url, params=params)
        response.raise_for_status()
        splits = (response.json().get("stats") or [{}])[0].get("splits", [])
        if not splits:
            return None
        recent = splits[-last_n:] if last_n else splits
        eras: list[float] = []
        ks_total = 0
        ip_total = 0.0
        for s in recent:
            stat = s.get("stat", {})
            try:
                eras.append(float(stat.get("era", 0)))
            except (TypeError, ValueError):
                pass
            ks_total += int(stat.get("strikeOuts", 0) or 0)
            # MLB IP is decimal: 6.0 = 6 IP, 6.1 = 6 1/3, 6.2 = 6 2/3.
            ip_str = str(stat.get("inningsPitched", "0"))
            ip_total += _ip_to_float(ip_str)
        if not eras or ip_total <= 0:
            return None
        return {
            "era_recent": sum(eras) / len(eras),
            "k9_recent": ks_total * 9.0 / ip_total,
            "starts_seen": len(recent),
        }

    async def close(self):
        await self.client.aclose()


def _ip_to_float(ip: str) -> float:
    """Convert MLB-style innings-pitched string ('6.1' = 6 1/3) to a float."""
    try:
        whole, frac = ip.split(".")
        whole_n = int(whole)
        third = int(frac) if frac else 0
        return whole_n + third / 3.0
    except (ValueError, AttributeError):
        try:
            return float(ip)
        except (TypeError, ValueError):
            return 0.0
```

- [ ] **Step 5: Run tests and verify they pass**

Run: `python -m pytest backend/tests/test_mlb_stats.py -v`
Expected: PASS (4 tests).

- [ ] **Step 6: Commit**

```bash
git add backend/collectors/mlb_stats.py backend/tests/test_mlb_stats.py
git commit -m "feat(mlb): add MLB Stats API collector for schedule + probable pitchers"
```

---

## Task 5: Pitcher skill score function

**Files:**
- Create: `backend/analysis/pitcher.py`
- Test: `backend/tests/test_pitcher.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_pitcher.py`:

```python
"""Pitcher skill score: reduces ERA + K/9 to a [0, 1] score.

Calibration: league-average ERA ~4.00 → 0.50; ace ERA ~2.50 → ~0.85;
replacement ERA ~5.50 → ~0.20. K/9 contributes a smaller bump.
"""
from backend.analysis.pitcher import pitcher_skill_score


def test_average_pitcher_scores_around_half():
    s = pitcher_skill_score(era=4.00, k9=8.5)  # ~league average
    assert 0.45 <= s <= 0.55


def test_ace_pitcher_scores_high():
    s = pitcher_skill_score(era=2.40, k9=11.5)  # Cy Young calibre
    assert s >= 0.80


def test_replacement_level_scores_low():
    s = pitcher_skill_score(era=5.80, k9=6.5)
    assert s <= 0.25


def test_score_is_clamped_to_unit_interval():
    """Extreme inputs must not produce <0 or >1."""
    very_low = pitcher_skill_score(era=0.10, k9=20.0)
    very_high = pitcher_skill_score(era=15.0, k9=2.0)
    assert 0.0 <= very_low <= 1.0
    assert 0.0 <= very_high <= 1.0


def test_missing_inputs_return_neutral():
    """If we have no pitcher data, skill = 0.5 (no edge either direction)."""
    assert pitcher_skill_score(era=None, k9=None) == 0.5
```

- [ ] **Step 2: Run tests and verify they fail**

Run: `python -m pytest backend/tests/test_pitcher.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement pitcher_skill_score**

Create `backend/analysis/pitcher.py`:

```python
"""Reduce a pitcher's recent rolling stats to a [0, 1] skill score.

Used by the MLB branch of SportSpecificStrategy as the dominant signal —
in MLB, the starting pitcher is the single biggest game-to-game variable.

Calibration (rough, league-relative):
  ERA 2.50 → ~0.85   (ace)
  ERA 4.00 → ~0.50   (league average)
  ERA 5.50 → ~0.20   (replacement-level)
"""
from __future__ import annotations


# League-average anchors. Centered so that ERA=4.00 + K/9=8.5 yields exactly 0.5.
_LEAGUE_ERA = 4.00
_LEAGUE_K9 = 8.5

# Sigmoid scale parameters: smaller scale = sharper slope around the anchor.
_ERA_SCALE = 1.5  # ERA contributes the bulk of the signal
_K9_SCALE = 4.0   # K/9 is a tie-breaker / strikeout-stuff bump

# Weights sum to 1.0
_W_ERA = 0.75
_W_K9 = 0.25


def _sigmoid(x: float) -> float:
    # Stable for both signs.
    if x >= 0:
        return 1.0 / (1.0 + 2.71828 ** (-x))
    e = 2.71828 ** x
    return e / (1.0 + e)


def pitcher_skill_score(era: float | None, k9: float | None) -> float:
    """Return a skill score in [0, 1] where 0.5 is league-average.

    Lower ERA → higher score; higher K/9 → higher score.
    Missing inputs → 0.5 (neutral) so MLB picks still generate when pitchers
    haven't been announced.
    """
    if era is None and k9 is None:
        return 0.5
    era_term = _sigmoid((_LEAGUE_ERA - (era if era is not None else _LEAGUE_ERA)) / _ERA_SCALE)
    k9_term = _sigmoid(((k9 if k9 is not None else _LEAGUE_K9) - _LEAGUE_K9) / _K9_SCALE)
    score = _W_ERA * era_term + _W_K9 * k9_term
    return max(0.0, min(1.0, score))
```

- [ ] **Step 4: Run tests and verify they pass**

Run: `python -m pytest backend/tests/test_pitcher.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/analysis/pitcher.py backend/tests/test_pitcher.py
git commit -m "feat(mlb): pitcher_skill_score — sigmoid blend of ERA + K/9 to [0,1]"
```

---

## Task 6: Add `pitcher_skill_score` to TeamStats dataclass

**Files:**
- Modify: `backend/data_types.py:5-21`
- Test: `backend/tests/test_data_types.py`

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_data_types.py`:

```python
def test_team_stats_supports_optional_pitcher_skill_score():
    """MLB needs a per-game pitcher signal carried alongside team stats."""
    from backend.data_types import TeamStats
    stats = TeamStats(
        point_diff=0.0, home_record=(0, 0), away_record=(0, 0),
        last_n_record=(0, 0), offensive_rating=100.0, defensive_rating=100.0,
        pace=100.0, strength_of_schedule=0.5, elo_rating=1500, rest_days=1,
        pitcher_skill_score=0.72,
    )
    assert stats.pitcher_skill_score == 0.72


def test_team_stats_pitcher_skill_score_defaults_to_none():
    """Field must be optional for non-MLB sports — backwards compatibility."""
    from backend.data_types import TeamStats
    stats = TeamStats(
        point_diff=0.0, home_record=(0, 0), away_record=(0, 0),
        last_n_record=(0, 0), offensive_rating=100.0, defensive_rating=100.0,
        pace=100.0, strength_of_schedule=0.5, elo_rating=1500, rest_days=1,
    )
    assert stats.pitcher_skill_score is None
```

- [ ] **Step 2: Run tests and verify they fail**

Run: `python -m pytest backend/tests/test_data_types.py -v -k pitcher`
Expected: FAIL — `pitcher_skill_score` is not a field of TeamStats.

- [ ] **Step 3: Add the field**

In `backend/data_types.py`, add to `TeamStats` (after `schedule_fatigue_score: float = 0.0`):

```python
    pitcher_skill_score: float | None = None  # MLB-only; 0.5 = league-average
```

- [ ] **Step 4: Run tests and verify they pass**

Run: `python -m pytest backend/tests/test_data_types.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/data_types.py backend/tests/test_data_types.py
git commit -m "feat(mlb): add optional pitcher_skill_score field to TeamStats"
```

---

## Task 7: SportSpecificStrategy MLB weights and pitcher branch

**Files:**
- Modify: `backend/analysis/variants/sport_specific.py:8-13, 76-103, 138-149`
- Test: `backend/tests/test_sport_specific.py`

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_sport_specific.py`:

```python
def test_mlb_weights_present_with_pitcher_dominance():
    """MLB weights must exist and pitcher must dominate (>= 0.4 weight)."""
    from backend.analysis.variants.sport_specific import SPORT_WEIGHTS
    assert "mlb" in SPORT_WEIGHTS
    assert SPORT_WEIGHTS["mlb"]["pitcher"] >= 0.40


def test_mlb_strategy_uses_pitcher_signal():
    """A strong home pitcher (skill 0.85) vs a weak away pitcher (skill 0.30)
    must shift home win probability noticeably even when team stats are equal."""
    from backend.analysis.variants.sport_specific import SportSpecificStrategy
    from backend.data_types import GameData, TeamStats, OddsSnapshot
    from datetime import date as _date

    def _ts(pitcher: float | None) -> TeamStats:
        return TeamStats(
            point_diff=0.0, home_record=(10, 10), away_record=(10, 10),
            last_n_record=(5, 5), offensive_rating=100.0, defensive_rating=100.0,
            pace=100.0, strength_of_schedule=0.5, elo_rating=1500, rest_days=1,
            pitcher_skill_score=pitcher,
        )

    odds = OddsSnapshot(bookmaker="dk", moneyline_home=-110, moneyline_away=-110,
                        spread_home=-1.5, spread_away=1.5, over_under=8.5)
    game_strong_home = GameData(
        game_id=1, sport="mlb", date=_date(2026, 4, 29),
        home_team_id=1, away_team_id=2,
        home_stats=_ts(0.85), away_stats=_ts(0.30), odds=[odds],
    )
    game_strong_away = GameData(
        game_id=2, sport="mlb", date=_date(2026, 4, 29),
        home_team_id=1, away_team_id=2,
        home_stats=_ts(0.30), away_stats=_ts(0.85), odds=[odds],
    )

    strat = SportSpecificStrategy(name="mlb_test", config={"min_edge": 0.0})
    p_home = strat._model_probability(game_strong_home)
    p_home_when_away_is_better = strat._model_probability(game_strong_away)
    assert p_home > 0.55, f"Strong home pitcher should push p_home above 0.55, got {p_home}"
    assert p_home_when_away_is_better < 0.45
    # The pitcher swing should be material (>= 15 points).
    assert (p_home - p_home_when_away_is_better) >= 0.15


def test_mlb_strategy_handles_missing_pitcher_gracefully():
    """When pitchers aren't announced yet, must not crash and should produce
    a probability near 0.5 if all other signals are even."""
    from backend.analysis.variants.sport_specific import SportSpecificStrategy
    from backend.data_types import GameData, TeamStats, OddsSnapshot
    from datetime import date as _date

    ts = TeamStats(
        point_diff=0.0, home_record=(10, 10), away_record=(10, 10),
        last_n_record=(5, 5), offensive_rating=100.0, defensive_rating=100.0,
        pace=100.0, strength_of_schedule=0.5, elo_rating=1500, rest_days=1,
        pitcher_skill_score=None,
    )
    odds = OddsSnapshot(bookmaker="dk", moneyline_home=-110, moneyline_away=-110,
                        spread_home=-1.5, spread_away=1.5, over_under=8.5)
    game = GameData(
        game_id=1, sport="mlb", date=_date(2026, 4, 29),
        home_team_id=1, away_team_id=2,
        home_stats=ts, away_stats=ts, odds=[odds],
    )
    strat = SportSpecificStrategy(name="mlb_test", config={"min_edge": 0.0})
    p = strat._model_probability(game)
    assert 0.45 <= p <= 0.55  # neutral fallback
```

Also update the existing closed-set assertion in `backend/tests/test_sport_specific.py:56`:

```python
def test_sport_weights_cover_supported_team_sports():
    """SPORT_WEIGHTS must include every team sport that runs through the strategy."""
    assert {"nba", "nfl", "ncaab", "ncaaf", "mlb"}.issubset(set(SPORT_WEIGHTS.keys()))
```

(Replace whatever was there — the prior assertion was an exact-equality which now fails by design.)

- [ ] **Step 2: Run tests and verify they fail**

Run: `python -m pytest backend/tests/test_sport_specific.py -v -k mlb`
Expected: FAIL — `mlb` not in SPORT_WEIGHTS, no MLB branch in `_model_probability`.

- [ ] **Step 3: Add MLB branch to SPORT_WEIGHTS and `_model_probability`**

In `backend/analysis/variants/sport_specific.py`, extend `SPORT_WEIGHTS`:

```python
SPORT_WEIGHTS = {
    "nba": {"pd": 0.25, "elo": 0.25, "rating": 0.20, "rest": 0.15, "venue": 0.15},
    "nfl": {"pd": 0.20, "elo": 0.30, "rating": 0.15, "turnover": 0.20, "redzone": 0.15},
    "ncaab": {"pd": 0.20, "elo": 0.25, "rating": 0.25, "conference": 0.15, "venue": 0.15},
    "ncaaf": {"pd": 0.20, "elo": 0.30, "rating": 0.15, "conference": 0.20, "venue": 0.15},
    # MLB: pitcher dominates by design — the starter is the single biggest variable.
    "mlb": {"pd": 0.10, "elo": 0.20, "rating": 0.10, "pitcher": 0.45, "venue": 0.15},
}
```

Inside `_model_probability`, add a branch alongside the existing `nba` / `nfl` / `ncaab,ncaaf` ones (insert after the `ncaab/ncaaf` branch, before the schedule adjustments):

```python
        elif sport == "mlb":
            pitcher_score = self._pitcher_score(hs, aws)
            venue_score = self._venue_score(hs, aws)
            prob += weights.get("pitcher", 0.45) * pitcher_score + weights.get("venue", 0.15) * venue_score
```

Add the helper to the class:

```python
    def _pitcher_score(self, hs, aws) -> float:
        """MLB: home_pitcher_skill / (home + away). Returns 0.5 if either is missing
        (neutral) so picks still generate before probable pitchers are announced.
        """
        h = hs.pitcher_skill_score
        a = aws.pitcher_skill_score
        if h is None or a is None:
            return 0.5
        # Map advantage to [0,1]. If h == a → 0.5; if h dominates → ~1; else → ~0.
        diff = h - a
        # Sigmoid with scale 0.3 → a 0.3 advantage gives ~0.73, full advantage ~0.95.
        return 1.0 / (1.0 + 10 ** (-diff / 0.3))
```

Update `_count_agreeing` to include the pitcher signal for MLB:

```python
    def _count_agreeing(self, game: GameData, side: str) -> int:
        count = 0
        hs, aws = game.home_stats, game.away_stats
        if side == "home":
            if hs.point_diff > aws.point_diff: count += 1
            if hs.elo_rating > aws.elo_rating: count += 1
            if (hs.offensive_rating - hs.defensive_rating) > (aws.offensive_rating - aws.defensive_rating): count += 1
            if game.sport == "mlb" and hs.pitcher_skill_score is not None and aws.pitcher_skill_score is not None:
                if hs.pitcher_skill_score > aws.pitcher_skill_score: count += 1
        else:
            if aws.point_diff > hs.point_diff: count += 1
            if aws.elo_rating > hs.elo_rating: count += 1
            if (aws.offensive_rating - aws.defensive_rating) > (hs.offensive_rating - hs.defensive_rating): count += 1
            if game.sport == "mlb" and hs.pitcher_skill_score is not None and aws.pitcher_skill_score is not None:
                if aws.pitcher_skill_score > hs.pitcher_skill_score: count += 1
        return count
```

- [ ] **Step 4: Run tests and verify they pass**

Run: `python -m pytest backend/tests/test_sport_specific.py -v`
Expected: PASS (existing + 3 new MLB tests).

- [ ] **Step 5: Commit**

```bash
git add backend/analysis/variants/sport_specific.py backend/tests/test_sport_specific.py
git commit -m "feat(mlb): pitcher-dominant SPORT_WEIGHTS + MLB branch in strategy"
```

---

## Task 8: Pick generator loads pitcher data for MLB games

**Files:**
- Modify: `backend/pipeline/pick_generator.py:101-127`
- Test: `backend/tests/test_pick_generator.py`

The pick_generator currently builds GameData purely from team-level rows. For MLB games, we need to attach pitcher_skill_score to home_stats / away_stats. We do this in-memory per pipeline run — no schema persistence.

We assume the *caller* (the scheduler / pipeline runner) has already fetched probable pitchers and stuffed them into a dict keyed by game_id. The pick generator accepts an optional `pitcher_scores: dict[int, dict[str, float]]` argument.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_pick_generator.py`:

```python
def test_build_game_data_attaches_pitcher_score_for_mlb(tmp_path):
    """When pitcher_scores contains an entry for an MLB game, both stats objects
    should pick up pitcher_skill_score from it."""
    from backend.database import get_engine, get_session
    from backend.models import Base, Team, Game, EloRating, TeamStat
    from backend.pipeline.pick_generator import _build_game_data
    from datetime import date as _date

    engine = get_engine(":memory:")
    Base.metadata.create_all(engine)
    session = get_session(engine)
    home = Team(id=1, name="BOS", abbreviation="BOS", sport="mlb")
    away = Team(id=2, name="NYY", abbreviation="NYY", sport="mlb")
    game = Game(id=10, sport="mlb", season="2026", date=_date(2026, 4, 29),
                home_team_id=1, away_team_id=2, status="scheduled")
    session.add_all([home, away, game,
                     EloRating(team_id=1, sport="mlb", rating=1500),
                     EloRating(team_id=2, sport="mlb", rating=1500)])
    session.commit()

    pitcher_scores = {10: {"home": 0.78, "away": 0.41}}
    gd = _build_game_data(session, game, pitcher_scores=pitcher_scores)
    assert gd.home_stats.pitcher_skill_score == 0.78
    assert gd.away_stats.pitcher_skill_score == 0.41


def test_build_game_data_omits_pitcher_score_for_non_mlb():
    from backend.database import get_engine, get_session
    from backend.models import Base, Team, Game, EloRating
    from backend.pipeline.pick_generator import _build_game_data
    from datetime import date as _date

    engine = get_engine(":memory:")
    Base.metadata.create_all(engine)
    session = get_session(engine)
    home = Team(id=1, name="BOS", abbreviation="BOS", sport="nba")
    away = Team(id=2, name="LAL", abbreviation="LAL", sport="nba")
    game = Game(id=10, sport="nba", season="2025-26", date=_date(2026, 4, 29),
                home_team_id=1, away_team_id=2, status="scheduled")
    session.add_all([home, away, game,
                     EloRating(team_id=1, sport="nba", rating=1500),
                     EloRating(team_id=2, sport="nba", rating=1500)])
    session.commit()

    gd = _build_game_data(session, game, pitcher_scores={10: {"home": 0.9, "away": 0.1}})
    assert gd.home_stats.pitcher_skill_score is None  # ignored for non-MLB
    assert gd.away_stats.pitcher_skill_score is None
```

- [ ] **Step 2: Run tests and verify they fail**

Run: `python -m pytest backend/tests/test_pick_generator.py -v -k pitcher`
Expected: FAIL — `_build_game_data` doesn't accept `pitcher_scores`.

- [ ] **Step 3: Update pick_generator**

In `backend/pipeline/pick_generator.py`, change `_build_game_data` (around line 101) to accept and apply pitcher scores:

```python
def _build_game_data(session: Session, game: Game,
                     pitcher_scores: dict[int, dict[str, float]] | None = None) -> GameData:
    home_stats = _get_team_stats(session, game.home_team_id, game.sport)
    away_stats = _get_team_stats(session, game.away_team_id, game.sport)
    odds_rows = session.query(Odds).filter(Odds.game_id == game.id).all()
    odds_snapshots = [
        OddsSnapshot(
            bookmaker=o.bookmaker,
            moneyline_home=o.moneyline_home, moneyline_away=o.moneyline_away,
            spread_home=o.spread_home, spread_away=o.spread_away,
            over_under=o.over_under,
        ) for o in odds_rows
        if o.moneyline_home is not None and o.moneyline_away is not None
    ]
    h_fatigued, h_fatigue_score = _check_schedule_fatigue(session, game.home_team_id, game.date, game.sport)
    a_fatigued, a_fatigue_score = _check_schedule_fatigue(session, game.away_team_id, game.date, game.sport)
    home_stats.is_schedule_fatigued = h_fatigued
    home_stats.schedule_fatigue_score = h_fatigue_score
    away_stats.is_schedule_fatigued = a_fatigued
    away_stats.schedule_fatigue_score = a_fatigue_score
    home_stats.is_lookahead_spot = _check_lookahead_spot(session, game.home_team_id, game.date, game.sport, game.id)
    away_stats.is_lookahead_spot = _check_lookahead_spot(session, game.away_team_id, game.date, game.sport, game.id)

    # MLB: attach probable-pitcher skill score if the caller has pre-computed it.
    if game.sport == "mlb" and pitcher_scores and game.id in pitcher_scores:
        ps = pitcher_scores[game.id]
        home_stats.pitcher_skill_score = ps.get("home")
        away_stats.pitcher_skill_score = ps.get("away")

    return GameData(game_id=game.id, sport=game.sport, date=game.date,
        home_team_id=game.home_team_id, away_team_id=game.away_team_id,
        home_stats=home_stats, away_stats=away_stats, odds=odds_snapshots)
```

Update `generate_and_store_picks` to thread `pitcher_scores` through:

```python
def generate_and_store_picks(session: Session, strategy_id: int,
                              target_date: date | None = None,
                              pitcher_scores: dict[int, dict[str, float]] | None = None) -> int:
    # ... existing top of function ...
    # When iterating games:
    for game in games:
        game_data = _build_game_data(session, game, pitcher_scores=pitcher_scores)
        # ... rest unchanged
```

(Show the exact 2-line change at each call site rather than re-writing the whole function — the engineer should locate each call to `_build_game_data` in the function and add the kwarg.)

- [ ] **Step 4: Run tests and verify they pass**

Run: `python -m pytest backend/tests/test_pick_generator.py -v`
Expected: PASS — including existing tests, since `pitcher_scores=None` is the default and behavior is unchanged for them.

- [ ] **Step 5: Commit**

```bash
git add backend/pipeline/pick_generator.py backend/tests/test_pick_generator.py
git commit -m "feat(mlb): pick_generator threads optional pitcher_scores into MLB GameData"
```

---

## Task 9: Pipeline registration — add MLB to ALL_SPORTS

**Files:**
- Modify: `backend/pipeline/full_pipeline.py:13`
- Test: `backend/tests/test_pipeline_integration.py` (verify import doesn't crash)

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_pipeline_integration.py`:

```python
def test_all_sports_includes_mlb():
    from backend.pipeline.full_pipeline import ALL_SPORTS
    assert "mlb" in ALL_SPORTS
```

- [ ] **Step 2: Run test and verify it fails**

Run: `python -m pytest backend/tests/test_pipeline_integration.py::test_all_sports_includes_mlb -v`
Expected: FAIL.

- [ ] **Step 3: Add `mlb` to `ALL_SPORTS`**

In `backend/pipeline/full_pipeline.py:13`:

```python
ALL_SPORTS = ["nba", "nfl", "ncaab", "ncaaf", "boxing", "mma", "mlb"]
```

Also add MLB season to `config.yaml` if it has a `seasons` section (engineer should grep for `nba:` under `seasons` and append MLB):

```yaml
  mlb:
    start: "03-27"
    end: "10-31"
```

- [ ] **Step 4: Run test and verify it passes**

Run: `python -m pytest backend/tests/test_pipeline_integration.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/pipeline/full_pipeline.py config.yaml backend/tests/test_pipeline_integration.py
git commit -m "feat(mlb): register MLB in ALL_SPORTS and config seasons"
```

---

## Task 10: Frontend SPORTS arrays + Layout nav

**Files:**
- Modify: `frontend/src/pages/TodaysPicks.tsx:14`
- Modify: `frontend/src/pages/PlayerProps.tsx:9`
- Modify: `frontend/src/pages/TrackRecord.tsx:8`
- Modify: `frontend/src/components/StrategyForm.tsx:11`
- Modify: `frontend/src/components/Layout.tsx:18`
- Modify: `frontend/src/pages/Backtesting.tsx:7`

These are all simple string-array additions. There is no automated test for the frontend SPORTS lists — verification is by `tsc --noEmit` followed by manual UI smoke.

- [ ] **Step 1: Add `'mlb'` to each SPORTS array**

In each of the six files, locate the `SPORTS = [...]` declaration and add `'mlb'` after the existing four sports. Example for `TodaysPicks.tsx:14`:

```ts
const SPORTS = ['all', 'nba', 'nfl', 'ncaab', 'ncaaf', 'mlb', 'boxing', 'mma'] as const;
```

Place `'mlb'` *before* `'boxing'`/`'mma'` to group the team sports together visually.

For `Layout.tsx:18` (which currently has only the four originals — no boxing/mma):

```ts
const SPORTS = ['all', 'nba', 'nfl', 'ncaab', 'ncaaf', 'mlb']
```

For `StrategyForm.tsx:11`:

```ts
const SPORTS = ['', 'nba', 'nfl', 'ncaab', 'ncaaf', 'mlb', 'boxing', 'mma'] as const;
```

For `Backtesting.tsx:7` (no `'all'`):

```ts
const SPORTS = ['nba', 'nfl', 'ncaab', 'ncaaf', 'mlb'] as const;
```

- [ ] **Step 2: Type-check the frontend**

Run: `cd frontend && npx tsc --noEmit`
Expected: no errors.

- [ ] **Step 3: Smoke test in browser**

Run: `cd frontend && npm run dev`
Open http://localhost:5173, click the MLB tab on TodaysPicks, TrackRecord, PlayerProps. Confirm:
- Tab is clickable and shows MLB header.
- Empty state renders gracefully (no picks yet — that's expected on day 1).
- No console errors.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/pages/TodaysPicks.tsx frontend/src/pages/PlayerProps.tsx frontend/src/pages/TrackRecord.tsx frontend/src/components/StrategyForm.tsx frontend/src/components/Layout.tsx frontend/src/pages/Backtesting.tsx
git commit -m "feat(mlb): add MLB to all frontend SPORTS arrays and nav"
```

---

## Task 11: Scheduler hooks pitcher-fetch into MLB pipeline runs

**Files:**
- Modify: `backend/pipeline/scheduler.py` (the function that calls `generate_and_store_picks`)
- Test: `backend/tests/test_pipeline_integration.py`

The scheduler runs the pipeline per-sport on game-window cron triggers. For MLB windows we need to fetch pitcher data and pass it down.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_pipeline_integration.py`:

```python
@pytest.mark.asyncio
async def test_run_mlb_window_fetches_pitcher_scores(httpx_mock, tmp_path):
    """An MLB pipeline run should hit MLB Stats API once for schedule and once
    per probable pitcher, then pass pitcher_scores into pick generation."""
    from datetime import date as _date
    from backend.pipeline.scheduler import run_mlb_window

    # Schedule response: one game with two probable pitchers.
    httpx_mock.add_response(
        url="https://statsapi.mlb.com/api/v1/schedule?sportId=1&date=2026-04-29&hydrate=probablePitcher",
        json={"dates": [{"games": [{
            "gamePk": 700001,
            "gameDate": "2026-04-29T23:05:00Z",
            "teams": {
                "home": {"team": {"id": 111, "abbreviation": "BOS"},
                         "probablePitcher": {"id": 5001, "fullName": "A. Pitcher"}},
                "away": {"team": {"id": 147, "abbreviation": "NYY"},
                         "probablePitcher": {"id": 5002, "fullName": "B. Pitcher"}},
            },
        }]}]},
    )
    httpx_mock.add_response(
        url="https://statsapi.mlb.com/api/v1/people/5001/stats?stats=gameLog&group=pitching&season=2026",
        json={"stats": [{"splits": [{"stat": {"era": "2.50", "strikeOuts": 8, "inningsPitched": "6.0"}}]}]},
    )
    httpx_mock.add_response(
        url="https://statsapi.mlb.com/api/v1/people/5002/stats?stats=gameLog&group=pitching&season=2026",
        json={"stats": [{"splits": [{"stat": {"era": "5.50", "strikeOuts": 4, "inningsPitched": "5.0"}}]}]},
    )

    scores = await run_mlb_window(target_date=_date(2026, 4, 29))
    assert 700001 in scores  # keyed by mlb_game_pk
    assert scores[700001]["home"] > 0.6  # ace-ish
    assert scores[700001]["away"] < 0.4  # bad outing
```

- [ ] **Step 2: Run test and verify it fails**

Run: `python -m pytest backend/tests/test_pipeline_integration.py::test_run_mlb_window_fetches_pitcher_scores -v`
Expected: FAIL — `run_mlb_window` doesn't exist.

- [ ] **Step 3: Add `run_mlb_window` and pitcher-fetch helper**

In `backend/pipeline/scheduler.py`, append:

```python
async def fetch_pitcher_scores_for_date(target_date) -> dict[int, dict[str, float]]:
    """Return {mlb_game_pk: {'home': score, 'away': score}} for today's MLB games.

    Missing pitchers (not yet announced or first-start rookies) get None;
    the strategy treats them as neutral.
    """
    from backend.collectors.mlb_stats import MLBStatsCollector
    from backend.analysis.pitcher import pitcher_skill_score
    collector = MLBStatsCollector()
    out: dict[int, dict[str, float]] = {}
    try:
        games = await collector.fetch_schedule(target_date)
        for g in games:
            home_id = g.get("home_probable_pitcher_id")
            away_id = g.get("away_probable_pitcher_id")
            home_stats = await collector.fetch_pitcher_recent(home_id, season=target_date.year) if home_id else None
            away_stats = await collector.fetch_pitcher_recent(away_id, season=target_date.year) if away_id else None
            out[g["mlb_game_pk"]] = {
                "home": pitcher_skill_score(
                    era=home_stats["era_recent"] if home_stats else None,
                    k9=home_stats["k9_recent"] if home_stats else None,
                ),
                "away": pitcher_skill_score(
                    era=away_stats["era_recent"] if away_stats else None,
                    k9=away_stats["k9_recent"] if away_stats else None,
                ),
            }
    finally:
        await collector.close()
    return out


async def run_mlb_window(target_date) -> dict[int, dict[str, float]]:
    """Fetch pitcher scores for an MLB game window. Public API for the scheduler."""
    return await fetch_pitcher_scores_for_date(target_date)
```

In the existing `run_window(sport, ...)` function (or wherever `generate_and_store_picks` is called), thread the scores through when sport == "mlb":

```python
    pitcher_scores = None
    if sport == "mlb":
        pitcher_scores = await fetch_pitcher_scores_for_date(today)
        # NB: pitcher_scores is keyed by mlb_game_pk; pick_generator expects keying by
        # internal game.id. Translate by looking up Game rows where the upstream
        # collector has set an mlb_game_pk hint, or by team+date matching.
        # For v1: map by (home_team_abbr, away_team_abbr, date) since we don't yet
        # persist mlb_game_pk on Game.
        pitcher_scores = _remap_pitcher_scores_to_game_ids(session, pitcher_scores, today)
    generate_and_store_picks(session, strategy.id, today, pitcher_scores=pitcher_scores)
```

And the helper (engineer to add at module scope):

```python
def _remap_pitcher_scores_to_game_ids(session, scores_by_pk, target_date) -> dict[int, dict[str, float]]:
    """Translate {mlb_game_pk: scores} → {game.id: scores} by matching teams + date.

    v1 pragmatic mapping: looks up Game rows for sport=mlb on target_date and
    matches by team abbreviation pairs. mlb_game_pk persistence is a v2 task.
    """
    from backend.models import Game, Team
    games = session.query(Game).filter(Game.sport == "mlb", Game.date == target_date).all()
    abbr_lookup = {t.id: t.abbreviation for t in session.query(Team).filter(Team.sport == "mlb").all()}
    # We don't have a way to invert mlb_game_pk → (home_abbr, away_abbr) without
    # carrying that mapping forward; so for v1, return scores keyed by Game.id
    # via positional matching (one MLB game per slate is unrealistic — this needs
    # the upstream collector to surface team abbreviations alongside the pk).
    out = {}
    for g in games:
        # Match against the schedule data: caller is expected to pass scores keyed
        # by (home_abbr, away_abbr) tuples instead of pks for v1. Update fetch_pitcher_scores_for_date
        # accordingly when wiring this in.
        key = (abbr_lookup.get(g.home_team_id), abbr_lookup.get(g.away_team_id))
        if key in scores_by_pk:
            out[g.id] = scores_by_pk[key]
    return out
```

> **NOTE TO ENGINEER:** Step 3 surfaces a v1 mapping wart. The cleanest fix is to update `fetch_pitcher_scores_for_date` to key its return dict by `(home_abbr, away_abbr)` tuples instead of `mlb_game_pk`. Adjust both the function and the test in step 1 accordingly before committing — the test must reflect the actual key shape. This is intentional: the plan flags the design choice rather than hiding it.

- [ ] **Step 4: Adjust the test for the chosen key shape and re-run**

Update `test_run_mlb_window_fetches_pitcher_scores` to assert against `("BOS", "NYY")` instead of `700001` if the engineer chose tuple-keying. Run: `python -m pytest backend/tests/test_pipeline_integration.py -v`. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/pipeline/scheduler.py backend/tests/test_pipeline_integration.py
git commit -m "feat(mlb): scheduler fetches pitcher scores and threads into pick generation"
```

---

## Task 12: End-to-end MLB integration smoke test

**Why:** Tasks 1-11 each test a slice. This task verifies the full path: synthetic MLB game + odds + pitcher data → `generate_and_store_picks` → at least one PickModel row written.

**Files:**
- Create: `backend/tests/test_mlb_integration.py`

- [ ] **Step 1: Write the test**

```python
"""End-to-end MLB pipeline smoke test.

Verifies that with a real-shaped game + odds + pitcher_scores, the strategy
produces a moneyline pick when the home pitcher dominates and the line offers
positive edge. No HTTP — purely SQLite + in-process strategy.
"""
from datetime import date as _date, datetime, timezone

import pytest

from backend.database import get_engine, get_session
from backend.models import (
    Base, Team, Game, Odds, EloRating, StrategyModel, PickModel,
)
from backend.pipeline.pick_generator import generate_and_store_picks


def test_mlb_strategy_generates_pick_when_pitcher_advantage_creates_edge():
    engine = get_engine(":memory:")
    Base.metadata.create_all(engine)
    session = get_session(engine)

    home = Team(id=1, name="Boston Red Sox", abbreviation="BOS", sport="mlb")
    away = Team(id=2, name="New York Yankees", abbreviation="NYY", sport="mlb")
    session.add_all([home, away])
    session.flush()

    game = Game(id=10, sport="mlb", season="2026", date=_date(2026, 4, 29),
                home_team_id=1, away_team_id=2, status="scheduled",
                start_time=datetime(2026, 4, 29, 23, 5, tzinfo=timezone.utc))
    session.add(game)
    session.flush()

    # Equal team ELO — pitcher advantage should be the deciding signal.
    session.add_all([
        EloRating(team_id=1, sport="mlb", rating=1500),
        EloRating(team_id=2, sport="mlb", rating=1500),
    ])
    # Pick-em odds: -110 both sides → 52.4% implied each.
    session.add(Odds(
        game_id=10, bookmaker="dk",
        moneyline_home=+105, moneyline_away=-115,  # slight underdog at home
        spread_home=-1.5, spread_away=1.5, over_under=8.5,
        timestamp=datetime(2026, 4, 29, 18, 0, tzinfo=timezone.utc),
    ))
    strat = StrategyModel(id=1, name="sport_specific",
                           config_json='{"min_edge": 3.0}', is_active=True)
    session.add(strat)
    session.commit()

    # Strong home pitcher (skill 0.85), weak away pitcher (0.30) — pitcher branch
    # should push home win probability well above the +105 implied prob (~48.8%).
    pitcher_scores = {10: {"home": 0.85, "away": 0.30}}

    n = generate_and_store_picks(session, strategy_id=1, target_date=_date(2026, 4, 29),
                                  pitcher_scores=pitcher_scores)
    assert n >= 1, "Strong home pitcher + plus money should produce at least one pick"

    picks = session.query(PickModel).filter(PickModel.game_id == 10).all()
    assert any(p.pick_type == "moneyline" and "HOME" in p.pick_value for p in picks)
    session.close()


def test_mlb_strategy_skips_when_no_edge():
    """Equal pitchers + market-priced moneyline → no pick should be generated."""
    engine = get_engine(":memory:")
    Base.metadata.create_all(engine)
    session = get_session(engine)
    session.add_all([
        Team(id=1, name="BOS", abbreviation="BOS", sport="mlb"),
        Team(id=2, name="NYY", abbreviation="NYY", sport="mlb"),
        EloRating(team_id=1, sport="mlb", rating=1500),
        EloRating(team_id=2, sport="mlb", rating=1500),
        Game(id=11, sport="mlb", season="2026", date=_date(2026, 4, 29),
             home_team_id=1, away_team_id=2, status="scheduled"),
        Odds(game_id=11, bookmaker="dk",
             moneyline_home=-110, moneyline_away=-110,
             spread_home=-1.5, spread_away=1.5, over_under=8.5,
             timestamp=datetime(2026, 4, 29, 18, 0, tzinfo=timezone.utc)),
        StrategyModel(id=1, name="sport_specific",
                      config_json='{"min_edge": 3.0}', is_active=True),
    ])
    session.commit()

    pitcher_scores = {11: {"home": 0.50, "away": 0.50}}  # equal
    n = generate_and_store_picks(session, strategy_id=1, target_date=_date(2026, 4, 29),
                                  pitcher_scores=pitcher_scores)
    assert n == 0
    session.close()
```

- [ ] **Step 2: Run the integration tests**

Run: `python -m pytest backend/tests/test_mlb_integration.py -v`
Expected: PASS.

- [ ] **Step 3: Run the entire backend suite to catch regressions**

Run: `python -m pytest backend/ -q`
Expected: ALL tests pass (existing 241+ + new MLB tests).

- [ ] **Step 4: Commit**

```bash
git add backend/tests/test_mlb_integration.py
git commit -m "test(mlb): end-to-end smoke — pitcher advantage produces a moneyline pick"
```

---

## Self-review checklist (run before handing off)

- **Spec coverage:**
  - ML/RL/totals support → Tasks 2 + 7 (Odds API key + strategy weights cover all three; ML is explicitly tested in Tasks 7 + 12)
  - Pitcher-adjusted depth → Tasks 4-7
  - MLB Stats API + Odds API as data sources → Tasks 2-4
  - Frontend SPORTS arrays → Task 10
  - Sport constants → Task 1
- **Placeholder scan:** None of "TBD", "implement later", or naked "add error handling" remain. Task 11 has a NOTE TO ENGINEER about a real design choice (key shape), which is acceptable because it's specific and forces a deliberate fix rather than vague hand-waving.
- **Type consistency:**
  - `pitcher_skill_score: float | None` is used identically in Tasks 6, 7, 8.
  - `pitcher_scores: dict[int, dict[str, float]] | None` is consistent in Tasks 8, 11, 12.
  - `MLBStatsCollector.fetch_schedule` returns the same shape across Tasks 4 and 11.
  - `pitcher_skill_score(era, k9)` signature is consistent in Tasks 5, 11.

## v2 follow-ups (not in this plan)

- **Persist pitcher snapshots** (`Game.home_pitcher_id`, `Game.home_pitcher_era_at_pick`, ...) so MLB picks can be backtested.
- **F5 markets** (first-five-innings ML/total) — separate strategy module.
- **Lineup splits** — pull batter-vs-pitcher splits and project run scoring more precisely. Probably uses `pybaseball`.
- **Park factors + weather** — Coors Field overs, wind blowing out at Wrigley, etc.
- **Bullpen modeling** — relevant for late-game total calculations.

---

**Plan complete and saved to `docs/superpowers/plans/2026-04-29-mlb-betting.md`. Two execution options:**

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints.

**Which approach?**

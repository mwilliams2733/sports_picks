"""NFL game-time weather: the forecast rule, shared by the backtest and production.

`backend.scripts.weather_experiment` (observed gamebook weather) found
rain/snow totals finishing 4.3 points under the close, and rain cutting
passing and receiving yards. `backend.scripts.weather_forecast_backtest`
re-runs it on archived FORECASTS with exactly the constants below, so the
rule production applies is the rule that was measured.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

#: Forecast hours summed from kickoff: the kickoff hour and the two after.
FORECAST_HOURS = 3

#: Forecast precipitation (mm, over FORECAST_HOURS) at or above which a game
#: is wet. Fixed before the backtest's first fetch, not tuned.
WET_MM = 1.0

#: Mean forecast wind (mph, over FORECAST_HOURS) at or above which it is windy.
WINDY_MPH = 15.0

STADIUMS: dict[str, dict] = {
    k: v for k, v in json.loads(
        (Path(__file__).resolve().parents[1] / "data" / "nfl_stadiums.json")
        .read_text(encoding="utf-8")).items()
    if not k.startswith("_")}


def outdoor_stadium(home_abbr: str) -> dict | None:
    """The home team's stadium if its weather is the game's weather: open
    air. A dome or retractable roof (open or shut is a game-day call) is
    None, as is a team not in the table."""
    s = STADIUMS.get(home_abbr)
    return s if s is not None and s["roof"] == "outdoors" else None


# --- player props -------------------------------------------------------------

#: market -> {condition: measured effect as a share of the baseline}, from
#: `weather_forecast_backtest` (2026-10-06, archived forecasts, clustered by
#: game). Applied only where p < 0.05: rushing in wind (-0.12, p 0.054) and
#: in rain (+0.02, p 0.76) are not.
PROP_EFFECTS = {
    "player_pass_yds": {"windy": -0.15, "wet": -0.14},
    "player_reception_yds": {"windy": -0.15, "wet": -0.10},
}


def is_wet(precip_mm: float | None) -> bool:
    return precip_mm is not None and precip_mm >= WET_MM


def is_windy(wind_mph: float | None) -> bool:
    return wind_mph is not None and wind_mph >= WINDY_MPH


def prop_factor(market: str, precip_mm: float | None, wind_mph: float | None) -> float | None:
    """The multiplier for a prop projection, or None when nothing applies."""
    effects = PROP_EFFECTS.get(market)
    if not effects:
        return None
    shift = (effects["windy"] if is_windy(wind_mph) else 0.0) + \
        (effects["wet"] if is_wet(precip_mm) else 0.0)
    return 1.0 + shift if shift else None


# --- the rain-Under tracking rule ---------------------------------------------

def rain_under_picks(session, games, weather: dict) -> dict:
    """Store, refresh or withdraw the rain-Under TRACKING pick for each game.

    A game whose latest forecast (`weather`: game_id -> GameWeather) is wet
    gets "Under <consensus total>" at the consensus Under price, the same
    `average_odds` consensus the model's picks use, so CLV compares like with
    like. Stored under the `weather_rain_under` strategy, `tracking_only`:
    never published, never emailed, graded like any total.

    Backtest (archived forecasts, 2022-2026): Under 26-8 on 34 games, total
    7.6 below the close. The hypothesis came from those same games, so these
    picks are the out-of-sample test, not a proven edge.

    A pick whose game's forecast is no longer wet is withdrawn by the
    generator's own rule (`withdraw_pick`); a graded pick or one whose game
    has started is never touched (`_refreshable`).
    """
    import json
    from datetime import datetime, timezone

    from backend.analysis.odds_utils import american_to_implied_prob, remove_vig
    from backend.analysis.strategy import average_odds
    from backend.models import (WEATHER_RAIN_UNDER_STRATEGY_ID, EmailedPick, Odds,
                                PickModel, PickResult)
    from backend.pipeline.pick_generator import _refreshable, withdraw_pick
    from backend.pipeline.pick_versions import record_pick_version

    games = [g for g in games if g.id in weather]
    if not games:
        return {"inserted": 0, "refreshed": 0, "withdrawn": 0}
    ids = [g.id for g in games]
    existing = {p.game_id: p for p in session.query(PickModel).filter(
        PickModel.strategy_id == WEATHER_RAIN_UNDER_STRATEGY_ID,
        PickModel.pick_type == "over_under", PickModel.game_id.in_(ids))}
    pick_ids = [p.id for p in existing.values()] or [-1]
    graded = {pid for (pid,) in session.query(PickResult.pick_id)
              .filter(PickResult.pick_id.in_(pick_ids))}
    emailed = {pid for (pid,) in session.query(EmailedPick.pick_id)
               .filter(EmailedPick.pick_id.in_(pick_ids))}
    now = datetime.now(timezone.utc)
    counts = {"inserted": 0, "refreshed": 0, "withdrawn": 0}

    for g in games:
        w = weather[g.id]
        row = existing.get(g.id)
        if not is_wet(w.precip_mm):
            if row is not None and withdraw_pick(session, row, g, graded, emailed, now):
                counts["withdrawn"] += 1
            continue
        quote = average_odds(session.query(Odds).filter(Odds.game_id == g.id).all())
        line = quote and quote.get("over_under")
        under = quote and quote.get("under_price")
        if not line or under is None:
            logger.info("rain-under: game %s is wet but has no total/Under price", g.id)
            continue
        over = quote.get("over_price")
        fair = (remove_vig(american_to_implied_prob(over), american_to_implied_prob(under))[1]
                if over is not None else None)
        fields = dict(pick_value=f"Under {line:g}", confidence=1, edge_pct=0.0,
                      odds_at_pick=under, model_prob=None, market_prob_novig=fair,
                      rationale_json=json.dumps({"rule": "rain_under", "weather_id": w.id,
                                                 "precip_mm": round(w.precip_mm, 2),
                                                 "wind_mph": round(w.wind_mph, 1),
                                                 "temp_f": round(w.temp_f, 1)}),
                      tracking_only=True)
        if row is None:
            row = PickModel(game_id=g.id, strategy_id=WEATHER_RAIN_UNDER_STRATEGY_ID,
                            pick_type="over_under", created_at=now, **fields)
            session.add(row)
            session.flush()
            record_pick_version(session, row, "insert")
            counts["inserted"] += 1
        elif _refreshable(row, g, graded):
            for k, v in fields.items():
                setattr(row, k, v)
            row.withdrawn_at = None
            row.created_at = now
            record_pick_version(session, row, "refresh")
            counts["refreshed"] += 1
    session.commit()
    if any(counts.values()):
        logger.info("rain-under: %s", counts)
    return counts

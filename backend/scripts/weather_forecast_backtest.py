"""The weather experiment again, with FORECASTS instead of observed weather.

Why this exists
---------------
`weather_experiment` found rain/snow totals finishing 4.3 points under the
close, but it used the gamebook's observed kickoff weather. A bettor sees a
forecast. Open-Meteo's historical-forecast archive keeps what its models
forecast for each hour back to 2022, so the bettable rule can be tested on
the same games instead of waited for.

The archive stitches together the first hours of successive model runs: a
short-range forecast, about what the live feed sees at a game's window (2h
before kickoff). Precipitation probability is empty in the archive, so the
rule uses precipitation amount only.

The rule, fixed before the first fetch (2026-10-06):

* **wet** = forecast precipitation >= 1.0 mm summed over the kickoff hour and
  the two after it. 0.5 and 2.0 mm are printed as sensitivity, not as
  alternatives to choose from.
* **windy** = mean forecast wind over the same hours >= 15 mph.
* Outdoor = the home team's stadium in `backend/data/nfl_stadiums.json` is
  `outdoors`; retractable roofs and neutral sites are skipped -- the same
  rule production uses.

Each game's forecast becomes a `weather_experiment.Weather`, and the tests
are that module's own (`report_totals`, `prop_rows`, `report_props`), so the
observed and forecast runs differ only in where the weather came from.

Fetches once into --cache-dir (about 100 requests, one per outdoor stadium
per season), then reads the cache. Writes nothing else.

    python -m backend.scripts.weather_forecast_backtest --pbp-dir pbp --cache-dir fc
"""
from __future__ import annotations

import argparse
import glob
import json
import logging
import os
from datetime import date, datetime, timedelta

from backend.analysis.football_weather import (FORECAST_HOURS, STADIUMS,
                                               WET_MM, WINDY_MPH)
from backend.scripts.import_nflverse_history import ABBR_FIXUPS
from backend.scripts.injury_experiment import (PBP_COLUMNS,
                                               team_games_from_frame)
from backend.scripts.weather_experiment import (WEATHER_COLUMNS, Weather,
                                                report_props, report_totals,
                                                prop_rows, weather_by_game)

logger = logging.getLogger(__name__)

ARCHIVE_URL = "https://historical-forecast-api.open-meteo.com/v1/forecast"
HOURLY = "temperature_2m,precipitation,wind_speed_10m"
SENSITIVITY_MM = (0.5, WET_MM, 2.0)
GAME_COLUMNS = ["game_id", "season", "season_type", "game_date", "home_team",
                "start_time", "location"]


def kickoff_et(game_date: str, start_time) -> datetime | None:
    """Kickoff in ET from nflverse's `start_time` ("9/7/25, 13:02:38")."""
    if not isinstance(start_time, str) or "," not in start_time:
        return None
    clock = start_time.split(",")[1].strip()
    try:
        h, m, _ = (int(x) for x in clock.split(":"))
    except ValueError:
        return None
    return datetime.combine(date.fromisoformat(game_date), datetime.min.time()) + \
        timedelta(hours=h, minutes=m)


def fetch_season(cache_dir: str, team: str, season: int, start: date, end: date) -> dict:
    """{"YYYY-MM-DDTHH:00": (temp_f, precip_mm, wind_mph)} for a stadium, cached."""
    path = os.path.join(cache_dir, f"{team}_{season}.json")
    if not os.path.exists(path):
        import httpx
        s = STADIUMS[team]
        resp = httpx.get(ARCHIVE_URL, timeout=120, params=dict(
            latitude=s["lat"], longitude=s["lon"], start_date=start.isoformat(),
            end_date=end.isoformat(), hourly=HOURLY, temperature_unit="fahrenheit",
            wind_speed_unit="mph", timezone="America/New_York"))
        resp.raise_for_status()
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(resp.json(), fh)
    with open(path, encoding="utf-8") as fh:
        h = json.load(fh)["hourly"]
    return {t: (tt, p, w) for t, tt, p, w in zip(h["time"], h["temperature_2m"],
                                                h["precipitation"], h["wind_speed_10m"])}


def game_forecasts(frame, cache_dir: str) -> dict[str, tuple[float, float, float]]:
    """game_id -> (kickoff temp F, precip mm over FORECAST_HOURS, mean wind mph)."""
    g = frame[(frame.season_type == "REG") & (frame.location == "Home")].drop_duplicates("game_id")
    g = g.assign(team=g.home_team.map(lambda t: ABBR_FIXUPS.get(t, t)))
    g = g[g.team.map(lambda t: STADIUMS.get(t, {}).get("roof") == "outdoors")]
    os.makedirs(cache_dir, exist_ok=True)
    out = {}
    for (team, season), grp in g.groupby(["team", "season"]):
        days = [date.fromisoformat(d) for d in grp.game_date]
        hourly = fetch_season(cache_dir, team, int(season), min(days), max(days) + timedelta(days=1))
        for r in grp.itertuples(index=False):
            ko = kickoff_et(r.game_date, r.start_time)
            if ko is None:
                continue
            hours = [hourly.get((ko.replace(minute=0) + timedelta(hours=i)).strftime("%Y-%m-%dT%H:00"))
                     for i in range(FORECAST_HOURS)]
            if any(x is None or None in x for x in hours):
                continue
            out[r.game_id] = (hours[0][0], sum(x[1] for x in hours),
                              sum(x[2] for x in hours) / len(hours))
    return out


def as_weather(fc, wet_mm: float) -> dict[str, Weather]:
    return {gid: Weather("outdoors", wind, temp, precip >= wet_mm, True)
            for gid, (temp, precip, wind) in fc.items()}


def main(argv=None) -> int:
    import pandas as pd
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--pbp-dir", required=True)
    ap.add_argument("--cache-dir", required=True)
    args = ap.parse_args(argv)
    pbp = sorted(glob.glob(os.path.join(args.pbp_dir, "play_by_play_*.csv.gz")))
    cols = sorted(set(PBP_COLUMNS) | set(WEATHER_COLUMNS) | set(GAME_COLUMNS))
    frame = pd.concat([pd.read_csv(p, usecols=cols, low_memory=False) for p in pbp])
    frame = frame[frame.season_type == "REG"]
    team_games, lines = team_games_from_frame(frame)
    fc = game_forecasts(frame, args.cache_dir)
    observed = weather_by_game(frame)

    sep = "-" * 72
    print("=" * 72)
    print("WEATHER FORECAST BACKTEST  (Open-Meteo archived forecasts, NFL 2022-2026)")
    print("=" * 72)
    agree = [(fc[g][1] >= WET_MM, observed[g].precip) for g in fc if g in observed]
    both = sum(a and b for a, b in agree)
    print(f"  {len(fc)} outdoor games with a forecast. Forecast wet (>= {WET_MM} mm): "
          f"{sum(a for a, _ in agree)}; gamebook rain/snow: {sum(b for _, b in agree)}; both: {both}")
    for mm in SENSITIVITY_MM:
        tag = "PRIMARY" if mm == WET_MM else "sensitivity"
        print("\n".join([sep, f"A. TOTALS, wet = forecast >= {mm} mm  ({tag})", sep]
                        + report_totals(lines, as_weather(fc, mm), column_only=False)))
    print("\n".join([sep, f"B. PROPS, wet >= {WET_MM} mm, windy >= {WINDY_MPH:.0f} mph", sep]
                    + report_props(prop_rows(team_games, as_weather(fc, WET_MM)))))
    print("\n  'wind' and 'cold' rows above use forecast wind/temperature too.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

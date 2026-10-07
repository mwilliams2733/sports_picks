"""Does NFL weather tell us anything the closing line does not?

Why this exists
---------------
Second item on the owner's criteria backlog (`docs/criteria-backlog.md`,
2026-10-06). The model has no weather input and no collector. Before
building one, three tests, fixed before the first run:

**A. Totals.** Outdoor and open-roof regular-season games, 2022-2026:

    total = a + b*total_line + c*feature

one regression per feature: wind (mph), wind >= 15 mph, cold (<= 32 F),
precipitation (rain or snow in the gamebook text). `c` is the points the
close left over; 0 means the market priced the weather. Plus the Under
record in windy, cold and wet games against -110 break-even.

**B. Props (projection).** Per player-game with a walk-forward baseline
(prior games this season, MIN_PRIOR games, over the market floor):

    actual = a + b*base + c1*base*wind15 + c2*base*precip

for pass, receiving and rush yards. Clustered by game.

**C. Sides.** Is the "dome team on the road in the cold" priced? Away team
plays home games under a roof (most of its home games that season were
`dome`/`closed`), game at <= 35 F outdoors:

    margin = a + b*spread_line + c*(dome_away_in_cold)

Data and its limits
-------------------
nflverse play-by-play carries `temp`, `wind` and a gamebook `weather` text.
The columns are missing for about half of 2022 outdoor games; the text then
supplies them. Where both exist they disagree on 18% of games (median 1
mph, up to 16), so the policy is fixed: column first, text as fallback, and
a column-only rerun as a robustness check.

These are OBSERVED kickoff conditions, not forecasts. The market only saw
the forecast. If observed weather shows no residual, forecasts cannot; if it
shows one, the bettable effect is smaller. Measurement noise also biases
every `c` toward zero, so a null here is "no detectable edge", not "weather
does nothing".

Writes nothing, needs no database. Fetch play-by-play as in
`epa_experiment`'s docstring, then:

    python -m backend.scripts.weather_experiment --pbp-dir pbp
"""
from __future__ import annotations

import argparse
import glob
import logging
import math
import os
import re
from collections import defaultdict
from dataclasses import dataclass

from backend.scripts.injury_experiment import (PBP_COLUMNS, binom_p,
                                               clustered_ols,
                                               team_games_from_frame)

logger = logging.getLogger(__name__)

OUTDOOR_ROOFS = frozenset({"outdoors", "open"})
ROOFED = frozenset({"dome", "closed"})
WINDY_MPH = 15.0
COLD_F = 32.0
DOME_COLD_F = 35.0
MIN_PRIOR = 3
#: market -> (TeamGame stat, baseline floor). Floors as prop_matchup_experiment.
PROP_MARKETS = {
    "player_pass_yds": ("pass_yards", 120.0),
    "player_reception_yds": ("rec_yards", 20.0),
    "player_rush_yds": ("rush_yards", 25.0),
}
WEATHER_COLUMNS = ["game_id", "roof", "temp", "wind", "weather"]

_WIND = re.compile(r"Wind:\s*(?:[A-Za-z]+\s+)?(\d+)\s*mph", re.I)
_CALM = re.compile(r"Wind:\s*calm", re.I)
_TEMP = re.compile(r"Temp:\s*(-?\d+)", re.I)
_PRECIP = re.compile(r"\b(rain|snow|showers|drizzle|sleet|flurries)", re.I)


def parse_wind(text) -> float | None:
    if not isinstance(text, str):
        return None
    m = _WIND.search(text)
    if m:
        return float(m.group(1))
    return 0.0 if _CALM.search(text) else None


def parse_temp(text) -> float | None:
    if not isinstance(text, str):
        return None
    m = _TEMP.search(text)
    return float(m.group(1)) if m else None


def has_precip(text) -> bool:
    """Rain or snow named in the gamebook text. "No rain" style negations
    were not seen in the data; a forecast phrase like "rain later" would
    count, which errs toward calling a game wet."""
    return isinstance(text, str) and bool(_PRECIP.search(text))


def _num(v) -> float | None:
    return None if v is None or (isinstance(v, float) and math.isnan(v)) else float(v)


@dataclass(frozen=True)
class Weather:
    roof: str
    wind: float | None
    temp: float | None
    precip: bool
    from_column: bool   # both wind and temp came from the columns

    @property
    def outdoor(self) -> bool:
        return self.roof in OUTDOOR_ROOFS


def weather_by_game(frame) -> dict[str, Weather]:
    """game_id -> Weather. Column first, gamebook text as the fallback."""
    out = {}
    for r in frame.drop_duplicates("game_id").itertuples(index=False):
        wind_c, temp_c = _num(r.wind), _num(r.temp)
        wind = wind_c if wind_c is not None else parse_wind(r.weather)
        temp = temp_c if temp_c is not None else parse_temp(r.weather)
        out[r.game_id] = Weather(str(r.roof), wind, temp, has_precip(r.weather),
                                 wind_c is not None and temp_c is not None)
    return out


def dome_teams(frame) -> set[tuple[int, str]]:
    """(season, team) whose home games that season were mostly roofed."""
    g = frame[frame.season_type == "REG"].drop_duplicates("game_id")
    counts = defaultdict(lambda: [0, 0])
    for r in g.itertuples(index=False):
        c = counts[(int(r.season), r.home_team)]
        c[0] += r.roof in ROOFED
        c[1] += 1
    return {k for k, (roofed, n) in counts.items() if roofed * 2 > n}


#: Totals features: name -> (label, value from Weather, or None to skip the game)
TOTAL_FEATURES = {
    "wind": ("wind, per mph", lambda w: w.wind),
    "windy": (f"wind >= {WINDY_MPH:.0f} mph", lambda w: None if w.wind is None
              else float(w.wind >= WINDY_MPH)),
    "cold": (f"cold, <= {COLD_F:.0f} F", lambda w: None if w.temp is None
             else float(w.temp <= COLD_F)),
    "precip": ("rain or snow", lambda w: float(w.precip)),
}


def report_totals(lines, weather, *, column_only: bool) -> list[str]:
    games = [(gl, weather[gl.game_id]) for gl in lines
             if gl.game_id in weather and weather[gl.game_id].outdoor
             and (weather[gl.game_id].from_column or not column_only)]
    out = [f"  {len(games)} outdoor/open games"
           + (" (column-only rows)" if column_only else "")]
    for name, (label, fn) in TOTAL_FEATURES.items():
        rows = [(gl, fn(w)) for gl, w in games if fn(w) is not None]
        beta, se, p, _ = clustered_ols([[gl.total_line for gl, _ in rows],
                                        [v for _, v in rows]],
                                       [gl.total for gl, _ in rows],
                                       [gl.game_id for gl, _ in rows])
        treated = sum(1 for _, v in rows if v) if name != "wind" else len(rows)
        out.append(f"    {label:<18} n {treated:>4}: c = {beta[2]:+.2f} pts "
                   f"(95% CI {beta[2] - 1.96 * se[2]:+.2f}..{beta[2] + 1.96 * se[2]:+.2f}),"
                   f" p = {p[2]:.2f}")
        if name != "wind" and not column_only:
            w = l = push = 0
            for gl, v in rows:
                if not v:
                    continue
                if gl.total == gl.total_line:
                    push += 1
                elif gl.total < gl.total_line:
                    w += 1
                else:
                    l += 1
            d = w + l
            rate = f"{w / d:.3f}" if d else "n/a"
            out.append(f"      Under {w}-{l}-{push}  {rate}  {w * (100 / 110) - l:+.1f}u  "
                       f"p vs 52.4% = {binom_p(w, d):.2f}")
    return out


def prop_rows(team_games, weather):
    """(market, actual, baseline, windy, precip, game_id), outdoor games only
    for the effect; every game counts toward the baseline."""
    by_team = defaultdict(list)
    for tg in team_games:
        by_team[(tg.season, tg.team)].append(tg)
    rows = []
    for games in by_team.values():
        games.sort(key=lambda g: g.week)
        hist: dict[tuple[str, str], list[float]] = defaultdict(list)
        for g in games:
            w = weather.get(g.game_id)
            players = set(g.attempts) | g.players()
            if w is not None and w.outdoor and w.wind is not None:
                for market, (stat, floor) in PROP_MARKETS.items():
                    for pid in players:
                        prior = hist[(market, pid)]
                        if len(prior) >= MIN_PRIOR and sum(prior) / len(prior) >= floor:
                            rows.append((market, float(getattr(g, stat)[pid]),
                                         sum(prior) / len(prior),
                                         w.wind >= WINDY_MPH, w.precip, g.game_id))
            # After this game's rows are built, so a baseline never includes it.
            for market, (stat, _) in PROP_MARKETS.items():
                for pid in players:
                    hist[(market, pid)].append(float(getattr(g, stat)[pid]))
    return rows


def report_props(rows) -> list[str]:
    out = []
    for market in PROP_MARKETS:
        rs = [r for r in rows if r[0] == market]
        base = [r[2] for r in rs]
        beta, se, p, g = clustered_ols(
            [base, [r[2] * r[3] for r in rs], [r[2] * r[4] for r in rs]],
            [r[1] for r in rs], [r[5] for r in rs])
        out += [f"  {market}: {len(rs)} player-games in {g} outdoor games",
                f"    wind >= {WINDY_MPH:.0f} (n {sum(r[3] for r in rs)}): c1 = {beta[2]:+.3f} x baseline "
                f"(95% CI {beta[2] - 1.96 * se[2]:+.3f}..{beta[2] + 1.96 * se[2]:+.3f}), p = {p[2]:.2g}",
                f"    rain/snow (n {sum(r[4] for r in rs)}): c2 = {beta[3]:+.3f} x baseline "
                f"(95% CI {beta[3] - 1.96 * se[3]:+.3f}..{beta[3] + 1.96 * se[3]:+.3f}), p = {p[3]:.2g}"]
    return out


def report_sides(lines, weather, domes) -> list[str]:
    rows = []
    for gl in lines:
        w = weather.get(gl.game_id)
        if w is None:
            continue
        treated = (w.outdoor and w.temp is not None and w.temp <= DOME_COLD_F
                   and (gl.season, gl.away) in domes and (gl.season, gl.home) not in domes)
        rows.append((gl, float(treated)))
    beta, se, p, _ = clustered_ols([[gl.spread_line for gl, _ in rows], [t for _, t in rows]],
                                   [gl.margin for gl, _ in rows],
                                   [gl.game_id for gl, _ in rows])
    w = l = push = 0
    for gl, t in rows:
        if not t:
            continue
        cover = gl.margin - gl.spread_line   # > 0: home covered, i.e. fade the dome team
        if cover == 0:
            push += 1
        elif cover > 0:
            w += 1
        else:
            l += 1
    d = w + l
    rate = f"{w / d:.3f}" if d else "n/a"
    return [f"  {len(rows)} games; dome team away at <= {DOME_COLD_F:.0f} F outdoors: "
            f"n {int(sum(t for _, t in rows))}",
            f"    c = {beta[2]:+.2f} pts to the home side (95% CI {beta[2] - 1.96 * se[2]:+.2f}"
            f"..{beta[2] + 1.96 * se[2]:+.2f}), p = {p[2]:.2f}",
            f"    ATS fading the dome team: {w}-{l}-{push}  {rate}  "
            f"{w * (100 / 110) - l:+.1f}u  p vs 52.4% = {binom_p(w, d):.2f}"]


def main(argv=None) -> int:
    import pandas as pd
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--pbp-dir", required=True)
    args = ap.parse_args(argv)
    pbp = sorted(glob.glob(os.path.join(args.pbp_dir, "play_by_play_*.csv.gz")))
    if not pbp:
        raise SystemExit("no play_by_play_*.csv.gz -- see epa_experiment's docstring")
    cols = sorted(set(PBP_COLUMNS) | set(WEATHER_COLUMNS))
    frame = pd.concat([pd.read_csv(p, usecols=cols, low_memory=False) for p in pbp])
    frame = frame[frame.season_type == "REG"]
    weather = weather_by_game(frame)
    team_games, lines = team_games_from_frame(frame)
    sep = "-" * 72
    print("=" * 72)
    print("WEATHER EXPERIMENT  (NFL regular season 2022-2026, observed kickoff weather)")
    print("=" * 72)
    print("\n".join([sep, "A. TOTALS  (c = points over the close; 0 = priced)", sep]
                    + report_totals(lines, weather, column_only=False)
                    + ["", "  Robustness: column-only rows"]
                    + report_totals(lines, weather, column_only=True)))
    print("\n".join([sep, "B. PROPS  (projection only -- no line test)", sep]
                    + report_props(prop_rows(team_games, weather))))
    print("\n".join([sep, "C. SIDES  (dome team on the road in the cold)", sep]
                    + report_sides(lines, weather, dome_teams(frame))))
    print("\n  Observed weather, not forecasts; noise biases every c toward zero.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

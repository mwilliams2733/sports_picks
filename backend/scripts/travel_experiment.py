"""Do NFL travel and rest tell us anything the closing spread does not?

Why this exists
---------------
Third item on the owner's criteria backlog (`docs/criteria-backlog.md`,
2026-10-06). The model has no travel input. Its rest flag
(`_check_schedule_fatigue`) is basketball-only.

Population: regular-season games 2022-2026 at the home team's stadium
(nflverse `location == "Home"`; neutral and international games excluded,
because the home team's stadium is not the venue). The away team travels
from its own home stadium. Stadiums and time zones come from
`backend/data/nfl_stadiums.json`, the table production uses.

Four features, fixed before the first run, each signed so positive favours
the HOME side, each regressed separately:

    margin = a + b*spread_line + c*feature

1. **distance** -- the away team's great-circle travel, thousands of miles.
2. **tz_east** -- hours the away team moved east: venue UTC offset minus
   the away team's home UTC offset, at kickoff (real zones, so Arizona's
   missing DST is handled).
3. **body_clock** -- 1 if the away team kicks off before 11:00 by its home
   clock (a West Coast team at a 1pm ET start).
4. **rest_diff** -- home days since its last game minus away's. Week 1 and
   a team's first game are excluded (no previous game this season).

ATS, against -110 break-even:
* fade away teams traveling >= 2,000 miles;
* fade away teams on the early body clock;
* back the team with a >= 3-day rest edge (mostly byes vs short weeks).

Four features are tested: a single p near 0.03 is not a finding (Bonferroni
0.05/4 = 0.0125).

Writes nothing, needs no database:

    python -m backend.scripts.travel_experiment --pbp-dir pbp
"""
from __future__ import annotations

import argparse
import glob
import logging
import math
import os
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo

from backend.analysis.football_weather import STADIUMS
from backend.scripts.import_nflverse_history import ABBR_FIXUPS
from backend.scripts.injury_experiment import binom_p, clustered_ols
from backend.scripts.weather_forecast_backtest import kickoff_et

logger = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")
LONG_TRIP_MILES = 2000.0
EARLY_LOCAL_HOUR = 11
REST_EDGE_DAYS = 3
COLUMNS = ["game_id", "season", "season_type", "game_date", "home_team",
           "away_team", "start_time", "location", "result", "spread_line"]


def miles(a: dict, b: dict) -> float:
    """Great-circle distance between two stadium entries."""
    r = 3958.8
    p1, p2 = math.radians(a["lat"]), math.radians(b["lat"])
    dp, dl = p2 - p1, math.radians(b["lon"] - a["lon"])
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def utc_offset_hours(tz: str, when: datetime) -> float:
    return when.astimezone(ZoneInfo(tz)).utcoffset().total_seconds() / 3600


@dataclass(frozen=True)
class TravelRow:
    game_id: str
    season: int
    margin: float
    spread_line: float
    distance: float        # thousands of miles, away team
    tz_east: float         # hours the away team moved east
    body_clock: float      # 1 if away kicks off before EARLY_LOCAL_HOUR at home
    rest_diff: float | None


def rest_days(frame) -> dict[tuple[str, str], int]:
    """(game_id, team) -> days since that team's previous game this season."""
    seen = defaultdict(list)
    for r in frame.drop_duplicates("game_id").itertuples(index=False):
        d = date.fromisoformat(r.game_date)
        for t in (r.home_team, r.away_team):
            seen[(int(r.season), t)].append((d, r.game_id))
    out = {}
    for (_, team), games in seen.items():
        games.sort()
        for (prev, _), (d, gid) in zip(games, games[1:]):
            out[(gid, team)] = (d - prev).days
    return out


def travel_rows(frame) -> list[TravelRow]:
    g = frame[frame.season_type == "REG"].drop_duplicates("game_id")
    rest = rest_days(g)
    rows = []
    for r in g.itertuples(index=False):
        if r.location != "Home" or any(isinstance(v, float) and math.isnan(v)
                                       for v in (r.result, r.spread_line)):
            continue
        home, away = (ABBR_FIXUPS.get(t, t) for t in (r.home_team, r.away_team))
        hs, aw = STADIUMS.get(home), STADIUMS.get(away)
        ko = kickoff_et(r.game_date, r.start_time)
        if hs is None or aw is None or ko is None:
            continue
        ko = ko.replace(tzinfo=ET)
        tz_east = utc_offset_hours(hs["tz"], ko) - utc_offset_hours(aw["tz"], ko)
        away_local = ko.astimezone(ZoneInfo(aw["tz"]))
        rh, ra = rest.get((r.game_id, r.home_team)), rest.get((r.game_id, r.away_team))
        rows.append(TravelRow(
            r.game_id, int(r.season), float(r.result), float(r.spread_line),
            # Positive favours home: the further the away team came, the more.
            distance=miles(aw, hs) / 1000.0,
            tz_east=tz_east,
            body_clock=float(away_local.hour < EARLY_LOCAL_HOUR),
            rest_diff=float(rh - ra) if rh is not None and ra is not None else None))
    return rows


def ats(rows, pick_home) -> tuple[int, int, int]:
    """Record of betting the side `pick_home(row)` returns (True home, False
    away, None no bet) against the closing spread."""
    w = l = p = 0
    for r in rows:
        side = pick_home(r)
        if side is None:
            continue
        cover = r.margin - r.spread_line
        if cover == 0:
            p += 1
        elif (cover > 0) == side:
            w += 1
        else:
            l += 1
    return w, l, p


def _ats_line(label: str, rec) -> str:
    w, l, p = rec
    d = w + l
    rate = f"{w / d:.3f}" if d else "n/a"
    return (f"    ATS {label}: {w}-{l}-{p}  {rate}  {w * (100 / 110) - l:+.1f}u  "
            f"p vs 52.4% = {binom_p(w, d):.2f}")


def report(rows) -> list[str]:
    out = [f"  {len(rows)} games at the home team's stadium (neutral sites excluded)",
           "  c = points the close left to the HOME side per unit of the feature"]
    feats = {
        "distance": ("away travel, per 1,000 mi", lambda r: r.distance),
        "tz_east": ("away moved east, per hour", lambda r: r.tz_east),
        "body_clock": (f"away kicks off < {EARLY_LOCAL_HOUR}:00 home clock", lambda r: r.body_clock),
        "rest_diff": ("rest, home minus away, per day", lambda r: r.rest_diff),
    }
    for name, (label, fn) in feats.items():
        rs = [r for r in rows if fn(r) is not None]
        beta, se, p, _ = clustered_ols([[r.spread_line for r in rs], [fn(r) for r in rs]],
                                       [r.margin for r in rs], [r.game_id for r in rs])
        extra = ""
        if name == "body_clock":
            extra = f", n treated {int(sum(fn(r) for r in rs))}"
        elif name == "distance":
            extra = f", n >= {LONG_TRIP_MILES:.0f} mi {sum(r.distance * 1000 >= LONG_TRIP_MILES for r in rs)}"
        elif name == "rest_diff":
            extra = f", n edge >= {REST_EDGE_DAYS}d {sum(abs(r.rest_diff) >= REST_EDGE_DAYS for r in rs)}"
        out.append(f"  {label}  (n {len(rs)}{extra})")
        out.append(f"    c = {beta[2]:+.3f} pts (95% CI {beta[2] - 1.96 * se[2]:+.3f}"
                   f"..{beta[2] + 1.96 * se[2]:+.3f}), p = {p[2]:.3f}")
    out.append(_ats_line(f"fade away teams traveling >= {LONG_TRIP_MILES:.0f} mi",
                         ats(rows, lambda r: True if r.distance * 1000 >= LONG_TRIP_MILES else None)))
    out.append(_ats_line("fade away teams on the early body clock",
                         ats(rows, lambda r: True if r.body_clock else None)))
    out.append(_ats_line(f"back the team with a >= {REST_EDGE_DAYS}-day rest edge",
                         ats([r for r in rows if r.rest_diff is not None],
                             lambda r: (r.rest_diff > 0) if abs(r.rest_diff) >= REST_EDGE_DAYS
                             else None)))
    by_season = defaultdict(lambda: [0, 0])
    for r in rows:
        if r.body_clock:
            cover = r.margin - r.spread_line
            if cover:
                by_season[r.season][0 if cover > 0 else 1] += 1
    out.append("    body clock by season (home covers-fails): "
               + ", ".join(f"{s} {w}-{l}" for s, (w, l) in sorted(by_season.items())))
    out.append(f"  Four features tested: treat p > {0.05 / 4:.4f} as unproven.")
    return out


def main(argv=None) -> int:
    import pandas as pd
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--pbp-dir", required=True)
    args = ap.parse_args(argv)
    pbp = sorted(glob.glob(os.path.join(args.pbp_dir, "play_by_play_*.csv.gz")))
    if not pbp:
        raise SystemExit("no play_by_play_*.csv.gz -- see epa_experiment's docstring")
    frame = pd.concat([pd.read_csv(p, usecols=COLUMNS, low_memory=False) for p in pbp])
    print("=" * 72)
    print("TRAVEL AND REST EXPERIMENT  (NFL regular season 2022-2026, vs the close)")
    print("=" * 72)
    print("\n".join(report(travel_rows(frame))))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

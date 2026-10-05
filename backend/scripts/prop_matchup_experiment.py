"""Does the opponent's pass / run defense help project NFL player props?

Why this exists
---------------
The owner asked (2026-10-04) whether props consider where a defense ranks
against the run and the pass. They do not: `prop_analyzer` scales a
projection by the opponent's `defensive_rating`, which is a basketball stat
(points per 100 possessions) and is never computed for football, so every
NFL prop gets no matchup adjustment at all. Before wiring one in, two tests:

**1. Projection.** Over 2022-2026 player-games, does the opponent's
walk-forward yards allowed add anything to the player's own walk-forward
season average?

    actual = a + b * baseline + c * baseline * (factor - 1)

`c` is the matchup's information. c = 1 means "scale the average by the
factor" is exactly right; c = 0 means the defense tells you nothing. This
test says whether the matchup is real.

**2. The line.** On this season's NFL prop lines (since 2026-09-20), does the
line already price it?

    actual = a + b * line + c * line * (factor - 1)

c near 0 means the books already priced the matchup in, and adjusting our
projection cannot beat them. This is the test that decides whether there is
an edge. It has few games; read its interval, not its point estimate.

Definitions
-----------
* Defense: per team-game, `passing_yards` and `rushing_yards` allowed
  (nflverse play-by-play, by `defteam`), walk-forward within the season and
  shrunk toward the league mean (`football_defense.shrunk_factor`, the
  function production uses), so a
  defense with one game is not judged on one game. Fixed a priori, not
  tuned. Factor = shrunk allowed / league mean of team-games before the date.
* Receiving yards use the opponent's pass defense (passing yards allowed).
* Baseline: the player's mean of the stat over prior games this season,
  needing MIN_PRIOR games and a mean over the market's floor (QB, featured
  back, regular receiver), roughly who books post lines for.

Read only. Writes nothing.

    python -m backend.scripts.prop_matchup_experiment --db <snapshot> --pbp-dir pbp
"""
from __future__ import annotations

import argparse
import glob
import logging
import math
import os
from collections import defaultdict
from datetime import date, timedelta

from backend.analysis.football_defense import shrunk_factor
from backend.scripts.import_nflverse_history import ABBR_FIXUPS

logger = logging.getLogger(__name__)

MIN_PRIOR = 3
#: market -> (game-log column, defense side, baseline floor in yards)
MARKETS = {
    "player_pass_yds": ("pass_yards", "pass", 120.0),
    "player_rush_yds": ("rush_yards", "rush", 25.0),
    "player_reception_yds": ("rec_yards", "pass", 20.0),
}


def defense_table(frame) -> dict[tuple[int, str], list[tuple[date, float, float]]]:
    """(season, team) -> [(date, pass allowed, rush allowed)] per game, date order."""
    g = (frame.dropna(subset=["defteam"])
         .groupby(["season", "game_id", "game_date", "defteam"], as_index=False)
         [["passing_yards", "rushing_yards"]].sum())
    out = defaultdict(list)
    for r in g.itertuples(index=False):
        team = ABBR_FIXUPS.get(r.defteam, r.defteam)
        out[(int(r.season), team)].append(
            (date.fromisoformat(str(r.game_date)), float(r.passing_yards), float(r.rushing_yards)))
    for v in out.values():
        v.sort()
    return out


def factor_at(defense, season: int, team: str, when: date, side: str) -> float | None:
    """Shrunk walk-forward allowed / league mean, using games strictly before `when`."""
    idx = 1 if side == "pass" else 2
    league = [g[idx] for (s, _), games in defense.items() if s == season
              for g in games if g[0] < when]
    own = [g[idx] for g in defense.get((season, team), []) if g[0] < when]
    return shrunk_factor(own, league)


def season_of(d: date) -> int:
    return d.year if d.month >= 3 else d.year - 1


def ols(x_cols, y):
    import numpy as np
    from scipy import stats
    x = np.column_stack([np.ones(len(y))] + [np.asarray(c, float) for c in x_cols])
    y = np.asarray(y, float)
    inv = np.linalg.pinv(x.T @ x)
    beta = inv @ x.T @ y
    resid = y - x @ beta
    dof = len(y) - x.shape[1]
    se = np.sqrt(np.diag(inv) * float(resid @ resid) / dof)
    p = 2 * stats.t.sf(np.abs(beta / se), dof)
    return beta, se, p


def player_rows(conn, defense):
    """Per player-game with a baseline: market, actual, baseline, factor, game date, name."""
    import pandas as pd
    logs = pd.read_sql("""
        select ps.player_name, ps.game_date, ps.pass_yards, ps.rush_yards, ps.rec_yards,
               t.abbreviation as team
        from player_stats ps join teams t on t.id = ps.team_id
        where ps.sport = 'nfl' and ps.stat_type = 'game_log'""", conn)
    games = pd.read_sql("""
        select g.date, h.abbreviation as home, a.abbreviation as away
        from games g join teams h on h.id = g.home_team_id join teams a on a.id = g.away_team_id
        where g.sport = 'nfl'""", conn)
    opp = {}
    for r in games.itertuples(index=False):
        d = date.fromisoformat(str(r.date))
        for dd in (d - timedelta(days=1), d, d + timedelta(days=1)):  # UTC vs local dates
            opp.setdefault((dd, r.home), r.away)
            opp.setdefault((dd, r.away), r.home)

    logs["d"] = logs.game_date.map(date.fromisoformat)
    out = []
    for (name, team), grp in logs.groupby(["player_name", "team"]):
        grp = grp.sort_values("d")
        for market, (col, side, floor) in MARKETS.items():
            prior: dict[int, list[float]] = defaultdict(list)
            for r in grp.itertuples(index=False):
                season, val = season_of(r.d), getattr(r, col)
                hist = prior[season]
                if not (isinstance(val, float) and math.isnan(val)) and val is not None:
                    o = opp.get((r.d, team))
                    f = factor_at(defense, season, o, r.d, side) if o else None
                    if f is not None:
                        # The baseline is only for test 1; the line test needs
                        # just actual, line and factor, and early in a season
                        # almost no one has MIN_PRIOR games yet.
                        ok = len(hist) >= MIN_PRIOR and sum(hist) / len(hist) >= floor
                        out.append((market, float(val), sum(hist) / len(hist) if ok else None,
                                    f, r.d, name))
                    hist.append(float(val))
    return out


def prop_lines(conn) -> dict[tuple[str, str, date], float]:
    """(market, player, game date) -> median Over line across books (main lines only)."""
    import pandas as pd
    df = pd.read_sql("""
        select pp.market, pp.player_name, pp.line, g.date
        from player_props pp join games g on g.id = pp.game_id
        where g.sport = 'nfl' and pp.outcome = 'Over' and pp.line is not null
          and pp.market in ('player_pass_yds','player_rush_yds','player_reception_yds')""", conn)
    out = {}
    for (m, p, d), grp in df.groupby(["market", "player_name", "date"]):
        out[(m, p, date.fromisoformat(str(d)))] = float(grp.line.median())
    return out


def report(rows, lines) -> str:
    lines_out = ["=" * 72, "PROP MATCHUP EXPERIMENT  (opponent pass / run defense)", "=" * 72]
    for market in MARKETS:
        every = [r for r in rows if r[0] == market]
        rs = [r for r in every if r[2] is not None]
        y = [r[1] for r in rs]
        base = [r[2] for r in rs]
        term = [r[2] * (r[3] - 1) for r in rs]
        beta, se, p = ols([base, term], y)
        rmse_b = math.sqrt(sum((a - b) ** 2 for a, b in zip(y, base)) / len(y))
        rmse_f = math.sqrt(sum((r[1] - r[2] * r[3]) ** 2 for r in rs) / len(y))
        players = len({r[5] for r in rs})
        lines_out += ["-" * 72, market, "-" * 72,
                      f"  1. projection: n {len(rs)} player-games, {players} players",
                      f"     matchup coef c = {beta[2]:+.3f} (95% CI {beta[2] - 1.96 * se[2]:+.3f}"
                      f"..{beta[2] + 1.96 * se[2]:+.3f}), p = {p[2]:.2g}",
                      f"     RMSE own average {rmse_b:.2f} vs average x factor {rmse_f:.2f}"]
        matched = []
        for market_, val, b, f, d, name in every:
            ln = None
            for dd in (d, d - timedelta(days=1), d + timedelta(days=1)):
                ln = lines.get((market_, name, dd))
                if ln is not None:
                    break
            if ln is not None:
                matched.append((val, ln, f))
        if len(matched) >= 20:
            bl, sl, pl = ols([[m[1] for m in matched], [m[1] * (m[2] - 1) for m in matched]],
                             [m[0] for m in matched])
            over = [m for m in matched if m[0] != m[1]]
            soft = [m for m in over if m[2] > 1.0]
            tough = [m for m in over if m[2] <= 1.0]
            hit = lambda ms: (sum(m[0] > m[1] for m in ms) / len(ms)) if ms else float("nan")
            lines_out += [f"  2. vs the line: n {len(matched)} player-games with a line",
                          f"     matchup coef c = {bl[2]:+.3f} (95% CI {bl[2] - 1.96 * sl[2]:+.3f}"
                          f"..{bl[2] + 1.96 * sl[2]:+.3f}), p = {pl[2]:.2g}",
                          f"     Over hit rate: soft defense (factor>1) {hit(soft):.3f} on {len(soft)}, "
                          f"tough {hit(tough):.3f} on {len(tough)}"]
        else:
            lines_out.append(f"  2. vs the line: only {len(matched)} matched, not tested")
    lines_out += ["", "  OLS standard errors treat player-games as independent. A player",
                  "  appears many times and a game's players share its script, so the",
                  "  effective n is smaller and the intervals are optimistic."]
    return "\n".join(lines_out)


def main(argv=None) -> int:
    import sqlite3
    import pandas as pd
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", required=True, help="Database snapshot. Read only.")
    ap.add_argument("--pbp-dir", required=True)
    args = ap.parse_args(argv)
    paths = sorted(glob.glob(os.path.join(args.pbp_dir, "play_by_play_*.csv.gz")))
    if not paths:
        raise SystemExit(f"no play_by_play_*.csv.gz under {args.pbp_dir!r}")
    frame = pd.concat([pd.read_csv(p, usecols=["season", "game_id", "game_date", "defteam",
                                                "passing_yards", "rushing_yards"],
                                   low_memory=False) for p in paths])
    conn = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    try:
        rows = player_rows(conn, defense_table(frame))
        print(report(rows, prop_lines(conn)))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

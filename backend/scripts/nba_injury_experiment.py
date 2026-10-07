"""When an NBA team's best scorer sits, how much do his teammates' numbers move?

Why this exists
---------------
NBA joins the digest on 2026-10-20 (owner, 2026-10-06). The NFL injury test
(`injury_experiment`) found a missing starter fully priced in the spread
but a real shift in teammates' yards. In the NBA one player is a far larger
share of a team, and late scratches land about 30 minutes before tip-off,
so if teammates' prop lines lag anywhere it is here. This measures the
shift first, from our own 2025-26 ESPN box scores (`player_stats` game logs).

Definitions, fixed before the first run (2026-10-07):

* **Team-game.** A (team, date) in the regular-season game logs (through
  2026-04-13), with the 2026-09-19 backfill's duplicated games dropped
  (`drop_duplicated_games`). Teams never play twice a day, so no game matching is needed
  -- matching on date +/- 1 would cross back-to-backs.
* **Star.** The team's leader in points per game so far this season, among
  players with >= MIN_STAR_GAMES games for the team, from earlier games only.
* **Star out.** The star logged no minutes tonight but played in one of the
  team's previous RECENT_GAMES games: an absence, not a departed player.
* **Teammate baseline.** His mean of the stat over prior games this season
  (>= MIN_PRIOR), above a prop-sized floor (points 8, rebounds 4, assists 3).

    actual = a + b * base + c * base * star_out

`c` is the share of a teammate's baseline he gains. Clustered by game.

The line test: on the games with stored prop lines (2026-03..05), does the
line already include it? actual - line = a + c * star_out, Over hit rates.

Writes nothing:

    python -m backend.scripts.nba_injury_experiment --db <snapshot>
"""
from __future__ import annotations

import argparse
import sqlite3
from collections import defaultdict
from datetime import date

from backend.scripts.injury_experiment import clustered_ols

REGULAR_END = date(2026, 4, 13)
MIN_STAR_GAMES = 10
RECENT_GAMES = 5
MIN_PRIOR = 5
DUPLICATE_SHARE = 0.8
#: stat -> (prop market, baseline floor)
STATS = {"points": ("player_points", 8.0),
         "rebounds": ("player_rebounds", 4.0),
         "assists": ("player_assists", 3.0)}


def team_games(conn):
    """{team_id: [(date, {player: row})]} in date order, regular season."""
    games = defaultdict(lambda: defaultdict(dict))
    real_teams = {tid for (tid,) in conn.execute(
        "select distinct home_team_id from games where sport='nba' and season_type='regular'")}
    for name, tid, d, mins, pts, reb, ast in conn.execute("""
            select player_name, team_id, game_date, minutes, points, rebounds, assists
            from player_stats where sport='nba' and stat_type='game_log'"""):
        d = date.fromisoformat(d)
        if tid not in real_teams or d > REGULAR_END:
            continue
        games[tid][d][name] = dict(minutes=mins or 0.0, points=pts or 0.0,
                                   rebounds=reb or 0.0, assists=ast or 0.0)
    return {tid: drop_duplicated_games(sorted(by_date.items())) for tid, by_date in games.items()}


def drop_duplicated_games(games):
    """Drop the second copy of a game stored on two adjacent dates.

    A 2026-09-19 backfill wrote 1,040 nba game-log rows twice, the copy one
    day after the original with identical lines (96 team-games). Kept, the
    copy is a phantom game: a star "absent" from it is not absent, and every
    teammate's baseline counts the game twice. A next-day box score where at
    least DUPLICATE_SHARE of the shared players have identical lines is a
    copy -- real back-to-backs never come close.
    """
    out = []
    for d, players in games:
        if out:
            pd, prev = out[-1]
            common = [p for p in players if p in prev and players[p]["minutes"] > 0]
            same = sum(players[p] == prev[p] for p in common)
            if (d - pd).days == 1 and common and same >= DUPLICATE_SHARE * len(common):
                continue
        out.append((d, players))
    return out


def star_out_flags(games_by_team):
    """{(team, date): (star, out)} from prior games only."""
    out = {}
    for tid, games in games_by_team.items():
        totals = defaultdict(lambda: [0.0, 0])        # player -> [points, games]
        recent: list[set] = []
        for d, players in games:
            played = {p for p, r in players.items() if r["minutes"] > 0}
            eligible = {p: t / n for p, (t, n) in totals.items() if n >= MIN_STAR_GAMES}
            star = max(eligible, key=lambda p: (eligible[p], p)) if eligible else None
            if star is not None:
                was_active = any(star in s for s in recent[-RECENT_GAMES:])
                out[(tid, d)] = (star, star not in played and was_active)
            for p in played:
                totals[p][0] += players[p]["points"]
                totals[p][1] += 1
            recent.append(played)
    return out


def teammate_rows(games_by_team, flags):
    """(stat, actual, baseline, star_out, team, date, player) per teammate-game."""
    rows = []
    for tid, games in games_by_team.items():
        hist = defaultdict(list)                     # (stat, player) -> values
        for d, players in games:
            star, out = flags.get((tid, d), (None, False))
            for p, r in players.items():
                if r["minutes"] <= 0 or p == star:
                    continue
                for stat, (_, floor) in STATS.items():
                    prior = hist[(stat, p)]
                    if len(prior) >= MIN_PRIOR and sum(prior) / len(prior) >= floor:
                        rows.append((stat, r[stat], sum(prior) / len(prior), out, tid, d, p))
            for p, r in players.items():
                if r["minutes"] > 0:
                    for stat in STATS:
                        hist[(stat, p)].append(r[stat])
    return rows


def prop_lines(conn):
    """{(player, market, date): median Over line}."""
    lines = defaultdict(list)
    for p, m, line, d in conn.execute("""
            select pp.player_name, pp.market, pp.line, g.date from player_props pp
            join games g on g.id = pp.game_id
            where g.sport='nba' and pp.outcome='Over' and pp.line is not null"""):
        lines[(p, m, date.fromisoformat(d))].append(line)
    return {k: sorted(v)[len(v) // 2] for k, v in lines.items()}


def report(rows, flags, lines) -> list[str]:
    n_out = sum(o for _, o in flags.values())
    out = [f"  {len(flags)} team-games with a star; star out in {n_out} "
           f"({100 * n_out / len(flags):.1f}%)"]
    for stat, (market, floor) in STATS.items():
        rs = [r for r in rows if r[0] == stat]
        cl = [f"{r[4]}|{r[5]}" for r in rs]
        beta, se, p, g = clustered_ols([[r[2] for r in rs], [r[2] * r[3] for r in rs]],
                                       [r[1] for r in rs], cl)
        treated = sum(r[3] for r in rs)
        out += [f"  {stat}: {len(rs)} teammate-games in {g} team-games, floor {floor:g}",
                f"    star out (n {treated}): c = {beta[2]:+.3f} x baseline "
                f"(95% CI {beta[2] - 1.96 * se[2]:+.3f}..{beta[2] + 1.96 * se[2]:+.3f}), "
                f"p = {p[2]:.2g}   baseline slope {beta[1]:.3f}"]
        matched = []
        for _, actual, base, o, tid, d, player in rs:
            for dd in (d,):
                ln = lines.get((player, market, dd))
                if ln is not None:
                    matched.append((actual, ln, o))
        if matched:
            def hit(ms):
                ms = [m for m in ms if m[0] != m[1]]
                return f"{sum(m[0] > m[1] for m in ms)}/{len(ms)}" if ms else "0/0"
            out.append(f"    vs prop lines: {len(matched)} matched; Over hit with star out "
                       f"{hit([m for m in matched if m[2]])}, star in {hit([m for m in matched if not m[2]])}")
    out += ["", "  Clustered by team-game; a player recurs across games, so intervals",
            "  are somewhat optimistic. One season only."]
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", required=True, help="A .backup snapshot.")
    args = ap.parse_args(argv)
    conn = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    try:
        games = team_games(conn)
        flags = star_out_flags(games)
        rows = teammate_rows(games, flags)
        lines = prop_lines(conn)
    finally:
        conn.close()
    print("=" * 74)
    print("NBA: WHEN THE STAR SITS  (2025-26 regular season, ESPN box scores)")
    print("=" * 74)
    print("\n".join(report(rows, flags, lines)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

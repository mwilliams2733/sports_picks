"""Delete the copies of player game logs the 2026-09-19 backfill wrote twice.

Why this exists
---------------
`nba_injury_experiment` (2026-10-07) found player_stats game_log rows stored
twice: the same player, team and identical stat line on two adjacent dates.
Measured on the live db: nba 1,040 rows (96 team-games), ncaab 32; nfl and
ncaaf none. Every copy was written by the 2026-09-19 backfill. A copy is a
phantom game: the prop model's recent form (`_recent_form`, last five logs)
counts it twice, and it adds games a team never played.

Which copy goes: the LATER-dated row of an identical adjacent pair, when
either (a) `games` shows the team played only on the earlier date, or (b)
the row was written by the 2026-09-19 backfill (BACKFILL_DAY). Measured on
the live db: in all 896 pairs where `games` decides, the copy was the later
row and was written that day, so (b) settles the back-to-backs `games`
cannot (a real game on both dates -- the copy took the second game's date,
and that real second game is missing for those players). Anything else is
skipped and reported: a wrong guess would delete a real game. Only rows
identical to their partner are touched.

Stop the scheduler and back the database up first; a dry run is the default.
Idempotent: a second `--apply` deletes nothing.

    python -m backend.scripts.dedupe_game_logs --db <abs path>
    python -m backend.scripts.dedupe_game_logs --db <abs path> --apply
"""
from __future__ import annotations

import argparse
import sqlite3
from collections import Counter, defaultdict
from datetime import date, timedelta

#: The backfill that wrote the copies (see the module docstring).
BACKFILL_DAY = date(2026, 9, 19)

#: Columns that must match for two rows to be the same game's line.
STAT_COLUMNS = ("minutes", "points", "rebounds", "assists", "threes", "steals", "blocks",
                "turnovers", "pass_yards", "rush_yards", "rec_yards", "receptions", "touchdowns")


def find_copies(rows, team_game_dates) -> tuple[list[int], list[tuple]]:
    """(row ids to delete, ambiguous pairs).

    `rows`: dicts with id, sport, player_name, team_id, game_date (date),
    fetched (date written) and STAT_COLUMNS. `team_game_dates`:
    {(sport, team_id): set of dates the team actually played}.
    """
    by_key = defaultdict(dict)
    for r in rows:
        by_key[(r["sport"], r["player_name"], r["team_id"])][r["game_date"]] = r
    doomed, ambiguous = [], []
    for (sport, player, team), by_date in by_key.items():
        for d, first in by_date.items():
            second = by_date.get(d + timedelta(days=1))
            if second is None or any(first[c] != second[c] for c in STAT_COLUMNS):
                continue
            if not any(first[c] for c in STAT_COLUMNS):
                continue                      # an all-empty line proves nothing
            played = team_game_dates.get((sport, team), set())
            on_first, on_second = d in played, d + timedelta(days=1) in played
            if (on_first and not on_second) or second["fetched"] == BACKFILL_DAY:
                doomed.append(second["id"])
            else:
                ambiguous.append((sport, player, team, d))
    return sorted(set(doomed)), ambiguous


def load(conn):
    rows = []
    for rec in conn.execute("""
            select id, sport, player_name, team_id, game_date, fetched_at,
                   minutes, points, rebounds, assists, threes, steals, blocks,
                   turnovers, pass_yards, rush_yards, rec_yards, receptions, touchdowns
            from player_stats where stat_type = 'game_log'"""):
        r = dict(zip(("id", "sport", "player_name", "team_id", "game_date", "fetched")
                     + STAT_COLUMNS, rec))
        r["game_date"] = date.fromisoformat(r["game_date"])
        r["fetched"] = date.fromisoformat(r["fetched"][:10])
        rows.append(r)
    played = defaultdict(set)
    for sport, d, h, a in conn.execute(
            "select sport, date, home_team_id, away_team_id from games where status = 'final'"):
        played[(sport, h)].add(date.fromisoformat(d))
        played[(sport, a)].add(date.fromisoformat(d))
    return rows, played


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", required=True)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args(argv)
    conn = sqlite3.connect(args.db)
    try:
        rows, played = load(conn)
        doomed, ambiguous = find_copies(rows, played)
        by_sport = Counter(r["sport"] for r in rows if r["id"] in set(doomed))
        print(f"copies to delete: {len(doomed)} {dict(by_sport)}; ambiguous pairs skipped: "
              f"{len(ambiguous)}")
        for a in ambiguous[:10]:
            print("  ambiguous:", a)
        if args.apply and doomed:
            conn.executemany("delete from player_stats where id = ?", [(i,) for i in doomed])
            conn.commit()
            print(f"deleted {len(doomed)} rows")
        elif not args.apply:
            print("dry run: nothing deleted (pass --apply)")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

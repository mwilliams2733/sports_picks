"""Repair team rows whose `abbreviation` holds a display name.

Each bad row is one of:

  rename  -- no existing row owns the target abbreviation. Rewrite in place,
             keeping the id, so nothing needs repointing.
  merge   -- an existing row owns it. Repoint every foreign key to the
             survivor and delete the bad row.

`(sport, abbreviation)` uniqueness is the point of the exercise: the Elo
replay keys on abbreviation in memory (team_stats.py:362), so two rows
sharing one would silently merge two programmes' ratings.

Child tables are DISCOVERED from the schema, never listed. A hand-written
list is how an earlier repair missed 578 `player_props` rows and died on a
FOREIGN KEY constraint.

    python -m backend.scripts.fix_team_identity --db <abs path> [--apply]
"""

from __future__ import annotations

import argparse
import sqlite3
from datetime import date, timedelta
from typing import NamedTuple

from backend.team_identity import ABBREVIATION_SPORTS, resolution_of


class Plan(NamedTuple):
    team_id: int
    label: str
    target_abbr: str | None
    target_team_id: int | None
    games: int


def schema_names(cur) -> frozenset[str]:
    """Every table and column name the database actually declares.

    Used as an allowlist for identifier interpolation below.
    """
    names: set[str] = set()
    for (table,) in cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall():
        if not table.replace("_", "").isalnum():
            continue
        names.add(table)
        # nosemgrep: python.sqlalchemy.security.sqlalchemy-execute-raw-query
        # `table` is a sqlite_master name that just passed the isalnum guard
        # above; PRAGMA takes no bound parameters.
        for col in cur.execute(f'PRAGMA table_info("{table}")').fetchall():
            names.add(col[1])
    return frozenset(names)


def ident(name: str, allowed: frozenset[str] | None = None) -> str:
    """Quote an identifier that cannot be passed as a bound parameter.

    Table and column names are not parameterizable, so they have to be
    interpolated. Two guards make that safe, and both are local -- neither
    relies on where the caller got the string:

    1. the name must be alphanumeric-plus-underscore, and
    2. when ``allowed`` is given, it must be a name the schema declares.

    Every interpolation in this module passes the schema allowlist.
    """
    if not name.replace("_", "").isalnum():
        raise ValueError(f"refusing to interpolate identifier {name!r}")
    if allowed is not None and name not in allowed:
        raise ValueError(f"{name!r} is not a table or column in this database")
    return f'"{name}"'


def fk_children(cur, parent: str) -> list[tuple[str, str]]:
    """Every (table, column) holding a foreign key to ``parent``.id."""
    allowed = schema_names(cur)
    out: list[tuple[str, str]] = []
    for (table,) in cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall():
        if table not in allowed:
            continue
        # nosemgrep: python.sqlalchemy.security.sqlalchemy-execute-raw-query
        # Identifier, not a value: PRAGMA accepts no bound parameters. The
        # name comes from sqlite_master and is checked against the schema
        # allowlist by ident().
        sql = "PRAGMA foreign_key_list(" + ident(table, allowed) + ")"
        for fk in cur.execute(sql).fetchall():
            if fk[2] == parent:
                out.append((table, fk[3]))
    return out


def classify(cur, sport: str) -> dict[str, list[Plan]]:
    """Sort every team row of ``sport`` into rename / merge / unresolved.

    Rows already holding a real abbreviation are skipped entirely: they are
    not in any bucket, so a re-run is a no-op.
    """
    rows = cur.execute(
        "SELECT id, name, abbreviation FROM teams WHERE sport=? ORDER BY id",
        (sport,),
    ).fetchall()
    owner = {abbr: tid for tid, _, abbr in rows}

    buckets: dict[str, list[Plan]] = {"rename": [], "merge": [], "unresolved": []}
    for tid, _name, abbr in rows:
        target, how = resolution_of(sport, abbr)
        if how == "already_abbr":
            continue
        games = cur.execute(
            "SELECT COUNT(*) FROM games WHERE home_team_id=? OR away_team_id=?",
            (tid, tid),
        ).fetchone()[0]
        if target is None:
            buckets["unresolved"].append(Plan(tid, abbr, None, None, games))
        elif target in owner and owner[target] != tid:
            buckets["merge"].append(Plan(tid, abbr, target, owner[target], games))
        else:
            buckets["rename"].append(Plan(tid, abbr, target, None, games))
    return buckets


def _duplicate_games(cur, sport: str) -> list[tuple]:
    """Fixtures now held twice: same sport, date and teams.

    Only visible AFTER a merge -- while the two schools have separate team
    ids the rows look like different fixtures, so a dry run cannot find these.
    """
    return cur.execute(
        "SELECT sport, date, home_team_id, away_team_id, COUNT(*) c, "
        "       GROUP_CONCAT(id) ids "
        "FROM games WHERE sport=? "
        "GROUP BY sport, date, home_team_id, away_team_id HAVING c > 1",
        (sport,),
    ).fetchall()


def resolve_duplicate_games(con: sqlite3.Connection, sport: str, *,
                            apply: bool) -> dict:
    """Collapse fixtures held by more than one game row.

    **The FINAL row survives.** It carries the scores, the espn_id and the
    derived team_stats/elo_history; the twin is an empty ``scheduled`` row
    that happens to hold the picks and odds, because those arrived from the
    Odds API against the unmatchable team row.

    This is deliberately the OPPOSITE of merge_duplicate_games.py's rule
    ("the row with picks wins"). Measured against production after the team
    merge, that rule would choose the wrong row in 27 of 34 groups --
    discarding real scores and stranding the picks on a row that can never
    finalise.

    A group is resolved only when exactly one row is final. Zero finals (two
    scheduled rows, nothing to choose between them) or several (two different
    results claiming one fixture) are reported and left alone: guessing there
    is how a real result gets deleted.
    """
    cur = con.cursor()
    allowed = schema_names(cur)
    children = fk_children(cur, "games")

    resolved = 0
    skipped: list[tuple] = []
    for row in _duplicate_games(cur, sport):
        ids = [int(i) for i in row[5].split(",")]
        finals = [
            g for g in ids
            if cur.execute("SELECT status FROM games WHERE id=?", (g,)
                           ).fetchone()[0] == "final"
        ]
        if len(finals) != 1:
            skipped.append((row[1], ids, len(finals)))
            continue
        resolved += 1
        if not apply:
            continue
        survivor = finals[0]
        for loser in (g for g in ids if g != survivor):
            for table, column in children:
                # nosemgrep: python.sqlalchemy.security.sqlalchemy-execute-raw-query
                # Identifiers only, each checked against the schema
                # allowlist; both values are bound.
                sql = ("UPDATE " + ident(table, allowed) + " SET "
                       + ident(column, allowed) + "=? WHERE "
                       + ident(column, allowed) + "=?")
                cur.execute(sql, (survivor, loser))
            cur.execute("DELETE FROM games WHERE id=?", (loser,))

    if apply:
        con.commit()
        for table, column in children:
            # nosemgrep: python.sqlalchemy.security.sqlalchemy-execute-raw-query
            # Identifiers only, each checked against the schema allowlist.
            sql = ("SELECT COUNT(*) FROM " + ident(table, allowed) + " x "
                   "LEFT JOIN games g ON g.id = x." + ident(column, allowed)
                   + " WHERE x." + ident(column, allowed)
                   + " IS NOT NULL AND g.id IS NULL")
            orphan = cur.execute(sql).fetchone()[0]
            assert orphan == 0, f"{orphan} orphaned rows in {table}"

    if skipped:
        print()
        print(f"  {len(skipped)} duplicate fixtures left alone "
              f"(not exactly one final row):")
        for gdate, ids, n in skipped[:10]:
            print(f"     {gdate} ids={ids} final_rows={n}")

    return {"resolved": resolved, "skipped": skipped}


def _repoint_and_delete(cur, allowed, children, survivor: int, loser: int) -> None:
    """Move every child row from ``loser`` onto ``survivor``, then drop it."""
    for table, column in children:
        # nosemgrep: python.sqlalchemy.security.sqlalchemy-execute-raw-query
        # Identifiers only, each checked against the schema allowlist;
        # both values are bound.
        sql = ("UPDATE " + ident(table, allowed) + " SET "
               + ident(column, allowed) + "=? WHERE "
               + ident(column, allowed) + "=?")
        cur.execute(sql, (survivor, loser))
    cur.execute("DELETE FROM games WHERE id=?", (loser,))


def resolve_offset_twins(con: sqlite3.Connection, sport: str, *,
                         apply: bool) -> dict:
    """Merge fixtures that exist twice a day apart (the plan-014 shape).

    ESPN timestamps are UTC, so an evening tip is filed under the next day.
    Before ``espn_id`` existed, rows were identified by (date, teams), and the
    same fixture could be inserted twice: the real one, and an empty twin
    dated a day later.

    The signature is deliberately narrow, because teams really do play each
    other on consecutive days. All of these must hold:

      * same sport and same two teams, exactly one day apart;
      * exactly one row is final AND carries an espn_id;
      * the other has no score, no espn_id and is not final.

    Production holds 47 same-team pairs a day apart; only 2 match this
    signature. The other 45 are genuine back-to-backs and mma cards, and are
    left alone. A looser rule would silently delete real games.
    """
    cur = con.cursor()
    allowed = schema_names(cur)
    children = fk_children(cur, "games")

    by_match: dict[tuple, list] = {}
    for row in cur.execute(
        "SELECT id, date, home_team_id, away_team_id, status, home_score, "
        "espn_id FROM games WHERE sport=? ORDER BY date", (sport,)
    ).fetchall():
        by_match.setdefault((row[2], row[3]), []).append(row)

    pairs = []
    for games in by_match.values():
        games.sort(key=lambda r: r[1])
        for a, b in zip(games, games[1:]):
            da = date.fromisoformat(str(a[1])[:10])
            db_ = date.fromisoformat(str(b[1])[:10])
            if (db_ - da) != timedelta(days=1):
                continue
            finals = [g for g in (a, b) if g[4] == "final" and g[6] is not None]
            empties = [g for g in (a, b)
                       if g[4] != "final" and g[5] is None and g[6] is None]
            if len(finals) == 1 and len(empties) == 1:
                pairs.append((finals[0], empties[0]))

    for survivor, twin in pairs:
        print(f"  twin {twin[0]} ({twin[1]}) -> {survivor[0]} "
              f"({survivor[1]}, espn={survivor[6]})")
        if apply:
            _repoint_and_delete(cur, allowed, children, survivor[0], twin[0])

    if apply and pairs:
        con.commit()

    return {"resolved": len(pairs)}


def repair(con: sqlite3.Connection, sport: str, *, apply: bool) -> dict:
    if sport not in ABBREVIATION_SPORTS:
        raise ValueError(f"{sport} identifies teams by name, not abbreviation")
    cur = con.cursor()
    allowed = schema_names(cur)
    children = fk_children(cur, "teams")
    buckets = classify(cur, sport)

    print(f"{'APPLY' if apply else 'DRY RUN'} -- sport={sport}")
    print("  team child columns:", ", ".join(f"{t}.{c}" for t, c in children))
    for name, plans in buckets.items():
        print(f"  {name:<11} {len(plans):>3} rows, "
              f"{sum(p.games for p in plans):>3} game references")

    if apply:
        for p in buckets["merge"]:
            for table, column in children:
                # nosemgrep: python.sqlalchemy.security.sqlalchemy-execute-raw-query
                # Only the table/column IDENTIFIERS are interpolated, each
                # checked against the schema allowlist; both values are bound.
                sql = ("UPDATE " + ident(table, allowed) + " SET "
                       + ident(column, allowed) + "=? WHERE "
                       + ident(column, allowed) + "=?")
                cur.execute(sql, (p.target_team_id, p.team_id))
            cur.execute("DELETE FROM teams WHERE id=?", (p.team_id,))
        for p in buckets["rename"]:
            cur.execute("UPDATE teams SET abbreviation=? WHERE id=?",
                        (p.target_abbr, p.team_id))
        con.commit()

        abbrs = [r[0] for r in cur.execute(
            "SELECT abbreviation FROM teams WHERE sport=?", (sport,))]
        assert len(abbrs) == len(set(abbrs)), "abbreviations not unique after repair"
        for table, column in children:
            # nosemgrep: python.sqlalchemy.security.sqlalchemy-execute-raw-query
            # Identifiers only, each checked against the schema allowlist.
            sql = ("SELECT COUNT(*) FROM " + ident(table, allowed) + " x "
                   "LEFT JOIN teams t ON t.id = x." + ident(column, allowed)
                   + " WHERE x." + ident(column, allowed)
                   + " IS NOT NULL AND t.id IS NULL")
            orphan = cur.execute(sql).fetchone()[0]
            assert orphan == 0, f"{orphan} orphaned rows in {table}"

    # Duplicate fixtures only become visible once the team rows collapse:
    # while the two schools have separate ids the rows look like different
    # fixtures, so a dry run genuinely cannot find them.
    dupes = _duplicate_games(cur, sport) if apply else []
    if dupes:
        print()
        print(f"  {len(dupes)} fixtures are now held by more than one game row.")
        print("  Merging teams revealed twins that the separate ids hid.")

    if apply:
        dup_result = resolve_duplicate_games(con, sport, apply=True)
        if dup_result["resolved"]:
            print(f"  resolved {dup_result['resolved']}, keeping the final row.")
        # Same-DATE duplicates are not the only kind the merge reveals: an
        # evening tip filed under ESPN's UTC date produces a twin one day
        # later. Both only become visible once the team ids collapse.
        twin_result = resolve_offset_twins(con, sport, apply=True)
        if twin_result["resolved"]:
            print(f"  resolved {twin_result['resolved']} date-offset twins.")
    else:
        dup_result = {"resolved": 0, "skipped": []}
        twin_result = {"resolved": 0}
        print()
        print("  Duplicate fixtures cannot be counted until the merge has run;")
        print("  --apply will report and resolve them.")

    return {
        "buckets": buckets,
        "duplicate_games": dupes,
        "duplicates_resolved": dup_result["resolved"],
        "duplicates_skipped": dup_result["skipped"],
        "offset_twins_resolved": twin_result["resolved"],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--sport", default="ncaab")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    con = sqlite3.connect(args.db)
    con.execute("PRAGMA foreign_keys = ON")
    repair(con, args.sport, apply=args.apply)
    if not args.apply:
        print("\n  (dry run -- nothing written; re-run with --apply)")
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

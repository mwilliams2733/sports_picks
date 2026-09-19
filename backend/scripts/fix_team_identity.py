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

    dupes = _duplicate_games(cur, sport) if apply else []
    if dupes:
        print(f"\n  !! {len(dupes)} fixtures are now held by more than one game row.")
        print("     Merging teams revealed twins that the separate ids hid.")
        for row in dupes[:10]:
            print(f"     {row[1]} teams={row[2]}/{row[3]} game ids={row[5]}")
        print("     Resolve these before finalising: the FINAL row survives.")

    return {"buckets": buckets, "duplicate_games": dupes}


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

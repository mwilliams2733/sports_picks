"""Audit how combat-sport picks were generated, priced and graded.

Why this exists
---------------
The review brief (2026-09-28, workstream D) found MMA at +30.91u on 52
graded picks and suspected a grading bug. This script puts every graded
combat pick on one CSV row a human can check in half an hour, and flags
the specific ways a combat pick can be wrong:

``OU_ON_COMBAT``
    A total graded on a combat bout. Our "score" for a bout is a 0/1 pair
    (winner 1, loser 0), so ``home_score + away_score`` is always 1 and
    ``grade_pick`` settles any "Over x" with x < 1 as a win whatever
    happened in the fight.
``NON_COMBAT_STRATEGY``
    The pick came from a strategy other than the combat model -- e.g. the
    team-sport ``ensemble`` run against fighters.
``PRICE_OUTSIDE_RANGE``
    ``odds_at_pick`` lies outside the range of prices the books showed for
    the side picked. Picks are priced at the cross-book consensus, so an
    exact match to one quote is not expected; leaving the range is.
``PRICE_MATCHES_OPPONENT``
    ``odds_at_pick`` lies outside the picked side's range but inside the
    opponent's -- the signature of a side/price swap.
``RESULT_DISAGREES``
    Re-grading with the project's own ``grade_pick`` gives a different
    result from the one stored.
``PAYOUT_DISAGREES``
    The stored payout differs from ``payout_for(result, odds_at_pick)``.
``NO_SNAPSHOTS``
    Nothing to check the price against.

Read-only: the database is opened ``mode=ro``. It never grades, writes or
deletes anything.

    python -m backend.scripts.audit_combat_grading --db <abs path> --out mma_audit.csv
"""
from __future__ import annotations

import argparse
import collections
import csv
import sqlite3
import sys
from dataclasses import dataclass, field

from backend.pipeline.grader import grade_pick, payout_for

COMBAT_STRATEGY_NAMES = {"combat_sports", "combat"}

FIELDS = [
    "pick_id", "date", "game_id", "home", "away", "home_score", "away_score",
    "winner", "pick_type", "pick_value", "picked", "odds_at_pick", "strategy",
    "own_prices", "opp_prices", "result", "payout", "regraded", "flags",
]


@dataclass
class AuditRow:
    values: dict
    flags: list[str] = field(default_factory=list)


def _connect(db_path: str) -> sqlite3.Connection:
    uri = "file:" + db_path.replace("\\", "/") + "?mode=ro"
    return sqlite3.connect(uri, uri=True)


def _side(pick_type: str, pick_value: str) -> str | None:
    head = pick_value.split(" ", 1)[0]
    if pick_type in ("moneyline", "spread") and head in ("HOME", "AWAY"):
        return head
    return None


def _series(conn, game_id: int) -> tuple[set[int], set[int]]:
    """Every distinct home and away moneyline the market showed for a game,
    from the append-only snapshots plus the current odds rows."""
    home, away = set(), set()
    queries = (
        "SELECT moneyline_home, moneyline_away FROM line_snapshots WHERE game_id = ?",
        "SELECT moneyline_home, moneyline_away FROM odds WHERE game_id = ?",
    )
    for sql in queries:
        for h, a in conn.execute(sql, (game_id,)):
            if h is not None:
                home.add(int(h))
            if a is not None:
                away.add(int(a))
    return home, away


def audit(conn, sport: str = "mma") -> list[AuditRow]:
    rows = conn.execute("""
        SELECT p.id, g.date, g.id, ht.name, at.name, g.home_score, g.away_score,
               p.pick_type, p.pick_value, p.odds_at_pick, s.name, r.result, r.payout
        FROM picks p
        JOIN pick_results r ON r.pick_id = p.id
        JOIN games g ON g.id = p.game_id
        JOIN teams ht ON ht.id = g.home_team_id
        JOIN teams at ON at.id = g.away_team_id
        LEFT JOIN strategies s ON s.id = p.strategy_id
        WHERE g.sport = ?
        ORDER BY g.date, p.id""", (sport,)).fetchall()

    out = []
    for (pid, day, gid, home, away, hs, as_, ptype, pval, odds, strat,
         result, payout) in rows:
        winner = (home if hs > as_ else away if as_ > hs else "draw") \
            if hs is not None and as_ is not None else ""
        side = _side(ptype, pval)
        picked = {"HOME": home, "AWAY": away}.get(side, "")
        home_px, away_px = _series(conn, gid)
        own = home_px if side == "HOME" else away_px if side == "AWAY" else set()
        opp = away_px if side == "HOME" else home_px if side == "AWAY" else set()

        regraded = ""
        if hs is not None and as_ is not None and odds is not None:
            graded = grade_pick(ptype, pval, hs, as_, odds)
            regraded = graded[0] if graded else ""

        row = AuditRow(values={
            "pick_id": pid, "date": day, "game_id": gid, "home": home, "away": away,
            "home_score": hs, "away_score": as_, "winner": winner,
            "pick_type": ptype, "pick_value": pval, "picked": picked,
            "odds_at_pick": odds, "strategy": strat or "",
            "own_prices": " ".join(str(x) for x in sorted(own)),
            "opp_prices": " ".join(str(x) for x in sorted(opp)),
            "result": result, "payout": round(payout, 4) if payout is not None else "",
            "regraded": regraded,
        })
        if ptype == "over_under":
            row.flags.append("OU_ON_COMBAT")
        if (strat or "") not in COMBAT_STRATEGY_NAMES:
            row.flags.append("NON_COMBAT_STRATEGY")
        if side and odds is not None:
            # A pick is priced at the CONSENSUS across books (average_odds),
            # so it rarely equals any single quote -- but it can never fall
            # outside the range the books showed for that side.
            if not own and not opp:
                row.flags.append("NO_SNAPSHOTS")
            elif not own or not (min(own) <= odds <= max(own)):
                row.flags.append("PRICE_OUTSIDE_RANGE")
                if opp and min(opp) <= odds <= max(opp):
                    row.flags.append("PRICE_MATCHES_OPPONENT")
        if regraded and regraded != result:
            row.flags.append("RESULT_DISAGREES")
        if result in ("win", "loss", "push") and odds is not None and payout is not None \
                and abs(payout - payout_for(result, odds)) > 1e-6:
            row.flags.append("PAYOUT_DISAGREES")
        out.append(row)
    return out


def summarize(rows: list[AuditRow]) -> list[str]:
    by_type = collections.defaultdict(lambda: [0, 0, 0, 0.0])   # w, l, p, units
    flag_counts = collections.Counter()
    for r in rows:
        t = by_type[r.values["pick_type"]]
        res = r.values["result"]
        t[0 if res == "win" else 1 if res == "loss" else 2] += 1
        t[3] += r.values["payout"] or 0.0
        flag_counts.update(r.flags)
    lines = [f"graded picks: {len(rows)}   distinct dates: "
             f"{len({r.values['date'] for r in rows})}   distinct bouts: "
             f"{len({r.values['game_id'] for r in rows})}"]
    for ptype, (w, l, p, u) in sorted(by_type.items()):
        lines.append(f"  {ptype:<11} {w}-{l}-{p}   units {u:+.2f}")
    clean = [r for r in rows if not r.flags or r.flags == ["NON_COMBAT_STRATEGY"]]
    cw = sum(r.values["result"] == "win" for r in clean)
    cl = sum(r.values["result"] == "loss" for r in clean)
    cu = sum(r.values["payout"] or 0.0 for r in clean)
    lines.append(f"  unflagged apart from strategy: {cw}-{cl}   units {cu:+.2f}")
    lines.append("flags: " + (", ".join(f"{k} {v}" for k, v in flag_counts.most_common())
                              or "none"))
    return lines


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--db", required=True, help="Path to the database (opened read-only).")
    ap.add_argument("--sport", default="mma")
    ap.add_argument("--out", help="Write the per-pick CSV here.")
    args = ap.parse_args(argv)

    conn = _connect(args.db)
    try:
        rows = audit(conn, args.sport)
    finally:
        conn.close()
    if args.out:
        with open(args.out, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=FIELDS)
            w.writeheader()
            for r in rows:
                w.writerow({**r.values, "flags": " ".join(r.flags)})
    print("\n".join(summarize(rows)))
    return 0


if __name__ == "__main__":
    sys.exit(main())

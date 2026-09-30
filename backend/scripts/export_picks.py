"""Export every stored pick to CSV, for offline analysis by a data scientist.

Read-only: the database is opened ``mode=ro`` (the same URI pattern as
``backend/scripts/audit_combat_grading.py``), so this script cannot write to
-- or be blamed for corrupting -- the live database. It never grades,
selects or sends anything; it only reads and writes CSV files.

Writes three files into ``--out``:

``picks.csv``
    One row per stored pick (game AND prop, graded AND ungraded).
``line_history.csv``
    One row per ``line_snapshots`` row (the append-only price series).
``manifest.txt``
    When the export ran, the db path, the git HEAD, row counts per file and
    sport, the filters applied, and a pointer to ``docs/data-dictionary.md``.

Excluded on purpose: ``paper_picks`` and ``user_profiles``. Both are private
data about the owner and the people using the paper-trading feature, not
data about the model, and are not needed for model analysis. See the
manifest for the same note.

    python -m backend.scripts.export_picks --db <abs path> --out <dir> \\
        [--since YYYY-MM-DD] [--sport nfl ...]
"""
from __future__ import annotations

import argparse
import csv
import os
import re
import sqlite3
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone

from backend.analysis.odds_utils import (
    InvalidOddsError,
    american_to_implied_prob,
    compute_pick_clv,
    parse_pick_line,
)
from backend.analysis.line_snapshots import depth_from_pairs
from backend.digest.render import _prop_label, _selection_label
from backend.digest.selector import DigestPick

PICKS_FIELDS = [
    "pick_id", "created_at",
    "game_id", "sport", "season", "game_date", "start_time_utc", "game_status",
    "home_team", "away_team",
    "home_score", "away_score",
    "strategy",
    "pick_type", "pick_value", "pick_label",
    "side", "line",
    "odds_at_pick", "implied_prob_raw", "model_prob", "edge_pct",
    "market_prob_novig",
    "confidence", "suggested_unit_size",
    "factors",
    "prop_player", "prop_market",
    "result", "payout",
    "odds_at_close", "line_at_close",
    "clv_price_pp", "clv_line_pts",
    "series_depth",
    "odds_reconstructed",
    "emailed", "emailed_odds", "emailed_at",
    "emailed_pick_value", "emailed_digest_date", "emailed_confidence",
]

LINE_HISTORY_FIELDS = [
    "game_id", "sport", "home_team", "away_team", "game_date", "start_time_utc",
    "bookmaker",
    "moneyline_home", "moneyline_away",
    "spread_home", "spread_away",
    "over_under",
    "spread_home_price", "spread_away_price",
    "over_price", "under_price",
    "captured_at", "last_seen_at", "series_depth",
]


def _connect_ro(db_path: str) -> sqlite3.Connection:
    """Open `db_path` read-only. Raises if the path does not exist -- a
    missing --db must be a loud, immediate error, not a silent empty export
    (sqlite3 with mode=ro would otherwise raise a less obvious error deep
    inside the first query, or -- worse, with a typo'd directory -- create
    nothing and succeed)."""
    if not os.path.isfile(db_path):
        raise FileNotFoundError(f"--db path does not exist or is not a file: {db_path}")
    uri = "file:" + db_path.replace("\\", "/") + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _side(pick_type: str, pick_value: str) -> str | None:
    """HOME / AWAY / Over / Under / prop outcome, or None if unparseable.

    Mirrors the parsing `backend/digest/render.py::_selection_label` and
    `backend/scripts/audit_combat_grading.py::_side` already do on the same
    stored strings -- not a fourth definition, just applied uniformly here.
    """
    if pick_type == "prop":
        # Prop pick_value looks like "Player Over 10.5 Market" or a bare
        # "Yes" for an anytime-TD outcome (see docs/data-dictionary.md).
        m = re.search(r"\b(Over|Under)\b", pick_value)
        if m:
            return m.group(1)
        if pick_value.strip().lower() in ("yes", "no"):
            return pick_value.strip().capitalize()
        return None
    head = pick_value.split(" ", 1)[0]
    if head in ("HOME", "AWAY"):
        return head
    if head in ("Over", "Under"):
        return head
    return None


def _pick_label(pick_type: str, pick_value: str, sport: str,
                home_name: str, away_name: str) -> str:
    """Team-resolved display label, reusing the digest's own resolvers
    (`_selection_label` for game picks, `_prop_label` for props) rather than
    writing a third label function that could drift from what the email or
    the frontend actually show."""
    if pick_type == "prop":
        return _prop_label(pick_value)
    stub = DigestPick(sport=sport, matchup="", pick_value=pick_value, odds=0,
                      confidence=0, edge_pct=0.0, rationale="",
                      home_team=home_name, away_team=away_name)
    return _selection_label(stub)


def _iso_z(raw) -> str:
    """Normalize a stored timestamp to ISO-8601 with a `Z` suffix.

    Every datetime column in this schema (`created_at`, `start_time`,
    `captured_at`, `last_seen_at`, `sent_at`) is written naive by SQLAlchemy
    as `"YYYY-MM-DD HH:MM:SS[.ffffff]"`, and the project convention is that
    every naive stored datetime IS UTC (see `backend/time_utils.py`'s
    `game_start_utc`, which does exactly this normalization for
    `start_time`). This does the same thing for every timestamp column in
    the export, so a reader never has to guess a timezone or reimplement
    that convention. Blank in, blank out. An already-aware value (e.g. one
    read back through the ORM rather than raw sqlite3) is converted to UTC
    first rather than assumed.
    """
    if raw is None or raw == "":
        return ""
    if isinstance(raw, datetime):
        dt = raw
    else:
        dt = datetime.fromisoformat(str(raw).replace(" ", "T"))
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt.isoformat() + "Z"


def _implied_prob_raw(odds: int | None) -> float | None:
    if odds is None:
        return None
    try:
        return american_to_implied_prob(odds)
    except InvalidOddsError:
        return None


def _market_prob_novig(pick_type: str, model_prob: float | None,
                       edge_pct: float | None) -> float | None:
    """The fair (no-vig) market probability the pick's edge was measured
    against.

    - moneyline: back out of edge_pct's own definition (edge_pct = (model -
      market) * 100, see backend/analysis/variants/ensemble.py:284-285/
      292-293), so market = model - edge_pct/100. Arithmetic on the two
      stored columns, not an independent re-derivation -- but both are
      themselves rounded before storage (edge_pct to 0.1 percentage points,
      model_prob to 4 decimal places), so this is accurate to within about
      +/-0.0005 of what the strategy computed internally, not bit-exact.
    - spread / over_under: edge_pct is measured against a flat 0.5 (the
      fair coin-flip value the model used for a line pick), not a de-vigged
      market probability -- there is no market-side win probability stored
      for a spread/total pick. 0.5 is the documented convention, not a
      computed value, and is written only when `model_prob` is itself
      present -- a pick with no stored `model_prob` (all 141 legacy
      spread/over_under picks from before 2026-09-17; see
      docs/data-dictionary.md's known traps) gets a blank here rather than
      a fabricated 0.5, since 0.5 documents what a REAL pick's edge was
      measured against and a legacy row's edge was not necessarily measured
      against anything at all.
    - prop: props are never de-vigged against a market probability at all
      (edge_pct is a stat-unit gap -- see docs/data-dictionary.md's known
      traps). Blank.
    """
    if pick_type == "prop":
        return None
    if pick_type == "moneyline":
        if model_prob is None or edge_pct is None:
            return None
        return model_prob - edge_pct / 100.0
    if pick_type in ("spread", "over_under"):
        return 0.5 if model_prob is not None else None
    return None


def _factors(rationale_json: str | None) -> str:
    """The rationale's factor codes, `;`-joined as "code:side:strength".

    Malformed or absent rationale degrades to "", same as the digest's own
    `_rationale_for` degrades to an empty rationale string rather than
    raising -- a pick with unreadable rationale is still a pick.
    """
    if not rationale_json:
        return ""
    import json
    try:
        raw = json.loads(rationale_json)
    except (ValueError, TypeError):
        return ""
    if not isinstance(raw, list):
        return ""
    parts = []
    for f in raw:
        if not isinstance(f, dict):
            continue
        code = f.get("code", "")
        side = f.get("side", "")
        strength = f.get("strength", "")
        if code:
            parts.append(f"{code}:{side}:{strength}")
    return ";".join(parts)


def _git_head(repo_root: str) -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo_root,
            capture_output=True, text=True, check=True, timeout=10)
        return out.stdout.strip()
    except Exception as e:
        return f"(unavailable: {type(e).__name__})"


def _series_depth(conn: sqlite3.Connection) -> dict[int, int]:
    """game_id -> the most line_snapshots rows any single bookmaker has on
    it, via `line_snapshots.depth_from_pairs` -- the same definition
    `series_depth` uses, fed from a raw mode=ro connection because this
    script never opens a SQLAlchemy session.

    1 means no book was ever seen to change its price for that game -- the
    "closing" price on record is the same observation as the opening one,
    not a real close. `clv_report.measurable()` drops these; this column
    lets the export reproduce that same population instead of treating
    every row with a non-blank clv_* column as equally trustworthy.
    """
    return depth_from_pairs(
        conn.execute("SELECT game_id, bookmaker FROM line_snapshots"))


def export(conn: sqlite3.Connection, out_dir: str, *,
          since: str | None = None, sports: list[str] | None = None) -> dict:
    """Write picks.csv and line_history.csv into `out_dir`. Returns a dict
    of counts used to build the manifest.

    Every query below is a fixed string with no interpolated SQL -- `since`
    and `sports` are applied as a Python-side filter after a full read,
    rather than built into a dynamic WHERE/IN clause. This table is small
    enough that a full read costs nothing, and it keeps every query here
    literal and reviewable in one place rather than assembled per-call.
    """
    os.makedirs(out_dir, exist_ok=True)
    sports_set = set(sports) if sports else None

    all_pick_rows = conn.execute("""
        SELECT p.id AS pick_id, p.created_at, p.game_id, p.strategy_id,
               p.pick_type, p.pick_value, p.confidence, p.edge_pct,
               p.odds_at_pick, p.model_prob, p.suggested_unit_size,
               p.rationale_json, p.prop_player, p.prop_market,
               p.odds_reconstructed,
               g.sport, g.season, g.date AS game_date, g.start_time,
               g.status AS game_status, g.home_score, g.away_score,
               ht.name AS home_name, at.name AS away_name,
               s.name AS strategy_name
        FROM picks p
        JOIN games g ON g.id = p.game_id
        JOIN teams ht ON ht.id = g.home_team_id
        JOIN teams at ON at.id = g.away_team_id
        LEFT JOIN strategies s ON s.id = p.strategy_id
        ORDER BY p.id
    """).fetchall()

    def _keep(r) -> bool:
        if since is not None and r["game_date"] < since:
            return False
        if sports_set is not None and r["sport"] not in sports_set:
            return False
        return True

    pick_rows = [r for r in all_pick_rows if _keep(r)]
    pick_id_set = {r["pick_id"] for r in pick_rows}

    # pick_results and emailed_picks are fetched separately (full read, then
    # filtered to pick_id_set in Python) and joined in Python: neither table
    # enforces one-row-per-pick (no unique constraint on
    # pick_results.pick_id; emailed_picks is unique per (digest_date,
    # pick_id) but a pick can be re-emailed on a later date), so a SQL JOIN
    # against either could silently multiply picks.csv's rows. "First seen"
    # (lowest id) is used for pick_results, an arbitrary but deterministic
    # tie-break -- a pick should only ever have one result row in practice.
    # emailed_picks instead keeps the LATEST emailing (highest sent_at) when
    # a pick was emailed on more than one digest date: a data scientist
    # asking "was this emailed, and what did the reader see" almost always
    # wants the most recent send, not the first. Both choices are documented
    # in docs/data-dictionary.md.
    results_by_pick: dict[int, sqlite3.Row] = {}
    for row in conn.execute("SELECT * FROM pick_results ORDER BY id"):
        if row["pick_id"] in pick_id_set:
            results_by_pick.setdefault(row["pick_id"], row)

    emailed_by_pick: dict[int, sqlite3.Row] = {}
    for row in conn.execute("SELECT * FROM emailed_picks ORDER BY sent_at"):
        if row["pick_id"] in pick_id_set:
            emailed_by_pick[row["pick_id"]] = row  # last write wins: latest sent_at

    depth_by_game = _series_depth(conn)

    picks_path = os.path.join(out_dir, "picks.csv")
    sport_counts: Counter = Counter()
    with open(picks_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=PICKS_FIELDS)
        w.writeheader()
        for r in pick_rows:
            sport_counts[r["sport"]] += 1
            result_row = results_by_pick.get(r["pick_id"])
            emailed_row = emailed_by_pick.get(r["pick_id"])

            clv_pct, clv_points = (None, None)
            if result_row is not None:
                clv_pct, clv_points = compute_pick_clv(
                    r["pick_type"], r["pick_value"], r["odds_at_pick"],
                    result_row["odds_at_close"], result_row["line_at_close"])

            w.writerow({
                "pick_id": r["pick_id"],
                "created_at": _iso_z(r["created_at"]),
                "game_id": r["game_id"],
                "sport": r["sport"],
                "season": r["season"],
                "game_date": r["game_date"],
                "start_time_utc": _iso_z(r["start_time"]),
                "game_status": r["game_status"],
                "home_team": r["home_name"],
                "away_team": r["away_name"],
                "home_score": r["home_score"],
                "away_score": r["away_score"],
                "strategy": r["strategy_name"] or "",
                "pick_type": r["pick_type"],
                "pick_value": r["pick_value"],
                "pick_label": _pick_label(r["pick_type"], r["pick_value"], r["sport"],
                                          r["home_name"], r["away_name"]),
                "side": _side(r["pick_type"], r["pick_value"]) or "",
                "line": parse_pick_line(r["pick_value"]) if r["pick_type"] != "moneyline" else "",
                "odds_at_pick": r["odds_at_pick"],
                "implied_prob_raw": _implied_prob_raw(r["odds_at_pick"]),
                "model_prob": r["model_prob"],
                "edge_pct": r["edge_pct"],
                "market_prob_novig": _market_prob_novig(
                    r["pick_type"], r["model_prob"], r["edge_pct"]),
                "confidence": r["confidence"],
                "suggested_unit_size": r["suggested_unit_size"],
                "factors": _factors(r["rationale_json"]),
                "prop_player": r["prop_player"] or "",
                "prop_market": r["prop_market"] or "",
                "result": result_row["result"] if result_row else "",
                "payout": result_row["payout"] if result_row else "",
                "odds_at_close": result_row["odds_at_close"] if result_row else "",
                "line_at_close": result_row["line_at_close"] if result_row else "",
                "clv_price_pp": clv_pct if clv_pct is not None else "",
                "clv_line_pts": clv_points if clv_points is not None else "",
                "series_depth": depth_by_game.get(r["game_id"], 0),
                "odds_reconstructed": bool(r["odds_reconstructed"]),
                "emailed": emailed_row is not None,
                "emailed_odds": emailed_row["odds"] if emailed_row else "",
                "emailed_at": _iso_z(emailed_row["sent_at"]) if emailed_row else "",
                "emailed_pick_value": emailed_row["pick_value"] if emailed_row else "",
                "emailed_digest_date": emailed_row["digest_date"] if emailed_row else "",
                "emailed_confidence": (
                    emailed_row["confidence"] if emailed_row and emailed_row["confidence"] is not None
                    else ""
                ),
            })

    all_line_rows = conn.execute("""
        SELECT ls.game_id, g.sport, g.date AS game_date, g.start_time,
               ht.name AS home_name, at.name AS away_name,
               ls.bookmaker,
               ls.moneyline_home, ls.moneyline_away,
               ls.spread_home, ls.spread_away, ls.over_under,
               ls.spread_home_price, ls.spread_away_price,
               ls.over_price, ls.under_price,
               ls.captured_at, ls.last_seen_at
        FROM line_snapshots ls
        JOIN games g ON g.id = ls.game_id
        JOIN teams ht ON ht.id = g.home_team_id
        JOIN teams at ON at.id = g.away_team_id
        ORDER BY ls.game_id, ls.bookmaker, ls.captured_at
    """).fetchall()
    line_rows = [r for r in all_line_rows if _keep(r)]

    line_history_path = os.path.join(out_dir, "line_history.csv")
    with open(line_history_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=LINE_HISTORY_FIELDS)
        w.writeheader()
        for r in line_rows:
            w.writerow({
                "game_id": r["game_id"], "sport": r["sport"],
                "home_team": r["home_name"], "away_team": r["away_name"],
                "game_date": r["game_date"],
                "start_time_utc": _iso_z(r["start_time"]),
                "bookmaker": r["bookmaker"],
                "moneyline_home": r["moneyline_home"],
                "moneyline_away": r["moneyline_away"],
                "spread_home": r["spread_home"], "spread_away": r["spread_away"],
                "over_under": r["over_under"],
                "spread_home_price": r["spread_home_price"],
                "spread_away_price": r["spread_away_price"],
                "over_price": r["over_price"], "under_price": r["under_price"],
                "captured_at": _iso_z(r["captured_at"]),
                "last_seen_at": _iso_z(r["last_seen_at"]),
                "series_depth": depth_by_game.get(r["game_id"], 0),
            })

    return {
        "picks_rows": len(pick_rows),
        "picks_path": picks_path,
        "line_history_rows": len(line_rows),
        "line_history_path": line_history_path,
        "sport_counts": dict(sport_counts),
    }


def write_manifest(out_dir: str, *, db_path: str, repo_root: str,
                   since: str | None, sports: list[str] | None,
                   counts: dict) -> str:
    manifest_path = os.path.join(out_dir, "manifest.txt")
    # ISO-8601 UTC with a Z suffix, same convention as every timestamp
    # column in the two CSVs (see `_iso_z`).
    now = datetime.now(tz=timezone.utc).replace(tzinfo=None).isoformat() + "Z"
    lines = [
        f"sports_picks pick export -- {now}",
        f"db path: {db_path}",
        f"git HEAD: {_git_head(repo_root)}",
        "",
        "filters applied:",
        f"  --since: {since or '(none -- all history)'}",
        f"  --sport: {', '.join(sports) if sports else '(none -- all sports)'}",
        "",
        f"picks.csv: {counts['picks_rows']} rows",
    ]
    for sport, n in sorted(counts["sport_counts"].items()):
        lines.append(f"  {sport}: {n}")
    lines += [
        f"line_history.csv: {counts['line_history_rows']} rows",
        "",
        "Excluded on purpose: paper_picks and user_profiles. Both are "
        "private data about the owner and paper-trading users, not data "
        "about the model, and are not needed for model analysis.",
        "",
        "See docs/data-dictionary.md for column definitions, grading and "
        "CLV conventions, and known traps in this data.",
    ]
    text = "\n".join(lines) + "\n"
    with open(manifest_path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return manifest_path


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", required=True, help="Path to the database (opened read-only).")
    ap.add_argument("--out", required=True, help="Directory to write the CSVs and manifest into.")
    ap.add_argument("--since", help="ISO date (YYYY-MM-DD). Only games on or after this date.")
    ap.add_argument("--sport", dest="sports", action="append",
                    help="Restrict to this sport. Repeatable.")
    args = ap.parse_args(argv)

    repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    try:
        conn = _connect_ro(args.db)
    except FileNotFoundError as e:
        print(str(e), file=sys.stderr)
        return 1

    try:
        counts = export(conn, args.out, since=args.since, sports=args.sports)
    finally:
        conn.close()

    manifest_path = write_manifest(
        args.out, db_path=args.db, repo_root=repo_root,
        since=args.since, sports=args.sports, counts=counts)

    print(f"wrote {counts['picks_rows']} picks -> {counts['picks_path']}")
    print(f"wrote {counts['line_history_rows']} line snapshots -> {counts['line_history_path']}")
    print(f"wrote manifest -> {manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

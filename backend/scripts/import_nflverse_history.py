"""Import NFL history and closing lines from an nflverse export.

Why
---
This database held 48 nfl games, 2026-09-09 to 2026-09-28, 31 of them final.
That is the whole nfl record the model trains on, against 1,253 nba games.
It is why nfl's home-advantage slot is fitted from 30 hosted games, why
`calibration_report`'s `MIN_EVAL_GAMES = 200` means nfl can never be scored
at all, and why every nfl conclusion here has rested on single digits.

nflverse publishes 1,411 games over 2022-2026, 1,171 with BOTH a result and
a closing moneyline. On that sample the closing line is well calibrated in
every band -- 0.0-0.2 predicts 16.3% and observes 15.4%, 0.8-1.0 predicts
85.3% and observes 89.3% -- which is the null `market_shrinkage` rests on
and previously could not resolve at n=209 across all sports.

Input
-----
A CSV exported from nflverse, so this script has no nflreadpy/duckdb
dependency and the same file reproduces the same import:

    pip install nflreadpy duckdb
    python -c "import nflreadpy as nfl, duckdb; \\
        con=duckdb.connect('nfl.duckdb'); \\
        con.register('g', nfl.load_schedules([2022,2023,2024,2025,2026]).to_arrow()); \\
        con.execute(\\"COPY (SELECT game_id,season,week,game_type,gameday,away_team,\\
        home_team,away_score,home_score,result,roof,spread_line,home_spread_odds,\\
        away_spread_odds,total_line,over_odds,under_odds,home_moneyline,\\
        away_moneyline FROM g) TO 'nflverse_games.csv' (HEADER)\\")"

    python -m backend.scripts.import_nflverse_history --db <abs path> --csv nflverse_games.csv
    python -m backend.scripts.import_nflverse_history --db <abs path> --csv nflverse_games.csv --apply

Two decisions worth knowing
---------------------------
**Closing lines go under their own bookmaker.** `CLOSING_BOOKMAKER` labels
them instead of mixing them into the live consensus. `_average_odds` would
otherwise read a closing line as one more book's pre-game quote: harmless
for a finished game, lookahead for anything being predicted, because nobody
could have taken the close at pick time.

**An unknown abbreviation refuses.** nflverse writes `LA` and `WAS` where
this database writes `LAR` and `WSH`; the other 30 match exactly. Creating a
`Team` for an unmapped abbreviation would split a franchise's Elo history in
two, which is the failure `dedupe_combat_games` exists to clean up.
"""
from __future__ import annotations

import argparse
import csv
import logging
import os
from dataclasses import dataclass
from datetime import date

from backend.config import season_label, seasons_config
from backend.database import get_engine, get_session, run_migrations
from backend.models import Game, Odds, Team

logger = logging.getLogger(__name__)

#: nflverse abbreviation -> this database's. The only two that differ.
ABBR_FIXUPS = {"LA": "LAR", "WAS": "WSH"}

#: Bookmaker name for a closing line. Not a real book, and deliberately not
#: one: it marks these rows as post-hoc so nothing reads them as a quote
#: that was available before kickoff.
CLOSING_BOOKMAKER = "nflverse_close"

#: nflverse `game_type` -> `Game.season_type`.
SEASON_TYPES = {"REG": "regular", "POST": "postseason", "WC": "postseason",
                "DIV": "postseason", "CON": "postseason", "SB": "postseason"}


@dataclass(frozen=True)
class NflverseGame:
    """One row of the export."""
    game_id: str
    season: int
    week: int | None
    game_type: str
    gameday: date
    away_team: str
    home_team: str
    away_score: int | None
    home_score: int | None
    roof: str | None
    spread_line: float | None
    home_spread_odds: int | None
    away_spread_odds: int | None
    total_line: float | None
    over_odds: int | None
    under_odds: int | None
    home_moneyline: int | None
    away_moneyline: int | None


def _num(value, cast):
    if value in (None, "", "NA", "NULL"):
        return None
    try:
        return cast(float(value)) if cast is int else cast(value)
    except (TypeError, ValueError):
        return None


def read_csv(path: str) -> list[NflverseGame]:
    """Parse the export. A row without a date is unusable and skipped."""
    rows: list[NflverseGame] = []
    with open(path, newline="", encoding="utf-8") as handle:
        for raw in csv.DictReader(handle):
            day = raw.get("gameday")
            if not day:
                continue
            rows.append(NflverseGame(
                game_id=raw["game_id"], season=int(raw["season"]),
                week=_num(raw.get("week"), int),
                game_type=raw.get("game_type") or "REG",
                gameday=date.fromisoformat(day),
                away_team=raw["away_team"], home_team=raw["home_team"],
                away_score=_num(raw.get("away_score"), int),
                home_score=_num(raw.get("home_score"), int),
                roof=raw.get("roof"),
                spread_line=_num(raw.get("spread_line"), float),
                home_spread_odds=_num(raw.get("home_spread_odds"), int),
                away_spread_odds=_num(raw.get("away_spread_odds"), int),
                total_line=_num(raw.get("total_line"), float),
                over_odds=_num(raw.get("over_odds"), int),
                under_odds=_num(raw.get("under_odds"), int),
                home_moneyline=_num(raw.get("home_moneyline"), int),
                away_moneyline=_num(raw.get("away_moneyline"), int),
            ))
    return rows


def _team_ids(session) -> dict[str, int]:
    return {t.abbreviation: t.id
            for t in session.query(Team).filter(Team.sport == "nfl").all()}


def import_games(session, rows: list[NflverseGame], *,
                 apply: bool = False) -> dict:
    """Create or update games and their closing lines. Dry run by default."""
    seasons = seasons_config()
    summary = {"rows": len(rows), "created": 0, "matched_existing": 0,
               "odds_written": 0, "refused_unknown_team": 0,
               "unknown_abbrs": set()}
    teams = _team_ids(session)

    for row in rows:
        home = ABBR_FIXUPS.get(row.home_team, row.home_team)
        away = ABBR_FIXUPS.get(row.away_team, row.away_team)
        if home not in teams or away not in teams:
            summary["refused_unknown_team"] += 1
            summary["unknown_abbrs"].update(
                a for a in (home, away) if a not in teams)
            continue

        # The repo's own label, not `str(row.season)`. nflverse writes the
        # start year as an int; this database writes "2026-27" for a season
        # that crosses the new year. Two conventions in one column is the
        # bug `season_label` was written to end -- nba once carried four
        # labels for two seasons.
        season = season_label("nfl", row.gameday, seasons)
        played = row.home_score is not None and row.away_score is not None
        existing = (session.query(Game)
                    .filter(Game.sport == "nfl", Game.date == row.gameday,
                            Game.home_team_id == teams[home],
                            Game.away_team_id == teams[away])
                    .first())

        if existing is not None:
            summary["matched_existing"] += 1
            game = existing
            if apply and played and game.status != "final":
                game.home_score, game.away_score = row.home_score, row.away_score
                game.status = "final"
        else:
            summary["created"] += 1
            if not apply:
                continue
            game = Game(
                sport="nfl", date=row.gameday, season=season,
                status="final" if played else "scheduled",
                home_team_id=teams[home], away_team_id=teams[away],
                home_score=row.home_score, away_score=row.away_score,
                week=row.week,
                season_type=SEASON_TYPES.get(row.game_type, "unknown"),
                neutral_site=False)
            session.add(game)
            session.flush()

        if not apply:
            continue

        # An all-NULL row is not a quote. A 2026 fixture that has not been
        # priced yet simply has no closing line.
        if not any((row.home_moneyline, row.away_moneyline,
                    row.spread_line, row.total_line)):
            continue

        odds = (session.query(Odds)
                .filter(Odds.game_id == game.id,
                        Odds.bookmaker == CLOSING_BOOKMAKER).first())
        if odds is None:
            odds = Odds(game_id=game.id, bookmaker=CLOSING_BOOKMAKER)
            session.add(odds)
        odds.moneyline_home = row.home_moneyline
        odds.moneyline_away = row.away_moneyline
        # nflverse `spread_line` is POSITIVE when the home side is favoured;
        # `Odds.spread_home` is the handicap applied to the home team, so the
        # sign flips. Unflipped, every favourite prices as an underdog.
        odds.spread_home = -row.spread_line if row.spread_line is not None else None
        odds.spread_away = row.spread_line
        odds.over_under = row.total_line
        odds.spread_home_price = row.home_spread_odds
        odds.spread_away_price = row.away_spread_odds
        odds.over_price = row.over_odds
        odds.under_price = row.under_odds
        summary["odds_written"] += 1

    if apply:
        session.commit()
    summary["unknown_abbrs"] = sorted(summary["unknown_abbrs"])
    return summary


def format_summary(s: dict, apply: bool) -> str:
    lines = ["Imported nflverse history" if apply
             else "DRY RUN -- nothing written", ""]
    lines.append(f"  rows in export        : {s['rows']}")
    lines.append(f"  {'created' if apply else 'would create':<21} : {s['created']}")
    lines.append(f"  already present       : {s['matched_existing']}")
    lines.append(f"  closing lines written : {s['odds_written']}")
    if s["refused_unknown_team"]:
        lines.append(f"  REFUSED (unknown team): {s['refused_unknown_team']} "
                     f"-- {', '.join(s['unknown_abbrs'])}")
        lines.append("    A team row is never created here: a franchise under "
                     "two abbreviations")
        lines.append("    splits its Elo history. Add the mapping to "
                     "ABBR_FIXUPS instead.")
    lines.append("")
    lines.append(f"  Closing lines are stored under bookmaker "
                 f"{CLOSING_BOOKMAKER!r}, not as a")
    lines.append("  pre-game quote: nobody could have taken the close at pick "
                 "time.")
    return "\n".join(lines)


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO)
    ap = argparse.ArgumentParser(
        description="Import NFL history and closing lines. Dry run by default.")
    ap.add_argument("--db", required=True, help="Path to the database. Back it up first.")
    ap.add_argument("--csv", required=True, help="nflverse export (see module docstring).")
    ap.add_argument("--apply", action="store_true",
                    help="Actually write. Without this it is a dry run.")
    args = ap.parse_args(argv)

    for path in (args.db, args.csv):
        if path != ":memory:" and not os.path.exists(path):
            raise SystemExit(f"{path!r} does not exist.")

    engine = get_engine(args.db)
    run_migrations(engine)
    session = get_session(engine)
    try:
        rows = read_csv(args.csv)
        print(format_summary(import_games(session, rows, apply=args.apply),
                             args.apply))
    finally:
        session.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

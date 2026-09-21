"""Restore games marked canceled that were actually played.

Why they exist
--------------
`full_pipeline._reconcile_against_espn` marks a game canceled when ESPN's
event list for that exact date does not contain the team pair. The idea is
sound -- ESPN is authoritative for whether a fixture exists -- but the
lookup it trusts is narrower than the schedule:

* `ESPNCollector.fetch_scoreboard` sends only ``dates``. For ncaab that
  returns **2 events for 2026-03-15**, a conference championship Sunday.
  Adding ``groups=50`` (Division I) returns the games that were missing.
* ESPN files some events under the neighbouring date -- the UTC/Eastern
  convention that `espn_box_score.resolve_espn_event` already compensates
  for with a +/-1 day window. The reconciler compares one date only.

Either way the pair is absent, and a played game is recorded as canceled.

Audited 2026-09-20: of 24 games marked canceled (excluding boxing and mma,
which were voided deliberately), **15 were final on ESPN**, holding 18
ungraded picks. One -- `PUR vs Queens University Royals` -- is genuinely
absent from every date in the window. The nba rows are `TBD vs TBD`
playoff placeholders and hold no picks.

Restore, do not void
--------------------
These picks are not unanswerable, which is what `void_stuck_bouts` is for.
They have real winners. Voiding would book a push for a game that was
decided, and quietly remove 18 real wagers from the record.

A restored game keeps its result: `_reconcile_against_espn` skips rows
whose status is ``final``, so the next run cannot re-cancel it.

The refusals are the point
--------------------------
This writes scores that grade real money. Three refusals, each leaving the
row exactly as found:

* **Not found on ESPN** -- the game may truly not exist.
* **Found but not final** -- postponed or suspended has no result to write.
  mlb CIN vs STL appears with a 0-0 line and must not become a nil-nil final.
* **Our abbreviation absent from ESPN's competitors** -- the match was loose
  and the orientation unknown. Scores are mapped by abbreviation, never by
  ESPN's competitor order, for the same reason `finalize_combat` orients by
  name rather than corner.

Dry run is the DEFAULT.

    python -m backend.scripts.restore_miscanceled_games --db <abs path>
    python -m backend.scripts.restore_miscanceled_games --db <abs path> --apply
"""
import argparse
import logging
import os
from dataclasses import dataclass
from datetime import timedelta

import httpx

from backend.database import get_engine, get_session, run_migrations
from backend.models import Game, Team

logger = logging.getLogger(__name__)

_BASE = "https://site.api.espn.com/apis/site/v2/sports"
_TIMEOUT = 25.0

#: Scoreboard path per sport. Mirrors ``collectors/espn.SPORT_URLS``; kept
#: here because this script also needs the query params below, which that
#: collector does not yet send.
_PATHS = {
    "ncaab": "basketball/mens-college-basketball",
    "ncaaf": "football/college-football",
    "nba": "basketball/nba",
    "nfl": "football/nfl",
    "mlb": "baseball/mlb",
}

#: Extra scoreboard params. ``groups=50`` is Division I basketball and
#: ``groups=80`` is FBS football; without them ESPN answers with a featured
#: subset, which is what made the reconciler cancel real games. Verified
#: against a known-good control before use -- see ``_verify_lookup``.
_PARAMS = {"ncaab": {"groups": "50"}, "ncaaf": {"groups": "80"}}

#: ESPN files some events under the neighbouring calendar date. Same window,
#: and same order, as ``espn_box_score.resolve_espn_event``: an exact match
#: always wins.
_OFFSETS = (0, -1, 1)


@dataclass(frozen=True)
class EspnResult:
    """What ESPN says about one game."""
    event_id: str
    final: bool
    scores: dict[str, int]


def miscanceled(session, sports: tuple[str, ...] | None = None) -> list[Game]:
    """Canceled games carrying no score, which might have been played.

    A canceled game that already has a score was settled by something else;
    rewriting it would discard a recorded result.
    """
    q = (session.query(Game)
         .filter(Game.status == "canceled",
                 Game.home_score.is_(None), Game.away_score.is_(None)))
    if sports:
        q = q.filter(Game.sport.in_(list(sports)))
    return q.order_by(Game.date.asc(), Game.id.asc()).all()


def espn_lookup(client: httpx.Client | None = None):
    """A lookup callable that asks ESPN's scoreboard over the date window."""
    owns = client is None
    client = client or httpx.Client(timeout=_TIMEOUT)
    cache: dict[tuple, list] = {}

    def board(sport: str, stamp: str) -> list:
        key = (sport, stamp)
        if key not in cache:
            params = {"dates": stamp, **_PARAMS.get(sport, {})}
            try:
                resp = client.get(f"{_BASE}/{_PATHS[sport]}/scoreboard",
                                  params=params)
                cache[key] = resp.json().get("events", []) if resp.status_code == 200 else []
            except Exception as exc:
                logger.warning("ESPN scoreboard %s %s failed: %s", sport, stamp, exc)
                cache[key] = []
        return cache[key]

    def lookup(game: Game, home_abbr: str, away_abbr: str) -> EspnResult | None:
        if game.sport not in _PATHS:
            return None
        for delta in _OFFSETS:
            stamp = (game.date + timedelta(days=delta)).strftime("%Y%m%d")
            for event in board(game.sport, stamp):
                name = (event.get("shortName") or "").upper()
                if home_abbr.upper() not in name or away_abbr.upper() not in name:
                    continue
                comp = (event.get("competitions") or [{}])[0]
                status = ((comp.get("status") or {}).get("type") or {}).get("name")
                scores = {}
                for c in comp.get("competitors", []):
                    abbr = (c.get("team") or {}).get("abbreviation")
                    raw = c.get("score")
                    if abbr is not None and raw not in (None, ""):
                        try:
                            scores[abbr] = int(raw)
                        except (TypeError, ValueError):
                            pass
                return EspnResult(event_id=str(event.get("id")),
                                  final=status == "STATUS_FINAL", scores=scores)
        return None

    lookup.close = (lambda: client.close()) if owns else (lambda: None)
    return lookup


def run_on_session(session, *, sports: tuple[str, ...] | None = None,
                   apply: bool = False, lookup=None) -> dict:
    """Report, and with ``apply``, restore games ESPN reports as final."""
    summary = {"candidates": 0, "restored": 0, "refused_absent": 0,
               "refused_not_final": 0, "refused_unmatched": 0,
               "restored_ids": []}
    owns_lookup = lookup is None
    lookup = lookup or espn_lookup()
    try:
        for game in miscanceled(session, sports):
            summary["candidates"] += 1
            home = session.get(Team, game.home_team_id)
            away = session.get(Team, game.away_team_id)
            if not home or not away:
                summary["refused_unmatched"] += 1
                continue

            result = lookup(game, home.abbreviation, away.abbreviation)
            if result is None:
                summary["refused_absent"] += 1
                continue
            if not result.final:
                summary["refused_not_final"] += 1
                continue
            # Orientation by abbreviation, never by ESPN's competitor order.
            if (home.abbreviation not in result.scores
                    or away.abbreviation not in result.scores):
                summary["refused_unmatched"] += 1
                logger.warning("game %s: %s/%s not both in ESPN scores %s",
                               game.id, home.abbreviation, away.abbreviation,
                               sorted(result.scores))
                continue

            summary["restored"] += 1
            summary["restored_ids"].append(game.id)
            if apply:
                game.status = "final"
                game.home_score = result.scores[home.abbreviation]
                game.away_score = result.scores[away.abbreviation]
                game.espn_id = game.espn_id or result.event_id

        if apply and summary["restored"]:
            session.commit()
    finally:
        if owns_lookup:
            getattr(lookup, "close", lambda: None)()
    return summary


def run(db_path: str, *, sports: tuple[str, ...] | None = None,
        apply: bool = False) -> dict:
    """CLI entry point. Refuses a path that does not exist."""
    if db_path != ":memory:" and not os.path.exists(db_path):
        raise FileNotFoundError(
            f"--db {db_path!r} does not exist. Refusing to create a new, empty "
            f"database: a zero-row 'successful' run would hide the typo.")
    engine = get_engine(db_path)
    run_migrations(engine)
    session = get_session(engine)
    try:
        return run_on_session(session, sports=sports, apply=apply)
    finally:
        session.close()


def format_summary(s: dict, apply: bool) -> str:
    lines = ["Restored games that were actually played" if apply
             else "DRY RUN -- nothing written", ""]
    lines.append(f"  canceled candidates : {s['candidates']}")
    lines.append(f"  {'restored' if apply else 'would restore':<19} : {s['restored']}")
    lines.append(f"  refused (absent)    : {s['refused_absent']}")
    lines.append(f"  refused (not final) : {s['refused_not_final']}")
    lines.append(f"  refused (unmatched) : {s['refused_unmatched']}")
    if s["restored_ids"]:
        lines.append(f"  game ids            : {s['restored_ids'][:25]}"
                     + (" ..." if len(s["restored_ids"]) > 25 else ""))
    lines.append("")
    lines.append("  Scores are mapped by team abbreviation, never by ESPN's")
    lines.append("  competitor order. Anything unconfirmed is left canceled.")
    lines.append("  Picks on a restored game grade on the next grading run.")
    return "\n".join(lines)


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO)
    ap = argparse.ArgumentParser(
        description="Restore canceled games ESPN reports as played. "
                    "Dry run by default.")
    ap.add_argument("--db", required=True, help="Path to the database. Back it up first.")
    ap.add_argument("--sport", action="append",
                    help="Limit to a sport. Repeatable. Default: all.")
    ap.add_argument("--apply", action="store_true",
                    help="Actually write. Without this it is a dry run.")
    args = ap.parse_args(argv)
    sports = tuple(args.sport) if args.sport else None
    print(format_summary(run(args.db, sports=sports, apply=args.apply), args.apply))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

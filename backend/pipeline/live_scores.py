"""Live scores for games under way (sportsbook spec 2026-10-07 §8).

Every 2 minutes, for team-sport games past kickoff and not yet final, read
ESPN's scoreboard for their (sport, ET date) and write the running score,
ESPN's one-line clock and status "in_progress". It never writes "final" --
the results path owns that, with its reconciliation and score healing -- and
never touches a final row. When nothing is live it makes no request at all.

Combat sports are skipped: the scoreboard parser reads one bout per MMA card,
no MMA row carries an ESPN id, and boxing has no feed.
"""
from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from backend.collectors.espn import ESPNCollector
from backend.database import get_session
from sqlalchemy import or_

from backend.models import Game
from backend.time_utils import ET, game_start_utc

logger = logging.getLogger(__name__)

LIVE_SPORTS = ("nfl", "ncaaf", "nba", "ncaab", "mlb")
#: Games dated today or yesterday (ET). A row unfinished after that is a
#: results problem, not a live one -- polling it forever would cost requests.
LIVE_LOOKBACK_DAYS = 1


def live_candidates(session, now: datetime) -> list[Game]:
    first_day = now.astimezone(ET).date() - timedelta(days=LIVE_LOOKBACK_DAYS)
    rows = (session.query(Game)
            .filter(Game.sport.in_(LIVE_SPORTS),
                    Game.status.in_(("scheduled", "in_progress")),
                    Game.espn_id.isnot(None), Game.start_time.isnot(None),
                    Game.date >= first_day,
                    # Once ESPN calls it ("Final", "Final/OT") the score is
                    # settled; the 08:00 results pass makes it final. Polling
                    # it all night would only cost requests.
                    or_(Game.live_detail.is_(None), ~Game.live_detail.like("Final%")))
            .all())
    return [g for g in rows if game_start_utc(g) <= now]


def _is_live(event: dict) -> bool:
    """ESPN's state, not its status name: halftime and delays are "in". A
    finished game ("post") counts only if final -- a postponed or canceled
    one is the results path's to handle."""
    state = event.get("state")
    return state == "in" or (state == "post" and event.get("status") == "final")


def apply_scoreboard(session, games: list[Game], events: list[dict]) -> int:
    """Write each live event onto its game. The write is a conditional UPDATE
    ("still scheduled or in_progress"), not an attribute set: the results pass
    may make a row final in another thread while this job waits on ESPN, and
    the in-memory copy can't know -- a plain write would un-final it."""
    by_id = {str(e.get("espn_id")): e for e in events}
    updated = 0
    for game in games:
        event = by_id.get(str(game.espn_id))
        if event is None or not _is_live(event):
            continue
        values = {"status": "in_progress", "live_detail": event.get("live_detail")}
        if event.get("home_score") is not None:
            values["home_score"] = event["home_score"]
        if event.get("away_score") is not None:
            values["away_score"] = event["away_score"]
        updated += (session.query(Game)
                    .filter(Game.id == game.id, Game.status.in_(("scheduled", "in_progress")))
                    .update(values, synchronize_session=False))
    return updated


async def update_live_scores(session, *, now: datetime | None = None, fetch=None) -> int:
    now = now or datetime.now(timezone.utc)
    games = live_candidates(session, now)
    if not games:
        return 0
    by_board: dict[tuple, list[Game]] = defaultdict(list)
    for game in games:
        by_board[(game.sport, game.date)].append(game)
    collector = ESPNCollector() if fetch is None else None
    fetcher = fetch or collector.fetch_scoreboard
    updated = 0
    try:
        for (sport, day), board_games in by_board.items():
            try:
                events = await fetcher(sport, day.strftime("%Y%m%d"))
            except Exception:
                logger.warning("live_scores: %s scoreboard for %s failed", sport, day, exc_info=True)
                continue
            updated += apply_scoreboard(session, board_games, events)
        session.commit()
    finally:
        if collector is not None:
            await collector.close()
    if updated:
        logger.info("live_scores: updated %d game(s)", updated)
    return updated


def run_live_scores(engine) -> None:
    """The scheduler's entry point: never raises, so a bad poll can't hurt
    the jobs around it."""
    session = get_session(engine)
    try:
        asyncio.run(update_live_scores(session))
    except Exception:
        session.rollback()
        logger.exception("live_scores failed")
    finally:
        session.close()

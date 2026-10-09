"""The league activity feed (sportsbook spec 2026-10-07 §10).

Writing events and broadcasting them, the per-player streaks, and the
settlement announcements. Both grading paths -- the owner's POST /users/grade
and the scheduler's grade_pending_picks -- call settle_and_announce, so a bet
announces itself the same way however it was graded, and at most once.
"""
from __future__ import annotations

import asyncio
import json
import logging

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

from backend.models import ActivityFeed, Game, PaperPick, Parlay, Team, UserProfile
from backend.pipeline import paper_settlement

logger = logging.getLogger(__name__)

#: result -> (event type, verb).
SETTLED = {"win": ("pick_won", "won"), "loss": ("pick_lost", "lost"), "push": ("pick_pushed", "pushed")}


def log_feed_event(session, loop, user_id: int | None, event_type: str, payload: dict) -> None:
    """Save an event, then broadcast it over the WebSocket when an event loop
    is available (the API process; the scheduler has none, and the frontend
    falls back to a 60 s refetch)."""
    session.add(ActivityFeed(user_id=user_id, event_type=event_type, payload=json.dumps(payload)))
    session.commit()
    if loop is None:
        return
    try:
        from backend.api.websocket import manager
        asyncio.run_coroutine_threadsafe(manager.broadcast(event_type, payload), loop)
    except Exception:
        logger.warning("Feed broadcast failed", exc_info=True)


def update_streaks(session, loop, user_id: int) -> None:
    """Recompute a player's streak from their graded straight bets, newest first.

    Parlay legs are left out: one 3-leg parlay is not a 3-pick streak, and
    Leaders ranks straight bets only."""
    picks = (session.query(PaperPick)
             .filter(PaperPick.user_id == user_id, PaperPick.result.isnot(None),
                     PaperPick.parlay_id.is_(None))
             .order_by(PaperPick.created_at.desc())
             .all())
    if not picks:
        return
    current = picks[0].result
    if current == "push":
        current = picks[1].result if len(picks) > 1 else "none"
    streak = 0
    for p in picks:
        if p.result == "push":
            continue
        if p.result == current:
            streak += 1
        else:
            break
    user = session.get(UserProfile, user_id)
    if not user:
        return
    new_type = "win" if current == "win" else "loss" if current == "loss" else "none"
    # Announce only when the streak grows, not on every grading run.
    grew = streak > ((user.current_streak or 0) if user.streak_type == new_type else 0)
    user.current_streak = streak
    user.streak_type = new_type
    if current == "win" and streak > (user.best_streak or 0):
        user.best_streak = streak
    if streak >= 3 and grew:
        log_feed_event(session, loop, user_id, "streak", {
            "user_name": user.name,
            "message": f"{user.name} is on a {streak}-pick {'win' if current == 'win' else 'loss'} streak!",
            "streak": streak, "streak_type": user.streak_type,
        })


def _money(x: float) -> str:
    if x > 0:
        return f"+${x:,.2f}"
    if x < 0:
        return f"−${abs(x):,.2f}"
    return "$0.00"


def _announced(session, bet_key: str) -> bool:
    types = [t for t, _ in SETTLED.values()]
    return (session.query(ActivityFeed.id)
            .filter(ActivityFeed.event_type.in_(types),
                    func.json_extract(ActivityFeed.payload, "$.bet_key") == bet_key)
            .first()) is not None


def straight_label(session, pick: PaperPick) -> str:
    if pick.pick_type == "prop":
        return pick.pick_value
    from backend.api.picks import _resolve_pick_value
    game = session.get(Game, pick.game_id)
    home, away = session.get(Team, game.home_team_id), session.get(Team, game.away_team_id)
    return _resolve_pick_value(pick.pick_value, home.abbreviation, away.abbreviation)


def announce_settlements(session, loop, graded: list[PaperPick], parlays: list[Parlay]) -> int:
    """One won/lost/pushed event per newly settled straight bet and parlay --
    never for a parlay's legs, and never twice for the same bet."""
    written = 0
    names: dict[int, str] = {}

    def name(uid: int) -> str:
        if uid not in names:
            u = session.get(UserProfile, uid)
            names[uid] = u.name if u else "Unknown"
        return names[uid]

    def announce(uid, kind, bet_id, result, payout, what):
        nonlocal written
        if result not in SETTLED:
            return
        key = f"{kind}-{bet_id}"
        if _announced(session, key):
            return
        event_type, verb = SETTLED[result]
        try:
            log_feed_event(session, loop, uid, event_type, {
                "user_name": name(uid), "message": f"{name(uid)} {verb} {what} — {_money(payout or 0.0)}",
                "bet_key": key, "bet_id": bet_id, "kind": kind, "result": result, "payout": payout,
            })
        except IntegrityError:
            # Another grader announced it between our check and our insert.
            session.rollback()
            return
        written += 1

    for pick in graded:
        if pick.parlay_id is None:
            announce(pick.user_id, "straight", pick.id, pick.result, pick.payout, straight_label(session, pick))
    for parlay in parlays:
        legs = session.query(PaperPick).filter(PaperPick.parlay_id == parlay.id).count()
        announce(parlay.user_id, "parlay", parlay.id, parlay.result, parlay.payout, f"a {legs}-leg parlay")
    return written


def settle_and_announce(session, loop=None) -> dict:
    """Grade every pending paper bet, settle parlays, announce each newly
    settled bet once and refresh streaks. The one path both graders use."""
    graded = paper_settlement.grade_paper_picks(session)
    parlays = paper_settlement.settle_parlays_list(session)
    events = announce_settlements(session, loop, graded, parlays)
    for uid in {p.user_id for p in graded} | {p.user_id for p in parlays}:
        update_streaks(session, loop, uid)
    session.commit()
    return {"graded": len(graded), "parlays_settled": len(parlays), "events": events}

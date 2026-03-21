from datetime import datetime, timezone, date, timedelta
from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel
from sqlalchemy import func
from backend.database import get_session
from backend.models import UserProfile, PaperPick, Game, PlayerStat, ActivityFeed
from backend.pipeline.grader import grade_pick, grade_prop_pick
from backend.analysis.odds_utils import calculate_payout
import json
import asyncio

router = APIRouter()


def _log_feed_event(session, user_id: int | None, event_type: str, payload: dict):
    """Save activity event to DB and broadcast via WebSocket."""
    session.add(ActivityFeed(
        user_id=user_id,
        event_type=event_type,
        payload=json.dumps(payload),
    ))
    session.commit()
    # Broadcast via WebSocket (fire-and-forget)
    try:
        from backend.api.websocket import manager
        asyncio.get_event_loop().create_task(
            manager.broadcast(event_type, payload)
        )
    except RuntimeError:
        pass  # No event loop running (e.g., in tests)


class CreateUserRequest(BaseModel):
    name: str


class PlacePickRequest(BaseModel):
    game_id: int
    pick_type: str
    pick_value: str
    odds: int
    stake: float
    prop_market: str | None = None
    prop_player: str | None = None


@router.get("/")
def list_users(request: Request):
    """List all user profiles with current balance and record."""
    session = get_session(request.app.state.engine)
    try:
        users = session.query(UserProfile).all()
        result = []
        for u in users:
            picks = session.query(PaperPick).filter(PaperPick.user_id == u.id).all()
            wins = sum(1 for p in picks if p.result == "win")
            losses = sum(1 for p in picks if p.result == "loss")
            pushes = sum(1 for p in picks if p.result == "push")
            pending = sum(1 for p in picks if p.result is None)
            total_wagered = sum(p.stake for p in picks)
            total_payout = sum(p.payout or 0 for p in picks)
            current_balance = u.starting_balance + total_payout
            total_picks = wins + losses + pushes
            result.append({
                "id": u.id,
                "name": u.name,
                "starting_balance": u.starting_balance,
                "current_balance": round(current_balance, 2),
                "total_wagered": round(total_wagered, 2),
                "profit": round(total_payout, 2),
                "roi": round((total_payout / total_wagered * 100) if total_wagered > 0 else 0, 2),
                "wins": wins,
                "losses": losses,
                "pushes": pushes,
                "pending": pending,
                "win_rate": round((wins / total_picks * 100) if total_picks > 0 else 0, 1),
                "current_streak": u.current_streak or 0,
                "best_streak": u.best_streak or 0,
                "streak_type": u.streak_type or "none",
                "created_at": str(u.created_at),
            })
        # Sort by current_balance descending (leaderboard)
        result.sort(key=lambda x: x["current_balance"], reverse=True)
        return result
    finally:
        session.close()


@router.post("/")
def create_user(request: Request, body: CreateUserRequest):
    """Create a new user profile."""
    session = get_session(request.app.state.engine)
    try:
        existing = session.query(UserProfile).filter(UserProfile.name == body.name).first()
        if existing:
            raise HTTPException(status_code=400, detail="Username already taken")
        user = UserProfile(name=body.name)
        session.add(user)
        session.commit()
        return {"id": user.id, "name": user.name, "starting_balance": user.starting_balance}
    finally:
        session.close()


@router.delete("/{user_id}")
def delete_user(request: Request, user_id: int):
    """Delete a user and all their picks."""
    session = get_session(request.app.state.engine)
    try:
        user = session.get(UserProfile, user_id)
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        session.query(PaperPick).filter(PaperPick.user_id == user_id).delete()
        session.query(ActivityFeed).filter(ActivityFeed.user_id == user_id).delete()
        session.delete(user)
        session.commit()
        return {"deleted": True, "id": user_id}
    finally:
        session.close()


@router.get("/{user_id}")
def get_user(request: Request, user_id: int):
    """Get a user profile with full stats."""
    session = get_session(request.app.state.engine)
    try:
        user = session.get(UserProfile, user_id)
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        picks = session.query(PaperPick).filter(PaperPick.user_id == user_id).all()
        wins = sum(1 for p in picks if p.result == "win")
        losses = sum(1 for p in picks if p.result == "loss")
        pushes = sum(1 for p in picks if p.result == "push")
        pending = sum(1 for p in picks if p.result is None)
        total_wagered = sum(p.stake for p in picks)
        total_payout = sum(p.payout or 0 for p in picks)
        current_balance = user.starting_balance + total_payout
        total_picks = wins + losses + pushes
        return {
            "id": user.id,
            "name": user.name,
            "starting_balance": user.starting_balance,
            "current_balance": round(current_balance, 2),
            "total_wagered": round(total_wagered, 2),
            "profit": round(total_payout, 2),
            "roi": round((total_payout / total_wagered * 100) if total_wagered > 0 else 0, 2),
            "wins": wins,
            "losses": losses,
            "pushes": pushes,
            "pending": pending,
            "win_rate": round((wins / total_picks * 100) if total_picks > 0 else 0, 1),
        }
    finally:
        session.close()


@router.post("/{user_id}/picks")
def place_pick(request: Request, user_id: int, body: PlacePickRequest):
    """Place a paper pick for a user."""
    session = get_session(request.app.state.engine)
    try:
        user = session.get(UserProfile, user_id)
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        # Calculate current balance
        picks = session.query(PaperPick).filter(PaperPick.user_id == user_id).all()
        total_payout = sum(p.payout or 0 for p in picks)
        current_balance = user.starting_balance + total_payout

        if body.stake > current_balance:
            raise HTTPException(status_code=400, detail="Insufficient balance")
        if body.stake <= 0:
            raise HTTPException(status_code=400, detail="Stake must be positive")

        # Check if the game exists
        game = session.get(Game, body.game_id)
        if not game:
            raise HTTPException(status_code=404, detail="Game not found")

        # If game is already final, grade immediately
        result = None
        payout = None
        if game.status == "final" and game.home_score is not None and game.away_score is not None:
            if body.pick_type == "prop" and body.prop_player and body.prop_market:
                # Grade prop pick using player stats
                player_stat = (
                    session.query(PlayerStat)
                    .filter_by(player_name=body.prop_player, stat_type="game_log", game_date=game.date)
                    .first()
                )
                prop_result = grade_prop_pick(body.pick_value, body.prop_market, player_stat)
                if prop_result:
                    result = prop_result[0]
                    if result == "win":
                        payout = body.stake * calculate_payout(body.odds)
                    elif result == "push":
                        payout = 0.0
                    else:
                        payout = -body.stake
            else:
                grade_result, grade_payout_ratio = grade_pick(
                    body.pick_type, body.pick_value,
                    game.home_score, game.away_score, body.odds
                )
                result = grade_result
                if grade_result == "win":
                    payout = body.stake * calculate_payout(body.odds)
                elif grade_result == "push":
                    payout = 0.0
                else:
                    payout = -body.stake

        pick = PaperPick(
            user_id=user_id,
            game_id=body.game_id,
            pick_type=body.pick_type,
            pick_value=body.pick_value,
            odds=body.odds,
            stake=body.stake,
            result=result,
            payout=payout,
            prop_market=body.prop_market,
            prop_player=body.prop_player,
        )
        session.add(pick)
        session.commit()

        # Log activity feed event
        user = session.query(UserProfile).get(user_id)
        user_name = user.name if user else "Unknown"
        odds_str = f"{body.odds:+d}" if body.odds >= 0 else str(body.odds)
        _log_feed_event(session, user_id, "pick_placed", {
            "user_name": user_name,
            "message": f"{user_name} bet {body.pick_value} {odds_str} — ${body.stake:,.0f}",
            "pick_value": body.pick_value,
            "odds": body.odds,
            "stake": body.stake,
        })

        if result:
            event_type = "pick_won" if result == "win" else "pick_lost"
            _log_feed_event(session, user_id, event_type, {
                "user_name": user_name,
                "message": f"{user_name} {'won' if result == 'win' else 'lost'} {body.pick_value} — {'+'  if (payout or 0) > 0 else ''}${payout or 0:,.0f}",
                "result": result,
                "payout": payout,
            })
            _update_streaks(session, user_id)

        return {
            "id": pick.id,
            "result": result,
            "payout": payout,
            "new_balance": round(current_balance + (payout or 0), 2),
        }
    finally:
        session.close()


@router.get("/{user_id}/picks")
def get_user_picks(request: Request, user_id: int):
    """Get all picks for a user."""
    session = get_session(request.app.state.engine)
    try:
        user = session.get(UserProfile, user_id)
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        picks = (
            session.query(PaperPick, Game)
            .join(Game, PaperPick.game_id == Game.id)
            .filter(PaperPick.user_id == user_id)
            .order_by(PaperPick.created_at.desc())
            .all()
        )

        result = []
        for p, g in picks:
            entry = {
                "id": p.id,
                "game_id": p.game_id,
                "sport": g.sport,
                "date": str(g.date),
                "status": g.status,
                "pick_type": p.pick_type,
                "pick_value": p.pick_value,
                "odds": p.odds,
                "stake": p.stake,
                "result": p.result,
                "payout": p.payout,
                "created_at": str(p.created_at),
            }
            if p.prop_market:
                entry["prop_market"] = p.prop_market
            if p.prop_player:
                entry["prop_player"] = p.prop_player
            result.append(entry)
        return result
    finally:
        session.close()


@router.post("/grade")
def grade_paper_picks(request: Request):
    """Grade all pending paper picks for games that are final."""
    session = get_session(request.app.state.engine)
    try:
        pending = (
            session.query(PaperPick, Game)
            .join(Game, PaperPick.game_id == Game.id)
            .filter(PaperPick.result.is_(None))
            .filter(Game.status == "final")
            .all()
        )
        graded = 0
        for pick, game in pending:
            if game.home_score is None or game.away_score is None:
                continue

            if pick.pick_type == "prop" and pick.prop_player and pick.prop_market:
                # Grade prop pick using player stats
                player_stat = (
                    session.query(PlayerStat)
                    .filter_by(player_name=pick.prop_player, stat_type="game_log", game_date=game.date)
                    .first()
                )
                prop_result = grade_prop_pick(pick.pick_value, pick.prop_market, player_stat)
                if not prop_result:
                    continue  # No stats available yet, skip
                pick.result = prop_result[0]
                if pick.result == "win":
                    pick.payout = pick.stake * calculate_payout(pick.odds)
                elif pick.result == "push":
                    pick.payout = 0.0
                else:
                    pick.payout = -pick.stake
            else:
                grade_result, grade_payout_ratio = grade_pick(
                    pick.pick_type, pick.pick_value,
                    game.home_score, game.away_score, pick.odds
                )
                pick.result = grade_result
                if grade_result == "win":
                    pick.payout = pick.stake * calculate_payout(pick.odds)
                elif grade_result == "push":
                    pick.payout = 0.0
                else:
                    pick.payout = -pick.stake
            graded += 1

            # Log grading events to activity feed
            user = session.query(UserProfile).get(pick.user_id)
            user_name = user.name if user else "Unknown"
            event_type = "pick_won" if pick.result == "win" else "pick_lost"
            if pick.result in ("win", "loss"):
                _log_feed_event(session, pick.user_id, event_type, {
                    "user_name": user_name,
                    "message": f"{user_name} {'won' if pick.result == 'win' else 'lost'} {pick.pick_value} — {'+'  if (pick.payout or 0) > 0 else ''}${pick.payout or 0:,.0f}",
                    "result": pick.result,
                    "payout": pick.payout,
                })

        # Update streaks for all affected users
        affected_users = set(pick.user_id for pick, _ in pending)
        for uid in affected_users:
            _update_streaks(session, uid)

        session.commit()
        return {"graded": graded}
    finally:
        session.close()


def _update_streaks(session, user_id: int):
    """Recompute streaks from the user's most recent graded picks."""
    picks = (
        session.query(PaperPick)
        .filter(PaperPick.user_id == user_id, PaperPick.result.isnot(None))
        .order_by(PaperPick.created_at.desc())
        .all()
    )
    if not picks:
        return

    # Current streak = consecutive same results from most recent
    current_result = picks[0].result
    if current_result == "push":
        current_result = picks[1].result if len(picks) > 1 else "none"

    streak = 0
    for p in picks:
        if p.result == "push":
            continue
        if p.result == current_result:
            streak += 1
        else:
            break

    user = session.get(UserProfile, user_id)
    if user:
        user.current_streak = streak
        user.streak_type = "win" if current_result == "win" else "loss" if current_result == "loss" else "none"
        if current_result == "win" and streak > (user.best_streak or 0):
            user.best_streak = streak

        # Log streak event if notable (3+)
        if streak >= 3:
            _log_feed_event(session, user_id, "streak", {
                "user_name": user.name,
                "message": f"{user.name} is on a {streak}-pick {'win' if current_result == 'win' else 'loss'} streak!",
                "streak": streak,
                "streak_type": user.streak_type,
            })


def _compute_period_stats(picks: list) -> dict:
    """Compute win/loss/profit stats from a list of (PaperPick, Game) tuples."""
    wins = sum(1 for p, _ in picks if p.result == "win")
    losses = sum(1 for p, _ in picks if p.result == "loss")
    pushes = sum(1 for p, _ in picks if p.result == "push")
    total = wins + losses + pushes
    profit = sum(p.payout or 0 for p, _ in picks)
    wagered = sum(p.stake for p, _ in picks if p.result is not None)
    return {
        "wins": wins,
        "losses": losses,
        "pushes": pushes,
        "total": total,
        "win_rate": round((wins / total * 100) if total > 0 else 0, 1),
        "profit": round(profit, 2),
        "roi": round((profit / wagered * 100) if wagered > 0 else 0, 2),
    }


@router.get("/{user_id}/stats")
def get_user_stats(request: Request, user_id: int):
    """Get daily, weekly, monthly, and all-time stats for a user."""
    session = get_session(request.app.state.engine)
    try:
        user = session.get(UserProfile, user_id)
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        all_picks = (
            session.query(PaperPick, Game)
            .join(Game, PaperPick.game_id == Game.id)
            .filter(PaperPick.user_id == user_id)
            .all()
        )

        today = date.today()
        week_start = today - timedelta(days=today.weekday())  # Monday
        month_start = today.replace(day=1)

        daily = [(p, g) for p, g in all_picks if g.date == today]
        weekly = [(p, g) for p, g in all_picks if g.date >= week_start]
        monthly = [(p, g) for p, g in all_picks if g.date >= month_start]

        # Daily breakdown for chart (last 30 days)
        daily_breakdown = []
        for i in range(30):
            d = today - timedelta(days=29 - i)
            day_picks = [(p, g) for p, g in all_picks if g.date == d and p.result is not None]
            if day_picks:
                stats = _compute_period_stats(day_picks)
                daily_breakdown.append({"date": str(d), **stats})

        return {
            "today": _compute_period_stats(daily),
            "this_week": _compute_period_stats(weekly),
            "this_month": _compute_period_stats(monthly),
            "all_time": _compute_period_stats(all_picks),
            "daily_breakdown": daily_breakdown,
        }
    finally:
        session.close()


@router.get("/feed")
def get_activity_feed(request: Request, limit: int = 50):
    """Get recent activity feed events."""
    session = get_session(request.app.state.engine)
    try:
        events = (
            session.query(ActivityFeed)
            .order_by(ActivityFeed.created_at.desc())
            .limit(limit)
            .all()
        )
        return [
            {
                "id": e.id,
                "user_id": e.user_id,
                "event_type": e.event_type,
                "payload": json.loads(e.payload),
                "created_at": e.created_at.isoformat() if e.created_at else None,
            }
            for e in events
        ]
    finally:
        session.close()

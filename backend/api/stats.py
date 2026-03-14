from fastapi import APIRouter, Request
from backend.database import get_session
from backend.models import PickModel, PickResult, Game

router = APIRouter()

@router.get("/record")
def get_record(request: Request, sport: str | None = None):
    session = get_session(request.app.state.engine)
    try:
        query = (session.query(PickResult, PickModel, Game)
            .join(PickModel, PickResult.pick_id == PickModel.id)
            .join(Game, PickModel.game_id == Game.id))
        if sport: query = query.filter(Game.sport == sport)
        rows = query.all()
        wins = sum(1 for r, _, _ in rows if r.result == "win")
        losses = sum(1 for r, _, _ in rows if r.result == "loss")
        pushes = sum(1 for r, _, _ in rows if r.result == "push")
        total = wins + losses
        total_profit = sum(r.payout for r, _, _ in rows)
        return {"wins": wins, "losses": losses, "pushes": pushes, "total": total,
            "win_rate": round((wins / total * 100) if total > 0 else 0, 2),
            "roi": round((total_profit / total * 100) if total > 0 else 0, 2),
            "total_profit": round(total_profit, 4)}
    finally:
        session.close()

@router.get("/daily")
def get_daily(request: Request, sport: str | None = None):
    session = get_session(request.app.state.engine)
    try:
        query = (session.query(Game.date, PickResult.result, PickResult.payout)
            .join(PickModel, PickResult.pick_id == PickModel.id)
            .join(Game, PickModel.game_id == Game.id))
        if sport: query = query.filter(Game.sport == sport)
        rows = query.order_by(Game.date).all()
        daily = {}
        for d, result, payout in rows:
            key = str(d)
            if key not in daily:
                daily[key] = {"date": key, "wins": 0, "losses": 0, "pushes": 0, "profit": 0.0}
            if result == "win": daily[key]["wins"] += 1
            elif result == "loss": daily[key]["losses"] += 1
            else: daily[key]["pushes"] += 1
            daily[key]["profit"] = round(daily[key]["profit"] + payout, 4)
        return list(daily.values())
    finally:
        session.close()

from fastapi import APIRouter, Request
from backend.database import get_session
from backend.models import PickModel, PickResult, Game
from backend.analysis.odds_utils import american_to_implied_prob

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

@router.get("/clv")
def get_clv_stats(request: Request):
    """Calculate Closing Line Value metrics."""
    session = get_session(request.app.state.engine)
    try:
        results = (
            session.query(PickResult, PickModel)
            .join(PickModel, PickResult.pick_id == PickModel.id)
            .filter(PickResult.odds_at_close.isnot(None))
            .all()
        )

        if not results:
            return {"total_picks": 0, "clv_positive": 0, "avg_clv": 0}

        clv_values = []
        for pr, pm in results:
            # CLV = implied prob at close - implied prob at pick
            # If we got better odds than closing, CLV is positive
            pick_implied = american_to_implied_prob(pm.odds_at_pick or -110)
            close_implied = american_to_implied_prob(pr.odds_at_close)
            # CLV: if we bet at lower implied prob and it closed higher, we got value
            clv = (close_implied - pick_implied) * 100
            clv_values.append(clv)

        positive_clv = sum(1 for c in clv_values if c > 0)
        return {
            "total_picks": len(clv_values),
            "clv_positive": positive_clv,
            "clv_positive_pct": round(positive_clv / len(clv_values) * 100, 1),
            "avg_clv": round(sum(clv_values) / len(clv_values), 2),
        }
    finally:
        session.close()

from datetime import date
from fastapi import APIRouter, Request
from backend.database import get_session
from backend.models import PickModel, Game, PickResult, Team

router = APIRouter()

@router.get("/today")
def get_today_picks(request: Request, sport: str | None = None, min_confidence: int = 0, pick_type: str | None = None):
    session = get_session(request.app.state.engine)
    try:
        today = date.today()
        query = (session.query(PickModel, Game, Team)
            .join(Game, PickModel.game_id == Game.id)
            .join(Team, Game.home_team_id == Team.id)
            .filter(Game.date == today))
        if sport: query = query.filter(Game.sport == sport)
        if min_confidence > 0: query = query.filter(PickModel.confidence >= min_confidence)
        if pick_type: query = query.filter(PickModel.pick_type == pick_type)
        results = []
        for pick, game, home_team in query.all():
            results.append({"id": pick.id, "game_id": pick.game_id, "sport": game.sport,
                "date": str(game.date), "pick_type": pick.pick_type, "pick_value": pick.pick_value,
                "confidence": pick.confidence, "edge_pct": pick.edge_pct, "odds_at_pick": pick.odds_at_pick})
        return results
    finally:
        session.close()

@router.get("/history")
def get_picks_history(request: Request, sport: str | None = None, page: int = 1, per_page: int = 50):
    session = get_session(request.app.state.engine)
    try:
        query = (session.query(PickModel, Game).join(Game, PickModel.game_id == Game.id).order_by(Game.date.desc()))
        if sport: query = query.filter(Game.sport == sport)
        offset = (page - 1) * per_page
        rows = query.offset(offset).limit(per_page).all()
        results = []
        for pick, game in rows:
            result_row = session.query(PickResult).filter(PickResult.pick_id == pick.id).first()
            results.append({"id": pick.id, "game_id": pick.game_id, "sport": game.sport,
                "date": str(game.date), "pick_type": pick.pick_type, "pick_value": pick.pick_value,
                "confidence": pick.confidence, "edge_pct": pick.edge_pct,
                "odds_at_pick": pick.odds_at_pick,
                "result": result_row.result if result_row else None,
                "payout": result_row.payout if result_row else None})
        return results
    finally:
        session.close()

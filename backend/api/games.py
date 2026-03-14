from fastapi import APIRouter, Request, HTTPException
from backend.database import get_session
from backend.models import Game, Odds, Team

router = APIRouter()

@router.get("/{game_id}")
def get_game(request: Request, game_id: int):
    session = get_session(request.app.state.engine)
    try:
        game = session.query(Game).get(game_id)
        if not game: raise HTTPException(status_code=404)
        home = session.query(Team).get(game.home_team_id)
        away = session.query(Team).get(game.away_team_id)
        odds = session.query(Odds).filter(Odds.game_id == game_id).all()
        return {"id": game.id, "sport": game.sport, "season": game.season,
            "week": game.week, "date": str(game.date), "status": game.status,
            "home_team": {"id": home.id, "name": home.name, "abbreviation": home.abbreviation} if home else None,
            "away_team": {"id": away.id, "name": away.name, "abbreviation": away.abbreviation} if away else None,
            "home_score": game.home_score, "away_score": game.away_score,
            "odds_history": [{"bookmaker": o.bookmaker, "moneyline_home": o.moneyline_home,
                "moneyline_away": o.moneyline_away, "spread_home": o.spread_home,
                "spread_away": o.spread_away, "over_under": o.over_under,
                "timestamp": str(o.timestamp)} for o in odds]}
    finally:
        session.close()

from datetime import date, timedelta
from fastapi import APIRouter, Request, HTTPException
from sqlalchemy.orm import aliased
from backend.database import get_session
from backend.models import Game, Odds, Team

router = APIRouter()


@router.get("/today")
def get_today_games(request: Request, sport: str | None = None):
    session = get_session(request.app.state.engine)
    try:
        day = date.today()
        AwayTeam = aliased(Team)
        query = (session.query(Game, Team, AwayTeam)
            .join(Team, Game.home_team_id == Team.id)
            .join(AwayTeam, Game.away_team_id == AwayTeam.id)
            .filter(Game.date == day))
        if sport:
            query = query.filter(Game.sport == sport)
        query = query.order_by(Game.sport, Game.date)

        results = []
        for game, home, away in query.all():
            odds = session.query(Odds).filter(Odds.game_id == game.id).all()
            # Use consensus (first bookmaker) for display
            best = odds[0] if odds else None
            results.append({
                "id": game.id,
                "sport": game.sport,
                "date": str(game.date),
                "status": game.status,
                "home_team": home.abbreviation,
                "away_team": away.abbreviation,
                "home_team_name": home.name,
                "away_team_name": away.name,
                "home_score": game.home_score,
                "away_score": game.away_score,
                "moneyline_home": best.moneyline_home if best else None,
                "moneyline_away": best.moneyline_away if best else None,
                "spread_home": best.spread_home if best else None,
                "over_under": best.over_under if best else None,
                "bookmaker": best.bookmaker if best else None,
                "odds_count": len(odds),
            })
        return results
    finally:
        session.close()


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

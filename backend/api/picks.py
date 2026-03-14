from datetime import date, timedelta
from fastapi import APIRouter, Request
from backend.database import get_session
from backend.models import PickModel, Game, PickResult, Team

router = APIRouter()


def _resolve_pick_value(pick_value: str, home_abbr: str, away_abbr: str) -> str:
    """Replace HOME/AWAY with actual team abbreviations in pick display values."""
    result = pick_value
    result = result.replace("HOME ML", f"{home_abbr} ML")
    result = result.replace("AWAY ML", f"{away_abbr} ML")
    result = result.replace("HOME ", f"{home_abbr} ")
    result = result.replace("AWAY ", f"{away_abbr} ")
    return result

@router.get("/today")
def get_today_picks(request: Request, sport: str | None = None, min_confidence: int = 0, pick_type: str | None = None, target_date: str | None = None):
    session = get_session(request.app.state.engine)
    try:
        if target_date:
            day = date.fromisoformat(target_date)
        else:
            # Show today's picks; if none, show tomorrow's
            day = date.today()
            today_count = session.query(PickModel).join(Game).filter(Game.date == day).count()
            if today_count == 0:
                day = day + timedelta(days=1)
        from sqlalchemy.orm import aliased
        AwayTeam = aliased(Team)
        query = (session.query(PickModel, Game, Team, AwayTeam)
            .join(Game, PickModel.game_id == Game.id)
            .join(Team, Game.home_team_id == Team.id)
            .join(AwayTeam, Game.away_team_id == AwayTeam.id)
            .filter(Game.date == day))
        if sport: query = query.filter(Game.sport == sport)
        if min_confidence > 0: query = query.filter(PickModel.confidence >= min_confidence)
        if pick_type: query = query.filter(PickModel.pick_type == pick_type)
        results = []
        for pick, game, home_team, away_team in query.all():
            pick_display = _resolve_pick_value(pick.pick_value, home_team.abbreviation, away_team.abbreviation)
            results.append({"id": pick.id, "game_id": pick.game_id, "sport": game.sport,
                "date": str(game.date), "pick_type": pick.pick_type, "pick_value": pick_display,
                "confidence": pick.confidence, "edge_pct": pick.edge_pct, "odds_at_pick": pick.odds_at_pick,
                "home_team": home_team.abbreviation, "away_team": away_team.abbreviation,
                "matchup": f"{away_team.abbreviation} @ {home_team.abbreviation}"})
        return results
    finally:
        session.close()

@router.get("/history")
def get_picks_history(request: Request, sport: str | None = None, page: int = 1, per_page: int = 50):
    session = get_session(request.app.state.engine)
    try:
        from sqlalchemy.orm import aliased
        AwayTeam = aliased(Team)
        query = (session.query(PickModel, Game, Team, AwayTeam)
            .join(Game, PickModel.game_id == Game.id)
            .join(Team, Game.home_team_id == Team.id)
            .join(AwayTeam, Game.away_team_id == AwayTeam.id)
            .order_by(Game.date.desc()))
        if sport: query = query.filter(Game.sport == sport)
        offset = (page - 1) * per_page
        rows = query.offset(offset).limit(per_page).all()
        results = []
        for pick, game, home_team, away_team in rows:
            result_row = session.query(PickResult).filter(PickResult.pick_id == pick.id).first()
            pick_display = _resolve_pick_value(pick.pick_value, home_team.abbreviation, away_team.abbreviation)
            results.append({"id": pick.id, "game_id": pick.game_id, "sport": game.sport,
                "date": str(game.date), "pick_type": pick.pick_type, "pick_value": pick_display,
                "confidence": pick.confidence, "edge_pct": pick.edge_pct,
                "odds_at_pick": pick.odds_at_pick,
                "result": result_row.result if result_row else None,
                "payout": result_row.payout if result_row else None,
                "home_team": home_team.abbreviation, "away_team": away_team.abbreviation,
                "matchup": f"{away_team.abbreviation} @ {home_team.abbreviation}"})
        return results
    finally:
        session.close()

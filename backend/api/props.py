from datetime import date, timedelta
from fastapi import APIRouter, Request
from sqlalchemy.orm import aliased
from backend.database import get_session
from backend.models import PlayerProp, Game, Team, PlayerStat, TeamStat
from backend.analysis.prop_analyzer import PropAnalyzer
from backend.time_utils import et_today

router = APIRouter()

# Human-readable labels for prop market keys
MARKET_LABELS = {
    "player_points": "Points",
    "player_rebounds": "Rebounds",
    "player_assists": "Assists",
    "player_threes": "3-Pointers",
    "player_blocks": "Blocks",
    "player_steals": "Steals",
    "player_turnovers": "Turnovers",
    "player_points_rebounds_assists": "Pts+Reb+Ast",
    "player_points_rebounds": "Pts+Reb",
    "player_points_assists": "Pts+Ast",
    "player_rebounds_assists": "Reb+Ast",
    "player_pass_yds": "Pass Yards",
    "player_rush_yds": "Rush Yards",
    "player_reception_yds": "Rec Yards",
    "player_pass_tds": "Pass TDs",
    "player_anytime_td": "Anytime TD",
    "player_receptions": "Receptions",
    "player_pass_yds": "Pass Yards",
    "player_rush_yds": "Rush Yards",
    "player_reception_yds": "Rec Yards",
    "player_anytime_td": "Anytime TD",
}


@router.get("/today")
def get_today_props(request: Request, sport: str | None = None, market: str | None = None):
    session = get_session(request.app.state.engine)
    try:
        day = et_today()
        # If no props today, check tomorrow
        count = session.query(PlayerProp).join(Game).filter(Game.date == day).count()
        if count == 0:
            day = day + timedelta(days=1)

        AwayTeam = aliased(Team)
        query = (session.query(PlayerProp, Game, Team, AwayTeam)
            .join(Game, PlayerProp.game_id == Game.id)
            .join(Team, Game.home_team_id == Team.id)
            .join(AwayTeam, Game.away_team_id == AwayTeam.id)
            .filter(Game.date == day))

        if sport:
            query = query.filter(Game.sport == sport)
        if market:
            query = query.filter(PlayerProp.market == market)

        query = query.order_by(PlayerProp.market, PlayerProp.player_name, PlayerProp.outcome)

        analyzer = PropAnalyzer()

        # Cache opponent defensive ratings for teams playing today
        all_results = query.all()
        today_team_ids = set()
        for prop, game, home_team, away_team in all_results:
            today_team_ids.add(game.home_team_id)
            today_team_ids.add(game.away_team_id)

        team_def_cache = {}
        for tid in today_team_ids:
            def_stat = session.query(TeamStat).filter(
                TeamStat.team_id == tid,
                TeamStat.stat_type == "defensive_rating"
            ).order_by(TeamStat.id.desc()).first()
            if def_stat:
                team_def_cache[tid] = def_stat.value

        results = []
        for prop, game, home_team, away_team in all_results:
            season_avg = session.query(PlayerStat).filter_by(
                player_name=prop.player_name, stat_type="season_avg").first()
            recent = (session.query(PlayerStat)
                .filter_by(player_name=prop.player_name, stat_type="game_log")
                .order_by(PlayerStat.game_date.desc()).limit(5).all())
            # Determine opponent defensive rating
            opponent_def = None
            player_team_id = None
            if season_avg:
                player_team_id = season_avg.team_id
            elif recent:
                player_team_id = recent[0].team_id
            if player_team_id:
                opp_id = game.away_team_id if player_team_id == game.home_team_id else game.home_team_id
                opponent_def = team_def_cache.get(opp_id)

            analysis = analyzer.analyze(prop, season_avg, recent, opponent_def_rating=opponent_def)
            results.append({
                "id": prop.id,
                "game_id": game.id,
                "sport": game.sport,
                "date": str(game.date),
                "matchup": f"{away_team.abbreviation} @ {home_team.abbreviation}",
                "bookmaker": prop.bookmaker,
                "market": prop.market,
                "market_label": MARKET_LABELS.get(prop.market, prop.market),
                "player_name": prop.player_name,
                "outcome": prop.outcome,
                "line": prop.line,
                "odds": prop.odds,
                "projection": analysis.projection if analysis else None,
                "edge_pct": analysis.edge_pct if analysis else None,
                "confidence": analysis.confidence if analysis else None,
                "season_avg": analysis.season_avg if analysis else None,
                "recent_avg": analysis.recent_avg if analysis else None,
                "source": analysis.source if analysis else None,
                "is_stale": analysis.is_stale if analysis else None,
            })
        return results
    finally:
        session.close()


@router.get("/markets")
def list_markets():
    """Return available prop market keys with labels."""
    return [{"key": k, "label": v} for k, v in MARKET_LABELS.items()]

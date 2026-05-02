from datetime import date, datetime, timedelta, timezone
from fastapi import APIRouter, Request, HTTPException
from sqlalchemy import or_, and_, select
from sqlalchemy.orm import aliased
from backend.database import get_session
from backend.models import Game, Odds, Team

router = APIRouter()

# Grace window for stale "scheduled" rows: if start_time is more than this far
# in the past and the row hasn't moved to in_progress/final, the pipeline
# missed an update — likely postponed/canceled, hide from Today's Picks.
_SCHEDULED_GRACE = timedelta(hours=4)


@router.get("/today")
def get_today_games(request: Request, sport: str | None = None):
    """Today's bettable games — only what the user can actually wager on.

    Inclusion rule:
      - status == 'in_progress' (live), OR
      - status == 'scheduled' AND at least one Odds row attached AND
        (start_time is None OR start_time hasn't long-passed)

    Final games and phantom rows (no odds, stale start_time) are excluded.
    """
    session = get_session(request.app.state.engine)
    try:
        day = date.today()
        now = datetime.now(timezone.utc)
        grace_cutoff = now - _SCHEDULED_GRACE

        AwayTeam = aliased(Team)
        bettable = or_(
            Game.status == "in_progress",
            and_(
                Game.status == "scheduled",
                Game.id.in_(select(Odds.game_id).distinct()),
                or_(Game.start_time.is_(None), Game.start_time >= grace_cutoff),
            ),
        )

        query = (
            session.query(Game, Team, AwayTeam)
            .join(Team, Game.home_team_id == Team.id)
            .join(AwayTeam, Game.away_team_id == AwayTeam.id)
            .filter(Game.date == day, bettable)
        )
        if sport:
            query = query.filter(Game.sport == sport)
        query = query.order_by(Game.sport, Game.start_time.asc().nullslast(), Game.id)

        results = []
        for game, home, away in query.all():
            odds_rows = session.query(Odds).filter(Odds.game_id == game.id).all()
            best = odds_rows[0] if odds_rows else None
            results.append({
                "id": game.id,
                "sport": game.sport,
                "date": str(game.date),
                "status": game.status,
                "start_time": game.start_time.isoformat() if game.start_time else None,
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
                "odds_count": len(odds_rows),
                "last_meeting": _last_meeting(session, game),
                "home_l10_record": _l10_record(session, game.home_team_id, game.sport, game.date),
                "away_l10_record": _l10_record(session, game.away_team_id, game.sport, game.date),
            })
        return results
    finally:
        session.close()


def _last_meeting(session, game: Game) -> dict | None:
    """Return the most recent finalized prior game between the same two teams.

    Returns score in the orientation of the LAST meeting (its own home/away),
    plus a `winner` field of 'home' | 'away' | 'tie' relative to TODAY's game
    so the UI can show "MIN won last meeting" without recomputing orientation.
    """
    prior = (
        session.query(Game)
        .filter(
            Game.sport == game.sport,
            Game.status == "final",
            Game.date < game.date,
            or_(
                and_(Game.home_team_id == game.home_team_id,
                     Game.away_team_id == game.away_team_id),
                and_(Game.home_team_id == game.away_team_id,
                     Game.away_team_id == game.home_team_id),
            ),
        )
        .order_by(Game.date.desc())
        .first()
    )
    if not prior or prior.home_score is None or prior.away_score is None:
        return None
    if prior.home_score == prior.away_score:
        winner = "tie"
    elif prior.home_team_id == game.home_team_id:
        winner = "home" if prior.home_score > prior.away_score else "away"
    else:
        # Teams flipped between then and now — invert.
        winner = "away" if prior.home_score > prior.away_score else "home"
    return {
        "date": str(prior.date),
        "home_score": prior.home_score,
        "away_score": prior.away_score,
        "winner": winner,
    }


def _l10_record(session, team_id: int, sport: str, before: date) -> str:
    """Return 'W-L' over a team's last 10 finalized games before `before`."""
    games = (
        session.query(Game)
        .filter(
            Game.sport == sport,
            Game.status == "final",
            Game.date < before,
            or_(Game.home_team_id == team_id, Game.away_team_id == team_id),
        )
        .order_by(Game.date.desc())
        .limit(10)
        .all()
    )
    wins = 0
    losses = 0
    for g in games:
        if g.home_score is None or g.away_score is None:
            continue
        is_home = g.home_team_id == team_id
        team_score = g.home_score if is_home else g.away_score
        opp_score = g.away_score if is_home else g.home_score
        if team_score > opp_score:
            wins += 1
        elif team_score < opp_score:
            losses += 1
    return f"{wins}-{losses}"


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

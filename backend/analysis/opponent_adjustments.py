"""Adjust team stats for opponent strength (schedule-adjusted efficiency)."""

from sqlalchemy.orm import Session
from backend.models import Game, TeamStat


def compute_adjusted_efficiency(
    session: Session, team_id: int, sport: str, stat_type: str = "offensive_rating"
) -> float | None:
    """Compute schedule-adjusted efficiency for a team.

    Raw efficiency weighted by opponent defensive quality:
    adj_off = raw_off * (league_avg_def / avg_opponent_def)

    Returns adjusted value or None if insufficient data.
    """
    raw = _get_stat(session, team_id, stat_type)
    if raw is None:
        return None

    games = session.query(Game).filter(
        ((Game.home_team_id == team_id) | (Game.away_team_id == team_id)),
        Game.status == "final",
    ).all()

    if len(games) < 5:
        return raw

    opp_stat = "defensive_rating" if stat_type == "offensive_rating" else "offensive_rating"
    opp_ratings = []
    for g in games:
        opp_id = g.away_team_id if g.home_team_id == team_id else g.home_team_id
        opp_val = _get_stat(session, opp_id, opp_stat)
        if opp_val is not None:
            opp_ratings.append(opp_val)

    if not opp_ratings:
        return raw

    league_avg = 110.0
    avg_opp = sum(opp_ratings) / len(opp_ratings)

    if stat_type == "offensive_rating":
        adjustment = league_avg / avg_opp if avg_opp > 0 else 1.0
    else:
        adjustment = avg_opp / league_avg if league_avg > 0 else 1.0

    return raw * adjustment


def _get_stat(session: Session, team_id: int, stat_type: str) -> float | None:
    row = session.query(TeamStat).filter(
        TeamStat.team_id == team_id,
        TeamStat.stat_type == stat_type,
    ).first()
    return row.value if row else None

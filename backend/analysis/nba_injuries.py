"""When an NBA team's best scorer is out, his teammates' props move.

Measured by `backend.scripts.nba_injury_experiment` (2026-10-07, 2025-26
regular season, ESPN box scores, the 2026-09-19 duplicated logs removed).
When the star sits, a teammate gains a share of his own baseline (clustered
by team-game):

* points    +0.124  (95% CI +0.099..+0.149, p 4e-22)
* rebounds  +0.064  (p 3e-5)
* assists   +0.079  (p 5e-4)

The definitions are the experiment's, so the multipliers mean what was
measured:

* **Star**: the team's points-per-game leader this season among players
  with >= MIN_STAR_GAMES games for the team, from earlier games only. Early
  in a season nobody qualifies yet, so there is no adjustment until about
  the eleventh game -- last season's leader was not measured.
* **Out**: ESPN's roster lists him with a status that cannot play
  (`football_injuries.is_absent`, shared), AND he played in one of the
  team's last RECENT_GAMES games. A player gone for weeks was not "out" in
  the experiment, and the market has long since adjusted.

Prop lines were not tested (33 matched cases): better projections, not a
proven edge.
"""
from __future__ import annotations

from datetime import date

from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.analysis.football_injuries import is_absent
from backend.models import Game, PlayerStat

MIN_STAR_GAMES = 10
RECENT_GAMES = 5

#: market -> measured share of a teammate's baseline gained when the star is out.
TEAMMATE_EFFECT = {
    "player_points": 0.124,
    "player_rebounds": 0.064,
    "player_assists": 0.079,
}


def _season_start(session: Session, season: str) -> date | None:
    return (session.query(func.min(Game.date))
            .filter(Game.sport == "nba", Game.season == season).scalar())


def season_star(session: Session, team_id: int, season: str, before: date) -> str | None:
    """The team's points-per-game leader this season before `before`, among
    players with at least MIN_STAR_GAMES games for the team."""
    start = _season_start(session, season)
    if start is None:
        return None
    rows = (session.query(PlayerStat.player_name,
                          func.avg(PlayerStat.points).label("ppg"),
                          func.count(PlayerStat.id).label("games"))
            .filter(PlayerStat.sport == "nba", PlayerStat.stat_type == "game_log",
                    PlayerStat.team_id == team_id, PlayerStat.minutes > 0,
                    PlayerStat.game_date >= start, PlayerStat.game_date < before)
            .group_by(PlayerStat.player_name)
            .having(func.count(PlayerStat.id) >= MIN_STAR_GAMES)
            .order_by(func.avg(PlayerStat.points).desc(), PlayerStat.player_name)
            .first())
    return rows.player_name if rows else None


def recently_active(session: Session, team_id: int, player: str, before: date) -> bool:
    """Whether `player` played in one of the team's last RECENT_GAMES games."""
    recent = [d for (d,) in (session.query(PlayerStat.game_date)
                             .filter(PlayerStat.sport == "nba",
                                     PlayerStat.stat_type == "game_log",
                                     PlayerStat.team_id == team_id,
                                     PlayerStat.game_date < before)
                             .distinct().order_by(PlayerStat.game_date.desc())
                             .limit(RECENT_GAMES))]
    if not recent:
        return False
    return (session.query(PlayerStat.id)
            .filter(PlayerStat.sport == "nba", PlayerStat.stat_type == "game_log",
                    PlayerStat.team_id == team_id, PlayerStat.player_name == player,
                    PlayerStat.minutes > 0, PlayerStat.game_date.in_(recent))
            .first()) is not None


def star_out(session: Session, team_id: int, season: str, before: date,
             roster: dict | None) -> str | None:
    """The star's name if he is out tonight by the measured definition, else None."""
    if roster is None:
        return None
    star = season_star(session, team_id, season, before)
    if star is None or not is_absent(roster, star):
        return None
    return star if recently_active(session, team_id, star, before) else None


def injury_factor(market: str, player: str, missing_star: str | None) -> float | None:
    """The multiplier for a teammate's projection, or None when nothing applies."""
    effect = TEAMMATE_EFFECT.get(market)
    if effect is None or missing_star is None or player == missing_star:
        return None
    return 1.0 + effect

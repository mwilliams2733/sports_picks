"""How much an NFL defense allows through the air and on the ground.

Feeds the prop projection's matchup adjustment. Until 2026-10-04 football
props had none: `prop_analyzer` scaled by the opponent's `defensive_rating`,
a basketball stat (points per 100 possessions) never computed for football.

Measured by `backend.scripts.prop_matchup_experiment` (2022-2026 player-games,
nflverse play-by-play): the opponent's walk-forward yards allowed adds
information to a player's own season average for pass yards (c = +0.50,
n 1,679), rush yards (+0.52, n 3,125) and receiving yards (+0.43, n 7,982),
all p < 1e-7. c is the share of the defense factor that shows up in the
outcome, so the projection moves by MATCHUP_WEIGHT of it, not all of it.

The same run found no sign the BOOKS miss it (all three line coefficients
<= 0, wide intervals, 27 games). This makes our projection more accurate;
it is not evidence of an edge.

Yards allowed come from `player_stats` game logs, summed per team-game.
Checked against play-by-play for 2025-26: team pass and rush totals match
exactly (median and 10th-percentile ratio 1.000 on 668 team-games).
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta

from sqlalchemy.orm import Session

from backend.models import Game, PlayerStat

#: Share of the defense factor applied to the projection (measured c ~ 0.5).
MATCHUP_WEIGHT = 0.5

#: League-average pseudo-games a defense is shrunk with, so one game does not
#: define it. Fixed before measuring, not tuned.
SHRINK_GAMES = 3

#: A league mean needs a full week of team-games behind it.
MIN_LEAGUE_GAMES = 32

#: Which side of the defense each yardage market faces.
MARKET_SIDE = {
    "player_pass_yds": "pass",
    "player_reception_yds": "pass",
    "player_rush_yds": "rush",
}


def shrunk_factor(own: list[float], league: list[float]) -> float | None:
    """A defense's yards allowed per game over the league's, shrunk to 1.0.

    `own` and `league` must hold only games before the one being projected.
    None until the league has MIN_LEAGUE_GAMES team-games.
    """
    if len(league) < MIN_LEAGUE_GAMES:
        return None
    mean = sum(league) / len(league)
    if mean <= 0:
        return None
    return ((sum(own) + SHRINK_GAMES * mean) / (len(own) + SHRINK_GAMES)) / mean


def adjust(projection: float, factor: float) -> float:
    """Move a projection by MATCHUP_WEIGHT of the defense factor."""
    return projection * (1.0 + MATCHUP_WEIGHT * (factor - 1.0))


def yards_allowed(session: Session, sport: str, season: str,
                  before: date) -> dict[int, dict[str, list[float]]]:
    """team_id -> {"pass": [...], "rush": [...]} allowed per game, for
    `season`'s final games strictly before `before`.

    A game's date and its players' game-log date can differ by a day (UTC vs
    local), so the log is looked up on the game date, then a day either side.
    NFL teams never play on consecutive days, so that cannot cross games.
    """
    games = (session.query(Game)
             .filter(Game.sport == sport, Game.season == season,
                     Game.status == "final", Game.date < before)
             .all())
    if not games:
        return {}
    lo = min(g.date for g in games) - timedelta(days=1)
    offense: dict[tuple[int, date], list[float]] = defaultdict(lambda: [0.0, 0.0])
    for team_id, d, p, r in (session.query(PlayerStat.team_id, PlayerStat.game_date,
                                           PlayerStat.pass_yards, PlayerStat.rush_yards)
                             .filter(PlayerStat.sport == sport,
                                     PlayerStat.stat_type == "game_log",
                                     PlayerStat.game_date >= lo,
                                     PlayerStat.game_date <= before)):
        tot = offense[(team_id, d)]
        tot[0] += p or 0.0
        tot[1] += r or 0.0

    out: dict[int, dict[str, list[float]]] = defaultdict(lambda: {"pass": [], "rush": []})
    for g in games:
        for off_id, def_id in ((g.home_team_id, g.away_team_id),
                               (g.away_team_id, g.home_team_id)):
            tot = next((offense[(off_id, d)] for d in
                        (g.date, g.date - timedelta(days=1), g.date + timedelta(days=1))
                        if (off_id, d) in offense), None)
            if tot is None:  # no box score stored for that side
                continue
            out[def_id]["pass"].append(tot[0])
            out[def_id]["rush"].append(tot[1])
    return dict(out)


def matchup_factor(allowed: dict[int, dict[str, list[float]]], defense_team_id: int,
                   market: str) -> float | None:
    """The factor for a prop on `market` against `defense_team_id`, or None
    when the market is not a yardage market or the league is too young."""
    side = MARKET_SIDE.get(market)
    if side is None:
        return None
    league = [v for team in allowed.values() for v in team[side]]
    own = allowed.get(defense_team_id, {}).get(side, [])
    return shrunk_factor(own, league)

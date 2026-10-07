"""Who is missing from an NFL team today, and what it does to teammates' props.

Measured by `backend.scripts.injury_experiment` (2026-10-06, nflverse
2022-2026, regular season, `--leader-basis yards`). When a team's
season-to-date yards leader is absent, the other players' yards move by a
share of their own baseline (clustered by game):

* rushing leader out   -> other backs      +0.26 (95% CI +0.11..+0.41, n 204)
* receiving leader out -> other receivers  +0.17 (+0.10..+0.24, n 634)
* passing leader out   -> receivers        -0.07 (-0.13..-0.01, n 847)
* passing leader out   -> backs            +0.02 (p 0.77): not applied

Leaders are by YARDS because `player_stats` stores no attempts, targets or
carries. The experiment measured both: by touches the effects were +0.44 and
+0.12, so the definition matters and these constants belong to this one.

The same experiment found a missing QB fully priced into the closing spread
and total. These factors make projections more accurate; whether prop lines
already price them is unmeasured. It is not evidence of an edge.

"Absent" mirrors the experiment: an injury status other than one that may
still play (Questionable and milder), or no longer on the roster at all
(released, traded). Status comes from ESPN's team roster, which lists every
player's current injury including injured reserve. The game `summary`
payload was rejected: it caps its injury list at five per team and on
2026-10-06 left out Tampa Bay's starting QB, listed Out.
"""
from __future__ import annotations

import logging
from datetime import date

import httpx
from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.collectors.espn_http import get_with_retry
from backend.models import Game, PlayerStat
from backend.team_identity import espn_team_id

logger = logging.getLogger(__name__)

ROSTER_URL = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/teams/{}/roster"

#: Injury statuses under which a player may still play. Anything else (Out,
#: Doubtful, Injured Reserve, Suspension, PUP...) is absent, as the
#: experiment counted any weekly-roster status but active.
MAY_PLAY = frozenset({"Questionable", "Probable", "Day-To-Day", "Active"})

#: leader kind -> the game-log column that decides it.
LEADER_STAT = {"qb": "pass_yards", "target": "rec_yards", "carry": "rush_yards"}

#: market -> (leader kind whose absence lifts the player, measured effect).
TEAMMATE_OUT = {
    "player_reception_yds": ("target", 0.17),
    "player_rush_yds": ("carry", 0.26),
}

#: market -> measured effect of the team's passing leader being absent.
QB_OUT = {"player_reception_yds": -0.07}


def parse_roster(payload: dict) -> dict[str, str | None]:
    """player name -> current injury status (None if healthy), from ESPN's roster.

    Keyed by both `displayName` and `fullName`: game logs come from box
    scores, which use the display name.
    """
    out: dict[str, str | None] = {}
    for group in payload.get("athletes", []):
        for athlete in group.get("items", []):
            injuries = athlete.get("injuries") or []
            status = injuries[0].get("status") if injuries else None
            for key in ("displayName", "fullName"):
                if athlete.get(key):
                    out[athlete[key]] = status
    return out


def is_absent(roster: dict[str, str | None], player: str | None) -> bool:
    if player is None:
        return False
    if player not in roster:
        return True
    status = roster[player]
    return status is not None and status not in MAY_PLAY


async def fetch_roster(client: httpx.AsyncClient, team_label: str) -> dict[str, str | None] | None:
    """The team's roster statuses, or None when it cannot be read.

    None means unknown -- no adjustment -- never "everyone is absent".
    """
    espn_id = espn_team_id("nfl", team_label)
    if espn_id is None:
        logger.warning("injuries: no ESPN id for %r", team_label)
        return None
    try:
        resp = await get_with_retry(client, ROSTER_URL.format(espn_id))
        resp.raise_for_status()
        roster = parse_roster(resp.json())
    except Exception as exc:
        logger.warning("injuries: roster for %s failed: %s", team_label, exc)
        return None
    return roster or None


def season_leaders(session: Session, team_id: int, season: str,
                   before: date) -> dict[str, str]:
    """{"qb"|"target"|"carry": player} by yards in this season's game logs
    strictly before `before`. A kind with no yards yet has no leader."""
    start = (session.query(func.min(Game.date))
             .filter(Game.sport == "nfl", Game.season == season).scalar())
    if start is None:
        return {}
    leaders = {}
    for kind, stat in LEADER_STAT.items():
        col = getattr(PlayerStat, stat)
        row = (session.query(PlayerStat.player_name, func.sum(col).label("total"))
               .filter(PlayerStat.sport == "nfl", PlayerStat.stat_type == "game_log",
                       PlayerStat.team_id == team_id,
                       PlayerStat.game_date >= start, PlayerStat.game_date < before)
               .group_by(PlayerStat.player_name)
               .order_by(func.sum(col).desc(), PlayerStat.player_name)
               .first())
        if row is not None and row.total:
            leaders[kind] = row.player_name
    return leaders


def injury_factor(market: str, player: str, leaders: dict[str, str],
                  roster: dict[str, str | None] | None) -> float | None:
    """The multiplier for `player`'s projection on `market`, or None when
    nothing applies (unmeasured market, unknown roster, nobody missing)."""
    if roster is None or (market not in TEAMMATE_OUT and market not in QB_OUT):
        return None
    shift = 0.0
    if market in TEAMMATE_OUT:
        kind, effect = TEAMMATE_OUT[market]
        leader = leaders.get(kind)
        if leader != player and is_absent(roster, leader):
            shift += effect
    if market in QB_OUT and is_absent(roster, leaders.get("qb")):
        shift += QB_OUT[market]
    return 1.0 + shift if shift else None

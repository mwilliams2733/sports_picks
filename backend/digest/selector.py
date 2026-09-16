"""Choose and rank what goes into the daily digest.

Pure with respect to time and network: the caller supplies the target date
and a session. No sending, no formatting.
"""
import json
from dataclasses import dataclass
from datetime import datetime

from backend.config import is_sport_in_season
from backend.data_types import PickFactor
from backend.analysis.rationale import render_rationale
from backend.models import Game, PickModel, Team


@dataclass(frozen=True)
class DigestPick:
    sport: str
    matchup: str
    pick_value: str
    odds: int
    confidence: int
    edge_pct: float
    rationale: str


@dataclass(frozen=True)
class DigestSection:
    sport: str
    picks: list[DigestPick]
    props: list[DigestPick]


def _matchup(session, game: Game) -> str:
    home = session.get(Team, game.home_team_id)
    away = session.get(Team, game.away_team_id)
    home_name = home.name if home else "Home"
    away_name = away.name if away else "Away"
    return f"{away_name} @ {home_name}"


def _rationale_for(session, pick: PickModel, game: Game) -> str:
    if not pick.rationale_json:
        return ""
    try:
        raw = json.loads(pick.rationale_json)
    except (ValueError, TypeError):
        return ""
    if not isinstance(raw, list):
        return ""
    factors = [
        PickFactor(code=f.get("code", ""), side=f.get("side", "home"),
                   strength=f.get("strength", "moderate"))
        for f in raw if isinstance(f, dict)
    ]
    home = session.get(Team, game.home_team_id)
    away = session.get(Team, game.away_team_id)
    return render_rationale(factors,
                            home.name if home else "Home",
                            away.name if away else "Away")


def select_digest(session, target_date, sports, seasons, max_per_sport: int = 5):
    """Return one DigestSection per active sport that has something to show."""
    sections: list[DigestSection] = []

    for sport in sports:
        if not is_sport_in_season(sport, seasons, target_date):
            continue
        games = (
            session.query(Game)
            .filter(Game.sport == sport, Game.date == target_date)
            .all()
        )
        if not games:
            continue
        games_by_id = {g.id: g for g in games}

        # `prop_pipeline` writes its analyzed props into this SAME picks table
        # with pick_type="prop". They must never be ranked against game picks:
        # a prop's edge_pct is (prob - 0.5) * 200, which ignores the prop's
        # price, while a game pick's edge is measured against the de-vigged
        # market. Mixing them floats juiced props above better game picks.
        picks = (
            session.query(PickModel)
            .filter(PickModel.game_id.in_(list(games_by_id)),
                    PickModel.confidence >= 1,
                    PickModel.pick_type != "prop")
            .all()
        )

        def _pick_sort_key(p):
            # start_time is nullable, and stored rows may be naive or aware.
            # Comparing a datetime to a date, or a naive to an aware datetime,
            # raises TypeError mid-sort — normalize to naive and push missing
            # start times to the end.
            st = games_by_id[p.game_id].start_time
            when = st.replace(tzinfo=None) if st is not None else datetime.max
            return (-p.confidence, -(p.edge_pct or 0.0), when, p.id or 0)

        picks.sort(key=_pick_sort_key)

        digest_picks = [
            DigestPick(
                sport=sport,
                matchup=_matchup(session, games_by_id[p.game_id]),
                pick_value=p.pick_value,
                odds=p.odds_at_pick or -110,
                confidence=p.confidence,
                edge_pct=round(p.edge_pct or 0.0, 1),
                rationale=_rationale_for(session, p, games_by_id[p.game_id]),
            )
            for p in picks[:max_per_sport]
        ]

        # Props come from the same table but are ranked among themselves only.
        props = (
            session.query(PickModel)
            .filter(PickModel.game_id.in_(list(games_by_id)),
                    PickModel.confidence >= 1,
                    PickModel.pick_type == "prop")
            .all()
        )
        props.sort(key=_pick_sort_key)
        digest_props = [
            DigestPick(
                sport=sport,
                matchup=_matchup(session, games_by_id[p.game_id]),
                pick_value=p.pick_value,
                odds=p.odds_at_pick or -110,
                confidence=p.confidence,
                edge_pct=round(p.edge_pct or 0.0, 1),
                rationale=_rationale_for(session, p, games_by_id[p.game_id]),
            )
            for p in props[:max_per_sport]
        ]

        if not digest_picks and not digest_props:
            continue
        sections.append(DigestSection(sport=sport, picks=digest_picks, props=digest_props))

    return sections

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


def _dedupe_latest(picks: list[PickModel]) -> list[PickModel]:
    """Collapse repeated picks to the most recently created row.

    `_run_window` runs once per game window per sport per day and regenerates
    picks for ALL of today's games each time, and `generate_and_store_picks`
    inserts unconditionally (there is no unique constraint on `picks`). Three
    NBA windows therefore leave three identical "HOME ML" rows per game. Left
    alone, the digest's "top 5" is the same one or two picks repeated.

    Identity is (game_id, pick_type, pick_value). `created_at` is nullable in
    practice (rows written before the default, or by raw SQL), so a missing
    timestamp sorts oldest and never displaces a real one. `id` breaks ties,
    since it is monotonic for inserts into the same table.
    """
    latest: dict[tuple, PickModel] = {}
    for p in picks:
        key = (p.game_id, p.pick_type, p.pick_value)
        incumbent = latest.get(key)
        if incumbent is None or _recency(p) > _recency(incumbent):
            latest[key] = p
    return list(latest.values())


def _recency(pick: PickModel) -> tuple:
    created = pick.created_at
    if created is None:
        return (datetime.min, pick.id or 0)
    # Rows can come back naive or aware depending on how they were written;
    # comparing the two raises TypeError mid-sort.
    return (created.replace(tzinfo=None), pick.id or 0)


def select_digest(session, target_date, sports, seasons, max_per_sport: int = 5,
                  max_odds: int | None = 150):
    """Return one DigestSection per active sport that has something to show.

    Game picks are filtered by a price ceiling to avoid emailing longshots that
    carry high edges but poor win rates. Measured on the graded book (moneyline,
    five team sports, 129 decided picks):

    | price band | n | win % | units |
    |---|---|---|---|
    | favorite at -150 or shorter | 15 | 73% | +1.3 |
    | favorite -149 to -101 | 14 | 43% | -3.4 |
    | dog +100 to +200 | 42 | 40% | -3.0 |
    | dog longer than +200 | 58 | 16% | -16.6 |

    A pick with no stored price is kept: unknown is not a longshot, and the
    renderer shows it at -110 anyway. The ceiling is applied here rather than
    in the strategy, so that the picks still exist for grading and measurement;
    only what gets emailed changes.
    """
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

        picks = _dedupe_latest(picks)

        if max_odds is not None:
            # A pick with no stored price is kept: unknown is not a longshot,
            # and the renderer shows it at -110 anyway.
            picks = [p for p in picks
                     if p.odds_at_pick is None or p.odds_at_pick <= max_odds]

        def _pick_sort_key(p):
            st = games_by_id[p.game_id].start_time
            when = st.replace(tzinfo=None) if st is not None else datetime.max
            # Win probability first. Confidence is a threshold on edge, and
            # edge is model minus market in absolute points, which is largest
            # exactly where the model is most wrong (long-priced underdogs).
            # A pick with no stored probability sorts after every pick that
            # has one; among those, the old order still applies.
            prob = p.model_prob if p.model_prob is not None else -1.0
            return (-prob, -p.confidence, -(p.edge_pct or 0.0), when, p.id or 0)

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
        props = _dedupe_latest(props)
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

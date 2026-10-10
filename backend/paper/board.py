"""The sportsbook lobby's board: every bettable game in a date window, priced.

Each side is priced by ``pricing.game_quotes`` -- the function a bet is
charged by -- so the board and the bet cannot disagree. A game whose sides
are all refused (stale, unquoted) is still listed: the lobby shows its tiles
locked with the refusal message rather than hiding it.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import aliased

from backend.analysis.odds_utils import prob_to_american
from backend.analysis.rationale import factors_from_json, pick_note
from backend.models import Game, PickModel, Team
from backend.paper import pricing
from backend.time_utils import ET, game_start_utc

MAX_DAYS = 14
GAME_PICK_TYPES = ("moneyline", "spread", "over_under")
_NO_KICKOFF = datetime.max.replace(tzinfo=timezone.utc)


def clamp_days(days: int) -> int:
    return max(1, min(MAX_DAYS, days))


def _model_picks(session, game_ids: list[int]) -> dict[int, PickModel]:
    """The highest-edge published model pick on a game market, per game."""
    if not game_ids:
        return {}
    rows = (session.query(PickModel)
            .filter(PickModel.game_id.in_(game_ids), PickModel.published(),
                    PickModel.by_model(), PickModel.pick_type.in_(GAME_PICK_TYPES))
            .order_by(PickModel.edge_pct.desc(), PickModel.id)
            .all())
    best: dict[int, PickModel] = {}
    for p in rows:
        best.setdefault(p.game_id, p)
    return best


def _model_pick_view(p: PickModel | None, sport: str, home: str, away: str) -> dict | None:
    if p is None:
        return None
    return {"pick_type": p.pick_type, "pick_value": p.pick_value,
            "odds": p.odds_at_pick, "edge_pct": p.edge_pct,
            "reasoning": _reasoning(p, sport, home, away)}


def _reasoning(p: PickModel, sport: str, home: str, away: str) -> dict | None:
    """Why the model made this pick: its own numbers and the note
    `rationale.pick_note` writes from them. None without a probability."""
    if p.model_prob is None:
        return None
    return {
        "model_prob": round(p.model_prob, 4),
        "market_prob": None if p.market_prob_novig is None else round(p.market_prob_novig, 4),
        "edge_pct": p.edge_pct,
        "fair_odds": prob_to_american(p.model_prob) if 0 < p.model_prob < 1 else None,
        "units": p.suggested_unit_size,
        "note": pick_note(sport=sport, pick_type=p.pick_type, pick_value=p.pick_value,
                          home=home, away=away, model_prob=p.model_prob,
                          market_prob=p.market_prob_novig, edge_pct=p.edge_pct,
                          odds=p.odds_at_pick, factors=factors_from_json(p.rationale_json)),
    }


def build_board(session, *, sport: str | None = None, days: int = 7,
                now: datetime | None = None) -> list[dict]:
    now = now or datetime.now(timezone.utc)
    first = now.astimezone(ET).date()
    last = first + timedelta(days=clamp_days(days))
    Away = aliased(Team)
    q = (session.query(Game, Team, Away)
         .join(Team, Game.home_team_id == Team.id)
         .join(Away, Game.away_team_id == Away.id)
         .filter(Game.date >= first, Game.date < last))
    if sport:
        q = q.filter(Game.sport == sport)
    rows = [(g, h, a) for g, h, a in q.all() if pricing.open_for_betting(g, now)]
    rows.sort(key=lambda r: (r[0].date, game_start_utc(r[0]) or _NO_KICKOFF, r[0].id))
    picks = _model_picks(session, [g.id for g, _, _ in rows])
    board = []
    for game, home, away in rows:
        start = game_start_utc(game)
        board.append({
            "id": game.id,
            "sport": game.sport,
            "date": str(game.date),
            "start_time": start.isoformat() if start else None,
            "home_team": home.abbreviation,
            "away_team": away.abbreviation,
            "quotes": pricing.game_quotes(session, game, now),
            # Counting priced props costs one prop_quotes call per game: 0.85 s of
            # a 1.35 s 7-day board (94 games, 2026-10-07). The lobby shows
            # "Props ›" without a count; the game page prices them on demand.
            "prop_count": None,
            "model_pick": _model_pick_view(picks.get(game.id), game.sport,
                                           home.abbreviation, away.abbreviation),
        })
    return board

"""A player's bets as sportsbook tickets (spec 2026-10-07 §7).

One ticket per straight bet and one per parlay -- never a parlay's legs as
separate bets -- each carrying its legs' games, newest first. Read-only: the
"to win" figure is settlement's own formula, so a ticket promises exactly
what grading will pay.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import aliased

from backend.models import Game, PaperPick, Parlay, Team
from backend.pipeline.grader import payout_for
from backend.pipeline.paper_settlement import parlay_win_payout
from backend.time_utils import as_utc, game_start_utc

_NO_KICKOFF = datetime.max.replace(tzinfo=timezone.utc)


def _game(game, home, away) -> dict:
    start = game_start_utc(game)
    return {"id": game.id, "sport": game.sport, "home_team": home.abbreviation,
            "away_team": away.abbreviation, "start_time": start.isoformat() if start else None,
            "status": game.status, "home_score": game.home_score, "away_score": game.away_score,
            "live_detail": None}                     # Phase 4 fills this in


def _leg(pick, game, home, away) -> dict:
    return {"pick_type": pick.pick_type, "pick_value": pick.pick_value, "odds": pick.odds,
            "prop_player": pick.prop_player, "prop_market": pick.prop_market,
            "result": pick.result, "game": _game(game, home, away)}


def tickets(session, user_id: int) -> list[dict]:
    Home, Away = aliased(Team), aliased(Team)
    rows = (session.query(PaperPick, Game, Home, Away)
            .join(Game, Game.id == PaperPick.game_id)
            .join(Home, Home.id == Game.home_team_id)
            .join(Away, Away.id == Game.away_team_id)
            .filter(PaperPick.user_id == user_id)
            .all())
    out: list[tuple[datetime, int, dict]] = []
    parlay_legs: dict[int, list[tuple[datetime, PaperPick, dict]]] = {}
    for pick, game, home, away in rows:
        leg = _leg(pick, game, home, away)
        if pick.parlay_id is not None:
            parlay_legs.setdefault(pick.parlay_id, []).append(
                (game_start_utc(game) or _NO_KICKOFF, pick, leg))
            continue
        placed = as_utc(pick.created_at)
        out.append((placed, pick.id, {
            "kind": "straight", "id": pick.id, "stake": pick.stake, "odds": pick.odds,
            "to_win": round(pick.stake * payout_for("win", pick.odds), 2),
            "result": pick.result, "payout": pick.payout, "created_at": placed.isoformat(),
            "sgp": False, "legs": [leg]}))
    for parlay in session.query(Parlay).filter(Parlay.user_id == user_id).all():
        legs = sorted(parlay_legs.get(parlay.id, []), key=lambda x: (x[0], x[1].id))
        placed = as_utc(parlay.created_at)
        out.append((placed, parlay.id, {
            "kind": "parlay", "id": parlay.id, "stake": parlay.stake, "odds": parlay.combined_odds,
            "to_win": round(parlay_win_payout(parlay.stake, [p.odds for _, p, _ in legs]), 2),
            "result": parlay.result, "payout": parlay.payout, "created_at": placed.isoformat(),
            "sgp": len({p.game_id for _, p, _ in legs}) < len(legs),
            "legs": [leg for _, _, leg in legs]}))
    out.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return [t for _, _, t in out]

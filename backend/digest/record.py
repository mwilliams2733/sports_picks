"""What the digest sent, and how those picks did.

Two halves:

* :func:`record_emailed` writes one :class:`EmailedPick` per pick in a
  digest that was actually sent. It copies the value and price the email
  showed, because the stored pick can be refreshed after the send.
* :func:`emailed_record` grades those copies with the grader's own
  functions -- ``grade_pick``, ``grade_prop_pick``, ``payout_for`` and
  ``prop_box_score`` -- so this can never settle a pick differently from
  ``pick_results``, only from a different (the emailed) snapshot.

Graded on read rather than stored: there is no grading job to keep in step,
and a box score that lands late is picked up the next time anyone asks.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date

from sqlalchemy.orm import Session

from backend.analysis.scorecard import Bet, summarize
from backend.models import EmailedPick, Game, PickModel
from backend.pipeline.grader import (grade_pick, grade_prop_pick, payout_for,
                                     prop_box_score)

logger = logging.getLogger(__name__)


def record_emailed(session: Session, sections, digest_date: date) -> int:
    """Record every pick in ``sections`` as sent on ``digest_date``.

    Returns how many rows were added. A pick already recorded for that date
    is skipped, so a resend does not count the same bet twice.
    """
    items = [p for s in sections for p in (*s.picks, *s.props)
             if p.pick_id is not None]
    if not items:
        return 0
    stored = {p.id: p for p in session.query(PickModel).filter(
        PickModel.id.in_([i.pick_id for i in items]))}
    already = {pid for (pid,) in session.query(EmailedPick.pick_id).filter(
        EmailedPick.digest_date == digest_date)}
    added = 0
    for item in items:
        pick = stored.get(item.pick_id)
        if pick is None or item.pick_id in already:
            continue
        session.add(EmailedPick(
            digest_date=digest_date, pick_id=pick.id, game_id=pick.game_id,
            sport=item.sport, pick_type=pick.pick_type,
            # As emailed, not as stored: see EmailedPick.
            pick_value=item.pick_value, odds=item.odds,
            prop_player=pick.prop_player, prop_market=pick.prop_market,
            confidence=item.confidence,
            best_book=item.best_book, best_odds=item.best_odds))
        already.add(item.pick_id)
        added += 1
    session.commit()
    return added


def grade_emailed(session: Session, row: EmailedPick, game: Game):
    """``(result, units)`` for one emailed pick, or None while unsettled."""
    if game.status != "final" or game.home_score is None or game.away_score is None:
        return None
    if row.pick_type == "prop":
        if not (row.prop_player and row.prop_market):
            return None
        outcome = grade_prop_pick(
            row.pick_value, row.prop_market,
            prop_box_score(session, row.prop_player, game.date))
        if outcome is None:
            return None
        result = outcome[0]
    else:
        outcome = grade_pick(row.pick_type, row.pick_value,
                             game.home_score, game.away_score, row.odds,
                             sport=row.sport)
        if outcome is None:
            return None
        result = outcome[0]
    # One unit staked, priced at the emailed odds.
    return result, payout_for(result, row.odds)


def _as_bet(session: Session, row: EmailedPick, game: Game) -> Bet:
    graded = grade_emailed(session, row, game)
    result, units = graded if graded is not None else (None, 0.0)
    return Bet(result=result, stake=1.0, profit=units, odds=row.odds,
               day=row.digest_date, stars=row.confidence)


def emailed_bets(session: Session, kind: str | None = None) -> list[Bet]:
    """Every emailed pick as a 1u Bet, oldest first. kind: "game", "prop" or None."""
    q = (session.query(EmailedPick, Game).join(Game, Game.id == EmailedPick.game_id)
         .order_by(EmailedPick.digest_date.asc(), EmailedPick.id.asc()))
    if kind == "game":
        q = q.filter(EmailedPick.pick_type != "prop")
    elif kind == "prop":
        q = q.filter(EmailedPick.pick_type == "prop")
    return [_as_bet(session, row, game) for row, game in q.all()]


@dataclass
class RecordRow:
    sport: str
    kind: str          # "game" or "prop"
    wins: int
    losses: int
    pushes: int
    pending: int
    units: float
    win_pct: float | None


def emailed_record(session: Session, since: date | None = None,
                   until: date | None = None) -> list[RecordRow]:
    """The record of everything the digest sent, by sport and game/prop."""
    q = (session.query(EmailedPick, Game).join(Game, Game.id == EmailedPick.game_id)
         .order_by(EmailedPick.digest_date.asc(), EmailedPick.id.asc()))
    if since is not None:
        q = q.filter(EmailedPick.digest_date >= since)
    if until is not None:
        q = q.filter(EmailedPick.digest_date <= until)
    buckets: dict[tuple[str, str], list[Bet]] = {}
    for row, game in q.all():
        kind = "prop" if row.pick_type == "prop" else "game"
        buckets.setdefault((row.sport, kind), []).append(_as_bet(session, row, game))
    rows = []
    for (sport, kind), bets in sorted(buckets.items()):
        s = summarize(bets)
        rows.append(RecordRow(sport=sport, kind=kind, wins=s.wins, losses=s.losses,
                              pushes=s.pushes, pending=s.pending, units=s.profit,
                              win_pct=s.win_rate))
    return rows

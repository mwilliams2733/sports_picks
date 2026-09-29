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
            confidence=item.confidence))
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
                             game.home_score, game.away_score, row.odds)
        if outcome is None:
            return None
        result = outcome[0]
    # One unit staked, priced at the emailed odds.
    return result, payout_for(result, row.odds)


@dataclass
class RecordRow:
    sport: str
    kind: str          # "game" or "prop"
    wins: int = 0
    losses: int = 0
    pushes: int = 0
    pending: int = 0
    units: float = 0.0

    @property
    def win_pct(self) -> float | None:
        """Wins over decided results. None with nothing decided yet."""
        decided = self.wins + self.losses
        return self.wins / decided if decided else None


def emailed_record(session: Session, since: date | None = None,
                   until: date | None = None) -> list[RecordRow]:
    """The record of everything the digest sent, by sport and game/prop."""
    q = session.query(EmailedPick, Game).join(Game, Game.id == EmailedPick.game_id)
    if since is not None:
        q = q.filter(EmailedPick.digest_date >= since)
    if until is not None:
        q = q.filter(EmailedPick.digest_date <= until)
    rows: dict[tuple[str, str], RecordRow] = {}
    for emailed, game in q.all():
        kind = "prop" if emailed.pick_type == "prop" else "game"
        rec = rows.setdefault((emailed.sport, kind),
                              RecordRow(sport=emailed.sport, kind=kind))
        graded = grade_emailed(session, emailed, game)
        if graded is None:
            rec.pending += 1
            continue
        result, units = graded
        if result == "win":
            rec.wins += 1
        elif result == "loss":
            rec.losses += 1
        else:
            rec.pushes += 1
        rec.units += units
    return sorted(rows.values(), key=lambda r: (r.sport, r.kind))

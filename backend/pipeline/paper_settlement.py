"""Settle paper parlays once every leg is graded.

Legs are PaperPick rows (stake 0) graded by the ordinary paper-grading
passes; the parlay's own stake and payout live on its Parlay row. Settlement
used to happen only at placement, so a parlay placed before its games never
settled (plan 001 pinned this as a known bug).

Rules, unchanged from placement-time settlement: any losing leg loses the
stake; otherwise any push pushes the whole parlay; otherwise it wins at the
product of the legs' decimal prices.
"""
from datetime import date, datetime, timedelta, timezone

from backend.models import Game, PaperPick, Parlay
from backend.pipeline.grader import (
    grade_pick, grade_prop_pick, payout_for, prop_box_score,
)
from backend.time_utils import et_today

#: How long an ungradeable bet waits before it is pushed. One day is not
#: enough: a wrongly-canceled game has been corrected before, and prop box
#: scores can land the next day.
VOID_AFTER_DAYS = 2
#: Statuses that mean the game will not produce a result on this row.
UNPLAYED = ("canceled", "postponed")


def grade_paper_picks(session, today: date | None = None) -> list[PaperPick]:
    """Grade every pending paper pick whose game is final; return those graded.

    Then push what can never be graded (see `_push_ungradeable`); those are
    in the returned list too, with result "push".

    The one paper-grading path: the scheduler and the owner's /users/grade
    route both call it, so they cannot settle a bet differently. Payouts go
    through ``payout_for``, which books an unusable stored price (legacy rows
    at odds 0) as 0.0 rather than raising -- this runs inside the morning
    scout, and one bad row must not take the slate down with it.
    """
    pending = (
        session.query(PaperPick, Game)
        .join(Game, PaperPick.game_id == Game.id)
        .filter(PaperPick.result.is_(None))
        .filter(Game.status == "final")
        .all()
    )
    graded = []
    for pick, game in pending:
        if game.home_score is None or game.away_score is None:
            continue
        if pick.pick_type == "prop" and pick.prop_player and pick.prop_market:
            player_stat = prop_box_score(session, pick.prop_player, game.date)
            outcome = grade_prop_pick(pick.pick_value, pick.prop_market, player_stat)
        else:
            outcome = grade_pick(pick.pick_type, pick.pick_value,
                                 game.home_score, game.away_score, pick.odds,
                                 sport=game.sport)
        if outcome is None:
            continue
        pick.result = outcome[0]
        pick.payout = pick.stake * payout_for(pick.result, pick.odds)
        pick.graded_at = datetime.now(timezone.utc)
        graded.append(pick)
    graded.extend(_push_ungradeable(session, today or et_today()))
    session.commit()
    return graded


def _push_ungradeable(session, today: date) -> list[PaperPick]:
    """Settle as a push every pending bet that can never be graded.

    Owner ruling 2026-09-30. Since open stakes are reserved against what a
    player can bet, a bet that never grades would hold its stake forever --
    a canceled or postponed game, or a prop whose stat never arrives (MLB
    props and anytime-TD rows cannot be graded at all). Once its game is
    `VOID_AFTER_DAYS` past and still ungraded it is pushed: stake returned,
    counted as no bet. A parlay leg pushed here pushes its parlay under the
    existing rule in `settle_parlays`.

    A still-``scheduled`` game is left alone: it may yet finalize.
    Runs after the grading pass, so a pending bet on a ``final`` game here
    is one that pass could not grade.
    """
    cutoff = today - timedelta(days=VOID_AFTER_DAYS)
    rows = (
        session.query(PaperPick)
        .join(Game, PaperPick.game_id == Game.id)
        .filter(PaperPick.result.is_(None))
        .filter(Game.status.in_(UNPLAYED + ("final",)))
        .filter(Game.date <= cutoff)
        .all()
    )
    now = datetime.now(timezone.utc)
    for pick in rows:
        pick.result, pick.payout, pick.graded_at = "push", 0.0, now
    return rows


def settle_parlays(session) -> int:
    settled = 0
    for parlay in session.query(Parlay).filter(Parlay.result.is_(None)).all():
        legs = session.query(PaperPick).filter(PaperPick.parlay_id == parlay.id).all()
        if len(legs) < 2 or any(leg.result is None for leg in legs):
            continue
        results = {leg.result for leg in legs}
        if "loss" in results:
            parlay.result, parlay.payout = "loss", -parlay.stake
        elif "push" in results:
            parlay.result, parlay.payout = "push", 0.0
        else:
            decimal = 1.0
            for leg in legs:
                decimal *= 1 + payout_for("win", leg.odds)
            parlay.result, parlay.payout = "win", parlay.stake * (decimal - 1)
        settled += 1
    session.commit()
    return settled

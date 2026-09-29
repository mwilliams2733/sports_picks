"""Settle paper parlays once every leg is graded.

Legs are PaperPick rows (stake 0) graded by the ordinary paper-grading
passes; the parlay's own stake and payout live on its Parlay row. Settlement
used to happen only at placement, so a parlay placed before its games never
settled (plan 001 pinned this as a known bug).

Rules, unchanged from placement-time settlement: any losing leg loses the
stake; otherwise any push pushes the whole parlay; otherwise it wins at the
product of the legs' decimal prices.
"""
from backend.analysis.odds_utils import calculate_payout
from backend.models import PaperPick, Parlay


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
                decimal *= 1 + calculate_payout(leg.odds)
            parlay.result, parlay.payout = "win", parlay.stake * (decimal - 1)
        settled += 1
    session.commit()
    return settled

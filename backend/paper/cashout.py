"""Cash out (sportsbook spec 2026-10-07 §9): pre-game only, 5% margin.

``offer = stake x D_placed x p_now x (1 - CASH_OUT_MARGIN)``, rounded down to
the cent. D_placed comes from settlement's own ``payout_for`` (a parlay: the
product of its legs, exactly ``parlay_win_payout``'s). p_now is the no-vig
chance of the same side at the same line, from the consensus pair that
``pricing.price`` -- the function that prices every bet -- quotes now. A
parlay's p_now is the product over its legs.

The endpoint and the My Bets tickets both call :func:`offer`, so the offer a
ticket shows is the one the endpoint pays.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, replace
from datetime import datetime, timezone

from backend.analysis.odds_utils import american_to_implied_prob, parse_pick_line, remove_vig
from backend.models import Game, PaperPick, Parlay, PlayerProp
from backend.paper import pricing
from backend.paper.pricing import GAME_MARKETS, GameBet, PricingError, PropBet
from backend.pipeline.grader import payout_for

CASH_OUT_MARGIN = 0.05

MESSAGES = {
    "game_started": "A game in this bet has started — cash out is pre-game only.",
    "line_moved": "The line has moved since you bet, so there's no cash out offer.",
    "stale": "The price is stale — ask Marcus to refresh.",
    "not_quoted": "No book is quoting this bet right now.",
    "not_gradeable": "This market can't be graded, so it can't be cashed out.",
    "settled": "This bet has already settled.",
}

_FLIP = {"HOME": "AWAY", "AWAY": "HOME", "Over": "Under", "Under": "Over"}
_PROP = re.compile(r"\b(Over|Under) (\d+(?:\.\d+)?)\b")


class CashOutUnavailable(Exception):
    """No offer for this bet now. ``reason`` is a stable code."""

    def __init__(self, reason: str):
        self.reason = reason
        self.message = MESSAGES[reason]
        super().__init__(self.message)


@dataclass(frozen=True)
class Offer:
    amount: float
    p_now: float
    d_placed: float


def _stored_bet(pick: PaperPick) -> tuple[GameBet | PropBet, float | None]:
    """The bet a stored pick is, and its line, parsed back out of pick_value
    ("HOME -3.5", "Over 220.5", "QB One Over 225.5 Pass Yds")."""
    if pick.pick_type == "prop":
        m = _PROP.search(pick.pick_value or "")
        if not (m and pick.prop_player and pick.prop_market):
            raise CashOutUnavailable("not_quoted")
        line = float(m.group(2))
        return PropBet(pick.game_id, pick.prop_player, pick.prop_market, m.group(1), line), line
    side = (pick.pick_value or "").split(" ")[0]
    if (pick.pick_type, side) not in GAME_MARKETS:
        raise CashOutUnavailable("not_quoted")
    if pick.pick_type == "moneyline":
        return GameBet(pick.game_id, pick.pick_type, side), None
    line = parse_pick_line(pick.pick_value)
    if line is None:
        raise CashOutUnavailable("not_quoted")
    return GameBet(pick.game_id, pick.pick_type, side), line


def _opposite(bet, line):
    if isinstance(bet, PropBet):
        return replace(bet, outcome=_FLIP[bet.outcome]), line
    mirror = None if line is None else (-line if bet.pick_type == "spread" else line)
    return GameBet(bet.game_id, bet.pick_type, _FLIP[bet.side]), mirror


def _prop_moved(session, bet: PropBet) -> bool:
    return (session.query(PlayerProp.id)
            .filter(PlayerProp.game_id == bet.game_id, PlayerProp.player_name == bet.prop_player,
                    PlayerProp.market == bet.prop_market, PlayerProp.outcome == bet.outcome,
                    PlayerProp.line.isnot(None), PlayerProp.line != bet.line)
            .first()) is not None


def _quote(session, game, bet, line, now, at_line: bool = False) -> pricing.Quote:
    """This side at its quoted line (``line_moved`` if that is not the bet's
    line), or -- ``at_line`` -- the other side priced at exactly ``line``."""
    try:
        q = pricing.price(session, game, bet, now,
                          at_line=line if at_line and isinstance(bet, GameBet) else None)
    except PricingError as e:
        if e.reason == "not_quoted" and isinstance(bet, PropBet) and _prop_moved(session, bet):
            raise CashOutUnavailable("line_moved") from None
        raise CashOutUnavailable(e.reason) from None
    if line is not None and abs(q.line - line) > 1e-9:
        raise CashOutUnavailable("line_moved")
    return q


def _leg_chance(session, pick: PaperPick, now: datetime) -> float:
    game = session.get(Game, pick.game_id)
    bet, line = _stored_bet(pick)
    mine = _quote(session, game, bet, line, now)
    other_bet, other_line = _opposite(bet, line)
    theirs = _quote(session, game, other_bet, other_line, now, at_line=True)
    return remove_vig(american_to_implied_prob(mine.odds), american_to_implied_prob(theirs.odds))[0]


def _decimal(odds: int) -> float:
    return 1 + payout_for("win", odds)


def _round_down(x: float) -> float:
    # round() first so 90.68000000000001 stays 90.68 instead of a float
    # artefact like 90.6799999 flooring to 90.67.
    return math.floor(round(x * 100, 6)) / 100


def offer(session, kind: str, row, now: datetime | None = None) -> Offer:
    """The cash-out offer for a straight bet (PaperPick) or a parlay (Parlay)."""
    now = now or datetime.now(timezone.utc)
    if row.result is not None:
        raise CashOutUnavailable("settled")
    if kind == "straight":
        legs = [row]
    else:
        legs = (session.query(PaperPick).filter(PaperPick.parlay_id == row.id)
                .order_by(PaperPick.id).all())
    p_now = d_placed = 1.0
    for leg in legs:
        p_now *= _leg_chance(session, leg, now)
        d_placed *= _decimal(leg.odds)
    amount = _round_down(row.stake * d_placed * p_now * (1 - CASH_OUT_MARGIN))
    return Offer(amount=amount, p_now=p_now, d_placed=d_placed)


def offer_view(session, kind: str, row, now: datetime | None = None) -> dict | None:
    """A ticket's cash-out field: None once settled."""
    if row.result is not None:
        return None
    try:
        o = offer(session, kind, row, now)
    except CashOutUnavailable as e:
        return {"available": False, "reason": e.reason, "message": e.message}
    return {"available": True, "offer": o.amount}


class OfferChanged(Exception):
    """The offer now is below the one the player confirmed."""

    def __init__(self, new_offer: float):
        self.offer = new_offer
        super().__init__(f"The offer changed to ${new_offer:,.2f}.")


def cash_out(session, loop, user, kind: str, row, expected_offer: float) -> Offer:
    """Settle ``row`` as cashed out at the current offer, and announce it.

    The caller holds the bankroll lock (users.hold_bankroll) before loading
    ``row``, so a second request for the same bet waits, then finds it
    settled. An offer below ``expected_offer`` is refused; one above it is
    paid -- the player never gets less than they saw without being asked.
    """
    from backend.paper.feed import log_feed_event, straight_label   # feed imports api helpers lazily too
    o = offer(session, kind, row)
    if o.amount < expected_offer:
        raise OfferChanged(o.amount)
    row.result = "cashed_out"
    row.payout = round(o.amount - row.stake, 2)
    if kind == "straight":
        row.graded_at = datetime.now(timezone.utc)
        what = straight_label(session, row)
    else:
        legs = session.query(PaperPick).filter(PaperPick.parlay_id == row.id).count()
        what = f"a {legs}-leg parlay"
    session.commit()
    log_feed_event(session, loop, user.id, "cashed_out", {
        "user_name": user.name, "message": f"{user.name} cashed out {what} for ${o.amount:,.2f}",
        "bet_key": f"{kind}-{row.id}", "bet_id": row.id, "kind": kind,
        "result": "cashed_out", "payout": row.payout, "offer": o.amount,
    })
    return o

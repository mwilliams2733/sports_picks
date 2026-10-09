"""The only place a paper bet gets its price.

Players used to type their own odds and line, and ``grade_pick`` settled
whatever was typed -- a spread of "HOME +60" at -110 was accepted, so the
ROI leaderboard could be gamed through the ordinary UI (plan 026's final
review). Since plan 027 every bet route and every quote endpoint calls
:func:`price`, so the price a player is shown is produced by the same
function that prices the bet.

Prices are :func:`backend.analysis.strategy.consensus_moneyline`, the
function ``average_odds`` (the Model's own picks) is built from -- never a
second averager. Spread and total LINES are not averaged, unlike the
Model's: :func:`backend.analysis.strategy.quoted_line` takes the line most
books quote, and the price is the consensus of the books quoting exactly
that line (owner, 2026-10-04, reversing the 2026-09-29 "consensus line
only": an average line like HOME -11.6 is one no book offers and cannot
push).

Unlike ``average_odds``, a paper bet's consensus is taken over only the
rows that are both usable and fresh for the exact market being priced:
``Odds``/``PlayerProp`` rows are upserted per book and never deleted, so a
book that drops out of the feed keeps its last row forever, and averaging
every row in regardless of age lets one stale book quietly move the price
(or, for a line, lets the line come from a row that never quoted a price
for it at all).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from backend.analysis.odds_utils import InvalidOddsError, american_to_implied_prob
from backend.analysis.prop_markets import MARKET_STAT_MAP, market_label
from backend.analysis.strategy import consensus_moneyline, quoted_line
from backend.models import Odds, PlayerProp
from backend.pipeline.team_stats import COMBAT_SPORTS
from backend.time_utils import ET, as_utc, game_start_utc

#: A price older than this is refused. Judged per market, on the newest
#: quote time behind the price.
MAX_QUOTE_AGE = timedelta(hours=6)

_STATUS = {"game_started": 400, "not_quoted": 409, "stale": 409, "not_gradeable": 409}
_MESSAGES = {
    "game_started": "Betting has closed: this game has already started",
    "not_quoted": "No book is quoting this bet right now.",
    "stale": "The price is stale — ask Marcus to refresh.",
    "not_gradeable": "This market can't be graded, so it can't be bet.",
}

#: (pick_type, side) -> (price column, line column or None) on ``Odds``.
GAME_MARKETS: dict[tuple[str, str], tuple[str, str | None]] = {
    ("moneyline", "HOME"): ("moneyline_home", None),
    ("moneyline", "AWAY"): ("moneyline_away", None),
    ("spread", "HOME"): ("spread_home_price", "spread_home"),
    ("spread", "AWAY"): ("spread_away_price", "spread_away"),
    ("over_under", "Over"): ("over_price", "over_under"),
    ("over_under", "Under"): ("under_price", "over_under"),
}


class PricingError(Exception):
    """A bet the server will not price. ``reason`` is a stable code."""

    def __init__(self, reason: str):
        self.reason = reason
        self.message = _MESSAGES[reason]
        self.status = _STATUS[reason]
        super().__init__(self.message)


@dataclass(frozen=True)
class GameBet:
    game_id: int
    pick_type: str   # moneyline | spread | over_under
    side: str        # HOME | AWAY (moneyline, spread); Over | Under (total)


@dataclass(frozen=True)
class PropBet:
    game_id: int
    prop_player: str
    prop_market: str
    outcome: str     # Over | Under
    line: float


@dataclass(frozen=True)
class Quote:
    pick_type: str
    pick_value: str
    odds: int
    line: float | None
    quoted_at: datetime
    prop_player: str | None = None
    prop_market: str | None = None

    def as_dict(self) -> dict:
        return {
            "pick_type": self.pick_type,
            "pick_value": self.pick_value,
            "odds": self.odds,
            "line": self.line,
            "quoted_at": self.quoted_at.isoformat(),
            "prop_player": self.prop_player,
            "prop_market": self.prop_market,
        }


_utc = as_utc


def _usable(price) -> bool:
    if price is None:
        return False
    try:
        american_to_implied_prob(price)
    except InvalidOddsError:
        return False
    return True


def open_for_betting(game, now: datetime | None = None) -> bool:
    """Only games that have not started. A missing start_time is not past
    for a same-day game (the project-wide convention for unknown data), but
    ingestion never writes an in-progress status, so a 'scheduled' game
    dated before today with no start_time is a stale row for a game that
    already happened -- 82 such rows (59 MMA, 23 boxing) exist in the live
    db with public results. A status past 'scheduled' is always closed."""
    now = now or datetime.now(timezone.utc)
    if game.status != "scheduled":
        return False
    start = game_start_utc(game)
    if start is None:
        return game.date >= now.astimezone(ET).date()
    return start > now


def _fresh(rows, timestamp_attr: str, now: datetime):
    """Rows whose timestamp is within MAX_QUOTE_AGE of ``now``."""
    return [r for r in rows if now - _utc(getattr(r, timestamp_attr)) <= MAX_QUOTE_AGE]


def _price_game(session, game, bet: GameBet, now: datetime, at_line: float | None = None) -> Quote:
    # A combat bout's stored score is a 0/1 win/loss pair, not points: a
    # spread or total can never be graded (grader.grade_pick voids one as a
    # push rather than settle it). Refusing the QUOTE here, not just the
    # grade, stops a player from ever being offered -- let alone placing --
    # a bet that can only ever come back as a push. Harmless today only
    # because the collector fetches h2h alone for combat sports; this is the
    # gate for if that ever changes. Moneyline is unaffected.
    if game.sport in COMBAT_SPORTS and bet.pick_type in ("spread", "over_under"):
        raise PricingError("not_gradeable")
    try:
        price_key, line_key = GAME_MARKETS[(bet.pick_type, bet.side)]
    except KeyError:
        raise ValueError(f"not a game bet: {bet.pick_type}/{bet.side}") from None
    rows = session.query(Odds).filter(Odds.game_id == game.id).all()
    # Usable at ANY age, so a book that dropped out of the feed and never
    # gets deleted (upsert-per-book, no delete) doesn't get to set the
    # clock: it must first clear the bar of quoting this market at all.
    usable = [r for r in rows if _usable(getattr(r, price_key))
              and (line_key is None or getattr(r, line_key) is not None)]
    if not usable:
        raise PricingError("not_quoted")
    fresh = _fresh(usable, "timestamp", now)
    if not fresh:
        raise PricingError("stale")
    line = None
    if line_key:
        # A line a book is quoting, priced by the books quoting exactly it --
        # the rule props already followed. The average line was one no book
        # offered and could never push.
        # ``at_line`` (cash out's other side): the books quoting exactly that
        # line, not this side's own quoted line -- on a tie quoted_line gives
        # each side its worse line, so HOME -3.5 and AWAY +3 are both "the"
        # line of one evenly split market.
        line = at_line if at_line is not None else quoted_line(
            [getattr(r, line_key) for r in fresh], higher_is_worse=bet.side == "Over")
        fresh = [r for r in fresh if abs(getattr(r, line_key) - line) < 1e-9]
        if not fresh:
            raise PricingError("not_quoted")
    odds = consensus_moneyline([getattr(r, price_key) for r in fresh])
    quoted_at = max(_utc(r.timestamp) for r in fresh)
    if bet.pick_type == "moneyline":
        label = f"{bet.side} ML"
    elif bet.pick_type == "spread":
        label = f"{bet.side} {line:+g}"
    else:
        label = f"{bet.side} {line:g}"
    return Quote(bet.pick_type, label, odds, line, quoted_at)


def _price_prop(session, game, bet: PropBet, now: datetime) -> Quote:
    # The grader parses "(Over|Under) <line>" and sums MARKET_STAT_MAP's
    # columns; anything else would sit pending forever.
    if bet.prop_market not in MARKET_STAT_MAP or bet.outcome not in ("Over", "Under"):
        raise PricingError("not_gradeable")
    rows = (session.query(PlayerProp)
            .filter(PlayerProp.game_id == game.id,
                    PlayerProp.market == bet.prop_market,
                    PlayerProp.player_name == bet.prop_player,
                    PlayerProp.outcome == bet.outcome,
                    PlayerProp.line.isnot(None))
            .all())
    usable = [r for r in rows if abs(r.line - bet.line) < 1e-9 and _usable(r.odds)]
    if not usable:
        raise PricingError("not_quoted")
    fresh = _fresh(usable, "fetched_at", now)
    if not fresh:
        raise PricingError("stale")
    odds = consensus_moneyline([r.odds for r in fresh])
    quoted_at = max(_utc(r.fetched_at) for r in fresh)
    # The matched book row's own line, not the client's bet.line float: a
    # request of 225.5000000001 (within the 1e-9 tolerance above) must be
    # answered with the book's clean 225.5, not echo the client's noise back
    # as the stored/displayed line.
    line = fresh[0].line
    label = f"{bet.prop_player} {bet.outcome} {line:g} {market_label(bet.prop_market)}"
    return Quote("prop", label, odds, line, quoted_at,
                 prop_player=bet.prop_player, prop_market=bet.prop_market)


def price(session, game, bet: GameBet | PropBet, now: datetime | None = None,
          at_line: float | None = None) -> Quote:
    """Price ``bet`` on ``game`` at the current consensus, or raise PricingError.

    ``at_line`` prices a spread or total at that exact line instead of the
    line most books quote (cash out prices the other side at the mirror of
    the bet's line). Bets are always placed without it."""
    now = now or datetime.now(timezone.utc)
    if not open_for_betting(game, now):
        raise PricingError("game_started")
    if isinstance(bet, PropBet):
        return _price_prop(session, game, bet, now)
    return _price_game(session, game, bet, now, at_line)


def combine(odds: list[int]) -> tuple[int, float]:
    """A parlay's price from its legs' prices: (American, decimal)."""
    if not odds:
        raise ValueError("a parlay needs at least one leg")
    decimal = 1.0
    for o in odds:
        decimal *= (1 + 100 / abs(o)) if o < 0 else (1 + o / 100)
    if decimal >= 2.0:
        american = int(round((decimal - 1) * 100))
    else:
        american = int(round(-100 / (decimal - 1)))
    return american, decimal


def _entry(session, game, bet, now, base: dict) -> dict:
    try:
        quote = price(session, game, bet, now)
    except PricingError as e:
        return {**base, "available": False, "reason": e.reason, "message": e.message}
    return {**base, "available": True, **quote.as_dict()}


def game_quotes(session, game, now: datetime | None = None) -> list[dict]:
    """Every game market's six sides, each a quote or a refusal."""
    now = now or datetime.now(timezone.utc)
    return [_entry(session, game, GameBet(game.id, pick_type, side), now,
                   {"pick_type": pick_type, "side": side})
            for pick_type, side in GAME_MARKETS]


def prop_quotes(session, game, now: datetime | None = None) -> list[dict]:
    """Every gradeable (player, market, Over/Under, line) a book quotes on ``game``."""
    now = now or datetime.now(timezone.utc)
    keys = (session.query(PlayerProp.player_name, PlayerProp.market,
                          PlayerProp.outcome, PlayerProp.line)
            .filter(PlayerProp.game_id == game.id,
                    PlayerProp.market.in_(list(MARKET_STAT_MAP)),
                    PlayerProp.outcome.in_(("Over", "Under")),
                    PlayerProp.line.isnot(None))
            .distinct().all())
    out = []
    for player, market, outcome, line in sorted(keys, key=lambda k: (k[0], k[1], k[3], k[2])):
        base = {"prop_player": player, "prop_market": market,
                "market_label": market_label(market), "outcome": outcome, "line": line}
        out.append(_entry(session, game, PropBet(game.id, player, market, outcome, line),
                          now, base))
    return out

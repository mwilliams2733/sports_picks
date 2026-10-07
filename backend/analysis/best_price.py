"""The best available price for an emailed pick, across the books we store.

`backend.scripts.price_shopping_experiment` (2026-10-07) measured what
taking the best of our 11 books' prices is worth: an NFL moneyline at a
typical book is worth -4.09% at the close, at the best book -0.43%. It is
not an edge by itself, but it is the one finding a reader can act on today,
so the digest names the book.

Only the SAME bet counts: a moneyline side, or a spread/total/prop at the
pick's exact number. A better price at a different number is a different
bet, and is never offered as this one.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from backend.analysis.odds_utils import InvalidOddsError, calculate_payout

#: Rows in `odds` that are not a book anyone can bet at.
NOT_BOOKS = frozenset({"nflverse_close"})

BOOK_NAMES = {
    "draftkings": "DraftKings", "fanduel": "FanDuel", "betmgm": "BetMGM",
    "williamhill_us": "Caesars", "betrivers": "BetRivers", "fanatics": "Fanatics",
    "espnbet": "ESPN BET", "bovada": "Bovada", "betonlineag": "BetOnline",
    "mybookieag": "MyBookie", "betus": "BetUS", "lowvig": "LowVig",
}


def book_name(key: str) -> str:
    return BOOK_NAMES.get(key, key)


@dataclass(frozen=True)
class BestPrice:
    bookmaker: str      # the API key, as stored
    odds: int

    @property
    def name(self) -> str:
        return book_name(self.bookmaker)


def _best(candidates) -> BestPrice | None:
    """Highest payout; ties go to the alphabetically first book, so the
    answer does not depend on row order."""
    best = None
    for book, price in sorted(candidates, key=lambda c: c[0]):
        if price is None or book in NOT_BOOKS:
            continue
        try:
            payout = calculate_payout(price)
        except InvalidOddsError:
            continue
        if best is None or payout > best[0]:
            best = (payout, BestPrice(book, price))
    return best[1] if best else None


def best_game_price(odds_rows, pick_type: str, pick_value: str) -> BestPrice | None:
    """Best price for a stored game pick ("HOME ML", "AWAY +3.5", "Under 44.5")
    among `odds_rows` (the `odds` table: one current row per book)."""
    side, _, rest = pick_value.partition(" ")
    if pick_type == "moneyline" and side in ("HOME", "AWAY"):
        col = "moneyline_home" if side == "HOME" else "moneyline_away"
        return _best((o.bookmaker, getattr(o, col)) for o in odds_rows)
    try:
        point = float(rest)
    except ValueError:
        return None
    if pick_type == "spread" and side in ("HOME", "AWAY"):
        pt, col = (("spread_home", "spread_home_price") if side == "HOME"
                   else ("spread_away", "spread_away_price"))
        return _best((o.bookmaker, getattr(o, col)) for o in odds_rows
                     if getattr(o, pt) == point)
    if pick_type == "over_under" and side in ("Over", "Under"):
        col = "over_price" if side == "Over" else "under_price"
        return _best((o.bookmaker, getattr(o, col)) for o in odds_rows
                     if o.over_under == point)
    return None


_PROP = re.compile(r"\b(Over|Under)\s+(\d+(?:\.\d+)?)\b")


def best_prop_price(prop_rows, player: str, market: str, pick_value: str) -> BestPrice | None:
    """Best price for an Over/Under prop at the pick's exact line, among the
    current `player_props` rows. Yes/No props (anytime TD) are not emailed."""
    m = _PROP.search(pick_value)
    if not m:
        return None
    outcome, line = m.group(1), float(m.group(2))
    return _best((r.bookmaker, r.odds) for r in prop_rows
                 if r.player_name == player and r.market == market
                 and r.outcome == outcome and r.line == line)

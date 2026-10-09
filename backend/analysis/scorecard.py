"""The one definition of every pick-quality metric.

Every page that reports a win rate or ROI computes it here, from a list of
settled or pending bets that says nothing about where they came from: the
emailed digest (1u per pick) and friends' paper bets (dollars) both arrive as
`Bet`s. Two code paths that must agree call one function rather than a test
asserting they match.

Definitions (spec 2026-09-28 §1):
  win rate    wins / (wins + losses); pushes and pending excluded
  90% range   Wilson score interval, z = 1.645
  break-even  mean implied probability (vig included) of the decided bets'
              prices -- the win rate those prices required
  ROI         profit / stake over settled bets; a push is staked and returned
  cash out    a settled bet in ROI (profit = payout); not a win, loss or push,
              and not in break-even (sportsbook spec 2026-10-07 §9)
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Iterable

from backend.analysis.odds_utils import (InvalidOddsError, american_to_implied_prob,
                                         calculate_payout)

logger = logging.getLogger(__name__)

Z90 = 1.645

#: The payout multiplier of a flat -110 bet: the reference unit effective_bets
#: measures other bets against.
B_REF = 100 / 110


@dataclass(frozen=True)
class Bet:
    result: str | None      # "win" | "loss" | "push" | "cashed_out" | None while pending
    stake: float
    profit: float           # net, in the bet's own currency; 0.0 while pending
    odds: int
    day: date
    stars: int | None = None


def wilson(wins: int, decided: int, z: float = Z90) -> tuple[float, float] | None:
    """Wilson score interval for ``wins`` of ``decided``, or None with none decided."""
    if decided == 0:
        return None
    p = wins / decided
    z2 = z * z
    denom = 1 + z2 / decided
    centre = (p + z2 / (2 * decided)) / denom
    half = z * math.sqrt(p * (1 - p) / decided + z2 / (4 * decided * decided)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


def _r(x: float | None) -> float | None:
    return None if x is None else round(x, 4)


@dataclass(frozen=True)
class Summary:
    label: str
    wins: int
    losses: int
    pushes: int
    pending: int
    win_rate: float | None
    range_low: float | None
    range_high: float | None
    break_even: float | None
    profit: float
    staked: float
    roi: float | None
    cashed_out: int = 0

    @property
    def n(self) -> int:
        """Settled bets, cash outs included."""
        return self.wins + self.losses + self.pushes + self.cashed_out

    @property
    def verdict(self) -> str | None:
        """"above"/"below" only when the WHOLE range clears break-even."""
        if self.range_low is None or self.break_even is None:
            return None
        if self.range_low > self.break_even:
            return "above"
        if self.range_high < self.break_even:
            return "below"
        return None

    def to_dict(self) -> dict:
        return {
            "label": self.label, "wins": self.wins, "losses": self.losses,
            "pushes": self.pushes, "cashed_out": self.cashed_out, "pending": self.pending, "n": self.n,
            "win_rate": _r(self.win_rate), "range_low": _r(self.range_low),
            "range_high": _r(self.range_high), "break_even": _r(self.break_even),
            "profit": round(self.profit, 4), "staked": round(self.staked, 4),
            "roi": _r(self.roi), "verdict": self.verdict,
        }


def summarize(bets: Iterable[Bet], label: str = "all") -> Summary:
    bets = list(bets)
    settled = [b for b in bets if b.result is not None]
    wins = sum(1 for b in settled if b.result == "win")
    losses = sum(1 for b in settled if b.result == "loss")
    pushes = sum(1 for b in settled if b.result == "push")
    cashed_out = sum(1 for b in settled if b.result == "cashed_out")
    decided = wins + losses
    interval = wilson(wins, decided)
    prices = []
    bad_odds_count = 0
    for b in settled:
        if b.result in ("win", "loss"):
            try:
                prices.append(american_to_implied_prob(b.odds))
            except InvalidOddsError:
                bad_odds_count += 1
    if bad_odds_count > 0:
        logger.warning(f"{bad_odds_count} decided bets excluded from break-even for invalid odds")
    profit = sum(b.profit for b in settled)
    staked = sum(b.stake for b in settled)
    return Summary(
        label=label, wins=wins, losses=losses, pushes=pushes,
        pending=len(bets) - len(settled),
        win_rate=wins / decided if decided else None,
        range_low=interval[0] if interval else None,
        range_high=interval[1] if interval else None,
        break_even=sum(prices) / len(prices) if prices else None,
        profit=profit, staked=staked,
        roi=profit / staked if staked else None,
        cashed_out=cashed_out,
    )


def effective_bets(bets: Iterable[Bet]) -> float:
    """The number of flat -110 bets carrying the same information as these
    settled bets: an effective sample size weighted by return variance, not
    by raw count.

    Per-unit profit variance at a fair price equals that price's payout
    multiplier, so one $5,000 win at +200 (payout 2.0) is as volatile as
    2.0 / (100/110) flat -110 bets of the same stake -- and a stake ten or
    a hundred times the rest of a record swings that variance further still.
    ``n_eff = B_REF * (sum stake)^2 / sum(stake^2 * payout_i)`` discounts for
    both: it equals the settled bet count exactly for flat-stake -110
    bettors, and shrinks toward zero as a record concentrates into a few
    large, long-odds bets. Bets with unusable odds are treated as -110
    (``B_REF``) rather than excluded, so one bad price can't inflate this.
    Returns 0.0 with no settled bets or zero total stake.
    """
    settled = [b for b in bets if b.result is not None]
    if not settled:
        return 0.0
    total_stake = sum(b.stake for b in settled)
    if total_stake == 0:
        return 0.0
    weighted = 0.0
    for b in settled:
        try:
            payout_i = calculate_payout(b.odds)
        except InvalidOddsError:
            payout_i = B_REF
        weighted += (b.stake ** 2) * payout_i
    if weighted == 0:
        return 0.0
    return B_REF * (total_stake ** 2) / weighted


def _week(d: date) -> str:
    return (d - timedelta(days=d.weekday())).isoformat()


def _month(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def _stars(stars: int | None) -> str:
    return str(stars) if stars else "unrated"


def group(bets: Iterable[Bet], by: str) -> list[Summary]:
    """Summaries per week (Mon-Sun, ET day), calendar month, or star level.

    Weeks and months newest first; stars 5..1 then "unrated".
    """
    if by == "week":
        key = lambda b: _week(b.day)                       # noqa: E731
    elif by == "month":
        key = lambda b: _month(b.day)                      # noqa: E731
    elif by == "stars":
        key = lambda b: _stars(b.stars)                    # noqa: E731
    else:
        raise ValueError(f"unknown grouping {by!r}")
    buckets: dict[str, list[Bet]] = {}
    for b in bets:
        buckets.setdefault(key(b), []).append(b)
    if by == "stars":
        order = sorted(buckets, key=lambda k: (k == "unrated", -int(k) if k != "unrated" else 0))
    else:
        order = sorted(buckets, reverse=True)
    return [summarize(buckets[k], label=k) for k in order]


@dataclass(frozen=True)
class Trend:
    points: list[tuple[date, float]]    # day-end cumulative profit
    max_drawdown: float
    longest_losing_streak: int


def trend(bets: Iterable[Bet]) -> Trend:
    """Cumulative profit by day, max drawdown, longest losing streak.

    Bets are taken in day order, input order within a day. Longest losing
    streak follows the caller's input order (per-bet). Drawdown is measured on
    day-end cumulative profit values, with running peak starting at 0.
    """
    settled = sorted((b for b in bets if b.result is not None), key=lambda b: b.day)
    cum = 0.0
    streak = longest = 0
    by_day: dict[date, float] = {}
    for b in settled:
        cum += b.profit
        if b.result == "loss":
            streak += 1
            longest = max(longest, streak)
        elif b.result == "win":
            streak = 0
        by_day[b.day] = cum

    # Compute max drawdown from day-end values
    peak = 0.0
    drawdown = 0.0
    for day in sorted(by_day.keys()):
        cum = by_day[day]
        peak = max(peak, cum)
        drawdown = max(drawdown, peak - cum)

    return Trend(points=sorted(by_day.items()), max_drawdown=drawdown,
                 longest_losing_streak=longest)

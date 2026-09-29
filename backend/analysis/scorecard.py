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
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Iterable

from backend.analysis.odds_utils import InvalidOddsError, american_to_implied_prob

Z90 = 1.645


@dataclass(frozen=True)
class Bet:
    result: str | None      # "win" | "loss" | "push" | None while pending
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

    @property
    def n(self) -> int:
        """Settled bets."""
        return self.wins + self.losses + self.pushes

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
            "pushes": self.pushes, "pending": self.pending, "n": self.n,
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
    decided = wins + losses
    interval = wilson(wins, decided)
    prices = []
    for b in settled:
        if b.result in ("win", "loss"):
            try:
                prices.append(american_to_implied_prob(b.odds))
            except InvalidOddsError:
                continue
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
    )


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

    Bets are taken in day order, input order within a day. A push breaks
    neither streak; a win ends a losing streak. Drawdown is measured from a
    running peak that starts at 0.
    """
    settled = sorted((b for b in bets if b.result is not None), key=lambda b: b.day)
    cum = peak = drawdown = 0.0
    streak = longest = 0
    by_day: dict[date, float] = {}
    for b in settled:
        cum += b.profit
        peak = max(peak, cum)
        drawdown = max(drawdown, peak - cum)
        if b.result == "loss":
            streak += 1
            longest = max(longest, streak)
        elif b.result == "win":
            streak = 0
        by_day[b.day] = cum
    return Trend(points=sorted(by_day.items()), max_drawdown=drawdown,
                 longest_losing_streak=longest)

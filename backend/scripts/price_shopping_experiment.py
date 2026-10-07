"""Is there an edge in betting off-market prices, with no model at all?

Why this exists
---------------
Every model test so far (EPA, turnovers, QB injuries, travel, rest, the NBA
model) found the closing line already knows what the model knows. The
best-documented retail edge is not prediction but price: when one book's
price is out of line with the rest of the market, take it. We store 11
books' prices for every game (`line_snapshots`, real series since
2026-09-23 00:58), so this can be measured now.

The rule, fixed before the first run (2026-10-07):

* **Moment.** Each time prices were fetched for a game before kickoff. A
  book's quote counts at a moment only if it was actually showing then: its
  row was captured at or before the moment and last seen at or after it
  (append-on-change rows extend `last_seen_at` while the price holds).
* **Fair price.** For each book's quote, the no-vig probability averaged
  over the OTHER books showing at that moment (leave-one-out, at least 4
  others), so a book cannot pull its own benchmark.
* **Off-market bet.** A side whose price has expected value >= 2% against
  that fair probability. 1%, 3% and 5% are printed as sensitivity.
* **One bet per (game, book, side)**, at the first moment it qualifies.
* **Scorecard: the close.** Each bet is re-valued at the no-vig closing
  consensus (every book's last pre-kickoff quote): EV_close = p_close *
  payout - (1 - p_close). That is the market's own final verdict on the
  price, the standard test of a bet's quality. Actual results are printed
  too but are mostly noise at this size.
* **Baseline.** The same EV_close for EVERY quote at every first moment:
  roughly minus the vig. An edge must beat the baseline, not just zero.

Primary: moneylines (prices compare directly). Secondary: spreads and
totals, only where the book's point equals the modal point among the books
at that moment and at the close (a price at a different number is a
different bet).

Intervals are bootstrapped by game: a game's bets share its outcome and its
close. Writes nothing:

    python -m backend.scripts.price_shopping_experiment --db <snapshot>
"""
from __future__ import annotations

import argparse
import random
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta

from backend.analysis.odds_utils import (american_to_implied_prob,
                                         calculate_payout, remove_vig)

SERIES_START = "2026-09-23 00:58"
EV_THRESHOLDS = (0.01, 0.02, 0.03, 0.05)
PRIMARY_EV = 0.02
MIN_OTHERS = 4
TOLERANCE = timedelta(seconds=90)
EXCLUDED_BOOKS = {"nflverse_close"}

#: market -> [(side, price column, point column or None, opposite price column)]
MARKETS = {
    "moneyline": [("home", "moneyline_home", None, "moneyline_away"),
                  ("away", "moneyline_away", None, "moneyline_home")],
    "spread": [("home", "spread_home_price", "spread_home", "spread_away_price"),
               ("away", "spread_away_price", "spread_home", "spread_home_price")],
    "total": [("over", "over_price", "over_under", "under_price"),
              ("under", "under_price", "over_under", "over_price")],
}


def _ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "")).replace(tzinfo=None)


def no_vig(price: int, opposite: int) -> float | None:
    """This side's no-vig probability from a two-way quote."""
    try:
        return remove_vig(american_to_implied_prob(price), american_to_implied_prob(opposite))[0]
    except Exception:
        return None


def ev(prob: float, price: int) -> float:
    """Expected profit per unit staked at `price` if `prob` is the truth."""
    return prob * calculate_payout(price) - (1.0 - prob)


@dataclass(frozen=True)
class Bet:
    game_id: int
    sport: str
    market: str
    side: str
    book: str
    price: int
    ev_entry: float
    ev_close: float
    won: bool | None


def showing(rows, moment: datetime):
    """Each book's quote actually displayed at `moment`."""
    out = {}
    for r in rows:
        if r["captured_at"] <= moment + TOLERANCE and r["last_seen_at"] >= moment - TOLERANCE:
            prev = out.get(r["bookmaker"])
            if prev is None or r["captured_at"] > prev["captured_at"]:
                out[r["bookmaker"]] = r
    return out


def side_prob(quote, side_cols, point: float | None) -> float | None:
    """No-vig prob for a side, if the quote is at `point` (None: moneyline)."""
    _, price_col, point_col, opp_col = side_cols
    if point_col is not None and quote[point_col] != point:
        return None
    if quote[price_col] is None or quote[opp_col] is None:
        return None
    return no_vig(quote[price_col], quote[opp_col])


def modal_point(quotes: dict, point_col: str | None):
    if point_col is None:
        return None
    pts = Counter(q[point_col] for q in quotes.values() if q[point_col] is not None)
    return pts.most_common(1)[0][0] if pts else "none"


def outcome(market: str, side: str, point, home: int, away: int) -> bool | None:
    """True win, False loss, None push."""
    if market == "moneyline":
        diff = home - away if side == "home" else away - home
        return None if diff == 0 else diff > 0
    if market == "spread":
        diff = (home + point - away) if side == "home" else (away - point - home)
        return None if diff == 0 else diff > 0
    total = home + away
    if total == point:
        return None
    return (total > point) == (side == "over")


def bets_for_game(game, rows) -> tuple[list[Bet], list[Bet]]:
    """(qualifying bets at the primary threshold or above, every first quote)
    for one game. Every Bet carries its ev_entry, so thresholds filter later."""
    kickoff = game["start_time"]
    pre = [r for r in rows if r["captured_at"] < kickoff]
    if not pre:
        return [], []
    # The close: each book's last quote captured before kickoff.
    last = {}
    for r in pre:
        if r["bookmaker"] not in last or r["captured_at"] > last[r["bookmaker"]]["captured_at"]:
            last[r["bookmaker"]] = r
    close = last
    moments = sorted({r["captured_at"] for r in pre} | {r["last_seen_at"] for r in pre
                                                        if r["last_seen_at"] < kickoff})
    first: dict[tuple, Bet] = {}
    every: dict[tuple, Bet] = {}
    for market, sides in MARKETS.items():
        point_col = sides[0][2]
        close_point = modal_point(close, point_col)
        for moment in moments:
            quotes = showing(pre, moment)
            if len(quotes) < MIN_OTHERS + 1:
                continue
            point = modal_point(quotes, point_col)
            if point == "none" or (point_col is not None and point != close_point):
                continue
            for side_cols in sides:
                side, price_col = side_cols[0], side_cols[1]
                probs = {b: side_prob(q, side_cols, point) for b, q in quotes.items()}
                close_probs = [p for p in (side_prob(q, side_cols, point) for q in close.values())
                               if p is not None]
                if len(close_probs) < MIN_OTHERS:
                    continue
                p_close = sum(close_probs) / len(close_probs)
                for book, q in quotes.items():
                    key = (market, side, book)
                    if probs[book] is None or key in every:
                        continue
                    others = [p for b, p in probs.items() if b != book and p is not None]
                    if len(others) < MIN_OTHERS:
                        continue
                    fair = sum(others) / len(others)
                    price = q[price_col]
                    bet = Bet(game["id"], game["sport"], market, side, book, price,
                              ev(fair, price), ev(p_close, price),
                              outcome(market, side, point, game["home_score"], game["away_score"]))
                    every[key] = bet
                    if bet.ev_entry >= min(EV_THRESHOLDS) and key not in first:
                        first[key] = bet
    return list(first.values()), list(every.values())


def load(db: str, sports):
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    games = [g for g in conn.execute("""
        select id, sport, start_time, home_score, away_score from games
        where status = 'final' and start_time is not null and home_score is not null""")
             if g["sport"] in sports]
    out = []
    for g in games:
        rows = [dict(r) for r in conn.execute("""
            select * from line_snapshots where game_id = ? and captured_at >= ?""",
            (g["id"], SERIES_START))]
        rows = [r for r in rows if r["bookmaker"] not in EXCLUDED_BOOKS]
        if not rows:
            continue
        for r in rows:
            r["captured_at"], r["last_seen_at"] = _ts(r["captured_at"]), _ts(r["last_seen_at"])
        game = dict(g)
        game["start_time"] = _ts(game["start_time"])
        out.append((game, rows))
    conn.close()
    return out


def summarize(bets: list[Bet], *, seed: int = 7, reps: int = 2000) -> str:
    if not bets:
        return "n 0"
    by_game = defaultdict(list)
    for b in bets:
        by_game[b.game_id].append(b)
    games = list(by_game)
    mean = sum(b.ev_close for b in bets) / len(bets)
    rng = random.Random(seed)
    boots = []
    for _ in range(reps):
        sample = [b for g in (rng.choice(games) for _ in games) for b in by_game[g]]
        boots.append(sum(b.ev_close for b in sample) / len(sample))
    boots.sort()
    lo, hi = boots[int(0.025 * reps)], boots[int(0.975 * reps)]
    decided = [b for b in bets if b.won is not None]
    units = sum(calculate_payout(b.price) if b.won else -1.0 for b in decided)
    wins = sum(b.won for b in decided)
    return (f"n {len(bets):>4} on {len(games):>3} games  EV at close {100 * mean:+.2f}% "
            f"(95% {100 * lo:+.2f}..{100 * hi:+.2f})  actual {wins}-{len(decided) - wins} "
            f"{units:+.1f}u")


def report(data) -> list[str]:
    out = []
    for sport in sorted({g["sport"] for g, _ in data}):
        flagged, everything = [], []
        for g, rows in data:
            if g["sport"] != sport:
                continue
            f, e = bets_for_game(g, rows)
            flagged += f
            everything += e
        out += ["-" * 78, f"{sport.upper()}  ({len({b.game_id for b in everything})} games)", "-" * 78]
        for market in MARKETS:
            tag = "PRIMARY" if market == "moneyline" else "secondary"
            out.append(f"  {market} ({tag})")
            out.append(f"    baseline, every quote:      {summarize([b for b in everything if b.market == market])}")
            for t in EV_THRESHOLDS:
                mark = " <-" if t == PRIMARY_EV else ""
                sel = [b for b in flagged if b.market == market and b.ev_entry >= t]
                out.append(f"    entry EV >= {100 * t:.0f}%{mark:<3}          {summarize(sel)}")
        prim = [b for b in flagged if b.ev_entry >= PRIMARY_EV]
        books = Counter(b.book for b in prim)
        out.append(f"  books supplying the >= {100 * PRIMARY_EV:.0f}% bets: "
                   + ", ".join(f"{k} {v}" for k, v in books.most_common()))
    out += ["", "  EV at close values each bet at the no-vig closing consensus: the market's",
            "  final verdict on the price. Baseline should be about minus the vig."]
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", required=True, help="A .backup snapshot (Windows path).")
    ap.add_argument("--sports", default="nfl,mlb,nba")
    args = ap.parse_args(argv)
    data = load(args.db, tuple(args.sports.split(",")))
    print("=" * 78)
    print("PRICE SHOPPING  (off-market prices vs leave-one-out no-vig consensus)")
    print("=" * 78)
    print("\n".join(report(data)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Does a bigger claimed edge actually win more?

A model that is calibrated should win more, and more profitably, where it
claims more edge. If ROI falls as claimed edge rises, the edge estimate is
not merely noisy -- it is inverted, and raising `min_edge` selects harder
for whatever is wrong.

This exists as a module rather than an ad-hoc query because the first
version of this analysis was run by hand, reported figures like "142 graded
moneyline picks" and "0-for-36", and was later found to have been computed
over a table where more than half the rows were duplicate picks. Numbers
that cannot be re-derived cannot be corrected.

Read it with two confounders in view, both of which the report prints:

* **Price tracks edge.** The model claims its biggest edges on the biggest
  underdogs, so an edge bucket is also a price bucket. `--min-price` and
  `--max-price` narrow the band so the comparison is nearer like-for-like.
* **Picks cluster.** Effective sample size is printed beside every raw n,
  and the distinct dates behind a bucket are printed too. Eighteen picks
  from three tournament days are not eighteen independent trials.
"""
import argparse
import statistics
from collections import Counter
from dataclasses import dataclass, field

from backend.analysis.calibration_report import effective_sample_size
from backend.database import get_engine, get_session
from backend.models import Game, PickModel, PickResult

#: Edge boundaries in percent. Open-ended at the top.
DEFAULT_BUCKETS = ((0.0, 20.0), (20.0, 35.0), (35.0, 1e9))


@dataclass
class Bucket:
    low: float
    high: float
    n: int = 0
    wins: int = 0
    units: float = 0.0
    avg_price: float | None = None
    n_teams: int = 0
    dates: list[str] = field(default_factory=list)
    away: int = 0
    neutral: int = 0

    @property
    def win_rate(self) -> float | None:
        return self.wins / self.n if self.n else None

    @property
    def roi(self) -> float | None:
        return self.units / self.n if self.n else None

    def effective_n(self, icc: float = 0.05) -> float:
        return effective_sample_size(self.n, self.n_teams, icc)


def graded_picks(session, pick_type: str = "moneyline", sport: str | None = None):
    """Graded picks of one market, with what the analysis needs from each."""
    q = (session.query(PickModel, PickResult, Game)
         .join(PickResult, PickResult.pick_id == PickModel.id)
         .join(Game, Game.id == PickModel.game_id)
         .filter(PickModel.pick_type == pick_type,
                 PickModel.edge_pct.isnot(None)))
    if sport:
        q = q.filter(Game.sport == sport)
    return q.all()


def bucket_rows(rows, buckets=DEFAULT_BUCKETS, min_price=None, max_price=None):
    """Group graded picks by claimed edge."""
    out = []
    for low, high in buckets:
        b = Bucket(low=low, high=high)
        prices, teams, dates = [], Counter(), []
        for pick, result, game in rows:
            if not (low <= pick.edge_pct < high):
                continue
            price = pick.odds_at_pick
            if min_price is not None and (price is None or price < min_price):
                continue
            if max_price is not None and (price is None or price > max_price):
                continue
            b.n += 1
            b.wins += 1 if result.result == "win" else 0
            b.units += result.payout or 0.0
            if price is not None:
                prices.append(price)
            teams[game.home_team_id] += 1
            teams[game.away_team_id] += 1
            dates.append(str(game.date))
            if "AWAY" in str(pick.pick_value or "").upper():
                b.away += 1
            if game.neutral_site:
                b.neutral += 1
        b.avg_price = statistics.mean(prices) if prices else None
        b.n_teams = len(teams)
        b.dates = sorted(set(dates))
        out.append(b)
    return out


def format_report(buckets: list[Bucket], title: str, icc: float = 0.05) -> str:
    lines = [title, ""]
    lines.append(f"  {'edge':<12} {'n':>4} {'eff n':>6} {'win%':>7} "
                 f"{'avg price':>10} {'units':>8} {'roi':>8} {'away':>6} {'neutral':>8}")
    lines.append("  " + "-" * 78)
    for b in buckets:
        label = f"{b.low:.0f}-{b.high:.0f}%" if b.high < 1e8 else f"{b.low:.0f}%+"
        if not b.n:
            lines.append(f"  {label:<12} {0:>4}")
            continue
        price = f"{b.avg_price:+.0f}" if b.avg_price is not None else "-"
        lines.append(
            f"  {label:<12} {b.n:>4} {b.effective_n(icc):>6.1f} "
            f"{100 * b.win_rate:>6.1f}% {price:>10} {b.units:>+8.2f} "
            f"{b.roi:>+8.3f} {b.away:>6} {b.neutral:>8}")
    lines.append("")
    lines.append(f"  effective n assumes intra-team ICC = {icc}; it is an")
    lines.append("  assumption, not a measurement.")
    thin = [b for b in buckets if 0 < b.n and len(b.dates) <= 3]
    for b in thin:
        label = f"{b.low:.0f}-{b.high:.0f}%" if b.high < 1e8 else f"{b.low:.0f}%+"
        lines.append(f"  NOTE: the {label} bucket's {b.n} picks span only "
                     f"{len(b.dates)} date(s) ({', '.join(b.dates)}).")
        lines.append("  Same-day results share conditions; treat them as far fewer")
        lines.append("  than independent trials.")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", required=True)
    ap.add_argument("--sport")
    ap.add_argument("--pick-type", default="moneyline")
    ap.add_argument("--min-price", type=int)
    ap.add_argument("--max-price", type=int)
    ap.add_argument("--icc", type=float, default=0.05)
    args = ap.parse_args(argv)

    session = get_session(get_engine(args.db))
    try:
        rows = graded_picks(session, args.pick_type, args.sport)
        buckets = bucket_rows(rows, min_price=args.min_price,
                              max_price=args.max_price)
    finally:
        session.close()

    band = ""
    if args.min_price is not None or args.max_price is not None:
        band = f", price {args.min_price or '-inf'}..{args.max_price or '+inf'}"
    title = (f"Edge buckets -- {args.pick_type}"
             f"{', ' + args.sport if args.sport else ', all sports'}{band}")
    print(format_report(buckets, title, icc=args.icc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

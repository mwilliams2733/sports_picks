"""Closing line value: did the price we took beat the price that closed?

Why CLV and not results
-----------------------
Every other edge measurement in this project waits for games to finish and
then fights variance for months. This repo's nfl backtest needed 528 picks
to reach p = 0.0104, and the market-shrinkage work needed 1,171 games to
establish that the closing line is calibrated. CLV resolves the moment a
game starts and has a far smaller residual, because it compares two prices
rather than a price to a coin flip.

It is also the only measurement that can tell "our model is wrong" apart
from "our model is right and ran bad". Consistently beating the close and
losing money is variance. Consistently losing to the close is the model.

What it needs to be true
------------------------
**The two prices must be consensused identically.** ``odds_at_pick`` comes
from `average_odds`, so `closing_consensus` runs the closing snapshots
through the same function. Until 2026-09-23 the stored close was whichever
book `Odds` happened to return first -- one row per bookmaker, ordered by
timestamp -- so the recorded CLV contained the gap between one arbitrary
book and the field on top of any real movement. Before ``7463de7`` that row
was frequently an in-play price as well.

**Absent is never zero.** A pick with no closing price on record is dropped,
not counted as zero CLV. Counting it drags every average toward "no edge",
which is the one conclusion a broken pipeline produces by default.

**Reconstructed prices are excluded.** ``odds_reconstructed`` marks picks
whose ``odds_at_pick`` was rebuilt afterwards from surviving book rows. The
CLV of a reconstructed price is the reconstruction error, not market
movement. ``--include-reconstructed`` overrides this deliberately.

**Price CLV and line CLV are never pooled.** Moneyline CLV is in implied
probability points; spread and total CLV is in line points. Adding them is
nonsense, so they are summarized separately -- the same split
`compute_pick_clv` already makes.

The statistics
--------------
The null is **zero, two-sided**. A bettor with no edge lands either side of
the close at random; testing only "is the mean positive" finds an edge in
half of all noise.

Significance is computed on **per-game means, with degrees of freedom from
the number of GAMES, not picks**. Every pick on one game shares one closing
line, so ten picks on a single slate are not ten independent observations of
the market. This is the conservative choice and it is deliberate: the
alternative manufactures significance out of correlated picks, which is the
mistake this repo has already made once with an effective-sample-size
caveat that was printed but not applied.

    python -m backend.analysis.clv_report --db <abs path>
    python -m backend.analysis.clv_report --db <abs path> --sport nfl
"""
from __future__ import annotations

import argparse
import logging
import math
import os
import statistics
from collections import defaultdict
from dataclasses import dataclass, replace

from backend.analysis.line_snapshots import series_depth

logger = logging.getLogger(__name__)

#: Markets whose CLV is an implied-probability delta, in percentage points.
PRICE_MARKETS = ("moneyline",)

#: Markets whose CLV is measured in line points.
LINE_MARKETS = ("spread", "over_under")


@dataclass(frozen=True)
class ClvSample:
    """One pick's closing line value.

    ``clv`` is None when no closing price was on record. That is an absent
    measurement, and `usable` drops it rather than letting a zero in.
    """
    pick_id: int
    game_id: int
    sport: str
    market: str
    clv: float | None
    reconstructed: bool
    #: Most observations any one book has on this game. 1 means the line was
    #: never seen to move, so the "close" is the same observation the pick
    #: was priced from -- see `measurable`.
    depth: int = 1


@dataclass(frozen=True)
class Summary:
    """Aggregate CLV for one group of comparable picks."""
    n: int
    games: int
    mean: float | None
    median: float | None
    beat: int
    p_value: float

    @property
    def beat_rate(self) -> float | None:
        return self.beat / self.n if self.n else None


def usable(samples, *, include_reconstructed: bool = False) -> list[ClvSample]:
    """Drop the samples that cannot honestly contribute to an average."""
    return [s for s in samples
            if s.clv is not None
            and (include_reconstructed or not s.reconstructed)]


def mean_clv(samples) -> float | None:
    """Mean CLV, or None for an empty sample -- absent is not neutral."""
    samples = list(samples)
    return statistics.fmean(s.clv for s in samples) if samples else None


def _p_value(samples) -> float:
    """Two-sided t-test of mean CLV against zero, clustered by game.

    Picks on the same game share a closing line, so they are averaged into
    one observation first and the degrees of freedom come from the number of
    games. Returns 1.0 -- no evidence -- when there are fewer than two games
    or when the per-game means have no variance at all, because neither case
    supports estimating a standard error. Reporting certainty from a
    zero-variance sample of three is how a report claims an edge it has not
    measured.
    """
    by_game: dict[int, list[float]] = defaultdict(list)
    for s in samples:
        by_game[s.game_id].append(s.clv)
    means = [statistics.fmean(v) for v in by_game.values()]
    if len(means) < 2:
        return 1.0

    sd = statistics.stdev(means)
    if sd == 0:
        return 1.0

    from scipy import stats
    t = statistics.fmean(means) / (sd / math.sqrt(len(means)))
    return float(2.0 * stats.t.sf(abs(t), len(means) - 1))


def summarize(samples) -> Summary:
    """Aggregate one group of comparable picks. Caller groups by market."""
    samples = list(samples)
    if not samples:
        return Summary(n=0, games=0, mean=None, median=None, beat=0,
                       p_value=1.0)
    values = [s.clv for s in samples]
    return Summary(
        n=len(samples),
        games=len({s.game_id for s in samples}),
        mean=statistics.fmean(values),
        median=statistics.median(values),
        # Strictly positive: landing exactly on the close is not beating it.
        beat=sum(1 for v in values if v > 0),
        p_value=_p_value(samples),
    )


# --- loading --------------------------------------------------------------

def load_samples(session, *, sport: str | None = None) -> list[ClvSample]:
    """Build a CLV sample for every graded pick with a stored close."""
    from backend.analysis.odds_utils import compute_pick_clv
    from backend.models import Game, PickModel, PickResult

    q = (session.query(PickResult, PickModel, Game)
         .join(PickModel, PickResult.pick_id == PickModel.id)
         .join(Game, PickModel.game_id == Game.id))
    if sport:
        q = q.filter(Game.sport == sport)

    out: list[ClvSample] = []
    for pr, pm, game in q.all():
        clv_pct, clv_points = compute_pick_clv(
            pm.pick_type, pm.pick_value, pm.odds_at_pick,
            pr.odds_at_close, pr.line_at_close)
        # Exactly one of the two is meaningful per market, and they are in
        # different units -- see PRICE_MARKETS / LINE_MARKETS.
        clv = clv_pct if pm.pick_type in PRICE_MARKETS else clv_points
        out.append(ClvSample(pick_id=pm.id, game_id=pm.game_id,
                             sport=game.sport, market=pm.pick_type, clv=clv,
                             reconstructed=bool(pm.odds_reconstructed)))

    depths = series_depth(session, {s.game_id for s in out})
    return [replace(s, depth=depths.get(s.game_id, 0)) for s in out]


def measurable(samples) -> list[ClvSample]:
    """Samples whose game has a line that was actually seen to move.

    A depth-1 series has one observation per book, so its closing price IS
    the price the pick was made from and the CLV is structurally near zero.
    Those samples are not evidence of matching the close; they are evidence
    of not having watched long enough.
    """
    return [s for s in samples if s.depth > 1]


def group_report(samples, markets) -> dict[str, Summary]:
    """Summaries keyed by sport for one family of markets, plus ``ALL``."""
    scoped = [s for s in samples if s.market in markets]
    out = {sport: summarize([s for s in scoped if s.sport == sport])
           for sport in sorted({s.sport for s in scoped})}
    if scoped:
        out["ALL"] = summarize(scoped)
    return out


def format_report(samples, *, include_reconstructed: bool) -> str:
    raw = list(samples)
    kept = usable(raw, include_reconstructed=include_reconstructed)
    no_close = sum(1 for s in raw if s.clv is None)
    recon = sum(1 for s in raw if s.clv is not None and s.reconstructed)

    out = ["=" * 74, "CLOSING LINE VALUE", "=" * 74, "",
           f"  graded picks examined     : {len(raw)}",
           f"  no closing price on record: {no_close}"
           "   (dropped -- absent is not zero)",
           f"  reconstructed odds_at_pick: {recon}"
           f"   ({'included' if include_reconstructed else 'dropped'})",
           f"  contributing to the report: {len(kept)}", ""]

    deep = measurable(kept)
    flat = len(kept) - len(deep)
    zero = sum(1 for s in kept if s.clv == 0.0)
    if flat:
        out += [
            "  " + "!" * 68,
            f"  {flat} of these {len(kept)} picks are on games whose line was never",
            "  OBSERVED to move -- one snapshot per book, so the closing price is",
            "  the same observation the pick was priced from. Their CLV is not a",
            "  measurement of beating the close; it is the gap to an anchor of",
            f"  unknown timing. {zero} picks have CLV of exactly zero.",
            "",
            "  Every game seeded by backfill_line_snapshots has this shape: the",
            "  upsert had already destroyed the earlier prices. Real series only",
            "  start accumulating from 2026-09-23.",
            f"  Genuinely measurable right now: {len(deep)} picks.",
            "  " + "!" * 68, ""]

    for title, markets, unit in (
            ("PRICE CLV -- moneyline, in implied-probability points",
             PRICE_MARKETS, "pts"),
            ("LINE CLV -- spread and totals, in line points",
             LINE_MARKETS, "pts")):
        out += ["-" * 74, title, "-" * 74]
        groups = group_report(kept, markets)
        if not groups:
            out += ["    no picks on these markets", ""]
            continue
        out.append(f"    {'sport':<8}{'picks':>7}{'games':>7}{'mean':>9}"
                   f"{'median':>9}{'beat%':>8}{'p':>9}")
        for sport, s in groups.items():
            beat = "     n/a" if s.beat_rate is None else f"{s.beat_rate:7.1%}"
            out.append(f"    {sport:<8}{s.n:>7}{s.games:>7}"
                       f"{s.mean:>+9.3f}{s.median:>+9.3f}{beat}"
                       f"{s.p_value:>9.4f}")
        out.append("")

    out += [
        "-" * 74,
        "  Positive mean = the price we took beat the price that closed.",
        "  The null is ZERO and the test is TWO-SIDED: a bettor with no edge",
        "  lands either side of the close at random, so a one-sided test",
        "  finds an edge in half of all noise.",
        "",
        "  p is computed on PER-GAME means with degrees of freedom from the",
        "  GAME count, not the pick count. Every pick on one game shares one",
        "  closing line, so a slate of correlated picks is not that many",
        "  independent observations of the market.",
        "",
        "  Price CLV and line CLV are in different units and are never",
        "  pooled.",
        "-" * 74, ""]
    return "\n".join(out)


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", required=True, help="Path to the database. Read only.")
    ap.add_argument("--sport", help="Limit to one sport.")
    ap.add_argument("--include-reconstructed", action="store_true",
                    help="Include picks whose odds_at_pick was rebuilt after "
                         "the fact. Their CLV is the reconstruction error.")
    args = ap.parse_args(argv)

    if not os.path.exists(args.db):
        raise SystemExit(f"{args.db!r} does not exist.")

    from backend.database import get_engine, get_session
    session = get_session(get_engine(args.db))
    try:
        print(format_report(load_samples(session, sport=args.sport),
                            include_reconstructed=args.include_reconstructed))
    finally:
        session.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

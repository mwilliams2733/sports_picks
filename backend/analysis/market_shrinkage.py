"""Measure how much of the model's disagreement with the price is worth keeping.

The ensemble prices a game from team features and never reads the market, so
its edge is ``model - implied`` and every pick is a bet that the model knows
something the price does not. This module scores that bet:

    p_final = lam * p_model + (1 - lam) * p_market_devigged

``lam = 1`` is today's behaviour. ``lam = 0`` says the model adds nothing.

Why this and not a calibration curve
------------------------------------
Post-hoc calibration was tried first and does not work here. A Platt fit on
877 nba games moved out-of-sample Brier by 0.0008 (a = 1.038), because the
reliability error is **not monotone** -- the bin gaps run +0.124, -0.004,
-0.189, -0.079, -0.131, +0.027, changing sign twice. Platt and isotonic are
both monotone transforms of the probability axis, so neither can repair an
error that reverses direction. Nothing is wrong with the mapping; the
probabilities carry no information to re-map.

Measured 2026-09-21
-------------------
209 final games with both a model probability and a price, 2026-03-13 to
2026-09-20, across nba (41), ncaab (60), ncaaf (56), mlb (38), nfl (14):

    lam   0.0  Brier 0.1725   (market alone)
    lam   0.5  Brier 0.1820
    lam   1.0  Brier 0.2131   (model alone -- today)

Monotone in lam. Fitted on a time split -- trained on nba/ncaab/mlb through
May, scored on ncaaf/mlb/nfl from June -- lam came out **0.00**, eval Brier
0.1516 against 0.2125 for the model alone.

This module deliberately does NOT apply lam. At lam = 0 the model never
disagrees with the price, so no pick clears any edge threshold and the
system stops producing picks. That is a decision about what the product
does, not a calibration detail.

    python -m backend.analysis.market_shrinkage --db <abs windows path>
"""
from __future__ import annotations

import argparse
import collections
import logging
import os
from dataclasses import dataclass
from datetime import date

logger = logging.getLogger(__name__)

#: Games required before a lambda is reported at all. Below this the fitted
#: value is dominated by which games happen to be present, and a bare number
#: from 12 games is indistinguishable from one fitted on 1,200.
MIN_SHRINKAGE_GAMES = 30

#: Lambda is searched on a 0.01 grid. A coarser grid rounds the live answer
#: (~0.03) to zero and loses the difference between "small" and "none".
_GRID = [i / 100 for i in range(101)]


@dataclass(frozen=True)
class Blend:
    """One game's two forecasts and what actually happened."""
    sport: str
    date: date
    model: float
    market: float
    outcome: int  # 1 home won, 0 home lost; draws are excluded upstream


def blended_brier(rows: list[Blend], lam: float) -> float:
    """Brier score of the blended forecast at ``lam``."""
    if not rows:
        raise ValueError("no rows to score")
    return sum((lam * r.model + (1 - lam) * r.market - r.outcome) ** 2
               for r in rows) / len(rows)


def best_lambda(rows: list[Blend]) -> float:
    """The blend weight minimising Brier, to two decimal places.

    Raises rather than returning a number when the sample cannot support
    one, or when a row carries a non-binary outcome -- a drawn game has no
    outcome to score and must not be counted as a home loss.
    """
    if len(rows) < MIN_SHRINKAGE_GAMES:
        raise ValueError(
            f"need at least {MIN_SHRINKAGE_GAMES} games to fit a shrinkage "
            f"weight, got {len(rows)}")
    bad = [r for r in rows if r.outcome not in (0, 1)]
    if bad:
        raise ValueError(
            f"{len(bad)} row(s) carry a non-binary outcome; a draw has no "
            f"outcome to score and must be excluded, not counted as a loss")
    return min(_GRID, key=lambda lam: blended_brier(rows, lam))


def collect(session, *, sports: tuple[str, ...] | None = None) -> list[Blend]:
    """Every final game carrying both a model probability and a price.

    Combat sports are excluded: `CombatSportsStrategy` is a different model
    and its fighters are 96/98 unrated, so it would measure nothing.
    """
    from backend.analysis import calibration_report as cr
    from backend.analysis.odds_utils import american_to_implied_prob, remove_vig
    from backend.analysis.variants.ensemble import EnsembleStrategy
    from backend.models import Game
    from backend.pipeline.pick_generator import _build_game_data

    query = (session.query(Game)
             .filter(Game.status == "final", Game.home_score.isnot(None))
             .order_by(Game.date))
    if sports:
        query = query.filter(Game.sport.in_(list(sports)))

    rows: list[Blend] = []
    for game in query.all():
        if game.sport in ("mma", "boxing") or game.home_score == game.away_score:
            continue
        game_data = _build_game_data(session, game)
        if not game_data.odds:
            continue
        strategy = EnsembleStrategy("ensemble", cr.LIVE_ENSEMBLE_CONFIG)
        avg = strategy._average_odds(game_data)
        if not avg or avg.get("moneyline_home") is None:
            continue
        fair_home, _ = remove_vig(
            american_to_implied_prob(avg["moneyline_home"]),
            american_to_implied_prob(avg["moneyline_away"]))
        rows.append(Blend(
            sport=game.sport, date=game.date,
            model=strategy._calibrated_probability(game_data),
            market=fair_home,
            outcome=1 if game.home_score > game.away_score else 0))
    return rows


def format_report(rows: list[Blend], split: date | None = None) -> str:
    """The measurement, with its sample size and span attached."""
    if not rows:
        return "No game carries both a model probability and a price."

    by_sport = collections.Counter(r.sport for r in rows)
    lines = [
        "Model vs market: how much disagreement is worth keeping", "",
        f"  games with both     : {len(rows)}",
        f"  by sport            : {dict(by_sport)}",
        f"  span                : {min(r.date for r in rows)} .. "
        f"{max(r.date for r in rows)}",
        "",
        f"  {'lambda':>7} {'Brier':>9}   meaning",
    ]
    for lam in (0.0, 0.25, 0.5, 0.75, 1.0):
        note = ("market alone" if lam == 0.0
                else "model alone (today)" if lam == 1.0 else "")
        lines.append(f"  {lam:>7.2f} {blended_brier(rows, lam):>9.4f}   {note}")

    if len(rows) < MIN_SHRINKAGE_GAMES:
        lines += ["", f"  Not fitting a lambda: {len(rows)} games is under "
                      f"{MIN_SHRINKAGE_GAMES}."]
        return "\n".join(lines)

    lam = best_lambda(rows)
    lines += ["", f"  best lambda         : {lam:.2f}  "
                  f"(Brier {blended_brier(rows, lam):.4f})"]

    if split is not None:
        train = [r for r in rows if r.date < split]
        held = [r for r in rows if r.date >= split]
        lines += ["", f"  held out at {split}:"]
        if len(train) < MIN_SHRINKAGE_GAMES or not held:
            lines.append(f"    not enough on one side ({len(train)} train, "
                         f"{len(held)} eval) -- in-sample only above")
        else:
            fitted = best_lambda(train)
            lines += [
                f"    lambda fitted on train : {fitted:.2f} "
                f"({len(train)} games)",
                f"    EVAL Brier at that lam : {blended_brier(held, fitted):.4f} "
                f"({len(held)} games)",
                f"    EVAL Brier at lam=1    : {blended_brier(held, 1.0):.4f}",
                f"    EVAL Brier at lam=0    : {blended_brier(held, 0.0):.4f}",
            ]

    lines += [
        "",
        "  Lambda is measured here, never applied. At lambda 0 the model",
        "  never disagrees with the price, so no pick clears an edge",
        "  threshold and the system stops producing picks -- a decision",
        "  about the product, not a calibration detail.",
        "",
        "  Sample sizes are small and mostly one window per sport. Games",
        "  sharing a team are not independent; treat this as directional.",
    ]
    return "\n".join(lines)


def main(argv=None) -> int:
    logging.basicConfig(level=logging.WARNING)
    ap = argparse.ArgumentParser(
        description="Measure the model-vs-market blend weight. Read-only.")
    ap.add_argument("--db", required=True, help="Path to the database.")
    ap.add_argument("--sport", action="append", help="Limit to a sport. Repeatable.")
    ap.add_argument("--split", help="Hold out games on/after this ISO date.")
    args = ap.parse_args(argv)

    if args.db != ":memory:" and not os.path.exists(args.db):
        raise SystemExit(f"--db {args.db!r} does not exist.")

    from backend.database import get_engine, get_session
    session = get_session(get_engine(args.db))
    try:
        rows = collect(session, sports=tuple(args.sport) if args.sport else None)
    finally:
        session.close()
    split = date.fromisoformat(args.split) if args.split else None
    print(format_report(rows, split))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

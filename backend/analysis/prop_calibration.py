"""Reliability of the prop confidence score, measured against outcomes.

Read-only. This module measures; it never tunes and it writes no rows.

Why it exists
-------------
The daily digest is majority player props by row count, and it ranks them by
``confidence``. Nearly half of every prop ever generated sits at confidence 5.
Until plan 010 no prop had ever been graded, so that score had never been
checked against a single outcome.

The question
------------
**Do 5-star props win more often than 4-star props?** If they do not, the
confidence score carries no information about outcomes and the digest must not
rank by it. Everything else here is in service of answering that honestly.

What it does not do
-------------------
It does not compare against a de-vigged market price, the way
``calibration_report`` does for game picks. Prop ``edge_pct`` is
``(prob - 0.5) * 200``, which ignores the prop's own market entirely, so there
is no trustworthy reference probability to score against. Win rate per tier is
the weaker but honest measurement available.

Clustering
----------
Props from the same game are **not** independent observations: they share its
pace, blowout risk and rotation. The clusters here are games, one per pick.
That is a different model from ``calibration_report.effective_sample_size``,
which clusters on teams and assumes two clusters per observation. The two are
deliberately not shared -- the arithmetic differs because the cluster
structure does, and forcing them together would make one of them wrong.
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field

from backend.models import Game, PickModel, PickResult

#: Below this much *effective* sample a tier's win rate is not worth acting on.
DEFAULT_MIN_BIN = 30

#: The ICC the reliability flag is judged at. Props from one game share its
#: pace, blowout risk and rotation; 0.05 is the optimistic end of the range
#: this report prints, so a tier flagged reliable clears the bar even on the
#: kindest assumption.
RELIABILITY_ICC = 0.05


@dataclass
class TierStats:
    confidence: int
    wins: int = 0
    losses: int = 0
    pushes: int = 0
    units: float = 0.0
    game_ids: set = field(default_factory=set)
    reliable: bool = False

    @property
    def settled(self) -> int:
        """Picks with a win/loss outcome. Pushes are excluded: a push is not a
        loss, and counting it as one understates every tier."""
        return self.wins + self.losses

    @property
    def graded(self) -> int:
        return self.wins + self.losses + self.pushes

    @property
    def win_rate(self) -> float | None:
        return self.wins / self.settled if self.settled else None

    @property
    def roi(self) -> float | None:
        """Units per settled pick, from the stored payout."""
        return self.units / self.settled if self.settled else None


@dataclass
class PropReport:
    sport: str
    tiers: dict[int, TierStats]
    min_bin: int

    @property
    def total_graded(self) -> int:
        return sum(t.graded for t in self.tiers.values())


def effective_prop_sample_size(n: int, n_games: int, icc: float) -> float:
    """Cluster-adjusted sample size, clustering on the game.

    With average cluster size ``m = n / n_games``, the design effect is
    ``1 + (m - 1) * icc``. One pick per game means no deflation; six props
    from one game count for far less than six. ``icc`` is an assumption, not a
    measurement -- quote it alongside the number.
    """
    if n <= 0 or n_games <= 0:
        return 0.0
    m = n / n_games
    return n / (1.0 + (m - 1.0) * icc)


def evaluate_prop_calibration(session, sport: str | None = None,
                              min_bin: int = DEFAULT_MIN_BIN) -> PropReport:
    """Win rate and ROI per confidence tier for graded prop picks."""
    query = (
        session.query(PickModel, PickResult)
        .join(PickResult, PickResult.pick_id == PickModel.id)
        .join(Game, Game.id == PickModel.game_id)
        .filter(PickModel.pick_type == "prop")
    )
    if sport is not None:
        query = query.filter(Game.sport == sport)

    tiers: dict[int, TierStats] = {c: TierStats(confidence=c) for c in range(1, 6)}
    for pick, result in query.all():
        tier = tiers.setdefault(pick.confidence, TierStats(confidence=pick.confidence))
        if result.result == "win":
            tier.wins += 1
        elif result.result == "loss":
            tier.losses += 1
        else:
            tier.pushes += 1
            continue          # pushes stake nothing and settle nothing
        tier.units += result.payout or 0.0
        tier.game_ids.add(pick.game_id)

    for tier in tiers.values():
        # Judged on effective n, not the raw count. This report tells its
        # reader to use the effective n; flagging a tier on the raw count
        # would contradict that in the one place it matters. 36 props from 2
        # games clear a raw bar of 30 while carrying about half the
        # information -- which is the actual shape of the production data.
        tier.reliable = effective_prop_sample_size(
            tier.settled, len(tier.game_ids), RELIABILITY_ICC) >= min_bin

    return PropReport(sport=sport or "all", tiers=tiers, min_bin=min_bin)


def format_prop_report(report: PropReport) -> str:
    lines = [f"Prop calibration -- sport={report.sport}", ""]
    if report.total_graded == 0:
        lines.append("  REFUSING: no graded prop picks.")
        lines.append("  Nothing here has been measured. Run the grader first;")
        lines.append("  an empty report is not a result about the model.")
        return "\n".join(lines)

    lines.append("  tier   settled   wins  losses  pushes   win%     units    roi  reliable")
    lines.append("  " + "-" * 74)
    for conf in sorted(report.tiers, reverse=True):
        tier = report.tiers[conf]
        if tier.graded == 0:
            lines.append(f"  {conf}          0      -       -       -      -         -      -    -")
            continue
        wr = f"{tier.win_rate * 100:5.1f}%" if tier.win_rate is not None else "    -"
        roi = f"{tier.roi:+6.3f}" if tier.roi is not None else "     -"
        lines.append(
            f"  {conf}     {tier.settled:6d} {tier.wins:6d}  {tier.losses:6d}  "
            f"{tier.pushes:6d}  {wr}  {tier.units:+8.2f} {roi}  "
            f"{'yes' if tier.reliable else 'NO'}"
        )

    lines.append("")
    lines.append(_headline(report))
    lines.append("")
    lines.append("  Sample size")
    for conf in sorted(report.tiers, reverse=True):
        tier = report.tiers[conf]
        if tier.settled == 0:
            continue
        for icc in (0.05, 0.15):
            ess = effective_prop_sample_size(tier.settled, len(tier.game_ids), icc)
            lines.append(f"    tier {conf}: n={tier.settled:<4d} across "
                         f"{len(tier.game_ids):<3d} games -> effective "
                         f"{ess:6.1f}  (ICC {icc})")
    lines.append("    Props from the same game share its pace, blowout risk and")
    lines.append("    rotation, so they are not independent observations. Treat the")
    lines.append("    effective n, not the raw count, as the basis for confidence.")
    lines.append("    The ICC is an assumption, not a measurement; both ends shown.")
    return "\n".join(lines)


def _headline(report: PropReport) -> str:
    """The one sentence the tool exists to produce."""
    five, four = report.tiers.get(5), report.tiers.get(4)
    if not five or not four or five.win_rate is None or four.win_rate is None:
        return ("  5-star vs 4-star: NOT MEASURABLE -- one of the tiers has no "
                "settled picks.")
    delta = (five.win_rate - four.win_rate) * 100
    verdict = "higher" if delta > 0 else "LOWER" if delta < 0 else "identical"
    caveat = "" if (five.reliable and four.reliable) else \
        f"  Both tiers are under min_bin={report.min_bin}; directional only."
    return (f"  5-star vs 4-star: {five.win_rate * 100:.1f}% vs "
            f"{four.win_rate * 100:.1f}% -- 5-star is {verdict} by "
            f"{abs(delta):.1f} points.{caveat}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Prop confidence reliability report. Read-only; writes no rows.")
    parser.add_argument(
        "--db", required=True,
        help="Path to the SQLite database. REQUIRED -- no default.")
    parser.add_argument("--sport", default=None)
    parser.add_argument("--min-bin", type=int, default=DEFAULT_MIN_BIN)
    args = parser.parse_args(argv)

    from backend.database import get_engine, get_session

    session = get_session(get_engine(args.db))
    try:
        print(format_prop_report(
            evaluate_prop_calibration(session, args.sport, min_bin=args.min_bin)))
    finally:
        session.rollback()      # belt and braces: this tool writes nothing
        session.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

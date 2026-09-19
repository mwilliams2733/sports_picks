"""Reconstruct `odds_at_pick` for picks that stored an ungradeable price.

American odds do not exist in the (-100, 100) band. 34 ensemble moneyline
picks from 2026-03-15..21 stored values there (-87..-61), because the old
consensus divided the sum of book prices by the count of ALL odds rows for
the game rather than by the count of rows actually carrying a price. Books
posting only a spread and total pulled the mean toward zero.

`payout_for` correctly refuses to price those, booking 0.0. Because a loss
books -1.0 at any price and only a win needs one, exactly the wins were
zeroed -- so ROI was biased downward rather than merely noisy.

**What this writes is a reconstruction, not a recording.** The price is
recomputed from the book rows that survive today, which are at or before the
pick's timestamp but are not provably the snapshot the pick was taken
against. Every repaired row is flagged `odds_reconstructed = True` so the
distinction survives in the data. Exclude or caveat those rows in any ROI or
CLV figure.

The consensus itself is not reimplemented here: it calls the same
`consensus_moneyline` the live strategies use, so a reconstructed price
cannot drift from one recorded today.

    python -m backend.scripts.repair_invalid_odds --db <abs path> [--apply]
"""

from __future__ import annotations

import argparse

from backend.analysis.strategy import consensus_moneyline
from backend.database import get_engine, get_session, run_migrations
from backend.models import Odds, PickModel, PickResult
from backend.pipeline.grader import payout_for


def find_repairable(session) -> list[PickModel]:
    """Picks whose stored price is inside the invalid American-odds band."""
    return (
        session.query(PickModel)
        .filter(PickModel.odds_at_pick.isnot(None))
        .filter(PickModel.odds_at_pick > -100)
        .filter(PickModel.odds_at_pick < 100)
        .order_by(PickModel.id)
        .all()
    )


def repair_invalid_odds(session, *, apply: bool) -> dict:
    picks = find_repairable(session)
    stats = {
        "repairable": len(picks),
        "would_repair": 0,
        "repaired": 0,
        "unrecoverable": 0,
        "skipped_not_moneyline": 0,
        "payouts_changed": 0,
    }

    for pick in picks:
        if pick.pick_type != "moneyline":
            # Spread and total are stored at a hardcoded -110, so there is no
            # book-level price to rebuild one from. Report, never guess.
            stats["skipped_not_moneyline"] += 1
            print(f"  pick {pick.id}: {pick.pick_type} at {pick.odds_at_pick} "
                  f"-- no book price to rebuild from, left alone")
            continue

        rows = session.query(Odds).filter(Odds.game_id == pick.game_id).all()
        home_side = pick.pick_value.upper().startswith("HOME")
        prices = [r.moneyline_home if home_side else r.moneyline_away
                  for r in rows]
        price = consensus_moneyline(prices)
        if price is None:
            stats["unrecoverable"] += 1
            print(f"  pick {pick.id}: game {pick.game_id} has no usable "
                  f"moneyline rows -- left at {pick.odds_at_pick}")
            continue

        n_prices = sum(1 for p in prices if p is not None)
        print(f"  pick {pick.id}: {pick.odds_at_pick} -> {price}  "
              f"({n_prices} prices of {len(rows)} odds rows)")
        stats["would_repair"] += 1

        if not apply:
            continue
        stats["repaired"] += 1

        pick.odds_at_pick = price
        pick.odds_reconstructed = True

        # payout is DERIVED from the price, so it has to move with it.
        result = (session.query(PickResult)
                  .filter(PickResult.pick_id == pick.id).one_or_none())
        if result is not None:
            new_payout = payout_for(result.result, price)
            if new_payout != result.payout:
                print(f"      payout {result.payout} -> {round(new_payout, 4)} "
                      f"({result.result})")
                result.payout = new_payout
                stats["payouts_changed"] += 1

    if apply:
        session.commit()
    return stats


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True,
                    help="Path to the SQLite database. REQUIRED -- no default, "
                         "so this cannot hit production by accident.")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    engine = get_engine(args.db)
    run_migrations(engine)
    session = get_session(engine)
    try:
        print(f"{'APPLY' if args.apply else 'DRY RUN'} -- {args.db}")
        stats = repair_invalid_odds(session, apply=args.apply)
    finally:
        session.close()

    print()
    for k, v in stats.items():
        print(f"  {k:<22} {v}")
    if args.apply:
        print("\n  Repaired rows are flagged odds_reconstructed = 1.")
        print("  They are an approximation of the price at pick time, not a")
        print("  record of it. Caveat them in any ROI or CLV figure.")
    else:
        print("\n  (dry run -- nothing written; re-run with --apply)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""The weekly review: the model, Claude and the closing line on one page.

Owner request 2026-10-04: make backtesting routine rather than something run
by hand when a question comes up. Every number here comes from the module
that already defines it -- `scorecard.summarize` for records, `clv_report`
for closing line value, `paper_bets.player_bets` for paper players,
`emailed_bets` for what readers were sent -- so this page cannot disagree
with the website. It adds only the joins between them.

Sections, each for the week and for everything to date:

1. What readers were sent (the emailed record).
2. Every graded model pick, published and tracking-only, by sport.
3. Claude's paper picks, and head-to-head with the model on the same games.
4. Closing line value, by market (moneyline in %, spreads/totals in points).
5. NFL props (tracking-only): predicted vs hit, and Brier against the price,
   split at the 2026-10-05 matchup change so before and after never pool.

Read only. Run on a `.backup` snapshot, never the live file:

    python -m backend.scripts.weekly_review --db <snapshot> [--end YYYY-MM-DD]
"""
from __future__ import annotations

import argparse
import os
from datetime import date, timedelta

from backend.analysis import clv_report
from backend.analysis.odds_utils import InvalidOddsError, american_to_implied_prob
from backend.analysis.paper_bets import player_bets
from backend.analysis.scorecard import Bet, Summary, summarize
from backend.database import get_engine, get_session
from backend.digest.record import emailed_bets
from backend.models import (WEATHER_RAIN_UNDER_STRATEGY_ID, Game, PaperPick, PickModel,
                            PickResult)

#: Paper player "Claude" (owner request 2026-10-04).
CLAUDE_USER_ID = 3

#: First prop window with the opponent pass / run defense adjustment
#: (docs/data-dictionary.md). Prop calibration is never pooled across it.
MATCHUP_SINCE = date(2026, 10, 5)


def fmt(s: Summary) -> str:
    if not s.n and not s.pending:
        return f"{s.label:<28} none"
    wr = "  n/a " if s.win_rate is None else f"{s.win_rate:.3f}"
    rng = ("" if s.range_low is None
           else f"  90% {s.range_low:.2f}-{s.range_high:.2f}")
    be = "" if s.break_even is None else f"  needs {s.break_even:.3f}"
    roi = "" if s.roi is None else f"  ROI {s.roi:+.1%}"
    pend = f"  ({s.pending} pending)" if s.pending else ""
    return (f"{s.label:<28} {s.wins}-{s.losses}-{s.pushes}  {wr}{rng}{be}{roi}"
            f"  {s.profit:+.2f}{pend}")


def week_and_total(bets: list[Bet], label: str, start: date, end: date) -> list[str]:
    week = [b for b in bets if start <= b.day <= end]
    return ["  " + fmt(summarize(week, f"{label} (week)")),
            "  " + fmt(summarize([b for b in bets if b.day <= end], f"{label} (to date)"))]


def model_bets(session, *, tracking: bool, sport: str | None = None,
               rain_rule: bool = False) -> list[Bet]:
    """Graded non-prop model picks as 1u Bets (payout is units per 1u staked).

    `rain_rule` selects the rain-Under rule's picks instead; otherwise they
    are excluded (`PickModel.by_model`), so the model's record is its own."""
    q = (session.query(PickModel, PickResult, Game)
         .join(PickResult, PickResult.pick_id == PickModel.id)
         .join(Game, Game.id == PickModel.game_id)
         .filter(PickModel.pick_type != "prop",
                 PickModel.tracking_only.is_(tracking),
                 PickModel.withdrawn_at.is_(None)))
    q = q.filter(PickModel.strategy_id == WEATHER_RAIN_UNDER_STRATEGY_ID if rain_rule
                 else PickModel.by_model())
    if sport:
        q = q.filter(Game.sport == sport)
    return [Bet(result=pr.result, stake=1.0, profit=pr.payout or 0.0,
                odds=pm.odds_at_pick or -110, day=g.date) for pm, pr, g in q.all()]


def _side(pick_value: str | None) -> str | None:
    head = (pick_value or "").split(" ")[0]
    return head if head in ("HOME", "AWAY") else None


def head_to_head(session, start: date, end: date) -> list[str]:
    """Claude's side picks against the model's side on the same game."""
    rows = (session.query(PaperPick, Game).join(Game, Game.id == PaperPick.game_id)
            .filter(PaperPick.user_id == CLAUDE_USER_ID, PaperPick.parlay_id.is_(None),
                    Game.date <= end).all())
    agree = against = no_side = 0
    against_results = {"claude": [0, 0], "model": [0, 0]}  # [won, lost] when they disagree
    for pp, g in rows:
        mine = _side(pp.pick_value)
        # Totals and props carry no HOME/AWAY, so _side drops them.
        theirs = {_side(p.pick_value) for p in session.query(PickModel).filter(
            PickModel.game_id == g.id, PickModel.withdrawn_at.is_(None))} - {None}
        if mine is None or len(theirs) != 1:
            no_side += 1
            continue
        if mine in theirs:
            agree += 1
            continue
        against += 1
        if pp.result in ("win", "loss"):
            won = pp.result == "win"
            against_results["claude"][0 if won else 1] += 1
            against_results["model"][1 if won else 0] += 1
    c, m = against_results["claude"], against_results["model"]
    return [f"  to date: agree {agree}, against {against}, model had no side {no_side}",
            f"  when they disagreed (settled): Claude {c[0]}-{c[1]}, model's side {m[0]}-{m[1]}"
            " (on Claude's line; spreads can differ slightly)"]


def clv_section(session, start: date, end: date) -> list[str]:
    samples = clv_report.usable(clv_report.load_samples(session))
    dates = dict(session.query(Game.id, Game.date)
                 .filter(Game.id.in_({s.game_id for s in samples})).all()) if samples else {}
    out = []
    for label, keep in (("week", lambda d: start <= d <= end), ("to date", lambda d: d <= end)):
        for market in ("moneyline", "spread", "over_under"):
            group = [s for s in samples if s.market == market and keep(dates[s.game_id])]
            if not group:
                continue
            s = clv_report.summarize(group)
            unit = "%" if market in clv_report.PRICE_MARKETS else " pts"
            out.append(f"  {label:<8} {market:<10} n {s.n:>4} on {s.games:>3} games  "
                       f"mean {s.mean:+.2f}{unit}  beat close {s.beat}/{s.n}  p {s.p_value:.2f}")
    return out or ["  no graded picks with a close yet"]


def prop_calibration(session, start: date, end: date) -> list[str]:
    graded = (session.query(PickModel, PickResult, Game)
              .join(PickResult, PickResult.pick_id == PickModel.id)
              .join(Game, Game.id == PickModel.game_id)
              .filter(PickModel.pick_type == "prop", Game.sport == "nfl",
                      Game.date <= end, PickResult.result.in_(("win", "loss"))).all())
    rows = [r for r in graded if r[0].model_prob is not None]
    # Props stored no probability before 2026-10-04, so those cannot be scored
    # here. Say how many, rather than reading as though there were none.
    out = [f"  {len(graded) - len(rows)} graded NFL props predate stored probabilities"
           " (2026-10-04) and are not scored here."] if len(graded) > len(rows) else []
    for label, keep in ((f"before {MATCHUP_SINCE}", lambda d: d < MATCHUP_SINCE),
                        (f"from {MATCHUP_SINCE}", lambda d: d >= MATCHUP_SINCE)):
        group = []
        for pm, pr, g in rows:
            if not keep(g.date):
                continue
            try:
                price = american_to_implied_prob(pm.odds_at_pick)
            except (InvalidOddsError, TypeError):
                continue
            group.append((pm.model_prob, price, 1.0 if pr.result == "win" else 0.0, g.id))
        if not group:
            out.append(f"  {label:<18} no graded props")
            continue
        n = len(group)
        pred = sum(x[0] for x in group) / n
        hit = sum(x[2] for x in group) / n
        brier_m = sum((x[0] - x[2]) ** 2 for x in group) / n
        brier_p = sum((x[1] - x[2]) ** 2 for x in group) / n
        out.append(f"  {label:<18} n {n:>4} on {len({x[3] for x in group}):>3} games  "
                   f"predicted {pred:.3f}  hit {hit:.3f}  "
                   f"Brier model {brier_m:.4f} vs price {brier_p:.4f}"
                   f"  ({'model better' if brier_m < brier_p else 'price better'})")
    out.append("  Price = implied probability with the vig, so it slightly overstates;"
               " props from one game are not independent (games column).")
    return out


def report(session, end: date) -> str:
    start = end - timedelta(days=6)
    out = ["=" * 78, f"WEEKLY REVIEW  {start} .. {end}", "=" * 78, "",
           "Records: W-L-P, win rate, 90% range, the win rate the prices needed, ROI,",
           "profit (units, or $ for paper). A verdict needs the whole 90% range to",
           "clear 'needs'; most lines here will not, and that is the honest reading.", "",
           "1. WHAT READERS WERE SENT"]
    out += week_and_total(emailed_bets(session, "game"), "emailed game picks", start, end)
    out += week_and_total(emailed_bets(session, "prop"), "emailed props", start, end)
    out += ["", "2. MODEL PICKS (graded, not withdrawn)"]
    for sport in ("nfl", "mlb", "mma"):
        out += week_and_total(model_bets(session, tracking=False, sport=sport),
                              f"{sport} published", start, end)
    out += week_and_total(model_bets(session, tracking=True, sport="nfl"),
                          "nfl tracking-only", start, end)
    out += ["", "3. CLAUDE (paper, $100 bets)"]
    out += week_and_total(player_bets(session, CLAUDE_USER_ID), "Claude", start, end)
    out += head_to_head(session, start, end)
    out += ["", "4. CLOSING LINE VALUE (from first-advice price; positive = beat the close)"]
    out += clv_section(session, start, end)
    out += ["", "5. NFL PROPS (tracking-only) -- calibration, split at the matchup change"]
    out += prop_calibration(session, start, end)
    out += ["", "6. RAIN-UNDER RULE (tracking-only; forecast >= 1.0 mm, outdoor NFL)",
            "  Backtest 2022-2026: Under 26-8, but on the games the idea came from.",
            "  This record is the out-of-sample test."]
    out += week_and_total(model_bets(session, tracking=True, rain_rule=True),
                          "rain-Under", start, end)
    return "\n".join(out) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", required=True, help="A .backup snapshot. Read only.")
    ap.add_argument("--end", type=date.fromisoformat,
                    help="Last day of the week (default: yesterday).")
    ap.add_argument("--out", help="Also write the report to this file.")
    args = ap.parse_args(argv)
    if not os.path.exists(args.db):
        raise SystemExit(f"{args.db!r} does not exist.")
    end = args.end or date.today() - timedelta(days=1)
    session = get_session(get_engine(args.db))
    try:
        text = report(session, end)
    finally:
        session.close()
    print(text)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

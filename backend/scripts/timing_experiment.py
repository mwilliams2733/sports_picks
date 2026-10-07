"""Does the line move toward the model after it opens?

Why this exists
---------------
Every test so far found the CLOSING line already knows what the model knows.
The remaining place for an edge is timing: if the model disagrees with the
line early and the market later moves its way, betting early captures that
move (closing-line value), even though the close itself cannot be beaten.

Our `line_snapshots` series (real since 2026-09-23) first sees NFL prices a
median of ~190 hours before kickoff, so this can be measured on our own data.

Definitions, fixed before the first run (2026-10-07):

* **Open.** The consensus of the books showing at the first fetch captured
  AFTER both teams' previous games had ended (previous start + 4h). An
  earlier price predates last week's result, which the model's prediction
  includes; movement from it toward the model would be hindsight.
* **Probabilities.** Home win, no-vig, moneylines, averaged over books
  (at least 4). Close = each book's last pre-kickoff quote.
* **Model.** The calibration report's own replay (`_fit_model` on games
  before 2026-09-23, `_predict_rows` after): the live probability path.

Tests:

1. **Movement.** logit(p_close) - logit(p_open) = a + c * (logit(p_model) -
   logit(p_open)). c > 0: the market moves toward the model after the open.
2. **Bettable.** Bet the model's side at the BEST open price when it
   disagrees with the open by >= 0 / 3 / 5 / 10 points of probability.
   Scored by EV at the no-vig close, beside the same bets at the best
   CLOSING price -- the difference is what betting early is worth.

NFL is primary; MLB is reported for sample size. Writes nothing:

    python -m backend.scripts.timing_experiment --db <snapshot>
"""
from __future__ import annotations

import argparse
import logging
import math
from datetime import date, timedelta

from backend.scripts.injury_experiment import clustered_ols
from backend.scripts.price_shopping_experiment import (MIN_OTHERS, ev, load,
                                                      no_vig, showing, summarize,
                                                      Bet, _ts)

SPLIT = date(2026, 9, 23)
PRIOR_GAME_HOURS = 4
THRESHOLDS = (0.0, 0.03, 0.05, 0.10)


def logit(p: float) -> float:
    return math.log(p / (1 - p))


def home_prob(quotes) -> float | None:
    """Mean no-vig home win probability over `quotes` (book -> row)."""
    ps = [no_vig(q["moneyline_home"], q["moneyline_away"]) for q in quotes.values()
          if q["moneyline_home"] is not None and q["moneyline_away"] is not None]
    ps = [p for p in ps if p is not None]
    return sum(ps) / len(ps) if len(ps) >= MIN_OTHERS else None


def best_price(quotes, side: str) -> int | None:
    from backend.analysis.odds_utils import calculate_payout
    col = "moneyline_home" if side == "home" else "moneyline_away"
    prices = [q[col] for q in quotes.values() if q[col] is not None]
    return max(prices, key=calculate_payout) if prices else None


def open_and_close(game, rows, ready_at):
    """(open quotes, open time, close quotes) or None. Open = first fetch at
    or after `ready_at` with enough books; close = each book's last quote
    before kickoff."""
    pre = [r for r in rows if r["captured_at"] < game["start_time"]]
    moments = sorted({r["captured_at"] for r in pre if r["captured_at"] >= ready_at})
    opened = None
    for m in moments:
        quotes = showing(pre, m)
        if home_prob(quotes) is not None:
            opened = (quotes, m)
            break
    if opened is None:
        return None
    close = {}
    for r in pre:
        if r["bookmaker"] not in close or r["captured_at"] > close[r["bookmaker"]]["captured_at"]:
            close[r["bookmaker"]] = r
    return opened[0], opened[1], close


def prior_end(conn_rows, team_ids, before):
    """Latest previous start among `team_ids` before `before`, + PRIOR_GAME_HOURS."""
    starts = [s for tid, s in conn_rows if tid in team_ids and s < before]
    return (max(starts) + timedelta(hours=PRIOR_GAME_HOURS)) if starts else None


def build(db: str, sport: str, model_probs: dict[int, float]):
    import sqlite3
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    sched = []
    teams = {}
    for gid, h, a, st in conn.execute(
            "select id, home_team_id, away_team_id, start_time from games "
            "where sport = ? and start_time is not null", (sport,)):
        t = _ts(st)
        sched += [(h, t), (a, t)]
        teams[gid] = (h, a)
    conn.close()
    out = []
    for game, rows in load(db, (sport,)):
        gid = game["id"]
        if gid not in model_probs or gid not in teams:
            continue
        ready = prior_end(sched, teams[gid], game["start_time"])
        if ready is None:
            continue
        oc = open_and_close(game, rows, ready)
        if oc is None:
            continue
        open_q, opened_at, close_q = oc
        p_open, p_close = home_prob(open_q), home_prob(close_q)
        if p_close is None:
            continue
        out.append(dict(game=game, p_model=model_probs[gid], p_open=p_open, p_close=p_close,
                        open_q=open_q, close_q=close_q,
                        hours=(game["start_time"] - opened_at).total_seconds() / 3600))
    return out


def report(sport: str, games) -> list[str]:
    out = ["-" * 78, f"{sport.upper()}  ({len(games)} games with a clean open, a close and a model)",
           "-" * 78]
    if len(games) < 5:
        return out + ["  too few games"]
    hrs = sorted(g["hours"] for g in games)
    out.append(f"  open-to-kickoff: median {hrs[len(hrs) // 2]:.0f} h "
               f"(min {hrs[0]:.0f}, max {hrs[-1]:.0f})")
    clip = lambda p: min(max(p, 1e-4), 1 - 1e-4)
    move = [logit(g["p_close"]) - logit(g["p_open"]) for g in games]
    dis = [logit(clip(g["p_model"])) - logit(g["p_open"]) for g in games]
    beta, se, p, _ = clustered_ols([dis], move, [g["game"]["id"] for g in games])
    out += ["  1. movement = a + c * (model - open), log-odds",
            f"     c = {beta[1]:+.3f} (95% CI {beta[1] - 1.96 * se[1]:+.3f}..{beta[1] + 1.96 * se[1]:+.3f}),"
            f" p = {p[1]:.2f}   (c > 0: the line moves toward the model)",
            f"     mean |move| {sum(abs(m) for m in move) / len(move):.3f} log-odds"]
    out.append("  2. bet the model's side; EV judged at the no-vig close")
    for t in THRESHOLDS:
        early, late = [], []
        for g in games:
            d = g["p_model"] - g["p_open"]
            if abs(d) < t or d == 0:
                continue
            side = "home" if d > 0 else "away"
            p_side_close = g["p_close"] if side == "home" else 1 - g["p_close"]
            hs, as_ = g["game"]["home_score"], g["game"]["away_score"]
            won = None if hs == as_ else ((hs > as_) == (side == "home"))
            for bucket, quotes in ((early, g["open_q"]), (late, g["close_q"])):
                price = best_price(quotes, side)
                if price is not None:
                    bucket.append(Bet(g["game"]["id"], sport, "moneyline", side, "best",
                                      price, 0.0, ev(p_side_close, price), won))
        out.append(f"     disagree >= {100 * t:>2.0f} pts  at open:  {summarize(early)}")
        out.append(f"                       at close: {summarize(late)}")
    return out


def main(argv=None) -> int:
    from backend.analysis.calibration_report import _final_games, _fit_model, _predict_rows
    from backend.database import get_engine, get_session
    logging.disable(logging.WARNING)
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", required=True, help="A .backup snapshot (Windows path).")
    ap.add_argument("--sports", default="nfl,mlb")
    args = ap.parse_args(argv)
    session = get_session(get_engine(args.db))
    _fit_model(session, SPLIT)
    print("=" * 78)
    print("TIMING  (does the line move toward the model after a clean open?)")
    print("=" * 78)
    for sport in args.sports.split(","):
        games = [g for g in _final_games(session, sport) if g.date >= SPLIT]
        probs = {g.id: p for g, (p, _, _) in zip(games, _predict_rows(session, games))}
        print("\n".join(report(sport, build(args.db, sport, probs))))
    session.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Does the NBA model know anything the closing spread does not?

Why this exists
---------------
The digest moves from mlb to nba around opening night (2026-10-20). The NFL
model was measured against the closing line (`epa_experiment`: nothing
beyond it). The NBA model never has been: only 42 NBA games in the db have
odds. This uses public closing spreads for every 2025-26 game.

Model side: the calibration report's own out-of-sample path
(`calibration_report._fit_model` / `_predict_rows`). The production
`CalibratedModel` is fitted on games before the report's split date and the
live probability path is replayed on the games after it -- the model that
makes picks, not a stand-in. Only 2025-26 is in the db, so the evaluation
set is that season's last ~30%.

Market side: closing spreads from the public Kaggle dataset
`cviaxmiwnptr/nba-betting-data-october-2007-to-june-2024` (file
nba_2008-2026.csv, through the 2026 Finals; `spread` is the favourite's
points, `whos_favored` says which side). Moneylines stop after 2022-23, so
the spread is the market.

Tests, fixed before the first run (2026-10-06):

1. **Incremental information.** margin = a + b*line + c*model_margin, where
   model_margin = SIGMA * Phi^-1(p_model). SIGMA only rescales c; its
   p-value does not depend on it. c = 0 means the model adds nothing.
2. **Brier.** Model probability vs the market's, p_mkt = Phi(line / s),
   s = SD of (margin - line) over the three seasons before 2025-26 (prior
   data only).
3. **ATS.** Bet the model's side against the close when it disagrees by at
   least 0 / 2 / 4 / 6 points, against -110 break-even.

All-Star games are excluded. Writes nothing; run it on a snapshot:

    python -m backend.scripts.nba_market_experiment --db <snapshot> --odds nba_2008-2026.csv
"""
from __future__ import annotations

import argparse
import logging
import math
from datetime import date, timedelta

from backend.scripts.injury_experiment import binom_p, clustered_ols

logger = logging.getLogger(__name__)

SIGMA = 12.0
THRESHOLDS = (0.0, 2.0, 4.0, 6.0)
PRIOR_SEASONS = (2023, 2024, 2025)     # dataset labels a season by its end year
EVAL_SEASON = 2026


def line_margin(spread: float, favored: str) -> float:
    """The close's home-margin forecast: positive when home is favoured."""
    return spread if favored == "home" else -spread


def load_lines(path: str):
    """{(date, home, away): (line_margin, actual_margin)} and the prior-season
    SD of (margin - line)."""
    import pandas as pd
    d = pd.read_csv(path)
    d = d[d.spread.notna() & d.whos_favored.isin(["home", "away"])]
    lines = {}
    resid = []
    for r in d.itertuples(index=False):
        lm = line_margin(float(r.spread), r.whos_favored)
        margin = float(r.score_home - r.score_away)
        if int(r.season) in PRIOR_SEASONS:
            resid.append(margin - lm)
        if int(r.season) == EVAL_SEASON:
            lines[(date.fromisoformat(r.date), r.home.upper(), r.away.upper())] = (lm, margin)
    mean = sum(resid) / len(resid)
    sd = math.sqrt(sum((x - mean) ** 2 for x in resid) / (len(resid) - 1))
    return lines, sd


def match(lines, d: date, home: str, away: str):
    """The game's line, allowing the db and the dataset a day apart (UTC vs
    local dates). Teams never play on consecutive days at the same venue
    pair, so this cannot cross games."""
    for dd in (d, d - timedelta(days=1), d + timedelta(days=1)):
        hit = lines.get((dd, home, away))
        if hit is not None:
            return hit
    return None


def model_rows(session):
    """(game_id, p_model, home, away, date) for every out-of-sample nba game."""
    from backend.analysis.calibration_report import (_final_games, _fit_model,
                                                     _predict_rows, choose_split_date)
    from backend.models import Team
    split = choose_split_date(session, "nba")
    _fit_model(session, split)
    games = [g for g in _final_games(session, "nba")
             if g.date >= split and g.season_type != "allstar"]
    preds = _predict_rows(session, games)
    teams = {t.id: t.abbreviation for t in session.query(Team).filter(Team.sport == "nba")}
    return split, [(g.id, p, teams[g.home_team_id], teams[g.away_team_id], g.date, hs - as_)
                   for g, (p, hs, as_) in zip(games, preds)]


def join_rows(rows, lines):
    """([(game_id, p, model_margin, line, margin)], unmatched count).

    A game is joined only when the dataset's final margin equals the db's:
    the score is the check that the two rows are the same game."""
    from scipy.stats import norm
    joined = []
    unmatched = 0
    for gid, p, home, away, d, db_margin in rows:
        hit = match(lines, d, home, away)
        if hit is None:
            unmatched += 1
            continue
        lm, margin = hit
        if margin != db_margin:
            # The dataset and the db disagree on the score: a mismatched game.
            unmatched += 1
            continue
        p = min(max(p, 1e-4), 1 - 1e-4)
        joined.append((gid, p, SIGMA * norm.ppf(p), lm, margin))
    return joined, unmatched


def report(split, rows, lines, sd) -> list[str]:
    from scipy.stats import norm
    joined, unmatched = join_rows(rows, lines)
    out = [f"  split {split}: {len(rows)} out-of-sample games, {len(joined)} matched to a "
           f"close ({unmatched} unmatched)",
           f"  prior-season SD of (margin - line): {sd:.2f} pts"]

    beta, se, pv, _ = clustered_ols([[j[3] for j in joined], [j[2] for j in joined]],
                                    [j[4] for j in joined], [j[0] for j in joined])
    corr = _corr([j[3] for j in joined], [j[2] for j in joined])
    out += ["", "  1. margin = a + b*line + c*model_margin",
            f"     line  b = {beta[1]:+.3f} (p {pv[1]:.2g})",
            f"     model c = {beta[2]:+.3f} (95% CI {beta[2] - 1.96 * se[2]:+.3f}.."
            f"{beta[2] + 1.96 * se[2]:+.3f}), p = {pv[2]:.2f}",
            f"     line and model correlate at {corr:+.2f}"]

    win = [1.0 if j[4] > 0 else 0.0 for j in joined]
    brier_m = sum((j[1] - w) ** 2 for j, w in zip(joined, win)) / len(joined)
    brier_k = sum((norm.cdf(j[3] / sd) - w) ** 2 for j, w in zip(joined, win)) / len(joined)
    rmse_m = math.sqrt(sum((j[2] - j[4]) ** 2 for j in joined) / len(joined))
    rmse_k = math.sqrt(sum((j[3] - j[4]) ** 2 for j in joined) / len(joined))
    out += ["", "  2. Brier (lower is better)",
            f"     model {brier_m:.4f}   market {brier_k:.4f}   coin flip 0.2500",
            f"     RMSE vs margin: model {rmse_m:.2f}   line {rmse_k:.2f}"]

    out += ["", "  3. ATS, betting the model's side against the close"]
    for t in THRESHOLDS:
        w = l = push = 0
        for _, _, mm, lm, margin in joined:
            if abs(mm - lm) < t or mm == lm:
                continue
            cover = margin - lm
            if cover == 0:
                push += 1
            elif (cover > 0) == (mm > lm):
                w += 1
            else:
                l += 1
        d = w + l
        rate = f"{w / d:.3f}" if d else "n/a"
        out.append(f"     disagree >= {t:.0f} pts: {w}-{l}-{push}  {rate}  "
                   f"{w * (100 / 110) - l:+.1f}u  p vs 52.4% = {binom_p(w, d):.2f}")
    out += ["", "  One season, games share teams: effective n is below the game count.",
            "  Read intervals, not point estimates."]
    return out


def _corr(a, b) -> float:
    ma, mb = sum(a) / len(a), sum(b) / len(b)
    cov = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    va = sum((x - ma) ** 2 for x in a)
    vb = sum((y - mb) ** 2 for y in b)
    return cov / math.sqrt(va * vb)


def main(argv=None) -> int:
    from backend.database import get_engine, get_session
    logging.basicConfig(level=logging.WARNING)
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", required=True, help="A .backup snapshot (Windows path).")
    ap.add_argument("--odds", required=True, help="nba_2008-2026.csv")
    args = ap.parse_args(argv)
    lines, sd = load_lines(args.odds)
    session = get_session(get_engine(args.db))
    try:
        split, rows = model_rows(session)
    finally:
        session.close()
    print("=" * 72)
    print("NBA MODEL vs THE CLOSING SPREAD  (2025-26, out of sample)")
    print("=" * 72)
    print("\n".join(report(split, rows, lines, sd)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

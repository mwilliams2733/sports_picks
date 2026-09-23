"""Does an EPA rating know anything the closing spread does not?

The question
------------
Three independent measurements agree that this repo's game model has no edge:
market shrinkage put lambda at 0.00 held out across seasons, the nfl backtest
came in worse than a no-edge null at p = 0.0104 on 528 picks, and the closing
line is calibrated in all eight probability bands on 1,171 games.

Every football feature the model fits on is derived from the final score --
point differential, win-loss splits, Elo. A score is the outcome of ~130
scrimmage plays plus special teams plus turnover luck, so it is a noisy
estimate of how well a team played. EPA/play is the standard alternative and
nflverse publishes it free. This script asks whether swapping the input
changes the answer, BEFORE any of it is wired into a strategy.

It is deliberately not a betting system. It writes nothing to the database
and generates no picks.

The three tests
---------------
**A. Predictive validity.** Root mean squared error of the EPA model against
RMSE of the closing spread, both forecasting the actual margin. If the model
cannot get near the line, nothing built on it can.

**B. Incremental information.** Regress margin on BOTH the line and the
model:

    margin = a + b * line + c * model + e

This is the sharp test. `c` is what the model knows that the line does not.
A model can have respectable standalone RMSE purely by agreeing with the
market; only `c` separates information from imitation. `c` indistinguishable
from zero is the same verdict market shrinkage reached on probabilities,
restated on the margin scale where it is easier to act on.

**C. Betting the disagreement.** ATS record at several disagreement
thresholds, scored against the 52.38% a bettor must clear at -110 -- not
against a coin flip. A 53% strategy beats chance and still loses money.

Honest by construction
----------------------
* Ratings are walk-forward: game N uses games 1..N-1 only. `epa_ratings`
  pins this from both directions.
* The EPA-to-points scale is FITTED on training seasons and applied frozen.
  Nothing in the test seasons touches the fit.
* The default split trains on 2022-2024 and tests on 2025-2026, so the
  verdict is out-of-sample in time, which is the only split that matters
  when the opponent is a market that also learns.

Measured 2026-09-22 -- the answer is no
---------------------------------------
Primary run: garbage time dropped, fitted on 2022-2024 (854 games), read on
2025-2026 (317 games). All 1,171 games joined to a closing line, none
unmatched. Fit came out at +2.29 points of home field and 37.88 points per
EPA/play.

**A. The rating is real, and the line is better.** RMSE against actual
margin: EPA model 13.283, closing line 12.399, always-predict-zero 14.321.
The model beats the do-nothing null by a full point, so EPA genuinely
measures football -- this is not a broken pipeline reporting noise. It is
simply 0.88 points worse than the number already on the board.

**B. It adds nothing to the price.** Joint fit gives the model a coefficient
of -0.3649 at p = 0.18 -- indistinguishable from zero, and negative. Read
the collinearity note below before reading the line coefficient: the two
predictors correlate at +0.808 and the univariate slopes are line +1.157 and
model +1.173, so the model is redundant rather than anti-predictive.

**C. Betting the disagreement loses, and loses worse the louder it gets.**

    thresh   bet     W     L    win%     units       p
       0.0   317   157   158  0.4984    -15.27  0.8312
       2.0   171    83    88  0.4854    -12.55  0.8605
       5.0    50    24    26  0.4800     -4.18  0.7770
       7.0    20     7    13  0.3500     -6.64  0.9632

The gradient is the finding. Filtering to the games where the model
disagrees most with the market makes the record WORSE, which is the same
adverse-selection signature the longshot-moneyline work found: selecting on
the largest disagreement selects the model's own largest errors.

Robustness -- four samples, one answer
--------------------------------------
Garbage time KEPT: model coefficient -0.3867 (p = 0.135), ATS 0.4952.

A wider split, fitted on 2022-2023 and read on 2024-2026 (602 games): model
coefficient -0.3603 (p = 0.069), ATS 0.4732, -57.64u, p = 0.9941. Per season
0.4413 / 0.5211 / 0.3226; no season clears break-even.

**And it loses in-sample.** On the training seasons -- the games the
EPA-to-points scale was fitted on -- the record is 398-428-28, 0.4818,
-66.18u. A strategy that cannot win where it was fitted has no tuning
problem to solve.

One cell looks like an edge and is not: the wider split shows 62-55 (+1.36u)
at threshold 5. That is p = 0.48 on 117 bets, and the same cell in the
primary split is 24-26. Six thresholds times four samples is twenty-four
looks; one of them landing above break-even by a unit is what noise looks
like.

What this rules out, and what it does not
-----------------------------------------
RULED OUT: that the model's failure is a feature-quality problem in football.
EPA is the strongest public team-strength signal available, it is measurably
informative, and swapping it in changes nothing -- because the closing line
already contains it. Any further score-derived or box-score-derived team
rating will land in the same place.

NOT RULED OUT, and not tested here: beating the OPENING number rather than
the close, which is a different and much softer target; player-level inputs
the line reacts to faster than a team rating can (quarterback status above
all); and predicting line MOVEMENT rather than game outcome. All three need
data this repo does not yet store -- odds snapshots rather than the current
destructive upsert.

Running it
----------
Play-by-play is not in the repo -- roughly 80MB of it. Fetch once:

    for y in 2022 2023 2024 2025 2026; do
      curl -sL -o pbp/play_by_play_$y.csv.gz \\
        https://github.com/nflverse/nflverse-data/releases/download/pbp/play_by_play_$y.csv.gz
    done

    python -m backend.scripts.epa_experiment --db <abs path> --pbp-dir pbp
"""
from __future__ import annotations

import argparse
import glob
import logging
import math
import os
import statistics
from dataclasses import dataclass
from datetime import date

from backend.analysis.epa_ratings import (aggregate_team_games, epa_edge,
                                          fit_margin, margin_from_edge,
                                          walk_forward_ratings, Play)
from backend.database import get_engine, get_session
from backend.models import Game, Odds, Team
from backend.scripts.import_nflverse_history import (ABBR_FIXUPS,
                                                     CLOSING_BOOKMAKER)

logger = logging.getLogger(__name__)

#: Seasons the EPA-to-points scale is fitted on. Nothing later touches it.
TRAIN_SEASONS = (2022, 2023, 2024)

#: Seasons the verdict is read from.
TEST_SEASONS = (2025, 2026)

#: Win rate a -110 bettor must clear on decided bets to break even.
#: 110 / (110 + 100). The null is this, never 0.5.
BREAK_EVEN = 110.0 / 210.0

#: Disagreement thresholds, in points, that test C reports.
THRESHOLDS = (0.0, 1.0, 2.0, 3.0, 5.0, 7.0)

#: Columns read from the play-by-play export. It ships 372.
PBP_COLUMNS = ["game_id", "season", "week", "game_date", "home_team",
               "away_team", "posteam", "defteam", "play_type", "epa", "wp",
               "aborted_play"]


@dataclass(frozen=True)
class Outcome:
    """One test-season game: what each forecaster said, and what happened.

    All three are on the same scale -- points, positive for the home side.
    """
    line_pred: float
    model_pred: float
    margin: float
    season: int = 0


@dataclass(frozen=True)
class Incremental:
    """Result of regressing margin on the line and the model together.

    `line_alone` / `model_alone` are the univariate slopes, and `corr` is the
    correlation between the two predictors. They exist because the joint
    coefficients are not readable without them: both forecasters are
    predicting the same quantity, so they are strongly collinear, and the
    pair is well determined while each one individually is not. A joint line
    coefficient of 1.37 looks like a market undershooting margins by 37%
    until you see that the model coefficient is -0.37 and the two sum to
    about 1.0.
    """
    n: int
    intercept: float
    line_coef: float
    line_p: float
    model_coef: float
    model_p: float
    resid_sd: float
    line_alone: float
    model_alone: float
    corr: float


@dataclass(frozen=True)
class AtsRecord:
    """An against-the-spread record at one disagreement threshold."""
    threshold: float
    bet: int
    won: int
    lost: int
    pushed: int

    @property
    def decided(self) -> int:
        """Pushes return the stake, so they are not part of a win rate."""
        return self.won + self.lost

    @property
    def win_rate(self) -> float | None:
        """None, not 0.0, when nothing was decided -- an absent measurement
        is not a catastrophic one."""
        return self.won / self.decided if self.decided else None

    @property
    def units(self) -> float:
        """Profit at -110: a win returns 100/110, a loss costs 1."""
        return self.won * (100.0 / 110.0) - self.lost

    @property
    def p_value_vs_breakeven(self) -> float:
        """One-sided binomial tail: P(at least this many wins | BREAK_EVEN).

        Against 52.38%, not 50%. A strategy can be significantly better than
        a coin flip and still lose money every month.
        """
        if not self.decided:
            return 1.0
        from scipy import stats
        return float(stats.binom.sf(self.won - 1, self.decided, BREAK_EVEN))


def line_margin(spread_home: float) -> float:
    """The closing line's own margin forecast, positive for the home side.

    `Odds.spread_home` is the handicap applied to the home team, so a 3.5
    point home favourite is stored -3.5 and is being forecast to win by 3.5.
    Used unflipped, every comparison in this script runs backwards and the
    market comes out anti-predictive.
    """
    return -spread_home


def rmse(pairs) -> float:
    """Root mean squared error over `(prediction, actual)` pairs."""
    pairs = list(pairs)
    if not pairs:
        raise ValueError("rmse needs at least one pair")
    return math.sqrt(sum((p - a) ** 2 for p, a in pairs) / len(pairs))


def incremental_information(rows) -> Incremental:
    """Fit `margin = a + b*line + c*model` and report both coefficients.

    `c` is the whole experiment: what the model knows that the price does
    not. Standard errors are the textbook OLS ones, which assume independent
    observations -- see the caveat printed with the results.
    """
    rows = list(rows)
    if len(rows) < 3:
        raise ValueError(f"need at least 3 games to fit 3 parameters, "
                         f"got {len(rows)}")

    import numpy as np
    from scipy import stats

    x = np.column_stack([np.ones(len(rows)),
                         np.array([r.line_pred for r in rows]),
                         np.array([r.model_pred for r in rows])])
    y = np.array([r.margin for r in rows], dtype=float)

    xtx_inv = np.linalg.pinv(x.T @ x)
    beta = xtx_inv @ x.T @ y
    resid = y - x @ beta
    dof = len(rows) - x.shape[1]
    sigma2 = float(resid @ resid) / dof
    se = np.sqrt(np.diag(xtx_inv) * sigma2)
    p = 2.0 * stats.t.sf(np.abs(beta / se), dof)

    line = x[:, 1]
    model = x[:, 2]
    # Univariate slopes and the predictors' correlation, so the joint
    # coefficients above can be read rather than misread.
    def _slope(v):
        centred = v - v.mean()
        denom = float(centred @ centred)
        return float(centred @ (y - y.mean()) / denom) if denom else 0.0

    corr = (float(np.corrcoef(line, model)[0, 1])
            if line.std() and model.std() else 0.0)

    return Incremental(n=len(rows), intercept=float(beta[0]),
                       line_coef=float(beta[1]), line_p=float(p[1]),
                       model_coef=float(beta[2]), model_p=float(p[2]),
                       resid_sd=math.sqrt(sigma2),
                       line_alone=_slope(line), model_alone=_slope(model),
                       corr=corr)


def ats_record(rows, *, threshold: float) -> AtsRecord:
    """Bet the side the model likes by more than `threshold` points.

    Home covers when the actual margin beats the line's forecast; landing
    exactly on it is a push, which returns the stake and is neither a win nor
    a loss.
    """
    won = lost = pushed = 0
    for r in rows:
        disagreement = r.model_pred - r.line_pred
        if abs(disagreement) <= threshold:
            continue
        if r.margin == r.line_pred:
            pushed += 1
        elif (r.margin > r.line_pred) == (disagreement > 0):
            won += 1
        else:
            lost += 1
    return AtsRecord(threshold=threshold, bet=won + lost + pushed,
                     won=won, lost=lost, pushed=pushed)


# --- loading -------------------------------------------------------------

def load_plays(pbp_dir: str) -> list[Play]:
    """Read every `play_by_play_*.csv.gz` in a directory."""
    import pandas as pd

    paths = sorted(glob.glob(os.path.join(pbp_dir, "play_by_play_*.csv.gz")))
    if not paths:
        raise SystemExit(f"no play_by_play_*.csv.gz under {pbp_dir!r} "
                         f"-- see this module's docstring for the fetch")

    plays: list[Play] = []
    for path in paths:
        frame = pd.read_csv(path, usecols=PBP_COLUMNS, low_memory=False)
        logger.info("%s: %d plays", os.path.basename(path), len(frame))
        for row in frame.itertuples(index=False):
            plays.append(Play(
                game_id=row.game_id, season=int(row.season),
                week=None if pd.isna(row.week) else int(row.week),
                game_date=date.fromisoformat(str(row.game_date)),
                home_team=row.home_team, away_team=row.away_team,
                posteam=None if pd.isna(row.posteam) else row.posteam,
                defteam=None if pd.isna(row.defteam) else row.defteam,
                play_type=None if pd.isna(row.play_type) else row.play_type,
                epa=None if pd.isna(row.epa) else float(row.epa),
                wp=None if pd.isna(row.wp) else float(row.wp),
                aborted_play=(0 if pd.isna(row.aborted_play)
                              else int(row.aborted_play))))
    return plays


def closing_lines(session) -> dict[tuple[date, str, str], tuple[float, float]]:
    """Map (date, home_abbr, away_abbr) -> (spread_home, actual_margin).

    Keyed on this database's abbreviations; callers translate nflverse's two
    outliers through the import script's own `ABBR_FIXUPS` rather than
    keeping a second copy of that mapping.
    """
    home, away = (Team.__table__.alias("home"), Team.__table__.alias("away"))
    rows = (session.query(Game.date, home.c.abbreviation, away.c.abbreviation,
                          Odds.spread_home, Game.home_score, Game.away_score)
            .join(home, home.c.id == Game.home_team_id)
            .join(away, away.c.id == Game.away_team_id)
            .join(Odds, Odds.game_id == Game.id)
            .filter(Game.sport == "nfl", Game.status == "final",
                    Odds.bookmaker == CLOSING_BOOKMAKER,
                    Odds.spread_home.isnot(None),
                    Game.home_score.isnot(None))
            .all())
    return {(d, h, a): (float(sp), float(hs - as_))
            for d, h, a, sp, hs, as_ in rows}


def build_outcomes(plays, lines, *, drop_garbage_time: bool = True,
                   train_seasons=TRAIN_SEASONS, test_seasons=TEST_SEASONS):
    """Join walk-forward ratings to closing lines, split by season.

    Returns `(train, test, coverage)` where each row is
    `(season, edge, line_pred, margin)`.
    """
    team_games = aggregate_team_games(plays, drop_garbage_time=drop_garbage_time)
    ratings = walk_forward_ratings(team_games)

    by_game: dict[str, dict] = {}
    for tg in team_games:
        by_game.setdefault(tg.game_id, {})[
            "home" if tg.is_home else "away"] = tg

    train, test, matched, unmatched = [], [], 0, 0
    for game_id, sides in by_game.items():
        if "home" not in sides or "away" not in sides:
            continue
        h, a = sides["home"], sides["away"]
        key = (h.game_date, ABBR_FIXUPS.get(h.team, h.team),
               ABBR_FIXUPS.get(a.team, a.team))
        if key not in lines:
            unmatched += 1
            continue
        matched += 1
        spread_home, margin = lines[key]
        edge = epa_edge(ratings[(game_id, h.team)], ratings[(game_id, a.team)])
        row = (h.season, edge, line_margin(spread_home), margin)
        if h.season in train_seasons:
            train.append(row)
        elif h.season in test_seasons:
            test.append(row)

    return train, test, {"matched": matched, "unmatched": unmatched}


def run(plays, lines, *, drop_garbage_time: bool = True,
        train_seasons=TRAIN_SEASONS, test_seasons=TEST_SEASONS) -> dict:
    """Fit on the training seasons, report the three tests on the test ones."""
    train, test, coverage = build_outcomes(
        plays, lines, drop_garbage_time=drop_garbage_time,
        train_seasons=train_seasons, test_seasons=test_seasons)
    if len(train) < 50 or len(test) < 50:
        raise SystemExit(f"not enough joined games: {len(train)} train, "
                         f"{len(test)} test (coverage {coverage})")

    fit = fit_margin([(edge, margin) for _, edge, _, margin in train])
    rows = [Outcome(line_pred=lp, model_pred=margin_from_edge(edge, fit),
                    margin=m, season=season)
            for season, edge, lp, m in test]

    return {
        "coverage": coverage,
        "train_seasons": tuple(train_seasons),
        "test_seasons": tuple(test_seasons),
        "n_train": len(train), "n_test": len(test),
        "fit": fit,
        "rmse_model": rmse([(r.model_pred, r.margin) for r in rows]),
        "rmse_line": rmse([(r.line_pred, r.margin) for r in rows]),
        "rmse_naive": rmse([(0.0, r.margin) for r in rows]),
        "incremental": incremental_information(rows),
        "ats": [ats_record(rows, threshold=t) for t in THRESHOLDS],
        "mean_abs_disagreement": statistics.fmean(
            abs(r.model_pred - r.line_pred) for r in rows),
        # One pooled number is one measurement. Split it so a result driven
        # by a single season cannot read as a stable effect.
        "by_season": {s: ats_record([r for r in rows if r.season == s],
                                    threshold=0.0)
                      for s in sorted({r.season for r in rows})},
        # The training seasons are in-sample for the FIT but the ATS record
        # on them is still worth seeing: a strategy that cannot win where it
        # was fitted has nothing to salvage out of sample.
        "train_ats": ats_record(
            [Outcome(line_pred=lp, model_pred=margin_from_edge(edge, fit),
                     margin=m, season=season)
             for season, edge, lp, m in train], threshold=0.0),
    }


def format_report(res: dict, *, drop_garbage_time: bool) -> str:
    fit, inc = res["fit"], res["incremental"]
    out = [
        "=" * 72,
        f"EPA EXPERIMENT   garbage time {'dropped' if drop_garbage_time else 'kept'}",
        "=" * 72, "",
        f"  train {res['train_seasons']} : {res['n_train']} games",
        f"  test  {res['test_seasons']} : {res['n_test']} games",
        f"  joined to a closing line: {res['coverage']['matched']}, "
        f"unmatched: {res['coverage']['unmatched']}",
        "",
        "  Fitted on the TRAINING seasons only, then frozen:",
        f"    home field      {fit.home_field:+.2f} points",
        f"    points per EPA  {fit.points_per_epa:+.2f}",
        "",
        "-" * 72,
        "A. PREDICTIVE VALIDITY -- root mean squared error vs actual margin",
        "-" * 72,
        f"    EPA model      {res['rmse_model']:.3f}",
        f"    closing line   {res['rmse_line']:.3f}",
        f"    always pick 0  {res['rmse_naive']:.3f}   (the do-nothing null)",
        "",
        f"    model - line   {res['rmse_model'] - res['rmse_line']:+.3f}"
        "   (negative would mean the model is better)",
        "",
        "-" * 72,
        "B. INCREMENTAL INFORMATION -- margin ~ line + model",
        "-" * 72,
        "    What does the model know that the price does not?",
        "",
        f"    intercept   {inc.intercept:+.3f}",
        f"    line        {inc.line_coef:+.4f}   p = {inc.line_p:.4f}",
        f"    model       {inc.model_coef:+.4f}   p = {inc.model_p:.4f}   "
        "<-- the answer",
        f"    residual sd {inc.resid_sd:.2f} points   n = {inc.n}",
        "",
        "    Read the pair, not either alone. Both forecast the same quantity",
        f"    and correlate at {inc.corr:+.3f}, so they are collinear: the SUM",
        f"    ({inc.line_coef + inc.model_coef:+.3f}) is well determined while each is not.",
        f"    Fitted separately: line {inc.line_alone:+.4f}, "
        f"model {inc.model_alone:+.4f}.",
        "",
        "    Standard errors assume independent games. Each team appears ~17",
        "    times a season, so the effective sample is smaller than n and",
        "    these p-values are, if anything, optimistic.",
        "",
        "-" * 72,
        "C. BETTING THE DISAGREEMENT -- ATS, scored against -110 break-even",
        "-" * 72,
        f"    mean |model - line| = {res['mean_abs_disagreement']:.2f} points",
        f"    break-even is {BREAK_EVEN:.4f}, not 0.5000",
        "",
        f"    {'thresh':>7}  {'bet':>5} {'W':>5} {'L':>5} {'P':>4}  "
        f"{'win%':>7} {'units':>8}  {'p':>7}",
    ]
    for rec in res["ats"]:
        wr = "    n/a" if rec.win_rate is None else f"{rec.win_rate:7.4f}"
        out.append(f"    {rec.threshold:>7.1f}  {rec.bet:>5} {rec.won:>5} "
                   f"{rec.lost:>5} {rec.pushed:>4}  {wr} {rec.units:>8.2f}  "
                   f"{rec.p_value_vs_breakeven:>7.4f}")
    out += ["", "    per season, at threshold 0:"]
    for season, rec in res["by_season"].items():
        wr = "    n/a" if rec.win_rate is None else f"{rec.win_rate:7.4f}"
        out.append(f"    {season:>7}  {rec.bet:>5} {rec.won:>5} {rec.lost:>5} "
                   f"{rec.pushed:>4}  {wr} {rec.units:>8.2f}")
    tr = res["train_ats"]
    out += ["",
            f"    for reference, the TRAINING seasons (in-sample for the fit): "
            f"{tr.won}-{tr.lost}-{tr.pushed}, "
            f"{'n/a' if tr.win_rate is None else format(tr.win_rate, '.4f')}, "
            f"{tr.units:+.2f}u"]
    out += ["", "    p is P(at least this many wins | break-even bettor).",
            "    Small p = evidence of an edge. Large p = consistent with none.", ""]
    return "\n".join(out)


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", required=True, help="Path to the database. Read only.")
    ap.add_argument("--pbp-dir", required=True,
                    help="Directory of play_by_play_*.csv.gz.")
    ap.add_argument("--keep-garbage-time", action="store_true",
                    help="Also report with the win-probability filter off.")
    ap.add_argument("--train-seasons", type=int, nargs="+",
                    default=list(TRAIN_SEASONS),
                    help="Seasons the EPA-to-points scale is fitted on.")
    ap.add_argument("--test-seasons", type=int, nargs="+",
                    default=list(TEST_SEASONS),
                    help="Seasons the verdict is read from. Must not overlap "
                         "the training seasons.")
    args = ap.parse_args(argv)

    if not os.path.exists(args.db):
        raise SystemExit(f"{args.db!r} does not exist.")
    overlap = set(args.train_seasons) & set(args.test_seasons)
    if overlap:
        raise SystemExit(f"train and test seasons overlap on {sorted(overlap)} "
                         f"-- the verdict would be read from the fit.")

    session = get_session(get_engine(args.db))
    try:
        lines = closing_lines(session)
        logger.info("closing lines available: %d", len(lines))
        plays = load_plays(args.pbp_dir)
        logger.info("plays loaded: %d", len(plays))

        modes = [True, False] if args.keep_garbage_time else [True]
        for drop in modes:
            print(format_report(
                run(plays, lines, drop_garbage_time=drop,
                    train_seasons=tuple(args.train_seasons),
                    test_seasons=tuple(args.test_seasons)),
                drop_garbage_time=drop))
    finally:
        session.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

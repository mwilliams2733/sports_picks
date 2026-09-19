"""Out-of-sample calibration report for the ensemble win-probability model.

Read-only. This module measures; it never tunes and it writes no rows.

Why it exists
-------------
``edge_pct = model_probability - devigged_market_probability``.  The market is
well calibrated, so ranking picks by edge ranks them by how far the model
disagrees with a well-calibrated reference.  If the model is the unreliable
party, that is a ranking by model *error*.  Before anyone retunes ``min_edge``
or changes the ranking key, somebody has to measure how reliable the model's
probabilities actually are.  This produces that reliability curve.

The leakage trap
----------------
``CalibratedModel.train_from_db`` selects *every* ``status='final'`` game.
Scoring the model on those same games would grade it on outcomes it was fit on
and report a calibration that does not exist.  ``evaluate`` therefore uses a
time-based split: the model is fit only on games strictly before
``split_date`` and scored only on games on or after it.

The restriction is applied with a SQLAlchemy ``do_orm_execute`` hook that adds
``with_loader_criteria(Game, Game.date < split_date)`` for the duration of the
fit.  This deliberately reuses the production ``train_from_db`` rather than
reimplementing it, so the thing being measured is the thing that runs in
production -- ``calibrated_model.py`` is out of scope and is not modified.

``EnsembleStrategy._calibrated`` is a *class* attribute and so persists across
instances within a process.  Every entry point here saves it, overwrites it
with an explicitly fitted model, and restores it afterwards, so a stale model
cannot silently leak into a later run.

Known limitation: three features are constant
---------------------------------------------
``offensive_rating``, ``defensive_rating`` and ``pace`` all need possession
counts, and nothing in this repo collects them.  ``backfill_team_stats``
deliberately refuses to invent them, so they are absent for every game and the
consumers' ``or 100.0`` fallbacks supply the same constant each time.  Whatever
this report measures, it measures on point differential, Elo, win-loss splits
and rest days.  The caveat is printed alongside the Brier score so it travels
with the numbers.

Historical note: until plan 008 this section documented a different, worse
limitation -- that the evaluation *features* were season-end snapshots, making
every error a lower bound.  That is no longer true.  ``_build_game_data``
passes ``game_id`` and ``game_date`` into ``_team_stat_rows``, which bounds the
lookup to rows written strictly before the game being scored and so can never
reach forward in time.  The numbers this report prints are estimates, not
lower bounds.
"""
from __future__ import annotations

import argparse
import contextlib
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import and_, event
from sqlalchemy.orm import Session, with_loader_criteria

from backend.analysis.calibrated_model import (
    MIN_TRAINING_GAMES,
    SPORT_VOCAB,
    CalibratedModel,
)
from backend.analysis.variants.ensemble import EnsembleStrategy
from backend.data_types import GameData, TeamStats
from backend.models import Game

# The live ensemble configuration, so the measured probabilities are the ones
# production would produce. Kept here rather than read from config.yaml because
# this report must not depend on a file someone may retune mid-investigation.
LIVE_ENSEMBLE_CONFIG = {
    "min_edge": 3,
    "k_factor": 20,
    "lookback": 10,
    "weights": {"pd": 0.3, "elo": 0.35, "rating": 0.25, "hca": 0.1},
}

# Below this many evaluation games a reliability curve is not worth printing.
MIN_EVAL_GAMES = 200


# --------------------------------------------------------------------------
# Pure statistics core (no database)
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Bin:
    """One bucket of a reliability curve.

    ``gap`` is signed as *predicted minus observed*: positive means the model
    claimed more than it delivered, i.e. overconfident in that bucket.
    """

    lo: float
    hi: float
    n: int
    mean_predicted: float | None
    observed_rate: float | None
    gap: float | None
    reliable: bool


def reliability_bins(
    pairs: list[tuple[float, int]], n_bins: int = 10, min_bin: int = 30
) -> list[Bin]:
    """Bucket ``(predicted_prob, actual_outcome)`` pairs into a reliability curve.

    Every bucket is returned, including empty ones, so a reader can see where
    the probability mass actually sits. A bucket holding fewer than ``min_bin``
    observations is flagged ``reliable=False`` rather than being silently
    averaged into a number nobody should trust.
    """
    if n_bins < 1:
        raise ValueError("n_bins must be >= 1")

    buckets: list[list[tuple[float, int]]] = [[] for _ in range(n_bins)]
    for prob, outcome in pairs:
        if not 0.0 <= prob <= 1.0:
            raise ValueError(f"predicted probability out of range: {prob}")
        if outcome not in (0, 1):
            raise ValueError(f"outcome must be 0 or 1, got {outcome!r}")
        # Round before truncating so a value that is a float hair under a bin
        # edge (0.3 stored as 0.29999999999999999) lands in the bin a reader
        # would expect. Hygiene: it cannot lose or duplicate an observation.
        idx = min(int(round(prob * n_bins, 12)), n_bins - 1)
        buckets[idx].append((prob, outcome))

    out: list[Bin] = []
    for i, bucket in enumerate(buckets):
        lo, hi = i / n_bins, (i + 1) / n_bins
        n = len(bucket)
        if n == 0:
            out.append(Bin(lo, hi, 0, None, None, None, False))
            continue
        mean_pred = sum(p for p, _ in bucket) / n
        observed = sum(o for _, o in bucket) / n
        out.append(
            Bin(lo, hi, n, mean_pred, observed, mean_pred - observed, n >= min_bin)
        )
    return out


def brier_score(pairs: list[tuple[float, int]]) -> float:
    """Mean squared error of the predicted probabilities. Lower is better."""
    if not pairs:
        raise ValueError("brier_score needs at least one pair")
    return sum((p - o) ** 2 for p, o in pairs) / len(pairs)


def binary_pairs(rows: list[tuple[float, int, int]]) -> list[tuple[float, int]]:
    """Turn ``(predicted_prob, home_score, away_score)`` into binary pairs.

    Ties are *excluded* from the series, not scored as home losses. NBA cannot
    tie, but other sports can, and folding a draw into the loss column would
    make the model look worse than it is for a reason unrelated to calibration.
    """
    pairs: list[tuple[float, int]] = []
    for prob, home_score, away_score in rows:
        if home_score == away_score:
            continue
        pairs.append((prob, 1 if home_score > away_score else 0))
    return pairs


def effective_sample_size(n: int, n_clusters: int, icc: float) -> float:
    """Cluster-adjusted sample size under an assumed intra-cluster correlation.

    Games sharing a team are not independent observations. The clusters are
    teams -- one cluster per team, not per team-season. With
    average cluster size ``m``, the design effect is ``1 + (m - 1) * icc`` and
    the effective n is ``n / design_effect``. ``icc`` is an assumption, not a
    measurement -- callers should quote the assumption alongside the number.
    """
    if n <= 0 or n_clusters <= 0:
        return 0.0
    m = (2.0 * n) / n_clusters  # each game touches two teams
    design_effect = 1.0 + (m - 1.0) * icc
    return n / design_effect


# --------------------------------------------------------------------------
# Database-backed evaluation
# --------------------------------------------------------------------------

@dataclass
class Report:
    sport: str
    split_date: date
    in_sample: bool
    eval_on: str
    fit_start: date | None
    fit_end: date | None
    n_fit: int
    n_model_training_games: int
    # False when CalibratedModel refused to fit (fewer than MIN_TRAINING_GAMES
    # in the window). The strategy then silently serves _fallback_probability,
    # a fixed heuristic -- so the curve would describe the heuristic, not the
    # model, and must not be presented as the model's calibration.
    model_trained: bool
    eval_start: date | None
    eval_end: date | None
    n_eval: int
    n_ties_excluded: int
    n_teams: int
    bins: list[Bin]
    brier: float | None
    #: P(home win) the fitted model gives when every difference feature is
    #: zero -- i.e. the baseline it has learned for this sport. Compared
    #: against ``actual_home_rate``, this is what exposes a pooled intercept
    #: being applied to a sport it does not fit. The Brier score will not:
    #: a baseline off by ten points still scores well if the ranking holds.
    fitted_home_baseline: float | None = None
    #: The home win rate of THIS SPORT'S games in the fit window. Not the
    #: whole fit set, which spans every sport -- comparing a sport-specific
    #: baseline against a pooled rate would re-create the confusion this
    #: pair of numbers exists to expose.
    actual_home_rate: float | None = None
    #: How many games ``actual_home_rate`` rests on. Printed because a rate
    #: without its denominator invites confidence the sample cannot support.
    n_fit_sport: int = 0
    #: How many of those had no host. A "home win rate" measured on neutral
    #: games is not home advantage -- in a tournament bracket the home side
    #: is the higher seed, so the rate is seed strength wearing the same
    #: name. The model gates its per-sport slot on this, and the reader needs
    #: to know how much of the number below it applies to.
    n_fit_neutral: int = 0
    fit_game_ids: frozenset[int] = field(default_factory=frozenset)
    eval_game_ids: frozenset[int] = field(default_factory=frozenset)


def _final_games(session: Session, sport: str | None = None) -> list[Game]:
    conds = [
        Game.status == "final",
        Game.home_score.isnot(None),
        Game.away_score.isnot(None),
    ]
    if sport is not None:
        conds.append(Game.sport == sport)
    return session.query(Game).filter(and_(*conds)).order_by(Game.date, Game.id).all()


def choose_split_date(session: Session, sport: str, train_frac: float = 0.7) -> date:
    """The date that puts roughly ``train_frac`` of a sport's final games behind it.

    Returns the date of the first game past the target index; games on that
    date fall in the evaluation set, so the split never cuts a single day in
    half (which would put a team's same-day context on both sides).
    """
    games = _final_games(session, sport)
    if not games:
        raise ValueError(f"no final games for sport {sport!r}")
    target = max(1, int(round(len(games) * train_frac)))
    return games[min(target, len(games) - 1)].date


@contextlib.contextmanager
def _games_before(session: Session, split_date: date | None):
    """Restrict every ``Game`` select on this session to dates before ``split_date``.

    Used to make the unmodified production ``train_from_db`` fit on the
    training window only. A ``None`` split_date is the deliberate no-op used by
    the in-sample control run.
    """
    if split_date is None:
        yield
        return

    def _limit(execute_state):
        if execute_state.is_select:
            execute_state.statement = execute_state.statement.options(
                with_loader_criteria(Game, Game.date < split_date, include_aliases=True)
            )

    event.listen(session, "do_orm_execute", _limit)
    try:
        yield
    finally:
        event.remove(session, "do_orm_execute", _limit)


def _fit_model(session: Session, split_date: date | None) -> CalibratedModel:
    model = CalibratedModel()
    with _games_before(session, split_date):
        model.train_from_db(session)
    return model


def _predict_rows(session: Session, games: list[Game]) -> list[tuple[float, int, int]]:
    """Replay the live probability path over ``games``.

    Deliberately leaves ``_lgbm_model`` as ``None``: a freshly constructed
    strategy is what ``pick_generator`` builds per game, and LightGBM is
    trained nightly then discarded, so in production the legacy path is the one
    that runs. Measuring anything else would measure a model that never makes a
    pick.
    """
    from backend.pipeline.pick_generator import _build_game_data

    rows: list[tuple[float, int, int]] = []
    for game in games:
        strategy = EnsembleStrategy("ensemble", LIVE_ENSEMBLE_CONFIG)
        game_data = _build_game_data(session, game)
        prob = strategy._calibrated_probability(game_data)
        rows.append((prob, game.home_score, game.away_score))
    return rows


def evaluate(
    session: Session,
    sport: str,
    n_bins: int = 10,
    min_bin: int = 30,
    split_date: date | None = None,
    in_sample: bool = False,
    eval_on: str = "eval",
) -> Report:
    """Measure calibration of the live probability path on held-out games.

    ``in_sample=True`` is the leakage control: the model is fit with no date
    restriction, so it has seen the evaluation games' outcomes. ``eval_on="fit"``
    scores the training window instead of the held-out one. Both exist to prove
    the guard bites; neither produces a publishable number.
    """
    if eval_on not in ("eval", "fit"):
        raise ValueError("eval_on must be 'eval' or 'fit'")

    if split_date is None:
        split_date = choose_split_date(session, sport)

    # The production model is fit on every sport's final games, not just one,
    # so the fit set is defined the same way -- that is what the model saw.
    # Under in_sample the date restriction is lifted, so the fit set must widen
    # to match, or the overlap warning would fail to fire on a genuinely
    # leaked run.
    all_final = _final_games(session)
    fit_games = all_final if in_sample else [g for g in all_final if g.date < split_date]
    sport_games = [g for g in all_final if g.sport == sport]
    if eval_on == "fit":
        eval_games = [g for g in sport_games if g.date < split_date]
    else:
        eval_games = [g for g in sport_games if g.date >= split_date]

    model = _fit_model(session, None if in_sample else split_date)

    # _calibrated is a class attribute; own it explicitly and put it back.
    previous = EnsembleStrategy._calibrated
    EnsembleStrategy._calibrated = model
    try:
        rows = _predict_rows(session, eval_games)
    finally:
        EnsembleStrategy._calibrated = previous

    pairs = binary_pairs(rows)
    teams = {g.home_team_id for g in eval_games} | {g.away_team_id for g in eval_games}

    # Probe the fitted model with every difference feature at zero, so the
    # only thing left speaking is the sport encoding and the intercept.
    # Built from the games already loaded -- a second query would describe a
    # different population than the rate it is printed beside.
    sport_fit = [g for g in fit_games if g.sport == sport]
    fitted_home_baseline = None
    actual_home_rate = None
    if model.trained:
        flat = TeamStats(
            point_diff=0.0, home_record=(0, 0), away_record=(0, 0),
            last_n_record=(0, 0), offensive_rating=100.0,
            defensive_rating=100.0, pace=100.0, strength_of_schedule=0.0,
            elo_rating=1500.0, rest_days=1,
        )
        probe = GameData(
            game_id=0, sport=sport, date=split_date,
            home_team_id=0, away_team_id=0,
            home_stats=flat, away_stats=flat, odds=[],
        )
        fitted_home_baseline = model.predict_home_win_prob(probe)
    if sport_fit:
        home_wins = sum(1 for g in sport_fit if g.home_score > g.away_score)
        actual_home_rate = home_wins / len(sport_fit)
    n_fit_neutral = sum(1 for g in sport_fit if g.neutral_site)

    return Report(
        sport=sport,
        split_date=split_date,
        in_sample=in_sample,
        eval_on=eval_on,
        fit_start=fit_games[0].date if fit_games else None,
        fit_end=fit_games[-1].date if fit_games else None,
        n_fit=len(fit_games),
        n_model_training_games=model.n_training_games,
        model_trained=model.trained,
        eval_start=eval_games[0].date if eval_games else None,
        eval_end=eval_games[-1].date if eval_games else None,
        n_eval=len(pairs),
        n_ties_excluded=len(rows) - len(pairs),
        n_teams=len(teams),
        bins=reliability_bins(pairs, n_bins=n_bins, min_bin=min_bin) if pairs else [],
        brier=brier_score(pairs) if pairs else None,
        fitted_home_baseline=fitted_home_baseline,
        actual_home_rate=actual_home_rate,
        n_fit_sport=len(sport_fit),
        n_fit_neutral=n_fit_neutral,
        fit_game_ids=frozenset(g.id for g in fit_games),
        eval_game_ids=frozenset(g.id for g in eval_games),
    )


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def is_leaked(report: Report) -> bool:
    """True when the model was fit on at least one game it is scored against.

    Derived from the actual fit/eval intersection, never from the ``in_sample``
    flag: ``--eval-on fit`` without ``--in-sample`` is fully leaked while the
    flag is False, and the header is the line that gets pasted downstream.
    """
    return bool(report.fit_game_ids & report.eval_game_ids)


def format_report(report: Report) -> str:
    r = report
    lines: list[str] = []
    if is_leaked(r):
        mode = "LEAKED -- fit and evaluation sets overlap. NOT a publishable number"
    else:
        mode = "out-of-sample"
    lines.append(f"Calibration report -- sport={r.sport} -- {mode}")
    lines.append(f"  split date        : {r.split_date} (fit < split <= evaluate)")
    lines.append(
        f"  fit set           : {r.n_fit} final games "
        f"({r.fit_start} .. {r.fit_end}), all sports"
    )
    lines.append(f"  model fit on      : {r.n_model_training_games} games")
    lines.append(
        f"  evaluation set    : {r.n_eval} {r.sport} games "
        f"({r.eval_start} .. {r.eval_end}), window={r.eval_on}"
    )
    lines.append(f"  ties excluded     : {r.n_ties_excluded}")
    if is_leaked(r):
        lines.append(
            f"  !! WARNING: fit and evaluation sets OVERLAP on "
            f"{len(r.fit_game_ids & r.eval_game_ids)} games -- this is leaked."
        )
    lines.append("")

    if not r.bins:
        lines.append("  No evaluation games. Nothing to report.")
        return "\n".join(lines)

    # Finding 1: an unfitted CalibratedModel does not raise -- it sets
    # trained=False, and predict_home_win_prob then silently returns the fixed
    # _fallback_probability heuristic. The resulting curve describes the
    # heuristic, not the model, so refuse to print it as calibration.
    if not r.model_trained:
        lines.append(
            "  !! REFUSING TO PRINT A RELIABILITY TABLE."
        )
        lines.append(
            f"  !! CalibratedModel did not fit: only {r.n_model_training_games} "
            f"training games in the window (needs {MIN_TRAINING_GAMES})."
        )
        lines.append(
            "  !! Every probability below would come from _fallback_probability,"
        )
        lines.append(
            "  !! a fixed heuristic -- NOT the model. Such a table would describe"
        )
        lines.append(
            "  !! the heuristic's calibration and must not be quoted as the"
        )
        lines.append(
            "  !! model's. Widen the fit window or fix the training data."
        )
        return "\n".join(lines)

    if r.n_eval < MIN_EVAL_GAMES:
        lines.append(
            f"  !! {r.n_eval} evaluation games is below the {MIN_EVAL_GAMES}-game "
            "floor. This curve is too thin to act on."
        )
        lines.append("")

    lines.append("  bin           n    mean_pred   observed        gap  reliable")
    lines.append("  " + "-" * 60)
    for b in r.bins:
        if b.n == 0:
            lines.append(
                f"  {b.lo:.1f}-{b.hi:.1f}     0           -          -          -        -"
            )
            continue
        lines.append(
            f"  {b.lo:.1f}-{b.hi:.1f} {b.n:5d} {b.mean_predicted:11.4f} "
            f"{b.observed_rate:10.4f} {b.gap:+10.4f}  {'yes' if b.reliable else 'NO'}"
        )
    lines.append("")
    lines.append(
        f"  Brier score       : {r.brier:.4f}  (lower is better; 0.25 = always 0.5)"
    )
    if r.fitted_home_baseline is not None:
        lines.append("")
        lines.append(
            f"  Fitted home baseline : {r.fitted_home_baseline:.4f}"
        )
        if r.actual_home_rate is not None:
            lines.append(
                f"  Actual home win rate : {r.actual_home_rate:.4f}  "
                f"(gap {r.fitted_home_baseline - r.actual_home_rate:+.4f})"
            )
            lines.append(
                f"  ...over the {r.n_fit_sport} {r.sport} games in the fit window."
            )
            if r.n_fit_neutral:
                share = r.n_fit_neutral / r.n_fit_sport
                lines.append(
                    f"  NOTE: {r.n_fit_neutral} of those ({share:.0%}) were at "
                    "NEUTRAL sites, where the"
                )
                lines.append(
                    "  home side is a bracket seed and not a host. That share of"
                )
                lines.append(
                    "  the rate above is seed strength, not home advantage, and"
                )
                lines.append(
                    "  the model's per-sport slot does not fire for those games."
                )
        if r.sport not in SPORT_VOCAB:
            lines.append(
                f"  WARNING: {r.sport!r} is absent from SPORT_VOCAB, so it sets"
            )
            lines.append(
                "  no one-hot slot and its baseline cannot differ from any other"
            )
            lines.append(
                "  absent sport's. Note this is NOT the pooled rate: the"
            )
            lines.append(
                "  intercept no longer equals it once the present sports carry"
            )
            lines.append(
                "  their own slots."
            )

    lines.append("")
    lines.append("  CAVEAT: three of the model's features carry no signal.")
    lines.append("  offensive_rating, defensive_rating and pace need possession")
    lines.append("  counts, which no collector in this repo supplies, so every game")
    lines.append("  falls back to the same constant. The score above was produced on")
    lines.append("  point differential, Elo, win-loss splits and rest days alone.")
    lines.append("")
    lines.append("  Sample size")
    lines.append(f"    raw n           : {r.n_eval} games across {r.n_teams} teams")
    for icc in (0.01, 0.05):
        ess = effective_sample_size(r.n_eval, r.n_teams, icc)
        lines.append(f"    effective n     : {ess:7.1f}  assuming intra-team ICC = {icc}")
    lines.append(
        "    Games sharing a team are NOT independent observations; the"
    )
    lines.append(
        "    clusters here are teams."
    )
    lines.append(
        "    Treat the effective n, not the raw count, as the basis for confidence."
    )
    lines.append(
        "    The ICC is an assumption, not a measurement; both ends are shown."
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Out-of-sample calibration report. Read-only; writes no rows."
    )
    parser.add_argument("--sport", required=True)
    parser.add_argument("--db", default="sports_picks.db")
    parser.add_argument("--bins", type=int, default=10)
    parser.add_argument("--min-bin", type=int, default=30)
    parser.add_argument("--train-frac", type=float, default=0.7)
    parser.add_argument(
        "--in-sample",
        action="store_true",
        help="Leakage control: fit on everything, including the evaluation games.",
    )
    parser.add_argument(
        "--eval-on",
        choices=("eval", "fit"),
        default="eval",
        help="Score the held-out window (default) or the training window.",
    )
    args = parser.parse_args(argv)

    from backend.database import get_engine, get_session

    engine = get_engine(args.db)
    session = get_session(engine)
    try:
        split = choose_split_date(session, args.sport, train_frac=args.train_frac)
        report = evaluate(
            session,
            args.sport,
            n_bins=args.bins,
            min_bin=args.min_bin,
            split_date=split,
            in_sample=args.in_sample,
            eval_on=args.eval_on,
        )
        print(format_report(report))
    finally:
        session.rollback()  # belt and braces: this tool writes nothing
        session.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Per-sport measure of whether the calibrated model beats its nulls.

Read-only. This module measures; it never tunes and it writes no rows.

Why it exists
-------------
``backend.analysis.calibration_report`` answers "how well-calibrated is
this sport's reliability curve", one sport per invocation, and refuses below
``MIN_EVAL_GAMES = 200``. It cannot answer the coarser, cross-sport question
this report exists for: does each sport's model beat two trivial nulls
(always the sport's own base rate, always 0.5), at once, for sports whose
evaluation sample never reaches 200 games. This report answers that instead,
reusing ``calibration_report``'s statistics core rather than reimplementing
it -- ``effective_sample_size`` and the ``do_orm_execute`` /
``with_loader_criteria`` fit-restriction approach both come from there.

What this tool produced when it was written (plan 021, 2026-09-23)
--------------------------------------------------------------------
A 70/30 time split taken WITHIN each sport, per-sport home-advantage one-hot
left in place, out-of-sample Brier (lower is better)::

    sport   eval n   effective n   pooled (today)   standardized   per-sport slopes
    mlb     45       29.8          0.2494           0.2784         too few to fit
    nba     375      291.9         0.1692           0.1704         0.1736
    ncaaf   79       40.8          0.1729           0.1635         0.1772
    nfl     352      302.2         0.2305           0.2313         0.2341

Per-sport slopes were worse in every sport that could support them.
Standardization was worse in three of four sports and better only in
college football, on an effective n of 40.8. The pooled fit
(``CalibratedModel``, unmodified) stayed; see the comment on
``CalibratedModel`` for the full account of that refusal.

Against the two trivial nulls, the same split::

    sport   effective n   model     always the base rate   always 0.5
    mlb     29.8          0.2494    0.2532                 0.2500
    nba     291.9         0.1692    0.2449                 0.2500
    ncaaf   40.8          0.1729    0.1894                 0.2500
    nfl     302.2         0.2305    0.2477                 0.2500

Basketball is the only sport where the model clearly beats both nulls.
Baseball's model was indistinguishable from a coin flip (0.2494 vs 0.2500)
on an effective sample of 30 -- fitted on features that did not include the
starting pitcher, which shipped the same day and could not be evaluated at
all because nothing persisted the scores it used (see
``backend.pipeline.scheduler._persist_pitcher_scores``, added by this same
plan). Re-run this report to see whether that changes the mlb row.

Beating the base rate is a low bar and is NOT the same as beating the
market price -- that is a different, harder question this report does not
answer.
"""
from __future__ import annotations

import argparse
import contextlib
from dataclasses import dataclass
from datetime import date

from sqlalchemy import and_, event, or_
from sqlalchemy.orm import Session, with_loader_criteria

from backend.analysis.calibrated_model import (
    NON_COMPETITIVE_PHASES,
    CalibratedModel,
)
from backend.analysis.calibration_report import (
    binary_pairs,
    brier_score,
    effective_sample_size,
)
from backend.analysis.variants.ensemble import EnsembleStrategy
from backend.models import Game

#: Below this many evaluation games a sport's row reports the refusal
#: instead of a Brier number -- too thin a sample to say anything.
DEFAULT_MIN_EVAL = 40

#: Assumption, not a measurement, quoted alongside every effective-n figure.
ICC = 0.05

#: Always-0.5 null. Not a measurement, just the Brier of a coin flip.
COIN_FLIP_BRIER = 0.25


@dataclass(frozen=True)
class SportSignal:
    sport: str
    train_split: date | None
    n_eval: int
    n_teams: int
    below_min_eval: bool
    model_brier: float | None
    base_rate_brier: float | None
    base_rate: float | None
    n_train: int
    beats_base_rate: bool | None


def _final_games(session: Session) -> list[Game]:
    return (
        session.query(Game)
        .filter(
            and_(
                Game.status == "final",
                Game.home_score.isnot(None),
                Game.away_score.isnot(None),
                Game.season_type.notin_(NON_COMPETITIVE_PHASES),
            )
        )
        .order_by(Game.date, Game.id)
        .all()
    )


def per_sport_split_dates(
    session: Session, train_frac: float = 0.7
) -> dict[str, date]:
    """The date that puts roughly ``train_frac`` of EACH sport's games behind it.

    One cutoff per sport, not one global cutoff -- a global split would put
    a whole sport on one side of the line, which is exactly what this report
    exists to avoid (a sport's train and eval windows must both exist).
    """
    by_sport: dict[str, list[Game]] = {}
    for g in _final_games(session):
        by_sport.setdefault(g.sport, []).append(g)

    splits: dict[str, date] = {}
    for sport, games in by_sport.items():
        target = max(1, int(round(len(games) * train_frac)))
        splits[sport] = games[min(target, len(games) - 1)].date
    return splits


@contextlib.contextmanager
def _games_before_per_sport(session: Session, splits: dict[str, date]):
    """Restrict every ``Game`` select on this session to each sport's train window.

    Sibling of ``calibration_report._games_before``, which takes one global
    ``split_date``. That single cutoff is wrong here: it would put whole
    sports entirely on one side of the line. This applies a compound
    criterion instead -- ``sport == s AND date < splits[s]`` for every sport
    in ``splits``, OR'd together. A sport absent from ``splits`` contributes
    NO games to the fit: a sport with too few games to split must not leak
    its whole history into training.
    """
    if not splits:
        yield
        return

    criterion = or_(*[
        and_(Game.sport == sport, Game.date < cutoff)
        for sport, cutoff in splits.items()
    ])

    def _limit(execute_state):
        if execute_state.is_select:
            execute_state.statement = execute_state.statement.options(
                with_loader_criteria(Game, criterion, include_aliases=True)
            )

    event.listen(session, "do_orm_execute", _limit)
    try:
        yield
    finally:
        event.remove(session, "do_orm_execute", _limit)


def _fit_model(session: Session, splits: dict[str, date]) -> CalibratedModel:
    model = CalibratedModel()
    with _games_before_per_sport(session, splits):
        model.train_from_db(session)
    return model


def _predict_rows(session: Session, games: list[Game]) -> list[tuple[float, int, int]]:
    """Replay the live probability path over ``games``. See
    ``calibration_report._predict_rows`` for why ``_lgbm_model`` is left
    unset -- the same reasoning applies here.
    """
    from backend.pipeline.pick_generator import _build_game_data

    rows: list[tuple[float, int, int]] = []
    for game in games:
        strategy = EnsembleStrategy("ensemble", {
            "min_edge": 3, "k_factor": 20, "lookback": 10,
            "weights": {"pd": 0.3, "elo": 0.35, "rating": 0.25, "hca": 0.1},
        })
        game_data = _build_game_data(session, game)
        prob = strategy._calibrated_probability(game_data)
        rows.append((prob, game.home_score, game.away_score))
    return rows


def run_report(
    session: Session,
    train_frac: float = 0.7,
    min_eval: int = DEFAULT_MIN_EVAL,
    sport: str | None = None,
) -> list[SportSignal]:
    """Measure, per sport, whether the model beats its nulls out-of-sample.

    Writes no rows. ``CalibratedModel`` is fit once, on every sport's train
    half at once (matching how the production model is fit across all
    sports), then scored per sport against that sport's eval half.
    """
    all_final = _final_games(session)
    splits = per_sport_split_dates(session, train_frac=train_frac)

    model = _fit_model(session, splits)

    previous = EnsembleStrategy._calibrated
    EnsembleStrategy._calibrated = model
    try:
        by_sport: dict[str, list[Game]] = {}
        for g in all_final:
            by_sport.setdefault(g.sport, []).append(g)

        sports = [sport] if sport else sorted(by_sport)
        results: list[SportSignal] = []
        for s in sports:
            games = by_sport.get(s, [])
            split = splits.get(s)
            if split is None:
                results.append(SportSignal(
                    sport=s, train_split=None, n_eval=0, n_teams=0,
                    below_min_eval=True, model_brier=None,
                    base_rate_brier=None, base_rate=None, n_train=0,
                    beats_base_rate=None,
                ))
                continue

            train_games = [g for g in games if g.date < split]
            eval_games = [g for g in games if g.date >= split]

            rows = _predict_rows(session, eval_games)
            pairs = binary_pairs(rows)
            n_eval = len(pairs)

            if n_eval < min_eval:
                results.append(SportSignal(
                    sport=s, train_split=split, n_eval=n_eval, n_teams=0,
                    below_min_eval=True, model_brier=None,
                    base_rate_brier=None, base_rate=None,
                    n_train=len(train_games), beats_base_rate=None,
                ))
                continue

            teams = {g.home_team_id for g in eval_games} | {g.away_team_id for g in eval_games}
            train_pairs = binary_pairs(
                [(0.5, g.home_score, g.away_score) for g in train_games]
            )
            base_rate = (
                sum(o for _, o in train_pairs) / len(train_pairs)
                if train_pairs else None
            )
            base_rate_pairs = (
                [(base_rate, o) for _, o in pairs] if base_rate is not None else None
            )

            model_brier = brier_score(pairs)
            base_rate_brier = brier_score(base_rate_pairs) if base_rate_pairs else None

            results.append(SportSignal(
                sport=s,
                train_split=split,
                n_eval=n_eval,
                n_teams=len(teams),
                below_min_eval=False,
                model_brier=model_brier,
                base_rate_brier=base_rate_brier,
                base_rate=base_rate,
                n_train=len(train_games),
                beats_base_rate=(
                    model_brier < base_rate_brier
                    if base_rate_brier is not None else None
                ),
            ))
        return results
    finally:
        EnsembleStrategy._calibrated = previous


def format_report(results: list[SportSignal], min_eval: int) -> str:
    lines: list[str] = []
    lines.append("Per-sport signal report -- does the model beat its nulls?")
    lines.append(
        f"Split within each sport, {min_eval}-game evaluation floor per sport."
    )
    lines.append(
        "Read the EFFECTIVE n, not the raw count -- games sharing a team are"
    )
    lines.append(
        f"not independent observations. ICC = {ICC} (an assumption, not a"
        " measurement)."
    )
    lines.append("")
    lines.append(
        "  sport      eval n   effective n     model    base rate   0.5    beats base rate"
    )
    lines.append("  " + "-" * 88)
    for r in results:
        if r.below_min_eval:
            reason = "not enough evaluation games" if r.train_split else "no train/eval split (too few games)"
            lines.append(f"  {r.sport:<9}  {r.n_eval:6d}   {reason}")
            continue
        ess = effective_sample_size(r.n_eval, r.n_teams, ICC)
        beats = "yes" if r.beats_base_rate else "no"
        lines.append(
            f"  {r.sport:<9}  {r.n_eval:6d}   {ess:11.1f}   {r.model_brier:7.4f}"
            f"   {r.base_rate_brier:9.4f}   {COIN_FLIP_BRIER:.4f}   {beats}"
        )
    lines.append("")
    lines.append(
        "NOTE: 'base rate' is the TRAIN half's home-win rate for that sport,"
    )
    lines.append(
        "not the eval half's -- scoring against the eval half's own rate"
    )
    lines.append("would leak the answer into the null it is compared against.")
    lines.append("")
    lines.append(
        "Beating the base rate is a low bar and is NOT the same as beating"
    )
    lines.append(
        "the market price. This report answers only whether a sport's model"
    )
    lines.append("carries any signal at all, not whether it is profitable.")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Per-sport signal report. Read-only; writes no rows."
    )
    parser.add_argument("--db", default="sports_picks.db")
    parser.add_argument("--sport", default=None)
    parser.add_argument("--train-frac", type=float, default=0.7)
    parser.add_argument("--min-eval", type=int, default=DEFAULT_MIN_EVAL)
    args = parser.parse_args(argv)

    from backend.database import get_engine, get_session

    engine = get_engine(args.db)
    session = get_session(engine)
    try:
        results = run_report(
            session,
            train_frac=args.train_frac,
            min_eval=args.min_eval,
            sport=args.sport,
        )
        print(format_report(results, args.min_eval))
    finally:
        session.rollback()  # belt and braces: this tool writes nothing
        session.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

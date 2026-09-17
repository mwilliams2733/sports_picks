"""Point-in-time team statistics -- the first real producer for ``team_stats``.

Why this module exists
----------------------
``TeamStat`` was defined, read in seven places, and written by nothing outside
the tests.  ``CalibratedModel`` therefore trained on 1058 games where four of
five features were identically zero, fitted coefficients from effectively one
row, and was then served real values at prediction time.  This module produces
the rows the model was always supposed to train on.

The one invariant
-----------------
**Every value attached to game G is computed only from games strictly before
G.**  A rolling point differential that includes G's own result, or an Elo
rating that reflects G's outcome, turns the training set into a lookahead
oracle and makes every downstream calibration number meaningless.

The boundary is *strictly before by date*.  A game on the same calendar date as
the target is excluded, even if it is a different game.  ``Game.start_time`` is
nullable across most of the history, so there is no reliable intra-day
ordering; including a same-day game would risk folding a later game into an
earlier one's features.  Excluding it can only ever make a feature staler,
never leaked, and staleness is the safe direction.

What is deliberately NOT computed
---------------------------------
``offensive_rating``, ``defensive_rating`` and ``pace`` all require
possessions.  Nothing in this repository fetches possessions -- ESPN's
collector exposes only ``fetch_scoreboard`` (teams, scores, status).  They are
therefore **not emitted at all**: no value, no default, no proxy.  A fabricated
pace number would be worse than a missing one, because the model would fit a
coefficient to noise and no downstream reader could tell.  The consumers'
existing ``or 100.0`` defaults continue to apply, which is honest -- those two
features remain constant and the model can only learn a zero coefficient for
them.  See "The decision this plan feeds" in plan 008.

Elo: pre-game, not post-game
----------------------------
``backend/backtesting/historical.py`` writes the **post-game** rating into
``EloHistory``.  Both consumers (``calibrated_model.train_from_db`` and
``ensemble.train_lgbm_from_db``) look that row up as
``elo_history[(team_id, game_id)]`` and use it as the *feature for* that game.
Post-game therefore leaks the outcome being predicted.  This module writes the
**pre-game** rating -- the rating each team carried into the game -- so the
existing consumers become correct without being modified.
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Iterable, Sequence

from sqlalchemy.orm import Session

from backend.analysis.elo import EloSystem
from backend.models import EloHistory, Game, Team, TeamStat

logger = logging.getLogger(__name__)

#: Rolling window, in games, for ``point_diff`` and the ``last_n`` record.
#: Matches the ensemble strategy's ``lookback`` default of 10.
DEFAULT_LOOKBACK = 10

#: Rest days assumed for a team's first game in the available history.  Three
#: days is roughly the league-wide average gap and, unlike 0, does not fire
#: ``back_to_back_*``.  It is an assumption, not a measurement, and it applies
#: only to a team's very first row.
DEFAULT_REST_DAYS = 3

#: Exactly the stat types this module produces.  Anything not in here is not
#: derivable from ``games`` alone and must not be invented -- see the module
#: docstring.
COMPUTED_STAT_TYPES = (
    "point_diff",
    "rest_days",
    "home_wins",
    "home_losses",
    "away_wins",
    "away_losses",
    "last_n_wins",
    "last_n_losses",
)

#: Elo replay parameters.  This module owns the only team-sport replay;
#: ``backtesting.historical.compute_historical_elo`` delegates to it rather
#: than keeping its own copy, so the two paths cannot disagree on what a
#: rating means.
ELO_K_FACTOR = 20

#: Combat sports keep their own Elo history, written post-game by
#: ``grader._apply_combat_elo_update``.  Replaying them here would double-count
#: and would silently change the semantics of rows another module owns.
COMBAT_SPORTS = ("mma", "boxing")


# --------------------------------------------------------------------------
# Pure functions over already-loaded rows (no DB access)
# --------------------------------------------------------------------------

def strictly_before(games: Iterable, before_date: date) -> list:
    """Games whose date is **strictly** earlier than ``before_date``.

    This single comparison is the whole point-in-time guarantee.  ``<=`` here
    would silently include the game being predicted; ``backend/tests/
    test_team_stats_pipeline.py::test_compute_ignores_a_game_on_the_same_date``
    exists to fail if anyone changes it.
    """
    return sorted(
        (g for g in games if g.date < before_date),
        key=lambda g: (g.date, g.id),
    )


def _points_for_against(game, team_id: int) -> tuple[int, int] | None:
    """``(points_for, points_against)`` for ``team_id``, or None if not playing
    or the game has no score."""
    if game.home_score is None or game.away_score is None:
        return None
    if game.home_team_id == team_id:
        return game.home_score, game.away_score
    if game.away_team_id == team_id:
        return game.away_score, game.home_score
    return None


def _team_games(prior_games: Iterable, team_id: int) -> list:
    """The subset of ``prior_games`` this team played, oldest first."""
    out = [g for g in prior_games if _points_for_against(g, team_id) is not None]
    out.sort(key=lambda g: (g.date, g.id))
    return out


def rolling_point_diff(prior_games: Sequence, team_id: int,
                       lookback: int = DEFAULT_LOOKBACK) -> float:
    """Mean scoring margin over the team's most recent ``lookback`` prior games.

    ``prior_games`` must already be restricted to games strictly before the
    target game -- use :func:`strictly_before`.  Returns ``0.0`` when the team
    has no prior games, which is the same value the consumers default to, so a
    debut game is not distinguishable from a missing row (it genuinely is not).
    """
    played = _team_games(prior_games, team_id)[-lookback:]
    if not played:
        return 0.0
    margins = []
    for g in played:
        pf, pa = _points_for_against(g, team_id)
        margins.append(pf - pa)
    return sum(margins) / len(margins)


def rest_days(prior_games: Sequence, team_id: int, game_date: date,
              default: int = DEFAULT_REST_DAYS) -> int:
    """Whole days between ``game_date`` and the team's most recent prior game.

    Returns ``default`` (:data:`DEFAULT_REST_DAYS`) when the team has no prior
    game in the available history.  ``prior_games`` must already be restricted
    to games strictly before the target -- use :func:`strictly_before`.
    """
    played = _team_games(prior_games, team_id)
    if not played:
        return default
    return (game_date - played[-1].date).days


def record_splits(prior_games: Sequence, team_id: int,
                  last_n: int = DEFAULT_LOOKBACK) -> dict[str, int]:
    """Home/away/last-N win-loss counts over the team's prior games.

    Key names match the ``stat_type`` vocabulary already read by
    ``pick_generator._get_team_stats``.  Ties count as neither a win nor a loss.
    """
    played = _team_games(prior_games, team_id)
    out = {
        "home_wins": 0, "home_losses": 0,
        "away_wins": 0, "away_losses": 0,
        "last_n_wins": 0, "last_n_losses": 0,
    }
    for g in played:
        pf, pa = _points_for_against(g, team_id)
        if pf == pa:
            continue
        at_home = g.home_team_id == team_id
        won = pf > pa
        if at_home:
            out["home_wins" if won else "home_losses"] += 1
        else:
            out["away_wins" if won else "away_losses"] += 1
    for g in played[-last_n:]:
        pf, pa = _points_for_against(g, team_id)
        if pf == pa:
            continue
        out["last_n_wins" if pf > pa else "last_n_losses"] += 1
    return out


def compute_team_stats(games: Iterable, team_id: int, before_date: date,
                       lookback: int = DEFAULT_LOOKBACK) -> dict[str, float]:
    """The full point-in-time stat dict for one team going into ``before_date``.

    ``games`` may be any collection -- this function applies the
    strictly-before filter itself, so a caller cannot forget to.  The returned
    keys are exactly :data:`COMPUTED_STAT_TYPES`; unmeasurable features
    (ratings, pace) are deliberately absent rather than defaulted.
    """
    prior = strictly_before(games, before_date)
    stats: dict[str, float] = {
        "point_diff": float(rolling_point_diff(prior, team_id, lookback=lookback)),
        "rest_days": float(rest_days(prior, team_id, before_date)),
    }
    stats.update({k: float(v) for k, v in
                  record_splits(prior, team_id, last_n=lookback).items()})
    return stats


# --------------------------------------------------------------------------
# Persistence
# --------------------------------------------------------------------------

def _upsert_stats(session: Session, game_id: int, team_id: int,
                  stats: dict[str, float]) -> int:
    """Write ``stats`` for one (game, team), updating rows that already exist.

    ``team_stats`` has no unique constraint, so this follows the same
    read-then-update-or-insert shape as ``full_pipeline._store_odds``.
    Returns the number of stat types written.
    """
    existing = {
        row.stat_type: row
        for row in session.query(TeamStat).filter(
            TeamStat.game_id == game_id, TeamStat.team_id == team_id
        ).all()
    }
    for stat_type, value in stats.items():
        row = existing.get(stat_type)
        if row is not None:
            row.value = value
        else:
            session.add(TeamStat(team_id=team_id, game_id=game_id,
                                 stat_type=stat_type, value=value))
    return len(stats)


def store_team_stats_for_game(session: Session, game, prior_games: Iterable) -> int:
    """Write point-in-time stats for both teams of ``game``.

    ``prior_games`` is the candidate pool; the strictly-before filter is
    applied internally against ``game.date``, so passing a superset (for
    example every final game of the sport) is safe and is what the backfill
    does.  Idempotent: re-running for the same game updates rather than
    duplicating.  Returns the number of stat rows written or updated.
    """
    written = 0
    for team_id in (game.home_team_id, game.away_team_id):
        stats = compute_team_stats(prior_games, team_id, game.date)
        written += _upsert_stats(session, game.id, team_id, stats)
    return written


def _game_has_stats(session: Session, game_id: int) -> bool:
    """Whether this specific game already has computed stat rows.

    Asked per game rather than tracked as a high-water mark, so a gap in the
    middle of the history is filled on the next run.  Restricted to
    :data:`COMPUTED_STAT_TYPES` so the handful of pre-existing rows of other
    stat types (game 1014's ratings/pace, of unknown provenance) do not make a
    game look done.
    """
    return session.query(TeamStat.id).filter(
        TeamStat.game_id == game_id,
        TeamStat.stat_type.in_(COMPUTED_STAT_TYPES),
    ).first() is not None


def _final_games(session: Session, sport: str) -> list[Game]:
    """This sport's scored final games, oldest first, with a stable tiebreak."""
    return (
        session.query(Game)
        .filter(Game.sport == sport, Game.status == "final",
                Game.home_score.isnot(None), Game.away_score.isnot(None))
        .order_by(Game.date, Game.id)
        .all()
    )


def backfill_team_stats(session: Session, sport: str, *, dry_run: bool = False,
                        force: bool = False,
                        lookback: int = DEFAULT_LOOKBACK) -> dict[str, int]:
    """Compute and store point-in-time stats for every final game of ``sport``.

    Games are walked in chronological order so each one sees only its
    predecessors.  Resumable and gap-tolerant: each game is asked individually
    whether it already has rows.  Does not commit -- the caller decides.

    ``force`` recomputes even for games that already have rows.  Use it once
    when the existing rows predate this module: the handful of legacy rows in
    production are of unknown provenance and may not be point-in-time, and the
    upsert makes overwriting them safe.
    """
    games = _final_games(session, sport)
    processed = skipped = rows = 0
    for i, game in enumerate(games):
        if not force and _game_has_stats(session, game.id):
            skipped += 1
            continue
        processed += 1
        if dry_run:
            continue
        # games[:i] is every game of this sport strictly earlier in the
        # ordering; store_team_stats_for_game re-applies the date filter, so
        # same-date games sitting in this slice are still excluded.
        rows += store_team_stats_for_game(session, game, games[:i])
    return {"sport": sport, "games_total": len(games),
            "games_processed": processed, "games_skipped": skipped,
            "rows_written": rows}


def backfill_elo_history(session: Session, sport: str, *,
                         dry_run: bool = False) -> dict[str, object]:
    """Replay ``sport`` chronologically writing the **pre-game** Elo rating.

    The row stored against game G is the rating each team carried *into* G, so
    ``elo_history[(team_id, game_id)]`` -- how both consumers read it -- is a
    legitimate feature for predicting G.  Storing the *post*-game rating there
    instead -- as ``backtesting.historical.compute_historical_elo`` did until
    it was made to delegate here -- is lookahead when read that way.

    ``EloRating`` (the current-rating table) is intentionally left alone: this
    function's job is the history, and rewriting live ratings would change the
    serving path at the same time as the training data.  The post-replay
    ratings are *returned* as ``final_ratings`` so a caller whose job is that
    table can persist them deliberately.

    Raises ``ValueError`` for combat sports, whose history is owned by the
    grader and uses post-game semantics.  Does not commit.
    """
    if sport in COMBAT_SPORTS:
        raise ValueError(
            f"{sport!r} Elo history is written post-game by "
            "backend.pipeline.grader._apply_combat_elo_update; refusing to "
            "replay it here with pre-game semantics."
        )

    from backend.analysis.sport_constants import get_home_advantage_elo

    games = _final_games(session, sport)
    already = {
        gid for (gid,) in session.query(EloHistory.game_id)
        .filter(EloHistory.sport == sport).distinct()
    }
    elo = EloSystem(k_factor=ELO_K_FACTOR,
                    home_advantage=get_home_advantage_elo(sport))

    written = skipped = 0
    for game in games:
        home_team = session.get(Team, game.home_team_id)
        away_team = session.get(Team, game.away_team_id)
        if not home_team or not away_team:
            continue

        # PRE-game ratings: read before the update is applied.
        home_rating_before = elo.get_rating(home_team.abbreviation)
        away_rating_before = elo.get_rating(away_team.abbreviation)

        if game.id in already:
            skipped += 1
        elif not dry_run:
            session.add_all([
                EloHistory(team_id=game.home_team_id, game_id=game.id,
                           sport=sport, rating=home_rating_before),
                EloHistory(team_id=game.away_team_id, game_id=game.id,
                           sport=sport, rating=away_rating_before),
            ])
            written += 2
        else:
            written += 2

        # Now -- and only now -- fold in this game's result, for the NEXT game.
        margin = game.home_score - game.away_score
        if margin == 0:
            continue
        winner = home_team.abbreviation if margin > 0 else away_team.abbreviation
        elo.update(home_team.abbreviation, away_team.abbreviation, winner,
                   margin=abs(margin))

    return {"sport": sport, "games_total": len(games),
            "rows_written": written, "games_skipped": skipped,
            # Post-replay rating per team abbreviation.  This is the only
            # legitimate source of a *current* rating, and it is deliberately
            # returned rather than written: callers that maintain ``EloRating``
            # (``backtesting.historical.compute_historical_elo``) persist it,
            # while the daily pipeline ignores it.  Skipped games still fold
            # into the replay, so this is correct on a re-run.
            "final_ratings": dict(elo.ratings)}


# --------------------------------------------------------------------------
# Daily pipeline entry point
# --------------------------------------------------------------------------

def update_team_stats_for_games(session: Session, games: Sequence) -> int:
    """Recompute point-in-time stats for the given games, one sport at a time.

    Called from the daily pipeline after scores land.  Each game's prior pool
    is that sport's final games; the strictly-before filter inside
    :func:`store_team_stats_for_game` guarantees the game's own result cannot
    enter its own features, even though the game is itself in the pool by the
    time this runs.  Does not commit.
    """
    written = 0
    pools: dict[str, list[Game]] = {}
    for game in games:
        if game.sport not in pools:
            pools[game.sport] = _final_games(session, game.sport)
        written += store_team_stats_for_game(session, game, pools[game.sport])
    return written

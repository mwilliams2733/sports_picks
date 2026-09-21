"""Void bouts that no source will ever be able to settle.

Why they exist
--------------
110 boxing bouts from 2026-03-20 to 2026-07-05 sit `scheduled`, carrying
36 picks that can never be graded. The cause is not a missing collector:

* **ESPN has no boxing at all.** `sports/boxing` returns 404 and the API
  answers that the sport and league are invalid.
* **The odds feed reaches back 3 days.** `/scores` caps `daysFrom` at 3
  and returns HTTP 422 beyond it, so `finalize_combat` physically cannot
  see a bout from March.
* The available third-party archives are US-only or need an account; most
  of these bouts are UK, Irish and Maltese small-hall cards.

These picks are not pending. They are unanswerable, and leaving them
pending misreports the book as holding open positions it will never
settle.

Why `push` and not a new word
-----------------------------
`push` is already understood by everything that reads a result. The
recalibrator computes ``decided = total - pushes``, so a push never
enters a win-rate denominator, and `grade_result` already books it at a
0.0 payout -- stake returned, ROI untouched. A new value like "void"
would fall through every ``win``/``loss``/``push`` branch in
`api/stats.py`, `api/users.py`, `analysis/recalibrator.py` and
`analysis/prop_calibration.py` and be silently miscounted.

The game itself becomes `canceled`, the spelling `collectors/espn.py`
already defines, and which `api/games.py` already excludes from Today's
Picks.

Two refusals
------------
* **A bout the odds feed could still settle is never voided.** The age
  floor is derived from `MAX_DAYS_FROM`, so it cannot drift out of step
  with what `finalize_combat` can reach.
* **A game with an already-graded pick, or with scores, is refused.**
  Voiding either would rewrite something that was actually recorded.

Like `dedupe_picks`, a dry run is the DEFAULT and `--apply` is required.
This settles real positions.

    python -m backend.scripts.void_stuck_bouts --db <abs path>
    python -m backend.scripts.void_stuck_bouts --db <abs path> --apply
"""
import argparse
import logging
import os
from datetime import date, timedelta

from backend.collectors.espn import CANCELED
from backend.collectors.odds_scores import MAX_DAYS_FROM
from backend.database import get_engine, get_session, run_migrations
from backend.models import Game, PickModel, PickResult
from backend.time_utils import et_today

logger = logging.getLogger(__name__)

#: Settled as a push: stake returned, excluded from the win-rate
#: denominator. See the module docstring.
VOID_RESULT = "push"
VOID_PAYOUT = 0.0


def settle_as_void(session, game, picks) -> int:
    """Cancel ``game`` and settle its picks as a push. Returns picks settled.

    The single definition of what voiding *means*, so every caller agrees:
    `void_stuck_bouts` (too old for any source) and
    `dedupe_combat_games.void_reschedules` (the fight moved and never
    happened here) settle a position identically or the book stops being
    comparable with itself.
    """
    game.status = CANCELED
    for pick in picks:
        session.add(PickResult(pick_id=pick.id, result=VOID_RESULT,
                               payout=VOID_PAYOUT))
    return len(picks)


def voidable(session, sport: str, today: date,
             *, min_age_days: int = MAX_DAYS_FROM) -> list[Game]:
    """Scheduled bouts too old for any source to settle.

    ``min_age_days`` is derived from the odds feed's reach rather than
    written as a literal: a bout inside that window is recoverable, and
    voiding it would throw away a result `finalize_combat` is about to
    fetch. If `/scores` ever reaches further back, this follows it.
    """
    return (session.query(Game)
            .filter(Game.sport == sport, Game.status == "scheduled",
                    Game.date < today - timedelta(days=min_age_days))
            .order_by(Game.date.asc()).all())


def stranded_picks(session, sports: tuple[str, ...] | None = None
                   ) -> list[tuple[Game, list[PickModel]]]:
    """Canceled games still carrying ungraded picks.

    :func:`voidable` only looks at ``scheduled`` rows, because it answers
    "too old for any source to settle". A game that is ALREADY canceled is
    past that question and matches nothing, so its picks stay pending
    forever while the book reports them as open positions.

    Games carrying a score are excluded here rather than refused, because
    a score means there is a result to grade; :func:`settle_stranded`
    reports them so the count is visible.
    """
    q = session.query(Game).filter(Game.status == CANCELED)
    if sports:
        q = q.filter(Game.sport.in_(list(sports)))

    graded_ids = {r.pick_id for r in session.query(PickResult.pick_id)}
    out = []
    for game in q.order_by(Game.date.asc(), Game.id.asc()).all():
        if game.home_score is not None or game.away_score is not None:
            continue
        pending = [p for p in session.query(PickModel)
                   .filter(PickModel.game_id == game.id).all()
                   if p.id not in graded_ids]
        if pending:
            out.append((game, pending))
    return out


def settle_stranded(session, *, sports: tuple[str, ...] | None = None,
                    apply: bool = False) -> dict:
    """Book the picks left pending on already-canceled games as pushes.

    **Run `restore_miscanceled_games` first.** Of the 21 stranded picks on
    2026-09-20, 18 were on games that had actually been played and were
    wrongly canceled by `_reconcile_against_espn`. Settling before
    restoring would book a push for games with real winners.
    """
    summary = {"voided": 0, "picks_settled": 0, "refused_scored": 0,
               "game_ids": []}

    scored_q = session.query(Game).filter(Game.status == CANCELED)
    if sports:
        scored_q = scored_q.filter(Game.sport.in_(list(sports)))
    for game in scored_q.all():
        if game.home_score is None and game.away_score is None:
            continue
        if any(True for _ in session.query(PickModel.id)
               .filter(PickModel.game_id == game.id)):
            summary["refused_scored"] += 1

    for game, picks in stranded_picks(session, sports):
        summary["voided"] += 1
        summary["picks_settled"] += len(picks)
        summary["game_ids"].append(game.id)
        if apply:
            settle_as_void(session, game, picks)

    if apply and summary["voided"]:
        session.commit()
    return summary


def run_on_session(session, *, sport: str = "boxing", today: date | None = None,
                   apply: bool = False) -> dict:
    """Report, and with ``apply``, void unanswerable bouts. Returns a summary.

    A dry run still reports what it *would* do, so the counts can be read
    before anything is written.
    """
    today = today or et_today()
    summary = {"sport": sport, "voided": 0, "picks_settled": 0,
               "refused_graded": 0, "refused_scored": 0, "refused_ids": []}

    candidates = voidable(session, sport, today)
    if not candidates:
        return summary

    graded_ids = {r.pick_id for r in session.query(PickResult.pick_id)}

    for game in candidates:
        # A game with a score is finalizable, not unanswerable: grading it
        # is the right answer and voiding it would discard a real result.
        if game.home_score is not None or game.away_score is not None:
            summary["refused_scored"] += 1
            summary["refused_ids"].append(game.id)
            continue

        picks = session.query(PickModel).filter(
            PickModel.game_id == game.id).all()
        if any(p.id in graded_ids for p in picks):
            summary["refused_graded"] += 1
            summary["refused_ids"].append(game.id)
            continue

        summary["voided"] += 1
        summary["picks_settled"] += len(picks)
        if apply:
            settle_as_void(session, game, picks)

    if apply and summary["voided"]:
        session.commit()
    return summary


def run(db_path: str, *, sport: str = "boxing", apply: bool = False,
        today: date | None = None) -> dict:
    """CLI entry point.

    Raises ``FileNotFoundError`` if ``db_path`` does not exist: otherwise the
    engine would create an empty database at a typo'd path and report a
    cheerful zero-row success against it.
    """
    if db_path != ":memory:" and not os.path.exists(db_path):
        raise FileNotFoundError(
            f"--db {db_path!r} does not exist. Refusing to create a new, empty "
            f"database: a zero-row 'successful' run would hide the typo.")

    engine = get_engine(db_path)
    run_migrations(engine)
    session = get_session(engine)
    try:
        return run_on_session(session, sport=sport, today=today, apply=apply)
    finally:
        session.close()


def format_summary(s: dict, apply: bool) -> str:
    lines = ["Voided unanswerable bouts" if apply
             else "DRY RUN -- nothing written", ""]
    lines.append(f"  sport            : {s['sport']}")
    lines.append(f"  {'voided' if apply else 'would void':<16} : {s['voided']}")
    lines.append(f"  picks settled    : {s['picks_settled']} "
                 f"(as {VOID_RESULT}, payout {VOID_PAYOUT})")
    if s["refused_scored"]:
        lines.append(f"  REFUSED (scored) : {s['refused_scored']} -- these have "
                     "a score and should be graded, not voided")
    if s["refused_graded"]:
        lines.append(f"  REFUSED (graded) : {s['refused_graded']} -- voiding "
                     "would rewrite a recorded result")
    if s["refused_ids"]:
        lines.append(f"  refused game ids : {s['refused_ids'][:20]}"
                     + (" ..." if len(s["refused_ids"]) > 20 else ""))
    lines.append("")
    lines.append(f"  Only bouts older than {MAX_DAYS_FROM} days are eligible: a")
    lines.append("  newer one is still reachable by finalize_combat.")
    lines.append("  A push is excluded from the win-rate denominator and")
    lines.append("  returns the stake, so ROI and calibration are untouched.")
    return "\n".join(lines)


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO)
    ap = argparse.ArgumentParser(
        description="Void bouts no source can settle. Dry run by default.")
    ap.add_argument("--db", required=True, help="Path to the database. Back it up first.")
    ap.add_argument("--sport", default="boxing")
    ap.add_argument("--stranded", action="store_true",
                    help="Settle picks left pending on ALREADY-canceled games "
                         "instead. Run restore_miscanceled_games first: a "
                         "wrongly-canceled game has a real winner to grade.")
    ap.add_argument("--apply", action="store_true",
                    help="Actually write. Without this it is a dry run.")
    args = ap.parse_args(argv)
    if args.stranded:
        engine = get_engine(args.db)
        run_migrations(engine)
        session = get_session(engine)
        try:
            s = settle_stranded(session, apply=args.apply)
        finally:
            session.close()
        print("Settled stranded picks" if args.apply else "DRY RUN -- nothing written")
        print(f"  canceled games with pending picks : {s['voided']}")
        print(f"  picks settled                     : {s['picks_settled']} "
              f"(as {VOID_RESULT}, payout {VOID_PAYOUT})")
        print(f"  refused (canceled WITH a score)   : {s['refused_scored']}")
        print(f"  game ids                          : {s['game_ids'][:25]}")
        return 0
    print(format_summary(
        run(args.db, sport=args.sport, apply=args.apply), args.apply))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

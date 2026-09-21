"""Remove a combat bout that is stored twice, and replay the Elo it skewed.

Why they exist
--------------
mma rows arrived from more than one path, and the paths disagree about
which fighter is "home". 24 fighter-pairs are stored more than once,
usually mirrored on the same date -- `Al-Selwady vs Shem Rock` and `Shem
Rock vs Al-Selwady`, scores swapped to match. The winner agrees in every
case, so nothing is wrong; it is counted twice. Measured 2026-09-20:

    25 redundant rows
    30 elo_history rows   (24% of all mma Elo history)
    23 graded picks       (+6.17 units ROI counts as real wagers)
   119 odds rows

This is `dedupe_picks` one level up: there the same wager appeared three
times, here the same bout does.

Scope, deliberately narrow
--------------------------
**Same date only.** 10 of the 24 pairs sit on different dates, and several
are 06-14 against 06-15 -- one card recorded under two date conventions,
not a duplicate to collapse. Others may be a real rematch, which is
ordinary in this sport. Collapsing either would destroy a genuine bout, so
a differing date means hands off. Those pairs are reported and left.

**Contradictory winners are refused.** If two copies disagree about who
won, one of them has been grading picks backwards and feeding a reversed
Elo update. Choosing a side would bury that; the pair is reported instead.

The Elo replay is not optional
------------------------------
Deleting a redundant `elo_history` row does not undo its effect. Combat
Elo is a running product and `EloRating` still holds the value the
double-counted bout produced, so the history and the live rating would
disagree. `backfill_elo_history` refuses combat sports -- theirs is
post-game and grader-owned -- so the replay here drives
`grader._apply_combat_elo_update`, the same function that wrote the rows,
rather than a second copy of the same arithmetic.

Like `dedupe_picks`, a dry run is the DEFAULT and `--apply` is required.
This deletes games, picks and recorded results.

    python -m backend.scripts.dedupe_combat_games --db <abs path>
    python -m backend.scripts.dedupe_combat_games --db <abs path> --apply
"""
import argparse
import logging
import os
from collections import defaultdict
from dataclasses import dataclass, field

from sqlalchemy import func

from backend.collectors.ufc import normalize_name
from backend.database import get_engine, get_session, run_migrations
from backend.models import (EloHistory, EloRating, Game, Odds, PickModel,
                            PickResult, Team)
from backend.time_utils import et_today

logger = logging.getLogger(__name__)

#: Sports whose Elo is binary and grader-owned.
COMBAT_SPORTS = ("mma", "boxing")

SEED_RATING = 1500.0


@dataclass
class Group:
    """One bout stored more than once on a single date."""
    key: tuple
    keep: int
    drop: list[int] = field(default_factory=list)


def _winner(game, names) -> str | None:
    """Normalised name of the winner, or None if undecided."""
    if game.home_score is None or game.away_score is None:
        return None
    if game.home_score == game.away_score:
        return None
    winning_id = (game.home_team_id if game.home_score > game.away_score
                  else game.away_team_id)
    return normalize_name(names.get(winning_id, ""))


def duplicate_groups(session, sport: str) -> list[Group]:
    """Bouts stored more than once on the same date, survivor first.

    Keyed on (date, unordered fighter pair), so the mirrored corner order
    that produced these rows collapses to one key while a rematch or a
    date-convention split stays distinct.
    """
    rows = session.query(Game).filter(Game.sport == sport).all()
    names = {t.id: t.abbreviation for t in
             session.query(Team).filter(Team.sport == sport)}

    by_key: dict[tuple, list[Game]] = defaultdict(list)
    for g in rows:
        pair = frozenset({normalize_name(names.get(g.home_team_id, "")),
                          normalize_name(names.get(g.away_team_id, ""))})
        if len(pair) != 2:          # same fighter twice: not a bout
            continue
        by_key[(g.date, pair)].append(g)

    groups: list[Group] = []
    for key, games in by_key.items():
        if len(games) < 2:
            continue
        games.sort(key=lambda g: g.id)
        groups.append(Group(key=key, keep=games[0].id,
                            drop=[g.id for g in games[1:]]))
    return sorted(groups, key=lambda g: g.keep)


def conflicting(session, group: Group, names) -> bool:
    """Whether the copies disagree about who won."""
    winners = set()
    for gid in [group.keep, *group.drop]:
        game = session.get(Game, gid)
        w = _winner(game, names)
        if w:
            winners.add(w)
    return len(winners) > 1


def rebuild_combat_elo(session, sport: str) -> dict:
    """Discard and replay ``sport``'s Elo from the seed, chronologically.

    From the seed, not from the current ratings: Elo is a running product,
    so replaying on top of what is already there would apply every bout a
    second time rather than removing a double count.

    Drives `grader._apply_combat_elo_update` so this cannot drift from the
    arithmetic that wrote the rows in the first place.
    """
    from backend.pipeline.grader import _apply_combat_elo_update

    session.query(EloHistory).filter(
        EloHistory.sport == sport).delete(synchronize_session=False)
    session.query(EloRating).filter(
        EloRating.sport == sport).delete(synchronize_session=False)
    session.flush()

    games = (session.query(Game)
             .filter(Game.sport == sport, Game.status == "final",
                     Game.home_score.isnot(None), Game.away_score.isnot(None))
             .order_by(Game.date.asc(), Game.id.asc()).all())
    for game in games:
        _apply_combat_elo_update(session, game)
    session.flush()
    return {"sport": sport, "bouts_replayed": len(games)}


def run_on_session(session, sport: str = "mma", *, apply: bool = False) -> dict:
    """Report, and with ``apply``, remove redundant copies and replay Elo."""
    summary = {"sport": sport, "groups": 0, "deleted": 0, "picks_deleted": 0,
               "units_removed": 0.0, "refused_conflict": 0,
               "refused_ids": [], "bouts_replayed": 0}
    if sport not in COMBAT_SPORTS:
        raise ValueError(f"{sport!r} is not a combat sport; its Elo is "
                         "pre-game and owned by backfill_elo_history.")

    names = {t.id: t.abbreviation for t in
             session.query(Team).filter(Team.sport == sport)}
    doomed: list[int] = []
    for group in duplicate_groups(session, sport):
        if conflicting(session, group, names):
            summary["refused_conflict"] += 1
            summary["refused_ids"].extend([group.keep, *group.drop])
            continue
        summary["groups"] += 1
        doomed.extend(group.drop)

    if not doomed:
        return summary

    pick_ids = [p.id for p in session.query(PickModel)
                .filter(PickModel.game_id.in_(doomed))]
    units = 0.0
    if pick_ids:
        units = sum(r.payout or 0.0 for r in session.query(PickResult)
                    .filter(PickResult.pick_id.in_(pick_ids)))
    summary["deleted"] = len(doomed)
    summary["picks_deleted"] = len(pick_ids)
    summary["units_removed"] = round(units, 2)

    if apply:
        # Children first: pick_results references picks, and picks, odds and
        # elo_history all reference games. Deleting a game while its rows
        # remain trips the foreign key and strands recorded results.
        if pick_ids:
            session.query(PickResult).filter(
                PickResult.pick_id.in_(pick_ids)).delete(synchronize_session=False)
            session.query(PickModel).filter(
                PickModel.id.in_(pick_ids)).delete(synchronize_session=False)
        session.query(EloHistory).filter(
            EloHistory.game_id.in_(doomed)).delete(synchronize_session=False)
        session.query(Odds).filter(
            Odds.game_id.in_(doomed)).delete(synchronize_session=False)
        session.query(Game).filter(
            Game.id.in_(doomed)).delete(synchronize_session=False)
        session.flush()
        # Only now, against the surviving bouts.
        summary["bouts_replayed"] = rebuild_combat_elo(
            session, sport)["bouts_replayed"]
        session.commit()
    return summary


def run(db_path: str, *, sport: str = "mma", apply: bool = False) -> dict:
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
        return run_on_session(session, sport, apply=apply)
    finally:
        session.close()


def format_summary(s: dict, apply: bool) -> str:
    lines = ["Deduplicated combat bouts" if apply
             else "DRY RUN -- nothing written", ""]
    lines.append(f"  sport             : {s['sport']}")
    lines.append(f"  duplicate groups  : {s['groups']}")
    lines.append(f"  {'deleted' if apply else 'would delete':<17} : "
                 f"{s['deleted']} redundant row(s)")
    lines.append(f"  picks removed     : {s['picks_deleted']} "
                 f"({s['units_removed']:+.2f} units of double-counted payout)")
    if apply:
        lines.append(f"  Elo replayed over : {s['bouts_replayed']} surviving bout(s)")
    if s["refused_conflict"]:
        lines.append("")
        lines.append(f"  REFUSED: {s['refused_conflict']} pair(s) disagree about who won.")
        lines.append("  One copy has been grading picks backwards and feeding a")
        lines.append("  reversed Elo update. Resolve those by hand.")
        lines.append(f"  game ids: {s['refused_ids'][:20]}")
    lines.append("")
    lines.append("  Only same-date pairs are touched. The same fighters on a")
    lines.append("  different date may be one card under two date conventions,")
    lines.append("  or a genuine rematch, and are left alone.")
    return "\n".join(lines)


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO)
    ap = argparse.ArgumentParser(
        description="Remove combat bouts stored twice and replay their Elo. "
                    "Dry run by default.")
    ap.add_argument("--db", required=True, help="Path to the database. Back it up first.")
    ap.add_argument("--sport", default="mma", choices=COMBAT_SPORTS)
    ap.add_argument("--apply", action="store_true",
                    help="Actually delete. Without this it is a dry run.")
    args = ap.parse_args(argv)
    print(format_summary(run(args.db, sport=args.sport, apply=args.apply),
                         args.apply))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


# --- a date split, where neither copy is redundant -------------------------

#: How far apart two copies of one bout can sit and still be the same card.
#: ESPN dates a Saturday-night UFC card 06-14 while the odds feed says
#: 06-15; anything wider is a reschedule, not a disagreement about when the
#: same event happened.
ADJACENT_DAYS = 2


def date_split_groups(session, sport: str) -> list[Group]:
    """Bouts recorded on near-adjacent dates by two disagreeing sources.

    Unlike `duplicate_groups`, neither copy here is redundant: the
    ESPN-dated row carries the RESULT and the odds-dated row carries the
    MARKET. The survivor is the `final` copy when there is exactly one,
    because `elo_history` points at it and keeping the other would orphan
    those rows and force a second Elo replay. With no result to preserve,
    the row the live odds feed tracks (`odds_api_id`) wins, then the copy
    carrying the most odds, then the lowest id.
    """
    names = {t.id: t.abbreviation for t in
             session.query(Team).filter(Team.sport == sport)}
    rows = session.query(Game).filter(Game.sport == sport).all()
    odds_counts = dict(
        session.query(Odds.game_id, func.count(Odds.id))
        .group_by(Odds.game_id).all())

    by_pair: dict[frozenset, list[Game]] = defaultdict(list)
    for g in rows:
        pair = frozenset({normalize_name(names.get(g.home_team_id, "")),
                          normalize_name(names.get(g.away_team_id, ""))})
        if len(pair) == 2:
            by_pair[pair].append(g)

    groups: list[Group] = []
    for pair, games in by_pair.items():
        games.sort(key=lambda g: (g.date, g.id))
        for i in range(len(games) - 1):
            a, b = games[i], games[i + 1]
            gap = (b.date - a.date).days
            if not 1 <= gap <= ADJACENT_DAYS:
                continue
            finals = [g for g in (a, b) if g.status == "final"]
            if len(finals) == 2:
                # Two results for one bout is not a split. Refused by the
                # caller rather than silently resolved.
                groups.append(Group(key=(pair, "conflict"), keep=a.id,
                                    drop=[b.id]))
                continue
            if finals:
                keep = finals[0]
            else:
                # odds_api_id outranks a raw odds count: it is the identity
                # the live feed matches on, so dropping the row that carries
                # it means the next fetch re-creates it as a new duplicate.
                keep = max((a, b), key=lambda g: (g.odds_api_id is not None,
                                                  odds_counts.get(g.id, 0),
                                                  -g.id))
            drop = b if keep is a else a
            groups.append(Group(key=(pair, "split"), keep=keep.id,
                                drop=[drop.id]))
    return groups


def merge_date_splits(session, sport: str = "mma", *,
                      apply: bool = False) -> dict:
    """Move the market onto the surviving row and drop the emptied twin."""
    summary = {"sport": sport, "merged": 0, "odds_moved": 0, "refused": 0,
               "refused_ids": []}
    for group in date_split_groups(session, sport):
        if group.key[1] == "conflict":
            summary["refused"] += 1
            summary["refused_ids"].extend([group.keep, *group.drop])
            continue
        drop_id = group.drop[0]
        moving = session.query(Odds).filter(Odds.game_id == drop_id).count()
        summary["merged"] += 1
        summary["odds_moved"] += moving
        if apply:
            # Reparented, not deleted: the price this bout was offered at is
            # the only record of what the market thought, and the surviving
            # row is the one everything else now points at.
            session.query(Odds).filter(Odds.game_id == drop_id).update(
                {Odds.game_id: group.keep}, synchronize_session=False)
            session.query(EloHistory).filter(
                EloHistory.game_id == drop_id).delete(synchronize_session=False)
            pick_ids = [p.id for p in session.query(PickModel)
                        .filter(PickModel.game_id == drop_id)]
            if pick_ids:
                session.query(PickResult).filter(
                    PickResult.pick_id.in_(pick_ids)).delete(synchronize_session=False)
                session.query(PickModel).filter(
                    PickModel.id.in_(pick_ids)).delete(synchronize_session=False)
            session.query(Game).filter(
                Game.id == drop_id).delete(synchronize_session=False)
    if apply and summary["merged"]:
        session.commit()
    return summary


# --- a bout that moved and never happened here -----------------------------

def stale_reschedules(session, sport: str, today) -> list[Game]:
    """Past-dated scheduled bouts that also exist on a later date.

    A later-dated twin is the only reliable way to tell a bout that MOVED
    from one that happened and went unmatched: the finalizer reports both
    as unmatched. Without the twin this cannot be inferred, which is why
    the other 53 stuck rows are left alone -- they are promotions no
    source covers, not fights that were rescheduled.
    """
    names = {t.id: t.abbreviation for t in
             session.query(Team).filter(Team.sport == sport)}
    rows = session.query(Game).filter(Game.sport == sport).all()
    by_pair: dict[frozenset, list[Game]] = defaultdict(list)
    for g in rows:
        pair = frozenset({normalize_name(names.get(g.home_team_id, "")),
                          normalize_name(names.get(g.away_team_id, ""))})
        if len(pair) == 2:
            by_pair[pair].append(g)

    out = []
    for games in by_pair.values():
        for g in games:
            if g.status != "scheduled" or g.date >= today:
                continue
            if any(t.date > g.date for t in games if t.id != g.id):
                out.append(g)
    return sorted(out, key=lambda g: g.id)


def void_reschedules(session, sport: str = "mma", today=None, *,
                     apply: bool = False) -> dict:
    """Cancel bouts that moved, settling their picks as a push.

    Delegates the settlement to `void_stuck_bouts.settle_as_void` so a
    voided position means the same thing however it got voided.
    """
    from backend.scripts.void_stuck_bouts import settle_as_void

    today = today or et_today()
    summary = {"sport": sport, "voided": 0, "picks_settled": 0}
    for game in stale_reschedules(session, sport, today):
        picks = session.query(PickModel).filter(
            PickModel.game_id == game.id).all()
        summary["voided"] += 1
        summary["picks_settled"] += len(picks)
        if apply:
            settle_as_void(session, game, picks)
    if apply and summary["voided"]:
        session.commit()
    return summary

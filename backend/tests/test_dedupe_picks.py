"""Guards for the duplicate-pick cleanup.

It deletes rows, so the dangerous outcomes are removing a graded pick (which
would silently rewrite recorded results) and removing the wrong member of a
group whose copies disagree.
"""
import datetime

import pytest

from backend.database import get_engine, get_session
from backend.models import (
    Base, Game, PickModel, PickResult, PickVersion, StrategyModel, Team,
)
from backend.pipeline.pick_versions import record_pick_version
from backend.scripts.dedupe_picks import duplicate_groups, run

D = datetime.date(2026, 9, 19)


def _db(tmp_path, picks, results=()):
    path = tmp_path / "d.db"
    s = get_session(get_engine(str(path)))
    Base.metadata.create_all(s.get_bind())
    s.add_all([Team(id=1, name="H", abbreviation="H", sport="ncaaf"),
               Team(id=2, name="A", abbreviation="A", sport="ncaaf"),
               Team(id=3, name="H2", abbreviation="H2", sport="mlb"),
               Team(id=4, name="A2", abbreviation="A2", sport="mlb")])
    s.flush()
    s.add_all([
        Game(id=1, sport="ncaaf", season="2026", date=D, status="scheduled",
             home_team_id=1, away_team_id=2),
        Game(id=2, sport="mlb", season="2026", date=D, status="scheduled",
             home_team_id=3, away_team_id=4),
    ])
    s.add(StrategyModel(id=1, name="ensemble", config_json="{}",
                        is_active=True, strategy_type="game"))
    s.flush()
    s.add_all(picks)
    s.flush()
    s.add_all(results)
    s.commit()
    s.close()
    return str(path)


def _pick(pid, game_id=1, pick_type="moneyline", value="HOME ML",
          prop_player=None, prop_market=None):
    return PickModel(id=pid, game_id=game_id, strategy_id=1,
                     pick_type=pick_type, pick_value=value, confidence=3,
                     edge_pct=5.0, odds_at_pick=-110,
                     prop_player=prop_player, prop_market=prop_market,
                     created_at=datetime.datetime(2026, 9, 19, 12, pid % 60))


def _ids(db):
    s = get_session(get_engine(db))
    try:
        return sorted(p.id for p in s.query(PickModel))
    finally:
        s.close()


def test_the_earliest_of_a_group_survives(tmp_path):
    """Matches the idempotence rule: the first pick stands, entry price kept."""
    db = _db(tmp_path, [_pick(1), _pick(2), _pick(3)])
    run(db, apply=True)
    assert _ids(db) == [1]


def test_distinct_markets_are_not_touched(tmp_path):
    db = _db(tmp_path, [_pick(1, pick_type="moneyline"),
                        _pick(2, pick_type="spread"),
                        _pick(3, pick_type="over_under")])
    run(db, apply=True)
    assert _ids(db) == [1, 2, 3]


def test_props_are_keyed_by_player_and_market(tmp_path):
    """Many prop picks share one game; only same player+market is a duplicate."""
    db = _db(tmp_path, [
        _pick(1, pick_type="prop", prop_player="A", prop_market="pass_yds"),
        _pick(2, pick_type="prop", prop_player="B", prop_market="pass_yds"),
        _pick(3, pick_type="prop", prop_player="A", prop_market="rush_yds"),
        _pick(4, pick_type="prop", prop_player="A", prop_market="pass_yds"),
    ])
    run(db, apply=True)
    assert _ids(db) == [1, 2, 3]


def test_a_graded_duplicate_is_refused_not_deleted(tmp_path):
    """Deleting a graded pick would silently rewrite recorded results."""
    db = _db(tmp_path, [_pick(1), _pick(2)],
             [PickResult(pick_id=2, result="win", payout=0.91)])
    summary = run(db, apply=True)
    assert _ids(db) == [1, 2], "deleted a graded pick"
    assert summary["refused_graded"] == 1


def test_a_graded_survivor_with_ungraded_copies_still_cleans(tmp_path):
    """Only the graded rows are protected, not the whole group."""
    db = _db(tmp_path, [_pick(1), _pick(2), _pick(3)],
             [PickResult(pick_id=1, result="win", payout=0.91)])
    run(db, apply=True)
    assert _ids(db) == [1]


def test_dry_run_is_the_default_and_writes_nothing(tmp_path):
    db = _db(tmp_path, [_pick(1), _pick(2)])
    summary = run(db)
    assert summary["would_delete"] == 1
    assert _ids(db) == [1, 2], "a dry run deleted rows"


def test_deleting_a_duplicate_with_versions_does_not_fk_error_and_leaves_no_orphans(tmp_path):
    """`dedupe_picks` bulk-deletes with `query.delete()`, which emits a
    plain SQL DELETE rather than going through the ORM's identity map --
    the review flagged this as the pattern to check, since SQLite's FK
    cascade is entirely a database-level guarantee (`PRAGMA
    foreign_keys=ON`, `ON DELETE CASCADE` in the `pick_versions` DDL) and
    not something the ORM enforces on a bulk delete. Pick 2 (dropped, the
    duplicate) carries two recorded versions; pick 1 (kept) carries one.
    """
    db = _db(tmp_path, [_pick(1), _pick(2)])
    s = get_session(get_engine(db))
    p1, p2 = (s.query(PickModel).filter(PickModel.id == pid).one()
             for pid in (1, 2))
    record_pick_version(s, p1, "insert")
    record_pick_version(s, p2, "insert")
    s.commit()
    p2.edge_pct = 9.0
    record_pick_version(s, p2, "refresh")
    s.commit()
    s.close()

    summary = run(db, apply=True)  # must not raise an IntegrityError

    assert _ids(db) == [1]
    assert summary["deleted"] == 1

    s = get_session(get_engine(db))
    try:
        remaining = s.query(PickVersion).all()
        assert {v.pick_id for v in remaining} == {1}, \
            "pick 2's version rows must be gone, not orphaned"
        assert len(remaining) == 1
    finally:
        s.close()


def test_a_sport_filter_limits_the_blast_radius(tmp_path):
    db = _db(tmp_path, [_pick(1), _pick(2),
                        _pick(3, game_id=2), _pick(4, game_id=2)])
    run(db, apply=True, sport="ncaaf")
    assert _ids(db) == [1, 3, 4], "touched a sport outside the filter"


def test_a_date_filter_limits_the_blast_radius(tmp_path):
    db = _db(tmp_path, [_pick(1), _pick(2)])
    run(db, apply=True, on_date=datetime.date(2026, 1, 1))
    assert _ids(db) == [1, 2]


def test_groups_are_reported_with_their_ids(tmp_path):
    db = _db(tmp_path, [_pick(1), _pick(2), _pick(3)])
    s = get_session(get_engine(db))
    try:
        groups = duplicate_groups(s)
    finally:
        s.close()
    assert len(groups) == 1
    assert groups[0].keep == 1
    assert groups[0].drop == [2, 3]


def test_run_refuses_a_db_path_that_does_not_exist(tmp_path):
    with pytest.raises(FileNotFoundError):
        run(str(tmp_path / "nope.db"))


# --------------------------------------------------------------------------
# Dropping graded re-prices.
#
# A duplicate is a later decision against a moved line, not a re-record of
# one -- ncaab 1260's moneyline was AWAY +367 on the first run and HOME +150
# on the six after. Keeping the first pick and dropping the rest makes the
# book what it would have been had the generator always been idempotent.
#
# Their pick_results rows must go with them, or the deletes trip the foreign
# key and the results are orphaned from any wager.
# --------------------------------------------------------------------------

def _results(db):
    s = get_session(get_engine(db))
    try:
        return sorted(r.pick_id for r in s.query(PickResult))
    finally:
        s.close()


def test_graded_duplicates_are_removed_when_asked(tmp_path):
    db = _db(tmp_path, [_pick(1), _pick(2), _pick(3)],
             [PickResult(pick_id=1, result="win", payout=0.91),
              PickResult(pick_id=2, result="win", payout=0.91),
              PickResult(pick_id=3, result="win", payout=0.91)])
    summary = run(db, apply=True, include_graded=True)

    assert _ids(db) == [1]
    assert summary["deleted"] == 2
    assert summary["refused_graded"] == 0


def test_their_results_go_with_them(tmp_path):
    """Otherwise the delete trips the FK, or leaves a result with no wager."""
    db = _db(tmp_path, [_pick(1), _pick(2)],
             [PickResult(pick_id=1, result="win", payout=0.91),
              PickResult(pick_id=2, result="win", payout=0.91)])
    run(db, apply=True, include_graded=True)

    assert _results(db) == [1], "orphaned or undeleted result rows"


def test_the_survivors_own_result_is_untouched(tmp_path):
    """Only the copies are dropped; the first pick keeps its grade."""
    db = _db(tmp_path, [_pick(1), _pick(2)],
             [PickResult(pick_id=1, result="win", payout=0.91),
              PickResult(pick_id=2, result="loss", payout=-1.0)])
    run(db, apply=True, include_graded=True)

    s = get_session(get_engine(db))
    try:
        kept = s.query(PickResult).one()
        assert kept.pick_id == 1 and kept.result == "win"
    finally:
        s.close()


def test_the_units_removed_are_reported(tmp_path):
    """The ROI change must be visible, not inferred afterwards."""
    db = _db(tmp_path, [_pick(1), _pick(2), _pick(3)],
             [PickResult(pick_id=1, result="win", payout=0.91),
              PickResult(pick_id=2, result="win", payout=0.91),
              PickResult(pick_id=3, result="loss", payout=-1.0)])
    summary = run(db, apply=True, include_graded=True)

    assert summary["units_removed"] == pytest.approx(-0.09)


def test_graded_rows_are_still_refused_by_default(tmp_path):
    """Opt-in only. The default must stay the cautious one."""
    db = _db(tmp_path, [_pick(1), _pick(2)],
             [PickResult(pick_id=2, result="win", payout=0.91)])
    run(db, apply=True)
    assert _ids(db) == [1, 2]


def test_a_dry_run_with_graded_still_writes_nothing(tmp_path):
    db = _db(tmp_path, [_pick(1), _pick(2)],
             [PickResult(pick_id=2, result="win", payout=0.91)])
    summary = run(db, include_graded=True)

    assert _ids(db) == [1, 2]
    assert _results(db) == [2]
    assert summary["would_delete"] == 1

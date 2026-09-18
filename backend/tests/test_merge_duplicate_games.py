"""Guards for merging games that share an ESPN event id.

Task 1's backfill proved 13 pairs are the same game stored twice, one per date
convention. Nine of them are final on both sides, which means
`backfill_elo_history` applied nine real results twice -- team 6 sat at 1586.23
after game 469 and 1599.43 after game 474, the same game and the same points
again.

The dangerous outcome here is not a crash. It is losing a pick, losing a score,
or preserving the double-count by repointing derived rows instead of deleting
them.
"""
import datetime

import pytest

from backend.database import get_engine, get_session
from backend.models import (
    Base, EloHistory, Game, Odds, PickModel, StrategyModel, Team, TeamStat,
)
from backend.scripts.merge_duplicate_games import run

ET_DAY = datetime.date(2026, 3, 14)
UTC_DAY = datetime.date(2026, 3, 15)


def _db(tmp_path, build):
    db = tmp_path / "merge.db"
    session = get_session(get_engine(str(db)))
    Base.metadata.create_all(session.get_bind())
    session.add_all([
        Team(id=1, name="MIA Team", abbreviation="MIA", sport="nba"),
        Team(id=2, name="ORL Team", abbreviation="ORL", sport="nba"),
    ])
    session.add(StrategyModel(id=1, name="s", config_json="{}"))
    session.flush()
    build(session)
    session.commit()
    session.close()
    return str(db)


def _twin(gid, day, status, *, scores=True, espn_id="401700001"):
    return Game(id=gid, sport="nba", season="2025-26", date=day, espn_id=espn_id,
                home_team_id=1, away_team_id=2, status=status,
                home_score=117 if scores else None,
                away_score=121 if scores else None)


def _game_ids(db):
    session = get_session(get_engine(db))
    try:
        return {g.id for g in session.query(Game).all()}
    finally:
        session.close()


def _game(db, gid):
    session = get_session(get_engine(db))
    try:
        return session.query(Game).filter(Game.id == gid).one_or_none()
    finally:
        session.close()


def test_the_row_with_picks_survives_even_if_the_other_is_final(tmp_path):
    """Picks are user-facing and irreplaceable; team_stats and elo_history are
    derived and regenerable. Production has 3 pairs where the ET-dated row
    carries the only pick and the UTC-dated twin carries nothing."""
    def build(session):
        session.add(_twin(1, ET_DAY, "scheduled", scores=False))
        session.add(_twin(2, UTC_DAY, "final"))
        session.flush()
        session.add(PickModel(id=1, game_id=1, strategy_id=1, pick_type="moneyline",
                              pick_value="HOME ML", confidence=3, edge_pct=4.0,
                              odds_at_pick=-110))
    db = _db(tmp_path, build)

    run(db)

    assert _game_ids(db) == {1}, "the row carrying the pick must survive"
    session = get_session(get_engine(db))
    try:
        assert session.query(PickModel).one().game_id == 1
    finally:
        session.close()


def test_scores_are_copied_onto_the_survivor_when_it_has_none(tmp_path):
    """The surviving row must not lose the result. Pair 401810867 has one side
    scheduled with no scores and the other final with them."""
    def build(session):
        session.add(_twin(1, ET_DAY, "scheduled", scores=False))
        session.add(_twin(2, UTC_DAY, "final"))
        session.flush()
        session.add(PickModel(id=1, game_id=1, strategy_id=1, pick_type="moneyline",
                              pick_value="HOME ML", confidence=3, edge_pct=4.0,
                              odds_at_pick=-110))
    db = _db(tmp_path, build)

    run(db)

    survivor = _game(db, 1)
    assert survivor.status == "final"
    assert (survivor.home_score, survivor.away_score) == (117, 121)


def test_the_losers_elo_history_is_deleted_not_repointed(tmp_path):
    """Repointing preserves the double-count, which is the actual damage: nine
    real games were applied twice by backfill_elo_history. The rows must be
    deleted so the replay can recompute them."""
    def build(session):
        session.add(_twin(1, ET_DAY, "final"))
        session.add(_twin(2, UTC_DAY, "final"))
        session.flush()
        for gid in (1, 2):
            session.add_all([
                EloHistory(team_id=1, game_id=gid, sport="nba", rating=1500.0),
                EloHistory(team_id=2, game_id=gid, sport="nba", rating=1500.0),
                TeamStat(team_id=1, game_id=gid,
                         stat_type="point_diff", value=1.0),
            ])
    db = _db(tmp_path, build)

    run(db)

    session = get_session(get_engine(db))
    try:
        assert session.query(Game).count() == 1
        # Only the survivor's derived rows remain -- not repointed duplicates.
        assert session.query(EloHistory).count() == 2
        assert session.query(TeamStat).count() == 1
        assert {h.game_id for h in session.query(EloHistory).all()} == {1}
    finally:
        session.close()


def test_odds_are_repointed_to_the_survivor(tmp_path):
    """Odds are real observations, not derived, so they move rather than die."""
    def build(session):
        session.add(_twin(1, ET_DAY, "scheduled", scores=False))
        session.add(_twin(2, UTC_DAY, "final"))
        session.flush()
        session.add(PickModel(id=1, game_id=1, strategy_id=1, pick_type="moneyline",
                              pick_value="HOME ML", confidence=3, edge_pct=4.0,
                              odds_at_pick=-110))
        session.add(Odds(game_id=2, bookmaker="dk", moneyline_home=-110,
                         moneyline_away=-110))
    db = _db(tmp_path, build)

    run(db)

    session = get_session(get_engine(db))
    try:
        assert session.query(Odds).one().game_id == 1
    finally:
        session.close()


def test_a_pair_with_picks_on_both_sides_is_refused(tmp_path):
    """Not present in today's data and not decidable by this rule: merging
    would silently move a pick from one game to another."""
    def build(session):
        session.add(_twin(1, ET_DAY, "final"))
        session.add(_twin(2, UTC_DAY, "final"))
        session.flush()
        for pid, gid in ((1, 1), (2, 2)):
            session.add(PickModel(id=pid, game_id=gid, strategy_id=1,
                                  pick_type="moneyline", pick_value="HOME ML",
                                  confidence=3, edge_pct=4.0, odds_at_pick=-110))
    db = _db(tmp_path, build)

    summary = run(db)

    assert _game_ids(db) == {1, 2}, "a contested pair must not be merged"
    assert summary["refused"] == 1


def test_dry_run_writes_nothing(tmp_path):
    def build(session):
        session.add(_twin(1, ET_DAY, "final"))
        session.add(_twin(2, UTC_DAY, "final"))
    db = _db(tmp_path, build)

    summary = run(db, dry_run=True)

    assert _game_ids(db) == {1, 2}
    assert summary["pairs"] == 1
    assert summary["merged"] == 0


def test_run_refuses_a_db_path_that_does_not_exist(tmp_path):
    with pytest.raises(FileNotFoundError):
        run(str(tmp_path / "nope.db"))

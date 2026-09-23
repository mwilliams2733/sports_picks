"""Tests for `scheduler._persist_pitcher_scores`.

Nothing persisted the pitcher scores a pick was priced on until this
existed -- they were fetched, used, and discarded, so no past pick could be
re-scored against the starter behind it. These tests hold that write path to
the same rules as any other measurement input: absent is absent (never
defaulted to neutral), a rerun updates rather than duplicates, and a write
failure must not propagate into the picks pipeline.
"""
from datetime import date

from backend.database import get_engine, get_session
from backend.models import Base, Team, Game, TeamStat
from backend.pipeline.scheduler import _persist_pitcher_scores, PITCHER_STAT_TYPE


def _session():
    engine = get_engine(":memory:")
    Base.metadata.create_all(engine)
    return get_session(engine)


def _seed_game(session, game_id=101, home_id=1, away_id=2):
    session.add_all([
        Team(id=home_id, name="Yankees", abbreviation="NYY", sport="mlb"),
        Team(id=away_id, name="Red Sox", abbreviation="BOS", sport="mlb"),
    ])
    session.flush()
    session.add(Game(id=game_id, sport="mlb", season="2026", date=date(2026, 9, 23),
                     home_team_id=home_id, away_team_id=away_id, status="scheduled"))
    session.commit()
    return home_id, away_id


def test_scores_are_written_for_both_sides():
    session = _session()
    home_id, away_id = _seed_game(session)

    written = _persist_pitcher_scores(session, {101: {"home": 0.72, "away": 0.41}})

    assert written == 2
    rows = session.query(TeamStat).filter(
        TeamStat.stat_type == PITCHER_STAT_TYPE).all()
    assert len(rows) == 2
    by_team = {r.team_id: r.value for r in rows}
    assert by_team[home_id] == 0.72
    assert by_team[away_id] == 0.41


def test_rerunning_updates_rather_than_duplicates():
    session = _session()
    home_id, away_id = _seed_game(session)

    _persist_pitcher_scores(session, {101: {"home": 0.72, "away": 0.41}})
    _persist_pitcher_scores(session, {101: {"home": 0.80, "away": 0.41}})

    rows = session.query(TeamStat).filter(
        TeamStat.stat_type == PITCHER_STAT_TYPE).all()
    assert len(rows) == 2
    by_team = {r.team_id: r.value for r in rows}
    assert by_team[home_id] == 0.80


def test_a_missing_side_writes_nothing_for_that_side():
    session = _session()
    home_id, away_id = _seed_game(session)

    written = _persist_pitcher_scores(session, {101: {"home": 0.72}})

    assert written == 1
    rows = session.query(TeamStat).filter(
        TeamStat.stat_type == PITCHER_STAT_TYPE).all()
    assert len(rows) == 1
    assert rows[0].team_id == home_id


def test_an_unknown_game_id_is_skipped():
    session = _session()
    _seed_game(session)

    written = _persist_pitcher_scores(session, {9999: {"home": 0.72, "away": 0.41}})

    assert written == 0
    rows = session.query(TeamStat).filter(
        TeamStat.stat_type == PITCHER_STAT_TYPE).all()
    assert rows == []


def test_a_write_failure_does_not_raise(monkeypatch):
    session = _session()
    _seed_game(session)

    def boom(*a, **k):
        raise RuntimeError("db is down")

    monkeypatch.setattr("backend.pipeline.team_stats._upsert_stats", boom)

    written = _persist_pitcher_scores(session, {101: {"home": 0.72, "away": 0.41}})

    assert isinstance(written, int)
    assert written == 0

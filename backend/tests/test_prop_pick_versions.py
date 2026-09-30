"""`pick_versions` recording through `_store_prop_picks` -- the prop side of
the same wiring `test_pick_generator_versions.py` covers for game picks.

Fixture shape copied from `test_prop_pick_upsert.py`.
"""
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine

from backend.data_types import PropAnalysis
from backend.database import get_session
from backend.models import Base, Game, PickModel, PickVersion, Team
from backend.pipeline.prop_pipeline import _store_prop_picks

DAY = date(2026, 9, 26)


def _a(line=205.5, odds=-110, edge=10.0, outcome="Over", player="Julian Lewis",
      market="player_pass_yds", game_id=1, bookmaker="draftkings"):
    return PropAnalysis(
        player_name=player, market=market, line=line, outcome=outcome,
        season_avg=220.0, recent_avg=220.0, projection=220.0, edge_pct=edge,
        confidence=3, source="espn", is_stale=False, game_id=game_id,
        odds=odds, bookmaker=bookmaker)


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    s = get_session(engine)
    s.add_all([Team(id=1, name="H", abbreviation="H", sport="ncaaf"),
              Team(id=2, name="A", abbreviation="A", sport="ncaaf")])
    s.flush()
    future = datetime.now(timezone.utc) + timedelta(hours=3)
    s.add(Game(id=1, sport="ncaaf", season="2026", date=DAY, status="scheduled",
              home_team_id=1, away_team_id=2, start_time=future.replace(tzinfo=None)))
    s.commit()
    yield s
    s.close()


def _the_pick(session):
    return session.query(PickModel).filter(PickModel.pick_type == "prop").one()


def _versions(session, pick_id):
    return (session.query(PickVersion)
            .filter(PickVersion.pick_id == pick_id)
            .order_by(PickVersion.version).all())


def test_prop_insert_gives_v1_with_source_insert(session):
    _store_prop_picks(session, [_a(line=205.5, odds=-110)], strategy_id=7)

    pick = _the_pick(session)
    versions = _versions(session, pick.id)
    assert [(v.version, v.source) for v in versions] == [(1, "insert")]
    assert versions[0].pick_value == pick.pick_value


def test_a_changed_prop_refresh_gives_v2(session):
    _store_prop_picks(session, [_a(line=205.5, odds=-110)], strategy_id=7)
    pick_id = _the_pick(session).id

    _store_prop_picks(session, [_a(line=207.5, odds=-105)], strategy_id=7)

    versions = _versions(session, pick_id)
    assert [v.version for v in versions] == [1, 2]
    assert versions[1].source == "refresh"
    assert versions[1].odds_at_pick == -105


def test_an_unchanged_prop_refresh_gives_nothing(session):
    _store_prop_picks(session, [_a(line=205.5, odds=-110)], strategy_id=7)
    pick_id = _the_pick(session).id

    _store_prop_picks(session, [_a(line=205.5, odds=-110)], strategy_id=7)

    assert len(_versions(session, pick_id)) == 1

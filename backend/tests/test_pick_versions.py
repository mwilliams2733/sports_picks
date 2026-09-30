"""Unit tests for `record_pick_version` itself -- the append-on-change
comparison and version numbering, isolated from either call site (see
`test_pick_generator_versions.py` / `test_prop_pick_versions.py` for the
wiring)."""
from datetime import date, datetime, timezone

import pytest
from sqlalchemy import create_engine

from backend.database import get_session
from backend.models import Base, Game, PickModel, PickVersion, StrategyModel, Team
from backend.pipeline.pick_versions import record_pick_version


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    s = get_session(engine)
    s.add_all([
        StrategyModel(id=1, name="ensemble", config_json="{}", is_active=True),
        Team(id=1, name="H", abbreviation="H", sport="nfl"),
        Team(id=2, name="A", abbreviation="A", sport="nfl"),
    ])
    s.flush()
    s.add(Game(id=1, sport="nfl", season="2026", date=date(2026, 9, 20),
              home_team_id=1, away_team_id=2, status="scheduled"))
    s.commit()
    yield s
    s.close()


def _pick(session, **kw):
    defaults = dict(game_id=1, strategy_id=1, pick_type="moneyline",
                    pick_value="HOME ML", confidence=3, edge_pct=5.0,
                    odds_at_pick=-110, model_prob=0.55,
                    suggested_unit_size=1.0, rationale_json=None,
                    created_at=datetime(2026, 9, 19, 12, 0, tzinfo=timezone.utc))
    defaults.update(kw)
    pick = PickModel(**defaults)
    session.add(pick)
    session.commit()
    return pick


def test_first_call_always_writes_version_1(session):
    pick = _pick(session)

    v = record_pick_version(session, pick, "insert")

    assert v is not None
    assert v.version == 1
    assert v.source == "insert"
    assert v.pick_value == "HOME ML"


def test_an_unchanged_pick_writes_nothing(session):
    pick = _pick(session)
    record_pick_version(session, pick, "insert")
    session.commit()

    v = record_pick_version(session, pick, "refresh")

    assert v is None
    assert session.query(PickVersion).filter(
        PickVersion.pick_id == pick.id).count() == 1


def test_a_changed_tracked_field_writes_the_next_version(session):
    pick = _pick(session)
    record_pick_version(session, pick, "insert")
    session.commit()

    pick.edge_pct = 7.5
    v = record_pick_version(session, pick, "refresh")

    assert v is not None
    assert v.version == 2
    assert v.edge_pct == 7.5


def test_none_equals_none_is_not_a_change(session):
    pick = _pick(session, model_prob=None)
    record_pick_version(session, pick, "insert")
    session.commit()

    # model_prob stays None on both sides -- must not count as a change.
    v = record_pick_version(session, pick, "refresh")

    assert v is None


def test_none_to_a_real_value_is_a_change(session):
    pick = _pick(session, model_prob=None)
    record_pick_version(session, pick, "insert")
    session.commit()

    pick.model_prob = 0.6
    v = record_pick_version(session, pick, "refresh")

    assert v is not None
    assert v.model_prob == 0.6


def test_version_numbers_increment_per_pick_not_globally(session):
    a = _pick(session)
    b = _pick(session, pick_type="spread", pick_value="HOME -1.5")
    record_pick_version(session, a, "insert")
    record_pick_version(session, b, "insert")
    session.commit()

    a.edge_pct = 9.0
    va = record_pick_version(session, a, "refresh")

    assert va.version == 2, "pick b's version-1 row must not bump pick a's counter"


def test_backfill_source_uses_created_at_not_now(session):
    created = datetime(2026, 3, 1, 8, 0, tzinfo=timezone.utc)
    pick = _pick(session, created_at=created)

    v = record_pick_version(session, pick, "backfill")

    assert v.recorded_at == created.replace(tzinfo=None)
    assert v.source == "backfill"


def test_insert_source_uses_now_not_created_at(session):
    created = datetime(2026, 3, 1, 8, 0, tzinfo=timezone.utc)
    pick = _pick(session, created_at=created)

    v = record_pick_version(session, pick, "insert")

    assert v.recorded_at != created


def test_unflushed_pick_is_flushed_before_recording(session):
    """A freshly-constructed, unflushed PickModel has no id yet -- the
    helper must flush so the FK is available rather than writing NULL."""
    pick = PickModel(game_id=1, strategy_id=1, pick_type="moneyline",
                     pick_value="AWAY ML", confidence=2, edge_pct=4.0,
                     odds_at_pick=120,
                     created_at=datetime.now(timezone.utc))
    session.add(pick)
    assert pick.id is None

    v = record_pick_version(session, pick, "insert")

    assert pick.id is not None
    assert v.pick_id == pick.id


def test_unique_constraint_on_pick_id_and_version(session):
    pick = _pick(session)
    session.add(PickVersion(pick_id=pick.id, version=1,
                            recorded_at=datetime.now(timezone.utc),
                            source="insert"))
    session.commit()
    session.add(PickVersion(pick_id=pick.id, version=1,
                            recorded_at=datetime.now(timezone.utc),
                            source="insert"))
    with pytest.raises(Exception):
        session.commit()


def test_a_version_collision_is_caught_and_skipped_not_fatal(session, monkeypatch, caplog):
    """Two overlapping scheduler runs (BackgroundScheduler's thread pool,
    per the review) can each compute the same next-version number for one
    pick from a read taken before either has written -- a real thread race
    is not reproducible deterministically in a single-threaded test, so
    this forces the exact failure mode `_next_version` would hand back in
    that race (a stale, already-taken number) via monkeypatch, and drives
    the real write path (the nested SAVEPOINT and its `except
    IntegrityError`) from there."""
    import logging
    from backend.pipeline import pick_versions

    pick = _pick(session)
    record_pick_version(session, pick, "insert")
    session.commit()
    assert [v.version for v in session.query(PickVersion)
            .filter(PickVersion.pick_id == pick.id)] == [1]

    # Force the "next" number to collide with the version already on
    # record, exactly as a stale concurrent read would.
    monkeypatch.setattr(pick_versions, "_next_version",
                        lambda session, pick_id: (1, None))
    pick.edge_pct = 12.0

    with caplog.at_level(logging.WARNING):
        result = record_pick_version(session, pick, "refresh")

    assert result is None, "a caught collision must not return a row"
    assert any("collision" in r.message and str(pick.id) in r.message
              for r in caplog.records), \
        "the collision must be logged with the pick_id"

    # The run continues: committing afterward must not raise, and the
    # pick's own refreshed field is not lost just because its version was.
    session.commit()
    refreshed = session.query(PickModel).filter(PickModel.id == pick.id).one()
    assert refreshed.edge_pct == 12.0
    versions = session.query(PickVersion).filter(
        PickVersion.pick_id == pick.id).all()
    assert [v.version for v in versions] == [1], \
        "the colliding version must not have been written"

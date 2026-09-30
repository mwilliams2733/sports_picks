"""backfill_pick_versions writes a version-1 row for every pick that has
none, is idempotent, and never touches picks that are already versioned."""
from datetime import date, datetime, timezone

import pytest

from backend.database import get_engine, get_session
from backend.models import Base, Game, PickModel, PickVersion, StrategyModel, Team
from backend.pipeline.pick_versions import record_pick_version
from backend.scripts.backfill_pick_versions import run_on_session


def _session():
    engine = get_engine(":memory:")
    Base.metadata.create_all(engine)
    s = get_session(engine)
    s.add_all([
        StrategyModel(id=1, name="ensemble", config_json="{}", is_active=True),
        Team(id=1, name="H", abbreviation="H", sport="nfl"),
        Team(id=2, name="A", abbreviation="A", sport="nfl"),
        Team(id=3, name="X", abbreviation="X", sport="mlb"),
        Team(id=4, name="Y", abbreviation="Y", sport="mlb"),
    ])
    s.flush()
    s.add_all([
        Game(id=1, sport="nfl", season="2026", date=date(2026, 3, 1),
             home_team_id=1, away_team_id=2, status="scheduled"),
        Game(id=2, sport="mlb", season="2026", date=date(2026, 3, 2),
             home_team_id=3, away_team_id=4, status="scheduled"),
    ])
    s.flush()
    created = datetime(2026, 3, 1, 10, 0, tzinfo=timezone.utc)
    # Pick 1: no version at all (the normal pre-migration case).
    s.add(PickModel(id=1, game_id=1, strategy_id=1, pick_type="moneyline",
                    pick_value="HOME ML", confidence=3, edge_pct=5.0,
                    odds_at_pick=-110, model_prob=0.55, created_at=created))
    # Pick 2: already has a live version -- must be left alone.
    s.add(PickModel(id=2, game_id=1, strategy_id=1, pick_type="spread",
                    pick_value="HOME -1.5", confidence=2, edge_pct=3.5,
                    odds_at_pick=-110, created_at=created))
    # Pick 3: a different sport, also unversioned.
    s.add(PickModel(id=3, game_id=2, strategy_id=1, pick_type="moneyline",
                    pick_value="AWAY ML", confidence=4, edge_pct=6.0,
                    odds_at_pick=120, created_at=created))
    s.commit()
    pick2 = s.get(PickModel, 2)
    record_pick_version(s, pick2, "insert")
    s.commit()
    return s


def _pick_ids_with_versions(s):
    return {pid for (pid,) in s.query(PickVersion.pick_id).distinct()}


def test_dry_run_writes_nothing():
    s = _session()
    before = _pick_ids_with_versions(s)

    summary = run_on_session(s, apply=False)

    assert _pick_ids_with_versions(s) == before
    assert set(summary["pick_ids"]) == {1, 3}
    assert summary["written"] == 2


def test_apply_gives_v1_for_unversioned_picks_only():
    s = _session()

    summary = run_on_session(s, apply=True)

    versions = {v.pick_id: v for v in s.query(PickVersion).all()}
    assert set(versions) == {1, 2, 3}
    v1 = versions[1]
    assert v1.version == 1
    assert v1.source == "backfill"
    assert v1.recorded_at == datetime(2026, 3, 1, 10, 0)
    assert v1.pick_value == "HOME ML"
    # Pick 2 was already versioned by record_pick_version(insert) before the
    # backfill ran -- its version must still be exactly 1, not a second row.
    assert s.query(PickVersion).filter(PickVersion.pick_id == 2).count() == 1
    assert summary["by_sport"] == {"nfl": 1, "mlb": 1}


def test_a_second_apply_writes_zero():
    s = _session()
    run_on_session(s, apply=True)

    summary = run_on_session(s, apply=True)

    assert summary["written"] == 0
    assert summary["pick_ids"] == []

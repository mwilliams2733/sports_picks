"""Tests for the closing-line backfill script.

The fixture records a LINE SNAPSHOT as well as an `Odds` row, because the
close is now read from the snapshot series. An `Odds` row alone is the
pre-2026-09-23 data shape: one row per bookmaker, no history. Production
rows all have a snapshot, seeded by `backfill_line_snapshots`.
"""
from datetime import date, datetime, timezone

import pytest

from backend.analysis.line_snapshots import record_snapshot
from backend.backfill_closing_lines import backfill
from backend.database import get_engine, get_session
from backend.models import (
    Base, Game, Odds, PickModel, PickResult, StrategyModel, Team,
)


@pytest.fixture
def db_with_legacy_picks(tmp_path):
    """Build a SQLite file DB containing graded picks with legacy -110 closes
    (the pre-fix data shape) so we can verify the backfill rewrites them."""
    db_path = str(tmp_path / "backfill.db")
    engine = get_engine(db_path)
    Base.metadata.create_all(engine)
    session = get_session(engine)

    t1 = Team(name="A", abbreviation="A", sport="nba")
    t2 = Team(name="B", abbreviation="B", sport="nba")
    session.add_all([t1, t2])
    session.flush()

    strat = StrategyModel(name="s", config_json="{}", is_active=True)
    session.add(strat)
    session.flush()

    g = Game(sport="nba", season="2025-26", date=date(2026, 1, 1),
             home_team_id=t1.id, away_team_id=t2.id,
             home_score=110, away_score=100, status="final")
    session.add(g)
    session.flush()

    closing = dict(moneyline_home=-160, moneyline_away=140,
                   spread_home=-3.5, spread_away=3.5, over_under=219.0)
    session.add(Odds(game_id=g.id, bookmaker="dk", **closing,
                     timestamp=datetime(2026, 1, 1, 19, 0, tzinfo=timezone.utc)))
    session.flush()
    record_snapshot(session, g.id, "dk", closing,
                    now=datetime(2026, 1, 1, 19, 0, tzinfo=timezone.utc))

    ml_pick = PickModel(game_id=g.id, strategy_id=strat.id,
                        pick_type="moneyline", pick_value="HOME ML",
                        confidence=4, edge_pct=5.0, odds_at_pick=-150)
    spread_pick = PickModel(game_id=g.id, strategy_id=strat.id,
                            pick_type="spread", pick_value="HOME -3.0",
                            confidence=4, edge_pct=4.0, odds_at_pick=-110)
    over_pick = PickModel(game_id=g.id, strategy_id=strat.id,
                          pick_type="over_under", pick_value="Over 218.5",
                          confidence=3, edge_pct=3.0, odds_at_pick=-105)
    session.add_all([ml_pick, spread_pick, over_pick])
    session.flush()

    # Legacy results: spread/total were stamped with the bogus -110 close.
    session.add_all([
        PickResult(pick_id=ml_pick.id, result="win", payout=0.667, odds_at_close=-160),
        PickResult(pick_id=spread_pick.id, result="win", payout=0.909, odds_at_close=-110),
        PickResult(pick_id=over_pick.id, result="loss", payout=-1.0, odds_at_close=-110),
    ])
    session.commit()
    session.close()
    return db_path


def test_backfill_rewrites_spread_and_total(db_with_legacy_picks):
    summary = backfill(db_with_legacy_picks, dry_run=False)

    assert summary["scanned"] == 3
    # Two rows changed line_at_close (spread + total); none had it before.
    assert summary["changed_line_at_close"] == 2
    # Spread pick odds_at_close goes -110 -> -110 (odds_at_pick), unchanged.
    # Over pick odds_at_close goes -110 -> -105 (odds_at_pick), changed.
    assert summary["changed_odds_at_close"] == 1

    # Verify persisted state.
    engine = get_engine(db_with_legacy_picks)
    session = get_session(engine)
    try:
        results = (
            session.query(PickResult, PickModel)
            .join(PickModel, PickResult.pick_id == PickModel.id)
            .order_by(PickModel.pick_type)
            .all()
        )
        by_type = {pm.pick_type: pr for pr, pm in results}
        # Moneyline: line stays None, odds stays at the captured close.
        assert by_type["moneyline"].line_at_close is None
        assert by_type["moneyline"].odds_at_close == -160
        # Spread: line populated with closing spread, odds is odds_at_pick.
        assert by_type["spread"].line_at_close == -3.5
        assert by_type["spread"].odds_at_close == -110
        # Over/under: line populated, odds is odds_at_pick (-105 from legacy -110).
        assert by_type["over_under"].line_at_close == 219.0
        assert by_type["over_under"].odds_at_close == -105
    finally:
        session.close()


def test_backfill_dry_run_does_not_persist(db_with_legacy_picks):
    summary = backfill(db_with_legacy_picks, dry_run=True)
    assert summary["dry_run"] is True
    # Dry run should still detect the same number of changes.
    assert summary["changed_line_at_close"] == 2

    # Verify nothing was actually written.
    engine = get_engine(db_with_legacy_picks)
    session = get_session(engine)
    try:
        results = session.query(PickResult).all()
        assert all(r.line_at_close is None for r in results)
    finally:
        session.close()


def test_backfill_idempotent(db_with_legacy_picks):
    backfill(db_with_legacy_picks, dry_run=False)
    second = backfill(db_with_legacy_picks, dry_run=False)
    assert second["changed_line_at_close"] == 0
    assert second["changed_odds_at_close"] == 0

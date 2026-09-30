"""`pick_versions` recording, wired through the real call sites in
`generate_and_store_picks` -- not a unit test of `record_pick_version` in
isolation (see `test_pick_versions.py` for that), but proof the pipeline
actually calls it on insert and on refresh, and that refreshing rebuilds
`rationale_json` instead of leaving the pre-flip factors stale.

Same fixture shape as `test_pick_refresh.py` (`value_only` over flat Elo),
so a changed market price is the only thing that moves between calls.
"""
from datetime import date, datetime, timedelta, timezone

from backend.models import (
    Base, EloRating, Game, Odds, PickModel, PickResult, PickVersion,
    StrategyModel, Team,
)
from backend.pipeline.pick_generator import generate_and_store_picks

DAY = date(2026, 3, 1)


def _seed(session, *, start_time=None, home_ml=-150, away_ml=130):
    session.add_all([
        Team(id=1, name="H", abbreviation="H1", sport="nba"),
        Team(id=2, name="A", abbreviation="A1", sport="nba"),
    ])
    session.flush()
    session.add(Game(id=1, sport="nba", season="2025-26", date=DAY,
                     home_team_id=1, away_team_id=2, status="scheduled",
                     start_time=start_time))
    session.flush()
    session.add_all([
        EloRating(team_id=1, sport="nba", rating=1500),
        EloRating(team_id=2, sport="nba", rating=1500),
        Odds(game_id=1, bookmaker="dk", moneyline_home=home_ml,
             moneyline_away=away_ml, spread_home=0.0, spread_away=0.0,
             over_under=0.0, timestamp=datetime(2026, 3, 1, 18, 0)),
        StrategyModel(id=1, name="value_only", config_json='{"min_edge": 0.1}',
                      is_active=True),
    ])
    session.commit()


def _the_pick(session):
    return session.query(PickModel).filter(
        PickModel.pick_type == "moneyline").one()


def _versions(session, pick_id):
    return (session.query(PickVersion)
            .filter(PickVersion.pick_id == pick_id)
            .order_by(PickVersion.version).all())


def test_a_new_pick_writes_exactly_version_1_with_source_insert(db_engine, db_session):
    Base.metadata.create_all(db_engine)
    _seed(db_session)

    generate_and_store_picks(db_session, strategy_id=1, target_date=DAY)

    pick = _the_pick(db_session)
    versions = _versions(db_session, pick.id)
    assert [(v.version, v.source) for v in versions] == [(1, "insert")]
    assert versions[0].pick_value == pick.pick_value
    assert versions[0].edge_pct == pick.edge_pct


def test_a_refresh_that_changes_the_price_writes_version_2_with_source_refresh(
        db_engine, db_session):
    Base.metadata.create_all(db_engine)
    _seed(db_session)
    generate_and_store_picks(db_session, strategy_id=1, target_date=DAY)
    pick_id = _the_pick(db_session).id

    # Move the market: same game, different odds on the next run.
    db_session.query(Odds).filter(Odds.game_id == 1).update({"moneyline_home": -170})
    db_session.commit()
    generate_and_store_picks(db_session, strategy_id=1, target_date=DAY)

    pick = _the_pick(db_session)
    assert pick.id == pick_id, "refresh must update in place"
    versions = _versions(db_session, pick.id)
    assert [v.version for v in versions] == [1, 2]
    assert versions[1].source == "refresh"
    assert versions[1].edge_pct == pick.edge_pct, "the new version mirrors the refreshed row"
    assert versions[0].edge_pct != versions[1].edge_pct, "the two versions actually differ"


def test_a_refresh_with_identical_values_writes_nothing(db_engine, db_session):
    Base.metadata.create_all(db_engine)
    _seed(db_session)
    generate_and_store_picks(db_session, strategy_id=1, target_date=DAY)
    pick_id = _the_pick(db_session).id
    assert len(_versions(db_session, pick_id)) == 1

    # Same inputs, run again: _refreshable allows it, but nothing changed.
    generate_and_store_picks(db_session, strategy_id=1, target_date=DAY)

    assert len(_versions(db_session, pick_id)) == 1


def test_a_graded_pick_gets_no_new_version(db_engine, db_session):
    Base.metadata.create_all(db_engine)
    _seed(db_session)
    generate_and_store_picks(db_session, strategy_id=1, target_date=DAY)
    pick = _the_pick(db_session)
    db_session.add(PickResult(pick_id=pick.id, result="win", payout=0.67))
    db_session.commit()
    assert len(_versions(db_session, pick.id)) == 1

    db_session.query(Odds).filter(Odds.game_id == 1).update({"moneyline_home": -170})
    db_session.commit()
    generate_and_store_picks(db_session, strategy_id=1, target_date=DAY)

    assert len(_versions(db_session, pick.id)) == 1, \
        "a graded pick must never gain a second version"


def test_a_pick_on_a_started_game_gets_no_new_version(db_engine, db_session):
    Base.metadata.create_all(db_engine)
    started = datetime.now(timezone.utc) - timedelta(hours=2)
    _seed(db_session, start_time=started)
    generate_and_store_picks(db_session, strategy_id=1, target_date=DAY,
                             skip_started=False)
    pick = _the_pick(db_session)
    assert len(_versions(db_session, pick.id)) == 1

    db_session.query(Odds).filter(Odds.game_id == 1).update({"moneyline_home": -170})
    db_session.commit()
    generate_and_store_picks(db_session, strategy_id=1, target_date=DAY,
                             skip_started=False)

    assert len(_versions(db_session, pick.id)) == 1, \
        "a pick on a started game must never gain a second version"


def test_a_side_flip_refreshes_rationale_and_the_version_carries_it(
        db_engine, db_session):
    """Regression for the bundled fix: `_refresh_pick` used to leave
    `rationale_json` untouched, so a pick that flipped sides kept the old
    side's factors.

    `value_only` (used elsewhere in this file) never emits factors, so a
    full-pipeline flip can't distinguish "refreshed" from "still empty".
    This drives `_refresh_pick` and `record_pick_version` directly with a
    fabricated `Pick` carrying real factors on each side, the same way
    `pick_generator`'s own call site does after `_refresh_pick` returns.
    """
    from dataclasses import replace
    from backend.data_types import Pick, PickFactor
    from backend.pipeline.pick_generator import _refresh_pick
    from backend.pipeline.pick_versions import record_pick_version

    Base.metadata.create_all(db_engine)
    _seed(db_session)
    home_pick = Pick(game_id=1, pick_type="moneyline", pick_value="HOME ML",
                     confidence=3, edge_pct=6.0, model_probability=0.6,
                     implied_probability=0.55, odds_at_pick=-150,
                     suggested_unit_size=1.0,
                     factors=[PickFactor(code="rating_gap", side="home",
                                         strength="moderate")])
    db_session.add(PickModel(
        game_id=1, strategy_id=1, pick_type="moneyline",
        pick_value=home_pick.pick_value, confidence=home_pick.confidence,
        edge_pct=home_pick.edge_pct, odds_at_pick=home_pick.odds_at_pick,
        model_prob=home_pick.model_probability,
        suggested_unit_size=home_pick.suggested_unit_size,
        rationale_json='[{"code": "rating_gap", "side": "home", "strength": "moderate"}]',
        created_at=datetime(2026, 3, 1, 12, 0, tzinfo=timezone.utc)))
    db_session.commit()
    pick = _the_pick(db_session)
    record_pick_version(db_session, pick, "insert")
    db_session.commit()
    first_rationale = pick.rationale_json

    away_pick = replace(home_pick, pick_value="AWAY ML",
                        factors=[PickFactor(code="rating_gap", side="away",
                                            strength="strong")])
    _refresh_pick(pick, away_pick)
    record_pick_version(db_session, pick, "refresh")
    db_session.commit()

    pick = _the_pick(db_session)
    assert pick.pick_value == "AWAY ML"
    assert pick.rationale_json != first_rationale, \
        "rationale_json must be refreshed on a flip, not left stale"
    assert '"side": "away"' in pick.rationale_json
    versions = _versions(db_session, pick.id)
    assert [v.version for v in versions] == [1, 2]
    assert versions[-1].rationale_json == pick.rationale_json

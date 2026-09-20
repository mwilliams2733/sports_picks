"""Guards for re-picking a game whose inputs were wrong.

`generate_and_store_picks` is idempotent per (game, strategy, pick_type),
which protects `odds_at_pick` -- the price a bet was taken at -- from being
restated by a later run. It also froze picks made on broken inputs: on
2026-09-20 a run at 01:27 ET wrote 17 NFL picks with flat Elo, every one
carrying P(home)=0.7496, and the 08:00 ET scout recomputed them correctly
and discarded every result because the markets were already picked. They
survived until deleted by hand.

A pick on a game that has not started is advice, not history: nothing has
been wagered and there is no price to restate. Those are refreshed. A
graded pick, or one on a game already under way, is left exactly alone --
restating either would rewrite a real result or a real wager.
"""
from datetime import date, datetime, timedelta, timezone

import pytest

from backend.models import (
    Base, EloRating, Game, Odds, PickModel, PickResult, StrategyModel, Team,
)
from backend.pipeline.pick_generator import generate_and_store_picks

DAY = date(2026, 3, 1)


def _seed(session, *, start_time=None):
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
        Odds(game_id=1, bookmaker="dk", moneyline_home=-150, moneyline_away=130,
             spread_home=0.0, spread_away=0.0, over_under=0.0,
             timestamp=datetime(2026, 3, 1, 18, 0)),
        StrategyModel(id=1, name="value_only", config_json='{"min_edge": 0.1}',
                      is_active=True),
    ])
    session.commit()


def _stale_pick(session, **kw):
    """A pick as a broken run would have written it."""
    defaults = dict(game_id=1, strategy_id=1, pick_type="moneyline",
                    pick_value="HOME ML", confidence=5, edge_pct=99.0,
                    odds_at_pick=-150, model_prob=0.7496)
    defaults.update(kw)
    pick = PickModel(**defaults)
    session.add(pick)
    session.commit()
    return pick.id


def _the_pick(session):
    return session.query(PickModel).filter(
        PickModel.pick_type == "moneyline").one()


def test_an_ungraded_pick_on_an_unstarted_game_is_refreshed(db_engine, db_session):
    Base.metadata.create_all(db_engine)
    _seed(db_session)
    pick_id = _stale_pick(db_session)

    generate_and_store_picks(db_session, strategy_id=1, target_date=DAY)

    pick = _the_pick(db_session)
    assert pick.edge_pct != 99.0, "stale edge survived the re-pick"
    assert pick.model_prob != 0.7496, "stale probability survived the re-pick"
    assert pick.id == pick_id, "refresh must update in place, not re-insert"


def test_refreshing_creates_no_duplicate_row(db_engine, db_session):
    Base.metadata.create_all(db_engine)
    _seed(db_session)
    _stale_pick(db_session)

    generate_and_store_picks(db_session, strategy_id=1, target_date=DAY)
    generate_and_store_picks(db_session, strategy_id=1, target_date=DAY)

    assert db_session.query(PickModel).filter(
        PickModel.pick_type == "moneyline").count() == 1


def test_a_graded_pick_is_never_rewritten(db_engine, db_session):
    """Restating a graded pick would rewrite a recorded wager."""
    Base.metadata.create_all(db_engine)
    _seed(db_session)
    pick_id = _stale_pick(db_session)
    db_session.add(PickResult(pick_id=pick_id, result="win", payout=0.67))
    db_session.commit()

    generate_and_store_picks(db_session, strategy_id=1, target_date=DAY)

    assert _the_pick(db_session).edge_pct == 99.0


def test_a_pick_on_a_started_game_is_never_rewritten(db_engine, db_session):
    """Once a game is under way the price is no longer takeable, so the
    stored one is history rather than advice."""
    Base.metadata.create_all(db_engine)
    started = datetime.now(timezone.utc) - timedelta(hours=2)
    _seed(db_session, start_time=started)
    _stale_pick(db_session)

    generate_and_store_picks(db_session, strategy_id=1, target_date=DAY,
                             skip_started=False)

    assert _the_pick(db_session).edge_pct == 99.0


def test_a_backtest_does_not_restate_a_long_finished_game(db_engine, db_session):
    """Backtests pass skip_started=False over games long over. Those must
    keep their stored picks, or a replay would rewrite its own history."""
    Base.metadata.create_all(db_engine)
    _seed(db_session, start_time=datetime(2026, 3, 1, 18, 0, tzinfo=timezone.utc))
    _stale_pick(db_session)

    generate_and_store_picks(db_session, strategy_id=1, target_date=DAY,
                             skip_started=False)

    assert _the_pick(db_session).edge_pct == 99.0

"""Prop picks must reach grade_prop_pick, and carry a payout matching their odds.

Before this, the strategy loop sent every pick through grade_pick, which has no
prop branch. The PaperPick loop thirty lines below it has always branched
correctly -- same file, two conventions.
"""
import datetime

import pytest

from backend.models import (
    Base, Game, PickModel, PickResult, PlayerStat, StrategyModel, Team,
)
from backend.pipeline.scheduler import grade_pending_picks

GAME_DATE = datetime.date(2026, 5, 24)


def _seed(session, *, stat_value=None, odds=-200,
          pick_value="Dean Wade Over 0.5 3-Pointers",
          prop_market="player_threes", prop_player="Dean Wade"):
    Base.metadata.create_all(session.get_bind())
    session.add_all([
        Team(id=1, name="Cleveland Cavaliers", abbreviation="CLE", sport="nba"),
        Team(id=2, name="New York Knicks", abbreviation="NY", sport="nba"),
    ])
    session.flush()
    session.add(Game(id=1, sport="nba", season="2025-26", date=GAME_DATE,
                     home_team_id=1, away_team_id=2,
                     home_score=110, away_score=105, status="final"))
    session.add(StrategyModel(id=1, name="props", config_json="{}",
                              strategy_type="prop"))
    session.flush()
    session.add(PickModel(
        id=1, game_id=1, strategy_id=1, pick_type="prop",
        pick_value=pick_value, confidence=5, edge_pct=9.0, odds_at_pick=odds,
        prop_player=prop_player, prop_market=prop_market,
    ))
    if stat_value is not None:
        session.add(PlayerStat(
            player_name=prop_player, team_id=1, sport="nba",
            stat_type="game_log", game_date=GAME_DATE, threes=stat_value,
            source="espn",
            fetched_at=datetime.datetime.now(datetime.timezone.utc)))
    session.commit()


def test_a_prop_is_graded_against_the_player_box_score(db_session):
    """Two threes clears an Over 0.5 line."""
    _seed(db_session, stat_value=2.0)

    grade_pending_picks(db_session)

    result = db_session.query(PickResult).one()
    assert result.pick_id == 1
    assert result.result == "win"


def test_a_winning_prop_pays_its_own_odds_not_a_flat_unit(db_session):
    """`grade_prop_pick` returns ("win", 1.0) for every winner -- it never sees
    the odds. Storing that ratio in PickResult.payout would book a -200 winner
    as +1.00 units instead of +0.50 and inflate every ROI number computed from
    this table.
    """
    _seed(db_session, stat_value=2.0, odds=-200)

    grade_pending_picks(db_session)

    result = db_session.query(PickResult).one()
    assert result.payout == pytest.approx(0.5)      # 100/200, not 1.0


def test_a_losing_prop_books_minus_one_unit(db_session):
    """Zero threes does not clear Over 0.5."""
    _seed(db_session, stat_value=0.0)

    grade_pending_picks(db_session)

    result = db_session.query(PickResult).one()
    assert result.result == "loss"
    assert result.payout == pytest.approx(-1.0)


def test_a_prop_with_no_box_score_is_left_ungraded_rather_than_defaulted(db_session):
    """Missing data must not become a result. An absent PickResult is the
    honest outcome; a default is a measurement that never happened."""
    _seed(db_session, stat_value=None)

    grade_pending_picks(db_session)          # must not raise

    assert db_session.query(PickResult).count() == 0


def test_a_prop_missing_its_market_is_left_ungraded(db_session):
    """The backfill leaves prop_market NULL when it cannot resolve a label
    exactly. Those picks must stay ungraded rather than fall through to
    grade_pick, which would have refused anyway -- but via the wrong path."""
    _seed(db_session, stat_value=2.0, prop_market=None)

    grade_pending_picks(db_session)

    assert db_session.query(PickResult).count() == 0

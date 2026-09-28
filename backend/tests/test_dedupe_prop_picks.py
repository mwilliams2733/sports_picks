"""Removing the prop picks stored before d8191aa made them one per market.

The window runs re-inserted every prop and every alternate line was its
own pick: 6,980 prop rows for 480 (game, strategy, player, market) keys on
2026-09-28, each graded as a 1u wager. One row per key survives -- the
oldest, which is the row `_store_prop_picks` would have kept refreshing --
plus any row the digest emailed, whose record must not lose its pick.
"""
from datetime import date, datetime, timezone

import pytest
from sqlalchemy import create_engine

from backend.database import get_session
from backend.models import (Base, EmailedPick, Game, PickModel, PickResult,
                            Team)
from backend.scripts.dedupe_prop_picks import apply_plan, plan

DAY = date(2026, 9, 26)


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    s = get_session(engine)
    s.add_all([Team(id=1, name="H", abbreviation="H", sport="ncaaf"),
               Team(id=2, name="A", abbreviation="A", sport="ncaaf")])
    s.flush()
    s.add_all([Game(id=g, sport="ncaaf", season="2026", date=DAY,
                    status="final", home_team_id=1, away_team_id=2)
               for g in (1, 2)])
    s.commit()
    yield s
    s.close()


def _prop(s, pid, *, game=1, strategy=7, player="QB", market="player_pass_yds",
          line=205.5, graded=None):
    s.add(PickModel(id=pid, game_id=game, strategy_id=strategy,
                    pick_type="prop", pick_value=f"{player} Over {line} Pass Yards",
                    confidence=3, edge_pct=10.0, odds_at_pick=-110,
                    prop_player=player, prop_market=market,
                    created_at=datetime.now(timezone.utc)))
    s.flush()
    if graded:
        s.add(PickResult(pick_id=pid, result=graded,
                         payout=0.91 if graded == "win" else -1.0))
    s.commit()


def _ids(s, model=PickModel):
    col = model.id if model is PickModel else model.pick_id
    return sorted(i for (i,) in s.query(col))


def test_one_row_per_key_survives_and_it_is_the_oldest(session):
    for pid, line in ((5, 205.5), (3, 204.5), (9, 211.5)):
        _prop(session, pid, line=line, graded="loss")

    apply_plan(session, plan(session))

    assert _ids(session) == [3]


def test_a_deleted_picks_result_goes_with_it(session):
    """The duplicate results are the bankroll damage; they must go too."""
    _prop(session, 1, graded="win")
    _prop(session, 2, graded="loss")
    _prop(session, 3, graded="loss")

    apply_plan(session, plan(session))

    assert _ids(session, PickResult) == [1]


def test_an_emailed_pick_is_kept_even_when_it_is_not_the_oldest(session):
    _prop(session, 1)
    _prop(session, 2, line=206.5)
    session.add(EmailedPick(digest_date=DAY, pick_id=2, game_id=1, sport="ncaaf",
                            pick_type="prop", pick_value="QB Over 206.5 Pass Yards",
                            odds=-110))
    session.commit()

    apply_plan(session, plan(session))

    assert _ids(session) == [1, 2]
    assert _ids(session, EmailedPick) == [2]


def test_different_keys_are_left_alone(session):
    _prop(session, 1)
    _prop(session, 2, game=2)
    _prop(session, 3, strategy=8)
    _prop(session, 4, player="RB")
    _prop(session, 5, market="player_rush_yds")

    assert plan(session).delete == []


def test_game_picks_are_never_touched(session):
    for pid in (1, 2):
        session.add(PickModel(id=pid, game_id=1, strategy_id=7,
                              pick_type="moneyline", pick_value="HOME ML",
                              confidence=3, edge_pct=5.0, odds_at_pick=-110,
                              created_at=datetime.now(timezone.utc)))
    session.commit()

    assert plan(session).delete == []


def test_planning_alone_changes_nothing(session):
    """The default is a dry run."""
    _prop(session, 1, graded="win")
    _prop(session, 2, graded="loss")

    p = plan(session)

    assert p.delete == [2]
    assert _ids(session) == [1, 2]
    assert _ids(session, PickResult) == [1, 2]


def test_the_plan_reports_the_bankroll_it_would_leave(session):
    """What the fix is FOR, stated before anything is deleted."""
    _prop(session, 1, graded="win")     # kept: +0.91
    _prop(session, 2, graded="loss")    # deleted
    _prop(session, 3, graded="loss")    # deleted

    p = plan(session)

    assert p.bankroll_before == pytest.approx(100 + 0.91 - 2)
    assert p.bankroll_after == pytest.approx(100 + 0.91)

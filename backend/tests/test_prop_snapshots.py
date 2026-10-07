"""The append-only prop price history (`prop_snapshots`)."""
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine

from backend.analysis.prop_snapshots import prop_history, record_prop_fetch
from backend.database import get_session, run_migrations
from backend.models import Base, Game, PropSnapshot, Team

T0 = datetime(2026, 10, 20, 21, 0, tzinfo=timezone.utc)


@pytest.fixture
def session():
    s = get_session(create_engine("sqlite:///:memory:"))
    Base.metadata.create_all(s.get_bind())
    s.add_all([Team(id=1, name="H", abbreviation="H", sport="nba"),
               Team(id=2, name="A", abbreviation="A", sport="nba")])
    s.flush()
    s.add(Game(id=1, sport="nba", season="2026-27", date=date(2026, 10, 20),
               status="scheduled", home_team_id=1, away_team_id=2))
    s.commit()
    yield s
    s.close()


def _p(player="Role", line=12.5, odds=-110, market="player_points", outcome="Over", book="dk"):
    return dict(bookmaker=book, market=market, player_name=player, outcome=outcome,
                line=line, odds=odds)


def test_an_unchanged_price_extends_the_row_instead_of_appending(session):
    record_prop_fetch(session, 1, [_p()], now=T0)
    counts = record_prop_fetch(session, 1, [_p()], now=T0 + timedelta(hours=2))
    (row,) = session.query(PropSnapshot).all()
    assert counts["held"] == 1
    assert (row.captured_at.replace(tzinfo=timezone.utc), row.last_seen_at.replace(tzinfo=timezone.utc)) == \
        (T0, T0 + timedelta(hours=2))


def test_a_line_or_price_move_appends(session):
    record_prop_fetch(session, 1, [_p()], now=T0)
    record_prop_fetch(session, 1, [_p(line=14.5)], now=T0 + timedelta(hours=1))
    record_prop_fetch(session, 1, [_p(line=14.5, odds=-125)], now=T0 + timedelta(hours=2))
    record_prop_fetch(session, 1, [_p()], now=T0 + timedelta(hours=3))   # moved back
    hist = prop_history(session, 1, "Role", "player_points")
    assert [(r.line, r.odds) for r in hist] == [(12.5, -110), (14.5, -110), (14.5, -125), (12.5, -110)]


def test_a_dropped_player_in_a_still_quoted_market_is_a_pull(session):
    record_prop_fetch(session, 1, [_p("Star", 28.5), _p("Role")], now=T0)
    counts = record_prop_fetch(session, 1, [_p("Role", 14.5)], now=T0 + timedelta(minutes=90))
    assert counts["pulled"] == 1
    star = prop_history(session, 1, "Star", "player_points")
    assert [(r.line, r.odds) for r in star] == [(28.5, -110), (None, None)]


def test_a_market_missing_from_the_fetch_is_not_a_pull(session):
    """A partial response (no rebounds market at all) must not fake pulls."""
    record_prop_fetch(session, 1, [_p(), _p(market="player_rebounds", line=6.5)], now=T0)
    counts = record_prop_fetch(session, 1, [_p()], now=T0 + timedelta(hours=1))
    assert counts["pulled"] == 0


def test_a_pull_is_recorded_once_and_a_return_appends(session):
    record_prop_fetch(session, 1, [_p("Star", 28.5), _p("Role")], now=T0)
    record_prop_fetch(session, 1, [_p("Role")], now=T0 + timedelta(hours=1))
    again = record_prop_fetch(session, 1, [_p("Role")], now=T0 + timedelta(hours=2))
    assert again["pulled"] == 0                     # already recorded as pulled
    record_prop_fetch(session, 1, [_p("Star", 27.5), _p("Role")], now=T0 + timedelta(hours=3))
    assert [r.odds for r in prop_history(session, 1, "Star", "player_points")] == [-110, None, -110]


def test_books_are_separate_series(session):
    record_prop_fetch(session, 1, [_p(book="dk"), _p(book="fd", odds=-105)], now=T0)
    record_prop_fetch(session, 1, [_p(book="dk")], now=T0 + timedelta(hours=1))
    assert [r.odds for r in prop_history(session, 1, "Role", "player_points", bookmaker="fd")] == [-105]


def test_storing_props_records_the_history(session):
    from backend.pipeline.full_pipeline import _store_props
    _store_props(session, 1, [_p()])
    _store_props(session, 1, [_p(line=13.5)])
    assert [r.line for r in prop_history(session, 1, "Role", "player_points")] == [12.5, 13.5]


def test_the_table_is_created_by_migrations():
    from sqlalchemy import inspect
    engine = create_engine("sqlite:///:memory:")
    run_migrations(engine)
    assert "prop_snapshots" in inspect(engine).get_table_names()

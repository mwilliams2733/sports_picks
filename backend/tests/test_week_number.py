"""Guards for the `week` column.

It existed on the model and was NULL for all 1,893 games in the database,
though ESPN returns `week.number` on every event in a response the collector
already parses. Week is the natural unit for the football sports -- it is how
rest, bye weeks and schedule strength are actually discussed -- and nothing
could group by it.

ESPN omits the block entirely for sports without weeks (nba, mlb, ncaab
return None at both the event and scoreboard level), so the payload itself
says which sports have one. No allowlist is needed and none is used: an
absent week stays None rather than becoming 0, because a game in week zero
and a game in a sport with no weeks are not the same thing.
"""
import datetime

import pytest

from backend.collectors.espn import SPORT_URLS, week_of
from backend.database import get_engine, get_session
from backend.models import Base, Game, Team
from backend.pipeline.full_pipeline import fetch_and_store_games
from backend.tests.test_espn_game_identity import _event

pytestmark = pytest.mark.httpx_mock(
    assert_all_responses_were_requested=False,
    can_send_already_matched_responses=True,
)

NFL_SB = SPORT_URLS["nfl"]
DAY = datetime.date(2026, 9, 13)


def test_week_is_read_from_the_event(): 
    assert week_of({"week": {"number": 3}}) == 3


def test_a_sport_without_weeks_yields_none():
    """nba and mlb omit the block; week 0 would be a lie."""
    assert week_of({}) is None
    assert week_of({"week": {}}) is None
    assert week_of({"week": None}) is None


def test_a_non_numeric_week_is_refused_rather_than_guessed():
    assert week_of({"week": {"number": "nonsense"}}) is None


def _db(tmp_path):
    db = tmp_path / "wk.db"
    session = get_session(get_engine(str(db)))
    Base.metadata.create_all(session.get_bind())
    session.add_all([
        Team(id=1, name="BUF Team", abbreviation="BUF", sport="nfl"),
        Team(id=2, name="DET Team", abbreviation="DET", sport="nfl"),
    ])
    session.commit()
    session.close()
    return str(db)


def _scoreboard(httpx_mock, events, day=DAY):
    httpx_mock.add_response(
        url=f"{NFL_SB}?dates={day.strftime('%Y%m%d')}", json={"events": events})


def _game(db):
    session = get_session(get_engine(db))
    try:
        return session.query(Game).one()
    finally:
        session.close()


def _event_with_week(week, **kw):
    ev = _event("801", "BUF", "DET", "STATUS_FINAL", 31, 10,
                when="2026-09-14T00:00Z", **kw)
    if week is not None:
        ev["week"] = {"number": week}
    return ev


@pytest.mark.asyncio
async def test_a_stored_game_carries_its_week(tmp_path, httpx_mock):
    db = _db(tmp_path)
    _scoreboard(httpx_mock, [_event_with_week(2)])
    session = get_session(get_engine(db))
    await fetch_and_store_games(session, ["nfl"], DAY, reconcile=False)
    session.close()

    assert _game(db).week == 2


@pytest.mark.asyncio
async def test_a_row_stored_without_a_week_acquires_one(tmp_path, httpx_mock):
    """1,893 existing rows predate this, so seeing a game must fill it in --
    the same catch-up espn_id and season_type already do."""
    db = _db(tmp_path)
    session = get_session(get_engine(db))
    session.add(Game(id=9, sport="nfl", season="2026", date=DAY, espn_id="801",
                     home_team_id=1, away_team_id=2, status="scheduled",
                     week=None))
    session.commit()
    session.close()

    _scoreboard(httpx_mock, [_event_with_week(2)])
    session = get_session(get_engine(db))
    await fetch_and_store_games(session, ["nfl"], DAY, reconcile=False)
    session.close()

    assert _game(db).week == 2

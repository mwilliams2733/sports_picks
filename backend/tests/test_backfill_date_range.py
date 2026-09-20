"""Guards for the historical game backfill.

Collection began on 2026-09-17, so nfl was missing week 1 and ncaaf was
missing 08-24 through 09-16. The two dangerous outcomes are storing games
that carry none of the identity and venue the model now depends on, and
reconciling a past date -- which would mark real, already-stored games
`canceled` because ESPN's scoreboard for an old date came back thin.
"""
import datetime

import pytest

from backend.collectors.espn import SPORT_URLS
from backend.database import get_engine, get_session
from backend.models import Base, Game, Team
from backend.scripts.backfill_date_range import daterange, run
from backend.tests.test_espn_game_identity import _event

pytestmark = pytest.mark.httpx_mock(
    assert_all_responses_were_requested=False,
    can_send_already_matched_responses=True,
)

NFL_SB = SPORT_URLS["nfl"]
D1 = datetime.date(2026, 9, 11)
D2 = datetime.date(2026, 9, 12)


def _db(tmp_path, rows=()):
    db = tmp_path / "bf.db"
    session = get_session(get_engine(str(db)))
    Base.metadata.create_all(session.get_bind())
    session.add_all([
        Team(id=1, name="BUF Team", abbreviation="BUF", sport="nfl"),
        Team(id=2, name="DET Team", abbreviation="DET", sport="nfl"),
        Team(id=3, name="KC Team", abbreviation="KC", sport="nfl"),
        Team(id=4, name="PHI Team", abbreviation="PHI", sport="nfl"),
    ])
    session.flush()
    session.add_all(list(rows))
    session.commit()
    session.close()
    return str(db)


def _scoreboard(httpx_mock, day, events):
    """Pin the mock to the exact URL, so a wrong date would 404 rather than
    silently serve this payload and make the test vacuous."""
    httpx_mock.add_response(
        url=f"{NFL_SB}?dates={day.strftime('%Y%m%d')}", json={"events": events})


def _games(db, **filters):
    session = get_session(get_engine(db))
    try:
        q = session.query(Game)
        for k, v in filters.items():
            q = q.filter(getattr(Game, k) == v)
        return q.all()
    finally:
        session.close()


def test_daterange_is_inclusive_at_both_ends():
    assert daterange(D1, D2) == [D1, D2]
    assert daterange(D1, D1) == [D1]


def test_every_date_in_the_range_is_fetched(tmp_path, httpx_mock):
    db = _db(tmp_path)
    _scoreboard(httpx_mock, D1, [
        _event("801", "BUF", "DET", "STATUS_FINAL", 41, 31,
               when="2026-09-12T00:00Z")])
    _scoreboard(httpx_mock, D2, [
        _event("802", "KC", "PHI", "STATUS_FINAL", 27, 20,
               when="2026-09-13T00:00Z")])

    summary = run(db, sport="nfl", start=D1, end=D2)

    assert summary["stored"] == 2
    assert {g.espn_id for g in _games(db)} == {"801", "802"}


def test_a_backfilled_game_carries_identity_and_venue(tmp_path, httpx_mock):
    """It must go through the pipeline's own storage path.

    The older `backtesting.historical.store_games` drops espn_id and writes
    neither neutral_site nor season_type, so a backfill built on it would
    produce rows the odds matcher cannot find and the model cannot read.
    """
    db = _db(tmp_path)
    _scoreboard(httpx_mock, D1, [
        _event("801", "BUF", "DET", "STATUS_FINAL", 41, 31,
               when="2026-09-12T00:00Z", neutral=True, season_type=2)])

    run(db, sport="nfl", start=D1, end=D1)

    game = _games(db)[0]
    assert game.espn_id == "801"
    assert game.neutral_site is True
    assert game.season_type == "regular"
    assert (game.home_score, game.away_score) == (41, 31)
    assert game.status == "final"


def test_backfilling_twice_stores_each_game_once(tmp_path, httpx_mock):
    db = _db(tmp_path)
    _scoreboard(httpx_mock, D1, [
        _event("801", "BUF", "DET", "STATUS_FINAL", 41, 31,
               when="2026-09-12T00:00Z")])

    run(db, sport="nfl", start=D1, end=D1)
    second = run(db, sport="nfl", start=D1, end=D1)

    assert second["stored"] == 0
    assert len(_games(db)) == 1


def test_a_past_date_is_not_reconciled(tmp_path, httpx_mock):
    """Reconciliation on a backfill date would delete history, not add it.

    `_reconcile_against_espn` marks any pending row ESPN did not list as
    canceled. On an old date a thin scoreboard is the normal case, so
    reconciling would turn real scheduled games into canceled ones.
    """
    existing = Game(id=99, sport="nfl", season="2026-2027", date=D1,
                    espn_id="999", home_team_id=3, away_team_id=4,
                    status="scheduled")
    db = _db(tmp_path, [existing])
    # ESPN lists a different game entirely for that date.
    _scoreboard(httpx_mock, D1, [
        _event("801", "BUF", "DET", "STATUS_FINAL", 41, 31,
               when="2026-09-12T00:00Z")])

    run(db, sport="nfl", start=D1, end=D1)

    assert _games(db, id=99)[0].status == "scheduled"


def test_one_failed_date_is_reported_and_does_not_abort_the_rest(tmp_path, httpx_mock):
    """A 404 partway through 24 days must not discard the days that worked,
    and a partial run must be visibly partial rather than quietly short."""
    db = _db(tmp_path)
    httpx_mock.add_response(
        url=f"{NFL_SB}?dates={D1.strftime('%Y%m%d')}", status_code=404)
    _scoreboard(httpx_mock, D2, [
        _event("802", "KC", "PHI", "STATUS_FINAL", 27, 20,
               when="2026-09-13T00:00Z")])

    summary = run(db, sport="nfl", start=D1, end=D2)

    assert summary["stored"] == 1
    assert [day for day, _ in summary["failed"]] == [D1.isoformat()]
    assert {g.espn_id for g in _games(db)} == {"802"}

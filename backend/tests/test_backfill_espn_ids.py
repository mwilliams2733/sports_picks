"""Guards for the espn_id backfill.

It writes exactly one column. The dangerous outcome is assigning one game's
identity to another, which the ambiguity guard exists to prevent.
"""
import datetime

import pytest

from backend.database import get_engine, get_session
from backend.models import Base, Game, Team
from backend.scripts.backfill_espn_ids import dates_missing_ids, run
from backend.tests.test_espn_game_identity import NBA_SB, _event

pytestmark = pytest.mark.httpx_mock(
    assert_all_responses_were_requested=False,
    can_send_already_matched_responses=True,
)

DAY = datetime.date(2026, 3, 14)


def _db(tmp_path, rows):
    db = tmp_path / "ids.db"
    session = get_session(get_engine(str(db)))
    Base.metadata.create_all(session.get_bind())
    session.add_all([
        Team(id=1, name="MIA Team", abbreviation="MIA", sport="nba"),
        Team(id=2, name="ORL Team", abbreviation="ORL", sport="nba"),
        Team(id=3, name="LAL Team", abbreviation="LAL", sport="nba"),
    ])
    session.flush()
    session.add_all(rows)
    session.commit()
    session.close()
    return str(db)


def _game(gid, home, away, day=DAY, espn_id=None, status="final"):
    return Game(id=gid, sport="nba", season="2025-26", date=day, espn_id=espn_id,
                home_team_id=home, away_team_id=away, status=status,
                home_score=100, away_score=99)


def _fetch(db, gid, attr="espn_id"):
    session = get_session(get_engine(db))
    try:
        return getattr(session.query(Game).filter(Game.id == gid).one(), attr)
    finally:
        session.close()


def test_only_dates_with_a_missing_id_are_listed(tmp_path):
    db = _db(tmp_path, [
        _game(1, 1, 2),                                   # missing
        _game(2, 1, 3, day=datetime.date(2026, 4, 1), espn_id="x"),  # has one
    ])
    session = get_session(get_engine(db))
    try:
        assert dates_missing_ids(session) == [("nba", DAY)]
    finally:
        session.close()


def test_a_matched_row_gets_its_id_and_nothing_else_changes(tmp_path, httpx_mock):
    """Identity only. A date, status or score change here would be out of
    contract -- Task 2 changes dates, and it depends on this having run."""
    db = _db(tmp_path, [_game(1, 1, 2, status="scheduled")])
    for stamp in ("20260314", "20260313", "20260315"):
        httpx_mock.add_response(
            url=f"{NBA_SB}?dates={stamp}",
            json={"events": [_event("401700001", "MIA", "ORL", "STATUS_FINAL",
                                    117, 121)]} if stamp == "20260314"
            else {"events": []})

    run(db)

    assert _fetch(db, 1) == "401700001"
    assert _fetch(db, 1, "date") == DAY               # unchanged
    assert _fetch(db, 1, "status") == "scheduled"     # unchanged
    assert _fetch(db, 1, "home_score") == 100         # unchanged


def test_two_rows_with_the_same_teams_on_one_date_are_left_alone(tmp_path, httpx_mock):
    """The pre-espn_id key cannot tell them apart, so assigning an id to either
    would be a guess that permanently mislabels a game. Production has two
    such groups."""
    db = _db(tmp_path, [_game(1, 1, 2), _game(2, 1, 2)])
    httpx_mock.add_response(
        url=f"{NBA_SB}?dates=20260314",
        json={"events": [_event("401700001", "MIA", "ORL", "STATUS_FINAL",
                                117, 121)]})
    for stamp in ("20260313", "20260315"):
        httpx_mock.add_response(url=f"{NBA_SB}?dates={stamp}", json={"events": []})

    summary = run(db)

    assert _fetch(db, 1) is None
    assert _fetch(db, 2) is None
    assert summary["per_sport"]["nba"]["ambiguous"] == 2


def test_a_row_espn_does_not_list_keeps_a_null_id(tmp_path, httpx_mock):
    db = _db(tmp_path, [_game(1, 1, 3)])          # MIA vs LAL
    for stamp in ("20260314", "20260313", "20260315"):
        httpx_mock.add_response(
            url=f"{NBA_SB}?dates={stamp}",
            json={"events": [_event("401700001", "MIA", "ORL", "STATUS_FINAL",
                                    117, 121)]})

    summary = run(db)

    assert _fetch(db, 1) is None
    assert summary["per_sport"]["nba"]["no_match"] == 1


def test_dry_run_makes_no_requests_and_writes_nothing(tmp_path):
    """No stubs registered: any request would fail the test."""
    db = _db(tmp_path, [_game(1, 1, 2)])

    summary = run(db, dry_run=True)

    assert _fetch(db, 1) is None
    assert summary["dates"] == 1
    assert summary["per_sport"] == {}


def test_run_refuses_a_db_path_that_does_not_exist(tmp_path):
    with pytest.raises(FileNotFoundError):
        run(str(tmp_path / "nope.db"))


def test_a_row_stored_under_the_wrong_date_convention_is_still_matched(
        tmp_path, httpx_mock):
    """The reason the search covers the day's neighbours.

    A row stored on 2026-03-15 (the UTC date of an 8pm ET game) has its ESPN
    event listed under dates=20260314. Asking only about the stored date finds
    nothing, which is precisely the rows this backfill exists to identify.
    """
    utc_day = datetime.date(2026, 3, 15)
    db = _db(tmp_path, [_game(1, 1, 2, day=utc_day, status="scheduled")])

    # ESPN lists the event on the EASTERN day only.
    httpx_mock.add_response(
        url=f"{NBA_SB}?dates=20260314",
        json={"events": [_event("401700001", "MIA", "ORL", "STATUS_FINAL",
                                117, 121)]})
    for stamp in ("20260315", "20260316"):
        httpx_mock.add_response(url=f"{NBA_SB}?dates={stamp}", json={"events": []})

    summary = run(db)

    assert _fetch(db, 1) == "401700001", (
        "only the +/-1 day search can identify a row stored under the other "
        "date convention")
    assert summary["per_sport"]["nba"]["matched"] == 1
    assert _fetch(db, 1, "date") == utc_day        # identity only; date unchanged


def test_it_works_against_a_database_that_predates_the_column(tmp_path):
    """Every real target lacks games.espn_id until the migration runs. Without
    running it the script dies with "no such column: games.espn_id"."""
    import sqlite3
    db = tmp_path / "legacy.db"
    con = sqlite3.connect(db)
    con.execute(
        "CREATE TABLE games ("
        " id INTEGER PRIMARY KEY, sport VARCHAR NOT NULL, season VARCHAR NOT NULL,"
        " date DATE NOT NULL, home_team_id INTEGER NOT NULL,"
        " away_team_id INTEGER NOT NULL, status VARCHAR NOT NULL)")
    con.execute("INSERT INTO games VALUES (1,'nba','2025-26','2026-03-14',1,2,'scheduled')")
    con.commit()
    con.close()

    summary = run(str(db), dry_run=True)      # must not raise

    assert summary["dates"] == 1

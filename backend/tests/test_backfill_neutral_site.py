"""Guards for the neutral_site backfill.

The column decides whether a game teaches the model a home advantage, so the
dangerous outcomes are writing True onto a hosted game and silently leaving a
neutral one as hosted because ESPN did not list it.
"""
import datetime

import pytest

from backend.database import get_engine, get_session
from backend.models import Base, Game, Team
from backend.scripts.backfill_neutral_site import dates_to_check, run
from backend.tests.test_espn_game_identity import NBA_SB, _event

pytestmark = pytest.mark.httpx_mock(
    assert_all_responses_were_requested=False,
    can_send_already_matched_responses=True,
)

DAY = datetime.date(2026, 3, 14)


def _db(tmp_path, rows):
    db = tmp_path / "neutral.db"
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


def _game(gid, espn_id, home=1, away=2, day=DAY, neutral=False):
    return Game(id=gid, sport="nba", season="2025-26", date=day,
                espn_id=espn_id, home_team_id=home, away_team_id=away,
                status="final", home_score=100, away_score=99,
                neutral_site=neutral)


def _neutral_of(db, gid):
    session = get_session(get_engine(db))
    try:
        return session.query(Game).filter(Game.id == gid).one().neutral_site
    finally:
        session.close()


def _scoreboard(httpx_mock, events, day=DAY):
    """Pin the mock to the exact URL, so a wrong date would 404 rather than
    silently serve this payload and make the test vacuous."""
    stamp = day.strftime("%Y%m%d")
    httpx_mock.add_response(url=f"{NBA_SB}?dates={stamp}",
                            json={"events": events})


def test_a_neutral_game_is_marked(tmp_path, httpx_mock):
    db = _db(tmp_path, [_game(1, "401")])
    _scoreboard(httpx_mock, [
        _event("401", "MIA", "ORL", "STATUS_FINAL", 100, 99, neutral=True)])

    run(db, sports=("nba",))

    assert _neutral_of(db, 1) is True


def test_a_hosted_game_is_left_hosted(tmp_path, httpx_mock):
    db = _db(tmp_path, [_game(1, "401")])
    _scoreboard(httpx_mock, [
        _event("401", "MIA", "ORL", "STATUS_FINAL", 100, 99, neutral=False)])

    run(db, sports=("nba",))

    assert _neutral_of(db, 1) is False


def test_a_row_wrongly_marked_neutral_is_corrected_back(tmp_path, httpx_mock):
    """The backfill is not write-once: ESPN is authoritative in both directions."""
    db = _db(tmp_path, [_game(1, "401", neutral=True)])
    _scoreboard(httpx_mock, [
        _event("401", "MIA", "ORL", "STATUS_FINAL", 100, 99, neutral=False)])

    run(db, sports=("nba",))

    assert _neutral_of(db, 1) is False


def test_a_row_espn_does_not_list_is_left_alone_and_counted(tmp_path, httpx_mock):
    """Never guess. An unlisted row keeps its value and is reported.

    Two unlisted rows with OPPOSITE starting values. With only one, "left
    alone" and "guessed that same value" are indistinguishable and the test
    would pass against a script that guesses.
    """
    db = _db(tmp_path, [_game(1, "998", neutral=True),
                        _game(2, "999", home=1, away=3, neutral=False)])
    _scoreboard(httpx_mock, [
        _event("401", "MIA", "ORL", "STATUS_FINAL", 100, 99, neutral=False)])
    # Unmatched rows send the script to the neighbouring days; they are empty
    # too, so both rows stay unmatched.
    for d in (datetime.date(2026, 3, 13), datetime.date(2026, 3, 15)):
        _scoreboard(httpx_mock, [], day=d)

    summary = run(db, sports=("nba",))

    assert _neutral_of(db, 1) is True
    assert _neutral_of(db, 2) is False
    assert summary["per_sport"]["nba"]["no_match"] == 2


def test_matching_is_by_espn_id_not_by_teams_and_date(tmp_path, httpx_mock):
    """Two rows on one date are told apart by id, with no ambiguity guard needed."""
    db = _db(tmp_path, [_game(1, "401"), _game(2, "402", home=1, away=3)])
    _scoreboard(httpx_mock, [
        _event("401", "MIA", "ORL", "STATUS_FINAL", 100, 99, neutral=True),
        _event("402", "MIA", "LAL", "STATUS_FINAL", 100, 99, neutral=False)])

    run(db, sports=("nba",))

    assert _neutral_of(db, 1) is True
    assert _neutral_of(db, 2) is False


def test_dry_run_reports_the_change_but_writes_nothing(tmp_path, httpx_mock):
    """Unlike backfill_espn_ids, a dry run here still fetches.

    A dry run that skipped the request could only say how many rows it would
    look at, which is not a preview of anything.
    """
    db = _db(tmp_path, [_game(1, "401")])
    _scoreboard(httpx_mock, [
        _event("401", "MIA", "ORL", "STATUS_FINAL", 100, 99, neutral=True)])

    summary = run(db, dry_run=True, sports=("nba",))

    assert summary["per_sport"]["nba"]["changed"] == 1
    assert _neutral_of(db, 1) is False, "dry run wrote to the database"

    # And the same call without --dry-run does write, so the assertion above
    # is about the flag rather than about the fetch having failed.
    run(db, sports=("nba",))
    assert _neutral_of(db, 1) is True


def test_an_absent_neutralSite_field_is_treated_as_hosted(tmp_path, httpx_mock):
    """Some feeds omit it. Default to hosted rather than guessing."""
    db = _db(tmp_path, [_game(1, "401", neutral=True)])
    _scoreboard(httpx_mock, [
        _event("401", "MIA", "ORL", "STATUS_FINAL", 100, 99)])  # no neutral kwarg

    run(db, sports=("nba",))

    assert _neutral_of(db, 1) is False


def test_every_row_is_checked_not_only_the_false_ones(tmp_path):
    """False is both the default and a real value, so it cannot be a filter."""
    db = _db(tmp_path, [_game(1, "401", neutral=False),
                        _game(2, "402", day=datetime.date(2026, 4, 1),
                              neutral=True)])
    session = get_session(get_engine(db))
    try:
        assert dates_to_check(session, ("nba",)) == [
            ("nba", DAY), ("nba", datetime.date(2026, 4, 1))]
    finally:
        session.close()


def test_a_row_without_an_espn_id_is_not_checked(tmp_path):
    db = _db(tmp_path, [_game(1, None)])
    session = get_session(get_engine(db))
    try:
        assert dates_to_check(session, ("nba",)) == []
    finally:
        session.close()


def test_run_refuses_a_db_path_that_does_not_exist(tmp_path):
    with pytest.raises(FileNotFoundError):
        run(str(tmp_path / "nope.db"))


def test_a_row_stored_under_the_wrong_date_convention_is_still_matched(
    tmp_path, httpx_mock
):
    """ESPN files by Eastern date; a naive UTC parse lands late games a day on.

    The row's stored date then looks up a scoreboard that does not contain
    it. Neighbouring days are consulted only for rows the exact date missed,
    so the common case still costs one request.
    """
    db = _db(tmp_path, [_game(1, "401", day=datetime.date(2026, 3, 15))])
    # Nothing on the stored date...
    _scoreboard(httpx_mock, [], day=datetime.date(2026, 3, 15))
    # ...but ESPN files it under the day before.
    _scoreboard(httpx_mock, [
        _event("401", "MIA", "ORL", "STATUS_FINAL", 100, 99, neutral=True)],
        day=datetime.date(2026, 3, 14))
    _scoreboard(httpx_mock, [], day=datetime.date(2026, 3, 16))

    summary = run(db, sports=("nba",))

    assert _neutral_of(db, 1) is True
    assert summary["per_sport"]["nba"].get("no_match", 0) == 0


def test_neighbours_are_not_fetched_when_the_exact_date_matches(
    tmp_path, httpx_mock
):
    """The common case must stay at one request per date.

    Registering only the exact date means a neighbour fetch would raise on
    an unmatched request rather than pass silently.
    """
    db = _db(tmp_path, [_game(1, "401")])
    _scoreboard(httpx_mock, [
        _event("401", "MIA", "ORL", "STATUS_FINAL", 100, 99, neutral=True)])

    run(db, sports=("nba",))

    assert len(httpx_mock.get_requests()) == 1


def test_a_row_no_neighbour_lists_is_still_left_alone(tmp_path, httpx_mock):
    db = _db(tmp_path, [_game(1, "999", neutral=True)])
    for d in (datetime.date(2026, 3, 13), DAY, datetime.date(2026, 3, 15)):
        _scoreboard(httpx_mock, [], day=d)

    summary = run(db, sports=("nba",))

    assert _neutral_of(db, 1) is True
    assert summary["per_sport"]["nba"]["no_match"] == 1

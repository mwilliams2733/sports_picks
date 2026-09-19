"""Guards for the one-off finals catch-up.

The dangerous outcome is not a crash: it is marking a played game 'canceled'
because its team pair failed to match, which hides the row this script exists
to rescue.

These tests drive `run()` itself against a temp-file database rather than
reproducing its orchestration, so a change to the `reconcile=False` it passes
is caught here.
"""
import datetime

import pytest

from backend.database import get_engine, get_session
from backend.models import Base, Game, Team
from backend.scripts.catch_up_finals import run, stuck_dates
from backend.tests.test_game_finalization import NBA_SB, _event

pytestmark = pytest.mark.httpx_mock(
    assert_all_responses_were_requested=False,
    can_send_already_matched_responses=True,
)

TODAY = datetime.date(2026, 9, 17)
STUCK_DAY = datetime.date(2026, 5, 1)


def _seeded_db(tmp_path):
    """A temp-file database with one stuck date and several rows that must be
    left alone. Returns its path."""
    db = tmp_path / "catchup.db"
    engine = get_engine(str(db))
    Base.metadata.create_all(engine)
    session = get_session(engine)
    session.add_all([
        Team(id=1, name="CLE", abbreviation="CLE", sport="nba"),
        Team(id=2, name="NY", abbreviation="NY", sport="nba"),
        Team(id=3, name="LAL", abbreviation="LAL", sport="nba"),
        Team(id=4, name="BOS", abbreviation="BOS", sport="nba"),
        Team(id=5, name="F1", abbreviation="F1", sport="mma"),
        Team(id=6, name="F2", abbreviation="F2", sport="mma"),
    ])
    session.flush()
    session.add_all([
        # stuck, in the past -- ESPN will list this one
        Game(id=1, sport="nba", season="2025-26", date=STUCK_DAY,
             home_team_id=1, away_team_id=2, status="scheduled"),
        # stuck, same date -- ESPN will NOT list it
        Game(id=2, sport="nba", season="2025-26", date=STUCK_DAY,
             home_team_id=3, away_team_id=4, status="scheduled"),
        # already final: its date must not be requested
        Game(id=3, sport="nba", season="2025-26", date=datetime.date(2026, 4, 1),
             home_team_id=1, away_team_id=2, status="final",
             home_score=100, away_score=90),
        # future: must not be requested
        Game(id=4, sport="nba", season="2026-27", date=datetime.date(2026, 10, 1),
             home_team_id=1, away_team_id=2, status="scheduled"),
        # out-of-scope sport
        Game(id=5, sport="mma", season="2026", date=STUCK_DAY,
             home_team_id=5, away_team_id=6, status="scheduled"),
    ])
    session.commit()
    session.close()
    return str(db)


def _status(db_path, game_id, attr="status"):
    session = get_session(get_engine(db_path))
    try:
        return getattr(session.query(Game).filter(Game.id == game_id).one(), attr)
    finally:
        session.close()


def _stub_espn(httpx_mock):
    """ESPN lists only game 1 for the stuck date.

    The neighbouring days are stubbed empty because the catch-up asks about
    them too: a row stored under the UTC date of an evening game sits a day
    after the date ESPN files it under.
    """
    httpx_mock.add_response(
        url=f"{NBA_SB}?dates=20260501",
        json={"events": [_event("CLE", "NY", "STATUS_FINAL", 110, 105,
                                when=STUCK_DAY)]})
    for stamp in ("20260430", "20260502"):
        httpx_mock.add_response(url=f"{NBA_SB}?dates={stamp}", json={"events": []})


def test_only_dates_with_a_stuck_past_game_are_requested(tmp_path):
    """Derived from the data, not a calendar: a date whose games are already
    final, a future date, and an out-of-scope sport all produce no request."""
    session = get_session(get_engine(_seeded_db(tmp_path)))
    try:
        assert stuck_dates(session, TODAY) == [("nba", STUCK_DAY)]
    finally:
        session.close()


def test_a_matched_game_is_finalized(tmp_path, httpx_mock):
    db = _seeded_db(tmp_path)
    _stub_espn(httpx_mock)

    summary = run(db, today=TODAY)

    assert _status(db, 1) == "final"
    assert summary["requests"] == 3   # the day plus its two neighbours
    # Counts cover PAST non-final games only, so the future game (id 4) is
    # correctly excluded: ids 1 and 2 before, id 2 alone after.
    assert summary["stuck_before"]["nba"] == 2
    assert summary["stuck_after"]["nba"] == 1


def test_an_unmatched_row_is_left_scheduled_not_canceled(tmp_path, httpx_mock):
    """The whole reason this script passes reconcile=False.

    ESPN lists game 1 and not game 2. With reconciliation on, game 2 -- a real
    game whose team pair simply did not match -- would be marked 'canceled',
    hiding it permanently.
    """
    db = _seeded_db(tmp_path)
    _stub_espn(httpx_mock)

    run(db, today=TODAY)

    assert _status(db, 1) == "final"
    assert _status(db, 2) == "scheduled", (
        "the catch-up cancelled a row it merely failed to match")


def test_a_future_game_and_an_out_of_scope_sport_are_untouched(tmp_path, httpx_mock):
    db = _seeded_db(tmp_path)
    _stub_espn(httpx_mock)

    run(db, today=TODAY)

    assert _status(db, 4) == "scheduled"     # future
    assert _status(db, 5) == "scheduled"     # mma


def test_dry_run_writes_nothing(tmp_path, httpx_mock):
    db = _seeded_db(tmp_path)
    _stub_espn(httpx_mock)

    summary = run(db, dry_run=True, today=TODAY)

    assert _status(db, 1) == "scheduled"
    assert summary["requests"] == 0
    assert summary["dates"] == 1


def test_run_refuses_a_db_path_that_does_not_exist(tmp_path):
    """A typo must not create an empty database and report a successful
    zero-game catch-up against it."""
    with pytest.raises(FileNotFoundError):
        run(str(tmp_path / "nope.db"))


def test_a_row_stored_under_the_wrong_date_convention_is_still_finalized(
        tmp_path, httpx_mock):
    """The UTC/ET residual, hit for real on production.

    Row 1603 sat on 2026-05-25 (the UTC date of an 8pm ET game) while ESPN
    files event 401873200 under 2026-05-24. Requesting only the stored date
    never returns the event, so 48 props could not be graded. The search must
    cover the day's neighbours, exactly as backfill_espn_ids does.
    """
    db = _seeded_db(tmp_path)
    # The row carries ESPN's id, as plan 014's backfill gives it. That is what
    # lets the neighbouring day's response find THIS row and correct its date
    # -- without an id the fallback still matches on the wrong date and would
    # insert a twin instead. Production's row 1603 has espn_id 401873200.
    session = get_session(get_engine(db))
    session.query(Game).filter(Game.id == 1).one().espn_id = "1"
    session.commit()
    session.close()

    # ESPN lists the game on the day BEFORE our stored date.
    httpx_mock.add_response(
        url=f"{NBA_SB}?dates=20260430",
        json={"events": [_event("CLE", "NY", "STATUS_FINAL", 110, 105,
                                when=datetime.date(2026, 4, 30))]})
    httpx_mock.add_response(url=f"{NBA_SB}?dates=20260501", json={"events": []})
    httpx_mock.add_response(url=f"{NBA_SB}?dates=20260502", json={"events": []})

    run(db, today=TODAY)

    assert _status(db, 1) == "final", (
        "a row stored under the other date convention was not finalized")
    assert _status(db, 1, "date") == datetime.date(2026, 4, 30), (
        "the stored date should have been corrected to ESPN's")

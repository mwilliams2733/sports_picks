"""ESPN files its scoreboard by Eastern date but timestamps events in UTC.

Taking `.date()` off the UTC datetime files every evening game a day late,
which is how the same game came to exist twice in this database -- once per
convention. Measured against the live API for 2026-03-14: all seven events
were returned under `dates=20260314`, and the three after 8pm ET
(ORL@MIA 00:00Z, DEN@LAL 00:30Z, SAC@LAC 02:30Z) carried the 15th.
"""
import datetime

from backend.time_utils import ET, et_date


def test_an_evening_game_is_dated_by_its_eastern_date_not_its_utc_date():
    assert et_date("2026-03-15T00:00Z") == datetime.date(2026, 3, 14)
    assert et_date("2026-03-15T02:30Z") == datetime.date(2026, 3, 14)


def test_an_afternoon_game_agrees_under_both_conventions():
    assert et_date("2026-03-14T17:00Z") == datetime.date(2026, 3, 14)
    assert et_date("2026-03-14T22:00Z") == datetime.date(2026, 3, 14)


def test_dst_boundary_is_handled_by_the_zone_not_a_fixed_offset():
    """ET is -5 in winter and -4 in summer. A fixed offset would mis-date
    every game for half the year."""
    assert et_date("2026-01-15T00:30Z") == datetime.date(2026, 1, 14)   # EST
    assert et_date("2026-07-15T03:30Z") == datetime.date(2026, 7, 14)   # EDT


def test_an_offset_timestamp_is_handled_as_well_as_a_z_suffix():
    """ESPN sends "Z" but the historical loader has seen explicit offsets."""
    assert et_date("2026-01-05T19:30:00+00:00") == datetime.date(2026, 1, 5)


def test_the_two_parsers_are_the_same_function():
    """full_pipeline and backtesting/historical each carried a byte-for-byte
    copy, so the same defect existed twice. Derive, do not duplicate."""
    from backend.backtesting import historical
    from backend.pipeline import full_pipeline
    assert full_pipeline.et_date is et_date
    assert historical.et_date is et_date


def test_there_is_one_ET_and_everything_shares_it():
    """ET was defined separately in digest/job.py and pipeline/scheduler.py."""
    from backend.digest import job
    from backend.pipeline import scheduler
    assert job.ET is ET
    assert scheduler.ET is ET

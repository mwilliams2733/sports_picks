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


# --------------------------------------------------------------------------
# "Today" must mean today in Eastern time.
#
# Games are stored under their Eastern date (et_date), but the pipeline asked
# `date.today()`, which is the machine's LOCAL date. This box runs Pacific,
# so between 21:00 and midnight PT -- midnight to 03:00 ET -- the two
# disagree and pick generation looks for the wrong day. Observed at 01:26 ET
# on 2026-09-20: 14 NFL games dated 09-20 were stored, and generation for
# "today" found none because locally it was still 09-19.
# --------------------------------------------------------------------------

import datetime

from backend.time_utils import ET, et_today


def test_et_today_is_the_eastern_date(monkeypatch):
    """Not the machine's date, and not UTC's."""
    import backend.time_utils as tu

    class _Fixed(datetime.datetime):
        @classmethod
        def now(cls, tz=None):
            # 02:00 ET on the 20th, which is 06:00 UTC and 23:00 PT on the 19th.
            return datetime.datetime(2026, 9, 20, 6, 0,
                                     tzinfo=datetime.timezone.utc).astimezone(tz)

    monkeypatch.setattr(tu, "datetime", _Fixed)
    assert et_today() == datetime.date(2026, 9, 20)


def test_et_today_rolls_over_at_eastern_midnight(monkeypatch):
    import backend.time_utils as tu

    class _Fixed(datetime.datetime):
        @classmethod
        def now(cls, tz=None):
            # 23:30 ET on the 19th = 03:30 UTC on the 20th.
            return datetime.datetime(2026, 9, 20, 3, 30,
                                     tzinfo=datetime.timezone.utc).astimezone(tz)

    monkeypatch.setattr(tu, "datetime", _Fixed)
    assert et_today() == datetime.date(2026, 9, 19)


def test_et_today_agrees_with_et_date_on_the_same_instant():
    """The two must not use different conventions for the same moment."""
    now = datetime.datetime.now(ET)
    from backend.time_utils import et_date
    assert et_today() == et_date(now.astimezone(datetime.timezone.utc)
                                 .strftime("%Y-%m-%dT%H:%M:%SZ"))

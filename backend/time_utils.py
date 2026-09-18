"""Shared time handling. One ``ET``, one ESPN date parser.

``ET`` was defined separately in ``digest/job.py`` and
``pipeline/scheduler.py``, and the ESPN date parser existed as a byte-for-byte
copy in ``pipeline/full_pipeline.py`` and ``backtesting/historical.py`` -- with
the same defect in both.
"""
from datetime import date, datetime
from zoneinfo import ZoneInfo

#: The timezone every US sports schedule in this project is expressed in.
ET = ZoneInfo("America/New_York")


def et_date(date_str: str) -> date:
    """The Eastern calendar date a UTC instant falls on.

    Named for the timezone rather than the source because both ESPN's
    ``event["date"]`` and the Odds API's ``commence_time`` go through it, and
    they are matched against each other.

    ESPN timestamps events in UTC but groups its scoreboard by Eastern date, so
    anything starting after 8pm ET carries the *next* UTC day. Taking
    ``.date()`` off the UTC datetime therefore files every evening game a day
    late -- which is how the same game came to exist twice in this database,
    once per convention, and why 13 pairs had to be merged.

    Measured against the live API for ``dates=20260314``: all seven events were
    returned under that date, and the three after 8pm ET (``00:00Z``,
    ``00:30Z``, ``02:30Z``) carried the 15th.

    ``ZoneInfo`` rather than a fixed offset: ET is -5 in winter and -4 in
    summer, so a constant would mis-date half the year.

    Note this is deliberately *not* what :func:`_parse_start_time` does. A
    start time is an instant and is correctly stored in UTC; only the calendar
    date a game is *filed under* is timezone-dependent.
    """
    return datetime.fromisoformat(date_str.replace("Z", "+00:00")).astimezone(ET).date()

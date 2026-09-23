"""The digest health check must tell three outcomes apart.

A check that only asks "did it send" cannot act on the answer. The three
states have different causes and different fixes:

* **sent** -- nothing to do.
* **empty** -- the digest ran and the selector found nothing. Either a
  genuinely quiet day, or the morning slate did not price anything in time.
  Those two look identical in the digest log and are separated by whether
  picks exist for that date NOW.
* **never ran** -- no digest line at all. The scheduler was down, or the
  8am scout failed before reaching the slate.

Conflating the last two is the specific mistake this file exists to stop.
"empty" is a working system reporting an honest nothing; "never ran" is a
broken one, and only the second is an alarm.
"""
import datetime

import pytest

from backend.scripts.check_digest import (Outcome, classify, digest_lines,
                                          slate_line)

TODAY = datetime.date(2026, 9, 23)

SENT = ("2026-09-23 11:00:02 INFO:backend.digest.job:"
        "Digest sent to 4 recipient(s)")
EMPTY = ("2026-09-23 11:00:02 INFO:backend.digest.job:"
         "Digest for 2026-09-23 is empty; nothing sent")
SLATE = ("2026-09-23 08:00:11 INFO:backend.pipeline.scheduler:"
         "Morning slate for 2026-09-23: mlb, nfl")
SLATE_EMPTY = ("2026-09-23 08:00:11 INFO:backend.pipeline.scheduler:"
               "Morning slate for 2026-09-23: nothing scheduled")
YESTERDAY_SENT = ("2026-09-22 11:00:02 INFO:backend.digest.job:"
                  "Digest sent to 4 recipient(s)")


# --- reading the log ------------------------------------------------------

def test_a_digest_line_for_today_is_found():
    assert digest_lines([SENT], TODAY) == [SENT]


def test_yesterdays_digest_line_is_not_todays():
    """The log is not rotated daily. Yesterday's success must not be read as
    today's -- that would report healthy every morning forever."""
    assert digest_lines([YESTERDAY_SENT], TODAY) == []


def test_an_empty_line_is_dated_by_its_message_not_its_timestamp():
    """`Digest for <date> is empty` carries the date it is about. A run just
    after midnight UTC would otherwise be filed under the wrong day."""
    assert digest_lines([EMPTY], TODAY) == [EMPTY]


def test_unrelated_lines_are_ignored():
    assert digest_lines(["INFO something else entirely"], TODAY) == []


def test_the_slate_line_is_found_and_its_sports_reported():
    assert slate_line([SLATE], TODAY) == "mlb, nfl"


def test_an_empty_slate_is_reported_as_such_not_as_missing():
    """"nothing scheduled" is an answer. None means the line never appeared,
    which is a different problem."""
    assert slate_line([SLATE_EMPTY], TODAY) == "nothing scheduled"


def test_a_missing_slate_line_is_none():
    assert slate_line([SENT], TODAY) is None


def test_yesterdays_slate_line_is_not_todays():
    assert slate_line([SLATE.replace("2026-09-23", "2026-09-22")], TODAY) is None


# --- classifying ----------------------------------------------------------

def test_a_sent_digest_is_healthy():
    result = classify([SLATE, SENT], TODAY, picks_now=10)

    assert result.outcome is Outcome.SENT
    assert result.ok is True


def test_no_digest_line_at_all_is_an_alarm():
    """The scheduler was down, or the scout died before the digest hour."""
    result = classify([], TODAY, picks_now=10)

    assert result.outcome is Outcome.NEVER_RAN
    assert result.ok is False


def test_empty_with_no_picks_even_now_is_a_quiet_day():
    """A working system reporting an honest nothing. Not an alarm."""
    result = classify([SLATE_EMPTY, EMPTY], TODAY, picks_now=0)

    assert result.outcome is Outcome.EMPTY_QUIET
    assert result.ok is True


def test_empty_but_picks_exist_now_means_the_slate_landed_too_late():
    """THE regression this whole line of work was about: picks written in
    the evening, hours after the 11:00 digest already ran."""
    result = classify([SLATE, EMPTY], TODAY, picks_now=14)

    assert result.outcome is Outcome.EMPTY_BUT_PICKS_EXIST
    assert result.ok is False


def test_a_missing_slate_line_is_called_out_even_when_the_digest_sent():
    """The digest can send off yesterday's leftovers while this morning's
    scout never ran. The slate line is the upstream signal."""
    result = classify([SENT], TODAY, picks_now=10)

    assert result.outcome is Outcome.SENT
    assert result.slate is None
    assert "slate" in result.detail.lower()


def test_the_detail_names_what_to_look_at():
    result = classify([], TODAY, picks_now=0)

    assert result.detail, "an alarm with no explanation is not actionable"


def test_the_exit_code_is_zero_only_when_ok():
    assert classify([SLATE, SENT], TODAY, picks_now=10).exit_code == 0
    assert classify([], TODAY, picks_now=0).exit_code != 0


# --- run before the digest hour -------------------------------------------

def test_before_the_digest_hour_is_not_an_alarm():
    """Run by hand at 02:00 ET, "no digest line yet" is the only possible
    answer and means nothing. Reporting it as a fault is how a check trains
    people to ignore it."""
    result = classify([], TODAY, picks_now=0, et_hour=2)

    assert result.outcome is Outcome.TOO_EARLY
    assert result.ok is True


def test_after_the_digest_hour_a_missing_line_is_still_an_alarm():
    """The other half: without it, TOO_EARLY could swallow every real
    failure."""
    result = classify([], TODAY, picks_now=0, et_hour=14)

    assert result.outcome is Outcome.NEVER_RAN
    assert result.ok is False


def test_the_boundary_is_the_configured_send_hour():
    """At exactly 11:00 the digest may not have finished; 11 is still early."""
    assert classify([], TODAY, picks_now=0,
                    et_hour=11).outcome is Outcome.TOO_EARLY
    assert classify([], TODAY, picks_now=0,
                    et_hour=12).outcome is Outcome.NEVER_RAN


def test_a_digest_that_already_sent_is_reported_even_when_early():
    """Early only excuses ABSENCE. A line that exists is real information
    whatever the clock says."""
    result = classify([SLATE, SENT], TODAY, picks_now=10, et_hour=2)

    assert result.outcome is Outcome.SENT

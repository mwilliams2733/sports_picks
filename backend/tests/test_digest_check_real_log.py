"""The digest health check must work on the log the scheduler actually writes.

It reported "never-ran" on every run but one from 2026-09-23 to 2026-10-03,
including days the digest went out (5 picks to 4 recipients at 11:00 ET on
2026-10-03). `digest_lines` only counted "Digest sent to N recipient(s)"
when the line STARTED with the date. The scheduler logs through
`logging.basicConfig`'s default format, which has no timestamp:

    INFO:backend.digest.sender:Digest sent to 4 recipient(s)

so a sent digest could never match. The unit tests passed because their
fixtures invented a timestamped format production never writes. Every line
here is copied from the real scheduler.log of 2026-10-03, or follows that
exact format.

The fix dates the line by its message, as the "empty" line already was:
the job logs ``Digest for <date> sent to N recipient(s)``. The check also
reads scheduler.log.prev, because a restart between the 11:00 send and the
09:18-local check moves the line there. That happened on 2026-10-03.
"""
import datetime
import logging
from datetime import date

from backend.scripts.check_digest import Outcome, classify, digest_lines, main

TODAY = date(2026, 10, 3)

# Verbatim from logs-archive/scheduler.20261003-0854.log.
REAL_SLATE = "INFO:__main__:Morning slate for 2026-10-03: ncaaf, mlb\n"
REAL_UNDATED_SENT = "INFO:backend.digest.sender:Digest sent to 4 recipient(s)\n"
# The same format, as backend/digest/job.py now writes it.
DATED_SENT = "INFO:backend.digest.job:Digest for 2026-10-03 sent to 4 recipient(s)\n"


def test_a_dated_sent_line_in_the_real_format_is_todays():
    assert digest_lines([REAL_SLATE, REAL_UNDATED_SENT, DATED_SENT], TODAY) == [DATED_SENT]


def test_the_real_log_with_a_dated_sent_line_is_healthy():
    result = classify([REAL_SLATE, REAL_UNDATED_SENT, DATED_SENT], TODAY, picks_now=39)

    assert result.outcome is Outcome.SENT
    assert result.ok is True


def test_yesterdays_dated_sent_line_is_not_todays():
    """The log is not rotated daily, so the date in the message must match."""
    yesterday = DATED_SENT.replace("2026-10-03", "2026-10-02")

    assert classify([REAL_SLATE, yesterday], TODAY, picks_now=39).outcome is Outcome.NEVER_RAN


def test_an_undated_sent_line_cannot_vouch_for_today():
    """Nothing in it says which day it was. Counting it would report
    yesterday's send as today's."""
    assert digest_lines([REAL_UNDATED_SENT], TODAY) == []


# -- the job writes the dated line ------------------------------------------

def _job(monkeypatch, caplog, *, enabled=True):
    from backend.database import get_engine
    from backend.digest.job import send_daily_digest
    from backend.models import Base
    from backend.tests.test_digest_sender import (FROZEN_SEND_BAR, WIRING_SEASONS,
                                                  _seed_wiring_nfl_game)
    engine = get_engine(":memory:")
    Base.metadata.create_all(engine)
    d = date(2026, 9, 20)
    _seed_wiring_nfl_game(engine, d, [("HOME ML", 6.0, -120, 0.6)])
    monkeypatch.setattr("backend.digest.job.send_email", lambda *a, **k: True)
    monkeypatch.setattr("backend.digest.job.record_emailed", lambda *a, **k: 1)
    caplog.set_level(logging.INFO)
    cfg = {"seasons": WIRING_SEASONS,
           "digest": {"enabled": enabled, "sports": ["nfl"], "send_bar": FROZEN_SEND_BAR,
                      "from": "a@b.c", "recipients": ["d@e.f", "g@h.i"]}}
    return send_daily_digest(cfg, engine, target_date=d)


def test_a_real_send_logs_the_dated_line(monkeypatch, caplog):
    result = _job(monkeypatch, caplog)

    assert result["sent"] is True
    assert "Digest for 2026-09-20 sent to 2 recipient(s)" in caplog.text


def test_a_dry_run_does_not_log_it(monkeypatch, caplog):
    """A dry run reached nobody, so it must not read as sent."""
    _job(monkeypatch, caplog, enabled=False)

    assert "sent to 2 recipient(s)" not in caplog.text


def test_the_dated_line_the_job_writes_is_the_one_the_check_reads(monkeypatch, caplog):
    """Derived, not duplicated: feed the job's own log output to the check."""
    _job(monkeypatch, caplog)
    lines = [f"{r.levelname}:{r.name}:{r.getMessage()}\n" for r in caplog.records]

    assert classify(lines, date(2026, 9, 20), picks_now=1).outcome is Outcome.SENT


# -- a restart before the check ---------------------------------------------

def test_the_check_also_reads_the_rotated_log(tmp_path):
    """start_scheduler.ps1 moves scheduler.log to .prev on every restart."""
    from backend.database import get_engine
    from backend.models import Base
    db = tmp_path / "s.db"
    Base.metadata.create_all(get_engine(str(db)))
    log = tmp_path / "scheduler.log"
    log.write_text("INFO:__main__:Scheduler started\n", encoding="utf-8")
    (tmp_path / "scheduler.log.prev").write_text(REAL_SLATE + DATED_SENT, encoding="utf-8")

    code = main(["--log", str(log), "--db", str(db), "--date", "2026-10-03",
                 "--health-log", ""])

    assert code == 0

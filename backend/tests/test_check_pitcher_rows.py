"""The pitcher-rows health check must not miss a phantom 0.5.

Plan 022 made a real pitcher landing on exactly 0.5 impossible (it needs an
ERA of exactly 4.00 AND a K/9 of exactly 8.5). Before it, four rows sat at
0.5; after it, zero. So any row stored at exactly 0.5 today means the
producer has started substituting a neutral value again -- the specific
regression this file exists to catch, and it must outrank every other
outcome: rows existing does not make a 0.5 among them healthy.
"""
import datetime

import pytest

from backend.scripts.check_pitcher_rows import (Outcome, classify,
                                                 pitcher_lines)

TODAY = datetime.date(2026, 9, 23)

SCOUT_LINE = ("2026-09-23 08:00:15 INFO:backend.pipeline.scheduler:"
              "MLB pitcher scores: 14 game(s), 28 stat row(s) recorded")
SCOUT_LINE_ZERO = ("2026-09-23 08:00:15 INFO:backend.pipeline.scheduler:"
                    "MLB pitcher scores: 16 game(s), 0 stat row(s) recorded")
YESTERDAY_SCOUT_LINE = ("2026-09-22 08:00:15 INFO:backend.pipeline.scheduler:"
                        "MLB pitcher scores: 12 game(s), 24 stat row(s) recorded")

GOOD_ROWS = [0.62, 0.71, 0.55, 0.48, 0.33, 0.9, 0.1, 0.77]


# --- reading the log --------------------------------------------------------

def test_a_pitcher_line_for_today_is_found():
    assert pitcher_lines([SCOUT_LINE], TODAY) == [SCOUT_LINE]


def test_yesterdays_pitcher_line_is_not_todays_reader():
    assert pitcher_lines([YESTERDAY_SCOUT_LINE], TODAY) == []


def test_unrelated_lines_are_ignored():
    assert pitcher_lines(["INFO something else entirely"], TODAY) == []


# --- classifying -------------------------------------------------------------

def test_no_mlb_games_today_is_healthy():
    result = classify([], TODAY, mlb_games_today=0, rows_today=[])

    assert result.outcome is Outcome.NO_MLB_TODAY
    assert result.ok is True


def test_rows_written_is_healthy():
    result = classify([SCOUT_LINE], TODAY, mlb_games_today=14,
                      rows_today=GOOD_ROWS)

    assert result.outcome is Outcome.ROWS_WRITTEN
    assert result.ok is True


def test_a_row_at_exactly_one_half_is_an_alarm():
    """THE regression this whole check exists to catch."""
    rows = GOOD_ROWS + [0.5]
    result = classify([SCOUT_LINE], TODAY, mlb_games_today=14, rows_today=rows)

    assert result.outcome is Outcome.AMBIGUOUS_HALVES
    assert result.ok is False
    assert "022" in result.detail


def test_ambiguous_halves_outranks_rows_written():
    """Rows exist AND one is 0.5 -- the alarm must win, not the happy path."""
    rows = GOOD_ROWS + [0.5]
    result = classify([SCOUT_LINE], TODAY, mlb_games_today=14, rows_today=rows)

    assert result.outcome is Outcome.AMBIGUOUS_HALVES
    assert result.outcome is not Outcome.ROWS_WRITTEN


def test_no_scout_line_with_games_scheduled_is_an_alarm():
    result = classify([], TODAY, mlb_games_today=14, rows_today=[])

    assert result.outcome is Outcome.SCOUT_NEVER_RAN
    assert result.ok is False


def test_a_scout_line_reporting_zero_rows_is_not_an_alarm():
    result = classify([SCOUT_LINE_ZERO], TODAY, mlb_games_today=16,
                      rows_today=[])

    assert result.outcome is Outcome.NO_STARTERS_ANNOUNCED
    assert result.ok is True


def test_yesterdays_scout_line_is_not_todays():
    """A log line dated yesterday must not be read as today's evidence --
    games are scheduled today, so this must classify as never-ran."""
    result = classify([YESTERDAY_SCOUT_LINE], TODAY, mlb_games_today=14,
                      rows_today=[])

    assert result.outcome is Outcome.SCOUT_NEVER_RAN
    assert result.ok is False


def test_the_health_log_gets_one_entry_per_run(tmp_path, monkeypatch):
    import backend.scripts.check_pitcher_rows as mod

    log_path = tmp_path / "scheduler.log"
    log_path.write_text(SCOUT_LINE + "\n", encoding="utf-8")
    health_path = tmp_path / "pitcher_health.log"

    monkeypatch.setattr(mod, "mlb_games_today", lambda db, date: 14)
    monkeypatch.setattr(mod, "pitcher_rows_for", lambda db, date: GOOD_ROWS)

    argv = ["--log", str(log_path), "--db", "unused.db",
            "--date", TODAY.isoformat(), "--health-log", str(health_path)]

    mod.main(argv)
    mod.main(argv)

    text = health_path.read_text(encoding="utf-8")
    assert text.count("=====") == 4  # one open + one date-stamp per entry, two entries
    assert len([l for l in text.splitlines() if l.startswith("=====")]) == 2

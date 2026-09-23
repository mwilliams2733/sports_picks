"""Rotating the digest health log without losing the history it exists for.

`start_scheduler.ps1` rotates `scheduler.log` to `scheduler.log.prev` and
keeps exactly one generation, so every restart DESTROYS the previous .prev
-- usually the log covering whatever is being investigated. That has already
cost a session, and it is the pattern this deliberately does not copy.

The point of a health record is answering "has this failed before?", which
needs more than the current file. So rotation keeps `KEEP` generations and
only ever deletes the oldest.
"""
import pytest

from backend.scripts.check_digest import (KEEP, MAX_BYTES, append_entry,
                                          rotate)


def _write(path, size):
    path.write_text("x" * size, encoding="utf-8")


def test_a_small_log_is_not_rotated(tmp_path):
    log = tmp_path / "health.log"
    _write(log, 10)

    rotate(str(log), max_bytes=100, keep=3)

    assert log.read_text(encoding="utf-8") == "x" * 10
    assert not (tmp_path / "health.log.1").exists()


def test_a_log_at_the_threshold_is_rotated(tmp_path):
    log = tmp_path / "health.log"
    _write(log, 100)

    rotate(str(log), max_bytes=100, keep=3)

    assert not log.exists(), "the current log should have been moved aside"
    assert (tmp_path / "health.log.1").read_text(encoding="utf-8") == "x" * 100


def test_an_existing_generation_shifts_down(tmp_path):
    log = tmp_path / "health.log"
    _write(log, 100)
    (tmp_path / "health.log.1").write_text("older", encoding="utf-8")

    rotate(str(log), max_bytes=100, keep=3)

    assert (tmp_path / "health.log.2").read_text(encoding="utf-8") == "older"
    assert (tmp_path / "health.log.1").read_text(encoding="utf-8") == "x" * 100


def test_the_middle_generations_survive(tmp_path):
    """THE test. scheduler.log's rotation destroys .prev every time; this
    must not, or the record cannot answer "did this break last week too"."""
    log = tmp_path / "health.log"
    _write(log, 100)
    for n, body in ((1, "gen1"), (2, "gen2")):
        (tmp_path / f"health.log.{n}").write_text(body, encoding="utf-8")

    rotate(str(log), max_bytes=100, keep=4)

    assert (tmp_path / "health.log.2").read_text(encoding="utf-8") == "gen1"
    assert (tmp_path / "health.log.3").read_text(encoding="utf-8") == "gen2"


def test_only_the_oldest_generation_is_dropped(tmp_path):
    log = tmp_path / "health.log"
    _write(log, 100)
    for n in (1, 2, 3):
        (tmp_path / f"health.log.{n}").write_text(f"gen{n}", encoding="utf-8")

    rotate(str(log), max_bytes=100, keep=3)

    # gen3 was the oldest kept and falls off; gen1 and gen2 shift down.
    assert (tmp_path / "health.log.2").read_text(encoding="utf-8") == "gen1"
    assert (tmp_path / "health.log.3").read_text(encoding="utf-8") == "gen2"
    assert not (tmp_path / "health.log.4").exists(), "kept more than KEEP"


def test_rotating_a_missing_log_is_a_no_op(tmp_path):
    rotate(str(tmp_path / "nothing.log"), max_bytes=100, keep=3)

    assert not (tmp_path / "nothing.log.1").exists()


# --- appending ------------------------------------------------------------

def test_an_entry_is_appended_not_overwritten(tmp_path):
    log = tmp_path / "health.log"

    append_entry(str(log), "first")
    append_entry(str(log), "second")

    body = log.read_text(encoding="utf-8")
    assert "first" in body and "second" in body


def test_an_entry_carries_a_timestamp_and_exit_code(tmp_path):
    log = tmp_path / "health.log"

    append_entry(str(log), "body", exit_code=1)

    body = log.read_text(encoding="utf-8")
    assert "exit 1" in body
    assert "=====" in body, "entries need a separator to be readable"


def test_appending_creates_the_file(tmp_path):
    log = tmp_path / "sub" / "health.log"
    log.parent.mkdir()

    append_entry(str(log), "body")

    assert log.exists()


def test_appending_rotates_when_the_log_has_grown(tmp_path):
    """Rotation happens on write, so nothing else has to schedule it."""
    log = tmp_path / "health.log"
    _write(log, 200)

    append_entry(str(log), "fresh", max_bytes=100, keep=2)

    assert (tmp_path / "health.log.1").exists()
    assert "fresh" in log.read_text(encoding="utf-8")


def test_the_defaults_keep_more_than_one_generation():
    """The scheduler.log mistake in constant form."""
    assert KEEP > 1
    assert MAX_BYTES > 0

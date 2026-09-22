"""Guards for the season relabel script.

The 598 rows relabelled when `config.season_label` landed were relabelled by
hand, which meant a backup restore or a second environment had no way to
reproduce them. The rule then changed again -- a date before the configured
start now belongs to the season about to begin, not the one that ended --
so rows written under the old rule need it applied a second time.

It recomputes rather than rewriting the old strings, because the stored
label is exactly the value that cannot be trusted.
"""
import datetime

import pytest
import yaml

from backend.database import get_engine, get_session
from backend.models import Base, Game, Team
from backend.scripts.backfill_season_labels import format_summary, run

SEASONS = {"seasons": {
    "nfl": {"start": "09-05", "end": "02-10"},
    "nba": {"start": "10-22", "end": "06-20"},
    "mlb": {"start": "03-27", "end": "10-31"},
}}


@pytest.fixture
def config_path(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text(yaml.safe_dump(SEASONS), encoding="utf-8")
    return str(p)


def _db(tmp_path, rows):
    """`rows` are (sport, date, stored_label) triples."""
    db = str(tmp_path / "labels.db")
    session = get_session(get_engine(db))
    Base.metadata.create_all(session.get_bind())
    session.add_all([
        Team(id=1, name="A", abbreviation="A", sport="nfl"),
        Team(id=2, name="B", abbreviation="B", sport="nfl"),
    ])
    for i, (sport, day, label) in enumerate(rows, start=1):
        session.add(Game(id=i, sport=sport, season=label, date=day,
                         home_team_id=1, away_team_id=2, status="final"))
    session.commit()
    session.close()
    return db


def _labels(db):
    session = get_session(get_engine(db))
    try:
        return {g.id: g.season for g in session.query(Game).order_by(Game.id).all()}
    finally:
        session.close()


def test_every_written_format_converges_on_one(tmp_path, config_path):
    """The three that were in production: "2026-2027", "2026" and "2026-27"."""
    db = _db(tmp_path, [
        ("nfl", datetime.date(2026, 9, 20), "2026-2027"),
        ("nfl", datetime.date(2026, 9, 20), "2026"),
        ("nfl", datetime.date(2026, 9, 20), "2026-27"),
    ])
    summary = run(db, config_path=config_path)
    assert set(_labels(db).values()) == {"2026-27"}
    assert summary["per_sport"]["nfl"]["changed"] == 2
    assert summary["per_sport"]["nfl"]["already_correct"] == 1


def test_a_preseason_row_written_under_the_old_rule_is_corrected(
        tmp_path, config_path):
    """The reason it runs a second time."""
    db = _db(tmp_path, [
        ("nfl", datetime.date(2026, 8, 15), "2025-26"),
        ("nba", datetime.date(2026, 10, 5), "2025-26"),
    ])
    run(db, config_path=config_path)
    assert set(_labels(db).values()) == {"2026-27"}


def test_a_dry_run_writes_nothing_but_still_reports(tmp_path, config_path):
    db = _db(tmp_path, [("nfl", datetime.date(2026, 9, 20), "2026-2027")])
    summary = run(db, dry_run=True, config_path=config_path)
    assert _labels(db) == {1: "2026-2027"}
    assert summary["per_sport"]["nfl"]["changed"] == 1
    assert "DRY RUN" in format_summary(summary, dry_run=True)


def test_it_can_be_limited_to_one_sport(tmp_path, config_path):
    db = _db(tmp_path, [
        ("nfl", datetime.date(2026, 9, 20), "2026-2027"),
        ("nba", datetime.date(2025, 11, 1), "2025-2026"),
    ])
    run(db, sports=("nfl",), config_path=config_path)
    assert _labels(db) == {1: "2026-27", 2: "2025-2026"}


def test_running_it_twice_changes_nothing_the_second_time(tmp_path, config_path):
    db = _db(tmp_path, [("nfl", datetime.date(2026, 8, 15), "2025-26")])
    run(db, config_path=config_path)
    summary = run(db, config_path=config_path)
    assert summary["per_sport"]["nfl"].get("changed", 0) == 0
    assert _labels(db) == {1: "2026-27"}

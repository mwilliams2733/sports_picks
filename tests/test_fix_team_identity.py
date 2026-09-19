"""The ncaab team-identity repair, against a real sqlite file.

A real file, not `sqlite://`, because the repair works through `sqlite3` and
`PRAGMA foreign_key_list` -- the schema is the thing under test.
"""

import sqlite3
from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.models import Base, EloHistory, Game, Team, TeamStat
from backend.scripts.fix_team_identity import classify, fk_children, repair


@pytest.fixture()
def db(tmp_path):
    path = tmp_path / "t.db"
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as s:
        s.add_all([
            # A correct row, and a display-name row that collides with it.
            Team(id=1, name="Pennsylvania", abbreviation="PENN", sport="ncaab"),
            Team(id=2, name="Pennsylvania Quakers",
                 abbreviation="Pennsylvania Quakers", sport="ncaab"),
            # A display-name row whose target abbreviation is free.
            Team(id=3, name="Yale Bulldogs", abbreviation="Yale Bulldogs",
                 sport="ncaab"),
            Team(id=4, name="Duke", abbreviation="DUKE", sport="ncaab"),
        ])
        s.add_all([
            Game(id=10, sport="ncaab", season="2026", date=date(2026, 1, 5),
                 home_team_id=2, away_team_id=4, status="scheduled"),
            Game(id=11, sport="ncaab", season="2026", date=date(2026, 1, 6),
                 home_team_id=3, away_team_id=4, status="scheduled"),
        ])
        s.add(TeamStat(team_id=2, game_id=10, stat_type="pts", value=70.0))
        s.add(EloHistory(team_id=2, game_id=10, sport="ncaab", rating=1500.0))
        s.commit()
    return str(path)


@pytest.fixture()
def con(db):
    c = sqlite3.connect(db)
    c.execute("PRAGMA foreign_keys = ON")
    yield c
    c.close()


def test_fk_children_discovers_every_team_reference(con):
    found = set(fk_children(con.cursor(), "teams"))
    # Discovered from the schema, not hardcoded -- games references teams TWICE.
    assert ("games", "home_team_id") in found
    assert ("games", "away_team_id") in found
    assert ("team_stats", "team_id") in found
    assert ("elo_history", "team_id") in found


def test_classify_splits_rename_from_merge(con):
    buckets = classify(con.cursor(), "ncaab")
    assert [p.team_id for p in buckets["merge"]] == [2]
    assert buckets["merge"][0].target_team_id == 1
    assert [p.team_id for p in buckets["rename"]] == [3]
    assert buckets["rename"][0].target_abbr == "YALE"


def test_classify_leaves_correct_rows_alone(con):
    buckets = classify(con.cursor(), "ncaab")
    touched = {p.team_id for v in buckets.values() for p in v}
    assert 1 not in touched and 4 not in touched


def test_merge_repoints_every_child_then_deletes_the_row(con):
    repair(con, "ncaab", apply=True)
    cur = con.cursor()
    assert cur.execute("SELECT COUNT(*) FROM teams WHERE id=2").fetchone()[0] == 0
    # The game, the stat and the elo row all moved to the surviving row.
    assert cur.execute(
        "SELECT home_team_id FROM games WHERE id=10").fetchone()[0] == 1
    assert cur.execute(
        "SELECT team_id FROM team_stats WHERE game_id=10").fetchone()[0] == 1
    assert cur.execute(
        "SELECT team_id FROM elo_history WHERE game_id=10").fetchone()[0] == 1


def test_rename_keeps_the_row_and_its_id(con):
    repair(con, "ncaab", apply=True)
    cur = con.cursor()
    assert cur.execute(
        "SELECT abbreviation FROM teams WHERE id=3").fetchone()[0] == "YALE"
    # A rename needs no repointing at all -- that is the point of keeping the id.
    assert cur.execute(
        "SELECT home_team_id FROM games WHERE id=11").fetchone()[0] == 3


def test_dry_run_writes_nothing(con):
    before = con.execute("SELECT COUNT(*) FROM teams").fetchone()[0]
    repair(con, "ncaab", apply=False)
    assert con.execute("SELECT COUNT(*) FROM teams").fetchone()[0] == before
    assert con.execute(
        "SELECT abbreviation FROM teams WHERE id=3").fetchone()[0] == "Yale Bulldogs"
    assert con.execute(
        "SELECT home_team_id FROM games WHERE id=10").fetchone()[0] == 2


def test_abbreviations_are_unique_after_repair(con):
    repair(con, "ncaab", apply=True)
    abbrs = [r[0] for r in con.execute(
        "SELECT abbreviation FROM teams WHERE sport='ncaab'")]
    # The invariant the in-memory Elo replay depends on.
    assert len(abbrs) == len(set(abbrs))


def test_unresolved_rows_are_left_untouched(con):
    con.execute("INSERT INTO teams (id, name, abbreviation, sport) "
                "VALUES (9, 'Springfield Isotopes', 'Springfield Isotopes', 'ncaab')")
    con.commit()
    result = repair(con, "ncaab", apply=True)
    assert con.execute(
        "SELECT abbreviation FROM teams WHERE id=9").fetchone()[0] \
        == "Springfield Isotopes"
    assert [p.team_id for p in result["buckets"]["unresolved"]] == [9]


def test_merge_that_would_create_a_duplicate_game_is_reported(con):
    """Two rows for one school can hold the same fixture twice."""
    # Team 1 already has the same fixture on the same date as team 2's game 10.
    con.execute("INSERT INTO games (id, sport, season, date, home_team_id, "
                "away_team_id, status) VALUES "
                "(12, 'ncaab', '2026', '2026-01-05', 1, 4, 'final')")
    con.commit()
    result = repair(con, "ncaab", apply=True)
    assert result["duplicate_games"], \
        "a collision must be reported, not silently created"


def test_no_duplicate_report_when_there_is_no_collision(con):
    """The collision check must not fire on the ordinary case."""
    result = repair(con, "ncaab", apply=True)
    assert result["duplicate_games"] == []


def test_refuses_a_sport_that_identifies_teams_by_name(con):
    with pytest.raises(ValueError, match="by name"):
        repair(con, "mma", apply=True)


def test_repair_is_idempotent(con):
    repair(con, "ncaab", apply=True)
    first = [r for r in con.execute(
        "SELECT id, abbreviation FROM teams ORDER BY id")]
    repair(con, "ncaab", apply=True)
    assert [r for r in con.execute(
        "SELECT id, abbreviation FROM teams ORDER BY id")] == first


def test_ident_rejects_a_name_the_schema_does_not_declare(con):
    from backend.scripts.fix_team_identity import ident, schema_names

    allowed = schema_names(con.cursor())
    assert ident("teams", allowed) == '"teams"'
    with pytest.raises(ValueError, match="not a table or column"):
        ident("teams_backup", allowed)


def test_ident_rejects_a_quoted_identifier(con):
    from backend.scripts.fix_team_identity import ident

    with pytest.raises(ValueError, match="refusing to interpolate"):
        ident('teams"; DROP TABLE teams; --')


def test_schema_names_includes_tables_and_columns(con):
    from backend.scripts.fix_team_identity import schema_names

    names = schema_names(con.cursor())
    assert {"teams", "games", "abbreviation", "home_team_id"} <= names

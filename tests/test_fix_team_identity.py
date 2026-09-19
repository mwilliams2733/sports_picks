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


# --- resolving the duplicate fixtures a merge reveals -------------------


@pytest.fixture()
def con_dupes(con):
    """After the merge, team 1 holds the same fixture twice.

    Shaped like production: the FINAL row carries the score, espn_id and
    derived stats; the SCHEDULED twin carries the picks and odds.
    """
    # Same teams and date as game 10, so this IS a duplicate fixture without
    # needing the team merge to run first -- these tests exercise the survivor
    # rule on its own. (Pointing it at team 1 instead would only become a
    # duplicate after the merge, and the group would be empty here.)
    con.execute("INSERT INTO games (id, sport, season, date, espn_id, "
                "home_team_id, away_team_id, home_score, away_score, status) "
                "VALUES (12, 'ncaab', '2026', '2026-01-05', '40185', "
                "2, 4, 86, 83, 'final')")
    con.execute("INSERT INTO team_stats (team_id, game_id, stat_type, value) "
                "VALUES (2, 12, 'pts', 86.0)")
    con.execute("INSERT INTO strategies (id, name, config_json, is_active, "
                "strategy_type) VALUES (1, 's', '{}', 1, 'game')")
    # created_at is NOT NULL with a *Python-side* SQLAlchemy default, which a
    # raw sqlite3 INSERT does not apply -- supply it explicitly.
    con.execute("INSERT INTO picks (game_id, strategy_id, pick_type, "
                "pick_value, confidence, edge_pct, created_at) "
                "VALUES (10, 1, 'spread', 'HOME', 3, 2.5, '2026-01-05 00:00:00')")
    con.commit()
    return con


def test_game_children_discovers_player_props(con_dupes):
    found = set(fk_children(con_dupes.cursor(), "games"))
    # The table a hand-written list missed on the Clippers repair.
    assert ("player_props", "game_id") in found
    assert ("picks", "game_id") in found
    assert ("odds", "game_id") in found


def test_the_final_row_survives_not_the_row_with_picks(con_dupes):
    """The survivor rule, and the one that matters.

    merge_duplicate_games.py keeps "the row with picks". Measured against
    production, that would choose the wrong row in 27 of 34 groups: the picks
    sit on the empty scheduled twin while the scores sit on the final row.
    """
    from backend.scripts.fix_team_identity import resolve_duplicate_games

    result = resolve_duplicate_games(con_dupes, "ncaab", apply=True)
    cur = con_dupes.cursor()

    assert cur.execute("SELECT COUNT(*) FROM games WHERE id=10").fetchone()[0] == 0
    surv = cur.execute(
        "SELECT status, home_score FROM games WHERE id=12").fetchone()
    assert surv == ("final", 86)
    # The pick moved onto the final row -- which is what makes it gradeable.
    assert cur.execute(
        "SELECT game_id FROM picks").fetchone()[0] == 12
    assert result["resolved"] == 1


def test_resolution_leaves_no_duplicate_fixtures(con_dupes):
    from backend.scripts.fix_team_identity import (
        _duplicate_games,
        resolve_duplicate_games,
    )

    resolve_duplicate_games(con_dupes, "ncaab", apply=True)
    assert _duplicate_games(con_dupes.cursor(), "ncaab") == []


def test_a_group_with_no_final_row_is_refused_not_guessed(con):
    from backend.scripts.fix_team_identity import resolve_duplicate_games

    # Two scheduled rows for one fixture: nothing distinguishes them.
    con.execute("INSERT INTO games (id, sport, season, date, home_team_id, "
                "away_team_id, status) VALUES "
                "(13, 'ncaab', '2026', '2026-01-05', 2, 4, 'scheduled')")
    con.commit()
    result = resolve_duplicate_games(con, "ncaab", apply=True)

    assert result["resolved"] == 0
    assert len(result["skipped"]) == 1
    assert con.execute("SELECT COUNT(*) FROM games WHERE id IN (10,13)").fetchone()[0] == 2


def test_a_group_with_two_final_rows_is_refused_not_guessed(con):
    from backend.scripts.fix_team_identity import resolve_duplicate_games

    con.execute("UPDATE games SET status='final', home_score=1, away_score=2 "
                "WHERE id=10")
    con.execute("INSERT INTO games (id, sport, season, date, home_team_id, "
                "away_team_id, home_score, away_score, status) VALUES "
                "(14, 'ncaab', '2026', '2026-01-05', 2, 4, 3, 4, 'final')")
    con.commit()
    result = resolve_duplicate_games(con, "ncaab", apply=True)

    assert result["resolved"] == 0
    assert len(result["skipped"]) == 1
    assert con.execute("SELECT COUNT(*) FROM games WHERE id IN (10,14)").fetchone()[0] == 2


def test_duplicate_resolution_dry_run_writes_nothing(con_dupes):
    from backend.scripts.fix_team_identity import resolve_duplicate_games

    resolve_duplicate_games(con_dupes, "ncaab", apply=False)
    cur = con_dupes.cursor()
    assert cur.execute("SELECT COUNT(*) FROM games WHERE id=10").fetchone()[0] == 1
    assert cur.execute("SELECT game_id FROM picks").fetchone()[0] == 10


def test_repair_resolves_duplicates_it_creates(con):
    """End to end: the merge reveals a twin, and the same run cleans it up."""
    # Team 1 (PENN) already holds the fixture that team 2's game 10 duplicates.
    con.execute("INSERT INTO games (id, sport, season, date, home_team_id, "
                "away_team_id, home_score, away_score, status) VALUES "
                "(12, 'ncaab', '2026', '2026-01-05', 1, 4, 86, 83, 'final')")
    con.commit()

    result = repair(con, "ncaab", apply=True)

    assert result["duplicates_resolved"] == 1
    from backend.scripts.fix_team_identity import _duplicate_games
    assert _duplicate_games(con.cursor(), "ncaab") == []
    # game 10's derived rows followed it onto the survivor.
    assert con.execute(
        "SELECT team_id FROM team_stats WHERE game_id=12").fetchone()[0] == 1


# --- date-offset twins (the UTC-vs-ET shape from plan 014) --------------


@pytest.fixture()
def con_offset(con):
    """A plan-014 twin: same teams, one day later, empty, no espn_id.

    ESPN files an evening tip under the UTC date, so the same fixture can
    exist twice a day apart. Only visible once the team rows merge.
    """
    con.execute("INSERT INTO games (id, sport, season, date, espn_id, "
                "home_team_id, away_team_id, home_score, away_score, status) "
                "VALUES (20, 'ncaab', '2026', '2026-01-04', '40199', "
                "2, 4, 115, 119, 'final')")
    # game 10 (2026-01-05, scheduled, no score, no espn_id) is its twin.
    con.commit()
    return con


def test_offset_twin_is_merged_into_the_final_row(con_offset):
    from backend.scripts.fix_team_identity import resolve_offset_twins

    result = resolve_offset_twins(con_offset, "ncaab", apply=True)
    cur = con_offset.cursor()
    assert result["resolved"] == 1
    assert cur.execute("SELECT COUNT(*) FROM games WHERE id=10").fetchone()[0] == 0
    assert cur.execute(
        "SELECT team_id FROM team_stats WHERE game_id=20").fetchone()[0] == 2


def test_two_final_rows_a_day_apart_are_left_alone(con):
    """A real back-to-back. Production has 47 pairs like this."""
    from backend.scripts.fix_team_identity import resolve_offset_twins

    con.execute("UPDATE games SET status='final', home_score=1, away_score=2, "
                "espn_id='A' WHERE id=10")
    con.execute("INSERT INTO games (id, sport, season, date, espn_id, "
                "home_team_id, away_team_id, home_score, away_score, status) "
                "VALUES (21, 'ncaab', '2026', '2026-01-04', 'B', "
                "2, 4, 3, 4, 'final')")
    con.commit()

    result = resolve_offset_twins(con, "ncaab", apply=True)
    assert result["resolved"] == 0
    assert con.execute(
        "SELECT COUNT(*) FROM games WHERE id IN (10,21)").fetchone()[0] == 2


def test_a_twin_carrying_an_espn_id_is_left_alone(con):
    """If the second row has its own espn_id it is its own fixture."""
    from backend.scripts.fix_team_identity import resolve_offset_twins

    con.execute("UPDATE games SET espn_id='OWN' WHERE id=10")
    con.execute("INSERT INTO games (id, sport, season, date, espn_id, "
                "home_team_id, away_team_id, home_score, away_score, status) "
                "VALUES (22, 'ncaab', '2026', '2026-01-04', 'X', "
                "2, 4, 115, 119, 'final')")
    con.commit()

    assert resolve_offset_twins(con, "ncaab", apply=True)["resolved"] == 0


def test_two_days_apart_is_not_a_twin(con):
    from backend.scripts.fix_team_identity import resolve_offset_twins

    con.execute("INSERT INTO games (id, sport, season, date, espn_id, "
                "home_team_id, away_team_id, home_score, away_score, status) "
                "VALUES (23, 'ncaab', '2026', '2026-01-03', 'Y', "
                "2, 4, 115, 119, 'final')")
    con.commit()
    assert resolve_offset_twins(con, "ncaab", apply=True)["resolved"] == 0


def test_offset_twin_dry_run_writes_nothing(con_offset):
    from backend.scripts.fix_team_identity import resolve_offset_twins

    resolve_offset_twins(con_offset, "ncaab", apply=False)
    assert con_offset.execute(
        "SELECT COUNT(*) FROM games WHERE id=10").fetchone()[0] == 1


def test_a_final_row_without_an_espn_id_is_not_trusted_as_survivor(con):
    """The espn_id is the evidence that the final row is ESPN's fixture.

    Without it we have a final row and an empty next-day row and no proof
    they are the same game, so the pair is left alone. Production has no
    case like this; the guard is deliberate conservatism, and without this
    test it was unenforced.
    """
    from backend.scripts.fix_team_identity import resolve_offset_twins

    # game 10 is 2026-01-05 scheduled/empty; add a final twin the day before
    # that carries NO espn_id.
    con.execute("INSERT INTO games (id, sport, season, date, "
                "home_team_id, away_team_id, home_score, away_score, status) "
                "VALUES (24, 'ncaab', '2026', '2026-01-04', 2, 4, 9, 8, 'final')")
    con.commit()

    assert resolve_offset_twins(con, "ncaab", apply=True)["resolved"] == 0
    assert con.execute(
        "SELECT COUNT(*) FROM games WHERE id IN (10,24)").fetchone()[0] == 2

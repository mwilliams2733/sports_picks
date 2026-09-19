"""The odds path must not invent teams whose abbreviation is a display name.

`_ensure_game_from_odds` already documents the intended rule -- "Only creates
new teams/games for sports without ESPN coverage (boxing, etc.)" -- but does
not enforce it. These tests make the docstring true.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.models import Base, Game, Team
from backend.pipeline.full_pipeline import _ensure_game_from_odds


def event(home, away, commence="2026-01-05T23:00:00Z"):
    return {"home_team": home, "away_team": away, "commence_time": commence}


@pytest.fixture()
def session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as s:
        yield s


def test_known_school_resolves_to_an_existing_row(session):
    session.add_all([
        Team(name="Pennsylvania", abbreviation="PENN", sport="ncaab"),
        Team(name="Duke", abbreviation="DUKE", sport="ncaab"),
    ])
    session.commit()

    # The existing lookup matches on Team.name only, so these labels miss it
    # and fall through to the creation branch -- which is the bug.
    _ensure_game_from_odds(
        session, "ncaab", event("Pennsylvania Quakers", "Duke Blue Devils"))

    assert session.query(Team).count() == 2, "must reuse PENN/DUKE, not add rows"
    game = session.query(Game).one()
    by_abbr = {t.abbreviation: t.id for t in session.query(Team).all()}
    assert game.home_team_id == by_abbr["PENN"]
    assert game.away_team_id == by_abbr["DUKE"]


def test_known_school_is_created_with_its_abbreviation(session):
    """No existing row: create one, but keyed by abbreviation, not display name."""
    _ensure_game_from_odds(
        session, "ncaab", event("Pennsylvania Quakers", "Duke Blue Devils"))

    abbrs = {t.abbreviation for t in session.query(Team).all()}
    assert abbrs == {"PENN", "DUKE"}


def test_unresolvable_school_creates_no_junk_team(session):
    _ensure_game_from_odds(
        session, "ncaab", event("Springfield Isotopes", "Shelbyville Atoms"))

    # A row we cannot match to ESPN can never get an espn_id, never finalise
    # and never grade. Refusing beats creating one that looks fine.
    assert session.query(Team).count() == 0
    assert session.query(Game).count() == 0


def test_one_unresolvable_side_creates_neither_team(session):
    """A game needs both sides. Half a game is worse than none."""
    session.add(Team(name="Duke", abbreviation="DUKE", sport="ncaab"))
    session.commit()

    _ensure_game_from_odds(
        session, "ncaab", event("Springfield Isotopes", "Duke Blue Devils"))

    assert session.query(Team).count() == 1, "no row created for the bad side"
    assert session.query(Game).count() == 0


def test_a_resolvable_first_side_is_not_left_behind(session):
    """Order matters: the bad side must be able to come SECOND.

    With the unresolvable team first, the function returns before it ever
    reaches the second side, so that test cannot tell a deferred add from an
    add-as-you-go. Here 'Duke Blue Devils' resolves and would be inserted
    first, leaving an orphan team row with no game behind it.
    """
    _ensure_game_from_odds(
        session, "ncaab", event("Duke Blue Devils", "Springfield Isotopes"))

    assert session.query(Team).count() == 0, "no orphan row for the good side"
    assert session.query(Game).count() == 0


def test_combat_sports_still_create_teams_from_fighter_names(session):
    # For mma/boxing the fighter's NAME is the identity. This path must stay.
    _ensure_game_from_odds(session, "mma", event("Jon Jones", "Stipe Miocic"))

    assert {t.abbreviation for t in session.query(Team).all()} == {
        "Jon Jones", "Stipe Miocic",
    }
    assert session.query(Game).count() == 1


def test_an_existing_game_is_not_duplicated_for_a_display_name_label(session):
    """The dedup check is gated on both teams resolving.

    This passed before the fix too, but for a bad reason: the first call
    created a junk row named 'Pennsylvania Quakers', which the second call's
    name lookup then matched. It is kept as a regression guard on the path
    that now resolves to PENN/DUKE instead -- the dedup check is skipped
    entirely whenever a side fails to resolve.
    """
    session.add_all([
        Team(name="Pennsylvania", abbreviation="PENN", sport="ncaab"),
        Team(name="Duke", abbreviation="DUKE", sport="ncaab"),
    ])
    session.commit()

    for _ in range(3):
        _ensure_game_from_odds(
            session, "ncaab", event("Pennsylvania Quakers", "Duke Blue Devils"))

    assert session.query(Game).count() == 1


def test_a_label_that_is_already_an_abbreviation_resolves(session):
    session.add_all([
        Team(name="Pennsylvania", abbreviation="PENN", sport="ncaab"),
        Team(name="Duke", abbreviation="DUKE", sport="ncaab"),
    ])
    session.commit()

    _ensure_game_from_odds(session, "ncaab", event("PENN", "DUKE"))

    assert session.query(Team).count() == 2
    assert session.query(Game).count() == 1


def test_a_sport_without_a_snapshot_still_refuses_junk(session, monkeypatch):
    """Adding a sport to ABBREVIATION_SPORTS without shipping its team table
    must refuse, not fall back to creating display-name rows.

    Originally written against nfl, which had no snapshot at the time. It
    does now (as do all five members), so the sport is faked instead --
    otherwise this test would quietly stop exercising the rule the moment a
    snapshot was added, which is exactly what happened.
    """
    from backend.pipeline import full_pipeline

    monkeypatch.setattr(
        full_pipeline, "ABBREVIATION_SPORTS",
        frozenset({"nba", "nfl", "ncaab", "ncaaf", "mlb", "quidditch"}),
    )
    _ensure_game_from_odds(
        session, "quidditch", event("Chudley Cannons", "Holyhead Harpies"))

    assert session.query(Team).count() == 0
    assert session.query(Game).count() == 0

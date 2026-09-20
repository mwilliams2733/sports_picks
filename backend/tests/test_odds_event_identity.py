"""A moved odds event is the same event.

The Odds API gives every event a stable id, which the collector has always
returned and which was discarded for want of a column. Games created from
odds were identified by (sport, date, teams) instead, so when the feed moved
an event's date it looked like a new fixture and a second row appeared.

The feed does move dates. It uses a placeholder for an undated event and
drifts it: one "Islam Makhachev vs Kamaru Usman" future accumulated three
rows -- 2026-12-31, 2027-07-01 and 2027-07-02 -- for a market the feed
currently dates 2027-07-02.
"""
import datetime

import pytest

from backend.database import get_engine, get_session
from backend.models import Base, Game, Team
from backend.pipeline.full_pipeline import _ensure_game_from_odds


@pytest.fixture()
def session(tmp_path):
    s = get_session(get_engine(str(tmp_path / "o.db")))
    Base.metadata.create_all(s.get_bind())
    return s


def _event(eid, home, away, when):
    return {"odds_api_id": eid, "home_team": home, "away_team": away,
            "commence_time": when}


def test_a_moved_event_updates_the_row_it_already_has(session):
    """The regression: this used to leave two rows for one market."""
    _ensure_game_from_odds(session, "mma", _event(
        "abc", "Islam Makhachev", "Kamaru Usman", "2026-12-31T22:00:00Z"))
    _ensure_game_from_odds(session, "mma", _event(
        "abc", "Islam Makhachev", "Kamaru Usman", "2027-07-02T02:00:00Z"))

    games = session.query(Game).all()
    assert len(games) == 1
    assert games[0].date == datetime.date(2027, 7, 1)   # ET of 02:00Z


def test_a_different_event_id_is_a_different_game(session):
    """Two genuinely separate bouts must not collapse into one."""
    _ensure_game_from_odds(session, "mma", _event(
        "one", "A Fighter", "B Fighter", "2026-10-01T20:00:00Z"))
    _ensure_game_from_odds(session, "mma", _event(
        "two", "A Fighter", "C Fighter", "2026-11-01T20:00:00Z"))
    assert session.query(Game).count() == 2


def test_the_id_is_stored_on_creation(session):
    _ensure_game_from_odds(session, "mma", _event(
        "abc", "A Fighter", "B Fighter", "2026-10-01T20:00:00Z"))
    assert session.query(Game).one().odds_api_id == "abc"


def test_a_row_predating_the_column_adopts_the_id(session):
    """So the next drift matches by id instead of inserting a twin."""
    session.add_all([Team(id=1, name="A Fighter", abbreviation="A Fighter",
                          sport="mma"),
                     Team(id=2, name="B Fighter", abbreviation="B Fighter",
                          sport="mma")])
    session.flush()
    session.add(Game(id=9, sport="mma", season="2026",
                     date=datetime.date(2026, 10, 1), status="scheduled",
                     home_team_id=1, away_team_id=2))
    session.commit()

    _ensure_game_from_odds(session, "mma", _event(
        "abc", "A Fighter", "B Fighter", "2026-10-01T20:00:00Z"))

    games = session.query(Game).all()
    assert len(games) == 1, "created a duplicate instead of adopting the row"
    assert games[0].odds_api_id == "abc"


def test_an_unchanged_event_is_left_alone(session):
    for _ in range(3):
        _ensure_game_from_odds(session, "mma", _event(
            "abc", "A Fighter", "B Fighter", "2026-10-01T20:00:00Z"))
    assert session.query(Game).count() == 1


def test_an_event_without_an_id_still_works(session):
    """Nothing should depend on the feed always supplying one."""
    ev = _event(None, "A Fighter", "B Fighter", "2026-10-01T20:00:00Z")
    _ensure_game_from_odds(session, "mma", ev)
    _ensure_game_from_odds(session, "mma", ev)
    assert session.query(Game).count() == 1

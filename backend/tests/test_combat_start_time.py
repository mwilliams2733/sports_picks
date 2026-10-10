"""Odds-feed combat games carry the feed's start time (2026-10-10).

MMA and boxing games are created from Odds API events and never had a
start_time, though every event has a commence_time. With no start time,
everything treated a fight as not yet started: price refreshes overwrote the
pre-fight price with in-play and settled ones (175 of 593 MMA odds rows were
written on or after the fight date, 21 at |moneyline| >= 1000), the closing
line was simply the last price seen, and paper bets stayed open during the
card. Only scheduled combat games are set; ESPN stays the authority for team
sports, and finished games are never touched.
"""
import datetime

import pytest

from backend.database import get_engine, get_session
from backend.models import Base, Game, Odds, Team
from backend.paper.pricing import open_for_betting
from backend.pipeline.full_pipeline import _ensure_game_from_odds, _store_odds


@pytest.fixture()
def session(tmp_path):
    s = get_session(get_engine(str(tmp_path / "c.db")))
    Base.metadata.create_all(s.get_bind())
    return s


def _event(eid, home, away, when, books=None):
    e = {"odds_api_id": eid, "home_team": home, "away_team": away, "commence_time": when}
    if books is not None:
        e["bookmakers"] = books
    return e


def _book(home, away):
    return [{"key": "dk", "moneyline_home": home, "moneyline_away": away,
             "spread_home": None, "spread_away": None, "over_under": None}]


def test_a_created_combat_game_gets_the_feeds_start_time(session):
    _ensure_game_from_odds(session, "mma", _event("e1", "A Fighter", "B Fighter", "2026-10-11T01:00:00Z"))
    assert session.query(Game).one().start_time == datetime.datetime(2026, 10, 11, 1, 0)


def test_a_moved_event_moves_its_start_time(session):
    _ensure_game_from_odds(session, "mma", _event("e1", "A Fighter", "B Fighter", "2026-12-31T22:00:00Z"))
    _ensure_game_from_odds(session, "mma", _event("e1", "A Fighter", "B Fighter", "2026-10-11T01:00:00Z"))
    g = session.query(Game).one()
    assert (g.date, g.start_time) == (datetime.date(2026, 10, 10), datetime.datetime(2026, 10, 11, 1, 0))


def test_an_existing_row_without_an_id_adopts_the_start_time(session):
    session.add_all([Team(id=1, name="A Fighter", abbreviation="A Fighter", sport="boxing"),
                     Team(id=2, name="B Fighter", abbreviation="B Fighter", sport="boxing")])
    session.flush()
    session.add(Game(sport="boxing", season="2026", date=datetime.date(2026, 10, 10),
                     home_team_id=1, away_team_id=2, status="scheduled"))
    session.commit()
    _ensure_game_from_odds(session, "boxing", _event("e9", "A Fighter", "B Fighter", "2026-10-11T01:00:00Z"))
    assert session.query(Game).one().start_time == datetime.datetime(2026, 10, 11, 1, 0)


def test_finished_and_team_sport_games_are_never_touched(session):
    session.add_all([Team(id=1, name="A Fighter", abbreviation="A Fighter", sport="mma"),
                     Team(id=2, name="B Fighter", abbreviation="B Fighter", sport="mma")])
    session.flush()
    session.add(Game(sport="mma", season="2026", date=datetime.date(2026, 10, 3), odds_api_id="done",
                     home_team_id=1, away_team_id=2, status="final", home_score=1, away_score=0))
    session.commit()
    _ensure_game_from_odds(session, "mma", _event("done", "A Fighter", "B Fighter", "2026-10-04T01:00:00Z"))
    assert session.query(Game).filter(Game.odds_api_id == "done").one().start_time is None


def test_after_the_start_the_price_is_frozen_and_betting_closes(session):
    past = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=30))
    when = past.strftime("%Y-%m-%dT%H:%M:%SZ")
    _ensure_game_from_odds(session, "mma", _event("live", "A Fighter", "B Fighter", when))
    game = session.query(Game).one()
    assert not open_for_betting(game)
    _store_odds(session, "mma", [_event("live", "A Fighter", "B Fighter", when, _book(-1500, 900))])
    assert session.query(Odds).count() == 0          # no in-play price written


def test_a_team_sport_keeps_espns_start_time(session):
    session.add_all([Team(id=1, name="Home Team", abbreviation="HOM", sport="nfl"),
                     Team(id=2, name="Away Team", abbreviation="AWY", sport="nfl")])
    session.flush()
    espn = datetime.datetime(2026, 10, 11, 17, 0)
    session.add(Game(sport="nfl", season="2026", date=datetime.date(2026, 10, 11), odds_api_id="nfl1",
                     home_team_id=1, away_team_id=2, status="scheduled", start_time=espn))
    session.commit()
    _ensure_game_from_odds(session, "nfl", _event("nfl1", "Home Team", "Away Team", "2026-10-11T20:25:00Z"))
    assert session.query(Game).one().start_time == espn

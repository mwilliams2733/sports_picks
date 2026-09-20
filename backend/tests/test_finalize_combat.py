"""Finalizing combat bouts from the odds feed's /scores endpoint.

Covers boxing, which has no other source at all -- ESPN answers "Invalid
sport (boxing)" -- and mma, where ESPN's UFC scoreboard misses the regional
promotions the odds feed carries.

The corner-order test is the one that matters. Our `home_team` is not
guaranteed to be the API's `home_team`, and getting it backwards writes a
reversed result that grades a real pick against the wrong fighter and feeds
a reversed Elo update. Nothing downstream can notice.
"""
import datetime

import pytest
from sqlalchemy import create_engine

from backend.database import get_session
from backend.models import Base, Game, Team
from backend.scripts.finalize_combat import finalize_from_scores, stuck_combat

TODAY = datetime.date(2026, 9, 20)


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return get_session(engine)


def _bout(session, day, home, away, sport="boxing"):
    def team(name):
        t = session.query(Team).filter(Team.sport == sport,
                                       Team.abbreviation == name).first()
        if t:
            return t
        t = Team(name=name, abbreviation=name, sport=sport)
        session.add(t)
        session.flush()
        return t

    g = Game(sport=sport, date=day, status="scheduled", season="2026",
             home_team_id=team(home).id, away_team_id=team(away).id)
    session.add(g)
    session.flush()
    return g


def _event(home, away, winner):
    loser = away if winner == home else home
    return {"completed": True, "home_team": home, "away_team": away,
            "scores": [{"name": winner, "score": "1"},
                       {"name": loser, "score": "0"}]}


def _stub(events):
    async def fetch(client, api_key, sport, days_from=3):
        from backend.collectors.odds_scores import bouts_from_scores
        return bouts_from_scores(events)
    return fetch


def test_a_stuck_bout_is_finalized(session, monkeypatch):
    g = _bout(session, TODAY - datetime.timedelta(days=1),
              "Abdullah Mason", "Joe Cordina")
    session.commit()
    monkeypatch.setattr("backend.scripts.finalize_combat.fetch_scores",
                        _stub([_event("Abdullah Mason", "Joe Cordina",
                                      "Abdullah Mason")]))

    summary = finalize_from_scores(session, "boxing", "KEY", today=TODAY)

    assert summary["finalized"] == 1
    assert g.status == "final"
    assert (g.home_score, g.away_score) == (1, 0)


def test_the_winner_is_oriented_by_name_not_by_corner(session, monkeypatch):
    """Our home fighter is the API's AWAY fighter here, and the API's away
    fighter won. The home column must record a loss."""
    g = _bout(session, TODAY - datetime.timedelta(days=1),
              "Joe Cordina", "Abdullah Mason")
    session.commit()
    monkeypatch.setattr("backend.scripts.finalize_combat.fetch_scores",
                        _stub([_event("Abdullah Mason", "Joe Cordina",
                                      "Abdullah Mason")]))

    finalize_from_scores(session, "boxing", "KEY", today=TODAY)

    assert (g.home_score, g.away_score) == (0, 1), (
        "our home fighter Cordina lost; recording a home win grades every "
        "pick on this bout backwards")


def test_a_bout_outside_the_three_day_window_is_untouched(session, monkeypatch):
    """/scores cannot return it, so asking about it is waste."""
    old = _bout(session, datetime.date(2026, 3, 21), "Old A", "Old B")
    session.commit()
    monkeypatch.setattr("backend.scripts.finalize_combat.fetch_scores",
                        _stub([_event("Old A", "Old B", "Old A")]))

    summary = finalize_from_scores(session, "boxing", "KEY", today=TODAY)

    assert old.status == "scheduled"
    assert summary["considered"] == 0


def test_an_unmatched_bout_stays_scheduled(session, monkeypatch):
    g = _bout(session, TODAY - datetime.timedelta(days=1), "Ours A", "Ours B")
    session.commit()
    monkeypatch.setattr("backend.scripts.finalize_combat.fetch_scores",
                        _stub([_event("Other X", "Other Y", "Other X")]))

    summary = finalize_from_scores(session, "boxing", "KEY", today=TODAY)

    assert g.status == "scheduled"
    assert summary["unmatched"] == 1


def test_dry_run_writes_nothing(session, monkeypatch):
    g = _bout(session, TODAY - datetime.timedelta(days=1), "A Fighter", "B Fighter")
    session.commit()
    monkeypatch.setattr("backend.scripts.finalize_combat.fetch_scores",
                        _stub([_event("A Fighter", "B Fighter", "A Fighter")]))

    summary = finalize_from_scores(session, "boxing", "KEY", today=TODAY,
                                   dry_run=True)

    assert summary["finalized"] == 1
    assert g.status == "scheduled"
    assert g.home_score is None


def test_no_api_key_makes_no_request(session, monkeypatch):
    """Without a key this must be a clean no-op, not a 401 traceback."""
    _bout(session, TODAY - datetime.timedelta(days=1), "A Fighter", "B Fighter")
    session.commit()

    def explode(*a, **k):
        raise AssertionError("must not reach the network without a key")

    monkeypatch.setattr("backend.scripts.finalize_combat.fetch_scores", explode)

    summary = finalize_from_scores(session, "boxing", None, today=TODAY)

    assert summary["finalized"] == 0
    assert summary["skipped_no_key"] is True


def test_stuck_combat_is_scoped_to_one_sport(session):
    _bout(session, TODAY - datetime.timedelta(days=1), "Boxer A", "Boxer B",
          sport="boxing")
    _bout(session, TODAY - datetime.timedelta(days=1), "Fighter A", "Fighter B",
          sport="mma")
    session.commit()

    assert len(stuck_combat(session, "boxing", TODAY)) == 1
    assert len(stuck_combat(session, "mma", TODAY)) == 1

"""A friend's paper bet must never be able to take down grading.

Before this, a paper bet was accepted with any integer odds. Clearing the
Odds box sends 0; when that bet won, ``calculate_payout(0)`` raised inside
``grade_pending_picks``, which the 08:00 scout called unguarded -- so the
scout aborted before pricing the slate and the digest went out empty, every
day, because the retries hit the same row.

Three guards, each tested here: placement refuses odds that are not a real
American price; grading books an unusable stored price at 0.0 instead of
raising (legacy rows exist); and the owner's /users/grade route runs the same
paper-grading code as the scheduler.
"""
import datetime

import pytest
from fastapi.testclient import TestClient

from backend.api.main import create_app
from backend.database import get_session
from backend.models import (
    ActivityFeed, Base, Game, PaperPick, Parlay, Team, UserProfile,
)
from backend.pipeline.paper_settlement import settle_parlays
from backend.pipeline.scheduler import grade_pending_picks
from backend.tests.auth_helpers import ALL_HEADERS, OWNER_HEADERS, TEST_PIN
from backend.time_utils import et_today

GAME_DATE = datetime.date(2026, 9, 20)


def _client():
    client = TestClient(create_app(":memory:"), headers=ALL_HEADERS)
    Base.metadata.create_all(client.app.state.engine)
    return client


def _scheduled_games(client, n):
    session = get_session(client.app.state.engine)
    ids = []
    for i in range(n):
        home = Team(name=f"H{i}", abbreviation=f"H{i}", sport="nba")
        away = Team(name=f"A{i}", abbreviation=f"A{i}", sport="nba")
        session.add_all([home, away])
        session.flush()
        game = Game(sport="nba", season="2025-26", date=et_today(),
                    home_team_id=home.id, away_team_id=away.id,
                    status="scheduled")
        session.add(game)
        session.flush()
        ids.append(game.id)
    session.commit()
    session.close()
    return ids


def _user(client, name="friend"):
    r = client.post("/users/", json={"name": name, "pin": TEST_PIN})
    assert r.status_code == 200
    return r.json()["id"]


# --- 1. placement refuses unusable odds ------------------------------------

BAD_ODDS = [0, 50, -50, 20000, -20000]


@pytest.mark.parametrize("odds", BAD_ODDS)
def test_a_single_bet_with_unusable_odds_is_refused(odds):
    client = _client()
    [gid] = _scheduled_games(client, 1)
    uid = _user(client)
    r = client.post(f"/users/{uid}/picks", json={
        "game_id": gid, "pick_type": "moneyline", "pick_value": "HOME ML",
        "odds": odds, "stake": 100})
    assert r.status_code == 422, r.text


@pytest.mark.parametrize("odds", BAD_ODDS)
def test_a_parlay_leg_with_unusable_odds_is_refused(odds):
    client = _client()
    g1, g2 = _scheduled_games(client, 2)
    uid = _user(client)
    r = client.post(f"/users/{uid}/parlay", json={"stake": 100, "legs": [
        {"game_id": g1, "pick_type": "moneyline", "pick_value": "HOME ML", "odds": -110},
        {"game_id": g2, "pick_type": "moneyline", "pick_value": "HOME ML", "odds": odds},
    ]})
    assert r.status_code == 422, r.text


@pytest.mark.parametrize("odds", [-100, 100, -10000, 10000, -110, 250])
def test_real_prices_are_still_accepted(odds):
    client = _client()
    [gid] = _scheduled_games(client, 1)
    uid = _user(client)
    r = client.post(f"/users/{uid}/picks", json={
        "game_id": gid, "pick_type": "moneyline", "pick_value": "HOME ML",
        "odds": odds, "stake": 100})
    assert r.status_code == 200, r.text


# --- 6. stake must be a finite positive number -----------------------------

@pytest.mark.parametrize("stake", ["NaN", "Infinity", "-Infinity"])
def test_a_non_finite_stake_is_refused(stake):
    client = _client()
    g1, g2 = _scheduled_games(client, 2)
    uid = _user(client)
    single = ('{"game_id": %d, "pick_type": "moneyline", "pick_value": "HOME ML",'
              ' "odds": -110, "stake": %s}' % (g1, stake))
    r = client.post(f"/users/{uid}/picks", content=single,
                    headers={"Content-Type": "application/json"})
    assert r.status_code == 422, r.text
    parlay = ('{"stake": %s, "legs": ['
              '{"game_id": %d, "pick_type": "moneyline", "pick_value": "HOME ML", "odds": -110},'
              '{"game_id": %d, "pick_type": "moneyline", "pick_value": "HOME ML", "odds": -110}]}'
              % (stake, g1, g2))
    r = client.post(f"/users/{uid}/parlay", content=parlay,
                    headers={"Content-Type": "application/json"})
    assert r.status_code == 422, r.text


# --- 2. grading never raises on stored odds --------------------------------

def _legacy(session, *, odds=0, parlay=False):
    """A final game the home side won, and a pick on it stored with ``odds``
    directly -- the way rows placed before validation existed look."""
    Base.metadata.create_all(session.get_bind())
    session.add_all([Team(id=1, name="Home", abbreviation="HOM", sport="nba"),
                     Team(id=2, name="Away", abbreviation="AWY", sport="nba"),
                     Team(id=3, name="Home2", abbreviation="HO2", sport="nba"),
                     Team(id=4, name="Away2", abbreviation="AW2", sport="nba")])
    session.add(UserProfile(id=1, name="legacy"))
    session.flush()
    session.add_all([
        Game(id=1, sport="nba", season="2025-26", date=GAME_DATE,
             home_team_id=1, away_team_id=2, home_score=110, away_score=100,
             status="final"),
        Game(id=2, sport="nba", season="2025-26", date=GAME_DATE,
             home_team_id=3, away_team_id=4, home_score=110, away_score=100,
             status="final"),
    ])
    session.flush()
    if parlay:
        session.add(Parlay(id=1, user_id=1, stake=100.0, combined_odds=264))
        session.flush()
        session.add_all([
            PaperPick(user_id=1, game_id=1, pick_type="moneyline",
                      pick_value="HOME ML", odds=odds, stake=0.0, parlay_id=1),
            PaperPick(user_id=1, game_id=2, pick_type="moneyline",
                      pick_value="HOME ML", odds=-110, stake=0.0, parlay_id=1),
        ])
    else:
        session.add(PaperPick(user_id=1, game_id=1, pick_type="moneyline",
                              pick_value="HOME ML", odds=odds, stake=100.0))
    session.commit()


def test_a_legacy_winning_bet_at_odds_zero_does_not_raise_in_grading(db_session):
    _legacy(db_session, odds=0)

    out = grade_pending_picks(db_session)

    pick = db_session.query(PaperPick).one()
    assert out["paper"] == 1
    assert pick.result == "win"
    assert pick.payout == 0.0


def test_a_legacy_parlay_leg_at_odds_zero_does_not_raise_in_settlement(db_session):
    _legacy(db_session, odds=0, parlay=True)
    for leg in db_session.query(PaperPick).all():
        leg.result, leg.payout = "win", 0.0
    db_session.commit()

    assert settle_parlays(db_session) == 1

    parlay = db_session.get(Parlay, 1)
    assert parlay.result == "win"
    # The unusable leg contributes nothing; the -110 leg pays 100/110.
    assert parlay.payout == pytest.approx(100 * (100 / 110))


def test_a_legacy_parlay_at_odds_zero_is_graded_and_settled_by_the_scheduler(db_session):
    _legacy(db_session, odds=0, parlay=True)

    out = grade_pending_picks(db_session)

    assert out["paper"] == 2
    assert out["parlays"] == 1


# --- 3. the owner route runs the scheduler's paper grading -----------------

def test_grade_route_survives_a_legacy_bet_at_odds_zero():
    client = _client()
    session = get_session(client.app.state.engine)
    _legacy(session, odds=0)
    session.close()

    r = client.post("/users/grade", headers=OWNER_HEADERS)

    assert r.status_code == 200, r.text
    assert r.json() == {"graded": 1, "parlays_settled": 0}


def test_grade_route_delegates_to_the_shared_paper_grader(monkeypatch):
    import backend.pipeline.paper_settlement as ps
    calls = []

    def fake(session):
        calls.append(session)
        return []

    monkeypatch.setattr(ps, "grade_paper_picks", fake)
    client = _client()

    r = client.post("/users/grade", headers=OWNER_HEADERS)

    assert r.status_code == 200
    assert len(calls) == 1


def test_the_scheduler_delegates_to_the_shared_paper_grader(db_session, monkeypatch):
    import backend.pipeline.paper_settlement as ps
    Base.metadata.create_all(db_session.get_bind())
    calls = []
    monkeypatch.setattr(ps, "grade_paper_picks",
                        lambda s: calls.append(s) or [])

    grade_pending_picks(db_session)

    assert calls == [db_session]


# --- 5. deleting a player who has a parlay ---------------------------------

def test_owner_can_delete_a_player_with_a_settled_parlay():
    client = _client()
    session = get_session(client.app.state.engine)
    _legacy(session, odds=-110, parlay=True)
    session.add(ActivityFeed(user_id=1, event_type="pick_won", payload="{}"))
    session.commit()
    session.close()
    assert client.post("/users/grade", headers=OWNER_HEADERS).json()[
        "parlays_settled"] == 1

    r = client.delete("/users/1", headers=OWNER_HEADERS)

    assert r.status_code == 200, r.text
    session = get_session(client.app.state.engine)
    assert session.query(PaperPick).count() == 0
    assert session.query(Parlay).count() == 0
    assert session.query(ActivityFeed).filter_by(user_id=1).count() == 0
    assert session.get(UserProfile, 1) is None
    session.close()

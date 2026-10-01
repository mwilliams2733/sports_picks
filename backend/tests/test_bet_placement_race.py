"""Two bets placed at once must not both pass the balance check.

place_pick/place_parlay read what the player has available, then insert
the bet. Two requests from one player -- a double-click, two tabs; sync
FastAPI endpoints run in a threadpool -- could each read the balance before
either committed, and both pass: a $10,000 bankroll staking $12,000.

The barrier below forces exactly that interleaving: each request waits at
the balance check until the other has reached it too. With the write lock
taken first, the second request cannot reach the check until the first has
committed, so the barrier times out and the second sees the first's stake.
"""
import threading

import pytest
from fastapi.testclient import TestClient

import backend.api.users as users_api
from backend.api.main import create_app
from backend.tests.auth_helpers import ALL_HEADERS
from backend.tests.test_api_users import _make_user, _seed_games


@pytest.fixture
def app(tmp_path):
    # A file database: the in-memory test engine shares one connection
    # across threads, which would hide the race rather than test it.
    return create_app(str(tmp_path / "race.db"))


def _race(app, monkeypatch, place):
    barrier = threading.Barrier(2, timeout=1.5)
    real = users_api.available_of

    def at_the_check(session, user):
        try:
            barrier.wait()
        except threading.BrokenBarrierError:
            pass
        return real(session, user)

    monkeypatch.setattr(users_api, "available_of", at_the_check)
    codes = []

    def go():
        codes.append(place(TestClient(app, headers=ALL_HEADERS)).status_code)

    threads = [threading.Thread(target=go) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=20)
    return sorted(codes)


def test_two_simultaneous_bets_cannot_overspend(app, monkeypatch):
    client = TestClient(app, headers=ALL_HEADERS)
    game_ids = _seed_games(client, [{"status": "scheduled"}])
    user_id = _make_user(client)

    codes = _race(app, monkeypatch, lambda c: c.post(f"/users/{user_id}/picks", json={
        "game_id": game_ids[0], "pick_type": "moneyline", "side": "HOME",
        "stake": 6000}))

    assert codes == [200, 400]
    assert client.get(f"/users/{user_id}").json()["available_balance"] == 4000.0


def test_two_simultaneous_parlays_cannot_overspend(app, monkeypatch):
    client = TestClient(app, headers=ALL_HEADERS)
    game_ids = _seed_games(client, [{"status": "scheduled"}, {"status": "scheduled"}])
    user_id = _make_user(client)

    codes = _race(app, monkeypatch, lambda c: c.post(f"/users/{user_id}/parlay", json={
        "stake": 6000,
        "legs": [{"game_id": g, "pick_type": "moneyline", "side": "HOME"}
                 for g in game_ids]}))

    assert codes == [200, 400]
    assert client.get(f"/users/{user_id}").json()["available_balance"] == 4000.0

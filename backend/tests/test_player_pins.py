"""A player's bets need their PIN. Five wrong PINs lock the player for 15
minutes; the lock is tested on an injected clock, not by waiting."""
import concurrent.futures
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from backend.api import pins
from backend.api.main import create_app
from backend.database import get_session
from backend.models import Base, Game, Team, UserProfile
from backend.tests.auth_helpers import OWNER_HEADERS
from backend.time_utils import et_today

BET = {"pick_type": "moneyline", "pick_value": "HOME ML", "odds": -110, "stake": 10}


def _setup(pin="1234", db_path=":memory:"):
    app = create_app(db_path)
    client = TestClient(app)
    s = get_session(app.state.engine)
    s.add_all([Team(id=1, name="H", abbreviation="H", sport="nba"),
               Team(id=2, name="A", abbreviation="A", sport="nba")])
    s.flush()
    s.add(Game(id=1, sport="nba", season="2026", date=et_today(), home_team_id=1,
               away_team_id=2, status="scheduled"))
    s.commit()
    s.close()
    uid = client.post("/users/", json={"name": "friend", "pin": pin}).json()["id"]
    return client, uid


def _bet(client, uid, pin):
    headers = {} if pin is None else {"X-Player-Pin": pin}
    return client.post(f"/users/{uid}/picks", json={**BET, "game_id": 1}, headers=headers)


def test_the_right_pin_places_the_bet():
    client, uid = _setup()
    assert _bet(client, uid, "1234").status_code == 200


def test_a_missing_or_wrong_pin_is_refused():
    client, uid = _setup()
    assert _bet(client, uid, None).status_code == 401
    assert _bet(client, uid, "9999").status_code == 401


def test_a_leading_zero_pin_works_as_typed():
    """Review Focus 2."""
    client, uid = _setup(pin="0123")
    assert _bet(client, uid, "123").status_code == 401
    assert _bet(client, uid, "0123").status_code == 200


def test_five_wrong_pins_lock_even_the_right_one_until_15_minutes_pass():
    clock = [datetime(2026, 9, 29, 12, 0, tzinfo=timezone.utc)]
    pins.guard.now = lambda: clock[0]
    client, uid = _setup()
    for _ in range(5):
        assert _bet(client, uid, "9999").status_code == 401
    assert _bet(client, uid, "1234").status_code == 429
    clock[0] += timedelta(minutes=14, seconds=59)
    assert _bet(client, uid, "1234").status_code == 429
    clock[0] += timedelta(seconds=2)
    assert _bet(client, uid, "1234").status_code == 200


def test_a_locked_player_gets_identical_responses_right_or_wrong_and_guesses_while_locked_do_not_extend_it():
    """Fix round 1, item 2: the lock must be checked before the PIN is ever
    compared, so a wrong and a right guess made while locked are
    indistinguishable from the response alone (no oracle for "was that
    close?"), and neither extends the lock -- only crossing MAX_FAILURES
    from a fresh state should ever set a new lock-until time."""
    clock = [datetime(2026, 9, 29, 12, 0, tzinfo=timezone.utc)]
    pins.guard.now = lambda: clock[0]
    client, uid = _setup()
    for _ in range(5):
        assert _bet(client, uid, "9999").status_code == 401

    wrong = _bet(client, uid, "0000")
    right = _bet(client, uid, "1234")
    assert wrong.status_code == 429
    assert right.status_code == 429
    assert wrong.json() == right.json()

    clock[0] += timedelta(minutes=10)
    assert _bet(client, uid, "9999").status_code == 429
    assert _bet(client, uid, "8888").status_code == 429

    clock[0] += timedelta(minutes=5, seconds=1)
    assert _bet(client, uid, "1234").status_code == 200


def test_a_concurrent_burst_of_wrong_pins_is_capped_at_max_failures(tmp_path):
    """Fix round 1, item 1: 30 simultaneous wrong PINs against a
    FILE-backed app (":memory:" SQLite was unreliable under this concurrent
    load) must not all reach the slow PBKDF2 compare before the lockout is
    recorded -- at most MAX_FAILURES may be evaluated at all (401); every
    other concurrent guess must be refused outright (429), never let
    through as an extra "free" guess."""
    client, uid = _setup(db_path=str(tmp_path / "t.db"))

    def _wrong_guess(_):
        return _bet(client, uid, "9999").status_code

    with concurrent.futures.ThreadPoolExecutor(max_workers=30) as pool:
        results = list(pool.map(_wrong_guess, range(30)))

    assert results.count(401) <= pins.MAX_FAILURES, results
    assert set(results) <= {401, 429}
    assert len(results) == 30


def test_the_pin_is_never_stored():
    client, uid = _setup(pin="482915")
    s = get_session(client.app.state.engine)
    u = s.get(UserProfile, uid)
    assert u.pin_hash and u.pin_salt
    assert "482915" not in (u.pin_hash + u.pin_salt)
    s.close()


def test_a_pinless_player_sets_a_pin_on_their_first_bet_then_needs_it():
    client, _ = _setup()
    s = get_session(client.app.state.engine)
    legacy = UserProfile(name="legacy")          # created before PINs existed
    s.add(legacy)
    s.commit()
    lid = legacy.id
    s.close()
    assert _bet(client, lid, "5555").status_code == 200
    assert _bet(client, lid, "6666").status_code == 401
    assert _bet(client, lid, "5555").status_code == 200


def test_joining_needs_a_4_to_6_digit_pin():
    client = TestClient(create_app(":memory:"))
    assert client.post("/users/", json={"name": "a"}).status_code == 422
    assert client.post("/users/", json={"name": "a", "pin": "12"}).status_code == 422
    assert client.post("/users/", json={"name": "a", "pin": "1234567"}).status_code == 422
    assert client.post("/users/", json={"name": "a", "pin": "12a4"}).status_code == 422
    assert client.post("/users/", json={"name": "a", "pin": "1234"}).status_code == 200


def test_names_are_unique_ignoring_case_and_spaces():
    """Review Focus 1."""
    client = TestClient(create_app(":memory:"))
    assert client.post("/users/", json={"name": "Marcus", "pin": "1234"}).status_code == 200
    assert client.post("/users/", json={"name": " marcus ", "pin": "1234"}).status_code == 400


def test_the_owner_can_reset_a_pin_and_nobody_else_can():
    client, uid = _setup()
    assert client.put(f"/users/{uid}/pin", json={"pin": "7777"}).status_code == 403
    assert client.put(f"/users/{uid}/pin", json={"pin": "7777"},
                      headers=OWNER_HEADERS).status_code == 200
    assert _bet(client, uid, "1234").status_code == 401
    assert _bet(client, uid, "7777").status_code == 200

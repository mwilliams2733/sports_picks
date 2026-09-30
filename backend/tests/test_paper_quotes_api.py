"""Quote endpoints and has_pin (plan 027, Task 2)."""
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from backend.api.main import create_app
from backend.database import get_session
from backend.models import Base, Game, Odds, PlayerProp, Team, UserProfile
from backend.tests.auth_helpers import ALL_HEADERS, TEST_PIN
from backend.time_utils import et_today


def _client():
    client = TestClient(create_app(":memory:"), headers=ALL_HEADERS)
    Base.metadata.create_all(client.app.state.engine)
    return client


def _game_with_odds(client):
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    s = get_session(client.app.state.engine)
    home = Team(name="Home", abbreviation="HOM", sport="nfl")
    away = Team(name="Away", abbreviation="AWY", sport="nfl")
    s.add_all([home, away])
    s.flush()
    g = Game(sport="nfl", season="2026", date=et_today(), home_team_id=home.id,
             away_team_id=away.id, status="scheduled")
    s.add(g)
    s.flush()
    s.add_all([
        Odds(game_id=g.id, bookmaker="dk", moneyline_home=-110, moneyline_away=100,
             timestamp=now - timedelta(minutes=5)),
        Odds(game_id=g.id, bookmaker="fd", moneyline_home=-130, moneyline_away=110,
             timestamp=now - timedelta(minutes=5)),
        PlayerProp(game_id=g.id, bookmaker="dk", market="player_pass_yds",
                   player_name="QB One", outcome="Over", line=225.5, odds=-110,
                   fetched_at=now - timedelta(minutes=5)),
    ])
    s.commit()
    gid = g.id
    s.close()
    return gid


def test_quotes_returns_the_consensus_and_refusals():
    client = _client()
    gid = _game_with_odds(client)
    r = client.get(f"/paper/quotes?game_id={gid}")
    assert r.status_code == 200
    quotes = {(q["pick_type"], q["side"]): q for q in r.json()["quotes"]}
    assert quotes[("moneyline", "HOME")]["odds"] == -120        # hand-computed in test_paper_pricing
    assert quotes[("moneyline", "HOME")]["pick_value"] == "HOME ML"
    assert quotes[("spread", "HOME")] == {
        "pick_type": "spread", "side": "HOME", "available": False,
        "reason": "not_quoted", "message": "No book is quoting this bet right now."}


def test_prop_quotes_lists_the_quoted_prop():
    client = _client()
    gid = _game_with_odds(client)
    [row] = client.get(f"/paper/prop-quotes?game_id={gid}").json()["quotes"]
    assert (row["prop_player"], row["outcome"], row["line"], row["odds"], row["available"]) == (
        "QB One", "Over", 225.5, -110, True)


def test_quotes_for_an_unknown_game_is_404():
    client = _client()
    assert client.get("/paper/quotes?game_id=999").status_code == 404
    assert client.get("/paper/prop-quotes?game_id=999").status_code == 404


def test_quotes_are_open_reads():
    client = TestClient(create_app(":memory:"))       # no owner key, no PIN
    Base.metadata.create_all(client.app.state.engine)
    gid = _game_with_odds(client)
    assert client.get(f"/paper/quotes?game_id={gid}").status_code == 200


def test_has_pin_is_reported_and_no_secret_leaves():
    client = _client()
    client.post("/users/", json={"name": "friend", "pin": TEST_PIN})
    s = get_session(client.app.state.engine)
    s.add(UserProfile(name="legacy"))                   # pre-026 player: no PIN
    s.commit()
    legacy_id = s.query(UserProfile).filter_by(name="legacy").one().id
    s.close()

    rows = {u["name"]: u for u in client.get("/users/").json()}
    assert rows["friend"]["has_pin"] is True
    assert rows["legacy"]["has_pin"] is False
    assert client.get(f"/users/{legacy_id}").json()["has_pin"] is False
    for body in (client.get("/users/").text, client.get(f"/users/{legacy_id}").text):
        assert "pin_hash" not in body and "pin_salt" not in body

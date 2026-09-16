from datetime import date

from fastapi.testclient import TestClient

from backend.api.main import create_app
from backend.database import get_session
from backend.models import Base, Team, Game


def _seed_scheduled_game(client):
    """Create a fresh Team pair and a scheduled Game. Returns the game id."""
    engine = client.app.state.engine
    Base.metadata.create_all(engine)
    session = get_session(engine)
    home = Team(name="Home", abbreviation="HM", sport="nba")
    away = Team(name="Away", abbreviation="AW", sport="nba")
    session.add_all([home, away])
    session.flush()
    game = Game(
        sport="nba",
        season="2025-26",
        date=date.today(),
        home_team_id=home.id,
        away_team_id=away.id,
        status="scheduled",
    )
    session.add(game)
    session.flush()
    game_id = game.id
    session.commit()
    session.close()
    return game_id


def _make_user(client, name="tester"):
    response = client.post("/users/", json={"name": name})
    assert response.status_code == 200
    return response.json()["id"]


def test_pick_placed_broadcasts_frame():
    """The load-bearing test: a real WebSocket frame must arrive when a pick
    is placed. A tool reporting "connected" is not a working tool — this
    opens a real connection and asserts a real frame, not just a handshake.

    Uses ``with TestClient(app) as client:`` (not the bare-constructor form
    used elsewhere in this repo's tests) because only the context-manager
    form runs the app's lifespan/startup, which is what populates
    ``app.state.loop`` — the loop `_log_feed_event` dispatches onto.
    """
    app = create_app(":memory:")
    with TestClient(app) as client:
        game_id = _seed_scheduled_game(client)
        user_id = _make_user(client, "alice")

        with client.websocket_connect("/ws") as ws:
            response = client.post(
                f"/users/{user_id}/picks",
                json={
                    "game_id": game_id,
                    "pick_type": "moneyline",
                    "pick_value": "Home ML",
                    "odds": -110,
                    "stake": 100,
                },
            )
            assert response.status_code == 200

            frame = ws.receive_json()
            assert frame["type"] == "pick_placed"
            assert "alice" in frame["data"]["message"]

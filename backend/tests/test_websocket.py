import queue
import threading
from datetime import date

from fastapi.testclient import TestClient

from backend.api.main import create_app
from backend.api.websocket import ConnectionManager
from backend.database import get_session
from backend.models import Base, Team, Game

# Starlette's WebSocketTestSession.receive() has no internal timeout, so a
# regressed broadcast dispatch would otherwise hang this test (and the whole
# pytest run) forever instead of failing. Bound the wait explicitly.
#
# We can't use concurrent.futures.ThreadPoolExecutor for this: its atexit
# hook joins every worker thread of every pool at interpreter shutdown
# regardless of shutdown(wait=False), so a thread still blocked inside
# receive_json() would hang process exit even after future.result(timeout=)
# raised. A plain daemon thread has no such join-on-exit behavior.
_RECEIVE_TIMEOUT_S = 5


def _receive_json_with_timeout(ws, timeout=_RECEIVE_TIMEOUT_S):
    """Call ws.receive_json() on a daemon thread so a hung dispatch fails
    cleanly instead of blocking the test (or the interpreter) forever."""
    result: queue.Queue = queue.Queue(maxsize=1)

    def _worker():
        try:
            result.put(("ok", ws.receive_json()))
        except Exception as exc:  # pragma: no cover - defensive
            result.put(("error", exc))

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()
    try:
        kind, value = result.get(timeout=timeout)
    except queue.Empty:
        raise AssertionError(
            f"no pick_placed frame arrived within {timeout}s — "
            "the broadcast dispatch is not reaching the event loop"
        )
    if kind == "error":
        raise value
    return value


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

    The frame is read via ``_receive_json_with_timeout`` rather than
    ``ws.receive_json()`` directly: Starlette's test WebSocket has no
    internal receive timeout, so if the dispatch ever regresses back to
    silently no-op'ing, this test must fail within a few seconds, not hang
    the run forever.
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

            frame = _receive_json_with_timeout(ws)
            assert frame["type"] == "pick_placed"
            assert "alice" in frame["data"]["message"]


def test_disconnect_is_idempotent():
    """Calling disconnect twice on the same socket must not raise."""
    manager = ConnectionManager()

    class FakeSocket:
        pass

    fake = FakeSocket()
    manager.active_connections.append(fake)
    manager.disconnect(fake)
    manager.disconnect(fake)  # must not raise ValueError
    assert fake not in manager.active_connections


def test_connection_removed_after_close():
    app = create_app(":memory:")
    with TestClient(app) as client:
        Base.metadata.create_all(client.app.state.engine)

        from backend.api.websocket import manager

        with client.websocket_connect("/ws"):
            assert len(manager.active_connections) == 1

        assert len(manager.active_connections) == 0

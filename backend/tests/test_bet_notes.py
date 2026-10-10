"""A straight bet can carry the bettor's written reasoning (spec B2).

Claude's NFL picks are written with reasoning at pick time; the note puts it
next to the bet on the ticket and in the league feed. The note is the
bettor's own: Tail copies the bet, never the note.
"""
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, text

from backend.api.main import create_app
from backend.database import migrate_paper_pick_note
from backend.tests.auth_helpers import ALL_HEADERS
from backend.tests.test_api_users import _make_user, _seed_games


def _setup():
    client = TestClient(create_app(":memory:"), headers=ALL_HEADERS)
    [gid] = _seed_games(client, [{"status": "scheduled"}])
    return client, gid, _make_user(client)


def _bet(client, uid, gid, **extra):
    return client.post(f"/users/{uid}/picks", json={
        "game_id": gid, "pick_type": "moneyline", "side": "HOME", "stake": 100, **extra})


def test_a_note_is_on_the_ticket_and_in_the_feed_but_not_in_the_tail_legs():   # Review Focus 3
    client, gid, uid = _setup()
    note = "Backup QB starts; the line has not moved. Confidence 2/5 · Model: no side."
    assert _bet(client, uid, gid, note=note).status_code == 200
    [ticket] = client.get(f"/users/{uid}/bets").json()["tickets"]
    assert ticket["note"] == note
    [event] = [e for e in client.get("/users/feed").json() if e["event_type"] == "pick_placed"]
    assert event["payload"]["note"] == note
    assert all("note" not in leg for leg in event["payload"]["legs"])


def test_markup_is_stored_as_text_and_the_limit_is_1000():   # Review Focus 2
    client, gid, uid = _setup()
    assert _bet(client, uid, gid, note="<b>bold</b>" + "x" * 989).status_code == 200
    [ticket] = client.get(f"/users/{uid}/bets").json()["tickets"]
    assert ticket["note"].startswith("<b>bold</b>") and len(ticket["note"]) == 1000
    assert _bet(client, uid, gid, note="x" * 1001).status_code == 422


def test_no_note_or_a_blank_one_stores_nothing():
    client, gid, uid = _setup()
    assert _bet(client, uid, gid).status_code == 200
    assert _bet(client, uid, gid, note="   ").status_code == 200
    tickets = client.get(f"/users/{uid}/bets").json()["tickets"]
    assert [t["note"] for t in tickets] == [None, None]
    events = [e for e in client.get("/users/feed").json() if e["event_type"] == "pick_placed"]
    assert all("note" not in e["payload"] for e in events)


def test_the_migration_adds_the_column_once():
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE paper_picks (id INTEGER PRIMARY KEY, stake FLOAT)"))
    migrate_paper_pick_note(engine)
    migrate_paper_pick_note(engine)                      # idempotent
    assert "note" in [c["name"] for c in inspect(engine).get_columns("paper_picks")]

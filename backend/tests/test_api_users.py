from datetime import date

import pytest
from fastapi.testclient import TestClient

from backend.api.main import create_app
from backend.database import get_session
from backend.models import Base, Team, Game, Parlay, PaperPick


def _seed_games(client, specs):
    """Create a fresh Team pair and a Game per spec.

    ``specs`` is a list of dicts like
    ``{"status": "final", "home_score": 110, "away_score": 100}`` or
    ``{"status": "scheduled"}``. Returns the list of created game ids.
    """
    engine = client.app.state.engine
    Base.metadata.create_all(engine)
    session = get_session(engine)
    game_ids = []
    for i, spec in enumerate(specs):
        home = Team(name=f"Home{i}", abbreviation=f"H{i}", sport="nba")
        away = Team(name=f"Away{i}", abbreviation=f"A{i}", sport="nba")
        session.add_all([home, away])
        session.flush()
        game = Game(
            sport="nba",
            season="2025-26",
            date=date.today(),
            home_team_id=home.id,
            away_team_id=away.id,
            status=spec.get("status", "scheduled"),
            home_score=spec.get("home_score"),
            away_score=spec.get("away_score"),
        )
        session.add(game)
        session.flush()
        game_ids.append(game.id)
    session.commit()
    session.close()
    return game_ids


def _make_user(client, name="tester"):
    response = client.post("/users/", json={"name": name})
    assert response.status_code == 200
    return response.json()["id"]


# --- Step 2: User CRUD tests -------------------------------------------


def test_create_user_returns_starting_balance():
    app = create_app(":memory:")
    client = TestClient(app)
    Base.metadata.create_all(client.app.state.engine)
    response = client.post("/users/", json={"name": "alice"})
    assert response.status_code == 200
    assert response.json()["starting_balance"] == 10000.0


def test_create_duplicate_user_rejected():
    app = create_app(":memory:")
    client = TestClient(app)
    Base.metadata.create_all(client.app.state.engine)
    first = client.post("/users/", json={"name": "alice"})
    assert first.status_code == 200
    second = client.post("/users/", json={"name": "alice"})
    assert second.status_code == 400
    assert second.json()["detail"] == "Username already taken"


def test_get_unknown_user_404():
    app = create_app(":memory:")
    client = TestClient(app)
    Base.metadata.create_all(client.app.state.engine)
    response = client.get("/users/999")
    assert response.status_code == 404


def test_delete_user_removes_picks():
    app = create_app(":memory:")
    client = TestClient(app)
    game_ids = _seed_games(client, [{"status": "final", "home_score": 110, "away_score": 100}])
    user_id = _make_user(client, "alice")
    pick_response = client.post(f"/users/{user_id}/picks", json={
        "game_id": game_ids[0],
        "pick_type": "moneyline",
        "pick_value": "HOME ML",
        "odds": -110,
        "stake": 100,
    })
    assert pick_response.status_code == 200

    delete_response = client.delete(f"/users/{user_id}")
    assert delete_response.status_code == 200

    get_response = client.get(f"/users/{user_id}")
    assert get_response.status_code == 404


def test_list_users_sorted_by_balance_desc():
    app = create_app(":memory:")
    client = TestClient(app)
    game_ids = _seed_games(client, [{"status": "final", "home_score": 110, "away_score": 100}])
    loser_id = _make_user(client, "loser")
    winner_id = _make_user(client, "winner")

    winner_pick = client.post(f"/users/{winner_id}/picks", json={
        "game_id": game_ids[0],
        "pick_type": "moneyline",
        "pick_value": "HOME ML",
        "odds": -110,
        "stake": 100,
    })
    assert winner_pick.status_code == 200
    assert winner_pick.json()["result"] == "win"

    response = client.get("/users/")
    assert response.status_code == 200
    data = response.json()
    names = [u["name"] for u in data]
    assert names.index("winner") < names.index("loser")


# --- Step 3: Single-pick placement and grading tests --------------------


def test_place_pick_on_scheduled_game_is_pending():
    app = create_app(":memory:")
    client = TestClient(app)
    game_ids = _seed_games(client, [{"status": "scheduled"}])
    user_id = _make_user(client)
    response = client.post(f"/users/{user_id}/picks", json={
        "game_id": game_ids[0],
        "pick_type": "moneyline",
        "pick_value": "HOME ML",
        "odds": -110,
        "stake": 100,
    })
    assert response.status_code == 200
    body = response.json()
    assert body["result"] is None
    assert body["payout"] is None


def test_place_pick_on_final_game_grades_immediately_win():
    app = create_app(":memory:")
    client = TestClient(app)
    game_ids = _seed_games(client, [{"status": "final", "home_score": 110, "away_score": 100}])
    user_id = _make_user(client)
    response = client.post(f"/users/{user_id}/picks", json={
        "game_id": game_ids[0],
        "pick_type": "moneyline",
        "pick_value": "HOME ML",
        "odds": -110,
        "stake": 100,
    })
    assert response.status_code == 200
    body = response.json()
    assert body["result"] == "win"
    # calculate_payout(-110) == 100/110, so a $100 win pays ~90.909.
    assert body["payout"] == pytest.approx(90.909, rel=1e-3)


def test_place_pick_zero_stake_rejected():
    app = create_app(":memory:")
    client = TestClient(app)
    game_ids = _seed_games(client, [{"status": "scheduled"}])
    user_id = _make_user(client)
    response = client.post(f"/users/{user_id}/picks", json={
        "game_id": game_ids[0],
        "pick_type": "moneyline",
        "pick_value": "HOME ML",
        "odds": -110,
        "stake": 0,
    })
    assert response.status_code == 400
    assert response.json()["detail"] == "Stake must be positive"


def test_place_pick_unknown_game_404():
    app = create_app(":memory:")
    client = TestClient(app)
    Base.metadata.create_all(client.app.state.engine)
    user_id = _make_user(client)
    response = client.post(f"/users/{user_id}/picks", json={
        "game_id": 9999,
        "pick_type": "moneyline",
        "pick_value": "HOME ML",
        "odds": -110,
        "stake": 100,
    })
    assert response.status_code == 404


def test_place_pick_exceeding_balance_rejected():
    app = create_app(":memory:")
    client = TestClient(app)
    game_ids = _seed_games(client, [{"status": "scheduled"}])
    user_id = _make_user(client)
    response = client.post(f"/users/{user_id}/picks", json={
        "game_id": game_ids[0],
        "pick_type": "moneyline",
        "pick_value": "HOME ML",
        "odds": -110,
        "stake": 10001,
    })
    assert response.status_code == 400
    assert response.json()["detail"] == "Insufficient balance"


def test_pending_stakes_are_not_reserved():
    # CHARACTERIZATION (known bug, see plans/README.md): place_pick computes
    # "current balance" from settled payouts only (users.py:181-183) and never
    # subtracts stakes on pending (unsettled) picks. So five separate $10,000
    # stakes on a scheduled game for a user with a $10,000 starting balance all
    # succeed, and the reported balance never drops. After the bankroll-
    # reservation fix, picks 2-5 must return 400 (insufficient balance) and
    # this assertion must be inverted.
    app = create_app(":memory:")
    client = TestClient(app)
    game_ids = _seed_games(client, [{"status": "scheduled"}])
    user_id = _make_user(client)

    for _ in range(5):
        response = client.post(f"/users/{user_id}/picks", json={
            "game_id": game_ids[0],
            "pick_type": "moneyline",
            "pick_value": "HOME ML",
            "odds": -110,
            "stake": 10000,
        })
        assert response.status_code == 200

    get_response = client.get(f"/users/{user_id}")
    assert get_response.status_code == 200
    assert get_response.json()["current_balance"] == 10000.0


# --- Step 4: Parlay tests ------------------------------------------------


def test_parlay_requires_two_legs():
    app = create_app(":memory:")
    client = TestClient(app)
    game_ids = _seed_games(client, [{"status": "scheduled"}])
    user_id = _make_user(client)
    response = client.post(f"/users/{user_id}/parlay", json={
        "stake": 100,
        "legs": [
            {"game_id": game_ids[0], "pick_type": "moneyline", "pick_value": "HOME ML", "odds": -110},
        ],
    })
    assert response.status_code == 400
    assert response.json()["detail"] == "Parlay requires at least 2 legs"


def test_parlay_combined_odds_two_minus_110_legs():
    app = create_app(":memory:")
    client = TestClient(app)
    game_ids = _seed_games(client, [
        {"status": "final", "home_score": 110, "away_score": 100},
        {"status": "final", "home_score": 110, "away_score": 100},
    ])
    user_id = _make_user(client)
    response = client.post(f"/users/{user_id}/parlay", json={
        "stake": 500,
        "legs": [
            {"game_id": game_ids[0], "pick_type": "moneyline", "pick_value": "HOME ML", "odds": -110},
            {"game_id": game_ids[1], "pick_type": "moneyline", "pick_value": "HOME ML", "odds": -110},
        ],
    })
    assert response.status_code == 200
    body = response.json()
    # Two -110 legs: combined_decimal = (1 + 100/110)^2 = 3.6446...
    # combined_american = round(2.6446 * 100) = 264.
    # potential_payout = round(500 * 2.6446, 2) = 1322.31. Verified constants.
    assert body["combined_odds"] == 264
    assert body["potential_payout"] == 1322.31


def test_parlay_payout_missing_from_balance():
    # CHARACTERIZATION (known bug, see plans/README.md): the balance formula
    # (users.py:150-151 in get_user) sums PaperPick.payout only. Parlay legs
    # are stored with payout=0 (users.py:408) and the real payout lives on
    # Parlay.payout, which is never summed into current_balance/profit. So a
    # winning parlay's profit is invisible from GET /users/{id}. After the
    # fix, current_balance must become 11322.31 and profit must become
    # 1322.31.
    app = create_app(":memory:")
    client = TestClient(app)
    game_ids = _seed_games(client, [
        {"status": "final", "home_score": 110, "away_score": 100},
        {"status": "final", "home_score": 110, "away_score": 100},
    ])
    user_id = _make_user(client)
    parlay_response = client.post(f"/users/{user_id}/parlay", json={
        "stake": 500,
        "legs": [
            {"game_id": game_ids[0], "pick_type": "moneyline", "pick_value": "HOME ML", "odds": -110},
            {"game_id": game_ids[1], "pick_type": "moneyline", "pick_value": "HOME ML", "odds": -110},
        ],
    })
    assert parlay_response.status_code == 200
    assert parlay_response.json()["result"] == "win"

    get_response = client.get(f"/users/{user_id}")
    assert get_response.status_code == 200
    body = get_response.json()
    assert body["current_balance"] == 10000.0
    assert body["profit"] == 0


def test_parlay_on_scheduled_games_never_settles():
    # CHARACTERIZATION (known bug, see plans/README.md): place_parlay only
    # grades the parlay when all legs are already final at placement time
    # (users.py:418, all_graded). POST /users/grade only iterates PaperPick
    # rows, never re-checking Parlay rows once their legs later become final.
    # So a parlay with a leg that was scheduled at placement never settles,
    # even after the game finishes and /users/grade runs. After the fix, the
    # parlay's result must become "win".
    app = create_app(":memory:")
    client = TestClient(app)
    game_ids = _seed_games(client, [
        {"status": "final", "home_score": 110, "away_score": 100},
        {"status": "scheduled"},
    ])
    user_id = _make_user(client)
    parlay_response = client.post(f"/users/{user_id}/parlay", json={
        "stake": 100,
        "legs": [
            {"game_id": game_ids[0], "pick_type": "moneyline", "pick_value": "HOME ML", "odds": -110},
            {"game_id": game_ids[1], "pick_type": "moneyline", "pick_value": "HOME ML", "odds": -110},
        ],
    })
    assert parlay_response.status_code == 200
    assert parlay_response.json()["result"] is None

    # Mark the second game final with a winning score, then run bulk grading.
    engine = client.app.state.engine
    session = get_session(engine)
    game = session.get(Game, game_ids[1])
    game.status = "final"
    game.home_score = 110
    game.away_score = 100
    session.commit()
    session.close()

    grade_response = client.post("/users/grade")
    assert grade_response.status_code == 200

    session = get_session(engine)
    parlay = session.get(Parlay, parlay_response.json()["id"])
    assert parlay.result is None
    session.close()


def test_parlay_legs_stored_with_zero_stake():
    app = create_app(":memory:")
    client = TestClient(app)
    game_ids = _seed_games(client, [
        {"status": "final", "home_score": 110, "away_score": 100},
        {"status": "final", "home_score": 110, "away_score": 100},
    ])
    user_id = _make_user(client)
    parlay_response = client.post(f"/users/{user_id}/parlay", json={
        "stake": 500,
        "legs": [
            {"game_id": game_ids[0], "pick_type": "moneyline", "pick_value": "HOME ML", "odds": -110},
            {"game_id": game_ids[1], "pick_type": "moneyline", "pick_value": "HOME ML", "odds": -110},
        ],
    })
    assert parlay_response.status_code == 200

    engine = client.app.state.engine
    session = get_session(engine)
    legs = session.query(PaperPick).filter(PaperPick.parlay_id.isnot(None)).all()
    assert len(legs) == 2
    for leg in legs:
        assert leg.stake == 0
    session.close()


# --- Step 5: Bulk grading, streaks, stats, feed --------------------------


def test_grade_endpoint_grades_pending_picks():
    app = create_app(":memory:")
    client = TestClient(app)
    game_ids = _seed_games(client, [{"status": "scheduled"}])
    user_id = _make_user(client)
    pick_response = client.post(f"/users/{user_id}/picks", json={
        "game_id": game_ids[0],
        "pick_type": "moneyline",
        "pick_value": "HOME ML",
        "odds": -110,
        "stake": 100,
    })
    assert pick_response.status_code == 200
    assert pick_response.json()["result"] is None

    engine = client.app.state.engine
    session = get_session(engine)
    game = session.get(Game, game_ids[0])
    game.status = "final"
    game.home_score = 110
    game.away_score = 100
    session.commit()
    session.close()

    grade_response = client.post("/users/grade")
    assert grade_response.status_code == 200
    assert grade_response.json() == {"graded": 1}

    session = get_session(engine)
    pick = session.get(PaperPick, pick_response.json()["id"])
    assert pick.result is not None
    session.close()


def test_grade_endpoint_skips_games_without_scores():
    app = create_app(":memory:")
    client = TestClient(app)
    game_ids = _seed_games(client, [{"status": "scheduled"}])
    user_id = _make_user(client)
    pick_response = client.post(f"/users/{user_id}/picks", json={
        "game_id": game_ids[0],
        "pick_type": "moneyline",
        "pick_value": "HOME ML",
        "odds": -110,
        "stake": 100,
    })
    assert pick_response.status_code == 200

    engine = client.app.state.engine
    session = get_session(engine)
    game = session.get(Game, game_ids[0])
    game.status = "final"
    # home_score/away_score stay None.
    session.commit()
    session.close()

    grade_response = client.post("/users/grade")
    assert grade_response.status_code == 200
    assert grade_response.json() == {"graded": 0}


def test_win_streak_counted():
    app = create_app(":memory:")
    client = TestClient(app)
    game_ids = _seed_games(client, [
        {"status": "final", "home_score": 110, "away_score": 100},
        {"status": "final", "home_score": 110, "away_score": 100},
        {"status": "final", "home_score": 110, "away_score": 100},
    ])
    user_id = _make_user(client)
    for game_id in game_ids:
        response = client.post(f"/users/{user_id}/picks", json={
            "game_id": game_id,
            "pick_type": "moneyline",
            "pick_value": "HOME ML",
            "odds": -110,
            "stake": 100,
        })
        assert response.status_code == 200
        assert response.json()["result"] == "win"

    response = client.get("/users/")
    assert response.status_code == 200
    user = next(u for u in response.json() if u["id"] == user_id)
    assert user["current_streak"] == 3
    assert user["streak_type"] == "win"


def test_user_stats_shape():
    app = create_app(":memory:")
    client = TestClient(app)
    game_ids = _seed_games(client, [{"status": "scheduled"}])
    user_id = _make_user(client)
    response = client.get(f"/users/{user_id}/stats")
    assert response.status_code == 200
    body = response.json()
    for key in ("today", "this_week", "this_month", "all_time", "daily_breakdown"):
        assert key in body


def test_feed_route_is_shadowed():
    # CHARACTERIZATION (known bug, see plans/README.md): GET /users/{user_id}
    # is registered before GET /users/feed (users.py:136 vs :634), and FastAPI
    # matches path routes in registration order, so "/users/feed" matches
    # {user_id}="feed" first, and int-parsing "feed" as user_id fails
    # validation. After plan 004 reorders the routes, this must become 200
    # returning a list.
    app = create_app(":memory:")
    client = TestClient(app)
    Base.metadata.create_all(client.app.state.engine)
    response = client.get("/users/feed")
    assert response.status_code == 422

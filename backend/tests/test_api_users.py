from datetime import date

import pytest
from fastapi.testclient import TestClient

from backend.api.main import create_app
from backend.database import get_session
from backend.models import Base, Team, Game, Parlay, PaperPick
from backend.tests.auth_helpers import ALL_HEADERS, TEST_PIN
from backend.tests.pricing_helpers import seed_fresh_odds
from backend.time_utils import et_today


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
            date=et_today(),
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
    for gid, spec in zip(game_ids, specs):
        if spec.get("status", "scheduled") == "scheduled":
            seed_fresh_odds(engine, gid)
    return game_ids


def _make_user(client, name="tester"):
    response = client.post("/users/", json={"name": name, "pin": TEST_PIN})
    assert response.status_code == 200
    return response.json()["id"]


def _finish(client, game_id, home=110, away=100):
    session = get_session(client.app.state.engine)
    game = session.get(Game, game_id)
    game.status, game.home_score, game.away_score = "final", home, away
    session.commit()
    session.close()


def _grade(client):
    response = client.post("/users/grade")
    assert response.status_code == 200
    return response.json()


# --- Step 2: User CRUD tests -------------------------------------------


def test_create_user_returns_starting_balance():
    app = create_app(":memory:")
    client = TestClient(app, headers=ALL_HEADERS)
    Base.metadata.create_all(client.app.state.engine)
    response = client.post("/users/", json={"name": "alice", "pin": TEST_PIN})
    assert response.status_code == 200
    assert response.json()["starting_balance"] == 10000.0


def test_create_duplicate_user_rejected():
    app = create_app(":memory:")
    client = TestClient(app, headers=ALL_HEADERS)
    Base.metadata.create_all(client.app.state.engine)
    first = client.post("/users/", json={"name": "alice", "pin": TEST_PIN})
    assert first.status_code == 200
    second = client.post("/users/", json={"name": "alice", "pin": TEST_PIN})
    assert second.status_code == 400
    assert second.json()["detail"] == "Username already taken"


def test_get_unknown_user_404():
    app = create_app(":memory:")
    client = TestClient(app, headers=ALL_HEADERS)
    Base.metadata.create_all(client.app.state.engine)
    response = client.get("/users/999")
    assert response.status_code == 404


def test_delete_user_removes_picks():
    app = create_app(":memory:")
    client = TestClient(app, headers=ALL_HEADERS)
    game_ids = _seed_games(client, [{"status": "scheduled"}])
    user_id = _make_user(client, "alice")
    pick_response = client.post(f"/users/{user_id}/picks", json={
        "game_id": game_ids[0],
        "pick_type": "moneyline",
        "side": "HOME",
        "stake": 100,
    })
    assert pick_response.status_code == 200
    _finish(client, game_ids[0])
    _grade(client)

    delete_response = client.delete(f"/users/{user_id}")
    assert delete_response.status_code == 200

    get_response = client.get(f"/users/{user_id}")
    assert get_response.status_code == 404


def test_list_users_sorted_by_balance_desc():
    app = create_app(":memory:")
    client = TestClient(app, headers=ALL_HEADERS)
    game_ids = _seed_games(client, [{"status": "scheduled"}])
    loser_id = _make_user(client, "loser")
    winner_id = _make_user(client, "winner")

    winner_pick = client.post(f"/users/{winner_id}/picks", json={
        "game_id": game_ids[0],
        "pick_type": "moneyline",
        "side": "HOME",
        "stake": 100,
    })
    assert winner_pick.status_code == 200
    assert winner_pick.json()["result"] is None
    _finish(client, game_ids[0])
    _grade(client)

    session = get_session(client.app.state.engine)
    pick = session.get(PaperPick, winner_pick.json()["id"])
    assert pick.result == "win"
    session.close()

    response = client.get("/users/")
    assert response.status_code == 200
    data = response.json()
    names = [u["name"] for u in data]
    assert names.index("winner") < names.index("loser")


# --- Step 3: Single-pick placement and grading tests --------------------


def test_place_pick_on_scheduled_game_is_pending():
    app = create_app(":memory:")
    client = TestClient(app, headers=ALL_HEADERS)
    game_ids = _seed_games(client, [{"status": "scheduled"}])
    user_id = _make_user(client)
    response = client.post(f"/users/{user_id}/picks", json={
        "game_id": game_ids[0],
        "pick_type": "moneyline",
        "side": "HOME",
        "stake": 100,
    })
    assert response.status_code == 200
    body = response.json()
    assert body["result"] is None
    assert body["payout"] is None


def test_a_bet_on_a_finished_game_is_refused():
    app = create_app(":memory:")
    client = TestClient(app, headers=ALL_HEADERS)
    game_ids = _seed_games(client, [{"status": "final", "home_score": 110, "away_score": 100}])
    user_id = _make_user(client)
    response = client.post(f"/users/{user_id}/picks", json={
        "game_id": game_ids[0], "pick_type": "moneyline",
        "side": "HOME", "stake": 100})
    assert response.status_code == 400


def test_a_bet_on_a_started_game_is_refused():
    from datetime import datetime, timedelta, timezone
    app = create_app(":memory:")
    client = TestClient(app, headers=ALL_HEADERS)
    game_ids = _seed_games(client, [{"status": "scheduled"}])
    session = get_session(app.state.engine)
    session.get(Game, game_ids[0]).start_time = (
        datetime.now(timezone.utc) - timedelta(minutes=5)).replace(tzinfo=None)
    session.commit()
    session.close()
    user_id = _make_user(client)
    response = client.post(f"/users/{user_id}/picks", json={
        "game_id": game_ids[0], "pick_type": "moneyline",
        "side": "HOME", "stake": 100})
    assert response.status_code == 400


def test_a_stale_scheduled_game_from_before_today_is_refused():
    """A game dated before today with no start_time is not a same-day game
    of unknown timing -- ingestion never writes an in-progress status, so a
    'scheduled' row dated before today is a stale row for a game that
    already happened (82 such rows exist in the live db)."""
    from datetime import timedelta
    app = create_app(":memory:")
    client = TestClient(app, headers=ALL_HEADERS)
    game_ids = _seed_games(client, [{"status": "scheduled"}])
    session = get_session(app.state.engine)
    game = session.get(Game, game_ids[0])
    game.date = et_today() - timedelta(days=1)
    game.start_time = None
    session.commit()
    session.close()
    user_id = _make_user(client)
    response = client.post(f"/users/{user_id}/picks", json={
        "game_id": game_ids[0], "pick_type": "moneyline",
        "side": "HOME", "stake": 100})
    assert response.status_code == 400


def test_a_bet_on_a_not_yet_started_game_is_accepted():
    from datetime import datetime, timedelta, timezone
    app = create_app(":memory:")
    client = TestClient(app, headers=ALL_HEADERS)
    game_ids = _seed_games(client, [{"status": "scheduled"}])
    session = get_session(app.state.engine)
    session.get(Game, game_ids[0]).start_time = (
        datetime.now(timezone.utc) + timedelta(hours=1)).replace(tzinfo=None)
    session.commit()
    session.close()
    user_id = _make_user(client)
    response = client.post(f"/users/{user_id}/picks", json={
        "game_id": game_ids[0], "pick_type": "moneyline",
        "side": "HOME", "stake": 100})
    assert response.status_code == 200


def test_a_settled_win_pays_at_the_price():
    app = create_app(":memory:")
    client = TestClient(app, headers=ALL_HEADERS)
    game_ids = _seed_games(client, [{"status": "scheduled"}])
    user_id = _make_user(client)
    pick = client.post(f"/users/{user_id}/picks", json={
        "game_id": game_ids[0], "pick_type": "moneyline",
        "side": "HOME", "stake": 100}).json()
    assert pick["result"] is None
    _finish(client, game_ids[0])
    _grade(client)
    body = client.get(f"/users/{user_id}").json()
    assert body["profit"] == pytest.approx(90.91, abs=0.01)


def test_place_pick_zero_stake_rejected():
    app = create_app(":memory:")
    client = TestClient(app, headers=ALL_HEADERS)
    game_ids = _seed_games(client, [{"status": "scheduled"}])
    user_id = _make_user(client)
    response = client.post(f"/users/{user_id}/picks", json={
        "game_id": game_ids[0],
        "pick_type": "moneyline",
        "side": "HOME",
        "stake": 0,
    })
    assert response.status_code == 400
    assert response.json()["detail"] == "Stake must be positive"


def test_place_pick_unknown_game_404():
    app = create_app(":memory:")
    client = TestClient(app, headers=ALL_HEADERS)
    Base.metadata.create_all(client.app.state.engine)
    user_id = _make_user(client)
    response = client.post(f"/users/{user_id}/picks", json={
        "game_id": 9999,
        "pick_type": "moneyline",
        "side": "HOME",
        "stake": 100,
    })
    assert response.status_code == 404


def test_place_pick_exceeding_balance_rejected():
    app = create_app(":memory:")
    client = TestClient(app, headers=ALL_HEADERS)
    game_ids = _seed_games(client, [{"status": "scheduled"}])
    user_id = _make_user(client)
    response = client.post(f"/users/{user_id}/picks", json={
        "game_id": game_ids[0],
        "pick_type": "moneyline",
        "side": "HOME",
        "stake": 10001,
    })
    assert response.status_code == 400
    assert response.json()["detail"] == "Insufficient balance"


def _bet(client, user_id, game_id, stake, side="HOME"):
    return client.post(f"/users/{user_id}/picks", json={
        "game_id": game_id, "pick_type": "moneyline", "side": side, "stake": stake})


def _parlay(client, user_id, game_ids, stake):
    return client.post(f"/users/{user_id}/parlay", json={
        "stake": stake,
        "legs": [{"game_id": g, "pick_type": "moneyline", "side": "HOME"}
                 for g in game_ids]})


def test_an_open_stake_is_reserved_against_the_next_bet():
    # Was a pinned bug (characterized in fb49c9e): the balance check counted
    # settled payouts only, so five $10,000 bets on a $10,000 bankroll all
    # went through while every game was still to play.
    app = create_app(":memory:")
    client = TestClient(app, headers=ALL_HEADERS)
    game_ids = _seed_games(client, [{"status": "scheduled"}])
    user_id = _make_user(client)

    first = _bet(client, user_id, game_ids[0], 6000)
    assert first.status_code == 200
    assert first.json()["new_balance"] == 4000.0

    second = _bet(client, user_id, game_ids[0], 4001)
    assert second.status_code == 400
    assert second.json()["detail"] == "Insufficient balance"
    assert _bet(client, user_id, game_ids[0], 4000).status_code == 200

    body = client.get(f"/users/{user_id}").json()
    # The settled bankroll is untouched by an open bet; what can still be
    # staked is not.
    assert body["current_balance"] == 10000.0
    assert body["available_balance"] == 0.0


def test_an_open_parlay_stake_is_reserved_too():
    app = create_app(":memory:")
    client = TestClient(app, headers=ALL_HEADERS)
    game_ids = _seed_games(client, [{"status": "scheduled"}, {"status": "scheduled"}])
    user_id = _make_user(client)

    placed = _parlay(client, user_id, game_ids, 7000)
    assert placed.status_code == 200
    assert placed.json()["new_balance"] == 3000.0
    assert _bet(client, user_id, game_ids[0], 3001).status_code == 400
    assert _parlay(client, user_id, game_ids, 3001).status_code == 400
    listed = {u["id"]: u for u in client.get("/users/").json()}
    assert listed[user_id]["available_balance"] == 3000.0


def test_a_settled_bet_releases_its_stake():
    app = create_app(":memory:")
    client = TestClient(app, headers=ALL_HEADERS)
    game_ids = _seed_games(client, [{"status": "scheduled"}])
    user_id = _make_user(client)
    assert _bet(client, user_id, game_ids[0], 1000, side="AWAY").status_code == 200
    assert client.get(f"/users/{user_id}").json()["available_balance"] == 9000.0

    _finish(client, game_ids[0], home=110, away=100)  # AWAY loses
    _grade(client)

    body = client.get(f"/users/{user_id}").json()
    # Lost once, not twice: the stake leaves "open" as the payout lands.
    assert body["current_balance"] == 9000.0
    assert body["available_balance"] == 9000.0


# --- Step 4: Parlay tests ------------------------------------------------


def test_parlay_requires_two_legs():
    app = create_app(":memory:")
    client = TestClient(app, headers=ALL_HEADERS)
    game_ids = _seed_games(client, [{"status": "scheduled"}])
    user_id = _make_user(client)
    response = client.post(f"/users/{user_id}/parlay", json={
        "stake": 100,
        "legs": [
            {"game_id": game_ids[0], "pick_type": "moneyline", "side": "HOME"},
        ],
    })
    assert response.status_code == 400
    assert response.json()["detail"] == "Parlay requires at least 2 legs"


def test_parlay_combined_odds_two_minus_110_legs():
    app = create_app(":memory:")
    client = TestClient(app, headers=ALL_HEADERS)
    game_ids = _seed_games(client, [
        {"status": "scheduled"},
        {"status": "scheduled"},
    ])
    user_id = _make_user(client)
    response = client.post(f"/users/{user_id}/parlay", json={
        "stake": 500,
        "legs": [
            {"game_id": game_ids[0], "pick_type": "moneyline", "side": "HOME"},
            {"game_id": game_ids[1], "pick_type": "moneyline", "side": "HOME"},
        ],
    })
    assert response.status_code == 200
    body = response.json()
    # Two -110 legs: combined_decimal = (1 + 100/110)^2 = 3.6446...
    # combined_american = round(2.6446 * 100) = 264.
    # potential_payout = round(500 * 2.6446, 2) = 1322.31. Verified constants.
    assert body["combined_odds"] == 264
    assert body["potential_payout"] == 1322.31


def test_a_winning_parlay_reaches_the_balance():
    # Was a pinned bug (plan 001): parlay payouts never reached the balance.
    app = create_app(":memory:")
    client = TestClient(app, headers=ALL_HEADERS)
    game_ids = _seed_games(client, [
        {"status": "scheduled"},
        {"status": "scheduled"},
    ])
    user_id = _make_user(client)
    parlay_response = client.post(f"/users/{user_id}/parlay", json={
        "stake": 500,
        "legs": [
            {"game_id": game_ids[0], "pick_type": "moneyline", "side": "HOME"},
            {"game_id": game_ids[1], "pick_type": "moneyline", "side": "HOME"},
        ],
    })
    assert parlay_response.status_code == 200
    assert parlay_response.json()["result"] is None
    _finish(client, game_ids[0])
    _finish(client, game_ids[1])
    _grade(client)

    get_response = client.get(f"/users/{user_id}")
    assert get_response.status_code == 200
    body = get_response.json()
    assert body["current_balance"] == pytest.approx(11322.31, abs=0.01)
    assert body["profit"] == pytest.approx(1322.31, abs=0.01)
    # Settled, so its stake is no longer held back from the next bet.
    assert body["available_balance"] == pytest.approx(11322.31, abs=0.01)


def test_a_parlay_settles_when_its_last_leg_finishes():
    # Was a pinned bug (plan 001): a parlay placed before its games never
    # settled.
    app = create_app(":memory:")
    client = TestClient(app, headers=ALL_HEADERS)
    game_ids = _seed_games(client, [
        {"status": "scheduled"},
        {"status": "scheduled"},
    ])
    user_id = _make_user(client)
    parlay_response = client.post(f"/users/{user_id}/parlay", json={
        "stake": 100,
        "legs": [
            {"game_id": game_ids[0], "pick_type": "moneyline", "side": "HOME"},
            {"game_id": game_ids[1], "pick_type": "moneyline", "side": "HOME"},
        ],
    })
    assert parlay_response.status_code == 200
    assert parlay_response.json()["result"] is None

    engine = client.app.state.engine
    _finish(client, game_ids[0])
    _finish(client, game_ids[1])
    _grade(client)

    session = get_session(engine)
    parlay = session.get(Parlay, parlay_response.json()["id"])
    assert parlay.result == "win"
    session.close()


def _seed_rematch(client, sport, *, mirrored, days_apart=180):
    """Two scheduled games between the same two teams/fighters.

    For combat this is the date-split twin -- one bout listed at two dates,
    e.g. "Kape vs Van" at 2026-12-26 and 2027-06-30 in the live db -- and
    ``mirrored`` lists it with home/away the other way round on the second
    row, which is how the two sources disagree.
    """
    from datetime import timedelta
    engine = client.app.state.engine
    Base.metadata.create_all(engine)
    session = get_session(engine)
    a = Team(name=f"{sport}-A", abbreviation=f"{sport}A", sport=sport)
    b = Team(name=f"{sport}-B", abbreviation=f"{sport}B", sport=sport)
    session.add_all([a, b])
    session.flush()
    first = Game(sport=sport, season="2026", date=et_today() + timedelta(days=1),
                 home_team_id=a.id, away_team_id=b.id, status="scheduled")
    home, away = (b, a) if mirrored else (a, b)
    second = Game(sport=sport, season="2026",
                  date=et_today() + timedelta(days=days_apart),
                  home_team_id=home.id, away_team_id=away.id, status="scheduled")
    session.add_all([first, second])
    session.commit()
    ids = [first.id, second.id]
    session.close()
    for gid in ids:
        seed_fresh_odds(engine, gid)
    return ids


@pytest.mark.parametrize("sport", ["mma", "boxing"])
@pytest.mark.parametrize("mirrored", [False, True])
def test_a_parlay_cannot_carry_one_fight_twice(sport, mirrored):
    # A combat bout listed at two dates is one event, so combine() would
    # price one fighter's win as two independent legs.
    app = create_app(":memory:")
    client = TestClient(app, headers=ALL_HEADERS)
    game_ids = _seed_rematch(client, sport, mirrored=mirrored)
    user_id = _make_user(client)
    response = _parlay(client, user_id, game_ids, 100)
    assert response.status_code == 400
    assert response.json()["detail"] == "A parlay can't have two legs on the same market."
    assert client.get(f"/users/{user_id}").json()["available_balance"] == 10000.0


def test_a_team_sport_rematch_is_two_games():
    # MLB plays the same opponent on consecutive days: two real events.
    app = create_app(":memory:")
    client = TestClient(app, headers=ALL_HEADERS)
    game_ids = _seed_rematch(client, "mlb", mirrored=False, days_apart=2)
    user_id = _make_user(client)
    assert _parlay(client, user_id, game_ids, 100).status_code == 200


def test_parlay_legs_stored_with_zero_stake():
    app = create_app(":memory:")
    client = TestClient(app, headers=ALL_HEADERS)
    game_ids = _seed_games(client, [
        {"status": "scheduled"},
        {"status": "scheduled"},
    ])
    user_id = _make_user(client)
    parlay_response = client.post(f"/users/{user_id}/parlay", json={
        "stake": 500,
        "legs": [
            {"game_id": game_ids[0], "pick_type": "moneyline", "side": "HOME"},
            {"game_id": game_ids[1], "pick_type": "moneyline", "side": "HOME"},
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
    client = TestClient(app, headers=ALL_HEADERS)
    game_ids = _seed_games(client, [{"status": "scheduled"}])
    user_id = _make_user(client)
    pick_response = client.post(f"/users/{user_id}/picks", json={
        "game_id": game_ids[0],
        "pick_type": "moneyline",
        "side": "HOME",
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
    assert grade_response.json() == {"graded": 1, "parlays_settled": 0}

    session = get_session(engine)
    pick = session.get(PaperPick, pick_response.json()["id"])
    assert pick.result is not None
    session.close()


def test_grade_endpoint_skips_games_without_scores():
    app = create_app(":memory:")
    client = TestClient(app, headers=ALL_HEADERS)
    game_ids = _seed_games(client, [{"status": "scheduled"}])
    user_id = _make_user(client)
    pick_response = client.post(f"/users/{user_id}/picks", json={
        "game_id": game_ids[0],
        "pick_type": "moneyline",
        "side": "HOME",
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
    assert grade_response.json() == {"graded": 0, "parlays_settled": 0}


def test_win_streak_counted():
    app = create_app(":memory:")
    client = TestClient(app, headers=ALL_HEADERS)
    game_ids = _seed_games(client, [
        {"status": "scheduled"},
        {"status": "scheduled"},
        {"status": "scheduled"},
    ])
    user_id = _make_user(client)
    for game_id in game_ids:
        response = client.post(f"/users/{user_id}/picks", json={
            "game_id": game_id,
            "pick_type": "moneyline",
            "side": "HOME",
            "stake": 100,
        })
        assert response.status_code == 200
        assert response.json()["result"] is None
        _finish(client, game_id)
    _grade(client)

    response = client.get("/users/")
    assert response.status_code == 200
    user = next(u for u in response.json() if u["id"] == user_id)
    assert user["current_streak"] == 3
    assert user["streak_type"] == "win"


def test_user_stats_shape():
    app = create_app(":memory:")
    client = TestClient(app, headers=ALL_HEADERS)
    game_ids = _seed_games(client, [{"status": "scheduled"}])
    user_id = _make_user(client)
    response = client.get(f"/users/{user_id}/stats")
    assert response.status_code == 200
    body = response.json()
    for key in ("today", "this_week", "this_month", "all_time", "daily_breakdown"):
        assert key in body


def test_feed_route_is_shadowed():
    app = create_app(":memory:")
    client = TestClient(app, headers=ALL_HEADERS)
    Base.metadata.create_all(client.app.state.engine)
    response = client.get("/users/feed")
    assert response.status_code == 200


def test_feed_route_not_shadowed():
    app = create_app(":memory:")
    client = TestClient(app, headers=ALL_HEADERS)
    Base.metadata.create_all(client.app.state.engine)
    response = client.get("/users/feed")
    assert response.status_code == 200
    assert isinstance(response.json(), list)


def test_feed_limit_is_capped():
    app = create_app(":memory:")
    client = TestClient(app, headers=ALL_HEADERS)
    Base.metadata.create_all(client.app.state.engine)
    response = client.get("/users/feed?limit=99999")
    assert response.status_code == 422


def test_get_user_by_id_still_works():
    app = create_app(":memory:")
    client = TestClient(app, headers=ALL_HEADERS)
    Base.metadata.create_all(client.app.state.engine)
    user_id = _make_user(client)
    response = client.get(f"/users/{user_id}")
    assert response.status_code == 200

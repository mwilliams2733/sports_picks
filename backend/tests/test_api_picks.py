from datetime import date, datetime, timezone
from fastapi.testclient import TestClient
from backend.api.main import create_app
from backend.models import Base, Team, Game, PickModel, PickResult, StrategyModel

def _seed_db(client):
    engine = client.app.state.engine
    Base.metadata.create_all(engine)
    from backend.database import get_session
    session = get_session(engine)
    t1 = Team(id=1, name="Boston Celtics", abbreviation="BOS", sport="nba")
    t2 = Team(id=2, name="LA Lakers", abbreviation="LAL", sport="nba")
    g = Game(id=1, sport="nba", season="2025-26", date=date.today(),
             home_team_id=1, away_team_id=2, status="scheduled")
    s = StrategyModel(id=1, name="ensemble", config_json="{}", is_active=True)
    p = PickModel(id=1, game_id=1, strategy_id=1, pick_type="moneyline",
                  pick_value="BOS ML", confidence=4, edge_pct=8.2,
                  odds_at_pick=-150, created_at=datetime.now(tz=timezone.utc))
    session.add_all([t1, t2, g, s, p])
    session.commit()
    session.close()

def test_get_today_picks():
    app = create_app(":memory:")
    client = TestClient(app)
    _seed_db(client)
    response = client.get("/picks/today")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["pick_value"] == "BOS ML"

def test_get_today_picks_filter_by_sport():
    app = create_app(":memory:")
    client = TestClient(app)
    _seed_db(client)
    response = client.get("/picks/today?sport=nfl")
    assert response.status_code == 200
    assert len(response.json()) == 0

def test_get_picks_history():
    app = create_app(":memory:")
    client = TestClient(app)
    _seed_db(client)
    response = client.get("/picks/history")
    assert response.status_code == 200


def _seed_history_with_clv(client):
    """Seed three picks: ungraded, graded moneyline (price CLV), graded spread (line CLV)."""
    engine = client.app.state.engine
    Base.metadata.create_all(engine)
    from backend.database import get_session
    s = get_session(engine)
    s.add_all([
        Team(id=1, name="Boston", abbreviation="BOS", sport="nba"),
        Team(id=2, name="LA", abbreviation="LAL", sport="nba"),
        StrategyModel(id=1, name="t", config_json="{}", is_active=True),
        Game(id=1, sport="nba", season="2025-26", date=date(2026, 4, 20),
             home_team_id=1, away_team_id=2, status="scheduled"),
        Game(id=2, sport="nba", season="2025-26", date=date(2026, 4, 21),
             home_team_id=1, away_team_id=2, status="final"),
        Game(id=3, sport="nba", season="2025-26", date=date(2026, 4, 22),
             home_team_id=1, away_team_id=2, status="final"),
        # Pending: no PickResult.
        PickModel(id=1, game_id=1, strategy_id=1, pick_type="moneyline",
                  pick_value="HOME ML", confidence=4, edge_pct=8.0,
                  odds_at_pick=-150, created_at=datetime.now(tz=timezone.utc)),
        # Graded moneyline: -150 -> -160 close = +1.54pp price CLV.
        PickModel(id=2, game_id=2, strategy_id=1, pick_type="moneyline",
                  pick_value="HOME ML", confidence=4, edge_pct=8.0,
                  odds_at_pick=-150, created_at=datetime.now(tz=timezone.utc)),
        PickResult(id=2, pick_id=2, result="win", payout=0.667, odds_at_close=-160),
        # Graded spread: HOME -3.0 with closing line HOME -3.5 = +0.5 line CLV.
        PickModel(id=3, game_id=3, strategy_id=1, pick_type="spread",
                  pick_value="HOME -3.0", confidence=3, edge_pct=4.0,
                  odds_at_pick=-110, created_at=datetime.now(tz=timezone.utc)),
        PickResult(id=3, pick_id=3, result="win", payout=0.909,
                   odds_at_close=-110, line_at_close=-3.5),
    ])
    s.commit()
    s.close()


def test_history_includes_per_pick_clv():
    app = create_app(":memory:")
    client = TestClient(app)
    _seed_history_with_clv(client)
    rows = client.get("/picks/history").json()
    by_id = {r["id"]: r for r in rows}

    # Pending: no result, both CLV fields null.
    assert by_id[1]["result"] is None
    assert by_id[1]["clv_pct"] is None
    assert by_id[1]["clv_points"] is None

    # Moneyline graded: clv_pct populated, clv_points null.
    assert by_id[2]["clv_pct"] == 1.54
    assert by_id[2]["clv_points"] is None

    # Spread graded: clv_points populated, clv_pct null.
    assert by_id[3]["clv_points"] == 0.5
    assert by_id[3]["clv_pct"] is None


def test_history_avoids_n_plus_one_query():
    """Smoke test: per-pick CLV must come from the joined PickResult, not a
    separate per-row SELECT. Verify by counting queries during the call."""
    from sqlalchemy import event
    app = create_app(":memory:")
    client = TestClient(app)
    _seed_history_with_clv(client)
    engine = client.app.state.engine

    counter = {"n": 0}

    @event.listens_for(engine, "before_cursor_execute")
    def count(conn, cursor, statement, params, context, executemany):
        if statement.strip().upper().startswith("SELECT"):
            counter["n"] += 1

    client.get("/picks/history")
    # With the previous N+1 implementation, 3 picks would yield ~4 SELECTs
    # (1 outer + 3 PickResult lookups). With the join, it should be 1.
    assert counter["n"] <= 2, f"Expected <=2 SELECTs, got {counter['n']}"

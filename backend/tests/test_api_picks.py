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

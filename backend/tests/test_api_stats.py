from datetime import date, datetime, timezone
from fastapi.testclient import TestClient
from backend.api.main import create_app
from backend.models import Base, Team, Game, PickModel, PickResult, StrategyModel

def _seed(client):
    engine = client.app.state.engine
    Base.metadata.create_all(engine)
    from backend.database import get_session
    s = get_session(engine)
    s.add_all([
        Team(id=1, name="BOS", abbreviation="BOS", sport="nba"),
        Team(id=2, name="LAL", abbreviation="LAL", sport="nba"),
        Game(id=1, sport="nba", season="2025-26", date=date(2026, 3, 10), home_team_id=1, away_team_id=2, status="final"),
        Game(id=2, sport="nba", season="2025-26", date=date(2026, 3, 11), home_team_id=1, away_team_id=2, status="final"),
        StrategyModel(id=1, name="test", config_json="{}", is_active=True),
        PickModel(id=1, game_id=1, strategy_id=1, pick_type="moneyline", pick_value="BOS ML", confidence=4, edge_pct=8.0, odds_at_pick=-150, created_at=datetime.now(tz=timezone.utc)),
        PickModel(id=2, game_id=2, strategy_id=1, pick_type="moneyline", pick_value="BOS ML", confidence=3, edge_pct=6.0, odds_at_pick=-130, created_at=datetime.now(tz=timezone.utc)),
        PickResult(id=1, pick_id=1, result="win", payout=0.667),
        PickResult(id=2, pick_id=2, result="loss", payout=-1.0),
    ])
    s.commit()
    s.close()

def test_get_record():
    app = create_app(":memory:")
    client = TestClient(app)
    _seed(client)
    resp = client.get("/stats/record")
    assert resp.status_code == 200
    data = resp.json()
    assert data["wins"] == 1
    assert data["losses"] == 1
    assert data["total"] == 2

def test_get_daily():
    app = create_app(":memory:")
    client = TestClient(app)
    _seed(client)
    resp = client.get("/stats/daily")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2

from fastapi.testclient import TestClient
from backend.api.main import create_app
from backend.models import Base, StrategyModel

def _seed(client):
    engine = client.app.state.engine
    Base.metadata.create_all(engine)
    from backend.database import get_session
    s = get_session(engine)
    s.add(StrategyModel(id=1, name="ensemble", description="test", config_json='{"min_edge": 5}', is_active=True))
    s.add(StrategyModel(id=2, name="value_only", description="test2", config_json='{"min_edge": 10}', is_active=False))
    s.commit()
    s.close()

def test_list_strategies():
    app = create_app(":memory:")
    client = TestClient(app)
    _seed(client)
    resp = client.get("/backtest/strategies")
    assert resp.status_code == 200
    assert len(resp.json()) == 2

def test_create_strategy():
    app = create_app(":memory:")
    client = TestClient(app)
    Base.metadata.create_all(client.app.state.engine)
    resp = client.post("/backtest/strategies", json={"name": "new_strat", "description": "desc", "config": {"min_edge": 7}})
    assert resp.status_code == 201
    assert resp.json()["name"] == "new_strat"

def test_promote_strategy():
    app = create_app(":memory:")
    client = TestClient(app)
    _seed(client)
    resp = client.patch("/backtest/strategies/2/promote")
    assert resp.status_code == 200
    strats = client.get("/backtest/strategies").json()
    active = [s for s in strats if s["is_active"]]
    assert len(active) == 1
    assert active[0]["id"] == 2

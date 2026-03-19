from fastapi.testclient import TestClient
from backend.api.main import create_app
from backend.models import Base

def test_pipeline_run_endpoint():
    app = create_app(":memory:")
    Base.metadata.create_all(app.state.engine)
    client = TestClient(app)
    resp = client.post("/pipeline/run")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] in ("completed", "error")


def test_pipeline_run_returns_credit_info():
    app = create_app(":memory:")
    Base.metadata.create_all(app.state.engine)
    client = TestClient(app)
    resp = client.post("/pipeline/run")
    assert resp.status_code == 200
    data = resp.json()
    assert "credits_used" in data
    assert "credits_remaining_month" in data
    assert "props_analyzed" in data


def test_pipeline_run_with_sport_filter():
    app = create_app(":memory:")
    Base.metadata.create_all(app.state.engine)
    client = TestClient(app)
    resp = client.post("/pipeline/run?sport=nba")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] in ("completed", "error")

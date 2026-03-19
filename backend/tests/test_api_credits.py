from fastapi.testclient import TestClient
from backend.api.main import create_app
from backend.models import Base


def test_credits_endpoint_returns_summary():
    app = create_app(":memory:")
    Base.metadata.create_all(app.state.engine)
    client = TestClient(app)
    resp = client.get("/credits/")
    assert resp.status_code == 200
    data = resp.json()
    assert "monthly_used" in data
    assert "monthly_limit" in data
    assert "monthly_remaining" in data
    assert "daily_used" in data
    assert "daily_target" in data
    assert data["monthly_used"] == 0
    assert data["monthly_limit"] == 20000

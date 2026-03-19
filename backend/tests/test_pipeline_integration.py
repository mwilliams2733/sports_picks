from datetime import datetime, date, timezone
from fastapi.testclient import TestClient
from backend.api.main import create_app
from backend.models import Base, ApiUsage
from backend.database import get_session


def test_full_pipeline_with_credits():
    app = create_app(":memory:")
    Base.metadata.create_all(app.state.engine)
    client = TestClient(app)
    resp = client.post("/pipeline/run")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "completed"
    assert "credits_used" in data
    assert "credits_remaining_month" in data
    assert "props_analyzed" in data


def test_pipeline_with_sport_filter():
    app = create_app(":memory:")
    Base.metadata.create_all(app.state.engine)
    client = TestClient(app)
    resp = client.post("/pipeline/run?sport=nba")
    assert resp.status_code == 200
    data = resp.json()
    assert data["active_sports"] == ["nba"]


def test_credits_endpoint_after_usage():
    app = create_app(":memory:")
    engine = app.state.engine
    Base.metadata.create_all(engine)
    session = get_session(engine)
    session.add(ApiUsage(
        endpoint="events", sport="nba", credits_used=1,
        requests_remaining=19999, created_at=datetime.now(tz=timezone.utc),
    ))
    session.commit()
    session.close()
    client = TestClient(app)
    resp = client.get("/credits/")
    assert resp.status_code == 200
    data = resp.json()
    assert data["monthly_used"] == 1
    assert data["api_requests_remaining"] == 19999

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

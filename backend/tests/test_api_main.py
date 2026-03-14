from fastapi.testclient import TestClient
from backend.api.main import create_app

def test_health_check():
    app = create_app(":memory:")
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}

"""Tests for plan 005: budget-exhausted alarm reaches the client, and the
Odds API key never leaks into logs or HTTP responses."""
import logging
import httpx
import pytest
from fastapi.testclient import TestClient
from backend.api.main import create_app
from backend.models import Base
from backend.collectors.budget import BudgetStatus
import backend.pipeline.full_pipeline as full_pipeline
import backend.api.pipeline_api as pipeline_api


def test_pipeline_run_returns_429_when_budget_exhausted(monkeypatch):
    """Load-bearing test: a MONTHLY_EXHAUSTED budget status must surface as a
    429 with status == 'budget_exhausted', not a silent 200 'completed'."""
    monkeypatch.setenv("ODDS_API_KEY", "FAKEKEY123")
    monkeypatch.setattr(full_pipeline, "check_budget", lambda session, budget: BudgetStatus.MONTHLY_EXHAUSTED)

    app = create_app(":memory:")
    Base.metadata.create_all(app.state.engine)
    client = TestClient(app)
    resp = client.post("/pipeline/run")
    assert resp.status_code == 429
    data = resp.json()
    assert data["status"] == "budget_exhausted"


@pytest.mark.asyncio
async def test_odds_fetch_failure_logs_redacted_message(caplog):
    """A raw httpx error containing an apiKey= URL must never reach the log
    verbatim: the key value must be redacted, but the sport and exception
    type must still be present for diagnostics."""
    from backend.collectors.odds_api import OddsAPICollector
    from backend.database import get_engine, get_session
    from backend.models import Base as ModelsBase

    async def failing_get(self, url, **kwargs):
        request = httpx.Request("GET", url, params=kwargs.get("params"))
        response = httpx.Response(429, request=request, text="Too Many Requests")
        raise httpx.HTTPStatusError(
            f"Client error '429 Too Many Requests' for url "
            f"'{request.url}'",
            request=request, response=response,
        )

    import pytest as _pytest
    mp = _pytest.MonkeyPatch()
    mp.setattr(httpx.AsyncClient, "get", failing_get)

    engine = get_engine(":memory:")
    ModelsBase.metadata.create_all(engine)
    session = get_session(engine)

    caplog.set_level(logging.WARNING)
    try:
        await full_pipeline.fetch_and_store_odds(session, ["nba"], api_key="FAKEKEY123")
    finally:
        mp.undo()
        session.close()

    assert "FAKEKEY123" not in caplog.text
    assert "<redacted>" in caplog.text
    assert "nba" in caplog.text
    assert "HTTPStatusError" in caplog.text


def test_pipeline_500_response_has_no_key_and_has_error_id(monkeypatch):
    """A generic pipeline failure whose message embeds an apiKey= URL must not
    leak the key (or even the word apiKey) to the HTTP client, and must carry
    an error_id for server-side correlation."""

    async def boom(*args, **kwargs):
        raise RuntimeError(
            "Client error '500 Internal Server Error' for url "
            "'https://api.the-odds-api.com/v4/sports/x/odds?apiKey=FAKEKEY123&regions=us'"
        )

    monkeypatch.setattr(pipeline_api, "fetch_and_store_games", boom)

    app = create_app(":memory:")
    Base.metadata.create_all(app.state.engine)
    client = TestClient(app)
    resp = client.post("/pipeline/run")
    assert resp.status_code == 500
    data = resp.json()
    assert "FAKEKEY123" not in resp.text
    assert "apiKey" not in resp.text
    assert "error_id" in data
    assert data["message"] == "Pipeline run failed"

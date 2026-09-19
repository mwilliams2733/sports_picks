"""`POST /pipeline/run`, with its collectors mocked.

These tests used to call the endpoint with nothing mocked, so each one ran
the entire real pipeline: live ESPN requests for every in-season sport, then
pick generation over whatever came back. Three tests took over 200 seconds
and made the whole suite's runtime depend on ESPN's latency -- 96 seconds one
hour, 16 minutes the next, both "green".

They also asserted `data["status"] in ("completed", "error")`, which verifies
almost nothing. Worse, the disjunction is unreachable: the endpoint returns
HTTP 500 on failure, so `status_code == 200` already implies "completed" and
the `"error"` branch could never be taken.

Everything here is mocked at the `backend.api.pipeline_api` namespace, where
the names are bound by `from ... import`.
"""

from datetime import date
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from backend.api import pipeline_api
from backend.api.main import create_app
from backend.models import Base, StrategyModel

CONFIG = {
    "database_path": ":memory:",
    # Fixed so the active-sport list does not depend on today's date.
    "seasons": {
        "nba": {"start": "10-22", "end": "06-20"},
        "mlb": {"start": "03-20", "end": "11-05"},
    },
    "odds_budget": {"monthly_limit": 20000, "daily_target": 600, "reserve": 2000},
}


@pytest.fixture()
def client(monkeypatch):
    """An app whose collectors never touch the network."""
    monkeypatch.setattr(pipeline_api, "load_config", lambda _p: dict(CONFIG))
    monkeypatch.setattr(pipeline_api, "fetch_and_store_games",
                        AsyncMock(return_value=7))
    monkeypatch.setattr(pipeline_api, "fetch_and_store_odds",
                        AsyncMock(return_value=11))
    monkeypatch.setattr(pipeline_api, "fetch_and_store_props",
                        AsyncMock(return_value=13))
    monkeypatch.setattr(pipeline_api, "generate_and_store_picks",
                        MagicMock(return_value=3))
    monkeypatch.setattr(pipeline_api, "run_prop_pipeline",
                        AsyncMock(return_value={"props_analyzed": 5,
                                                "picks_generated": 2}))
    app = create_app(":memory:")
    Base.metadata.create_all(app.state.engine)
    return TestClient(app)


@pytest.fixture()
def client_with_strategies(client):
    """Same, plus an active game strategy so picks are generated."""
    from backend.database import get_session

    session = get_session(client.app.state.engine)
    session.add(StrategyModel(id=1, name="ensemble", config_json="{}",
                              is_active=True, strategy_type="game"))
    session.commit()
    session.close()
    return client


def test_run_completes_and_reports_what_each_step_stored(client):
    resp = client.post("/pipeline/run?sport=nba")
    assert resp.status_code == 200
    data = resp.json()

    # "completed" specifically -- not "completed or error", which the old
    # assertion allowed and which a 200 makes unreachable anyway.
    assert data["status"] == "completed"
    assert data["games_stored"] == 7
    assert data["props_analyzed"] == 5


def test_the_sport_filter_scopes_the_run(client):
    resp = client.post("/pipeline/run?sport=nba")
    assert resp.json()["active_sports"] == ["nba"]
    pipeline_api.fetch_and_store_games.assert_awaited_once()
    assert pipeline_api.fetch_and_store_games.await_args.args[1] == ["nba"]


def test_without_a_sport_only_in_season_sports_run(client):
    """Season membership is computed, not taken on trust."""
    resp = client.post("/pipeline/run")
    active = resp.json()["active_sports"]

    in_season = [s for s in ("nba", "mlb")
                 if pipeline_api.is_sport_in_season(s, CONFIG["seasons"],
                                                    date.today())]
    assert active == in_season


def test_odds_are_not_fetched_without_an_api_key(client):
    """config has no odds_api_key, so those steps must be skipped entirely."""
    resp = client.post("/pipeline/run?sport=nba")

    pipeline_api.fetch_and_store_odds.assert_not_awaited()
    pipeline_api.fetch_and_store_props.assert_not_awaited()
    assert resp.json()["odds_stored"] == 0
    assert resp.json()["props_stored"] == 0


def test_odds_are_fetched_when_a_key_is_configured(client, monkeypatch):
    keyed = dict(CONFIG, odds_api_key="a-key")
    monkeypatch.setattr(pipeline_api, "load_config", lambda _p: dict(keyed))

    data = client.post("/pipeline/run?sport=nba").json()

    pipeline_api.fetch_and_store_odds.assert_awaited_once()
    assert data["odds_stored"] == 11
    assert data["props_stored"] == 13


def test_picks_generated_sums_game_and_prop_picks(client_with_strategies):
    data = client_with_strategies.post("/pipeline/run?sport=nba").json()

    # 3 game picks from generate_and_store_picks + 2 from the prop pipeline.
    assert data["picks_generated"] == 5


def test_no_game_strategy_means_no_game_picks(client):
    """With no active strategy seeded, only the prop pipeline contributes."""
    data = client.post("/pipeline/run?sport=nba").json()

    pipeline_api.generate_and_store_picks.assert_not_called()
    assert data["picks_generated"] == 2


def test_credit_fields_are_present(client):
    data = client.post("/pipeline/run?sport=nba").json()
    for field in ("credits_used", "credits_remaining_today",
                  "credits_remaining_month"):
        assert field in data


def test_a_collector_failure_returns_500_with_an_error_id(client, monkeypatch):
    monkeypatch.setattr(pipeline_api, "fetch_and_store_games",
                        AsyncMock(side_effect=RuntimeError("ESPN exploded")))

    resp = client.post("/pipeline/run?sport=nba")

    assert resp.status_code == 500
    data = resp.json()
    assert data["status"] == "error"
    assert data["error_id"]
    # The client is told nothing about what broke.
    assert "ESPN exploded" not in data["message"]


def test_an_api_key_in_an_exception_is_not_returned_to_the_client(
    client, monkeypatch
):
    """redact_api_key guards the log; the response must not carry it either."""
    keyed = dict(CONFIG, odds_api_key="SECRETKEY123")
    monkeypatch.setattr(pipeline_api, "load_config", lambda _p: dict(keyed))
    monkeypatch.setattr(
        pipeline_api, "fetch_and_store_odds",
        AsyncMock(side_effect=RuntimeError("401 for apiKey=SECRETKEY123")),
    )

    resp = client.post("/pipeline/run?sport=nba")

    assert resp.status_code == 500
    assert "SECRETKEY123" not in resp.text


def test_a_prop_pipeline_failure_does_not_fail_the_run(client, monkeypatch):
    """Props are best-effort: the endpoint catches and carries on."""
    monkeypatch.setattr(pipeline_api, "run_prop_pipeline",
                        AsyncMock(side_effect=RuntimeError("prop boom")))

    data = client.post("/pipeline/run?sport=nba").json()

    assert data["status"] == "completed"
    assert data["games_stored"] == 7
    assert data["props_analyzed"] == 0

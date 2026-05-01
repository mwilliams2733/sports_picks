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


def test_all_sports_includes_mlb():
    from backend.pipeline.full_pipeline import ALL_SPORTS
    assert "mlb" in ALL_SPORTS


import pytest

@pytest.mark.asyncio
async def test_run_mlb_window_fetches_pitcher_scores(httpx_mock):
    """An MLB pipeline run should hit MLB Stats API once for schedule and once
    per probable pitcher, then return scores keyed by (home_abbr, away_abbr)."""
    from datetime import date as _date
    from backend.pipeline.scheduler import fetch_pitcher_scores_for_date

    # Schedule response: one game with two probable pitchers.
    httpx_mock.add_response(
        url="https://statsapi.mlb.com/api/v1/schedule?sportId=1&date=2026-04-29&hydrate=probablePitcher",
        json={"dates": [{"games": [{
            "gamePk": 700001,
            "gameDate": "2026-04-29T23:05:00Z",
            "teams": {
                "home": {"team": {"id": 111, "abbreviation": "BOS"},
                         "probablePitcher": {"id": 5001, "fullName": "A. Pitcher"}},
                "away": {"team": {"id": 147, "abbreviation": "NYY"},
                         "probablePitcher": {"id": 5002, "fullName": "B. Pitcher"}},
            },
        }]}]},
    )
    httpx_mock.add_response(
        url="https://statsapi.mlb.com/api/v1/people/5001/stats?stats=gameLog&group=pitching&season=2026",
        json={"stats": [{"splits": [{"stat": {"era": "2.50", "strikeOuts": 8, "inningsPitched": "6.0"}}]}]},
    )
    httpx_mock.add_response(
        url="https://statsapi.mlb.com/api/v1/people/5002/stats?stats=gameLog&group=pitching&season=2026",
        json={"stats": [{"splits": [{"stat": {"era": "5.50", "strikeOuts": 4, "inningsPitched": "5.0"}}]}]},
    )

    scores = await fetch_pitcher_scores_for_date(_date(2026, 4, 29))
    # Keyed by (home_abbr, away_abbr) tuple — sport-agnostic, no internal IDs.
    assert ("BOS", "NYY") in scores
    assert scores[("BOS", "NYY")]["home"] > 0.6  # ace-ish ERA 2.50
    assert scores[("BOS", "NYY")]["away"] < 0.4  # bad outing ERA 5.50

import httpx
import pytest
from backend.collectors.espn import ESPNCollector

MOCK_SCOREBOARD = {
    "events": [
        {
            "id": "401585001",
            "date": "2026-03-13T00:00Z",
            "status": {"type": {"name": "STATUS_FINAL"}},
            "competitions": [{
                "competitors": [
                    {"id": "2", "homeAway": "home", "team": {"abbreviation": "BOS", "displayName": "Boston Celtics"}, "score": "112"},
                    {"id": "13", "homeAway": "away", "team": {"abbreviation": "LAL", "displayName": "Los Angeles Lakers"}, "score": "105"}
                ]
            }]
        }
    ]
}

@pytest.fixture
def mock_espn(monkeypatch):
    async def mock_get(self, url, **kwargs):
        request = httpx.Request("GET", url)
        response = httpx.Response(200, json=MOCK_SCOREBOARD, request=request)
        return response
    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)

@pytest.mark.asyncio
async def test_fetch_scoreboard(mock_espn):
    collector = ESPNCollector()
    games = await collector.fetch_scoreboard("nba", "20260313")
    assert len(games) == 1
    assert games[0]["home_team"] == "BOS"
    assert games[0]["away_team"] == "LAL"
    assert games[0]["home_score"] == 112
    assert games[0]["away_score"] == 105
    assert games[0]["status"] == "final"

def test_mlb_scoreboard_url_present():
    from backend.collectors.espn import SPORT_URLS
    url = SPORT_URLS.get("mlb")
    assert url is not None
    assert "baseball/mlb/scoreboard" in url

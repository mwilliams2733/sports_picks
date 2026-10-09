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


LIVE_SCOREBOARD = {"events": [{
    "id": "401", "date": "2026-10-11T17:00Z",
    "status": {"type": {"name": "STATUS_HALFTIME", "state": "in", "shortDetail": "Halftime"}},
    "competitions": [{"competitors": [
        {"id": "1", "homeAway": "home", "team": {"abbreviation": "DAL", "displayName": "Dallas Cowboys"}, "score": "17"},
        {"id": "2", "homeAway": "away", "team": {"abbreviation": "TB", "displayName": "Tampa Bay Buccaneers"}, "score": "14"},
    ]}],
}]}


@pytest.mark.asyncio
async def test_fetch_scoreboard_reports_the_live_state_and_clock(monkeypatch):
    async def mock_get(self, url, **kwargs):
        return httpx.Response(200, json=LIVE_SCOREBOARD, request=httpx.Request("GET", url))
    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)
    [g] = await ESPNCollector().fetch_scoreboard("nfl", "20261011")
    # Halftime's NAME maps to "scheduled"; its STATE says the game is on.
    assert (g["status"], g["state"], g["live_detail"]) == ("scheduled", "in", "Halftime")
    assert (g["home_score"], g["away_score"]) == (17, 14)

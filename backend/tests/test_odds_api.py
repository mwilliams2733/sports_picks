import httpx
import pytest
from backend.collectors.odds_api import OddsAPICollector

MOCK_ODDS = [
    {
        "id": "abc123",
        "home_team": "Boston Celtics",
        "away_team": "Los Angeles Lakers",
        "commence_time": "2026-03-13T00:00:00Z",
        "bookmakers": [
            {
                "key": "draftkings",
                "markets": [
                    {"key": "h2h", "outcomes": [
                        {"name": "Boston Celtics", "price": -150},
                        {"name": "Los Angeles Lakers", "price": 130}
                    ]},
                    {"key": "spreads", "outcomes": [
                        {"name": "Boston Celtics", "price": -110, "point": -4.5},
                        {"name": "Los Angeles Lakers", "price": -110, "point": 4.5}
                    ]},
                    {"key": "totals", "outcomes": [
                        {"name": "Over", "price": -110, "point": 218.5},
                        {"name": "Under", "price": -110, "point": 218.5}
                    ]}
                ]
            }
        ]
    }
]

@pytest.fixture
def mock_odds_api(monkeypatch):
    async def mock_get(self, url, **kwargs):
        request = httpx.Request("GET", url)
        return httpx.Response(200, json=MOCK_ODDS, headers={"x-requests-remaining": "498"}, request=request)
    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)

@pytest.mark.asyncio
async def test_fetch_odds(mock_odds_api):
    collector = OddsAPICollector(api_key="test_key")
    odds = await collector.fetch_odds("nba")
    assert len(odds) == 1
    game_odds = odds[0]
    assert game_odds["home_team"] == "Boston Celtics"
    assert len(game_odds["bookmakers"]) == 1
    bk = game_odds["bookmakers"][0]
    assert bk["moneyline_home"] == -150
    assert bk["spread_home"] == -4.5
    assert bk["over_under"] == 218.5

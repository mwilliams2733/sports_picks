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


def test_mlb_is_in_sport_keys():
    """MLB must map to The Odds API's baseball_mlb key for fetch_odds to work."""
    from backend.collectors.odds_api import SPORT_KEYS
    assert SPORT_KEYS["mlb"] == "baseball_mlb"


def test_mlb_prop_markets_defined():
    """MLB prop market list must contain all four documented batter/pitcher markets."""
    from backend.collectors.odds_api import PROP_MARKETS
    assert set(PROP_MARKETS["mlb"]) == {
        "batter_hits", "batter_home_runs", "batter_total_bases", "pitcher_strikeouts",
    }


@pytest.mark.asyncio
async def test_fetch_odds_for_mlb_requests_full_market_set(monkeypatch):
    """MLB fetch_odds must request h2h, spreads, AND totals — not h2h-only like combat sports."""
    captured = {}

    async def mock_get(self, url, **kwargs):
        captured["params"] = kwargs.get("params", {})
        request = httpx.Request("GET", url)
        return httpx.Response(200, json=[], headers={"x-requests-remaining": "490"}, request=request)

    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)

    collector = OddsAPICollector(api_key="test_key")
    await collector.fetch_odds("mlb")

    markets_param = captured["params"].get("markets", "")
    markets = [m.strip() for m in markets_param.split(",")]
    assert "spreads" in markets, f"Expected 'spreads' in markets param, got: {markets_param!r}"
    assert "totals" in markets, f"Expected 'totals' in markets param, got: {markets_param!r}"

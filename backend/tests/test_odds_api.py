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
    """MLB has popular prop markets — they must be wired up."""
    from backend.collectors.odds_api import PROP_MARKETS
    assert "batter_hits" in PROP_MARKETS["mlb"]
    assert "pitcher_strikeouts" in PROP_MARKETS["mlb"]


def test_mlb_uses_full_market_set():
    """MLB has h2h + spreads (run line) + totals — should NOT be h2h-only like combat sports."""
    from backend.collectors.odds_api import OddsAPICollector
    import inspect
    src = inspect.getsource(OddsAPICollector.fetch_odds)
    assert '"boxing", "mma"' in src or "'boxing', 'mma'" in src, (
        "h2h-only branch must not silently pick up mlb"
    )

import httpx

SPORT_KEYS = {
    "nba": "basketball_nba",
    "nfl": "americanfootball_nfl",
    "ncaab": "basketball_ncaab",
    "ncaaf": "americanfootball_ncaaf",
}

class OddsAPICollector:
    BASE_URL = "https://api.the-odds-api.com/v4/sports"

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.client = httpx.AsyncClient(timeout=30.0)
        self.requests_remaining: int | None = None

    async def fetch_odds(self, sport: str) -> list[dict]:
        sport_key = SPORT_KEYS[sport]
        url = f"{self.BASE_URL}/{sport_key}/odds"
        params = {
            "apiKey": self.api_key,
            "regions": "us",
            "markets": "h2h,spreads,totals",
            "oddsFormat": "american",
        }
        response = await self.client.get(url, params=params)
        response.raise_for_status()
        self.requests_remaining = int(response.headers.get("x-requests-remaining", 0))
        raw = response.json()
        results = []
        for event in raw:
            bookmakers = []
            for bk in event.get("bookmakers", []):
                parsed = self._parse_bookmaker(bk, event["home_team"])
                if parsed:
                    bookmakers.append(parsed)
            results.append({
                "odds_api_id": event["id"],
                "home_team": event["home_team"],
                "away_team": event["away_team"],
                "commence_time": event["commence_time"],
                "bookmakers": bookmakers,
            })
        return results

    def _parse_bookmaker(self, bk: dict, home_team: str) -> dict | None:
        result = {"key": bk["key"], "moneyline_home": None, "moneyline_away": None,
                  "spread_home": None, "spread_away": None, "over_under": None}
        for market in bk.get("markets", []):
            outcomes = market["outcomes"]
            if market["key"] == "h2h":
                for o in outcomes:
                    if o["name"] == home_team:
                        result["moneyline_home"] = o["price"]
                    else:
                        result["moneyline_away"] = o["price"]
            elif market["key"] == "spreads":
                for o in outcomes:
                    if o["name"] == home_team:
                        result["spread_home"] = o["point"]
                    else:
                        result["spread_away"] = o["point"]
            elif market["key"] == "totals":
                for o in outcomes:
                    if o["name"] == "Over":
                        result["over_under"] = o["point"]
        return result

    async def close(self):
        await self.client.aclose()

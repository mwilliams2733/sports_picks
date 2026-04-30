import httpx

SPORT_KEYS = {
    "nba": "basketball_nba",
    "nfl": "americanfootball_nfl",
    "ncaab": "basketball_ncaab",
    "ncaaf": "americanfootball_ncaaf",
    "boxing": "boxing_boxing",
    "mma": "mma_mixed_martial_arts",
    "mlb": "baseball_mlb",
}

class OddsAPICollector:
    BASE_URL = "https://api.the-odds-api.com/v4/sports"

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.client = httpx.AsyncClient(timeout=30.0)
        self.requests_remaining: int | None = None

    async def fetch_odds(self, sport: str) -> list[dict]:
        sport_key = SPORT_KEYS.get(sport)
        if not sport_key:
            return []
        url = f"{self.BASE_URL}/{sport_key}/odds"
        # Combat sports typically only have h2h (moneyline)
        markets = "h2h" if sport in ("boxing", "mma") else "h2h,spreads,totals"
        params = {
            "apiKey": self.api_key,
            "regions": "us",
            "markets": markets,
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

    async def fetch_events(self, sport: str) -> list[dict]:
        """Fetch upcoming event IDs for a sport (needed for player props)."""
        sport_key = SPORT_KEYS.get(sport)
        if not sport_key:
            return []
        url = f"{self.BASE_URL}/{sport_key}/events"
        params = {"apiKey": self.api_key}
        response = await self.client.get(url, params=params)
        response.raise_for_status()
        self.requests_remaining = int(response.headers.get("x-requests-remaining", 0))
        return response.json()

    async def fetch_player_props(self, sport: str, event_id: str, markets: list[str] | None = None) -> list[dict]:
        """Fetch player prop odds for a specific event."""
        sport_key = SPORT_KEYS.get(sport)
        if not sport_key:
            return []
        if markets is None:
            markets = PROP_MARKETS.get(sport, [])
        if not markets:
            return []
        url = f"{self.BASE_URL}/{sport_key}/events/{event_id}/odds"
        params = {
            "apiKey": self.api_key,
            "regions": "us",
            "markets": ",".join(markets),
            "oddsFormat": "american",
        }
        response = await self.client.get(url, params=params)
        response.raise_for_status()
        self.requests_remaining = int(response.headers.get("x-requests-remaining", 0))
        data = response.json()

        props = []
        for bk in data.get("bookmakers", []):
            for market in bk.get("markets", []):
                for outcome in market.get("outcomes", []):
                    if "description" not in outcome:
                        continue
                    props.append({
                        "event_id": event_id,
                        "bookmaker": bk["key"],
                        "market": market["key"],
                        "player_name": outcome["description"],
                        "outcome": outcome["name"],  # "Over" or "Under"
                        "line": outcome.get("point"),
                        "odds": outcome["price"],
                    })
        return props

    async def close(self):
        await self.client.aclose()


# Key prop markets per sport (most popular, keeps API usage low)
PROP_MARKETS = {
    "nba": ["player_points", "player_rebounds", "player_assists", "player_threes"],
    "nfl": ["player_pass_yds", "player_rush_yds", "player_reception_yds", "player_anytime_td"],
    "ncaab": ["player_points", "player_rebounds", "player_assists"],
    "ncaaf": ["player_pass_yds", "player_rush_yds", "player_anytime_td"],
    "boxing": [],  # Limited prop markets available
    "mma": [],     # Limited prop markets available
    "mlb": ["batter_hits", "batter_home_runs", "batter_total_bases", "pitcher_strikeouts"],
}

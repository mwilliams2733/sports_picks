import httpx

SPORT_URLS = {
    "nba": "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard",
    "nfl": "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard",
    "ncaab": "https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/scoreboard",
    "ncaaf": "https://site.api.espn.com/apis/site/v2/sports/football/college-football/scoreboard",
    "mma": "https://site.api.espn.com/apis/site/v2/sports/mma/ufc/scoreboard",
}
# Boxing has no ESPN scoreboard API — games come from Odds API only

STATUS_MAP = {
    "STATUS_SCHEDULED": "scheduled",
    "STATUS_IN_PROGRESS": "in_progress",
    "STATUS_FINAL": "final",
    "STATUS_POSTPONED": "postponed",
    "STATUS_CANCELED": "cancelled",
}

class ESPNCollector:
    def __init__(self):
        self.client = httpx.AsyncClient(timeout=30.0)

    async def fetch_scoreboard(self, sport: str, date_str: str) -> list[dict]:
        url = SPORT_URLS.get(sport)
        if not url:
            return []
        response = await self.client.get(url, params={"dates": date_str})
        response.raise_for_status()
        data = response.json()
        games = []
        for event in data.get("events", []):
            competition = event["competitions"][0]
            competitors = competition["competitors"]
            home = next(c for c in competitors if c["homeAway"] == "home")
            away = next(c for c in competitors if c["homeAway"] == "away")
            espn_status = event["status"]["type"]["name"]
            games.append({
                "espn_id": event["id"],
                "date": event["date"],
                "status": STATUS_MAP.get(espn_status, "scheduled"),
                "home_team": home["team"]["abbreviation"],
                "home_team_name": home["team"]["displayName"],
                "away_team": away["team"]["abbreviation"],
                "away_team_name": away["team"]["displayName"],
                "home_score": int(home.get("score", 0)) if home.get("score") else None,
                "away_score": int(away.get("score", 0)) if away.get("score") else None,
            })
        return games

    async def close(self):
        await self.client.aclose()

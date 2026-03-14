import logging

import httpx

from backend.collectors.player_stats.base import PlayerStatsSource

logger = logging.getLogger(__name__)

BASE_URL = "https://api.balldontlie.io/v1"


class BallDontLieSource(PlayerStatsSource):
    name = "balldontlie"

    def __init__(self, api_key: str | None = None):
        headers = {}
        if api_key:
            headers["Authorization"] = api_key
        self._client = httpx.AsyncClient(base_url=BASE_URL, headers=headers, timeout=15.0)

    async def fetch_season_averages(self, sport: str, team_abbr: str) -> list[dict]:
        """BallDontLie has limited team roster support; return empty."""
        if sport != "nba":
            return []
        return []

    async def fetch_recent_games(self, sport: str, player_name: str, n: int = 5) -> list[dict]:
        if sport != "nba":
            return []

        # Search player by last name
        name_parts = player_name.strip().split()
        last_name = name_parts[-1] if name_parts else player_name

        try:
            resp = await self._client.get("/players", params={"search": last_name, "per_page": 10})
            resp.raise_for_status()
            search_data = resp.json()
        except Exception as e:
            logger.error(f"BallDontLie: player search failed for '{player_name}': {e}")
            return []

        players = search_data.get("data", [])
        if not players:
            logger.warning(f"BallDontLie: no player found for '{player_name}'")
            return []

        # Find best match by full name
        player_id = None
        player_name_lower = player_name.lower()
        for p in players:
            full = f"{p.get('first_name', '')} {p.get('last_name', '')}".lower()
            if full == player_name_lower:
                player_id = p["id"]
                break
        if player_id is None:
            player_id = players[0]["id"]

        try:
            resp = await self._client.get(
                "/stats",
                params={"player_ids[]": player_id, "per_page": n, "sort": "date", "direction": "desc"},
            )
            resp.raise_for_status()
            stats_data = resp.json()
        except Exception as e:
            logger.error(f"BallDontLie: stats fetch failed for '{player_name}': {e}")
            return []

        results = []
        for entry in stats_data.get("data", [])[:n]:
            game = entry.get("game", {})
            game_date = game.get("date", "")
            if game_date:
                game_date = game_date[:10]  # strip time if present

            results.append({
                "game_date": game_date,
                "player_name": player_name,
                "minutes": self._parse_minutes(entry.get("min")),
                "points": entry.get("pts"),
                "rebounds": entry.get("reb"),
                "assists": entry.get("ast"),
                "threes": entry.get("fg3m"),
                "steals": entry.get("stl"),
                "blocks": entry.get("blk"),
                "turnovers": entry.get("turnover"),
                "pass_yards": None,
                "rush_yards": None,
                "rec_yards": None,
                "touchdowns": None,
            })

        return results

    def _parse_minutes(self, min_str) -> float | None:
        """Convert '32:15' or '32' to float minutes."""
        if min_str is None:
            return None
        try:
            if isinstance(min_str, str) and ":" in min_str:
                parts = min_str.split(":")
                return float(parts[0]) + float(parts[1]) / 60
            return float(min_str)
        except (ValueError, TypeError):
            return None

    async def is_available(self) -> bool:
        try:
            resp = await self._client.get("/teams", params={"per_page": 1}, timeout=5.0)
            return resp.status_code == 200
        except Exception:
            return False

    async def close(self):
        await self._client.aclose()

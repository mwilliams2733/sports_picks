from backend.collectors.player_stats.base import PlayerStatsSource


class MySportsFeedsSource(PlayerStatsSource):
    """Placeholder stub for MySportsFeeds API."""
    name = "mysportsfeeds"

    async def fetch_season_averages(self, sport: str, team_abbr: str) -> list[dict]:
        return []

    async def fetch_recent_games(self, sport: str, player_name: str, n: int = 5) -> list[dict]:
        return []

    async def is_available(self) -> bool:
        return False

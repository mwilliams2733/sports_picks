from abc import ABC, abstractmethod


class PlayerStatsSource(ABC):
    """Interface for player stats data sources."""
    name: str = "base"

    @abstractmethod
    async def fetch_season_averages(self, sport: str, team_abbr: str) -> list[dict]:
        """Return season averages for all players on a team.
        Each dict must have: player_name, minutes, points, rebounds, assists,
        threes, steals, blocks, turnovers. Football adds: pass_yards, rush_yards,
        rec_yards, touchdowns. Missing values should be None.
        """

    @abstractmethod
    async def fetch_recent_games(self, sport: str, player_name: str, n: int = 5) -> list[dict]:
        """Return last N game logs for a player.
        Each dict must have: game_date (YYYY-MM-DD string), plus same stat fields.
        """

    @abstractmethod
    async def is_available(self) -> bool:
        """Health check."""

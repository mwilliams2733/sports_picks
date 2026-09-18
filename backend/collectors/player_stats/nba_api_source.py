import asyncio
import logging
import time

from backend.collectors.player_stats.base import PlayerStatsSource

logger = logging.getLogger(__name__)

try:
    from nba_api.stats.endpoints import CommonTeamRoster, PlayerCareerStats, PlayerGameLog
    from nba_api.stats.static import players as nba_players
    NBA_API_AVAILABLE = True
except ImportError:
    NBA_API_AVAILABLE = False

NBA_TEAMS: dict[str, int] = {
    "ATL": 1610612737,
    "BOS": 1610612738,
    "BKN": 1610612751,
    "CHA": 1610612766,
    "CHI": 1610612741,
    "CLE": 1610612739,
    "DAL": 1610612742,
    "DEN": 1610612743,
    "DET": 1610612765,
    "GSW": 1610612744,
    "HOU": 1610612745,
    "IND": 1610612754,
    "LAC": 1610612746,
    "LAL": 1610612747,
    "MEM": 1610612763,
    "MIA": 1610612748,
    "MIL": 1610612749,
    "MIN": 1610612750,
    "NOP": 1610612740,
    "NYK": 1610612752,
    "OKC": 1610612760,
    "ORL": 1610612753,
    "PHI": 1610612755,
    "PHX": 1610612756,
    "POR": 1610612757,
    "SAC": 1610612758,
    "SAS": 1610612759,
    "TOR": 1610612761,
    "UTA": 1610612762,
    "WAS": 1610612764,
}


class NbaApiSource(PlayerStatsSource):
    name = "nba_api"

    def __init__(self):
        self._player_id_cache: dict[str, int] = {}

    async def fetch_season_averages(self, sport: str, team_abbr: str) -> list[dict]:
        if sport != "nba":
            return []
        if not NBA_API_AVAILABLE:
            logger.warning("nba_api package not installed")
            return []

        team_id = NBA_TEAMS.get(team_abbr.upper())
        if team_id is None:
            logger.warning(f"Unknown NBA team abbreviation: {team_abbr}")
            return []

        loop = asyncio.get_running_loop()

        try:
            def _get_roster():
                time.sleep(0.6)
                roster = CommonTeamRoster(team_id=team_id)
                return roster.get_normalized_dict()

            roster_data = await loop.run_in_executor(None, _get_roster)
            players = roster_data.get("CommonTeamRoster", [])
        except Exception as e:
            logger.error(f"Failed to fetch roster for {team_abbr}: {e}")
            return []

        results = []
        for player in players:
            player_id = player.get("PLAYER_ID")
            player_name = player.get("PLAYER", "")
            if not player_id:
                continue

            self._player_id_cache[player_name.lower()] = player_id

            try:
                def _get_career(pid=player_id):
                    time.sleep(0.6)
                    career = PlayerCareerStats(player_id=pid)
                    return career.get_normalized_dict()

                career_data = await loop.run_in_executor(None, _get_career)
                season_totals = career_data.get("SeasonTotalsRegularSeason", [])

                if not season_totals:
                    continue

                # Use most recent season
                latest = season_totals[-1]
                gp = latest.get("GP", 1) or 1

                results.append({
                    "player_name": player_name,
                    "minutes": round(latest.get("MIN", 0) / gp, 1) if latest.get("MIN") else None,
                    "points": round(latest.get("PTS", 0) / gp, 1) if latest.get("PTS") else None,
                    "rebounds": round(latest.get("REB", 0) / gp, 1) if latest.get("REB") else None,
                    "assists": round(latest.get("AST", 0) / gp, 1) if latest.get("AST") else None,
                    "threes": round(latest.get("FG3M", 0) / gp, 1) if latest.get("FG3M") else None,
                    "steals": round(latest.get("STL", 0) / gp, 1) if latest.get("STL") else None,
                    "blocks": round(latest.get("BLK", 0) / gp, 1) if latest.get("BLK") else None,
                    "turnovers": round(latest.get("TOV", 0) / gp, 1) if latest.get("TOV") else None,
                    "pass_yards": None,
                    "rush_yards": None,
                    "rec_yards": None,
                    "touchdowns": None,
                })
            except Exception as e:
                logger.warning(f"Failed to fetch career stats for {player_name}: {e}")
                continue

        return results

    async def fetch_recent_games(self, sport: str, player_name: str, n: int = 5) -> list[dict]:
        if sport != "nba":
            return []
        if not NBA_API_AVAILABLE:
            logger.warning("nba_api package not installed")
            return []

        loop = asyncio.get_running_loop()

        # Try cache first, then search
        player_id = self._player_id_cache.get(player_name.lower())
        if player_id is None:
            try:
                def _find_player():
                    return nba_players.find_players_by_full_name(player_name)

                matches = await loop.run_in_executor(None, _find_player)
                if not matches:
                    logger.warning(f"No NBA player found for: {player_name}")
                    return []
                player_id = matches[0]["id"]
                self._player_id_cache[player_name.lower()] = player_id
            except Exception as e:
                logger.error(f"Failed to find player ID for {player_name}: {e}")
                return []

        try:
            def _get_gamelog(pid=player_id):
                time.sleep(0.6)
                # No last_n_games parameter exists on PlayerGameLog; passing it
                # raised TypeError before any request, and the chain's blanket
                # except logged that as a source failure. The games[:n] slice
                # below is what limits the result.
                log = PlayerGameLog(player_id=pid)
                return log.get_normalized_dict()

            log_data = await loop.run_in_executor(None, _get_gamelog)
            games = log_data.get("PlayerGameLog", [])
        except Exception as e:
            logger.error(f"Failed to fetch game log for {player_name}: {e}")
            return []

        results = []
        for g in games[:n]:
            # GAME_DATE format: "MAR 10, 2026" -> need YYYY-MM-DD
            raw_date = g.get("GAME_DATE", "")
            try:
                from datetime import datetime
                dt = datetime.strptime(raw_date, "%b %d, %Y")
                game_date = dt.strftime("%Y-%m-%d")
            except Exception:
                game_date = raw_date

            results.append({
                "game_date": game_date,
                "player_name": player_name,
                "minutes": g.get("MIN"),
                "points": g.get("PTS"),
                "rebounds": g.get("REB"),
                "assists": g.get("AST"),
                "threes": g.get("FG3M"),
                "steals": g.get("STL"),
                "blocks": g.get("BLK"),
                "turnovers": g.get("TOV"),
                "pass_yards": None,
                "rush_yards": None,
                "rec_yards": None,
                "touchdowns": None,
            })

        return results

    async def is_available(self) -> bool:
        return NBA_API_AVAILABLE

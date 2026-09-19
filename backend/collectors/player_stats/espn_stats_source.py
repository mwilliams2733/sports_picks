import logging
from typing import Any

import httpx

from backend import team_identity
from backend.collectors.player_stats.base import PlayerStatsSource

logger = logging.getLogger(__name__)

ESPN_SPORT_URLS: dict[str, dict[str, str]] = {
    "nba": {
        "teams": "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/teams",
        "scoreboard": "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard",
        "base": "https://site.api.espn.com/apis/site/v2/sports/basketball/nba",
    },
    "nfl": {
        "teams": "https://site.api.espn.com/apis/site/v2/sports/football/nfl/teams",
        "base": "https://site.api.espn.com/apis/site/v2/sports/football/nfl",
    },
    "ncaab": {
        "teams": "https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/teams",
        "base": "https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball",
    },
    "ncaaf": {
        "teams": "https://site.api.espn.com/apis/site/v2/sports/football/college-football/teams",
        "base": "https://site.api.espn.com/apis/site/v2/sports/football/college-football",
    },
    "mma": {
        "base": "https://site.api.espn.com/apis/site/v2/sports/mma/ufc",
    },
    "boxing": {
        "base": "https://site.api.espn.com/apis/site/v2/sports/boxing",
    },
}

FOOTBALL_SPORTS = {"nfl", "ncaaf"}


class EspnStatsSource(PlayerStatsSource):
    name = "espn"

    def __init__(self):
        self._client = httpx.AsyncClient(timeout=15.0)
        #: sport -> {ABBREVIATION: espn_id}, filled only for sports with no
        #: committed snapshot. One request per sport per process, not per team.
        self._team_ids: dict[str, dict[str, str]] = {}

    async def fetch_season_averages(self, sport: str, team_abbr: str) -> list[dict]:
        sport = sport.lower()
        if sport not in ESPN_SPORT_URLS:
            logger.warning(f"ESPN: unsupported sport '{sport}'")
            return []

        urls = ESPN_SPORT_URLS[sport]
        team_id = await self._find_team_id(sport, team_abbr)
        if not team_id:
            logger.warning(f"ESPN: team '{team_abbr}' not found for sport '{sport}'")
            return []

        base = urls["base"]
        roster_url = f"{base}/teams/{team_id}/roster"
        try:
            resp = await self._client.get(roster_url)
            resp.raise_for_status()
            roster_data = resp.json()
        except Exception as e:
            logger.error(f"ESPN: failed to fetch roster for {team_abbr}: {e}")
            return []

        athletes = []
        for group in roster_data.get("athletes", []):
            athletes.extend(group.get("items", []))

        results = []
        for athlete in athletes:
            athlete_id = athlete.get("id")
            player_name = athlete.get("fullName", "")
            if not athlete_id:
                continue

            stats_url = f"{base}/athletes/{athlete_id}/statistics"
            try:
                resp = await self._client.get(stats_url)
                resp.raise_for_status()
                stats_data = resp.json()
            except Exception as e:
                logger.warning(f"ESPN: failed stats for {player_name}: {e}")
                continue

            if sport in FOOTBALL_SPORTS:
                parsed = self._parse_football_stats(stats_data)
            else:
                parsed = self._parse_basketball_stats(stats_data)

            parsed["player_name"] = player_name
            results.append(parsed)

        return results

    async def _find_team_id(self, sport: str, team_abbr: str) -> str | None:
        """ESPN's numeric team id for ``team_abbr``, or None if unresolvable.

        This method was called but never defined, so every call to
        ``fetch_season_averages`` raised AttributeError and the collector's
        blanket ``except`` recorded it as a source failure.

        Who that actually cost, counted against the sports whose props the
        pipeline analyses rather than against the chain table: **nfl, ncaab
        and ncaaf** list ESPN as their only source, so season averages were
        unreachable there, not merely degraded. nba lost only its third
        fallback, behind nba_api and balldontlie. boxing and mma sit in the
        chain but ``odds_api.PROP_MARKETS`` is empty for both, so no prop is
        ever fetched for them and nothing downstream depended on this.

        Offline first: ``backend/data/<sport>_teams.json`` is committed and
        carries every id, so nba and ncaab resolve with no request at all.
        Sports with no snapshot fall back to ESPN's teams endpoint, cached per
        sport. Combat sports have no teams endpoint and resolve to None --
        a fighter is not a team, so that is the right answer, not an error.
        """
        espn_id = team_identity.espn_team_id(sport, team_abbr)
        if espn_id:
            return espn_id

        teams_url = ESPN_SPORT_URLS[sport].get("teams")
        if not teams_url:
            return None

        table = await self._teams_table(sport, teams_url)
        return table.get(team_abbr.upper())

    async def _teams_table(self, sport: str, teams_url: str) -> dict[str, str]:
        """{ABBREVIATION: espn_id} for one sport, fetched at most once.

        A failed fetch is deliberately not cached, so a transient outage does
        not blank the table for the life of the process.
        """
        cached = self._team_ids.get(sport)
        if cached is not None:
            return cached

        try:
            # limit=500: ESPN pages at 50 by default, which silently truncates
            # ncaaf and ncaab. refresh_team_tables.py passes the same.
            resp = await self._client.get(teams_url, params={"limit": 500})
            resp.raise_for_status()
            payload = resp.json()
        except Exception as e:
            logger.error(f"ESPN: failed to fetch team table for {sport}: {e}")
            return {}

        table: dict[str, str] = {}
        # Walked rather than indexed: refresh_team_tables.py reads
        # payload["sports"][0]["leagues"][0]["teams"], which raises on an
        # empty or reshaped response. Here an unexpected shape must degrade
        # to "team not found", not to an exception inside a fallback chain.
        for sport_block in payload.get("sports", []):
            for league in sport_block.get("leagues", []):
                for entry in league.get("teams", []):
                    team = entry.get("team", {})
                    abbr, team_id = team.get("abbreviation"), team.get("id")
                    if abbr and team_id:
                        table[abbr.upper()] = str(team_id)

        if not table:
            logger.warning(f"ESPN: team table for {sport} parsed to nothing")
        self._team_ids[sport] = table
        return table

    async def fetch_recent_games(self, sport: str, player_name: str, n: int = 5) -> list[dict]:
        sport = sport.lower()
        if sport not in ESPN_SPORT_URLS:
            return []

        urls = ESPN_SPORT_URLS[sport]
        base = urls["base"]

        # Search athlete by name
        search_url = f"{base}/athletes"
        try:
            resp = await self._client.get(search_url, params={"limit": 50, "search": player_name})
            resp.raise_for_status()
            search_data = resp.json()
        except Exception as e:
            logger.error(f"ESPN: athlete search failed for {player_name}: {e}")
            return []

        items = search_data.get("items", [])
        if not items:
            logger.warning(f"ESPN: no athlete found for '{player_name}'")
            return []

        athlete_id = items[0].get("id")
        if not athlete_id:
            return []

        gamelog_url = f"{base}/athletes/{athlete_id}/gamelog"
        try:
            resp = await self._client.get(gamelog_url)
            resp.raise_for_status()
            gamelog_data = resp.json()
        except Exception as e:
            logger.error(f"ESPN: gamelog fetch failed for {player_name}: {e}")
            return []

        return self._parse_gamelog(gamelog_data, sport, player_name, n)

    def _parse_gamelog(self, data: dict, sport: str, player_name: str, n: int) -> list[dict]:
        results = []
        events = data.get("events", {})
        # events is typically a dict keyed by event id
        for event_id, event_data in list(events.items())[:n]:
            game_date = event_data.get("gameDate", "")[:10] if event_data.get("gameDate") else ""
            stats_data = {"statistics": event_data.get("statistics", [])}

            if sport in FOOTBALL_SPORTS:
                parsed = self._parse_football_stats(stats_data)
            else:
                parsed = self._parse_basketball_stats(stats_data)

            parsed["game_date"] = game_date
            parsed["player_name"] = player_name
            results.append(parsed)

        return results

    def _parse_basketball_stats(self, data: dict) -> dict:
        """Extract basketball stats from ESPN statistics response."""
        stat_map = {
            "PTS": "points",
            "REB": "rebounds",
            "AST": "assists",
            "MIN": "minutes",
            "3PM": "threes",
            "STL": "steals",
            "BLK": "blocks",
            "TO": "turnovers",
        }
        return self._extract_stats(data, stat_map)

    def _parse_football_stats(self, data: dict) -> dict:
        """Extract football stats from ESPN statistics response."""
        stat_map = {
            "PYDS": "pass_yards",
            "RYDS": "rush_yards",
            "RECYDS": "rec_yards",
            "TD": "touchdowns",
            "MIN": "minutes",
        }
        return self._extract_stats(data, stat_map)

    def _extract_stats(self, data: dict, stat_map: dict[str, str]) -> dict:
        result: dict[str, Any] = {
            "player_name": None,
            "minutes": None,
            "points": None,
            "rebounds": None,
            "assists": None,
            "threes": None,
            "steals": None,
            "blocks": None,
            "turnovers": None,
            "pass_yards": None,
            "rush_yards": None,
            "rec_yards": None,
            "touchdowns": None,
        }

        statistics = data.get("statistics", [])
        for stat_group in statistics:
            splits = stat_group.get("splits", [])
            for split in splits:
                categories = split.get("categories", [])
                for category in categories:
                    for stat in category.get("stats", []):
                        abbr = stat.get("abbreviation", "")
                        if abbr in stat_map:
                            result[stat_map[abbr]] = stat.get("value")

        return result

    async def is_available(self) -> bool:
        try:
            url = ESPN_SPORT_URLS["nba"]["scoreboard"]
            resp = await self._client.get(url, timeout=5.0)
            return resp.status_code == 200
        except Exception:
            return False

    async def close(self):
        await self._client.aclose()

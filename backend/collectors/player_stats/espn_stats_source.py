import logging
from typing import Any

import httpx

from backend.collectors.player_stats.base import PlayerStatsSource
from backend.collectors.espn_http import get_with_retry

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

#: Athlete season stats live on a different host and API version from
#: everything else here. The site v2 ".../athletes/{id}/statistics" path the
#: collector used returns 404 for every athlete.
ESPN_V3_STATS: dict[str, str] = {
    "nba": "https://site.web.api.espn.com/apis/common/v3/sports/basketball/nba",
    "ncaab": "https://site.web.api.espn.com/apis/common/v3/sports/basketball/mens-college-basketball",
    "nfl": "https://site.web.api.espn.com/apis/common/v3/sports/football/nfl",
    "ncaaf": "https://site.web.api.espn.com/apis/common/v3/sports/football/college-football",
}

#: Basketball: one "averages" category whose labels line up with its stats.
_BASKETBALL_LABELS = {
    "MIN": "minutes", "PTS": "points", "REB": "rebounds", "AST": "assists",
    "3PT": "threes", "STL": "steals", "BLK": "blocks", "TO": "turnovers",
}

#: Football: keyed by (category, label), because "YDS" appears under
#: passing, rushing AND receiving. A flat label map would report whichever
#: category happened to come last as all three.
_FOOTBALL_LABELS = {
    ("passing", "YDS"): "pass_yards",
    ("rushing", "YDS"): "rush_yards",
    ("receiving", "YDS"): "rec_yards",
    ("scoring", "TD"): "touchdowns",
}


def _to_number(raw: Any) -> float | None:
    """ESPN stat string -> float, or None when it is not a number.

    Handles two real shapes: thousands separators ("1,912") and
    made-attempted pairs ("2.5-6.0"), where the made half is the statistic
    and the attempted half is not. A leading minus is preserved, so "-2"
    receiving yards survives.
    """
    if raw is None:
        return None
    text = str(raw).strip().replace(",", "")
    if not text or text in {"-", "--"}:
        return None
    if "-" in text[1:]:
        text = text[0] + text[1:].split("-", 1)[0]
    try:
        return float(text)
    except ValueError:
        return None


def _empty_stat_row() -> dict:
    return {
        "player_name": None, "minutes": None, "points": None,
        "rebounds": None, "assists": None, "threes": None, "steals": None,
        "blocks": None, "turnovers": None, "pass_yards": None,
        "rush_yards": None, "rec_yards": None, "touchdowns": None,
    }


def _latest_row(category: dict) -> list:
    """The most recent season's values for a v3 category.

    Rows run oldest to newest, so the last one is the current season. Taking
    the first would report a player's rookie year as their form.
    """
    rows = category.get("statistics") or []
    if not rows:
        return []
    return rows[-1].get("stats") or []


class EspnStatsSource(PlayerStatsSource):
    name = "espn"

    def __init__(self):
        self._client = httpx.AsyncClient(timeout=15.0)

    async def _find_team_id(self, sport: str, team_abbr: str) -> str | None:
        """ESPN's numeric team id for ``team_abbr``, or None.

        This method was called by fetch_season_averages but never written, so
        every season-average fetch raised AttributeError. The collector caught
        it and logged a warning, which is why it survived: 284 warnings per
        scheduled run and stats_fetched: 0, with nothing ever failing loudly.

        Resolved from the committed team snapshot rather than ESPN's /teams
        endpoint: no network call, and it cannot drift from the abbreviations
        team_identity resolves to, because both read the same file.
        """
        from backend.team_identity import espn_id_for

        team_id = espn_id_for(sport, team_abbr)
        if team_id is None:
            logger.warning(
                "ESPN: no team id for %r in %s. If the team is real, the "
                "snapshot is stale -- re-run "
                "backend.scripts.refresh_team_tables --sport %s",
                team_abbr, sport, sport,
            )
        return team_id

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
            resp = await get_with_retry(self._client, roster_url)
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

            stats_base = ESPN_V3_STATS.get(sport)
            if not stats_base:
                continue
            stats_url = f"{stats_base}/athletes/{athlete_id}/stats"
            try:
                resp = await get_with_retry(self._client, stats_url)
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

    async def fetch_recent_games(self, sport: str, player_name: str, n: int = 5) -> list[dict]:
        sport = sport.lower()
        if sport not in ESPN_SPORT_URLS:
            return []

        urls = ESPN_SPORT_URLS[sport]
        base = urls["base"]

        # Search athlete by name
        search_url = f"{base}/athletes"
        try:
            resp = await get_with_retry(self._client, search_url, params={"limit": 50, "search": player_name})
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
            resp = await get_with_retry(self._client, gamelog_url)
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
        """Season averages from a v3 payload's "averages" category."""
        result = _empty_stat_row()
        for category in data.get("categories") or []:
            if category.get("name") != "averages":
                continue
            labels = category.get("labels") or []
            values = _latest_row(category)
            for label, value in zip(labels, values):
                field = _BASKETBALL_LABELS.get(label)
                if field:
                    result[field] = _to_number(value)
        return result

    def _parse_football_stats(self, data: dict) -> dict:
        """Season totals, keyed by (category, label) to disambiguate YDS."""
        result = _empty_stat_row()
        for category in data.get("categories") or []:
            name = category.get("name")
            labels = category.get("labels") or []
            values = _latest_row(category)
            for label, value in zip(labels, values):
                field = _FOOTBALL_LABELS.get((name, label))
                if field:
                    result[field] = _to_number(value)
        return result

    async def is_available(self) -> bool:
        try:
            url = ESPN_SPORT_URLS["nba"]["scoreboard"]
            resp = await get_with_retry(self._client, url, timeout=5.0)
            return resp.status_code == 200
        except Exception:
            return False

    async def close(self):
        await self._client.aclose()

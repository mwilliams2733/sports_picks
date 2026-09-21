import httpx

from backend.collectors.espn_http import get_with_retry

SPORT_URLS = {
    "nba": "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard",
    "nfl": "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard",
    "ncaab": "https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/scoreboard",
    "ncaaf": "https://site.api.espn.com/apis/site/v2/sports/football/college-football/scoreboard",
    "mma": "https://site.api.espn.com/apis/site/v2/sports/mma/ufc/scoreboard",
    "mlb": "https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/scoreboard",
}
#: Extra scoreboard query params per sport. Without a ``groups`` filter ESPN
#: answers a college request with a featured subset rather than the division:
#: 2 ncaab events for 2026-03-15, a conference championship Sunday. That
#: truncation is what made `_reconcile_against_espn` cancel 15 games that had
#: actually been played.
#:
#: ``50`` is Division I basketball and ``80`` is FBS football. They are NOT
#: interchangeable -- asking college football for ``groups=50`` returns 6
#: events for a full September Saturday. Pro leagues have a single division
#: and need no filter.
#:
#: ``limit`` is deliberately absent: passing ``limit=900`` to college football
#: *reduced* the result from 71 events to 25.
SCOREBOARD_PARAMS = {
    "ncaab": {"groups": "50"},
    "ncaaf": {"groups": "80"},
}

# Boxing has no ESPN scoreboard API — games come from Odds API only

#: ESPN's numeric season phase. Read from the EVENT, not the league block,
#: which still reports 2 on a playoff date.
SEASON_TYPE_MAP = {
    1: "preseason",
    2: "regular",
    3: "postseason",
    4: "allstar",
}

#: Competition-type abbreviations that mean the game is an exhibition, not a
#: result. ESPN labels All-Star games ``season.type = 2`` -- regular season --
#: so the season block alone cannot find them. The competition block can:
#: a normal game is "STD", a conference final "SEMI", an All-Star game
#: "ALLSTAR". The 2026 NBA All-Star round robin produced totals of 72, 82
#: and 93 against a real nba average of 230.9.
EXHIBITION_COMPETITION_TYPES = {"ALLSTAR"}


def season_type_of(event: dict, competition: dict) -> str:
    """The season phase, preferring the competition type where it disagrees.

    ESPN's two blocks can contradict each other and the competition block is
    the more specific one, so it wins.
    """
    abbr = ((competition.get("type") or {}).get("abbreviation") or "").upper()
    if abbr in EXHIBITION_COMPETITION_TYPES:
        return "allstar"
    return SEASON_TYPE_MAP.get((event.get("season") or {}).get("type"), "unknown")

def week_of(event: dict) -> int | None:
    """ESPN's week number for one event, or None where the sport has none.

    Read from the EVENT rather than the scoreboard block: a scoreboard
    response is keyed by the requested date and can carry a game filed under
    a different week, so the event is the authoritative one for its own game.

    None is not zero. ESPN omits the block entirely for sports without weeks
    -- nba, mlb and ncaab return nothing at either level -- so the payload
    itself says which sports have one and no allowlist is needed here. A
    defaulted 0 would make "no such concept" indistinguishable from "week
    zero", which ncaaf really does play.
    """
    number = (event.get("week") or {}).get("number")
    if isinstance(number, bool) or not isinstance(number, (int, float)):
        return None
    return int(number)


#: The one spelling of this status. ESPN's map used the double-l British
#: form while full_pipeline's reconciliation wrote and read the single-l
#: one, so a game ESPN reported as called off would have been invisible to
#: the path that restores a rescheduled game -- and uncounted by
#: catch_up_finals. No row had ever carried either value, so nothing broke;
#: it was waiting to.
CANCELED = "canceled"

STATUS_MAP = {
    "STATUS_SCHEDULED": "scheduled",
    "STATUS_IN_PROGRESS": "in_progress",
    "STATUS_FINAL": "final",
    "STATUS_POSTPONED": "postponed",
    "STATUS_CANCELED": CANCELED,
}

class ESPNCollector:
    def __init__(self):
        self.client = httpx.AsyncClient(timeout=30.0)

    async def fetch_scoreboard(self, sport: str, date_str: str) -> list[dict]:
        url = SPORT_URLS.get(sport)
        if not url:
            return []
        params = {"dates": date_str, **SCOREBOARD_PARAMS.get(sport, {})}
        response = await get_with_retry(self.client, url, params=params)
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
                # ESPN marks tournament and showcase games here. Absent on
                # some feeds, so default to hosted rather than guessing.
                "neutral_site": bool(competition.get("neutralSite", False)),
                "season_type": season_type_of(event, competition),
                "week": week_of(event),
            })
        return games

    async def close(self):
        await self.client.aclose()

import logging
import re

import httpx

_APIKEY_RE = re.compile(r"(apiKey=)[^&\s'\"]+", re.IGNORECASE)


def redact_api_key(text: str) -> str:
    """Replace any apiKey query-parameter value with a placeholder.

    The Odds API only accepts its key as a query parameter, so the key ends up
    inside httpx exception strings (which embed the request URL). Any text
    derived from such an exception must pass through here before being logged
    or returned.
    """
    return _APIKEY_RE.sub(r"\1<redacted>", text)


class _RedactingFilter(logging.Filter):
    """Strip apiKey values from every log record that passes through.

    ``redact_api_key`` covers text we format ourselves. It does not cover
    httpx, which logs the full request URL at INFO on every call -- and the
    Odds API accepts its key only as a query parameter, so each odds fetch
    wrote the live key into scheduler.log in plaintext. Found in production
    on 2026-09-19.

    The record is collapsed to its formatted message first: httpx logs with
    %-style arguments, so the URL lives in ``record.args`` and never in
    ``record.msg``. Redacting ``msg`` alone would miss it entirely.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if record.args:
            record.msg = record.getMessage()
            record.args = ()
        if isinstance(record.msg, str) and "apikey=" in record.msg.lower():
            record.msg = redact_api_key(record.msg)
        return True


def install_log_redaction() -> None:
    """Attach the redacting filter to the root logger and its handlers.

    A logger's filters apply only to records logged through it directly, not
    to propagated ones, so the handlers are filtered too -- that is where
    every propagated record actually lands. Idempotent: a second call does
    not stack a second filter.
    """
    root = logging.getLogger()
    for target in (root, *root.handlers):
        if not any(isinstance(f, _RedactingFilter) for f in target.filters):
            target.addFilter(_RedactingFilter())


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
        # Installed here rather than at an entrypoint so that no caller can
        # forget it: the key only ever leaves this process through a client
        # built right here, so this is the one place guaranteed to run before
        # httpx can log a URL containing it.
        install_log_redaction()
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
        # Prices are captured alongside the lines. Reading only `point` for
        # spreads and totals is why `ensemble` had to hardcode -110 for every
        # one of those picks: the real price was in the payload and discarded
        # here, one layer below the strategy that needed it.
        result = {"key": bk["key"], "moneyline_home": None, "moneyline_away": None,
                  "spread_home": None, "spread_away": None, "over_under": None,
                  "spread_home_price": None, "spread_away_price": None,
                  "over_price": None, "under_price": None}
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
                        result["spread_home_price"] = o.get("price")
                    else:
                        result["spread_away"] = o["point"]
                        result["spread_away_price"] = o.get("price")
            elif market["key"] == "totals":
                for o in outcomes:
                    # The Under carries the same point as the Over but its
                    # own price, which is the half this used to drop.
                    if o["name"] == "Over":
                        result["over_under"] = o["point"]
                        result["over_price"] = o.get("price")
                    elif o["name"] == "Under":
                        result["under_price"] = o.get("price")
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

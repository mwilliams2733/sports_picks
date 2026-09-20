import logging
import os
import pathlib
from datetime import date
import yaml
from functools import lru_cache
from dotenv import load_dotenv

load_dotenv(override=False)

logger = logging.getLogger(__name__)

#: Where this machine keeps its shared secrets. Override with SHARED_ENV_PATH.
DEFAULT_SHARED_ENV = pathlib.Path.home() / ".secrets" / "shared.env"

#: Names the Odds API key may appear under in the shared secrets file. The
#: file predates this repo and uses ``TheODDSAPI``; the canonical name is
#: accepted too so a future tidy-up does not silently break the lookup.
_ODDS_KEY_NAMES = ("ODDS_API_KEY", "TheODDSAPI")


def _read_env_file(path: str | os.PathLike) -> dict[str, str]:
    """Parse a KEY=VALUE file. Never logs a value."""
    p = pathlib.Path(path)
    if not p.exists():
        return {}
    out: dict[str, str] = {}
    try:
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip().strip('"').strip("'")
    except OSError as e:
        # Report that it could not be read, never what it contained.
        logger.warning("Could not read secrets file %s: %s", p, e.__class__.__name__)
        return {}
    return out


def resolve_odds_api_key(shared_env_path: str | os.PathLike | None = None) -> str | None:
    """Find the Odds API key, environment first.

    Order matters and is the point of this function:

    1. ``ODDS_API_KEY`` in the environment. Docker and
       ``deploy/sports-picks-web.service`` inject it that way, so they are
       unaffected by anything below.
    2. ``ODDS_API_KEY`` or ``TheODDSAPI`` in the shared secrets file.

    The key is deliberately NOT duplicated into a repo-local ``.env``. That
    duplication is exactly how the rotated key went unnoticed: a ``.env``
    dated March 2026 kept feeding a dead key to every scheduled run, which
    returned 401 and produced no picks while the logs looked healthy.

    Returns None when no key is configured. Callers must treat that as
    "no odds today" rather than passing an empty key to the API.
    """
    from_env = os.environ.get("ODDS_API_KEY")
    if from_env:
        return from_env

    path = (shared_env_path
            or os.environ.get("SHARED_ENV_PATH")
            or DEFAULT_SHARED_ENV)
    values = _read_env_file(path)
    for name in _ODDS_KEY_NAMES:
        if values.get(name):
            logger.info("Odds API key loaded from %s as %s", path, name)
            return values[name]

    logger.warning(
        "No Odds API key found: not in the environment, and %s has none of %s. "
        "Odds fetches will fail and no picks can be generated.",
        path, ", ".join(_ODDS_KEY_NAMES),
    )
    return None


def load_config(path: str) -> dict:
    with open(path) as f:
        config = yaml.safe_load(f)
    api_key = resolve_odds_api_key()
    if api_key:
        config["odds_api_key"] = api_key
    return config

def season_label(sport: str, game_date: date, seasons: dict) -> str:
    """The canonical name of the season ``game_date`` falls in, for ``sport``.

    One function, because three sites used to build this string three
    different ways -- ``f"{y}-{str(y+1)[-2:]}"`` in the backtesting loader,
    ``f"{y}-{y+1}"`` on the ESPN path and ``f"{y}"`` on the odds path -- so
    the same season was recorded differently depending on which collector
    happened to create the row. nba carried four labels for two seasons.

    A season that crosses the new year is named for the year it STARTED:
    ``2026-27`` covers September 2026 through February 2027. Deriving that
    from the configured start date is what fixes the second form, which read
    a January 2027 game's own year and called it ``2027-2028``.

    A season contained in one calendar year -- mlb, and the year-round combat
    sports -- is just that year.

    The two-digit suffix matches the 1,237 nba rows already stored as
    ``2025-26``: the canonical form is the one that does not require
    rewriting the history.

    Falls back to the bare year for an unknown sport or malformed config,
    mirroring :func:`is_sport_in_season`, because a label that is merely
    coarse beats refusing to store the game at all.
    """
    season = seasons.get(sport)
    if not season:
        return str(game_date.year)
    try:
        start_month, start_day = map(int, season["start"].split("-"))
        end_month, end_day = map(int, season["end"].split("-"))
    except (KeyError, ValueError, TypeError, AttributeError) as e:
        logger.error("Malformed season config for %r (%r): %s", sport, season, e)
        return str(game_date.year)

    if (start_month, start_day) <= (end_month, end_day):
        return str(game_date.year)          # contained in one calendar year

    start_year = (game_date.year
                  if (game_date.month, game_date.day) >= (start_month, start_day)
                  else game_date.year - 1)
    return f"{start_year}-{str(start_year + 1)[-2:]}"


@lru_cache(maxsize=4)
def seasons_config(path: str = "config.yaml") -> dict:
    """The seasons block, cached so per-game labelling does not re-read YAML."""
    try:
        return load_config(path).get("seasons", {}) or {}
    except (OSError, yaml.YAMLError) as e:
        logger.error("Could not load seasons from %r: %s", path, e)
        return {}


def is_sport_in_season(sport: str, seasons: dict, today: date | None = None) -> bool:
    today = today or date.today()
    season = seasons.get(sport)
    if not season:
        return False
    try:
        start_month, start_day = map(int, season["start"].split("-"))
        end_month, end_day = map(int, season["end"].split("-"))
        start = date(today.year, start_month, start_day)
        end = date(today.year, end_month, end_day)
    except (KeyError, ValueError, TypeError, AttributeError) as e:
        logger.error("Malformed season config for %r (%r): %s", sport, season, e)
        return False
    if start <= end:
        return start <= today <= end
    else:
        return today >= start or today <= end

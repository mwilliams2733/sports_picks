import logging
import os
import pathlib
from datetime import date
import yaml
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

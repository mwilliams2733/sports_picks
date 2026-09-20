"""Combat results from The Odds API's `/scores` endpoint.

Why this exists
---------------
ESPN has no boxing. Not a missing scoreboard -- boxing is not a sport in
its API at all: `sports/boxing` returns 404 and
`sports/boxing/boxing/scoreboard` answers *"sport 'boxing' and/or league
'boxing' are invalid"*. So nothing could ever finalize a boxing bout, and
on 2026-09-20 that was 140 rows `scheduled`, zero scores, zero
`elo_history`, and every boxing pick priced on two identical 1500 seeds.

The odds feed is the only source we already have that covers them. It also
covers the regional mma promotions ESPN's UFC scoreboard does not: 64 of
126 mma bouts (51%) failed to match there.

Names match by construction
---------------------------
Our combat rows are *created* from these very events, so `home_team` and
`away_team` are the same strings we stored. On 2026-09-20, 30 of 30
upcoming boxing pairs matched character for character, apostrophes
included. That is why this returns `BoutResult` and reuses `match_bout`
and `winner_is` unchanged -- the odds path and the ESPN path grade a bout
the same way or not at all.

Limits, both measured 2026-09-20
--------------------------------
* ``daysFrom`` is hard-capped at 3. 7 and 30 both return HTTP 422
  ``INVALID_SCORES_DAYS_FROM``. **This can never serve a historical
  backfill**, only the last three days.
* Each call costs 2 credits.
* mma is proven: 17 of 17 started bouts came back `completed` with a 0/1
  score pair. **boxing is unproven** -- no boxing bout had started inside
  the window, so `completed` was never exercised. The vendor's own
  coverage table says boxing has no scores, but that table also omits mma,
  which demonstrably works, so it is not evidence either way. If boxing
  never populates, every event here is skipped and this is a no-op: the
  failure mode is doing nothing, not writing something wrong.
"""
from __future__ import annotations

import logging

from backend.collectors.odds_api import SPORT_KEYS, install_log_redaction
from backend.collectors.ufc import BoutResult, normalize_name

logger = logging.getLogger(__name__)

BASE_URL = "https://api.the-odds-api.com/v4/sports"

#: The API's hard ceiling, not a preference: 7 and 30 return HTTP 422
#: INVALID_SCORES_DAYS_FROM. Asking for more is a wasted call and 2 wasted
#: credits.
MAX_DAYS_FROM = 3


def scores_sport_key(sport: str) -> str | None:
    """The odds-feed key for a sport, from the one mapping that defines it.

    Delegates rather than keeping a second copy: a sport added to the odds
    collector must not also need adding here.
    """
    return SPORT_KEYS.get(sport)


def _winner_from_scores(home: str, away: str, scores) -> str | None:
    """The fighter with the higher score, or None if that is not decidable.

    Returns None -- meaning *skip this bout* -- for a draw, a missing or
    malformed score, and a score row naming someone who is not in the bout.
    Every one of those is a case where inventing a winner would grade a real
    pick backwards and feed a reversed Elo update, and both look entirely
    normal afterwards. Refusing is always recoverable; guessing is not.
    """
    if not isinstance(scores, list) or len(scores) != 2:
        return None

    by_name = {}
    for entry in scores:
        if not isinstance(entry, dict):
            return None
        name = entry.get("name") or ""
        try:
            value = float(entry.get("score"))
        except (TypeError, ValueError):
            return None
        by_name[normalize_name(name)] = (name, value)

    # The scores array is not ordered, so look both competitors up rather
    # than trusting position. A name that is not one of the two competitors
    # means this event is not the bout it appears to be.
    wanted = (normalize_name(home), normalize_name(away))
    if len(set(wanted)) != 2 or set(by_name) != set(wanted):
        return None

    (a_name, a_score), (b_name, b_score) = by_name[wanted[0]], by_name[wanted[1]]
    if a_score == b_score:          # a draw has no winner
        return None
    return a_name if a_score > b_score else b_name


def bouts_from_scores(events) -> list[BoutResult]:
    """Every decided bout in a `/scores` payload.

    Skips anything not `completed`, and anything whose winner is not
    decidable. See `_winner_from_scores`.
    """
    out: list[BoutResult] = []
    for event in events or []:
        if not event.get("completed"):
            continue
        home = event.get("home_team") or ""
        away = event.get("away_team") or ""
        if not home or not away:
            continue
        winner = _winner_from_scores(home, away, event.get("scores"))
        if winner is None:
            continue
        out.append(BoutResult(fighter_a=home, fighter_b=away, winner=winner))
    return out


async def fetch_scores(client, api_key: str, sport: str,
                       days_from: int = MAX_DAYS_FROM) -> list[BoutResult]:
    """Decided bouts for one sport over the last ``days_from`` days.

    ``days_from`` is clamped to `MAX_DAYS_FROM` rather than passed through:
    a larger value is not a smaller result, it is an HTTP 422 and two
    credits spent on nothing.
    """
    sport_key = scores_sport_key(sport)
    if not sport_key:
        return []
    # The key only ever leaves this process through a request built here, so
    # this is the one place guaranteed to run before httpx can log a URL
    # containing it. Same reasoning as OddsAPICollector.__init__.
    install_log_redaction()

    response = await client.get(
        f"{BASE_URL}/{sport_key}/scores/",
        params={"apiKey": api_key,
                "daysFrom": max(1, min(days_from, MAX_DAYS_FROM))},
    )
    response.raise_for_status()
    remaining = response.headers.get("x-requests-remaining")
    if remaining is not None:
        logger.info("Odds API scores for %s: %s credits remaining",
                    sport, remaining)
    return bouts_from_scores(response.json())

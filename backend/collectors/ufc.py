"""Bout-level results for mma, from ESPN's UFC scoreboard.

Why this exists
---------------
`fetch_ufc_events` was named by two comments -- in `pipeline/scheduler.py`
and `scripts/catch_up_finals.py` -- as the thing that writes combat finals,
and was never written. Both comments then justified excluding mma and boxing
from every other finalization path, on the grounds that this one owned them.

Nothing owned them. On 2026-09-20 the database held 293 combat games, all
`scheduled`, none with scores, with zero `elo_history` rows and zero fighter
ratings, and 85 picks that could never be graded. `grade_completed_games`
ran daily and found nothing to do, because nothing ever set a combat game
final. Every combat pick was therefore priced on the 1500 seed for both
fighters -- indistinguishable opponents, every time.

Shape of the feed
-----------------
The scoreboard returns **one event per date**: the card. Its `competitions`
are the individual bouts, each with its own status and a `winner` flag on
each competitor. So a date with 13 bouts is one event, not 13.

Matching
--------
Our rows come from the odds feed and carry fighter display names; ESPN
carries its own. Names are normalised (case, accents, punctuation,
whitespace) and compared as an unordered pair, because which fighter landed
in the home column is arbitrary on both sides.

A pair that does not match both fighters exactly is refused. A wrong winner
is the one failure that cannot be noticed downstream: it grades a real pick
backwards and feeds a reversed Elo update, and both look entirely normal.
Boxing has no ESPN scoreboard at all and is not served here.
"""
from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass

from backend.collectors.espn import SPORT_URLS
from backend.collectors.espn_http import get_with_retry

logger = logging.getLogger(__name__)

_PUNCT = re.compile(r"[^a-z0-9 ]+")
_SPACE = re.compile(r"\s+")


@dataclass(frozen=True)
class BoutResult:
    fighter_a: str
    fighter_b: str
    winner: str


def normalize_name(name: str) -> str:
    """Casefold, strip accents and punctuation, collapse whitespace.

    Hyphens become spaces rather than vanishing, so "Abdul-Kareem" and
    "Abdul Kareem" agree, and the odds feed's punctuation choices stop
    mattering.
    """
    decomposed = unicodedata.normalize("NFKD", name or "")
    ascii_only = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    lowered = ascii_only.lower().replace("-", " ").replace("'", "")
    return _SPACE.sub(" ", _PUNCT.sub(" ", lowered)).strip()


def _pair(a: str, b: str) -> frozenset[str]:
    return frozenset({normalize_name(a), normalize_name(b)})


def bouts_from_event(event: dict) -> list[BoutResult]:
    """Every decided bout on one card.

    Skips a bout that is not final, and one with no winner flag: a draw or
    no-contest has no result, and inventing one would grade a pick against
    something that never happened.
    """
    out: list[BoutResult] = []
    for competition in event.get("competitions") or []:
        status = ((competition.get("status") or {}).get("type") or {}).get("name")
        if status != "STATUS_FINAL":
            continue
        competitors = competition.get("competitors") or []
        if len(competitors) != 2:
            continue
        names = [((c.get("athlete") or {}).get("displayName") or "")
                 for c in competitors]
        if not all(names):
            continue
        winners = [c for c in competitors if c.get("winner")]
        if len(winners) != 1:
            continue
        winner = (winners[0].get("athlete") or {}).get("displayName") or ""
        if not winner:
            continue
        out.append(BoutResult(fighter_a=names[0], fighter_b=names[1],
                              winner=winner))
    return out


def match_bout(bouts: list[BoutResult], home: str, away: str) -> BoutResult | None:
    """The bout between these two fighters, or None.

    Both names must match. A single shared fighter is not the same bout, and
    guessing on a half match is how a pick gets graded backwards.
    """
    wanted = _pair(home, away)
    if len(wanted) != 2:          # same fighter twice: not a bout
        return None
    for bout in bouts:
        if _pair(bout.fighter_a, bout.fighter_b) == wanted:
            return bout
    return None


def winner_is(bout: BoutResult, name: str) -> bool:
    """Whether ``name`` is the fighter ESPN flagged as the winner."""
    return normalize_name(bout.winner) == normalize_name(name)


async def fetch_ufc_events(client, day) -> list[BoutResult]:
    """Every decided bout ESPN lists for one date."""
    response = await get_with_retry(
        client, SPORT_URLS["mma"], params={"dates": day.strftime("%Y%m%d")})
    response.raise_for_status()
    bouts: list[BoutResult] = []
    for event in response.json().get("events") or []:
        bouts.extend(bouts_from_event(event))
    return bouts

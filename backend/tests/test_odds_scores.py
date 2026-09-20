"""Combat results from The Odds API's /scores endpoint.

Why this source
---------------
ESPN has no boxing at all -- `sports/boxing` 404s and the API answers
*"Invalid sport (boxing)"* -- so 140 boxing rows had never been finalized,
no scores, no elo_history, every pick priced on two identical 1500 seeds.
The odds feed is the only source that covers them, and it covers the
regional mma promotions ESPN's UFC scoreboard misses too: 64 of our 126
mma bouts (51%) failed to match against ESPN.

Its decisive advantage is that **names match by construction**. Our rows
were created from these very events, so `home_team`/`away_team` are the
same strings we stored -- 30 of 30 upcoming boxing pairs matched character
for character on 2026-09-20, apostrophes included.

Its hard limit is `daysFrom <= 3`; 7 and 30 return HTTP 422. It can never
serve a historical backfill.

What these tests pin down is the parsing, because that is where a mistake
grades a real pick backwards and feeds a reversed Elo update -- and both
look entirely normal afterwards.
"""
import pytest

from backend.collectors.odds_scores import (
    MAX_DAYS_FROM, bouts_from_scores, scores_sport_key,
)
from backend.collectors.ufc import match_bout, winner_is


def _event(home, away, scores=None, completed=True):
    return {"id": "x", "sport_key": "boxing_boxing",
            "commence_time": "2026-09-19T16:30:00Z",
            "completed": completed, "home_team": home, "away_team": away,
            "scores": scores, "last_update": None}


def _score(name, value):
    return {"name": name, "score": str(value)}


def test_winner_is_the_higher_score():
    """The real shape, copied from a live mma response."""
    bouts = bouts_from_scores([_event(
        "Lukasz Charzewski", "Josef Stummer",
        [_score("Lukasz Charzewski", 0), _score("Josef Stummer", 1)])])

    assert len(bouts) == 1
    assert bouts[0].winner == "Josef Stummer"


def test_winner_is_found_regardless_of_score_order():
    """The scores array is not guaranteed to lead with the home fighter."""
    bouts = bouts_from_scores([_event(
        "Alpha Fighter", "Beta Fighter",
        [_score("Beta Fighter", 0), _score("Alpha Fighter", 1)])])

    assert bouts[0].winner == "Alpha Fighter"


def test_an_unfinished_bout_is_skipped():
    bouts = bouts_from_scores([_event(
        "Alpha Fighter", "Beta Fighter",
        [_score("Alpha Fighter", 1), _score("Beta Fighter", 0)],
        completed=False)])

    assert bouts == []


def test_a_completed_bout_with_no_scores_is_skipped():
    """`completed` true with `scores` null is the shape boxing may return."""
    assert bouts_from_scores([_event("A Fighter", "B Fighter", None)]) == []


def test_a_draw_has_no_winner_and_is_skipped():
    """Equal scores is a draw. Inventing a winner grades a pick against
    something that never happened -- the same rule the ESPN path follows."""
    bouts = bouts_from_scores([_event(
        "Alpha Fighter", "Beta Fighter",
        [_score("Alpha Fighter", 1), _score("Beta Fighter", 1)])])

    assert bouts == []


def test_a_non_numeric_score_is_skipped_not_guessed():
    bouts = bouts_from_scores([_event(
        "Alpha Fighter", "Beta Fighter",
        [_score("Alpha Fighter", "TKO"), _score("Beta Fighter", "")])])

    assert bouts == []


def test_scores_naming_a_fighter_not_in_the_bout_is_refused():
    """A score row that does not correspond to a competitor means the event
    is not what it appears to be; refuse rather than pick the other one."""
    bouts = bouts_from_scores([_event(
        "Alpha Fighter", "Beta Fighter",
        [_score("Alpha Fighter", 0), _score("Someone Else", 1)])])

    assert bouts == []


def test_result_feeds_the_existing_matcher_unchanged():
    """The whole point of returning BoutResult: match_bout and winner_is are
    reused, so the odds path and the ESPN path cannot disagree."""
    bouts = bouts_from_scores([_event(
        "O'Shaquie Foster", "Emanuel Navarrete",
        [_score("O'Shaquie Foster", 1), _score("Emanuel Navarrete", 0)])])

    bout = match_bout(bouts, "Emanuel Navarrete", "O'Shaquie Foster")

    assert bout is not None, "unordered pair match must still work"
    assert winner_is(bout, "O'Shaquie Foster") is True
    assert winner_is(bout, "Emanuel Navarrete") is False


def test_sport_key_is_derived_from_the_odds_collector():
    """One mapping, not two: a new sport added there must not need adding
    here as well."""
    from backend.collectors.odds_api import SPORT_KEYS

    assert scores_sport_key("boxing") == SPORT_KEYS["boxing"]
    assert scores_sport_key("mma") == SPORT_KEYS["mma"]
    assert scores_sport_key("nfl") == SPORT_KEYS["nfl"]


def test_days_from_cap_matches_the_documented_api_limit():
    """7 and 30 return HTTP 422 INVALID_SCORES_DAYS_FROM, measured
    2026-09-20. Asking for more is a wasted call."""
    assert MAX_DAYS_FROM == 3


# --- the request itself ---------------------------------------------------

class _FakeResponse:
    status_code = 200
    headers = {"x-requests-remaining": "7975"}

    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class _RecordingClient:
    """Captures the request instead of making it."""

    def __init__(self, payload=()):
        self.payload = list(payload)
        self.url = None
        self.params = None

    async def get(self, url, params=None):
        self.url, self.params = url, dict(params or {})
        return _FakeResponse(self.payload)


@pytest.mark.asyncio
async def test_days_from_is_clamped_to_the_api_ceiling():
    """A larger daysFrom is not a bigger result, it is HTTP 422 and two
    credits spent on nothing."""
    client = _RecordingClient()

    await __import__("backend.collectors.odds_scores", fromlist=["x"]) \
        .fetch_scores(client, "KEY", "boxing", days_from=30)

    assert client.params["daysFrom"] == MAX_DAYS_FROM


@pytest.mark.asyncio
async def test_request_targets_the_mapped_sport_key():
    from backend.collectors.odds_api import SPORT_KEYS
    client = _RecordingClient()

    await __import__("backend.collectors.odds_scores", fromlist=["x"]) \
        .fetch_scores(client, "KEY", "boxing")

    assert SPORT_KEYS["boxing"] in client.url
    assert client.url.endswith("/scores/")


@pytest.mark.asyncio
async def test_an_unmapped_sport_makes_no_request_at_all():
    """No key means no endpoint; spending a credit to learn that is waste."""
    client = _RecordingClient()

    result = await __import__("backend.collectors.odds_scores",
                              fromlist=["x"]).fetch_scores(client, "KEY", "curling")

    assert result == []
    assert client.url is None

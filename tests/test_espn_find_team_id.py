"""`EspnStatsSource._find_team_id` -- the method that was never written.

`fetch_season_averages` called `self._find_team_id(sport, team_abbr)` and the
method existed nowhere on the class, so every call raised AttributeError. The
collector caught it and logged a warning, which is why it went unnoticed:
284 warnings in a single scheduled run, and `stats_fetched: 0` every time.
"""

import pytest

from backend.collectors.player_stats.espn_stats_source import EspnStatsSource
from backend.team_identity import espn_id_for


def test_espn_id_for_resolves_a_known_abbreviation():
    # ESPN's own id for the Clippers, straight from the committed snapshot.
    assert espn_id_for("nba", "LAC") == "12"


def test_espn_id_for_covers_every_sport_with_a_snapshot():
    for sport, abbr in (("nba", "LAL"), ("ncaab", "DUKE"),
                        ("ncaaf", "UL"), ("nfl", "DAL")):
        assert espn_id_for(sport, abbr), f"{sport}/{abbr} did not resolve"


def test_espn_id_for_returns_none_for_an_unknown_abbreviation():
    assert espn_id_for("nba", "ZZZ") is None


def test_espn_id_for_returns_none_for_a_sport_without_a_snapshot():
    assert espn_id_for("boxing", "ANY") is None


@pytest.mark.asyncio
async def test_find_team_id_resolves_without_a_network_call():
    """The snapshot is the source of truth, so this must not hit ESPN.

    An httpx client that raises on use proves it: if the implementation
    reaches for the network, the test fails.
    """
    src = EspnStatsSource()

    class _Exploding:
        async def get(self, *a, **k):
            raise AssertionError("_find_team_id must not make a network call")

    src._client = _Exploding()
    assert await src._find_team_id("nba", "LAC") == "12"


@pytest.mark.asyncio
async def test_find_team_id_is_none_for_an_unknown_team():
    src = EspnStatsSource()
    assert await src._find_team_id("nba", "ZZZ") is None


@pytest.mark.asyncio
async def test_fetch_season_averages_no_longer_raises_attributeerror():
    """The actual production symptom.

    Before the fix this raised AttributeError inside the collector, which
    swallowed it into a warning. Here an unknown team must return [] cleanly.
    """
    src = EspnStatsSource()
    result = await src.fetch_season_averages("nba", "ZZZ")
    assert result == []


@pytest.mark.asyncio
async def test_fetch_season_averages_requests_the_right_roster_url():
    """A resolved id must reach the roster URL as ESPN's id, not our abbr."""
    src = EspnStatsSource()
    seen = {}

    class _Recording:
        async def get(self, url, **kwargs):
            seen["url"] = url
            raise RuntimeError("stop here -- the URL is what is under test")

    src._client = _Recording()
    await src.fetch_season_averages("nba", "LAC")

    assert seen["url"].endswith("/teams/12/roster"), seen.get("url")


@pytest.mark.asyncio
async def test_an_unknown_team_makes_no_request_at_all():
    """Refuse early rather than fetch /teams/None/roster.

    Returning [] is not enough to prove the guard works: without it the URL
    is built with None, 404s, and the exception handler returns [] anyway.
    The observable difference is whether a request happens.
    """
    src = EspnStatsSource()
    calls = []

    class _Counting:
        async def get(self, url, **kwargs):
            calls.append(url)
            raise RuntimeError("should not be reached")

    src._client = _Counting()
    assert await src.fetch_season_averages("nba", "ZZZ") == []
    assert calls == [], f"made {len(calls)} pointless request(s): {calls}"

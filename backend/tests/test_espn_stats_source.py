import pytest
from backend.collectors.player_stats.espn_stats_source import (
    ESPN_SPORT_URLS,
    EspnStatsSource,
)


@pytest.fixture
def source():
    return EspnStatsSource()


@pytest.mark.asyncio
async def test_espn_source_name(source):
    assert source.name == "espn"


@pytest.mark.asyncio
async def test_espn_unsupported_sport(source):
    result = await source.fetch_season_averages("cricket", "BOS")
    assert result == []


@pytest.mark.asyncio
async def test_espn_parse_basketball_stats(source):
    data = {"statistics": [{"splits": [{"categories": [
        {"stats": [
            {"abbreviation": "PTS", "value": 25.0},
            {"abbreviation": "REB", "value": 8.0},
            {"abbreviation": "AST", "value": 5.0},
            {"abbreviation": "MIN", "value": 35.0},
        ]}
    ]}]}]}
    result = source._parse_basketball_stats(data)
    assert result["points"] == 25.0
    assert result["rebounds"] == 8.0


@pytest.mark.asyncio
async def test_espn_parse_football_stats(source):
    data = {"statistics": [{"splits": [{"categories": [
        {"stats": [
            {"abbreviation": "PYDS", "value": 285.0},
            {"abbreviation": "TD", "value": 2.0},
            {"abbreviation": "RYDS", "value": 25.0},
        ]}
    ]}]}]}
    result = source._parse_football_stats(data)
    assert result["pass_yards"] == 285.0
    assert result["touchdowns"] == 2.0


# ---------------------------------------------------------------------------
# _find_team_id
#
# The method was called at fetch_season_averages but never defined, and no
# test reached past the unsupported-sport guard, so an AttributeError on every
# call was invisible. These start at fetch_season_averages for exactly that
# reason: a test that only calls _find_team_id directly would still pass if
# the call site were wired to some other name.
# ---------------------------------------------------------------------------

pytestmark = pytest.mark.httpx_mock(
    assert_all_responses_were_requested=False,
    can_send_already_matched_responses=True,
)

NFL_TEAMS_URL = ESPN_SPORT_URLS["nfl"]["teams"] + "?limit=500"

TEAMS_PAYLOAD = {
    "sports": [{"leagues": [{"teams": [
        {"team": {"id": "12", "abbreviation": "KC", "displayName": "Kansas City Chiefs"}},
        {"team": {"id": "17", "abbreviation": "NE", "displayName": "New England Patriots"}},
    ]}]}]
}


@pytest.mark.asyncio
async def test_find_team_id_resolves_offline_for_nba(source, httpx_mock):
    """nba has a committed snapshot, so no request may be made to resolve it."""
    assert await source._find_team_id("nba", "BOS") == "2"
    assert httpx_mock.get_requests() == []


@pytest.mark.asyncio
async def test_find_team_id_accepts_a_display_name(source):
    """Team.abbreviation has held display names -- plan 015's whole subject."""
    assert await source._find_team_id("nba", "Boston Celtics") == "2"
    assert await source._find_team_id("nba", "LA Clippers") == "12"


@pytest.mark.asyncio
async def test_find_team_id_falls_back_to_espn_without_a_snapshot(source, httpx_mock):
    """nfl ships no snapshot, so the teams endpoint answers -- once per sport."""
    httpx_mock.add_response(url=NFL_TEAMS_URL, json=TEAMS_PAYLOAD)

    assert await source._find_team_id("nfl", "KC") == "12"
    assert await source._find_team_id("nfl", "ne") == "17"

    teams_calls = [r for r in httpx_mock.get_requests()
                   if str(r.url).startswith(ESPN_SPORT_URLS["nfl"]["teams"])]
    assert len(teams_calls) == 1, "team table must be fetched once per sport, not per team"


@pytest.mark.asyncio
async def test_find_team_id_is_none_for_a_sport_with_no_teams_endpoint(source, httpx_mock):
    """A fighter is not a team. None is the answer, not an exception."""
    assert await source._find_team_id("mma", "Jon Jones") is None
    assert httpx_mock.get_requests() == []


@pytest.mark.asyncio
async def test_find_team_id_survives_a_failed_team_fetch(source, httpx_mock):
    """A transient outage returns None and is not cached as an empty table."""
    httpx_mock.add_response(url=NFL_TEAMS_URL, status_code=503)
    assert await source._find_team_id("nfl", "KC") is None
    assert source._team_ids == {}


@pytest.mark.asyncio
async def test_find_team_id_survives_a_reshaped_payload(source, httpx_mock):
    """An unexpected shape degrades to 'not found', not to an exception."""
    httpx_mock.add_response(url=NFL_TEAMS_URL, json={})
    assert await source._find_team_id("nfl", "KC") is None


@pytest.mark.asyncio
async def test_season_averages_reaches_the_roster(source, httpx_mock):
    """The regression guard: this raised AttributeError before _find_team_id
    existed, and the collector's blanket except logged it as a source failure."""
    base = ESPN_SPORT_URLS["nba"]["base"]
    httpx_mock.add_response(url=f"{base}/teams/2/roster", json={
        "athletes": [{"items": [{"id": "99", "fullName": "Jayson Tatum"}]}]
    })
    httpx_mock.add_response(url=f"{base}/athletes/99/statistics", json={
        "statistics": [{"splits": [{"categories": [
            {"stats": [{"abbreviation": "PTS", "value": 27.1}]}
        ]}]}]
    })

    result = await source.fetch_season_averages("nba", "BOS")

    assert len(result) == 1
    assert result[0]["player_name"] == "Jayson Tatum"
    assert result[0]["points"] == 27.1

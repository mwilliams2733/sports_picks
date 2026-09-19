"""Tests for the MLB Stats API collector. HTTP is mocked via httpx_mock."""
import pytest
from datetime import date
from backend.collectors.mlb_stats import MLBStatsCollector


@pytest.fixture
def schedule_payload():
    """Minimal MLB Stats API /schedule shape."""
    return {
        "dates": [{
            "games": [{
                "gamePk": 700001,
                "gameDate": "2026-04-29T23:05:00Z",
                "teams": {
                    "home": {
                        "team": {"id": 111, "name": "Boston Red Sox", "abbreviation": "BOS"},
                        "probablePitcher": {"id": 5001, "fullName": "A. Pitcher"},
                    },
                    "away": {
                        "team": {"id": 147, "name": "New York Yankees", "abbreviation": "NYY"},
                        "probablePitcher": {"id": 5002, "fullName": "B. Pitcher"},
                    },
                },
            }],
        }],
    }


@pytest.fixture
def pitcher_stats_payload():
    """MLB Stats API /people/{id}/stats?stats=gameLog — pitcher recent starts."""
    return {
        "stats": [{
            "splits": [
                {"stat": {"era": "2.50", "strikeOuts": 8, "inningsPitched": "6.0"}},
                {"stat": {"era": "3.00", "strikeOuts": 7, "inningsPitched": "5.2"}},
                {"stat": {"era": "1.50", "strikeOuts": 9, "inningsPitched": "7.0"}},
            ],
        }],
    }


@pytest.mark.asyncio
async def test_fetch_schedule_returns_games_with_probables(httpx_mock, schedule_payload):
    httpx_mock.add_response(
        url="https://statsapi.mlb.com/api/v1/schedule?sportId=1&date=2026-04-29&hydrate=probablePitcher",
        json=schedule_payload,
    )
    collector = MLBStatsCollector()
    games = await collector.fetch_schedule(date(2026, 4, 29))
    assert len(games) == 1
    g = games[0]
    assert g["mlb_game_pk"] == 700001
    assert g["home_team"] == "BOS"
    assert g["away_team"] == "NYY"
    assert g["home_probable_pitcher_id"] == 5001
    assert g["away_probable_pitcher_id"] == 5002
    await collector.close()


@pytest.mark.asyncio
async def test_fetch_schedule_handles_missing_probables(httpx_mock):
    """Early in the day, MLB hasn't announced pitchers yet — must not crash."""
    payload = {
        "dates": [{
            "games": [{
                "gamePk": 700002,
                "gameDate": "2026-04-29T23:05:00Z",
                "teams": {
                    "home": {"team": {"id": 111, "abbreviation": "BOS"}},
                    "away": {"team": {"id": 147, "abbreviation": "NYY"}},
                },
            }],
        }],
    }
    httpx_mock.add_response(
        url="https://statsapi.mlb.com/api/v1/schedule?sportId=1&date=2026-04-29&hydrate=probablePitcher",
        json=payload,
    )
    collector = MLBStatsCollector()
    games = await collector.fetch_schedule(date(2026, 4, 29))
    assert games[0]["home_probable_pitcher_id"] is None
    assert games[0]["away_probable_pitcher_id"] is None
    await collector.close()


@pytest.mark.asyncio
async def test_fetch_pitcher_recent_stats_aggregates_last_n(httpx_mock, pitcher_stats_payload):
    httpx_mock.add_response(
        url="https://statsapi.mlb.com/api/v1/people/5001/stats?stats=gameLog&group=pitching&season=2026",
        json=pitcher_stats_payload,
    )
    collector = MLBStatsCollector()
    stats = await collector.fetch_pitcher_recent(pitcher_id=5001, season=2026, last_n=5)
    # Three starts in payload: ERAs 2.50, 3.00, 1.50; mean = 2.333
    assert abs(stats["era_recent"] - 2.333) < 0.01
    # K total = 24, IP total = 18.667 -> K/9 = 24 * 9 / 18.667 ~= 11.57
    assert abs(stats["k9_recent"] - 11.57) < 0.05
    assert stats["starts_seen"] == 3
    await collector.close()


@pytest.mark.asyncio
async def test_fetch_pitcher_recent_skips_splits_with_missing_era(httpx_mock):
    """Splits missing the 'era' key (e.g., relief appearances) must not contribute
    a fake 0.00 ERA to the rolling mean."""
    payload = {"stats": [{"splits": [
        {"stat": {"era": "3.00", "strikeOuts": 6, "inningsPitched": "5.0"}},
        {"stat": {"strikeOuts": 1, "inningsPitched": "1.0"}},  # no era key
        {"stat": {"era": "4.00", "strikeOuts": 5, "inningsPitched": "5.0"}},
    ]}]}
    httpx_mock.add_response(
        url="https://statsapi.mlb.com/api/v1/people/5003/stats?stats=gameLog&group=pitching&season=2026",
        json=payload,
    )
    from backend.collectors.mlb_stats import MLBStatsCollector
    collector = MLBStatsCollector()
    stats = await collector.fetch_pitcher_recent(pitcher_id=5003, season=2026)
    # Mean of the two valid ERAs (3.00, 4.00) = 3.50, NOT (3.00 + 0 + 4.00) / 3 = 2.33
    assert abs(stats["era_recent"] - 3.50) < 0.01
    # K total still aggregates all three splits = 12, IP = 11.0 → 9.818
    assert abs(stats["k9_recent"] - 9.818) < 0.05
    assert stats["starts_seen"] == 3
    await collector.close()


@pytest.mark.asyncio
async def test_fetch_pitcher_recent_handles_no_starts(httpx_mock):
    httpx_mock.add_response(
        url="https://statsapi.mlb.com/api/v1/people/9999/stats?stats=gameLog&group=pitching&season=2026",
        json={"stats": [{"splits": []}]},
    )
    collector = MLBStatsCollector()
    stats = await collector.fetch_pitcher_recent(pitcher_id=9999, season=2026)
    assert stats is None
    await collector.close()


# --------------------------------------------------------------------------
# The schedule endpoint does not return team abbreviations.
#
# fetch_schedule read `team["abbreviation"]`, which the MLB Stats API only
# sends when the request hydrates `team`. With `hydrate=probablePitcher`
# alone the team object is {id, link, name}, so `.get("abbreviation")`
# returned None for every game, every day. The caller skipped each game for
# want of a key and returned an empty map, so every MLB pick was priced on a
# neutral starting pitcher -- the dominant MLB feature, silently absent.
#
# Found 2026-09-19: all 12 MLB games logged `available=[]`.
# --------------------------------------------------------------------------

import logging

import pytest

from backend.collectors.mlb_stats import MLBStatsCollector

MLB_SCHEDULE = "https://statsapi.mlb.com/api/v1/schedule"


def _schedule_payload(pairs):
    """The real shape: team carries `name`, and NO `abbreviation`."""
    return {"dates": [{"games": [
        {
            "gamePk": 1000 + i,
            "gameDate": "2026-09-19T20:05:00Z",
            "teams": {
                "home": {"team": {"id": 1, "link": "/x", "name": home},
                         "probablePitcher": {"id": 111}},
                "away": {"team": {"id": 2, "link": "/y", "name": away},
                         "probablePitcher": {"id": 222}},
            },
        }
        for i, (home, away) in enumerate(pairs)
    ]}]}


@pytest.mark.asyncio
async def test_teams_are_resolved_from_the_name_the_api_actually_sends(
    httpx_mock
):
    httpx_mock.add_response(
        url=f'{MLB_SCHEDULE}?sportId=1&date=2026-09-19&hydrate=probablePitcher',
        json=_schedule_payload([("Cincinnati Reds", "Chicago Cubs")]))
    c = MLBStatsCollector()
    try:
        games = await c.fetch_schedule(date(2026, 9, 19))
    finally:
        await c.close()
    assert games[0]["home_team"] == "CIN"
    assert games[0]["away_team"] == "CHC"


@pytest.mark.asyncio
async def test_mlbs_own_abbreviations_are_translated_to_espns(httpx_mock):
    """MLB says AZ and CWS where ESPN -- and our teams table -- say ARI/CHW.

    Resolving through team_identity rather than trusting the upstream
    abbreviation is what makes this work without a hand-kept mapping.
    """
    httpx_mock.add_response(
        url=f'{MLB_SCHEDULE}?sportId=1&date=2026-09-19&hydrate=probablePitcher',
        json=_schedule_payload([("Arizona Diamondbacks", "Chicago White Sox")]))
    c = MLBStatsCollector()
    try:
        games = await c.fetch_schedule(date(2026, 9, 19))
    finally:
        await c.close()
    assert games[0]["home_team"] == "ARI"
    assert games[0]["away_team"] == "CHW"


@pytest.mark.asyncio
async def test_an_unresolvable_team_is_reported_not_silently_dropped(
    httpx_mock, caplog
):
    """The original bug was silent. It must not be able to be silent again."""
    httpx_mock.add_response(
        url=f'{MLB_SCHEDULE}?sportId=1&date=2026-09-19&hydrate=probablePitcher',
        json=_schedule_payload([("Sioux Falls Canaries", "Chicago Cubs")]))
    c = MLBStatsCollector()
    try:
        with caplog.at_level(logging.WARNING):
            games = await c.fetch_schedule(date(2026, 9, 19))
    finally:
        await c.close()
    assert games[0]["home_team"] is None
    assert "Sioux Falls Canaries" in caplog.text


@pytest.mark.asyncio
async def test_probable_pitcher_ids_still_come_through(httpx_mock):
    httpx_mock.add_response(
        url=f'{MLB_SCHEDULE}?sportId=1&date=2026-09-19&hydrate=probablePitcher',
        json=_schedule_payload([("Cincinnati Reds", "Chicago Cubs")]))
    c = MLBStatsCollector()
    try:
        games = await c.fetch_schedule(date(2026, 9, 19))
    finally:
        await c.close()
    assert games[0]["home_probable_pitcher_id"] == 111
    assert games[0]["away_probable_pitcher_id"] == 222


@pytest.mark.asyncio
async def test_mlbs_abbreviation_is_ignored_even_when_it_is_present(httpx_mock):
    """Guards against a future `hydrate=team` quietly undoing the fix.

    If the request ever hydrates `team`, the payload gains an `abbreviation`
    key -- and MLB's is the wrong one for us: AZ where our teams table says
    ARI. Preferring it would reintroduce the mismatch while every test built
    on the un-hydrated shape kept passing.
    """
    payload = _schedule_payload([("Arizona Diamondbacks", "Chicago White Sox")])
    game = payload["dates"][0]["games"][0]
    game["teams"]["home"]["team"]["abbreviation"] = "AZ"
    game["teams"]["away"]["team"]["abbreviation"] = "CWS"

    httpx_mock.add_response(
        url=f'{MLB_SCHEDULE}?sportId=1&date=2026-09-19&hydrate=probablePitcher',
        json=payload)
    c = MLBStatsCollector()
    try:
        games = await c.fetch_schedule(date(2026, 9, 19))
    finally:
        await c.close()

    assert games[0]["home_team"] == "ARI", "took MLB's abbreviation, not ESPN's"
    assert games[0]["away_team"] == "CHW"

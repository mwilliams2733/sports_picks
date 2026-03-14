import pytest
from unittest.mock import patch
from backend.collectors.player_stats.nba_api_source import NbaApiSource


@pytest.mark.asyncio
async def test_nba_api_source_name():
    source = NbaApiSource()
    assert source.name == "nba_api"


@pytest.mark.asyncio
async def test_nba_api_non_nba():
    source = NbaApiSource()
    result = await source.fetch_season_averages("nfl", "KC")
    assert result == []
    result2 = await source.fetch_recent_games("nfl", "Someone")
    assert result2 == []


@pytest.mark.asyncio
async def test_nba_api_unknown_team():
    source = NbaApiSource()
    result = await source.fetch_season_averages("nba", "XXX")
    assert result == []

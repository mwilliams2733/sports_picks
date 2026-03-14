import pytest
from backend.collectors.player_stats.balldontlie_source import BallDontLieSource


@pytest.mark.asyncio
async def test_balldontlie_name():
    source = BallDontLieSource()
    assert source.name == "balldontlie"


@pytest.mark.asyncio
async def test_balldontlie_non_nba():
    source = BallDontLieSource()
    result = await source.fetch_season_averages("nfl", "KC")
    assert result == []
    result2 = await source.fetch_recent_games("nfl", "Patrick Mahomes")
    assert result2 == []

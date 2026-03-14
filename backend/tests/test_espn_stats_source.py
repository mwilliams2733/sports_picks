import pytest
from backend.collectors.player_stats.espn_stats_source import EspnStatsSource


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

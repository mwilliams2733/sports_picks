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


# The two parser tests that lived here built ESPN payloads by hand in the
# shape `statistics[].splits[].categories[].stats[]`, with abbreviation/value
# objects. The endpoint that returned that shape --
# site.api.../v2/.../athletes/{id}/statistics -- 404s for every athlete, so
# those tests passed for months while the production path collected nothing.
#
# Parser coverage now lives in tests/test_espn_v3_stats_parser.py, against
# trimmed copies of real v3 responses.

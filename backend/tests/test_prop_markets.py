"""The market map is one definition, and it covers what we actually fetch.

grader.MARKET_STAT_MAP and prop_analyzer.MARKET_TO_STAT were separate dicts,
retyped from each other. By the time they were merged they were five keys
apart. These guard the two properties that made that drift dangerous.
"""
import pytest

from backend.analysis.prop_markets import MARKET_STAT_MAP
from backend.collectors.odds_api import PROP_MARKETS
from backend.models import PlayerStat

#: Sports whose props PlayerStat cannot settle at all: it has no baseball
#: column, and prop_pipeline.build_default_collector has no mlb chain to fill
#: one. Their markets are fetched and stored, then neither analysed nor
#: graded. Listed here so the coverage test below states the gap instead of
#: quietly skipping it.
UNSUPPORTED_SPORTS = {"mlb"}


def test_the_analyser_and_the_grader_read_the_same_map():
    """Not "the same contents" -- the same object. Equal dicts is precisely
    what the old arrangement had, right up until it didn't."""
    from backend.analysis import prop_analyzer
    from backend.pipeline import grader

    assert grader.MARKET_STAT_MAP is MARKET_STAT_MAP
    assert prop_analyzer.MARKET_STAT_MAP is MARKET_STAT_MAP


@pytest.mark.parametrize("sport", sorted(set(PROP_MARKETS) - UNSUPPORTED_SPORTS))
def test_every_market_we_request_can_be_settled(sport):
    """A market the analyser knows but the grader does not yields picks that
    can never be graded. One map makes that impossible per market; this makes
    it impossible per *fetched* market."""
    for market in PROP_MARKETS[sport]:
        assert market in MARKET_STAT_MAP, (
            f"{sport} requests {market!r} from The Odds API, but nothing maps "
            f"it to a PlayerStat field, so its picks can never be graded"
        )


def test_every_mapped_field_exists_on_player_stat():
    """A field name typo settles a prop against nothing and reads as a miss."""
    columns = set(PlayerStat.__table__.columns.keys())
    for market, fields in MARKET_STAT_MAP.items():
        for field in fields:
            assert field in columns, f"{market} -> {field!r} is not a PlayerStat column"


def test_mlb_props_are_fetched_but_cannot_be_settled():
    """Records the open gap rather than leaving it to be rediscovered.

    Delete this test when PlayerStat grows the columns; the parametrised
    coverage test above will then cover mlb once it leaves UNSUPPORTED_SPORTS.
    """
    assert PROP_MARKETS["mlb"], "mlb still asks The Odds API for props"
    assert not [m for m in PROP_MARKETS["mlb"] if m in MARKET_STAT_MAP]

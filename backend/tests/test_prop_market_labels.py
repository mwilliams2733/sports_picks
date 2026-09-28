"""Prop markets read as words in the email, never as API keys.

The 2026-09-28 digest printed "Zach Ertz Over 10.5 player_reception_yds":
`_market_label` knew 7 of 21 market keys, and every key it did not know was
written into `pick_value` verbatim. Two of the four nfl markets the odds
feed actually requests were among them.
"""
from datetime import date

import pytest

from backend.analysis.prop_markets import MARKET_STAT_MAP, market_label
from backend.collectors.odds_api import PROP_MARKETS
from backend.digest.render import render_digest
from backend.digest.selector import DigestPick, DigestSection

#: Every market we request AND can settle. mlb's are requested but not in
#: MARKET_STAT_MAP (see prop_markets' docstring), so they never become picks.
REQUESTED = sorted({m for markets in PROP_MARKETS.values() for m in markets
                    if m in MARKET_STAT_MAP})


@pytest.mark.parametrize("market", REQUESTED)
def test_every_requested_market_has_a_readable_label(market):
    label = market_label(market)

    assert label != market
    assert "_" not in label


def test_the_pipeline_uses_the_shared_labels():
    """Derived, not duplicated: the digest and the stored pick_value must
    agree on what a market is called."""
    from backend.pipeline import prop_pipeline

    assert prop_pipeline._market_label is market_label


def _props_section(pick_value):
    return DigestSection(sport="nfl", picks=[], props=[
        DigestPick(sport="nfl", matchup="Eagles at Bears", pick_value=pick_value,
                   odds=-113, confidence=5, edge_pct=0.0, rationale="")])


def test_a_pick_stored_with_a_raw_key_is_relabelled_in_the_email():
    """Picks already stored carry the raw key -- tonight's 65 MNF props do.
    The email must not wait for them to age out."""
    _, html, text = render_digest(
        [_props_section("Zach Ertz Over 10.5 player_reception_yds")],
        date(2026, 9, 28))

    for body in (html, text):
        assert "player_reception_yds" not in body
        assert "Zach Ertz Over 10.5 Receiving Yards" in body


def test_an_already_labelled_pick_is_left_alone():
    _, _, text = render_digest(
        [_props_section("Jalen Hurts Over 225.5 Pass Yards")], date(2026, 9, 28))

    assert "Jalen Hurts Over 225.5 Pass Yards" in text


def test_an_unknown_trailing_word_is_left_alone():
    """Only a known market key is swapped. Anything else passes through."""
    _, _, text = render_digest(
        [_props_section("Someone Over 1.5 batter_hits")], date(2026, 9, 28))

    assert "Someone Over 1.5 batter_hits" in text

"""Guards for the prop-field backfill.

The dangerous failure here is not crashing -- it is resolving a market
*plausibly but wrongly*, which grades a prop against the wrong stat and records
a confident wrong result.
"""
import pytest

from backend.scripts.backfill_prop_fields import (
    build_label_to_market, parse_pick_value, run,
)


def test_reverse_map_is_derived_from_the_forward_map():
    """Retyping _market_label's dict is how the two drift. Derived, a market
    added to MARKET_STAT_MAP appears here automatically."""
    reverse = build_label_to_market()
    assert reverse["3-Pointers"] == "player_threes"
    assert reverse["Points"] == "player_points"
    # 9 of 16 markets have no display label and fall through as themselves.
    assert reverse["player_blocks"] == "player_blocks"


def test_every_market_round_trips():
    """No market may be unreachable through the reverse map."""
    from backend.pipeline.grader import MARKET_STAT_MAP
    reverse = build_label_to_market()
    assert set(reverse.values()) == set(MARKET_STAT_MAP)


def test_parses_a_player_whose_name_contains_spaces_and_a_decimal_line():
    player, market = parse_pick_value("Dean Wade Over 0.5 3-Pointers",
                                      build_label_to_market())
    assert player == "Dean Wade"
    assert market == "player_threes"


def test_parses_a_whole_number_line_and_an_unlabelled_market():
    player, market = parse_pick_value("Alex Caruso Under 2 player_blocks",
                                      build_label_to_market())
    assert player == "Alex Caruso"
    assert market == "player_blocks"


def test_an_unknown_trailing_label_resolves_to_nothing_rather_than_a_guess():
    """The whole point of the script's caution. "Steals" is not a label any
    market produces (player_steals falls through as its own key), so this must
    not quietly become player_steals."""
    assert parse_pick_value("Alex Caruso Over 1.5 Steals",
                            build_label_to_market()) is None


def test_an_unparseable_pick_value_resolves_to_nothing():
    assert parse_pick_value("nonsense", build_label_to_market()) is None
    assert parse_pick_value("", build_label_to_market()) is None


def test_run_refuses_a_db_path_that_does_not_exist(tmp_path):
    """A typo'd path must not create an empty database and report a
    successful zero-row backfill against it."""
    with pytest.raises(FileNotFoundError):
        run(str(tmp_path / "nope.db"))

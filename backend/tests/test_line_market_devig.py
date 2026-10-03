"""Spread and total edges are measured against the de-vigged market price.

Moneyline edge has always been ``model - fair``, where ``fair`` is the
quoted price with the vig removed proportionally (`remove_vig`). Spreads
and totals used a flat 0.5 instead. That is the same number only when both
sides are priced alike: at -105 / -115 the home side's fair probability is
0.4884, not 0.5, so a flat 0.5 understated the home edge by 1.2 points and
overstated the away edge by the same.

Both markets now go through the moneyline's own `remove_vig`, and a market
with only one side quoted makes no pick, because it cannot be de-vigged.
"""
import pytest

from backend.analysis.odds_utils import american_to_implied_prob, remove_vig
from backend.tests.test_tracked_markets import _game, _picks


def _fair(price, other):
    return remove_vig(american_to_implied_prob(price), american_to_implied_prob(other))[0]


#: (this side's price, the other side's) as the fixture quotes them.
QUOTES = {"HOME": (-105, -115), "AWAY": (-115, -105),
          "Over": (-102, -118), "Under": (-118, -102)}


@pytest.mark.parametrize("market", ["spread", "over_under"])
def test_the_edge_is_against_the_devigged_price(market):
    (pick,) = _picks(_game("nfl"), market)
    fair = _fair(*QUOTES[pick.pick_value.split()[0]])

    assert fair != pytest.approx(0.5, abs=0.005), "the fixture must price the sides apart"
    assert pick.implied_probability == pytest.approx(fair, abs=1e-4)
    assert pick.edge_pct == pytest.approx((pick.model_probability - fair) * 100, abs=0.06)


def test_equal_prices_devig_to_one_half():
    """At -110 / -110 nothing changes: the fair price is exactly 0.5."""
    game = _game("nfl")
    snap = game.odds[0]
    snap.spread_home_price = snap.spread_away_price = -110

    (pick,) = _picks(game, "spread")

    assert pick.implied_probability == pytest.approx(0.5)


@pytest.mark.parametrize("market,missing", [
    ("spread", "spread_away_price"), ("over_under", "under_price"),
])
def test_a_market_with_one_side_unquoted_makes_no_pick(market, missing):
    """The quoted side cannot be de-vigged without the other."""
    game = _game("nfl")
    setattr(game.odds[0], missing, None)

    assert _picks(game, market) == []


def test_the_export_derives_the_fair_price_for_line_picks_like_moneyline():
    """One definition for every game pick: model_prob - edge_pct / 100.

    A pick from before this change was measured against 0.5, and the same
    arithmetic recovers 0.5 for it, within the storage rounding.
    """
    from backend.scripts.export_picks import _market_prob_novig

    assert _market_prob_novig("spread", 0.58, 9.16) == pytest.approx(0.4884)
    assert _market_prob_novig("over_under", 0.5512, 5.1) == pytest.approx(0.5002)
    assert _market_prob_novig("spread", None, 5.0) is None


def test_the_away_side_needs_both_quotes_too():
    """Same rule from the other side: here the model favours the away team,
    and the HOME price is the one missing."""
    game = _game("nfl")
    game.home_stats, game.away_stats = game.away_stats, game.home_stats
    assert [p.pick_value.split()[0] for p in _picks(game, "spread")] == ["AWAY"], (
        "with both sides quoted this fixture must pick the away side")

    game.odds[0].spread_home_price = None

    assert _picks(game, "spread") == []

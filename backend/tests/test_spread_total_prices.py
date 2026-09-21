"""Spread and total picks were priced at a hardcoded -110.

`ensemble` passed the literal `-110` to both `odds_at_pick` and
`fractional_kelly` for every spread and total, at four call sites. That
price is what grading pays out against, what CLV is measured from, and what
Kelly sizes against — so a market priced at -120 or +100 was recorded, paid
and sized as if it were -110.

It was not an arbitrary guess. The price was never available to pass,
because `OddsAPICollector._parse_bookmaker` read `o["point"]` for spreads
and totals and **discarded `o["price"]`**, which the API returns alongside
it (see the fixture in `test_odds_api.py`). The literal was standing in for
data thrown away one layer down.

So the fix runs the whole chain: capture the price, store it, average it,
and use it. `STANDARD_JUICE` remains as a named fallback for the rows that
predate the columns — every Odds row currently in the database — rather
than a magic number scattered through the strategy.

What this does NOT change
-------------------------
Edge for these markets is measured against a de-vigged `0.5`, not against
the price. That is deliberate (`spread_fair`, `ou_fair`) and separate. It
does mean a spread edge overstates real expected value by roughly the vig,
which is worth its own decision.
"""
import pytest

from backend.analysis.odds_utils import STANDARD_JUICE
from backend.collectors.odds_api import OddsAPICollector


def _bookmaker(spread_price=-110, total_price=-110, *, with_price=True):
    def outcome(name, price, point):
        o = {"name": name, "point": point}
        if with_price:
            o["price"] = price
        return o

    return {
        "key": "draftkings",
        "markets": [
            {"key": "h2h", "outcomes": [
                {"name": "Boston Celtics", "price": -150},
                {"name": "Los Angeles Lakers", "price": 130}]},
            {"key": "spreads", "outcomes": [
                outcome("Boston Celtics", spread_price, -4.5),
                outcome("Los Angeles Lakers", -105, 4.5)]},
            {"key": "totals", "outcomes": [
                outcome("Over", total_price, 218.5),
                outcome("Under", -108, 218.5)]},
        ],
    }


def _parse(**kw):
    collector = OddsAPICollector("key")
    return collector._parse_bookmaker(_bookmaker(**kw), "Boston Celtics")


# --- the collector must stop throwing the price away ----------------------

def test_spread_prices_are_captured_per_side():
    result = _parse(spread_price=-120)

    assert result["spread_home_price"] == -120
    assert result["spread_away_price"] == -105


def test_total_prices_are_captured_for_both_over_and_under():
    """The parser previously read only the Over, and only its point."""
    result = _parse(total_price=-115)

    assert result["over_price"] == -115
    assert result["under_price"] == -108


def test_the_line_is_still_captured():
    result = _parse()

    assert result["spread_home"] == -4.5
    assert result["spread_away"] == 4.5
    assert result["over_under"] == 218.5


def test_a_payload_without_prices_yields_none_not_a_guess():
    """A book quoting no price is unknown, not -110. The fallback belongs
    at the point of use, where it can be named."""
    result = _parse(with_price=False)

    assert result["spread_home_price"] is None
    assert result["over_price"] is None


# --- consensus across books ----------------------------------------------

def _avg_odds(snapshots):
    """Run `_average_odds` over the given book quotes.

    Reuses `test_ensemble._stats` for the TeamStats fixture rather than
    building a second one that would drift from it.
    """
    import datetime

    from backend.analysis.strategy import Strategy
    from backend.data_types import GameData
    from backend.tests.test_ensemble import _stats

    game = GameData(game_id=1, sport="nba", date=datetime.date(2026, 5, 1),
                    home_team_id=1, away_team_id=2,
                    home_stats=_stats(), away_stats=_stats(),
                    odds=snapshots)

    class _S(Strategy):
        def predict(self, game):
            return []

    return _S("t", {})._average_odds(game)


def _snap(bk, **prices):
    from backend.data_types import OddsSnapshot

    return OddsSnapshot(bookmaker=bk, moneyline_home=-150, moneyline_away=130,
                        spread_home=-4.5, spread_away=4.5, over_under=218.5,
                        **prices)


def test_prices_are_averaged_in_probability_space_like_moneylines():
    """American odds are non-linear around +/-100, so an arithmetic mean of
    -110 and +110 is 0, which is not a price at all."""
    avg = _avg_odds([_snap("a", spread_home_price=-110),
                     _snap("b", spread_home_price=110)])

    assert avg["spread_home_price"] is not None
    assert -120 < avg["spread_home_price"] < 120
    assert avg["spread_home_price"] != 0


def test_a_missing_price_across_every_book_stays_none():
    """None, not STANDARD_JUICE: the consensus reports what was quoted, and
    the fallback is applied where it can be named."""
    avg = _avg_odds([_snap("a")])

    assert avg["spread_home_price"] is None
    assert avg["over_price"] is None


# --- the fallback is named, not scattered ---------------------------------

def test_the_standard_juice_constant_is_the_one_fallback():
    assert STANDARD_JUICE == -110


def test_no_bare_minus_110_literal_remains_in_the_ensemble():
    """The point of the constant: a literal that reappears is a second
    source of truth that can drift from the first."""
    import pathlib

    src = pathlib.Path("backend/analysis/variants/ensemble.py").read_text(
        encoding="utf-8")
    code = [ln for ln in src.splitlines()
            if not ln.lstrip().startswith("#") and "-110" in ln]

    assert code == [], f"bare -110 still in ensemble code: {code}"


# --- the strategy must actually USE the price ------------------------------
#
# The two assertions below are the ones the whole change exists for. Without
# them, replacing `avg_odds.get("spread_home_price") or STANDARD_JUICE` with
# a bare `STANDARD_JUICE` passes every other test in this file: the collector
# captures the price, the consensus averages it, and the strategy quietly
# ignores it.

import datetime  # noqa: E402

import backend.analysis.variants.ensemble as ens  # noqa: E402
from backend.analysis.variants.ensemble import EnsembleStrategy  # noqa: E402
from backend.data_types import GameData  # noqa: E402


@pytest.fixture
def _markets_enabled(monkeypatch):
    """nba is not a validated spread/total sport in production. These tests
    are about which PRICE a pick carries, not about the gate."""
    monkeypatch.setattr(ens, "SPREAD_VALIDATED_SPORTS", frozenset({"nba"}))
    monkeypatch.setattr(ens, "TOTALS_VALIDATED_SPORTS", frozenset({"nba"}))


def _game(home_diff, away_diff, total_line, **prices):
    from backend.data_types import OddsSnapshot
    from backend.tests.test_ensemble import _stats

    return GameData(
        game_id=1, sport="nba", date=datetime.date(2026, 3, 13),
        home_team_id=1, away_team_id=2,
        home_stats=_stats(point_diff=home_diff, elo_rating=1600,
                          offensive_rating=110.0, defensive_rating=105.0),
        away_stats=_stats(point_diff=away_diff, elo_rating=1400,
                          offensive_rating=105.0, defensive_rating=110.0),
        odds=[OddsSnapshot(bookmaker="dk", moneyline_home=-200,
                           moneyline_away=170, spread_home=-3.5,
                           spread_away=3.5, over_under=total_line, **prices)])


PRICES = dict(spread_home_price=-125, spread_away_price=105,
              over_price=-135, under_price=115)


def _by_value(picks, prefix):
    return [p for p in picks if p.pick_value.startswith(prefix)]


def _predict(game, min_edge=1.0):
    return EnsembleStrategy(
        "ensemble", {"min_edge": min_edge, "max_edge": 100.0}).predict(game)


def test_the_home_spread_carries_its_own_price(_markets_enabled):
    """-125 is what this bet pays, so it is what grading, CLV and Kelly use.

    Asserted per SIDE and on the exact value. A looser assertion (`in the
    set of prices I passed`) passes when the strategy substitutes the other
    side's price, or fires only the side that happens to match.
    """
    picks = _predict(_game(10.0, -5.0, 215.0, **PRICES))
    home = _by_value(picks, "HOME ")

    assert [p.odds_at_pick for p in home if p.pick_type == "spread"] == [-125]


def test_the_away_spread_carries_ITS_own_price(_markets_enabled):
    """Mirrored fixture, because the home-favoured game never fires an away
    spread and so cannot pin the away price at all."""
    picks = _predict(_game(-5.0, 10.0, 215.0, **PRICES))
    away = _by_value(picks, "AWAY ")

    assert [p.odds_at_pick for p in away if p.pick_type == "spread"] == [105]


def test_the_under_carries_its_own_price(_markets_enabled):
    """Two default teams predict a 210-point total, so a 215 line is an
    Under."""
    picks = _predict(_game(10.0, -5.0, 215.0, **PRICES))
    under = _by_value(picks, "Under")

    assert [p.odds_at_pick for p in under] == [115]


def test_the_over_carries_ITS_own_price(_markets_enabled):
    """A 195 line against the same 210-point projection is an Over."""
    picks = _predict(_game(10.0, -5.0, 195.0, **PRICES))
    over = _by_value(picks, "Over")

    assert [p.odds_at_pick for p in over] == [-135]


def test_an_unpriced_market_falls_back_to_the_standard_juice(_markets_enabled):
    """Every Odds row predating the price columns looks like this."""
    picks = _predict(_game(10.0, -5.0, 215.0))
    spreads = [p for p in picks if p.pick_type == "spread"]

    assert spreads
    assert all(p.odds_at_pick == STANDARD_JUICE for p in spreads)

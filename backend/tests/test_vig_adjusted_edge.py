"""Edges are measured against the break-even price, which includes the vig.

Until 2026-10-03 a game pick's edge was ``model - fair``, where ``fair`` is
the market with the vig removed. That measures disagreement with a fair
price, not value: a bet still pays the vig, so it only profits if the
model beats the BREAK-EVEN probability, ``american_to_implied_prob`` of the
price actually offered. On 2026-09-17..10-03's 212 published nfl/mlb/ncaaf
moneylines, the gap averaged 1.73 points, with a maximum of 3.86.

The owner kept min_edge (3) and the tier thresholds, so a pick now needs
3 points over break-even. That is stricter, and about 22% of those
moneylines would not have been made.

The de-vigged fair probability is still kept, as
``PickModel.market_prob_novig``, because the export can no longer back it
out of ``edge_pct``.
"""
import pytest

import backend.analysis.variants.ensemble as ens
from backend.analysis.odds_utils import american_to_implied_prob
from backend.analysis.variants.ensemble import EnsembleStrategy
from backend.tests.test_tracked_markets import _game


def _all_picks(game, min_edge=0.0):
    return EnsembleStrategy("ensemble", {"min_edge": min_edge, "max_edge": 100.0,
                                         "max_odds": None}).predict(game)


def _by_market(game, market, min_edge=0.0):
    return [p for p in _all_picks(game, min_edge) if p.pick_type == market]


def _ml_game():
    game = _game("nfl")
    game.odds[0].moneyline_home = -120
    game.odds[0].moneyline_away = 100
    return game


def _away(game):
    """The same game with the teams' strength swapped, so the model takes
    the away side."""
    game.home_stats, game.away_stats = game.away_stats, game.home_stats
    return game


#: Every side of every market, so each of the six edge lines is exercised.
CASES = [
    ("moneyline", "HOME", _ml_game()), ("moneyline", "AWAY", _away(_ml_game())),
    ("spread", "HOME", _game("nfl")), ("spread", "AWAY", _away(_game("nfl"))),
    ("over_under", "Over", _game("nfl")), ("over_under", "Under", _game("nfl", total=80.5)),
]


@pytest.mark.parametrize("market,side,game", CASES)
def test_the_edge_is_against_the_break_even_price(market, side, game):
    picks = _by_market(game, market)
    assert [p.pick_value.split()[0] for p in picks] == [side], "fixture must pick this side"
    (pick,) = picks

    break_even = american_to_implied_prob(pick.odds_at_pick)

    assert pick.edge_pct == pytest.approx((pick.model_probability - break_even) * 100, abs=0.06)
    assert pick.implied_probability < break_even, (
        "implied_probability stays the de-vigged fair price, below break-even")


@pytest.mark.parametrize("market,side,game", CASES)
def test_an_edge_only_the_vig_supplied_is_refused(market, side, game, monkeypatch):
    """Set min_edge between the vig-adjusted edge and the de-vigged one.
    The old definition would make this pick; the new one must not.
    The every-game rule (NFL from 2026-10-10: a game that clears no bar
    still gets a Low pick) is off here -- this tests the bar itself;
    test_every_game_picks.py owns that rule."""
    monkeypatch.setattr("backend.analysis.variants.ensemble.EVERY_GAME_SPORTS", ())
    (pick,) = _by_market(game, market)
    devig_edge = (pick.model_probability - pick.implied_probability) * 100
    assert devig_edge - pick.edge_pct > 0.5, "the fixture's vig must be visible"

    between = (pick.edge_pct + devig_edge) / 2

    assert _by_market(game, market, min_edge=between) == []


# -- storage and export ------------------------------------------------------

def test_the_fair_price_is_stored_and_refreshed(monkeypatch):
    from backend.models import PickModel
    from backend.pipeline.pick_generator import generate_and_store_picks
    from backend.tests.test_tracked_markets import D, SPREAD, _stub_predict
    import backend.tests.test_tracked_markets as tm
    from backend.database import get_engine, get_session
    from backend.models import Base, Game, StrategyModel, Team
    import tempfile, os

    path = os.path.join(tempfile.mkdtemp(), "f.db")
    s = get_session(get_engine(path))
    Base.metadata.create_all(s.get_bind())
    s.add_all([Team(id=1, name="H", abbreviation="H", sport="nfl"),
               Team(id=2, name="A", abbreviation="A", sport="nfl")])
    s.flush()
    s.add(Game(id=1, sport="nfl", season="2026", date=D, status="scheduled",
               home_team_id=1, away_team_id=2))
    s.add(StrategyModel(id=1, name="ensemble", strategy_type="game", is_active=True,
                        config_json='{"min_edge": 0.0, "max_edge": 100.0}'))
    s.commit()

    from backend.data_types import Pick
    def stub(fair):
        monkeypatch.setattr(EnsembleStrategy, "predict", lambda self, g: [
            Pick(game_id=g.game_id, implied_probability=fair,
                 **{k: v for k, v in SPREAD.items()})])

    stub(0.4884)
    generate_and_store_picks(s, 1, D)
    assert s.query(PickModel).one().market_prob_novig == pytest.approx(0.4884)

    stub(0.4950)
    generate_and_store_picks(s, 1, D)
    assert s.query(PickModel).one().market_prob_novig == pytest.approx(0.4950)


def test_the_export_prefers_the_stored_fair_price():
    from backend.scripts.export_picks import _market_prob_novig

    # New rows: edge is against break-even, so model - edge would give
    # break-even, not the fair price. The stored value wins.
    assert _market_prob_novig("moneyline", 0.60, 5.5, stored=0.5328) == pytest.approx(0.5328)
    # Legacy rows have no stored value; their edge WAS against the fair
    # price, so the old derivation still holds for them.
    assert _market_prob_novig("moneyline", 0.60, 5.5, stored=None) == pytest.approx(0.545)
    assert _market_prob_novig("prop", 0.60, 5.5, stored=None) is None

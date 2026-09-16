import pytest
from backend.analysis.strategy import Strategy
from backend.analysis.odds_utils import american_to_implied_prob
from backend.data_types import GameData, TeamStats, OddsSnapshot, Pick
from datetime import date


def _make_stats(**overrides):
    defaults = dict(point_diff=0.0, home_record=(0, 0), away_record=(0, 0),
        last_n_record=(0, 0), offensive_rating=100.0, defensive_rating=100.0,
        pace=100.0, strength_of_schedule=0.5, elo_rating=1500.0, rest_days=1)
    defaults.update(overrides)
    return TeamStats(**defaults)


def _make_game(odds, **overrides):
    defaults = dict(game_id=1, sport="nba", date=date(2026, 3, 13),
        home_team_id=1, away_team_id=2, home_stats=_make_stats(), away_stats=_make_stats(),
        odds=odds, week=None)
    defaults.update(overrides)
    return GameData(**defaults)


def _odds(bookmaker, ml_home, ml_away, spread_home=None, spread_away=None, over_under=None):
    return OddsSnapshot(bookmaker=bookmaker, moneyline_home=ml_home, moneyline_away=ml_away,
        spread_home=spread_home, spread_away=spread_away, over_under=over_under)


class _ConcreteStrategy(Strategy):
    def predict(self, game):
        return []


def test_average_odds_near_pickem_does_not_crash():
    """[-108, +104] arithmetic average is -2 and used to raise InvalidOddsError."""
    strat = _ConcreteStrategy("test", {})
    game = _make_game(odds=[_odds("book1", -108, 100), _odds("book2", 104, -108)])
    result = strat._average_odds(game)
    assert result is not None
    price = result["moneyline_home"]
    assert price <= -100 or price >= 100
    implied = american_to_implied_prob(price)
    assert implied == pytest.approx(0.5047, abs=1e-3)


def test_average_odds_identical_books_round_trip_unchanged():
    strat = _ConcreteStrategy("test", {})
    game = _make_game(odds=[_odds("book1", -110, -110), _odds("book2", -110, -110)])
    result = strat._average_odds(game)
    assert result["moneyline_home"] == -110
    assert result["moneyline_away"] == -110


def test_average_odds_no_odds_returns_none():
    strat = _ConcreteStrategy("test", {})
    game = _make_game(odds=[])
    assert strat._average_odds(game) is None


def test_average_odds_skips_invalid_book_price():
    """A book with an invalid stored price (e.g. 0) is skipped, not fatal."""
    strat = _ConcreteStrategy("test", {})
    game = _make_game(odds=[_odds("bad_book", 0, 0), _odds("book2", -110, -110)])
    result = strat._average_odds(game)
    assert result is not None
    assert result["moneyline_home"] == -110
    assert result["moneyline_away"] == -110


def test_average_odds_missing_away_side_returns_none():
    """Adopts combat_sports' stricter guard: either side empty -> None."""
    strat = _ConcreteStrategy("test", {})
    game = _make_game(odds=[OddsSnapshot(bookmaker="book1", moneyline_home=-110, moneyline_away=None,
        spread_home=None, spread_away=None, over_under=None)])
    assert strat._average_odds(game) is None


def test_average_odds_spread_and_total_stay_arithmetic():
    strat = _ConcreteStrategy("test", {})
    game = _make_game(odds=[
        _odds("book1", -110, -110, spread_home=-3.0, spread_away=3.0, over_under=220.0),
        _odds("book2", -110, -110, spread_home=-4.0, spread_away=4.0, over_under=221.0),
    ])
    result = strat._average_odds(game)
    assert result["spread_home"] == -3.5
    assert result["spread_away"] == 3.5
    assert result["over_under"] == 220.5


def test_average_odds_never_returns_invalid_band():
    """p == 0.5 exactly must clamp to the -100/+100 boundary, never (-100, 100)."""
    strat = _ConcreteStrategy("test", {})
    # Two books whose average implied probability is exactly 0.5
    game = _make_game(odds=[_odds("book1", 100, 100), _odds("book2", 100, 100)])
    result = strat._average_odds(game)
    assert abs(result["moneyline_home"]) >= 100
    assert abs(result["moneyline_away"]) >= 100

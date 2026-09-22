"""The optional price ceiling on moneyline picks.

**Off by default, by decision on 2026-09-21.** Underdog moneylines are
generated. These tests pin two things: that the default really is off, and
that the filter really works when a strategy asks for it -- a switch that
silently does nothing is worse than no switch.

What turning it on would change, kept here so the decision stays legible.
On 528 decided nfl backtest picks:

    fav  <= -150     n=106   market 66.2%  actual 64.2%    -7.83u
    fav  -150..-100  n= 88   market 52.3%  actual 48.9%    -9.74u
    dog  +100..+200  n=334   market 39.7%  actual 33.8%   -61.02u

    ceiling off      593 picks   43.8%   -157.67u   -11.39% ROI
    ceiling at 100   227 picks   58.6%    -30.30u    -5.78% ROI

The underdog band is 78% of the loss, and it is the worst band for a
structural reason: edge is `model - implied` in ABSOLUTE probability
points. Reading a 12% shot as 33% is a 21-point edge; reading a 66%
favourite as 70% is 4. The same miscalibration makes longshots look far
more attractive, so a model that bets where it disagrees most with the
market is selecting its own largest errors.

Neither setting makes the strategy profitable. Favourites alone lost
17.57u over 194 nfl picks.

`100` and `200` both appear below because they mean different things:
American odds jump from -100 to +100, so a ceiling of 100 refuses EVERY
underdog while 200 refuses longshots only.
"""
import datetime

import pytest

from backend.analysis.variants import ensemble as ens
from backend.analysis.variants.ensemble import (DEFAULT_MAX_ODDS,
                                                EnsembleStrategy)
from backend.data_types import GameData, OddsSnapshot
from backend.tests.test_ensemble import _stats


def _game(home_ml, away_ml):
    """A game the model likes the HOME side of, priced as given.

    The model reads this matchup at 87.2% home whatever the price says, so
    quoting the home side as a longshot reproduces the live failure exactly:
    the model backing a team the market has as a big underdog.
    """
    return GameData(
        game_id=1, sport="nba", date=datetime.date(2026, 3, 13),
        home_team_id=1, away_team_id=2,
        home_stats=_stats(point_diff=10.0, elo_rating=1650,
                          offensive_rating=118.0, defensive_rating=104.0),
        away_stats=_stats(point_diff=-8.0, elo_rating=1350,
                          offensive_rating=104.0, defensive_rating=118.0),
        odds=[OddsSnapshot(bookmaker="dk", moneyline_home=home_ml,
                           moneyline_away=away_ml, spread_home=-9.5,
                           spread_away=9.5, over_under=215.0)])


def _away_favoured_game(home_ml, away_ml):
    """Mirror of `_game`: the model likes the AWAY side.

    Needed because every other fixture produces a home pick, so the away
    branch of the gate is never reached and could skip its price check
    entirely without a single test noticing.
    """
    return GameData(
        game_id=1, sport="nba", date=datetime.date(2026, 3, 13),
        home_team_id=1, away_team_id=2,
        home_stats=_stats(point_diff=-8.0, elo_rating=1350,
                          offensive_rating=104.0, defensive_rating=118.0),
        away_stats=_stats(point_diff=10.0, elo_rating=1650,
                          offensive_rating=118.0, defensive_rating=104.0),
        odds=[OddsSnapshot(bookmaker="dk", moneyline_home=home_ml,
                           moneyline_away=away_ml, spread_home=9.5,
                           spread_away=-9.5, over_under=215.0)])


def _moneylines(game, **config):
    cfg = {"min_edge": 1.0, "max_edge": 100.0}
    cfg.update(config)
    return [p for p in EnsembleStrategy("ensemble", cfg).predict(game)
            if p.pick_type == "moneyline"]


# --- the default is off ---------------------------------------------------

def test_there_is_no_ceiling_by_default():
    assert DEFAULT_MAX_ODDS is None


def test_an_underdog_moneyline_is_generated_by_default():
    """The decision this file exists to record: these picks are left in."""
    assert [p.odds_at_pick for p in _moneylines(_game(300, -380))] == [300]


def test_even_a_long_price_is_generated_by_default():
    assert [p.odds_at_pick for p in _moneylines(_game(650, -900))] == [650]


def test_an_away_underdog_is_generated_by_default():
    picks = _moneylines(_away_favoured_game(-380, 300))

    assert [p.odds_at_pick for p in picks] == [300]
    assert all(p.pick_value.startswith("AWAY") for p in picks)


# --- and the switch still works -------------------------------------------

def test_a_configured_ceiling_refuses_a_price_beyond_it():
    assert _moneylines(_game(300, -380), max_odds=200) == []


def test_a_configured_ceiling_admits_a_price_inside_it():
    assert [p.odds_at_pick for p in
            _moneylines(_game(180, -220), max_odds=200)] == [180]


def test_a_ceiling_of_100_refuses_every_underdog():
    """100 is not "a lower longshot cap" -- American odds jump from -100 to
    +100, so nothing sits between. It means favourites only."""
    assert _moneylines(_game(180, -220), max_odds=100) == []
    assert _moneylines(_game(105, -125), max_odds=100) == []


def test_a_favourite_survives_even_the_tightest_ceiling():
    assert [p.odds_at_pick for p in
            _moneylines(_game(-170, 145), max_odds=100)] == [-170]


def test_the_first_reachable_underdog_price_is_105_not_100():
    """A book quoting exactly +100 never reaches the guard as +100.
    `consensus_moneyline` averages in PROBABILITY space and converts back,
    and even money is 50% either way, so +100 round-trips to -100 -- the
    favourite-side spelling -- and is admitted. The ceiling's `>=` and `>`
    are indistinguishable at the boundary because the boundary is not
    reachable."""
    assert [p.odds_at_pick for p in
            _moneylines(_game(100, -120), max_odds=100)] == [-100]
    assert _moneylines(_game(105, -125), max_odds=100) == []


def test_the_away_side_is_gated_on_its_own_price_too():
    assert _moneylines(_away_favoured_game(-380, 300), max_odds=200) == []


def test_a_refusal_is_logged_rather_than_silent(caplog):
    """A pick that vanishes with no record is indistinguishable from a
    strategy that found nothing -- the failure mode this repo keeps
    producing."""
    with caplog.at_level("INFO", logger=ens.__name__):
        _moneylines(_game(300, -380), max_odds=200)

    assert any("max_odds" in r.getMessage() for r in caplog.records), \
        [r.getMessage() for r in caplog.records]


def test_spread_and_total_picks_are_unaffected_by_the_ceiling(monkeypatch):
    """Those markets price near -110 and have their own record. The
    diagnosis was specific to longshot moneylines."""
    monkeypatch.setattr(ens, "SPREAD_VALIDATED_SPORTS", frozenset({"nba"}))
    monkeypatch.setattr(ens, "TOTALS_VALIDATED_SPORTS", frozenset({"nba"}))

    picks = EnsembleStrategy(
        "ensemble", {"min_edge": 1.0, "max_edge": 100.0, "max_odds": 200}
    ).predict(_game(300, -380))

    assert [p.pick_type for p in picks if p.pick_type == "moneyline"] == []
    assert [p for p in picks if p.pick_type in ("spread", "over_under")], \
        "the fixture should still produce spread/total picks"

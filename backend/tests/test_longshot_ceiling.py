"""Refuse moneyline picks on big underdogs.

The model overestimates underdogs badly, and it is the single largest loss
source in the book. Measured on the live picks carrying a stored
`model_prob`, at prices of +200 or longer:

    model said 33.7%   market implied 12.3%   actually won 12.5%   (n=24)

The market was calibrated to within 0.2 points. The model claimed nearly
three times the true probability, and that fabricated edge is what made
these picks qualify at all: edge is `model - implied`, so the bigger the
overestimate the more attractive the bet looks.

What it cost, on the live book:

    moneyline >= +200   n=83   won 10.8%   -23.62u

against a whole-book loss of -10.10u. Removing only these picks turns the
book positive. They are also why the moneyline market runs at 31.5% while
every other market sits near or above 50%.

Moved to +100 on a real sample
------------------------------
+200 came from 24 live picks. Importing nflverse history took the nfl
backtest from 7 decided picks to 528, and the +100..+200 band -- entirely
inside the old ceiling -- was 334 of them and -61.02u, 78% of the loss.
The model reads a 39.7% dog as a bet and it wins 33.8%.

The full 528-pick backtest is worse than a no-edge null at p = 0.0104, so
the model's selections carry NEGATIVE information. The right cut is not
the most profitable band but the one where model and market disagree
least, which is favourites. This removes the segment the model is most
confidently wrong about. It does not create an edge: favourites alone
still lost 17.57u over 194 picks.

This guards a MODEL property, not a market one, so it defaults on for
every strategy rather than living only in one config. Override with
`config["max_odds"]`; `None` disables it.
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


def _moneylines(game, **config):
    cfg = {"min_edge": 1.0, "max_edge": 100.0}
    cfg.update(config)
    return [p for p in EnsembleStrategy("ensemble", cfg).predict(game)
            if p.pick_type == "moneyline"]


def test_the_default_ceiling_is_the_diagnosed_boundary():
    """100, not 200. American odds jump from -100 to +100, so this refuses
    EVERY underdog rather than trimming longshots -- see the constant's
    docstring for the 528-pick evidence that moved it."""
    assert DEFAULT_MAX_ODDS == 100


def test_the_first_reachable_underdog_price_is_refused():
    """+105, not +100.

    A book quoting exactly +100 never reaches the guard as +100.
    `consensus_moneyline` averages in PROBABILITY space and converts back,
    and even money is 50% either way, so +100 round-trips to -100 -- the
    favourite-side spelling -- and is admitted. The ceiling's `>=` and `>`
    are therefore indistinguishable at the boundary: it is not reachable.
    +105 is the first price that is.
    """
    assert _moneylines(_game(100, -120)), "even money reads as -100, a favourite"
    assert [p.odds_at_pick for p in _moneylines(_game(100, -120))] == [-100]
    assert _moneylines(_game(105, -125)) == []


def test_a_pick_beyond_the_ceiling_is_refused():
    """+200 implies 33.3%. The model claimed 33.7% at this band and the
    truth was 12.5%."""
    assert _moneylines(_game(200, -240)) == []


def test_a_longer_price_is_also_refused():
    assert _moneylines(_game(300, -380)) == []


def test_a_modest_underdog_is_now_refused_too():
    """+180 used to qualify. On 528 backtested picks the +100..+200 band
    was 334 picks and -61.02u -- 78% of the loss, entirely inside the old
    +200 ceiling."""
    assert _moneylines(_game(180, -220)) == []


def test_a_favourite_is_what_remains_inside_the_ceiling():
    """The bands where market and actual track closest: -150 or shorter
    ran 66.2% market against 64.2% actual."""
    picks = _moneylines(_game(-170, 145))

    assert [p.odds_at_pick for p in picks] == [-170]


def test_a_favourite_is_never_touched_by_the_ceiling():
    picks = _moneylines(_game(-150, 130))

    assert [p.odds_at_pick for p in picks] == [-150]


def test_the_ceiling_can_be_loosened_per_strategy():
    picks = _moneylines(_game(300, -380), max_odds=1000)

    assert [p.odds_at_pick for p in picks] == [300]


def test_the_ceiling_can_be_disabled_entirely():
    """A backtest sweeping thresholds needs the unfiltered baseline."""
    picks = _moneylines(_game(300, -380), max_odds=None)

    assert [p.odds_at_pick for p in picks] == [300]


def test_a_refusal_is_logged_rather_than_silent(caplog):
    """A pick that vanishes with no record is indistinguishable from a
    strategy that found nothing -- the failure mode this repo keeps
    producing."""
    with caplog.at_level("INFO", logger=ens.__name__):
        _moneylines(_game(300, -380))

    assert any("max_odds" in r.getMessage() for r in caplog.records),         [r.getMessage() for r in caplog.records]


def test_spread_and_total_picks_are_unaffected(monkeypatch):
    """Those markets price near -110 and have their own record. The
    diagnosis was specific to longshot moneylines."""
    monkeypatch.setattr(ens, "SPREAD_VALIDATED_SPORTS", frozenset({"nba"}))
    monkeypatch.setattr(ens, "TOTALS_VALIDATED_SPORTS", frozenset({"nba"}))

    picks = EnsembleStrategy(
        "ensemble", {"min_edge": 1.0, "max_edge": 100.0}).predict(_game(300, -380))

    assert [p.pick_type for p in picks if p.pick_type == "moneyline"] == []
    assert [p for p in picks if p.pick_type in ("spread", "over_under")],         "the fixture should still produce spread/total picks"


def _away_favoured_game(home_ml, away_ml):
    """Mirror of `_game`: the model likes the AWAY side.

    Needed because every fixture above produces a home pick, so the away
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


def test_the_away_side_is_gated_on_its_own_price_too():
    assert _moneylines(_away_favoured_game(-380, 300)) == []


def test_an_away_favourite_still_qualifies():
    picks = _moneylines(_away_favoured_game(145, -170))

    assert [p.odds_at_pick for p in picks] == [-170]
    assert all(p.pick_value.startswith("AWAY") for p in picks)


def test_an_away_underdog_is_refused():
    assert _moneylines(_away_favoured_game(-220, 180)) == []

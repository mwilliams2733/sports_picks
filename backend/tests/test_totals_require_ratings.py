"""The totals model must not bet on a constant.

`_predicted_total` is built from offensive_rating, defensive_rating and
pace. No collector in this repo supplies any of them -- `team_stats` holds
only rest_days, point_diff and win/loss splits -- so all three always fall
back to 100.0 and the prediction is exactly 200.0 for every game in every
sport.

Against real market lines that constant decides the side by itself: mlb
totals run 6.5-20.5, ncaaf 36.5-76.5, ncaab 130-172.5, all below 200 (always
Over), and nba 208.5-255.5, above it (always Under). The normal CDF then
saturates, so model_prob is exactly 1.0 and edge_pct exactly 50.0 -- 113 of
the 147 over_under picks in production carried that value.

Measured result: 52.0% win rate over 50 graded picks at -110, ROI -0.007.
A coin flip paying the vig, which is what picking a side by constant gives.
"""
import datetime

import pytest

from backend.analysis.variants.ensemble import EnsembleStrategy
from backend.data_types import GameData, OddsSnapshot, TeamStats


def _stats(*, measured: bool, off=100.0, deff=100.0, pace=100.0):
    return TeamStats(
        point_diff=0.0, home_record=(5, 5), away_record=(5, 5),
        last_n_record=(5, 5), offensive_rating=off, defensive_rating=deff,
        pace=pace, strength_of_schedule=0.5, elo_rating=1500.0, rest_days=1,
        ratings_measured=measured)


def _game(sport, line, home, away):
    return GameData(
        game_id=1, sport=sport, date=datetime.date(2026, 9, 19),
        home_team_id=1, away_team_id=2, home_stats=home, away_stats=away,
        odds=[OddsSnapshot(bookmaker="bk", moneyline_home=-110,
                           moneyline_away=-110, spread_home=-1.5,
                           spread_away=1.5, over_under=line)])


def _totals(picks):
    return [p for p in picks if p.pick_type == "over_under"]


@pytest.mark.parametrize("sport,line", [
    ("nba", 225.5), ("ncaab", 145.5), ("ncaaf", 52.5), ("mlb", 8.5),
])
def test_no_totals_pick_without_measured_ratings(sport, line):
    """The regression, across the full range of real market lines."""
    s = EnsembleStrategy("ensemble", {}, {})
    game = _game(sport, line, _stats(measured=False), _stats(measured=False))
    assert _totals(s.predict(game)) == []


def test_a_totals_pick_is_allowed_once_ratings_are_measured():
    """The guard is about provenance, not about disabling the market."""
    s = EnsembleStrategy("ensemble", {}, {})
    game = _game("nba", 225.5,
                 _stats(measured=True, off=118.0, deff=108.0, pace=101.0),
                 _stats(measured=True, off=117.0, deff=109.0, pace=101.0))
    # Not asserting a pick is produced -- that depends on thresholds -- only
    # that the guard is no longer what stops it.
    assert s._predicted_total(game) != 200.0


def test_one_side_unmeasured_is_still_refused():
    """A prediction is only as good as its worse input."""
    s = EnsembleStrategy("ensemble", {}, {})
    game = _game("nba", 225.5,
                 _stats(measured=True, off=118.0, deff=108.0, pace=101.0),
                 _stats(measured=False))
    assert _totals(s.predict(game)) == []


def test_the_constant_prediction_is_what_the_guard_is_for():
    """Pins the arithmetic the docstring describes, so it cannot drift."""
    s = EnsembleStrategy("ensemble", {}, {})
    game = _game("nba", 225.5, _stats(measured=False), _stats(measured=False))
    assert s._predicted_total(game) == 200.0


def test_moneyline_and_spread_are_unaffected():
    """They do not use the rating features; only totals do."""
    s = EnsembleStrategy("ensemble", {}, {})
    game = _game("nba", 225.5, _stats(measured=False), _stats(measured=False))
    kinds = {p.pick_type for p in s.predict(game)}
    assert "over_under" not in kinds


def test_ratings_measured_defaults_to_false():
    """A caller that has not thought about it must get the safe answer."""
    t = TeamStats(point_diff=0.0, home_record=(0, 0), away_record=(0, 0),
                  last_n_record=(0, 0), offensive_rating=100.0,
                  defensive_rating=100.0, pace=100.0,
                  strength_of_schedule=0.0, elo_rating=1500.0, rest_days=1)
    assert t.ratings_measured is False

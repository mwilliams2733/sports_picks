"""Owner, 2026-10-10: "publish picks on every NFL game". A game whose
moneyline clears no bar still gets the side the model prefers at the price,
at the lowest confidence tier, with its real (possibly negative) edge."""
from datetime import date

import pytest

from backend.analysis.variants.ensemble import EnsembleStrategy
from backend.data_types import GameData, OddsSnapshot, TeamStats


def _stats(point_diff=0.0, elo=1500.0):
    return TeamStats(point_diff=point_diff, home_record=(0, 0), away_record=(0, 0),
                     last_n_record=(0, 0), offensive_rating=100.0, defensive_rating=100.0,
                     pace=100.0, strength_of_schedule=0.5, elo_rating=elo, rest_days=1)


def _game(sport, strong_home=False):
    odds = [OddsSnapshot(bookmaker="b", moneyline_home=-110, moneyline_away=-110,
                         spread_home=None, spread_away=None, over_under=None)]
    return GameData(game_id=1, sport=sport, date=date(2026, 10, 11), home_team_id=1, away_team_id=2,
                    home_stats=_stats(10.0, 1700.0) if strong_home else _stats(),
                    away_stats=_stats(), odds=odds)


def _ml(picks):
    return [p for p in picks if p.pick_type == "moneyline"]


@pytest.mark.parametrize("home_prob, side", [(0.51, "HOME ML"), (0.48, "AWAY ML")])
def test_an_nfl_game_below_the_bar_still_gets_the_model_s_side(monkeypatch, home_prob, side):
    s = EnsembleStrategy("ensemble", {"min_edge": 3.0})
    monkeypatch.setattr(s, "_calibrated_probability", lambda g: home_prob)
    [pick] = _ml(s.predict(_game("nfl")))
    assert pick.pick_value == side
    assert pick.confidence == 1
    assert pick.edge_pct < 0          # 51% at -110 is under break-even
    assert pick.model_probability == round(max(home_prob, 1 - home_prob), 4)


def test_other_sports_keep_the_bar(monkeypatch):
    s = EnsembleStrategy("ensemble", {"min_edge": 3.0})
    monkeypatch.setattr(s, "_calibrated_probability", lambda g: 0.51)
    assert _ml(s.predict(_game("ncaaf"))) == []


def test_an_nfl_pick_that_clears_the_bar_is_unchanged(monkeypatch):
    s = EnsembleStrategy("ensemble", {"min_edge": 3.0})
    monkeypatch.setattr(s, "_calibrated_probability", lambda g: 0.60)
    [pick] = _ml(s.predict(_game("nfl")))
    assert pick.pick_value == "HOME ML" and pick.edge_pct > 3.0


def test_a_pick_the_ceiling_refused_is_published_as_low_not_high(monkeypatch):
    # 2026-10-11 snapshot: a +149 dog at 21.1 points -- refused by the
    # 20-point ceiling as a likely model error -- came back at tier 5.
    s = EnsembleStrategy("ensemble", {"min_edge": 3.0})
    monkeypatch.setattr(s, "_calibrated_probability", lambda g: 0.80)
    [pick] = _ml(s.predict(_game("nfl", strong_home=True)))   # both signals agree
    assert pick.edge_pct >= 20 and pick.confidence == 1

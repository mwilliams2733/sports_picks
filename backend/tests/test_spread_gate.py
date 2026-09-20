"""Guards for the per-sport spread gate.

Shrinking the rolling margin (7e61aa8) made the spread predictor far less
wrong -- NFL week 2 went from predicting +39 and -45 point margins to -7.5
and +6.5 -- and in doing so turned a sport producing zero spread picks into
one producing nine. Less wrong is not the same as right:
`backend.analysis.margin_report` measures the predictor against the market
line and it loses in every sport with data,

    nba    11.832 vs  8.591      ncaab  14.525 vs  9.564
    ncaaf  26.043 vs  8.176      mlb     3.308 vs  2.507

so disagreeing with that line is evidence of being wrong, not of an edge.

This mirrors TOTALS_VALIDATED_SPORTS exactly, and for the same reason. The
set is empty until margin_report says a sport beats the line on a sample
worth the name. The dangerous outcome is the set quietly filling up, or the
gate being bypassed for a sport that never earned it.
"""
import datetime

import pytest

from backend.analysis.variants.ensemble import (
    SPREAD_VALIDATED_SPORTS, EnsembleStrategy,
)
from backend.data_types import GameData, OddsSnapshot, TeamStats


def _stats(point_diff):
    return TeamStats(
        point_diff=point_diff, home_record=(0, 0), away_record=(0, 0),
        last_n_record=(5, 5), offensive_rating=100.0, defensive_rating=100.0,
        pace=100.0, strength_of_schedule=0.5, elo_rating=1500.0, rest_days=2,
        points_for=105.0, points_against=105.0)


def _game(sport="nba"):
    return GameData(
        game_id=1, sport=sport, date=datetime.date(2026, 3, 13),
        home_team_id=1, away_team_id=2,
        home_stats=_stats(10.0), away_stats=_stats(-5.0),
        odds=[OddsSnapshot(bookmaker="dk", moneyline_home=-200,
                           moneyline_away=170, spread_home=-3.5,
                           spread_away=3.5, over_under=215.0)])


def _spreads(sport="nba", **config):
    cfg = {"min_edge": 5.0, "max_edge": 100.0}
    cfg.update(config)
    picks = EnsembleStrategy("ensemble", cfg).predict(_game(sport))
    return [p for p in picks if p.pick_type == "spread"]


def test_no_sport_is_validated_yet():
    """Empty until margin_report says otherwise. If this ever fails, the
    numbers justifying the addition belong in the comment beside the set."""
    assert SPREAD_VALIDATED_SPORTS == frozenset()


def test_an_unvalidated_sport_produces_no_spread_pick():
    assert _spreads("nba") == []
    assert _spreads("nfl") == []


def test_a_validated_sport_produces_one(monkeypatch):
    """The gate is the only thing stopping it: with the sport allowed, the
    same game yields the pick it always did."""
    import backend.analysis.variants.ensemble as mod
    monkeypatch.setattr(mod, "SPREAD_VALIDATED_SPORTS", frozenset({"nba"}))

    picks = _spreads("nba")

    assert len(picks) == 1
    assert picks[0].pick_type == "spread"
    assert picks[0].odds_at_pick == -110


def test_the_gate_is_per_sport(monkeypatch):
    import backend.analysis.variants.ensemble as mod
    monkeypatch.setattr(mod, "SPREAD_VALIDATED_SPORTS", frozenset({"nba"}))

    assert len(_spreads("nba")) == 1
    assert _spreads("nfl") == []


def test_the_gate_does_not_suppress_other_markets():
    """Only spreads are gated. Moneylines must be unaffected.

    min_edge is dropped to 1.0 because this fixture's moneyline edge is
    about 4% -- enough to prove the branch still runs, not enough to clear
    the production floor.
    """
    picks = EnsembleStrategy("ensemble", {"min_edge": 1.0, "max_edge": 100.0}
                             ).predict(_game("nba"))
    assert any(p.pick_type == "moneyline" for p in picks)
    assert not any(p.pick_type == "spread" for p in picks)

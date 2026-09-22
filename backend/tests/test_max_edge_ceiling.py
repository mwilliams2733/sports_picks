"""A claimed edge can be too large to believe.

Measured on the deduplicated book, both markets break at the same place:

    moneyline   edge <20%  +0.129 ROI (n=32)   edge >=20%  -0.548 (n=22)
    spread      edge <20%  +0.162 ROI (n=23)   edge >=20%  -0.364 (n=24)

Spread is the cleaner evidence because every spread is priced at -110, so
the comparison is like-for-like: 33.3% wins against a 52.4% breakeven,
paired t = -2.25.

The model has no upper bound, so it takes the *largest* claimed edge -- the
exact selection the inversion punishes. `max_edge` refuses those.
"""
import datetime

import pytest

from backend.analysis.variants.ensemble import DEFAULT_MAX_EDGE, EnsembleStrategy
from backend.data_types import GameData, OddsSnapshot, TeamStats


def _stats(**kw):
    base = dict(point_diff=0.0, home_record=(5, 5), away_record=(5, 5),
                last_n_record=(5, 5), offensive_rating=100.0,
                defensive_rating=100.0, pace=100.0, strength_of_schedule=0.5,
                elo_rating=1500.0, rest_days=2, points_for=105.0,
                points_against=105.0)
    base.update(kw)
    return TeamStats(**base)


def _game(home_ml=-110, away_ml=-110, home_elo=1500.0, away_elo=1500.0,
          spread=-1.0):
    return GameData(
        game_id=1, sport="nba", date=datetime.date(2026, 9, 20),
        home_team_id=1, away_team_id=2,
        home_stats=_stats(elo_rating=home_elo),
        away_stats=_stats(elo_rating=away_elo),
        odds=[OddsSnapshot(bookmaker="bk", moneyline_home=home_ml,
                           moneyline_away=away_ml, spread_home=spread,
                           spread_away=-spread, over_under=220.0)])


def _edges(picks):
    return sorted(p.edge_pct for p in picks)


def test_an_enormous_claimed_edge_is_refused():
    """A huge mismatch priced as a coin flip: the model claims a big edge."""
    s = EnsembleStrategy("ensemble", {"min_edge": 5.0})
    game = _game(home_ml=-110, away_ml=-110, home_elo=2100.0, away_elo=1200.0)
    picks = s.predict(game)
    assert picks == [] or max(p.edge_pct for p in picks) < DEFAULT_MAX_EDGE


def test_a_modest_edge_is_still_taken(model_claiming):
    """A believable edge must survive the ceiling."""
    model_claiming(0.62)          # vs a -110/-110 line: about 12pp
    s = EnsembleStrategy("ensemble", {"min_edge": 1.0})
    picks = s.predict(_game(home_ml=-110, away_ml=-110))
    assert picks, "the ceiling swallowed an ordinary pick"
    assert all(p.edge_pct < DEFAULT_MAX_EDGE for p in picks)


def test_the_ceiling_is_configurable(model_claiming):
    model_claiming(0.95)          # vs a -110/-110 line: about 45pp
    game = _game(home_ml=-110, away_ml=-110)
    wide = EnsembleStrategy("ensemble", {"min_edge": 5.0, "max_edge": 100.0})
    assert any(p.edge_pct >= DEFAULT_MAX_EDGE for p in wide.predict(game))
    narrow = EnsembleStrategy("ensemble", {"min_edge": 5.0})
    assert narrow.predict(game) == [], "the default ceiling must refuse it"


def test_the_default_is_where_the_data_breaks():
    """20% is the boundary both markets change sign at, not a round number
    chosen for looking tidy."""
    assert DEFAULT_MAX_EDGE == 20.0


def test_the_ceiling_applies_to_spreads_too():
    """Spread is where the evidence is cleanest, every price being -110."""
    s = EnsembleStrategy("ensemble", {"min_edge": 1.0})
    game = _game(home_elo=2100.0, away_elo=1200.0, spread=-1.0)
    spreads = [p for p in s.predict(game) if p.pick_type == "spread"]
    assert all(p.edge_pct < DEFAULT_MAX_EDGE for p in spreads)


def test_a_ceiling_below_the_floor_yields_nothing():
    """Nonsense config must produce no picks rather than an exception."""
    s = EnsembleStrategy("ensemble", {"min_edge": 30.0, "max_edge": 10.0})
    assert s.predict(_game(home_elo=1800.0, away_elo=1400.0)) == []


def test_the_refusal_is_logged(caplog, model_claiming):
    import logging

    model_claiming(0.95)
    s = EnsembleStrategy("ensemble", {"min_edge": 5.0})
    with caplog.at_level(logging.INFO):
        s.predict(_game(home_ml=-110, away_ml=-110))
    assert "max_edge" in caplog.text or "implausible" in caplog.text.lower()

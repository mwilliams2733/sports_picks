"""Prop edge is measured over the price's break-even, like every game pick.

It was (prob - 0.5) * 200, which ignored the price: a -300 line scored the
same edge as a -110 line at the same probability. That is what let the top
rung of a book's ladder (Under 275.5 at -230, against a main line of 245.5)
read as a large edge. Since 2026-10-04 prop edge is
`odds_utils.value_edge(prob, price)`, the function game picks use. The star
thresholds were kept (owner), so the bar is stricter.
"""
from datetime import date

import pytest

from backend.analysis.odds_utils import american_to_implied_prob, value_edge
from backend.analysis.prop_analyzer import PropAnalyzer
from backend.pipeline.prop_pipeline import _build_prop_pick
from backend.tests.test_prop_analyzer import (_make_game, _make_game_log,
                                              _make_prop, _make_season_avg,
                                              _make_team, session)  # noqa: F401

HOT = [32.0, 30.0, 35.0, 28.0, 31.0]


def _analyze(session, odds, line=25.5, outcome="Over", **kw):
    team = _make_team(session)
    game = _make_game(session, team)
    season = _make_season_avg(session, team, pts=27.0)
    recent = [_make_game_log(session, team, p, date(2026, 3, i + 1)) for i, p in enumerate(HOT)]
    prop = _make_prop(session, game, line=line, outcome=outcome)
    prop.odds = odds
    return PropAnalyzer(**kw).analyze(prop, season, recent)


def test_edge_is_probability_over_break_even(session):
    a = _analyze(session, -110, min_edge=-100)

    assert a.model_probability is not None
    assert a.edge_pct == pytest.approx(value_edge(a.model_probability, -110), abs=0.01)
    assert a.edge_pct == pytest.approx(
        (a.model_probability - american_to_implied_prob(-110)) * 100, abs=0.01)


def test_a_juicier_price_is_less_edge_at_the_same_probability(session):
    """The defect: the old formula gave both the same edge."""
    cheap = _analyze(session, -110, min_edge=-100)
    session.rollback()
    juiced = _analyze(session, -300, min_edge=-100)

    assert cheap.model_probability == pytest.approx(juiced.model_probability)
    assert juiced.edge_pct < cheap.edge_pct
    assert cheap.edge_pct - juiced.edge_pct == pytest.approx(
        (american_to_implied_prob(-300) - american_to_implied_prob(-110)) * 100, abs=0.02)


def test_a_price_beyond_the_models_probability_is_no_pick(session):
    """At -2000 break-even is 95.2%; no projection here reaches it."""
    assert _analyze(session, -2000) is None


def test_the_stored_pick_carries_the_model_probability(session):
    a = _analyze(session, -110, min_edge=-100)

    pick = _build_prop_pick(a, strategy_id=7)

    assert pick.model_prob == pytest.approx(a.model_probability)
    assert pick.edge_pct == a.edge_pct


def test_a_refreshed_pick_takes_the_new_probability(session):
    from dataclasses import replace

    from backend.pipeline.prop_pipeline import _refresh_prop_pick
    a = _analyze(session, -110, min_edge=-100)
    pick = _build_prop_pick(a, strategy_id=7)

    _refresh_prop_pick(pick, replace(a, model_probability=0.61))

    assert pick.model_prob == pytest.approx(0.61)

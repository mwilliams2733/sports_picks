from backend.data_types import PickFactor
from backend.analysis.rationale import render_factor, render_rationale


def test_render_rating_gap_home_strong():
    f = PickFactor(code="rating_gap", side="home", strength="strong")
    assert render_factor(f, "Chiefs", "Bills") == "Rating gap strongly favors Chiefs"


def test_render_schedule_fatigue_names_the_tired_team_not_the_favored_one():
    # `side` is always THE SIDE THE FACTOR FAVORS. schedule_fatigue favors the
    # rested team, so side="home" means the AWAY team is the tired one, and
    # the template names that away team.
    f = PickFactor(code="schedule_fatigue", side="home", strength="moderate")
    assert render_factor(f, "Chiefs", "Bills") == "Bills on a compressed schedule"


def test_unknown_code_renders_empty_not_raises():
    f = PickFactor(code="not_a_real_code", side="home", strength="strong")
    assert render_factor(f, "Chiefs", "Bills") == ""


def test_render_rationale_joins_two_strongest():
    # All three favor the home side (Chiefs), which is what a real pick looks
    # like — factors for one pick all point the same way.
    factors = [
        PickFactor(code="rating_gap", side="home", strength="strong"),
        PickFactor(code="schedule_fatigue", side="home", strength="moderate"),
        PickFactor(code="recent_form", side="home", strength="slight"),
    ]
    out = render_rationale(factors, "Chiefs", "Bills", limit=2)
    assert out == "Rating gap strongly favors Chiefs; Bills on a compressed schedule."


def test_render_rationale_empty_factors_returns_empty_string():
    assert render_rationale([], "Chiefs", "Bills") == ""


def test_render_rationale_skips_unknown_codes():
    factors = [
        PickFactor(code="bogus", side="home", strength="strong"),
        PickFactor(code="rating_gap", side="home", strength="slight"),
    ]
    assert render_rationale(factors, "Chiefs", "Bills") == "Rating gap slightly favors Chiefs."


import pytest

from backend.analysis.odds_utils import prob_to_american
from backend.analysis.rationale import DEFAULT_CAVEAT, factors_from_json, pick_note


def test_prob_to_american_is_the_margin_free_price():
    assert prob_to_american(0.58) == -138
    assert prob_to_american(0.4) == 150
    assert prob_to_american(0.5) == -100
    with pytest.raises(ValueError):
        prob_to_american(1.0)


def test_factors_from_json_reads_codes_and_ignores_junk():
    fs = factors_from_json('[{"code": "rating_gap", "side": "home", "strength": "slight"}, 7]')
    assert [(f.code, f.side, f.strength) for f in fs] == [("rating_gap", "home", "slight")]
    assert factors_from_json(None) == [] and factors_from_json("not json") == []
    assert factors_from_json('{"a": 1}') == []


def _note(**kw):
    base = dict(sport="nfl", pick_type="moneyline", pick_value="HOME ML", home="Kansas City",
                away="Buffalo", model_prob=0.58, market_prob=0.52, edge_pct=10.7, odds=-110,
                factors=factors_from_json('[{"code": "rating_gap", "side": "home", "strength": "slight"}]'))
    return pick_note(**{**base, **kw})


def test_a_full_nfl_moneyline_note():
    assert _note() == (
        "The model gives Kansas City a 58% chance to win; the books' price, with their margin "
        "removed, says 52%. At -110 that is a 10.7% edge (fair price -138). Rating gap slightly "
        "favors Kansas City. The model has not shown an edge over NFL closing lines yet, so treat "
        "this as one opinion, not a sure thing.")


def test_spread_and_total_wording():
    assert _note(pick_type="spread", pick_value="AWAY +3.5", factors=[]).startswith(
        "The model gives Buffalo +3.5 a 58% chance to cover;")
    assert _note(pick_type="over_under", pick_value="Over 47.5", factors=[]).startswith(
        "The model gives the Over 47.5 a 58% chance to hit;")


def test_missing_numbers_are_left_out_never_invented():   # Review Focus 4
    note = _note(market_prob=None, odds=None, factors=[], sport="ncaaf")
    assert note == ("The model gives Kansas City a 58% chance to win. " + DEFAULT_CAVEAT)
    assert "None" not in note and "nan" not in note.lower()

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

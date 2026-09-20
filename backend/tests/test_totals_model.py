"""The points-for totals model.

Replaces `_predicted_total`, which was built from offensive_rating,
defensive_rating and pace -- none of which any collector supplies -- and so
returned exactly 200.0 for every game in every sport.

The prediction is the standard matchup average: each side's expected score
is the mean of its own scoring rate and its opponent's concession rate.
"""
import datetime

import pytest

import backend.analysis.variants.ensemble as ens
from backend.analysis.variants.ensemble import EnsembleStrategy
from backend.data_types import GameData, OddsSnapshot, TeamStats


def _stats(pf=None, pa=None):
    return TeamStats(
        point_diff=0.0, home_record=(5, 5), away_record=(5, 5),
        last_n_record=(5, 5), offensive_rating=100.0, defensive_rating=100.0,
        pace=100.0, strength_of_schedule=0.5, elo_rating=1500.0, rest_days=1,
        points_for=pf, points_against=pa)


def _game(sport, line, home, away):
    return GameData(
        game_id=1, sport=sport, date=datetime.date(2026, 9, 19),
        home_team_id=1, away_team_id=2, home_stats=home, away_stats=away,
        odds=[OddsSnapshot(bookmaker="bk", moneyline_home=-110,
                           moneyline_away=-110, spread_home=-1.5,
                           spread_away=1.5, over_under=line)])


S = EnsembleStrategy("ensemble", {}, {})


def test_two_average_teams_predict_their_shared_scoring_rate():
    """Both score 110 and concede 110, so the expected total is 220."""
    g = _game("nba", 220.5, _stats(110, 110), _stats(110, 110))
    assert S._predicted_total(g) == pytest.approx(220.0)


def test_a_strong_offence_against_a_weak_defence_raises_the_total():
    g = _game("nba", 220.5, _stats(120, 110), _stats(110, 120))
    # home expected = (120 + 120)/2 = 120; away = (110 + 110)/2 = 110
    assert S._predicted_total(g) == pytest.approx(230.0)


def test_the_prediction_scales_with_the_sport():
    """The old constant 200 was above every ncaab line and below every nba one."""
    ncaab = _game("ncaab", 145.5, _stats(72, 70), _stats(71, 73))
    nba = _game("nba", 225.5, _stats(114, 112), _stats(113, 115))
    assert 130 < S._predicted_total(ncaab) < 175
    assert 205 < S._predicted_total(nba) < 260


def test_a_low_scoring_sport_is_not_forced_to_200():
    """mlb totals run 6.5-20.5. The old model predicted 200 and took Over."""
    g = _game("mlb", 8.5, _stats(4.5, 4.2), _stats(4.1, 4.6))
    assert 6 < S._predicted_total(g) < 12


def test_no_totals_pick_without_scoring_history():
    g = _game("nba", 225.5, _stats(None, None), _stats(None, None))
    assert [p for p in S.predict(g) if p.pick_type == "over_under"] == []


def test_one_side_missing_is_still_refused():
    g = _game("nba", 225.5, _stats(114, 112), _stats(None, None))
    assert [p for p in S.predict(g) if p.pick_type == "over_under"] == []


@pytest.fixture()
def nba_validated(monkeypatch):
    """Treat nba as validated so the model itself can be exercised.

    TOTALS_VALIDATED_SPORTS is empty in production: the model does not beat
    the market line anywhere on a sample worth the name. These tests are
    about the model's output, not about that decision.
    """
    monkeypatch.setattr(ens, "TOTALS_VALIDATED_SPORTS", frozenset({"nba"}))


def test_a_totals_pick_is_produced_when_the_model_disagrees_with_the_line(
    nba_validated
):
    """A line far below both teams' scoring rates should read as an Over."""
    g = _game("nba", 190.5, _stats(118, 116), _stats(117, 119))
    outs = [p for p in S.predict(g) if p.pick_type == "over_under"]
    assert outs and outs[0].pick_value.startswith("Over")


def test_the_other_direction_reads_as_an_under(nba_validated):
    g = _game("nba", 250.5, _stats(105, 103), _stats(104, 106))
    outs = [p for p in S.predict(g) if p.pick_type == "over_under"]
    assert outs and outs[0].pick_value.startswith("Under")


def test_the_edge_is_no_longer_pinned_at_fifty(nba_validated):
    """model_prob 1.0 / edge 50.0 was the saturated-CDF signature."""
    g = _game("nba", 218.5, _stats(112, 110), _stats(111, 113))
    outs = [p for p in S.predict(g) if p.pick_type == "over_under"]
    for p in outs:
        assert p.edge_pct < 50.0
        assert p.model_probability < 1.0


# --------------------------------------------------------------------------
# The per-sport validation gate.
# --------------------------------------------------------------------------

def test_no_sport_is_validated_in_production():
    """The model does not beat the market line anywhere yet.

    If this starts failing, someone added a sport -- check that
    totals_report supports it on a sample worth the name.
    """
    assert ens.TOTALS_VALIDATED_SPORTS == frozenset()


def test_an_unvalidated_sport_produces_no_totals_pick(monkeypatch):
    monkeypatch.setattr(ens, "TOTALS_VALIDATED_SPORTS", frozenset({"mlb"}))
    g = _game("nba", 190.5, _stats(118, 116), _stats(117, 119))
    assert [p for p in S.predict(g) if p.pick_type == "over_under"] == []


def test_a_validated_sport_does_produce_one(monkeypatch):
    monkeypatch.setattr(ens, "TOTALS_VALIDATED_SPORTS", frozenset({"nba"}))
    g = _game("nba", 190.5, _stats(118, 116), _stats(117, 119))
    assert [p for p in S.predict(g) if p.pick_type == "over_under"]


def test_the_matchup_form_is_the_same_number():
    """Pins why the docstring does not claim an opponent adjustment.

    The two formulations differ only in how the total is split between the
    sides, and a total discards that. A test asserting the 'matchup' form
    would pass against either, which is how a misleading description
    survives.
    """
    h, a = _stats(118.0, 103.0), _stats(96.0, 127.0)
    g = _game("nba", 220.5, h, a)
    matchup = ((h.points_for + a.points_against) / 2
               + (a.points_for + h.points_against) / 2)
    assert S._predicted_total(g) == pytest.approx(matchup)


def test_it_is_the_mean_of_the_two_typical_game_totals():
    h, a = _stats(118.0, 103.0), _stats(96.0, 127.0)
    g = _game("nba", 220.5, h, a)
    assert S._predicted_total(g) == pytest.approx(((118 + 103) + (96 + 127)) / 2)

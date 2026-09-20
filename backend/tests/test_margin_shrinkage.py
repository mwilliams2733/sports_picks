"""Guards for shrinking a rolling margin toward zero.

`_predicted_point_diff` predicted the home margin as the raw difference of
two rolling mean margins, taken at face value however few games produced
them. In NFL week 2 that gave predicted margins of +39 and -45, cover
probabilities of 0.9936 and 0.0002, and claimed edges up to 50% -- every one
refused by `max_edge`, so the sport produced no spread picks at all.

The shrinkage constant is measured, not chosen: see
`backend.analysis.margin_report`. k=5 minimises mean absolute error in each
of the three sports that carry any signal, on samples of 1253 nba games over
175 dates, 260 ncaaf over 12, and 72 ncaab over 8. The minimum is flat
either side of 5, so the value is not fitted to a spike.
"""
import pytest

from backend.analysis.variants.ensemble import (
    MARGIN_SHRINKAGE_K, EnsembleStrategy, shrink_margin,
)
from backend.data_types import GameData, TeamStats


def _stats(point_diff, games):
    """A team whose rolling mean rests on `games` games."""
    return TeamStats(
        point_diff=point_diff, home_record=(0, 0), away_record=(0, 0),
        last_n_record=(games, 0), offensive_rating=100.0,
        defensive_rating=100.0, pace=100.0, strength_of_schedule=0.5,
        elo_rating=1500.0, rest_days=2)


def _game(home, away):
    import datetime
    return GameData(game_id=1, sport="nfl", date=datetime.date(2026, 9, 20),
                    home_team_id=1, away_team_id=2,
                    home_stats=home, away_stats=away, odds=[])


def test_k_is_the_measured_value():
    assert MARGIN_SHRINKAGE_K == 5.0


def test_a_single_game_is_heavily_discounted():
    """One game of evidence is worth a sixth of its face value at k=5."""
    assert shrink_margin(30.0, 1) == pytest.approx(30.0 * 1 / 6)


def test_a_team_with_no_history_predicts_nothing():
    """Zero games is no evidence, not a zero margin with confidence."""
    assert shrink_margin(30.0, 0) == 0.0


def test_a_nonsensical_game_count_cannot_flip_the_sign():
    """The `games <= 0` branch earns its place here, not at n=0 -- at zero
    the arithmetic already yields zero. At a negative count it would not:
    -1 would return -0.25x the margin, silently backing the other team."""
    assert shrink_margin(30.0, -1) == 0.0
    assert shrink_margin(-30.0, -3) == 0.0


def test_a_full_window_is_only_mildly_discounted():
    assert shrink_margin(30.0, 10) == pytest.approx(30.0 * 10 / 15)


def test_shrinkage_never_flips_the_sign_or_grows_the_margin():
    for pd in (-40.0, -7.5, 7.5, 40.0):
        for n in range(0, 11):
            out = shrink_margin(pd, n)
            assert abs(out) <= abs(pd)
            assert out == 0.0 or (out > 0) == (pd > 0)


def test_the_week_two_blowout_no_longer_predicts_an_absurd_margin():
    """The live case: two teams each one game in, one blown out, one blowing
    out. The raw difference was 45 points."""
    game = _game(_stats(-22.0, 1), _stats(23.0, 1))
    raw = game.home_stats.point_diff - game.away_stats.point_diff
    assert raw == -45.0

    predicted = EnsembleStrategy(name="t", config={})._predicted_point_diff(game)

    assert abs(predicted) < 10.0, "a one-game blowout still dominates"
    assert predicted == pytest.approx(-45.0 / 6)


def test_a_settled_season_still_carries_most_of_its_signal():
    """Shrinkage must not throw away evidence that has actually accumulated."""
    game = _game(_stats(8.0, 10), _stats(-4.0, 10))
    predicted = EnsembleStrategy(name="t", config={})._predicted_point_diff(game)
    assert predicted == pytest.approx(12.0 * 10 / 15)

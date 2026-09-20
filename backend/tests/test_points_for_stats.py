"""Points-for / points-against as point-in-time team stats.

These exist to give the totals model real inputs. `_predicted_total` used
offensive_rating, defensive_rating and pace -- none of which any collector
supplies -- so it returned exactly 200.0 for every game in every sport.

Two properties matter more than the averaging itself:

* **Point-in-time.** A game's own score must never reach its own features,
  or the model looks clairvoyant in backtest and is useless live.
* **Absent, not zero.** `rolling_point_diff` returns 0.0 for a team with no
  history, which is a sensible neutral margin. For points-for it is not: a
  predicted total of 0 is worse than no prediction. The key is omitted.
"""
import datetime

import pytest

from backend.pipeline.team_stats import (
    COMPUTED_STAT_TYPES,
    compute_team_stats,
    rolling_point_diff,
    rolling_points_against,
    rolling_points_for,
)


class _G:
    """Minimal stand-in for a Game row."""
    def __init__(self, day, home, away, hs, aws):
        self.id = day          # _team_games sorts on (date, id)
        self.date = datetime.date(2026, 3, day)
        self.home_team_id, self.away_team_id = home, away
        self.home_score, self.away_score = hs, aws


def _history():
    # Team 1 scores 110, 100, 90 and concedes 100, 95, 85.
    return [
        _G(1, 1, 2, 110, 100),
        _G(2, 2, 1, 95, 100),     # team 1 away: scores 100, concedes 95
        _G(3, 1, 2, 90, 85),
    ]


def test_points_for_is_the_mean_of_what_the_team_scored():
    assert rolling_points_for(_history(), 1) == pytest.approx((110 + 100 + 90) / 3)


def test_points_against_is_the_mean_of_what_it_conceded():
    assert rolling_points_against(_history(), 1) == pytest.approx((100 + 95 + 85) / 3)


def test_home_and_away_are_read_from_the_right_side():
    """Team 2 is the mirror image; reading the wrong column swaps them."""
    assert rolling_points_for(_history(), 2) == pytest.approx((100 + 95 + 85) / 3)
    assert rolling_points_against(_history(), 2) == pytest.approx((110 + 100 + 90) / 3)


def test_point_diff_stays_consistent_with_the_two_new_stats():
    """They must agree by construction, not by coincidence."""
    games = _history()
    assert rolling_point_diff(games, 1) == pytest.approx(
        rolling_points_for(games, 1) - rolling_points_against(games, 1))


def test_a_team_with_no_history_has_no_average():
    """0.0 is a real scoreline, so it cannot double as 'unknown'."""
    assert rolling_points_for([], 1) is None
    assert rolling_points_against([], 1) is None


def test_the_lookback_window_is_respected():
    games = _history()
    assert rolling_points_for(games, 1, lookback=1) == pytest.approx(90.0)


def test_only_games_strictly_before_the_date_are_used(monkeypatch):
    """The game's own result must not reach its own features."""
    games = _history() + [_G(4, 1, 2, 999, 0)]
    stats = compute_team_stats(games, 1, datetime.date(2026, 3, 4))
    assert stats["points_for"] == pytest.approx((110 + 100 + 90) / 3)


def test_the_keys_are_absent_rather_than_zero_when_unknown():
    stats = compute_team_stats([], 1, datetime.date(2026, 3, 4))
    assert "points_for" not in stats
    assert "points_against" not in stats


def test_they_are_declared_in_the_computed_contract():
    """COMPUTED_STAT_TYPES is what _game_has_stats and the backfill read."""
    assert "points_for" in COMPUTED_STAT_TYPES
    assert "points_against" in COMPUTED_STAT_TYPES


def test_a_tie_contributes_equally_to_both():
    games = [_G(1, 1, 2, 100, 100)]
    assert rolling_points_for(games, 1) == rolling_points_against(games, 1) == 100.0

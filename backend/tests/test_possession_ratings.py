"""offensive_rating, defensive_rating and pace, computed at last.

These three were the features `team_stats.py` refused to emit because
possessions were unavailable. `team_box_scores` now holds them, so the
features can be computed the same way every other one in that module is:
**strictly from games before the one they hang off.**

    offensive_rating = 100 * points scored      / possessions used
    defensive_rating = 100 * points conceded    / opponent possessions
    pace             = possessions per regulation-length game

Pace is normalised by minutes actually played, not by game count. An
overtime game uses more possessions without being played faster, and NBA
regulation is 48 minutes against college's 40, so raw possessions-per-game
is not comparable across either boundary.

The absence rule is inherited, not re-decided
---------------------------------------------
A team with no prior box scores gets **no** rating key, exactly as it gets
no `points_for` before its first game. That is the module's existing
contract -- an unmeasurable feature is absent rather than invented -- and
it is what let the model correctly learn nothing from these three while
they were constant. A defaulted 100.0 would be indistinguishable from a
measured one.
"""
import datetime

import pytest

from backend.pipeline.team_stats import (REGULATION_MINUTES, Possessions,
                                         compute_team_stats,
                                         rolling_defensive_rating,
                                         rolling_offensive_rating,
                                         rolling_pace)

DAY = datetime.date(2026, 3, 20)


class _Game:
    """Enough of a Game row for these pure functions."""

    def __init__(self, gid, day, home_id, away_id, home_score, away_score,
                 sport="nba"):
        self.id = gid
        self.date = day
        self.sport = sport
        self.home_team_id = home_id
        self.away_team_id = away_id
        self.home_score = home_score
        self.away_score = away_score


def _box(poss, minutes=240.0):
    return Possessions(possessions=poss, minutes=minutes)


def _one_prior_game(poss_home=100.0, poss_away=100.0, hs=110, as_=100):
    """Team 1 hosted team 2 the day before and won hs-as_."""
    game = _Game(1, DAY - datetime.timedelta(days=1), 1, 2, hs, as_)
    box = {(1, 1): _box(poss_home), (1, 2): _box(poss_away)}
    return [game], box


def test_offensive_rating_is_points_per_hundred_possessions():
    games, box = _one_prior_game(poss_home=100.0, hs=110)

    assert rolling_offensive_rating(games, 1, box) == pytest.approx(110.0)


def test_defensive_rating_uses_the_opponents_possessions():
    """The opponent scored their points on THEIR possessions, not ours. The
    two differ by offensive rebounds and turnovers every game."""
    games, box = _one_prior_game(poss_home=100.0, poss_away=80.0,
                                 hs=110, as_=100)

    assert rolling_defensive_rating(games, 1, box) == pytest.approx(125.0)


def test_a_teams_rating_is_computed_from_its_own_side_of_the_game():
    games, box = _one_prior_game(poss_home=100.0, poss_away=80.0,
                                 hs=110, as_=100)

    assert rolling_offensive_rating(games, 2, box) == pytest.approx(125.0)
    assert rolling_defensive_rating(games, 2, box) == pytest.approx(110.0)


def test_pace_normalises_by_minutes_so_overtime_is_not_faster():
    """110 possessions over 53 played minutes is a SLOWER game than 100 over
    48. Dividing by game count instead would call it faster."""
    reg = _Game(1, DAY - datetime.timedelta(days=2), 1, 2, 100, 98)
    ot = _Game(2, DAY - datetime.timedelta(days=1), 1, 2, 120, 118)
    box = {(1, 1): _box(100.0, minutes=240.0),
           (1, 2): _box(100.0, minutes=240.0),
           (2, 1): _box(110.0, minutes=265.0),
           (2, 2): _box(110.0, minutes=265.0)}

    pace = rolling_pace([reg, ot], 1, box, sport="nba")

    assert pace == pytest.approx((100.0 + 110.0 / 53 * 48) / 2, abs=0.01)
    assert pace < 100.0, "the overtime game was played slower, not faster"


def test_college_pace_uses_a_forty_minute_regulation():
    """68 possessions in 40 minutes is not the same pace as 68 in 48."""
    game = _Game(1, DAY - datetime.timedelta(days=1), 1, 2, 70, 68,
                 sport="ncaab")
    box = {(1, 1): _box(68.0, minutes=200.0), (1, 2): _box(68.0, minutes=200.0)}

    assert REGULATION_MINUTES["ncaab"] == 40
    assert rolling_pace([game], 1, box, sport="ncaab") == pytest.approx(68.0)


def test_a_team_with_no_prior_box_scores_has_no_rating():
    """Absent, not 100.0. A defaulted rating is indistinguishable from a
    measured one, which is how these three stayed invisible."""
    games, _ = _one_prior_game()

    assert rolling_offensive_rating(games, 1, {}) is None
    assert rolling_defensive_rating(games, 1, {}) is None
    assert rolling_pace(games, 1, {}, sport="nba") is None


def test_a_game_with_zero_possessions_is_skipped_not_divided_by():
    games, box = _one_prior_game()
    box[(1, 1)] = _box(0.0)

    assert rolling_offensive_rating(games, 1, box) is None


def test_a_box_score_without_minutes_cannot_contribute_to_pace():
    """Pace needs a length. The same game still contributes to the two
    ratings, which do not."""
    games, box = _one_prior_game()
    box[(1, 1)] = Possessions(possessions=100.0, minutes=None)

    assert rolling_pace(games, 1, box, sport="nba") is None
    assert rolling_offensive_rating(games, 1, box) == pytest.approx(110.0)


# --- the point-in-time contract -------------------------------------------

def test_a_game_on_the_same_day_does_not_feed_its_own_rating():
    """The module's invariant: strictly before, by date. Same-day games are
    excluded because `start_time` is nullable across most of the history,
    so there is no reliable intra-day ordering."""
    today = _Game(1, DAY, 1, 2, 110, 100)
    box = {(1, 1): _box(100.0), (1, 2): _box(100.0)}

    stats = compute_team_stats([today], 1, DAY, box_scores=box)

    assert "offensive_rating" not in stats
    assert "pace" not in stats


def test_the_ratings_join_the_stat_dict_when_they_can_be_measured():
    games, box = _one_prior_game(poss_home=100.0, poss_away=80.0,
                                 hs=110, as_=100)

    stats = compute_team_stats(games, 1, DAY, box_scores=box)

    assert stats["offensive_rating"] == pytest.approx(110.0)
    assert stats["defensive_rating"] == pytest.approx(125.0)
    assert stats["pace"] == pytest.approx(100.0)


def test_omitting_box_scores_entirely_changes_nothing_else():
    """Every existing caller passes no box scores. They must keep working
    and keep getting exactly the stats they got before."""
    games, _ = _one_prior_game()

    without = compute_team_stats(games, 1, DAY)

    assert "offensive_rating" not in without
    assert "point_diff" in without

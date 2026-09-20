"""Home/away scoring splits.

A team's scoring rate may differ by venue, so the totals model can in
principle use the home side's home history and the away side's road history
instead of one blended rate.

Two things the splits must get right:

* **Neutral games belong to neither venue.** A bracket game at a neutral
  site is not evidence of how a team plays at home, and ncaab's entire
  history is such games. They stay in the blended stats and are excluded
  from the splits.
* **A thin split is worse than no split.** Halving the sample doubles each
  estimate's variance, so a venue with too little history is omitted and the
  blended rate is used instead.
"""
import datetime

import pytest

from backend.pipeline.team_stats import (
    COMPUTED_STAT_TYPES,
    MIN_VENUE_GAMES,
    compute_team_stats,
    rolling_points_against,
    rolling_points_for,
)


class _G:
    def __init__(self, day, home, away, hs, aws, neutral=False):
        self.id = day
        # Offset so day=0 is valid; _team_games sorts on (date, id).
        self.date = datetime.date(2026, 3, 1) + datetime.timedelta(days=day)
        self.home_team_id, self.away_team_id = home, away
        self.home_score, self.away_score = hs, aws
        self.neutral_site = neutral


def _home_heavy(n=5):
    """Team 1 scores 120 at home and 90 on the road.

    n=5 per venue gives exactly DEFAULT_LOOKBACK games in total, so the
    blended rate covers all of them and is not silently truncated -- and
    exactly MIN_VENUE_GAMES per venue, so both splits qualify.
    """
    games = []
    for i in range(n):
        games.append(_G(i + 1, 1, 2, 120, 100))
    for i in range(n):
        games.append(_G(n + i + 1, 2, 1, 100, 90))
    return games


def test_home_split_uses_only_home_games():
    assert rolling_points_for(_home_heavy(), 1, venue="home") == pytest.approx(120.0)


def test_away_split_uses_only_road_games():
    assert rolling_points_for(_home_heavy(), 1, venue="away") == pytest.approx(90.0)


def test_the_blended_rate_still_mixes_both():
    assert rolling_points_for(_home_heavy(), 1) == pytest.approx(105.0)


def test_points_against_splits_the_same_way():
    assert rolling_points_against(_home_heavy(), 1, venue="home") == pytest.approx(100.0)
    assert rolling_points_against(_home_heavy(), 1, venue="away") == pytest.approx(100.0)


def test_a_neutral_game_counts_for_neither_venue():
    """ncaab's whole history is neutral-site bracket games.

    The neutral game is inserted first so it cannot be pushed out of the
    lookback window -- otherwise the test would pass by truncation rather
    than by the venue filter.
    """
    games = [_G(0, 1, 2, 200, 100, neutral=True)] + _home_heavy()
    assert rolling_points_for(games, 1, venue="home") == pytest.approx(120.0)
    assert rolling_points_for(games, 1, venue="away") == pytest.approx(90.0)


def test_a_neutral_game_still_counts_in_the_blended_rate():
    """It is real evidence of scoring, just not of home-court scoring."""
    games = [_G(1, 1, 2, 100, 90), _G(2, 1, 2, 200, 90, neutral=True)]
    assert rolling_points_for(games, 1) == pytest.approx(150.0)


def test_a_thin_venue_has_no_split():
    """Halving the sample doubles the variance; too few games is worse than none."""
    games = [_G(i + 1, 1, 2, 120, 100) for i in range(MIN_VENUE_GAMES - 1)]
    assert rolling_points_for(games, 1, venue="home") is None


def test_exactly_the_minimum_is_enough():
    games = [_G(i + 1, 1, 2, 120, 100) for i in range(MIN_VENUE_GAMES)]
    assert rolling_points_for(games, 1, venue="home") == pytest.approx(120.0)


def test_the_split_keys_are_declared_and_written():
    stats = compute_team_stats(_home_heavy(), 1, datetime.date(2026, 4, 1))
    for key in ("points_for_home", "points_against_home",
                "points_for_away", "points_against_away"):
        assert key in COMPUTED_STAT_TYPES
        assert key in stats


def test_the_split_keys_are_absent_when_a_venue_is_thin():
    games = [_G(1, 1, 2, 120, 100)]          # one home game only
    stats = compute_team_stats(games, 1, datetime.date(2026, 4, 1))
    assert "points_for_home" not in stats
    assert "points_for_away" not in stats
    assert "points_for" in stats, "the blended rate is still written"


def test_venue_splits_are_off_by_default():
    """Measured, and they change nothing.

    On 1068 nba games with both splits available, the paired difference in
    absolute error is -0.028 points (t = -0.20, 95% CI -0.297 .. +0.241) --
    0.18% of a ~15.6 MAE, indistinguishable from zero.

    So the simpler predictor stays. Turn this on when
    backend.analysis.totals_report shows a difference worth the halved
    sample per statistic.
    """
    import backend.analysis.variants.ensemble as ens
    assert ens.USE_VENUE_SPLITS is False


def test_the_splits_are_used_when_switched_on(monkeypatch):
    """The switch must actually reach the prediction."""
    import datetime as dt

    import backend.analysis.variants.ensemble as ens
    from backend.data_types import GameData, OddsSnapshot, TeamStats

    def _ts(**kw):
        base = dict(point_diff=0.0, home_record=(0, 0), away_record=(0, 0),
                    last_n_record=(0, 0), offensive_rating=100.0,
                    defensive_rating=100.0, pace=100.0,
                    strength_of_schedule=0.5, elo_rating=1500.0, rest_days=1,
                    points_for=100.0, points_against=100.0)
        base.update(kw)
        return TeamStats(**base)

    g = GameData(game_id=1, sport="nba", date=dt.date(2026, 9, 19),
                 home_team_id=1, away_team_id=2,
                 home_stats=_ts(points_for_home=130.0, points_against_home=130.0),
                 away_stats=_ts(points_for_away=130.0, points_against_away=130.0),
                 odds=[OddsSnapshot(bookmaker="b", moneyline_home=-110,
                                    moneyline_away=-110, spread_home=-1.0,
                                    spread_away=1.0, over_under=220.0)])
    s = ens.EnsembleStrategy("ensemble", {}, {})

    monkeypatch.setattr(ens, "USE_VENUE_SPLITS", False)
    assert s._predicted_total(g) == 200.0          # blended 100+100 each

    monkeypatch.setattr(ens, "USE_VENUE_SPLITS", True)
    assert s._predicted_total(g) == 260.0          # splits 130+130 each


def test_a_neutral_game_ignores_the_splits_even_when_switched_on(monkeypatch):
    """Neither side is at its own venue, so the blended rate is correct."""
    import datetime as dt

    import backend.analysis.variants.ensemble as ens
    from backend.data_types import GameData, OddsSnapshot, TeamStats

    def _ts(**kw):
        base = dict(point_diff=0.0, home_record=(0, 0), away_record=(0, 0),
                    last_n_record=(0, 0), offensive_rating=100.0,
                    defensive_rating=100.0, pace=100.0,
                    strength_of_schedule=0.5, elo_rating=1500.0, rest_days=1,
                    points_for=100.0, points_against=100.0)
        base.update(kw)
        return TeamStats(**base)

    g = GameData(game_id=1, sport="ncaab", date=dt.date(2026, 3, 19),
                 home_team_id=1, away_team_id=2, neutral_site=True,
                 home_stats=_ts(points_for_home=130.0, points_against_home=130.0),
                 away_stats=_ts(points_for_away=130.0, points_against_away=130.0),
                 odds=[OddsSnapshot(bookmaker="b", moneyline_home=-110,
                                    moneyline_away=-110, spread_home=-1.0,
                                    spread_away=1.0, over_under=145.0)])
    monkeypatch.setattr(ens, "USE_VENUE_SPLITS", True)
    s = ens.EnsembleStrategy("ensemble", {}, {})
    assert s._predicted_total(g) == 200.0

"""Opponent-strength adjustment for the scoring rates.

A team's raw points-for is inflated by facing weak defences and deflated by
facing strong ones. Over a full season that washes out -- opponent defence
faced varies by only 0.27 points sd across nba teams -- but the model reads
a 10-game window, where it varies by 1.45, because ten games of an
82-game schedule are nowhere near balanced.

The adjustment re-weights the same games rather than splitting them, so
unlike the venue splits it costs no sample size.
"""
import datetime

import pytest

from backend.pipeline.team_stats import (
    COMPUTED_STAT_TYPES,
    adjusted_points_against,
    adjusted_points_for,
    opponent_rates,
)


class _G:
    def __init__(self, day, home, away, hs, aws):
        self.id = day
        self.date = datetime.date(2026, 3, 1) + datetime.timedelta(days=day)
        self.home_team_id, self.away_team_id = home, away
        self.home_score, self.away_score = hs, aws
        self.neutral_site = False


def _league():
    """Teams 1 and 2 are average; 3 concedes a lot; 4 concedes few."""
    games = []
    d = 0
    for _ in range(4):                    # 1 v 2, both average at 100
        d += 1; games.append(_G(d, 1, 2, 100, 100))
    for _ in range(4):                    # 3 is a sieve: concedes 130
        d += 1; games.append(_G(d, 2, 3, 130, 100))
    for _ in range(4):                    # 4 is a wall: concedes 70
        d += 1; games.append(_G(d, 2, 4, 70, 100))
    return games


def test_opponent_rates_measure_each_team_and_the_league():
    pf, pa, lpf, lpa = opponent_rates(_league())
    assert pa[3] == pytest.approx(130.0)      # team 3 concedes 130
    assert pa[4] == pytest.approx(70.0)       # team 4 concedes 70
    assert lpf == pytest.approx(lpa), "every point scored is a point conceded"


def test_padding_a_weak_defence_is_discounted():
    """Team 2 scored 130 against a sieve; that is worth less than 130."""
    games = _league()
    pf, pa, lpf, lpa = opponent_rates(games)
    raw = sum(130 for _ in range(4)) / 4
    adj = adjusted_points_for(games, 2, lookback=4, rates=(pf, pa, lpf, lpa))
    assert adj < raw


def test_scoring_on_a_strong_defence_is_credited():
    """Team 2 scored only 70 against a wall; that is worth more than 70."""
    games = [g for g in _league() if g.away_team_id == 4 or g.home_team_id == 4] \
        + [g for g in _league() if 4 not in (g.home_team_id, g.away_team_id)]
    pf, pa, lpf, lpa = opponent_rates(_league())
    adj = adjusted_points_for(_league(), 2, lookback=4,
                              rates=(pf, pa, lpf, lpa))
    # Team 2's last 4 games are against team 4 (a wall), raw 70.
    assert adj > 70.0


def test_an_average_schedule_leaves_the_rate_alone():
    """Nothing to correct when every opponent is league-average."""
    games = [_G(i + 1, 1, 2, 100, 100) for i in range(6)]
    rates = opponent_rates(games)
    assert adjusted_points_for(games, 1, lookback=6, rates=rates) == \
        pytest.approx(100.0)


def test_points_against_adjusts_for_opponent_offence():
    games = _league()
    rates = opponent_rates(games)
    adj = adjusted_points_against(games, 2, lookback=12, rates=rates)
    assert adj is not None


def test_no_history_means_no_adjusted_rate():
    rates = opponent_rates([])
    assert adjusted_points_for([], 1, rates=rates) is None


def test_an_opponent_in_the_pool_is_always_rated():
    """Every opponent of a prior game is itself in that pool, by construction.

    Team 1 scored 100 against team 2, who concede 100 on average against a
    league average of 95, so team 2 is a below-average defence and the 100 is
    discounted to 95.
    """
    games = [_G(1, 1, 2, 100, 90)]
    rates = opponent_rates(games)
    assert adjusted_points_for(games, 1, lookback=1, rates=rates) ==         pytest.approx(95.0)


def test_a_zero_baseline_is_a_rate_not_a_missing_value():
    """mlb scores are single digits; an average of 0 conceded is possible.

    Truthiness on the baseline would treat it as unrated and skip the
    adjustment, quietly leaving the raw value in a low-scoring sport.
    """
    # Team 2 concedes 0 on average; league average is 1.0 per side.
    games = [_G(1, 1, 2, 0, 2), _G(2, 3, 4, 1, 1)]
    rates = opponent_rates(games)
    mean_pf, mean_pa, lpf, lpa = rates
    assert mean_pa[2] == 0.0 and lpa == pytest.approx(1.0)
    # Team 1 scored 0 against a defence that concedes 0, i.e. 1.0 below
    # league: the 0 is credited up to 1.0.
    assert adjusted_points_for(games, 1, lookback=1, rates=rates) ==         pytest.approx(1.0)


def test_the_adjusted_keys_are_declared():
    assert "points_for_adj" in COMPUTED_STAT_TYPES
    assert "points_against_adj" in COMPUTED_STAT_TYPES


def test_the_adjustment_is_on():
    """Measured, and the only sport with a real sample improves.

    nba: paired difference -0.118 points of MAE, t = -2.61, 95% CI
    -0.206 .. -0.029 over 1229 games. mlb and ncaab point the same way but
    their intervals straddle zero at n=79 and n=22.

    Contrast the venue splits at t = -0.20, which stay off.
    """
    import backend.analysis.variants.ensemble as ens
    assert ens.USE_OPPONENT_ADJUSTMENT is True


def test_the_adjusted_rates_reach_the_prediction(monkeypatch):
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
                 home_stats=_ts(points_for_adj=120.0, points_against_adj=120.0),
                 away_stats=_ts(points_for_adj=120.0, points_against_adj=120.0),
                 odds=[OddsSnapshot(bookmaker="b", moneyline_home=-110,
                                    moneyline_away=-110, spread_home=-1.0,
                                    spread_away=1.0, over_under=220.0)])
    s = ens.EnsembleStrategy("ensemble", {}, {})

    monkeypatch.setattr(ens, "USE_OPPONENT_ADJUSTMENT", True)
    assert s._predicted_total(g) == 240.0

    monkeypatch.setattr(ens, "USE_OPPONENT_ADJUSTMENT", False)
    assert s._predicted_total(g) == 200.0, "fell back to the raw rates"


def test_a_team_without_adjusted_rates_falls_back(monkeypatch):
    """Absent is absent: use the raw rate rather than skipping the game."""
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
                 home_stats=_ts(points_for_adj=120.0, points_against_adj=120.0),
                 away_stats=_ts(),                      # no adjusted rates
                 odds=[OddsSnapshot(bookmaker="b", moneyline_home=-110,
                                    moneyline_away=-110, spread_home=-1.0,
                                    spread_away=1.0, over_under=220.0)])
    monkeypatch.setattr(ens, "USE_OPPONENT_ADJUSTMENT", True)
    s = ens.EnsembleStrategy("ensemble", {}, {})
    assert s._predicted_total(g) == 220.0   # (240 + 200) / 2

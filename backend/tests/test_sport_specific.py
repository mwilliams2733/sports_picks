from datetime import date
from backend.analysis.variants.sport_specific import SportSpecificStrategy, SPORT_WEIGHTS
from backend.data_types import GameData, TeamStats, OddsSnapshot


def _stats(**kw):
    defaults = dict(point_diff=0.0, home_record=(0, 0), away_record=(0, 0),
        last_n_record=(0, 0), offensive_rating=100.0, defensive_rating=100.0,
        pace=100.0, strength_of_schedule=0.5, elo_rating=1500.0, rest_days=2)
    defaults.update(kw)
    return TeamStats(**defaults)


def test_nba_rest_days_advantage():
    """NBA: well-rested home team vs back-to-back away team should favor home."""
    game = GameData(game_id=1, sport="nba", date=date(2026, 3, 13),
        home_team_id=1, away_team_id=2,
        home_stats=_stats(rest_days=3, home_record=(20, 5), point_diff=3.0, elo_rating=1550),
        away_stats=_stats(rest_days=1, away_record=(10, 15), point_diff=-2.0, elo_rating=1450),
        odds=[OddsSnapshot(bookmaker="dk", moneyline_home=100, moneyline_away=-100,
            spread_home=-2.0, spread_away=2.0, over_under=210.0)])
    strategy = SportSpecificStrategy("sport_specific", {"min_edge": 3.0})
    picks = strategy.predict(game)
    assert len(picks) >= 1
    assert picks[0].pick_value == "HOME ML"


def test_nfl_turnover_margin():
    """NFL: team with strong turnover margin should be favored."""
    game = GameData(game_id=1, sport="nfl", date=date(2026, 11, 15),
        home_team_id=1, away_team_id=2,
        home_stats=_stats(point_diff=5.0, elo_rating=1550, turnover_margin=1.5, red_zone_pct=65.0),
        away_stats=_stats(point_diff=-3.0, elo_rating=1450, turnover_margin=-1.0, red_zone_pct=45.0),
        odds=[OddsSnapshot(bookmaker="dk", moneyline_home=100, moneyline_away=-100,
            spread_home=-3.0, spread_away=3.0, over_under=45.0)])
    strategy = SportSpecificStrategy("sport_specific", {"min_edge": 3.0})
    picks = strategy.predict(game)
    assert len(picks) >= 1
    assert picks[0].pick_value == "HOME ML"


def test_ncaab_conference_strength():
    """NCAA: team from stronger conference should get boost."""
    game = GameData(game_id=1, sport="ncaab", date=date(2026, 2, 15),
        home_team_id=1, away_team_id=2,
        home_stats=_stats(point_diff=4.0, elo_rating=1520, conference_strength=0.8),
        away_stats=_stats(point_diff=-2.0, elo_rating=1480, conference_strength=0.3),
        odds=[OddsSnapshot(bookmaker="dk", moneyline_home=100, moneyline_away=-100,
            spread_home=-3.0, spread_away=3.0, over_under=140.0)])
    strategy = SportSpecificStrategy("sport_specific", {"min_edge": 3.0})
    picks = strategy.predict(game)
    assert len(picks) >= 1


def test_sport_weights_cover_supported_team_sports():
    """SPORT_WEIGHTS must include every team sport that runs through the strategy."""
    assert {"nba", "nfl", "ncaab", "ncaaf", "mlb"}.issubset(set(SPORT_WEIGHTS.keys()))


def test_no_picks_without_odds():
    game = GameData(game_id=1, sport="nba", date=date(2026, 3, 13),
        home_team_id=1, away_team_id=2, home_stats=_stats(), away_stats=_stats(), odds=[])
    strategy = SportSpecificStrategy("sport_specific", {"min_edge": 5.0})
    assert strategy.predict(game) == []


def test_mlb_weights_present_with_pitcher_dominance():
    """MLB weights must exist and pitcher must dominate (>= 0.4 weight)."""
    from backend.analysis.variants.sport_specific import SPORT_WEIGHTS
    assert "mlb" in SPORT_WEIGHTS
    assert SPORT_WEIGHTS["mlb"]["pitcher"] >= 0.40


def test_mlb_strategy_uses_pitcher_signal():
    """A strong home pitcher (skill 0.85) vs a weak away pitcher (skill 0.30)
    must shift home win probability noticeably even when team stats are equal."""
    from backend.analysis.variants.sport_specific import SportSpecificStrategy
    from backend.data_types import GameData, TeamStats, OddsSnapshot
    from datetime import date as _date

    def _ts(pitcher: float | None) -> TeamStats:
        return TeamStats(
            point_diff=0.0, home_record=(10, 10), away_record=(10, 10),
            last_n_record=(5, 5), offensive_rating=100.0, defensive_rating=100.0,
            pace=100.0, strength_of_schedule=0.5, elo_rating=1500, rest_days=1,
            pitcher_skill_score=pitcher,
        )

    odds = OddsSnapshot(bookmaker="dk", moneyline_home=-110, moneyline_away=-110,
                        spread_home=-1.5, spread_away=1.5, over_under=8.5)
    game_strong_home = GameData(
        game_id=1, sport="mlb", date=_date(2026, 4, 29),
        home_team_id=1, away_team_id=2,
        home_stats=_ts(0.85), away_stats=_ts(0.30), odds=[odds],
    )
    game_strong_away = GameData(
        game_id=2, sport="mlb", date=_date(2026, 4, 29),
        home_team_id=1, away_team_id=2,
        home_stats=_ts(0.30), away_stats=_ts(0.85), odds=[odds],
    )

    strat = SportSpecificStrategy(name="mlb_test", config={"min_edge": 0.0})
    p_home = strat._model_probability(game_strong_home)
    p_home_when_away_is_better = strat._model_probability(game_strong_away)
    assert p_home > 0.55, f"Strong home pitcher should push p_home above 0.55, got {p_home}"
    assert p_home_when_away_is_better < 0.45
    assert (p_home - p_home_when_away_is_better) >= 0.15


def test_mlb_strategy_handles_missing_pitcher_gracefully():
    """When pitchers aren't announced yet, must not crash and should produce
    a probability near 0.5 if all other signals are even."""
    from backend.analysis.variants.sport_specific import SportSpecificStrategy
    from backend.data_types import GameData, TeamStats, OddsSnapshot
    from datetime import date as _date

    ts = TeamStats(
        point_diff=0.0, home_record=(10, 10), away_record=(10, 10),
        last_n_record=(5, 5), offensive_rating=100.0, defensive_rating=100.0,
        pace=100.0, strength_of_schedule=0.5, elo_rating=1500, rest_days=1,
        pitcher_skill_score=None,
    )
    odds = OddsSnapshot(bookmaker="dk", moneyline_home=-110, moneyline_away=-110,
                        spread_home=-1.5, spread_away=1.5, over_under=8.5)
    game = GameData(
        game_id=1, sport="mlb", date=_date(2026, 4, 29),
        home_team_id=1, away_team_id=2,
        home_stats=ts, away_stats=ts, odds=[odds],
    )
    strat = SportSpecificStrategy(name="mlb_test", config={"min_edge": 0.0})
    p = strat._model_probability(game)
    assert 0.45 <= p <= 0.55  # neutral fallback

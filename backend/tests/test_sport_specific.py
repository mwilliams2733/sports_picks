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


def test_sport_weights_exist_for_all_sports():
    assert set(SPORT_WEIGHTS.keys()) == {"nba", "nfl", "ncaab", "ncaaf"}


def test_no_picks_without_odds():
    game = GameData(game_id=1, sport="nba", date=date(2026, 3, 13),
        home_team_id=1, away_team_id=2, home_stats=_stats(), away_stats=_stats(), odds=[])
    strategy = SportSpecificStrategy("sport_specific", {"min_edge": 5.0})
    assert strategy.predict(game) == []

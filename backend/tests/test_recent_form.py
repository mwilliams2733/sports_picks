from datetime import date
from backend.analysis.variants.recent_form import RecentFormStrategy
from backend.data_types import GameData, TeamStats, OddsSnapshot


def _stats(**kw):
    defaults = dict(point_diff=0.0, home_record=(0, 0), away_record=(0, 0),
        last_n_record=(0, 0), offensive_rating=100.0, defensive_rating=100.0,
        pace=100.0, strength_of_schedule=0.5, elo_rating=1500.0, rest_days=2)
    defaults.update(kw)
    return TeamStats(**defaults)


def test_recent_form_favors_hot_team():
    game = GameData(game_id=1, sport="nba", date=date(2026, 3, 13),
        home_team_id=1, away_team_id=2,
        home_stats=_stats(last_n_record=(5, 0), home_record=(20, 5)),
        away_stats=_stats(last_n_record=(1, 4), away_record=(8, 18)),
        odds=[OddsSnapshot(bookmaker="dk", moneyline_home=100, moneyline_away=100,
            spread_home=-1.0, spread_away=1.0, over_under=210.0)])
    strategy = RecentFormStrategy("recent_form", {"min_edge": 3.0})
    picks = strategy.predict(game)
    assert len(picks) >= 1
    assert picks[0].pick_value == "HOME ML"


def test_recent_form_no_picks_even_matchup():
    game = GameData(game_id=1, sport="nba", date=date(2026, 3, 13),
        home_team_id=1, away_team_id=2,
        home_stats=_stats(last_n_record=(3, 2), home_record=(10, 10)),
        away_stats=_stats(last_n_record=(3, 2), away_record=(10, 10)),
        odds=[OddsSnapshot(bookmaker="dk", moneyline_home=-110, moneyline_away=-110,
            spread_home=-1.0, spread_away=1.0, over_under=210.0)])
    strategy = RecentFormStrategy("recent_form", {"min_edge": 5.0})
    picks = strategy.predict(game)
    assert picks == []


def test_recent_form_no_odds():
    game = GameData(game_id=1, sport="nba", date=date(2026, 3, 13),
        home_team_id=1, away_team_id=2, home_stats=_stats(), away_stats=_stats(), odds=[])
    strategy = RecentFormStrategy("recent_form", {"min_edge": 5.0})
    assert strategy.predict(game) == []

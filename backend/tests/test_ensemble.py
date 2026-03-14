from datetime import date
from backend.analysis.variants.ensemble import EnsembleStrategy
from backend.backtesting.backtester import Backtester
from backend.data_types import GameData, TeamStats, OddsSnapshot, Pick

def _stats(**kw):
    defaults = dict(point_diff=0.0, home_record=(0, 0), away_record=(0, 0),
        last_n_record=(0, 0), offensive_rating=100.0, defensive_rating=100.0,
        pace=100.0, strength_of_schedule=0.5, elo_rating=1500.0, rest_days=2)
    defaults.update(kw)
    return TeamStats(**defaults)

def test_ensemble_returns_picks_when_edge_exists():
    game = GameData(game_id=1, sport="nba", date=date(2026, 3, 13),
        home_team_id=1, away_team_id=2,
        home_stats=_stats(point_diff=8.0, elo_rating=1600, offensive_rating=115.0, defensive_rating=105.0),
        away_stats=_stats(point_diff=-3.0, elo_rating=1400, offensive_rating=105.0, defensive_rating=112.0),
        odds=[OddsSnapshot(bookmaker="dk", moneyline_home=-120, moneyline_away=100,
            spread_home=-3.5, spread_away=3.5, over_under=215.0)])
    strategy = EnsembleStrategy("ensemble", {"min_edge": 5.0, "k_factor": 20, "lookback": 10})
    picks = strategy.predict(game)
    assert isinstance(picks, list)
    for pick in picks:
        assert pick.edge_pct >= 0

def test_ensemble_no_picks_without_odds():
    game = GameData(game_id=1, sport="nba", date=date(2026, 3, 13),
        home_team_id=1, away_team_id=2, home_stats=_stats(), away_stats=_stats(), odds=[])
    strategy = EnsembleStrategy("ensemble", {"min_edge": 5.0, "k_factor": 20, "lookback": 10})
    picks = strategy.predict(game)
    assert picks == []

def test_spread_pick_generated_when_model_disagrees():
    """Spread pick generated when model point diff disagrees with bookmaker spread."""
    # Home point_diff=10, away point_diff=-5 => predicted_diff = 15
    # Spread is -3.5, so model says home wins by 15 but spread says 3.5
    # Edge = |15 - 3.5| = 11.5, well above min_edge=5
    game = GameData(game_id=1, sport="nba", date=date(2026, 3, 13),
        home_team_id=1, away_team_id=2,
        home_stats=_stats(point_diff=10.0, elo_rating=1600, offensive_rating=110.0, defensive_rating=105.0),
        away_stats=_stats(point_diff=-5.0, elo_rating=1400, offensive_rating=105.0, defensive_rating=110.0),
        odds=[OddsSnapshot(bookmaker="dk", moneyline_home=-200, moneyline_away=170,
            spread_home=-3.5, spread_away=3.5, over_under=215.0)])
    strategy = EnsembleStrategy("ensemble", {"min_edge": 5.0})
    picks = strategy.predict(game)
    spread_picks = [p for p in picks if p.pick_type == "spread"]
    assert len(spread_picks) == 1
    assert "HOME" in spread_picks[0].pick_value
    assert "-3.5" in spread_picks[0].pick_value
    assert spread_picks[0].odds_at_pick == -110

def test_spread_pick_away_side():
    """Spread pick for away team when model disagrees in away's favor."""
    # Home point_diff=-5, away point_diff=10 => predicted_diff = -15
    # Spread is -3.5 for home, so model says away wins but spread favors home
    # Edge = |-15 - 3.5| = 18.5
    game = GameData(game_id=1, sport="nba", date=date(2026, 3, 13),
        home_team_id=1, away_team_id=2,
        home_stats=_stats(point_diff=-5.0, elo_rating=1400, offensive_rating=105.0, defensive_rating=110.0),
        away_stats=_stats(point_diff=10.0, elo_rating=1600, offensive_rating=110.0, defensive_rating=105.0),
        odds=[OddsSnapshot(bookmaker="dk", moneyline_home=-200, moneyline_away=170,
            spread_home=-3.5, spread_away=3.5, over_under=215.0)])
    strategy = EnsembleStrategy("ensemble", {"min_edge": 5.0})
    picks = strategy.predict(game)
    spread_picks = [p for p in picks if p.pick_type == "spread"]
    assert len(spread_picks) == 1
    assert "AWAY" in spread_picks[0].pick_value
    assert "+3.5" in spread_picks[0].pick_value

def test_over_under_pick_generated():
    """O/U pick generated when predicted total differs from line."""
    # pace=110 each, off_rtg=115 each
    # predicted_total = (110+110)/2 * (115+115)/200 = 110 * 1.15 = 126.5
    # Line is 215, so predicted < line => Under pick, edge = 215-126.5=88.5
    # Actually let's use more realistic values
    # pace=100, off_rtg=110, def_rtg=100 for both
    # predicted = (100+100)/2 * (110+110)/200 = 100 * 1.1 = 110
    # Line = 215, edge = 105 (huge, but shows the logic)
    # Let's make it tighter: pace=105 each, off_rtg=108 each
    # predicted = 105 * 216/200 = 105 * 1.08 = 113.4, line=215 => Under
    # Actually let's use values that produce realistic totals
    # For NBA: pace~100, off_rtg~110 => total = 100 * 220/200 = 110 per team? No.
    # predicted_total = avg_pace * (home_off + away_off) / 200
    # For total ~220: pace=100, combined_off=440 => 100*440/200=220
    # off_rtg=220 each => predicted=100*440/200=220
    # That's unrealistic. Let's just use the formula and set line far from it.
    # pace=100, off_rtg=110 each => predicted = 100*(110+110)/200 = 110
    # line=120 => edge=10, Under pick
    game = GameData(game_id=1, sport="nba", date=date(2026, 3, 13),
        home_team_id=1, away_team_id=2,
        home_stats=_stats(offensive_rating=110.0, defensive_rating=100.0, pace=100.0),
        away_stats=_stats(offensive_rating=110.0, defensive_rating=100.0, pace=100.0),
        odds=[OddsSnapshot(bookmaker="dk", moneyline_home=-110, moneyline_away=-110,
            spread_home=-1.0, spread_away=1.0, over_under=120.0)])
    strategy = EnsembleStrategy("ensemble", {"min_edge": 5.0})
    picks = strategy.predict(game)
    ou_picks = [p for p in picks if p.pick_type == "over_under"]
    assert len(ou_picks) == 1
    assert "Under" in ou_picks[0].pick_value
    assert "120" in ou_picks[0].pick_value
    assert ou_picks[0].odds_at_pick == -110

def test_over_pick_generated():
    """Over pick generated when predicted total is above line."""
    # pace=100, off_rtg=110 each => predicted = 100*220/200 = 110
    # line=100 => edge=10, Over pick
    game = GameData(game_id=1, sport="nba", date=date(2026, 3, 13),
        home_team_id=1, away_team_id=2,
        home_stats=_stats(offensive_rating=110.0, defensive_rating=100.0, pace=100.0),
        away_stats=_stats(offensive_rating=110.0, defensive_rating=100.0, pace=100.0),
        odds=[OddsSnapshot(bookmaker="dk", moneyline_home=-110, moneyline_away=-110,
            spread_home=-1.0, spread_away=1.0, over_under=100.0)])
    strategy = EnsembleStrategy("ensemble", {"min_edge": 5.0})
    picks = strategy.predict(game)
    ou_picks = [p for p in picks if p.pick_type == "over_under"]
    assert len(ou_picks) == 1
    assert "Over" in ou_picks[0].pick_value
    assert "100" in ou_picks[0].pick_value

def test_all_three_pick_types_together():
    """All three pick types can appear in a single prediction."""
    # Big edge on everything: strong home team
    # point_diff: home=12, away=-8 => predicted_diff=20, spread=-3.5 => edge=16.5
    # pace=100, home_off=115, away_off=105 => predicted_total=100*220/200=110, line=125 => Under edge=15
    game = GameData(game_id=1, sport="nba", date=date(2026, 3, 13),
        home_team_id=1, away_team_id=2,
        home_stats=_stats(point_diff=12.0, elo_rating=1700, offensive_rating=115.0, defensive_rating=100.0, pace=100.0),
        away_stats=_stats(point_diff=-8.0, elo_rating=1300, offensive_rating=105.0, defensive_rating=115.0, pace=100.0),
        odds=[OddsSnapshot(bookmaker="dk", moneyline_home=-120, moneyline_away=100,
            spread_home=-3.5, spread_away=3.5, over_under=125.0)])
    strategy = EnsembleStrategy("ensemble", {"min_edge": 5.0})
    picks = strategy.predict(game)
    pick_types = {p.pick_type for p in picks}
    assert "moneyline" in pick_types
    assert "spread" in pick_types
    assert "over_under" in pick_types

def test_average_odds_includes_spread_and_ou():
    """_average_odds returns spread and O/U values."""
    game = GameData(game_id=1, sport="nba", date=date(2026, 3, 13),
        home_team_id=1, away_team_id=2,
        home_stats=_stats(), away_stats=_stats(),
        odds=[
            OddsSnapshot(bookmaker="dk", moneyline_home=-110, moneyline_away=-110,
                spread_home=-3.5, spread_away=3.5, over_under=215.0),
            OddsSnapshot(bookmaker="fd", moneyline_home=-120, moneyline_away=100,
                spread_home=-4.5, spread_away=4.5, over_under=217.0),
        ])
    strategy = EnsembleStrategy("ensemble", {})
    avg = strategy._average_odds(game)
    assert avg["spread_home"] == -4.0
    assert avg["spread_away"] == 4.0
    assert avg["over_under"] == 216.0

def test_backtester_grades_spread_correctly():
    """Backtester grades spread picks using the grader."""
    from backend.analysis.strategy import Strategy

    class SpreadStrategy(Strategy):
        def predict(self, game):
            return [Pick(game_id=game.game_id, pick_type="spread", pick_value="HOME -3.5",
                confidence=3, edge_pct=6.0, model_probability=0.55, implied_probability=0.5,
                odds_at_pick=-110)]

    stats = _stats()
    game = GameData(game_id=1, sport="nba", date=date(2026, 1, 1),
        home_team_id=1, away_team_id=2, home_stats=stats, away_stats=stats,
        odds=[OddsSnapshot("dk", -150, 130, -3.5, 3.5, 215.0)])
    strategy = SpreadStrategy("spread_mock", {})
    bt = Backtester(strategy)
    # Home wins by 10, covers -3.5
    results = bt.run([(game, 110, 100)])
    assert results["wins"] == 1
    assert results["losses"] == 0
    # Home wins by 2, does NOT cover -3.5
    results = bt.run([(game, 102, 100)])
    assert results["wins"] == 0
    assert results["losses"] == 1

def test_backtester_grades_over_under_correctly():
    """Backtester grades O/U picks using the grader."""
    from backend.analysis.strategy import Strategy

    class OUStrategy(Strategy):
        def predict(self, game):
            return [Pick(game_id=game.game_id, pick_type="over_under", pick_value="Over 215.5",
                confidence=3, edge_pct=6.0, model_probability=220.0, implied_probability=215.5,
                odds_at_pick=-110)]

    stats = _stats()
    game = GameData(game_id=1, sport="nba", date=date(2026, 1, 1),
        home_team_id=1, away_team_id=2, home_stats=stats, away_stats=stats,
        odds=[OddsSnapshot("dk", -150, 130, -3.5, 3.5, 215.0)])
    strategy = OUStrategy("ou_mock", {})
    bt = Backtester(strategy)
    # Total = 220 > 215.5 => Over wins
    results = bt.run([(game, 115, 105)])
    assert results["wins"] == 1
    # Total = 210 < 215.5 => Over loses
    results = bt.run([(game, 110, 100)])
    assert results["wins"] == 0
    assert results["losses"] == 1

def test_backtester_handles_push():
    """Backtester handles push results correctly."""
    from backend.analysis.strategy import Strategy

    class PushStrategy(Strategy):
        def predict(self, game):
            return [Pick(game_id=game.game_id, pick_type="over_under", pick_value="Over 215",
                confidence=3, edge_pct=6.0, model_probability=220.0, implied_probability=215.0,
                odds_at_pick=-110)]

    stats = _stats()
    game = GameData(game_id=1, sport="nba", date=date(2026, 1, 1),
        home_team_id=1, away_team_id=2, home_stats=stats, away_stats=stats,
        odds=[OddsSnapshot("dk", -150, 130, -3.5, 3.5, 215.0)])
    strategy = PushStrategy("push_mock", {})
    bt = Backtester(strategy)
    # Total = 215 == 215 => push
    results = bt.run([(game, 110, 105)])
    assert results["pushes"] == 1
    assert results["wins"] == 0
    assert results["losses"] == 0

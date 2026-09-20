from datetime import date
import backend.analysis.variants.ensemble as ens
from backend.analysis.variants.ensemble import EnsembleStrategy
from backend.backtesting.backtester import Backtester
from backend.data_types import GameData, TeamStats, OddsSnapshot, Pick
import pytest

# max_edge is lifted here: these fixtures use deliberately large
# synthetic edges to exercise pick mechanics, and the ceiling is
# owned by test_max_edge_ceiling.py.


@pytest.fixture(autouse=True)
def _totals_enabled(monkeypatch):
    """Treat nba as a validated totals sport for this module.

    TOTALS_VALIDATED_SPORTS is empty in production because the model
    does not beat the market line anywhere on a sample worth the name.
    These tests are about the strategy's mechanics, not that decision;
    test_totals_model.py owns the gate itself.
    """
    monkeypatch.setattr(ens, "TOTALS_VALIDATED_SPORTS",
                        frozenset({"nba", "ncaab"}))

def _stats(**kw):
    # points_for/points_against default to 105 each, so two default teams
    # predict a 210-point total -- the same value the old rating-based
    # formula produced for these fixtures, which keeps the totals tests below
    # asserting what they always asserted. Pass None to exercise the guard
    # that refuses a pick when a team has no scoring history.
    # last_n_record sums to the games the rolling point_diff was averaged
    # over, which is what `shrink_margin` discounts by. A full window is the
    # neutral default: a fixture asserting a team averages +10 a game is
    # describing a team that has played, and n=0 would shrink every margin in
    # this file to zero and quietly stop the spread tests testing anything.
    # Tests about a team with little history pass last_n_record explicitly.
    defaults = dict(point_diff=0.0, home_record=(0, 0), away_record=(0, 0),
        last_n_record=(5, 5), offensive_rating=100.0, defensive_rating=100.0,
        pace=100.0, strength_of_schedule=0.5, elo_rating=1500.0, rest_days=2,
        points_for=105.0, points_against=105.0)
    defaults.update(kw)
    return TeamStats(**defaults)

def test_ensemble_returns_picks_when_edge_exists():
    game = GameData(game_id=1, sport="nba", date=date(2026, 3, 13),
        home_team_id=1, away_team_id=2,
        home_stats=_stats(point_diff=8.0, elo_rating=1600, offensive_rating=115.0, defensive_rating=105.0),
        away_stats=_stats(point_diff=-3.0, elo_rating=1400, offensive_rating=105.0, defensive_rating=112.0),
        odds=[OddsSnapshot(bookmaker="dk", moneyline_home=-120, moneyline_away=100,
            spread_home=-3.5, spread_away=3.5, over_under=215.0)])
    strategy = EnsembleStrategy("ensemble", {"min_edge": 5.0, "max_edge": 100.0, "k_factor": 20, "lookback": 10})
    picks = strategy.predict(game)
    assert isinstance(picks, list)
    for pick in picks:
        assert pick.edge_pct >= 0

def test_ensemble_no_picks_without_odds():
    game = GameData(game_id=1, sport="nba", date=date(2026, 3, 13),
        home_team_id=1, away_team_id=2, home_stats=_stats(), away_stats=_stats(), odds=[])
    strategy = EnsembleStrategy("ensemble", {"min_edge": 5.0, "max_edge": 100.0, "k_factor": 20, "lookback": 10})
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
    strategy = EnsembleStrategy("ensemble", {"min_edge": 5.0, "max_edge": 100.0})
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
    strategy = EnsembleStrategy("ensemble", {"min_edge": 5.0, "max_edge": 100.0})
    picks = strategy.predict(game)
    spread_picks = [p for p in picks if p.pick_type == "spread"]
    assert len(spread_picks) == 1
    assert "AWAY" in spread_picks[0].pick_value
    assert "+3.5" in spread_picks[0].pick_value

def test_over_under_pick_generated():
    """O/U pick generated when predicted total differs from line."""
    # Matchup average: each side's expected score is the mean of its own
    # scoring rate and the opponent's concession rate.
    # home_pts = (105 + 105) / 2 = 105; away_pts = 105
    # predicted_total = 210, line=225 => Under pick
    game = GameData(game_id=1, sport="nba", date=date(2026, 3, 13),
        home_team_id=1, away_team_id=2,
        home_stats=_stats(offensive_rating=110.0, defensive_rating=100.0, pace=100.0),
        away_stats=_stats(offensive_rating=110.0, defensive_rating=100.0, pace=100.0),
        odds=[OddsSnapshot(bookmaker="dk", moneyline_home=-110, moneyline_away=-110,
            spread_home=-1.0, spread_away=1.0, over_under=225.0)])
    strategy = EnsembleStrategy("ensemble", {"min_edge": 5.0, "max_edge": 100.0})
    picks = strategy.predict(game)
    ou_picks = [p for p in picks if p.pick_type == "over_under"]
    assert len(ou_picks) == 1
    assert "Under" in ou_picks[0].pick_value
    assert "225" in ou_picks[0].pick_value
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
    strategy = EnsembleStrategy("ensemble", {"min_edge": 5.0, "max_edge": 100.0})
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
    strategy = EnsembleStrategy("ensemble", {"min_edge": 5.0, "max_edge": 100.0})
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
    strategy = EnsembleStrategy("ensemble", {"max_edge": 100.0})
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

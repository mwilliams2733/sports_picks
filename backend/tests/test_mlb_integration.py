"""End-to-end MLB pipeline smoke test.

Verifies that with a real-shaped game + odds + pitcher_scores, the strategy
produces a moneyline pick when the home pitcher dominates and the line offers
positive edge. No HTTP — purely SQLite + in-process strategy.
"""
from datetime import date as _date, datetime, timezone

import pytest

from backend.database import get_engine, get_session
from backend.models import (
    Base, Team, Game, Odds, EloRating, StrategyModel, PickModel,
)
from backend.pipeline.pick_generator import generate_and_store_picks


def test_mlb_strategy_generates_pick_when_pitcher_advantage_creates_edge():
    engine = get_engine(":memory:")
    Base.metadata.create_all(engine)
    session = get_session(engine)

    home = Team(id=1, name="Boston Red Sox", abbreviation="BOS", sport="mlb")
    away = Team(id=2, name="New York Yankees", abbreviation="NYY", sport="mlb")
    session.add_all([home, away])
    session.flush()

    game = Game(id=10, sport="mlb", season="2026", date=_date(2026, 4, 29),
                home_team_id=1, away_team_id=2, status="scheduled",
                start_time=datetime(2026, 4, 29, 23, 5, tzinfo=timezone.utc))
    session.add(game)
    session.flush()

    # Equal team ELO — pitcher advantage should be the deciding signal.
    session.add_all([
        EloRating(team_id=1, sport="mlb", rating=1500),
        EloRating(team_id=2, sport="mlb", rating=1500),
    ])
    # Slight underdog at home: +105 implies ~48.8%, but pitcher advantage will push above 50%.
    session.add(Odds(
        game_id=10, bookmaker="dk",
        moneyline_home=+105, moneyline_away=-115,
        spread_home=-1.5, spread_away=1.5, over_under=8.5,
        timestamp=datetime(2026, 4, 29, 18, 0, tzinfo=timezone.utc),
    ))
    strat = StrategyModel(id=1, name="sport_specific",
                           config_json='{"min_edge": 3.0}', is_active=True)
    session.add(strat)
    session.commit()

    # Strong home pitcher (skill 0.85), weak away pitcher (0.30) — pitcher branch
    # should push home win probability well above the +105 implied prob (~48.8%).
    pitcher_scores = {10: {"home": 0.85, "away": 0.30}}

    # Fixed historical date, so start_time is in the past. skip_started exists
    # for exactly this: the subject is the strategy's output, not whether the
    # game is still bettable.
    n = generate_and_store_picks(session, strategy_id=1, target_date=_date(2026, 4, 29),
                                  pitcher_scores=pitcher_scores,
                                  skip_started=False)
    assert n >= 1, "Strong home pitcher + plus money should produce at least one pick"

    picks = session.query(PickModel).filter(PickModel.game_id == 10).all()
    assert any(p.pick_type == "moneyline" and "HOME" in p.pick_value for p in picks)
    session.close()


def test_mlb_strategy_skips_when_no_edge():
    """Equal pitchers + market-priced moneyline -> no pick should be generated."""
    engine = get_engine(":memory:")
    Base.metadata.create_all(engine)
    session = get_session(engine)
    home = Team(id=1, name="BOS", abbreviation="BOS", sport="mlb")
    away = Team(id=2, name="NYY", abbreviation="NYY", sport="mlb")
    session.add_all([home, away])
    session.flush()
    game = Game(id=11, sport="mlb", season="2026", date=_date(2026, 4, 29),
                home_team_id=1, away_team_id=2, status="scheduled")
    session.add(game)
    session.flush()
    session.add_all([
        EloRating(team_id=1, sport="mlb", rating=1500),
        EloRating(team_id=2, sport="mlb", rating=1500),
        Odds(game_id=11, bookmaker="dk",
             moneyline_home=-110, moneyline_away=-110,
             spread_home=-1.5, spread_away=1.5, over_under=8.5,
             timestamp=datetime(2026, 4, 29, 18, 0, tzinfo=timezone.utc)),
        StrategyModel(id=1, name="sport_specific",
                      config_json='{"min_edge": 3.0}', is_active=True),
    ])
    session.commit()

    pitcher_scores = {11: {"home": 0.50, "away": 0.50}}  # equal
    # Fixed historical date, so start_time is in the past. skip_started exists
    # for exactly this: the subject is the strategy's output, not whether the
    # game is still bettable.
    n = generate_and_store_picks(session, strategy_id=1, target_date=_date(2026, 4, 29),
                                  pitcher_scores=pitcher_scores,
                                  skip_started=False)
    assert n == 0
    session.close()

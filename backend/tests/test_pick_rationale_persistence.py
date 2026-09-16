import json
from datetime import date

from backend.database import get_engine, get_session
from backend.models import Base, Team, Game, StrategyModel, PickModel, TeamStat, EloRating, Odds
from backend.pipeline.pick_generator import generate_and_store_picks
from datetime import datetime, timezone


def _seed(session, game_date):
    home = Team(id=1, name="Chiefs", abbreviation="KC", sport="nfl")
    away = Team(id=2, name="Bills", abbreviation="BUF", sport="nfl")
    session.add_all([home, away])
    session.flush()
    game = Game(id=1, sport="nfl", season="2026", date=game_date,
                home_team_id=1, away_team_id=2, status="scheduled")
    session.add(game)
    session.flush()
    session.add_all([
        TeamStat(team_id=1, game_id=1, stat_type="point_diff", value=6.0),
        TeamStat(team_id=2, game_id=1, stat_type="point_diff", value=0.0),
        TeamStat(team_id=1, game_id=1, stat_type="offensive_rating", value=110.0),
        TeamStat(team_id=1, game_id=1, stat_type="defensive_rating", value=100.0),
        TeamStat(team_id=2, game_id=1, stat_type="offensive_rating", value=100.0),
        TeamStat(team_id=2, game_id=1, stat_type="defensive_rating", value=105.0),
        EloRating(team_id=1, sport="nfl", rating=1650.0),
        EloRating(team_id=2, sport="nfl", rating=1500.0),
        Odds(game_id=1, bookmaker="dk", moneyline_home=-110, moneyline_away=-110,
             spread_home=0.0, spread_away=0.0, over_under=45.0,
             timestamp=datetime(2026, 3, 1, 18, 0, tzinfo=timezone.utc)),
        StrategyModel(id=1, name="value_only", config_json='{"min_edge": 0.1}', is_active=True),
    ])
    session.commit()


def test_pick_persists_rationale_and_model_prob():
    engine = get_engine(":memory:")
    Base.metadata.create_all(engine)
    session = get_session(engine)
    d = date(2026, 3, 1)
    _seed(session, d)

    assert generate_and_store_picks(session, strategy_id=1, target_date=d) >= 1

    pick = session.query(PickModel).first()
    assert pick is not None
    assert pick.model_prob is not None, "model_prob must be persisted"
    assert 0.0 < pick.model_prob < 1.0

    assert pick.rationale_json is not None, "rationale_json must be persisted"
    factors = json.loads(pick.rationale_json)
    assert isinstance(factors, list) and len(factors) >= 1, (
        "strategies must emit at least one factor for a moneyline pick"
    )
    for f in factors:
        assert set(f) == {"code", "side", "strength"}
        assert f["side"] in ("home", "away", "over", "under")
        assert f["strength"] in ("slight", "moderate", "strong")

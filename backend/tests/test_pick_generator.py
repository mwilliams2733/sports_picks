from datetime import date, datetime
from backend.pipeline.pick_generator import generate_and_store_picks
from backend.models import Base, Game, Team, StrategyModel, PickModel

def test_generate_picks_stores_to_db(db_engine, db_session):
    Base.metadata.create_all(db_engine)
    t1 = Team(id=1, name="Boston Celtics", abbreviation="BOS", sport="nba")
    t2 = Team(id=2, name="LA Lakers", abbreviation="LAL", sport="nba")
    g = Game(id=1, sport="nba", season="2025-26", date=date.today(),
             home_team_id=1, away_team_id=2, status="scheduled")
    s = StrategyModel(id=1, name="ensemble", config_json='{"min_edge": 0.1, "k_factor": 20, "lookback": 10}',
                      is_active=True)
    db_session.add_all([t1, t2, g, s])
    db_session.commit()
    count = generate_and_store_picks(db_session, strategy_id=1)
    assert isinstance(count, int)
    stored = db_session.query(PickModel).all()
    assert isinstance(stored, list)

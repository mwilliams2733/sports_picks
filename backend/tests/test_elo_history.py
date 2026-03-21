from backend.models import Base, Team, Game, EloHistory
from backend.database import get_engine, get_session
from datetime import date

def test_elo_history_stores_per_game_snapshot():
    engine = get_engine(":memory:")
    Base.metadata.create_all(engine)
    session = get_session(engine)

    t1 = Team(name="A", abbreviation="A", sport="nba")
    t2 = Team(name="B", abbreviation="B", sport="nba")
    session.add_all([t1, t2])
    session.flush()

    g = Game(sport="nba", season="2025-26", date=date(2026, 1, 1),
             home_team_id=t1.id, away_team_id=t2.id, home_score=110, away_score=100, status="final")
    session.add(g)
    session.flush()

    session.add(EloHistory(team_id=t1.id, game_id=g.id, sport="nba", rating=1520.0))
    session.add(EloHistory(team_id=t2.id, game_id=g.id, sport="nba", rating=1480.0))
    session.commit()

    h = session.query(EloHistory).filter(EloHistory.game_id == g.id).all()
    assert len(h) == 2
    ratings = {eh.team_id: eh.rating for eh in h}
    assert ratings[t1.id] == 1520.0
    assert ratings[t2.id] == 1480.0
    session.close()

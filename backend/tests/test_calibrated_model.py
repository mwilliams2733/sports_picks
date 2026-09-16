import datetime as dt

from backend.database import get_engine, get_session
from backend.models import Base, Game, Team
from backend.analysis.calibrated_model import CalibratedModel, MIN_TRAINING_GAMES


def _seed_games(session, status: str, count: int = MIN_TRAINING_GAMES):
    for i in range(count):
        h = Team(name=f"H{i}", abbreviation="H", sport="nba")
        a = Team(name=f"A{i}", abbreviation="A", sport="nba")
        session.add_all([h, a])
        session.flush()
        session.add(
            Game(
                sport="nba",
                season="2026",
                date=dt.date(2026, 1, 1) + dt.timedelta(days=i % count),
                home_team_id=h.id,
                away_team_id=a.id,
                status=status,
                home_score=110 + i % 20,
                away_score=100 + i % 15,
            )
        )
    session.commit()


def test_train_from_db_uses_final_status():
    engine = get_engine(":memory:")
    Base.metadata.create_all(engine)
    session = get_session(engine)
    _seed_games(session, "final", count=300)

    model = CalibratedModel()
    model.train_from_db(session)

    assert model.trained is True


def test_train_from_db_ignores_scheduled():
    engine = get_engine(":memory:")
    Base.metadata.create_all(engine)
    session = get_session(engine)
    _seed_games(session, "scheduled", count=300)

    model = CalibratedModel()
    model.train_from_db(session)

    assert model.trained is False

import pytest
from sqlalchemy import text
from backend.database import get_engine, get_session

@pytest.fixture
def db_engine(tmp_path):
    db_path = tmp_path / "test.db"
    engine = get_engine(str(db_path))
    yield engine
    engine.dispose()

@pytest.fixture
def db_session(db_engine):
    session = get_session(db_engine)
    yield session
    session.close()

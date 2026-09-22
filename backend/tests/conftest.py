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


@pytest.fixture(autouse=True)
def _untrained_calibrated_model():
    """Stop the calibrated model leaking between tests.

    `EnsembleStrategy._calibrated` is a CLASS attribute, trained lazily on
    first use from whatever database `DB_PATH` names -- by default the
    relative `sports_picks.db`, which on a developer's machine is the real
    one sitting in the repo root. Whichever test touched the strategy first
    therefore decided what every later test was predicting with, and a suite
    run from the repo root read production data.

    Reset around every test so the fixture a test sets up is the only thing
    it is measuring.

    An UNTRAINED instance, not ``None``. ``None`` is the "never touched"
    sentinel `_legacy_calibrated_probability` retrains on, so resetting to
    it makes every test that predicts refit the model -- and where the live
    database IS present that is a LogisticRegression over ~1,800 games,
    about 1,200 times. It is fast in CI only because there is no database
    there to train from, which is the same machine-dependence this fixture
    exists to remove. An instance short-circuits the retrain and gives the
    deterministic fallback everywhere.
    """
    from backend.analysis.calibrated_model import CalibratedModel
    from backend.analysis.variants.ensemble import EnsembleStrategy

    previous = EnsembleStrategy._calibrated
    EnsembleStrategy._calibrated = CalibratedModel()
    yield
    EnsembleStrategy._calibrated = previous

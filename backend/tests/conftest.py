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
def _isolate_calibrated_model():
    """Stop `EnsembleStrategy` reaching for the live database.

    `_legacy_calibrated_probability` trains a `CalibratedModel` from
    `os.environ.get("DB_PATH", "sports_picks.db")` on first use, so a
    strategy's probabilities -- and therefore whether a fixture clears
    `min_edge` -- depended on whether an untracked 10 MB file happened to
    sit in the working directory. Four tests passed on a developer machine
    and failed in CI for 25+ commits because of it, while ci.yml claimed
    "tests are isolated from the live database".

    `_calibrated` is also a CLASS attribute, so whichever test trained it
    first silently fixed the probability source for every test after it in
    the same process. Outcomes depended on collection order.

    Pinning an UNTRAINED model gives every test the deterministic
    `_fallback_probability` path and no file access. A test that wants a
    trained model assigns one itself.
    """
    from backend.analysis.calibrated_model import CalibratedModel
    from backend.analysis.variants.ensemble import EnsembleStrategy

    previous = EnsembleStrategy._calibrated
    EnsembleStrategy._calibrated = CalibratedModel()
    yield
    EnsembleStrategy._calibrated = previous


@pytest.fixture
def model_claiming():
    """Pin what the model believes, for tests about what happens next.

    A test about an edge CEILING needs a known edge. Deriving one from Elo
    through `_fallback_probability` does not work -- that path saturates
    around 13 percentage points, below `DEFAULT_MAX_EDGE`, so the ceiling
    can never fire. Those tests only ever passed because a trained model
    from the live database produced bigger numbers, which is precisely the
    hidden dependency `_isolate_calibrated_model` removes.

        def test_x(model_claiming):
            model_claiming(0.95)
            ...

    Restored by `_isolate_calibrated_model`, which is autouse.
    """
    from backend.analysis.variants.ensemble import EnsembleStrategy

    def _install(probability: float):
        class _Fixed:
            trained = True

            def predict_home_win_prob(self, game):
                return probability

        EnsembleStrategy._calibrated = _Fixed()

    return _install

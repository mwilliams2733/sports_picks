import socket

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


class RealNetworkAccess(RuntimeError):
    """A test tried to open a socket to the outside world."""


@pytest.fixture(autouse=True)
def no_real_network(request, monkeypatch):
    """Fail a real outbound connection instead of making one.

    Several tests drive `/pipeline/run` end to end without mocking anything,
    and were reaching ESPN and The Odds API for real. That stayed invisible
    while every one of those calls failed fast -- notably
    `EspnStatsSource.fetch_season_averages`, which raised AttributeError
    before it could reach the network at all. Once that method was fixed the
    same tests began crawling a live roster and every athlete's statistics,
    turning a 3-second file into one that runs for minutes or hangs.

    The block sits at the socket layer on purpose: pytest_httpx intercepts
    inside httpx, well above this, so every mocked test is unaffected, and
    the collectors already catch per-sport exceptions and log them -- so the
    pipeline tests still get `status: completed`, just from zero rows rather
    than from whatever today's live scoreboard happened to hold.

    Mark a test `@pytest.mark.allow_network` if it genuinely needs the wire.
    """
    if request.node.get_closest_marker("allow_network"):
        return

    # Loopback is exempt. The guard is about OUTBOUND traffic, and on
    # Windows asyncio's ProactorEventLoop builds its self-pipe by connecting
    # a socket to 127.0.0.1 -- blocking that does not stop a test reaching
    # ESPN, it stops the event loop existing, and every async test dies with
    # "'ProactorEventLoop' object has no attribute '_ssock'". 131 of them
    # did. CI runs ubuntu, where the selector loop needs no such socket, so
    # this could only ever fail on a developer machine.
    loopback = {"127.0.0.1", "::1", "localhost", "0.0.0.0", ""}

    def _is_local(address) -> bool:
        host = address[0] if isinstance(address, (tuple, list)) and address else address
        return isinstance(host, str) and host in loopback

    def _refuse():
        raise RealNetworkAccess(
            "This test tried to reach the network. Mock it with httpx_mock, "
            "or mark it @pytest.mark.allow_network if that is really intended."
        )

    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex
    real_create_connection = socket.create_connection

    def guarded_connect(self, address, *a, **kw):
        return real_connect(self, address, *a, **kw) if _is_local(address) else _refuse()

    def guarded_connect_ex(self, address, *a, **kw):
        return real_connect_ex(self, address, *a, **kw) if _is_local(address) else _refuse()

    def guarded_create_connection(address, *a, **kw):
        return (real_create_connection(address, *a, **kw) if _is_local(address)
                else _refuse())

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", guarded_connect_ex)
    monkeypatch.setattr(socket, "create_connection", guarded_create_connection)

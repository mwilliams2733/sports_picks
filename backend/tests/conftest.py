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

    def blocked(*args, **kwargs):
        raise RealNetworkAccess(
            "This test tried to reach the network. Mock it with httpx_mock, "
            "or mark it @pytest.mark.allow_network if that is really intended."
        )

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)

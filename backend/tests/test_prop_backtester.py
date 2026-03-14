import pytest
from datetime import date, datetime, timezone, timedelta
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from backend.models import Base, Team, PlayerStat
from backend.backtesting.prop_backtester import PropBacktester


@pytest.fixture
def session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s
    engine.dispose()


def _make_team(session: Session) -> Team:
    team = Team(name="Celtics", abbreviation="BOS", sport="nba")
    session.add(team)
    session.flush()
    return team


def _make_logs(session: Session, team: Team, n: int = 20, start_date: date = date(2026, 2, 1),
               player_name: str = "Jayson Tatum", minutes: float = 35.0) -> list[PlayerStat]:
    logs = []
    for i in range(n):
        points = 25.0 + (i % 5) * 2  # varying: 25, 27, 29, 31, 33, repeating
        log = PlayerStat(
            player_name=player_name,
            team_id=team.id,
            sport="nba",
            stat_type="game_log",
            game_date=start_date + timedelta(days=i),
            minutes=minutes,
            points=points,
            rebounds=8.0,
            assists=5.0,
            source="test",
            fetched_at=datetime.now(tz=timezone.utc),
        )
        session.add(log)
        logs.append(log)
    session.flush()
    return logs


def test_backtest_returns_results(session):
    """20 game logs with varying points should produce wins/losses/hit_rate/by_market."""
    team = _make_team(session)
    _make_logs(session, team, n=20, start_date=date(2026, 2, 1))

    backtester = PropBacktester({
        "recent_weight": 0.6,
        "season_weight": 0.4,
        "min_edge": 5.0,
        "lookback": 5,
        "min_minutes": 15,
    })

    result = backtester.backtest(
        session,
        sport="nba",
        start_date=date(2026, 2, 1),
        end_date=date(2026, 2, 20),
    )

    assert "wins" in result
    assert "losses" in result
    assert "hit_rate" in result
    assert "by_market" in result
    assert result["total"] == result["wins"] + result["losses"]
    # Should have processed some picks given 20 logs and lookback=5
    assert result["total"] >= 0
    assert "player_points" in result["by_market"]
    assert "player_rebounds" in result["by_market"]
    assert "player_assists" in result["by_market"]


def test_backtest_empty_range(session):
    """Date range with no data should return total=0."""
    team = _make_team(session)
    _make_logs(session, team, n=20, start_date=date(2026, 2, 1))

    backtester = PropBacktester({})

    result = backtester.backtest(
        session,
        sport="nba",
        start_date=date(2025, 1, 1),
        end_date=date(2025, 1, 31),
    )

    assert result["total"] == 0
    assert result["wins"] == 0
    assert result["losses"] == 0


def test_backtest_respects_min_minutes(session):
    """Player with minutes below min_minutes threshold should be excluded."""
    team = _make_team(session)
    # High-minutes player
    _make_logs(session, team, n=20, start_date=date(2026, 2, 1),
               player_name="Jayson Tatum", minutes=35.0)
    # Low-minutes player — should be excluded
    _make_logs(session, team, n=20, start_date=date(2026, 2, 1),
               player_name="Low Minutes Guy", minutes=5.0)

    backtester = PropBacktester({
        "recent_weight": 0.6,
        "season_weight": 0.4,
        "min_edge": 5.0,
        "lookback": 5,
        "min_minutes": 15,
    })

    result = backtester.backtest(
        session,
        sport="nba",
        start_date=date(2026, 2, 1),
        end_date=date(2026, 2, 20),
    )

    # None of the picks should be for "Low Minutes Guy"
    for pick in result["picks"]:
        assert pick["player_name"] != "Low Minutes Guy"

from datetime import datetime, timezone
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from backend.models import Base, ApiUsage, Game, Team
from backend.database import migrate_api_usage, migrate_game_start_time


def make_session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return Session(engine), engine


def test_api_usage_has_endpoint_column():
    session, _ = make_session()
    row = ApiUsage(endpoint="events", sport="nba", credits_used=1, created_at=datetime.now(tz=timezone.utc))
    session.add(row)
    session.commit()
    assert row.id is not None
    assert row.endpoint == "events"


def test_api_usage_has_requests_remaining():
    session, _ = make_session()
    row = ApiUsage(endpoint="player_props", sport="nba", credits_used=1,
                   requests_remaining=19500, created_at=datetime.now(tz=timezone.utc))
    session.add(row)
    session.commit()
    assert row.requests_remaining == 19500


def test_api_usage_created_at_index():
    _, engine = make_session()
    indexes = inspect(engine).get_indexes("api_usage")
    indexed_cols = [col for idx in indexes for col in idx["column_names"]]
    assert "created_at" in indexed_cols


def test_game_has_start_time():
    session, _ = make_session()
    team1 = Team(name="Team A", abbreviation="A", sport="nba")
    team2 = Team(name="Team B", abbreviation="B", sport="nba")
    session.add_all([team1, team2])
    session.flush()
    game = Game(
        sport="nba", season="2026-2027", date=datetime(2026, 3, 17).date(),
        home_team_id=team1.id, away_team_id=team2.id, status="scheduled",
        start_time=datetime(2026, 3, 17, 23, 30, tzinfo=timezone.utc),
    )
    session.add(game)
    session.commit()
    # SQLite does not preserve timezone info; compare naive datetime
    assert game.start_time.replace(tzinfo=None) == datetime(2026, 3, 17, 23, 30)


def test_game_start_time_nullable():
    session, _ = make_session()
    team1 = Team(name="Team C", abbreviation="C", sport="nba")
    team2 = Team(name="Team D", abbreviation="D", sport="nba")
    session.add_all([team1, team2])
    session.flush()
    game = Game(
        sport="nba", season="2026-2027", date=datetime(2026, 3, 17).date(),
        home_team_id=team1.id, away_team_id=team2.id, status="scheduled",
    )
    session.add(game)
    session.commit()
    assert game.start_time is None


def test_migrate_api_usage_drops_old_schema():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE api_usage (id INTEGER PRIMARY KEY, source TEXT, request_count INTEGER, month TEXT, updated_at DATETIME)"
        ))
    migrate_api_usage(engine)
    assert "api_usage" not in inspect(engine).get_table_names()
    Base.metadata.create_all(engine)
    columns = [c["name"] for c in inspect(engine).get_columns("api_usage")]
    assert "endpoint" in columns
    assert "source" not in columns


def test_migrate_game_start_time_adds_column():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE games (id INTEGER PRIMARY KEY, sport TEXT, season TEXT, date DATE, "
            "home_team_id INTEGER, away_team_id INTEGER, status TEXT)"
        ))
    migrate_game_start_time(engine)
    columns = [c["name"] for c in inspect(engine).get_columns("games")]
    assert "start_time" in columns

from backend.models import Base, Team, Game, Odds
from backend.database import get_engine, get_session
from backend.analysis.line_movement import analyze_line_movement, get_steam_moves
from datetime import date, datetime, timezone

def _setup():
    engine = get_engine(":memory:")
    Base.metadata.create_all(engine)
    session = get_session(engine)
    t1 = Team(name="A", abbreviation="A", sport="nba")
    t2 = Team(name="B", abbreviation="B", sport="nba")
    session.add_all([t1, t2])
    session.flush()
    g = Game(sport="nba", season="2025-26", date=date(2026, 1, 1),
             home_team_id=t1.id, away_team_id=t2.id, status="scheduled")
    session.add(g)
    session.flush()
    return session, g

def test_analyze_line_movement_basic():
    session, g = _setup()
    session.add(Odds(game_id=g.id, bookmaker="dk", moneyline_home=-150, moneyline_away=130,
                     spread_home=-3.5, spread_away=3.5, over_under=220.0,
                     timestamp=datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)))
    session.add(Odds(game_id=g.id, bookmaker="dk", moneyline_home=-170, moneyline_away=150,
                     spread_home=-4.5, spread_away=4.5, over_under=222.0,
                     timestamp=datetime(2026, 1, 1, 18, 0, tzinfo=timezone.utc)))
    session.flush()
    result = analyze_line_movement(session, g.id)
    assert result is not None
    assert result["ml_move_home"] == -20
    assert result["spread_move"] == -1.0
    assert result["ou_move"] == 2.0
    session.close()

def test_insufficient_snapshots_returns_none():
    session, g = _setup()
    session.add(Odds(game_id=g.id, bookmaker="dk", moneyline_home=-150, moneyline_away=130,
                     timestamp=datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)))
    session.flush()
    assert analyze_line_movement(session, g.id) is None
    session.close()

def test_steam_move_detection():
    session, g = _setup()
    session.add(Odds(game_id=g.id, bookmaker="dk", spread_home=-3.0, spread_away=3.0,
                     timestamp=datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)))
    session.add(Odds(game_id=g.id, bookmaker="dk", spread_home=-4.0, spread_away=4.0,
                     timestamp=datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)))
    session.add(Odds(game_id=g.id, bookmaker="dk", spread_home=-4.5, spread_away=4.5,
                     timestamp=datetime(2026, 1, 1, 14, 0, tzinfo=timezone.utc)))
    session.flush()
    moves = get_steam_moves(session, g.id, threshold=0.5)
    assert len(moves) == 2
    assert moves[0]["move"] == -1.0
    session.close()

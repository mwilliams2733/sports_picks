from backend.models import Base, Team, Game, Odds, PickResult
from backend.database import get_engine, get_session
from backend.pipeline.grader import capture_closing_odds
from datetime import date, datetime, timezone

def test_capture_closing_odds_moneyline_home():
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

    session.add(Odds(game_id=g.id, bookmaker="dk", moneyline_home=-150, moneyline_away=130,
                     timestamp=datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)))
    session.add(Odds(game_id=g.id, bookmaker="dk", moneyline_home=-170, moneyline_away=150,
                     timestamp=datetime(2026, 1, 1, 19, 0, tzinfo=timezone.utc)))
    session.flush()

    pr = PickResult(pick_id=1, result="win", payout=1.0)
    capture_closing_odds(session, pr, g.id, "moneyline", "HOME ML")
    assert pr.odds_at_close == -170

    session.close()

def test_capture_closing_odds_moneyline_away():
    engine = get_engine(":memory:")
    Base.metadata.create_all(engine)
    session = get_session(engine)

    t1 = Team(name="A", abbreviation="A", sport="nba")
    t2 = Team(name="B", abbreviation="B", sport="nba")
    session.add_all([t1, t2])
    session.flush()

    g = Game(sport="nba", season="2025-26", date=date(2026, 1, 1),
             home_team_id=t1.id, away_team_id=t2.id, home_score=100, away_score=110, status="final")
    session.add(g)
    session.flush()

    session.add(Odds(game_id=g.id, bookmaker="dk", moneyline_home=-150, moneyline_away=130,
                     timestamp=datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)))
    session.add(Odds(game_id=g.id, bookmaker="dk", moneyline_home=-170, moneyline_away=150,
                     timestamp=datetime(2026, 1, 1, 19, 0, tzinfo=timezone.utc)))
    session.flush()

    pr = PickResult(pick_id=1, result="win", payout=1.0)
    capture_closing_odds(session, pr, g.id, "moneyline", "AWAY ML")
    assert pr.odds_at_close == 150

    session.close()

def test_capture_closing_odds_no_odds_available():
    engine = get_engine(":memory:")
    Base.metadata.create_all(engine)
    session = get_session(engine)

    t1 = Team(name="A", abbreviation="A", sport="nba")
    t2 = Team(name="B", abbreviation="B", sport="nba")
    session.add_all([t1, t2])
    session.flush()

    g = Game(sport="nba", season="2025-26", date=date(2026, 1, 1),
             home_team_id=t1.id, away_team_id=t2.id, status="final")
    session.add(g)
    session.flush()

    pr = PickResult(pick_id=1, result="win", payout=1.0)
    capture_closing_odds(session, pr, g.id, "moneyline", "HOME ML")
    assert pr.odds_at_close is None  # No odds to capture

    session.close()


def _setup_game_with_odds(spread_home=None, spread_away=None, over_under=None):
    """Helper: build a session with one game and one closing-odds snapshot."""
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
    # Earlier snapshot — should be ignored (most recent wins).
    session.add(Odds(game_id=g.id, bookmaker="dk",
                     spread_home=-2.5, spread_away=2.5, over_under=215.0,
                     timestamp=datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)))
    # Closing snapshot.
    session.add(Odds(game_id=g.id, bookmaker="dk",
                     spread_home=spread_home, spread_away=spread_away, over_under=over_under,
                     timestamp=datetime(2026, 1, 1, 19, 0, tzinfo=timezone.utc)))
    session.flush()
    return engine, session, g


def test_capture_closing_odds_spread_home_uses_spread_home():
    engine, session, g = _setup_game_with_odds(spread_home=-3.5, spread_away=3.5)
    pr = PickResult(pick_id=1, result="win", payout=1.0)
    capture_closing_odds(session, pr, g.id, "spread", "HOME -3.0", odds_at_pick=-110)
    assert pr.line_at_close == -3.5
    assert pr.odds_at_close == -110  # juice fallback to odds_at_pick, not fabricated -110
    session.close()


def test_capture_closing_odds_spread_away_uses_spread_away():
    engine, session, g = _setup_game_with_odds(spread_home=-3.5, spread_away=3.5)
    pr = PickResult(pick_id=1, result="win", payout=1.0)
    capture_closing_odds(session, pr, g.id, "spread", "AWAY +4.0", odds_at_pick=-115)
    assert pr.line_at_close == 3.5
    assert pr.odds_at_close == -115
    session.close()


def test_capture_closing_odds_over_uses_over_under():
    engine, session, g = _setup_game_with_odds(over_under=219.0)
    pr = PickResult(pick_id=1, result="win", payout=1.0)
    capture_closing_odds(session, pr, g.id, "over_under", "Over 218.5", odds_at_pick=-110)
    assert pr.line_at_close == 219.0
    assert pr.odds_at_close == -110
    session.close()


def test_capture_closing_odds_under_uses_over_under():
    engine, session, g = _setup_game_with_odds(over_under=219.0)
    pr = PickResult(pick_id=1, result="loss", payout=-1.0)
    capture_closing_odds(session, pr, g.id, "over_under", "Under 220.5", odds_at_pick=-105)
    assert pr.line_at_close == 219.0
    assert pr.odds_at_close == -105
    session.close()


def test_capture_closing_odds_spread_no_odds_at_pick_leaves_odds_close_null():
    """If odds_at_pick is not provided for a spread bet, odds_at_close stays None
    (not fabricated -110). Only line_at_close is populated."""
    engine, session, g = _setup_game_with_odds(spread_home=-3.5, spread_away=3.5)
    pr = PickResult(pick_id=1, result="win", payout=1.0)
    capture_closing_odds(session, pr, g.id, "spread", "HOME -3.0")
    assert pr.line_at_close == -3.5
    assert pr.odds_at_close is None
    session.close()


def test_migrate_pick_result_line_at_close_adds_column():
    """The line_at_close migration should add the column when missing and be idempotent."""
    from backend.database import migrate_pick_result_line_at_close
    from sqlalchemy import inspect as sa_inspect, text
    engine = get_engine(":memory:")
    # Build a pick_results table without line_at_close to simulate pre-migration state.
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE pick_results ("
            " id INTEGER PRIMARY KEY,"
            " pick_id INTEGER NOT NULL,"
            " result TEXT NOT NULL,"
            " payout REAL NOT NULL DEFAULT 0.0,"
            " odds_at_close INTEGER"
            ")"
        ))
    inspector = sa_inspect(engine)
    cols = [c["name"] for c in inspector.get_columns("pick_results")]
    assert "line_at_close" not in cols
    migrate_pick_result_line_at_close(engine)
    inspector = sa_inspect(engine)
    cols = [c["name"] for c in inspector.get_columns("pick_results")]
    assert "line_at_close" in cols
    # Idempotent on second call.
    migrate_pick_result_line_at_close(engine)

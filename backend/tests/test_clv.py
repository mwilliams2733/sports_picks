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

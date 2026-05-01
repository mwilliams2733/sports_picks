from backend.pipeline.grader import grade_pick
from backend.analysis.odds_utils import calculate_payout

def test_grade_moneyline_home_win():
    result, payout = grade_pick("moneyline", "HOME ML", 110, 100, -150)
    assert result == "win"
    assert abs(payout - calculate_payout(-150)) < 0.01

def test_grade_moneyline_away_win():
    result, payout = grade_pick("moneyline", "AWAY ML", 100, 110, 130)
    assert result == "win"

def test_grade_moneyline_loss():
    result, payout = grade_pick("moneyline", "HOME ML", 95, 105, -150)
    assert result == "loss"
    assert payout == -1.0

def test_grade_spread_cover():
    result, payout = grade_pick("spread", "HOME -4.5", 110, 100, -110)
    assert result == "win"

def test_grade_spread_no_cover():
    result, payout = grade_pick("spread", "HOME -4.5", 103, 100, -110)
    assert result == "loss"

def test_grade_over_hit():
    result, payout = grade_pick("over_under", "Over 218.5", 115, 110, -110)
    assert result == "win"

def test_grade_under_hit():
    result, payout = grade_pick("over_under", "Under 218.5", 100, 105, -110)
    assert result == "win"


def test_combat_grader_updates_fighter_elo_on_decision():
    """A finalized MMA game with home_score=1, away_score=0 should bump home Elo
    by exactly K/2 = 12 points (expected 0.5, actual 1.0, delta = K * 0.5 = 12)."""
    from datetime import date as _date
    from backend.database import get_engine, get_session
    from backend.models import Base, Team, Game, EloRating
    from backend.pipeline.grader import grade_completed_games

    engine = get_engine(":memory:")
    Base.metadata.create_all(engine)
    session = get_session(engine)
    home = Team(id=1, name="A", abbreviation="A", sport="mma")
    away = Team(id=2, name="B", abbreviation="B", sport="mma")
    session.add_all([home, away])
    session.flush()
    session.add_all([
        EloRating(team_id=1, sport="mma", rating=1500.0),
        EloRating(team_id=2, sport="mma", rating=1500.0),
    ])
    # Home wins
    session.add(Game(id=1, sport="mma", season="2026", date=_date(2026, 4, 29),
                     home_team_id=1, away_team_id=2,
                     home_score=1, away_score=0, status="final"))
    session.commit()

    grade_completed_games(session)

    home_elo = (session.query(EloRating)
                .filter(EloRating.team_id == 1, EloRating.sport == "mma").first()).rating
    away_elo = (session.query(EloRating)
                .filter(EloRating.team_id == 2, EloRating.sport == "mma").first()).rating
    assert abs(home_elo - 1512.0) < 0.5, f"Home should gain ~12 Elo, got {home_elo}"
    assert abs(away_elo - 1488.0) < 0.5, f"Away should lose ~12 Elo, got {away_elo}"


def test_combat_grader_handles_draws():
    """A draw (home_score=1, away_score=1) on equal Elo should leave Elo unchanged."""
    from datetime import date as _date
    from backend.database import get_engine, get_session
    from backend.models import Base, Team, Game, EloRating
    from backend.pipeline.grader import grade_completed_games

    engine = get_engine(":memory:")
    Base.metadata.create_all(engine)
    session = get_session(engine)
    home = Team(id=1, name="A", abbreviation="A", sport="mma")
    away = Team(id=2, name="B", abbreviation="B", sport="mma")
    session.add_all([home, away])
    session.flush()
    session.add_all([
        EloRating(team_id=1, sport="mma", rating=1500.0),
        EloRating(team_id=2, sport="mma", rating=1500.0),
    ])
    # Draw
    session.add(Game(id=1, sport="mma", season="2026", date=_date(2026, 4, 29),
                     home_team_id=1, away_team_id=2,
                     home_score=1, away_score=1, status="final"))
    session.commit()

    grade_completed_games(session)

    home_elo = (session.query(EloRating)
                .filter(EloRating.team_id == 1, EloRating.sport == "mma").first()).rating
    away_elo = (session.query(EloRating)
                .filter(EloRating.team_id == 2, EloRating.sport == "mma").first()).rating
    # Equal Elo + draw → no change
    assert abs(home_elo - 1500.0) < 0.1
    assert abs(away_elo - 1500.0) < 0.1


def test_combat_grader_does_not_affect_team_sport_elo():
    """A finalized NBA game graded by the same path must not invoke the combat
    Elo update — team-sport Elo path is unchanged."""
    from datetime import date as _date
    from backend.database import get_engine, get_session
    from backend.models import Base, Team, Game, EloRating
    from backend.pipeline.grader import grade_completed_games

    engine = get_engine(":memory:")
    Base.metadata.create_all(engine)
    session = get_session(engine)
    home = Team(id=1, name="LAL", abbreviation="LAL", sport="nba")
    away = Team(id=2, name="BOS", abbreviation="BOS", sport="nba")
    session.add_all([home, away])
    session.flush()
    session.add_all([
        EloRating(team_id=1, sport="nba", rating=1500.0),
        EloRating(team_id=2, sport="nba", rating=1500.0),
    ])
    session.add(Game(id=1, sport="nba", season="2025-26", date=_date(2026, 4, 29),
                     home_team_id=1, away_team_id=2,
                     home_score=110, away_score=100, status="final"))
    session.commit()

    grade_completed_games(session)

    # Team-sport Elo update path is untouched by this task — Elo should remain 1500
    # OR be updated by the existing path (if one exists). Either is acceptable; what
    # matters is that the *combat* path didn't fire and apply a 12-point shift.
    home_elo = (session.query(EloRating)
                .filter(EloRating.team_id == 1, EloRating.sport == "nba").first()).rating
    # If combat path fired (incorrect), home would gain ~12 Elo (since 110 != 100,
    # actual_home would be 1.0). Assert no such delta.
    assert home_elo != 1512.0, "Combat Elo update must not fire for NBA games"

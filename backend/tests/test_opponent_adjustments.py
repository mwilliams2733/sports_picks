from backend.models import Base, Team, Game, TeamStat
from backend.database import get_engine, get_session
from backend.analysis.opponent_adjustments import compute_adjusted_efficiency
from datetime import date


def _make_dummy_game(session, home_id, away_id, day=1):
    """Create a dummy final game to satisfy FK constraints on TeamStat.game_id."""
    g = Game(sport="nba", season="2025-26", date=date(2026, 1, day),
             home_team_id=home_id, away_team_id=away_id,
             home_score=0, away_score=0, status="dummy")
    session.add(g)
    session.flush()
    return g


def test_adjusted_efficiency_boosts_tough_schedule():
    engine = get_engine(":memory:")
    Base.metadata.create_all(engine)
    session = get_session(engine)

    teams = [Team(name=f"T{i}", abbreviation=f"T{i}", sport="nba") for i in range(7)]
    session.add_all(teams)
    session.flush()

    # Create a dummy game to hang the base stat on
    dummy = _make_dummy_game(session, teams[0].id, teams[1].id, day=30)

    session.add(TeamStat(team_id=teams[0].id, game_id=dummy.id, stat_type="offensive_rating", value=100.0))
    for i in range(1, 6):
        session.add(TeamStat(team_id=teams[i].id, game_id=dummy.id, stat_type="defensive_rating", value=100.0))
        g = Game(sport="nba", season="2025-26", date=date(2026, 1, i),
                 home_team_id=teams[0].id, away_team_id=teams[i].id,
                 home_score=100, away_score=95, status="final")
        session.add(g)
    session.flush()

    adj = compute_adjusted_efficiency(session, teams[0].id, "nba", "offensive_rating")
    assert adj is not None
    assert adj > 100, f"Expected boost for tough schedule, got {adj}"
    assert abs(adj - 110.0) < 1.0
    session.close()


def test_adjusted_efficiency_penalizes_weak_schedule():
    engine = get_engine(":memory:")
    Base.metadata.create_all(engine)
    session = get_session(engine)

    teams = [Team(name=f"T{i}", abbreviation=f"T{i}", sport="nba") for i in range(7)]
    session.add_all(teams)
    session.flush()

    dummy = _make_dummy_game(session, teams[0].id, teams[1].id, day=30)

    session.add(TeamStat(team_id=teams[0].id, game_id=dummy.id, stat_type="offensive_rating", value=115.0))
    for i in range(1, 6):
        session.add(TeamStat(team_id=teams[i].id, game_id=dummy.id, stat_type="defensive_rating", value=120.0))
        g = Game(sport="nba", season="2025-26", date=date(2026, 1, i),
                 home_team_id=teams[0].id, away_team_id=teams[i].id,
                 home_score=110, away_score=95, status="final")
        session.add(g)
    session.flush()

    adj = compute_adjusted_efficiency(session, teams[0].id, "nba", "offensive_rating")
    assert adj < 115, f"Expected penalty for weak schedule, got {adj}"
    session.close()


def test_insufficient_games_returns_raw():
    engine = get_engine(":memory:")
    Base.metadata.create_all(engine)
    session = get_session(engine)

    teams = [Team(name=f"T{i}", abbreviation=f"T{i}", sport="nba") for i in range(2)]
    session.add_all(teams)
    session.flush()

    dummy = _make_dummy_game(session, teams[0].id, teams[1].id)

    session.add(TeamStat(team_id=teams[0].id, game_id=dummy.id, stat_type="offensive_rating", value=108.0))
    session.flush()

    adj = compute_adjusted_efficiency(session, teams[0].id, "nba", "offensive_rating")
    assert adj == 108.0
    session.close()

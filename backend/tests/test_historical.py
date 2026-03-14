from datetime import date
from backend.backtesting.historical import store_games, compute_historical_elo, _season_dates, _parse_date
from backend.models import Base, Team, Game, EloRating


def test_season_dates_nba():
    start, end = _season_dates("nba", 2024)
    assert start == date(2024, 10, 22)
    assert end == date(2025, 6, 20)


def test_season_dates_nfl_crosses_year():
    start, end = _season_dates("nfl", 2024)
    assert start == date(2024, 9, 5)
    assert end == date(2025, 2, 10)


def test_parse_date_iso():
    assert _parse_date("2026-03-14T00:00Z") == date(2026, 3, 14)
    assert _parse_date("2026-01-05T19:30:00+00:00") == date(2026, 1, 5)


def test_store_games_creates_teams_and_games(db_engine):
    from backend.database import get_session
    Base.metadata.create_all(db_engine)
    session = get_session(db_engine)

    games = [
        {"espn_id": "1", "date": "2026-01-10T19:00Z", "status": "final",
         "home_team": "BOS", "home_team_name": "Boston Celtics",
         "away_team": "LAL", "away_team_name": "Los Angeles Lakers",
         "home_score": 110, "away_score": 105},
        {"espn_id": "2", "date": "2026-01-11T20:00Z", "status": "final",
         "home_team": "LAL", "home_team_name": "Los Angeles Lakers",
         "away_team": "BOS", "away_team_name": "Boston Celtics",
         "home_score": 98, "away_score": 102},
    ]

    count = store_games(session, "nba", 2025, games)
    assert count == 2

    teams = session.query(Team).all()
    assert len(teams) == 2

    db_games = session.query(Game).all()
    assert len(db_games) == 2
    assert db_games[0].home_score == 110

    # Storing same games again should not create duplicates
    count2 = store_games(session, "nba", 2025, games)
    assert count2 == 0
    assert session.query(Game).count() == 2

    session.close()


def test_compute_historical_elo(db_engine):
    from backend.database import get_session
    Base.metadata.create_all(db_engine)
    session = get_session(db_engine)

    # Create teams
    t1 = Team(id=1, name="Boston Celtics", abbreviation="BOS", sport="nba")
    t2 = Team(id=2, name="LA Lakers", abbreviation="LAL", sport="nba")
    session.add_all([t1, t2])
    session.flush()

    # Create games where BOS wins both
    g1 = Game(sport="nba", season="2025-26", date=date(2026, 1, 10),
              home_team_id=1, away_team_id=2, home_score=110, away_score=100, status="final")
    g2 = Game(sport="nba", season="2025-26", date=date(2026, 1, 12),
              home_team_id=2, away_team_id=1, home_score=95, away_score=105, status="final")
    session.add_all([g1, g2])
    session.commit()

    compute_historical_elo(session, "nba")

    bos_elo = session.query(EloRating).filter(EloRating.team_id == 1).first()
    lal_elo = session.query(EloRating).filter(EloRating.team_id == 2).first()
    assert bos_elo is not None
    assert lal_elo is not None
    assert bos_elo.rating > 1500  # BOS won both, should be above 1500
    assert lal_elo.rating < 1500  # LAL lost both

    session.close()

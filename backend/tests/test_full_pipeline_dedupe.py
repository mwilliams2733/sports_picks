"""Tests for the upstream-fetcher dedupe fix.

Bug: _ensure_game_from_odds previously matched only on (sport, home, away)
filtered to status IN ('scheduled', 'in_progress'). Once the game graded
to 'final', the match returned None and a fresh duplicate row was created
on the next Odds API tick. This produced phantom 'scheduled' rows beside
the real graded ones.

Fix: match by (sport, date, home, away) with NO status filter — a row for
that exact day with those exact teams is always the same game.
"""
from datetime import date


def test_ensure_game_from_odds_does_not_duplicate_already_final_game():
    """The bug repro: an Odds API event for a matchup we've already graded
    to status='final' must not insert a fresh 'scheduled' duplicate."""
    from backend.database import get_engine, get_session
    from backend.models import Base, Team, Game
    from backend.pipeline.full_pipeline import _ensure_game_from_odds

    engine = get_engine(":memory:")
    Base.metadata.create_all(engine)
    session = get_session(engine)
    try:
        session.add_all([
            Team(id=1, name="Detroit Pistons", abbreviation="DET", sport="nba"),
            Team(id=2, name="Orlando Magic", abbreviation="ORL", sport="nba"),
            # Original graded game.
            Game(id=900, sport="nba", season="2025-26",
                 date=date(2026, 4, 29),
                 home_team_id=1, away_team_id=2,
                 home_score=116, away_score=109, status="final"),
        ])
        session.commit()

        event = {
            "home_team": "Detroit Pistons",
            "away_team": "Orlando Magic",
            "commence_time": "2026-04-29T23:00:00Z",
        }
        _ensure_game_from_odds(session, "nba", event)

        all_matchups = (
            session.query(Game)
            .filter(
                Game.sport == "nba",
                Game.date == date(2026, 4, 29),
                Game.home_team_id == 1,
                Game.away_team_id == 2,
            ).all()
        )
        assert len(all_matchups) == 1
        # Original status preserved.
        assert all_matchups[0].status == "final"
    finally:
        session.close()


def test_ensure_game_from_odds_does_not_duplicate_existing_scheduled_game():
    """Pre-existing 'scheduled' row → no duplicate; existing behavior preserved."""
    from backend.database import get_engine, get_session
    from backend.models import Base, Team, Game
    from backend.pipeline.full_pipeline import _ensure_game_from_odds

    engine = get_engine(":memory:")
    Base.metadata.create_all(engine)
    session = get_session(engine)
    try:
        session.add_all([
            Team(id=1, name="Boston Celtics", abbreviation="BOS", sport="nba"),
            Team(id=2, name="Philadelphia 76ers", abbreviation="PHI", sport="nba"),
            Game(id=901, sport="nba", season="2025-26",
                 date=date(2026, 5, 1),
                 home_team_id=2, away_team_id=1, status="scheduled"),
        ])
        session.commit()
        _ensure_game_from_odds(session, "nba", {
            "home_team": "Philadelphia 76ers",
            "away_team": "Boston Celtics",
            "commence_time": "2026-05-01T19:30:00Z",
        })
        rows = session.query(Game).filter(Game.sport == "nba").all()
        assert len(rows) == 1
        assert rows[0].id == 901
    finally:
        session.close()


def test_ensure_game_from_odds_creates_new_game_on_different_date():
    """Same teams, DIFFERENT date → genuinely a new game (rematch in series),
    must create a new row."""
    from backend.database import get_engine, get_session
    from backend.models import Base, Team, Game
    from backend.pipeline.full_pipeline import _ensure_game_from_odds

    engine = get_engine(":memory:")
    Base.metadata.create_all(engine)
    session = get_session(engine)
    try:
        session.add_all([
            Team(id=1, name="Denver Nuggets", abbreviation="DEN", sport="nba"),
            Team(id=2, name="Minnesota Timberwolves", abbreviation="MIN", sport="nba"),
            # Game 1 of the series, already graded.
            Game(id=902, sport="nba", season="2025-26",
                 date=date(2026, 4, 20),
                 home_team_id=2, away_team_id=1,
                 home_score=114, away_score=99, status="final"),
        ])
        session.commit()
        _ensure_game_from_odds(session, "nba", {
            "home_team": "Minnesota Timberwolves",
            "away_team": "Denver Nuggets",
            "commence_time": "2026-04-23T20:00:00Z",  # different date
        })
        rows = (session.query(Game)
                .filter(Game.sport == "nba")
                .order_by(Game.date.asc()).all())
        assert len(rows) == 2
        # Game 1 unchanged.
        assert rows[0].id == 902
        assert rows[0].status == "final"
        # New row for game 2.
        assert rows[1].date == date(2026, 4, 23)
        assert rows[1].status == "scheduled"
    finally:
        session.close()

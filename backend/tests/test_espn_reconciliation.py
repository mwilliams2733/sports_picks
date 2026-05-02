"""Tests for ESPN-authoritative reconciliation in _store_games.

Bug context: phantom Game rows leak into Today's Picks via the Odds API
(which posts lines for hypothetical playoff games that may never happen,
e.g. a Game 6/7 a series resolved short of). ESPN's scoreboard is the
authoritative list of games that actually exist on a given date — so on
each morning's ESPN fetch we reconcile our DB against it: any pending
row ESPN doesn't list is marked 'canceled', and any previously-canceled
row ESPN does list is restored to 'scheduled'.
"""
from datetime import date


def _espn_event(home_abbr: str, home_name: str, away_abbr: str, away_name: str,
                event_date: str = "2026-05-01T22:00:00Z", status: str = "scheduled"):
    """Build an ESPN-shaped event dict matching what fetch_scoreboard returns."""
    return {
        "home_team": home_abbr, "home_team_name": home_name,
        "away_team": away_abbr, "away_team_name": away_name,
        "date": event_date, "status": status,
        "home_score": None, "away_score": None,
    }


def test_phantom_game_not_in_espn_list_is_marked_canceled():
    """A scheduled DB row that ESPN does not include for the fetch date must
    be marked status='canceled' so it disappears from Today's Picks."""
    from backend.database import get_engine, get_session
    from backend.models import Base, Team, Game
    from backend.pipeline.full_pipeline import _store_games

    engine = get_engine(":memory:")
    Base.metadata.create_all(engine)
    session = get_session(engine)
    try:
        session.add_all([
            Team(id=1, name="Denver Nuggets", abbreviation="DEN", sport="nba"),
            Team(id=2, name="Minnesota Timberwolves", abbreviation="MIN", sport="nba"),
            Team(id=3, name="Detroit Pistons", abbreviation="DET", sport="nba"),
            Team(id=4, name="Orlando Magic", abbreviation="ORL", sport="nba"),
            # Phantom row from the Odds API path — bookmakers posted a line,
            # series ended early, ESPN never had this on its schedule.
            Game(id=100, sport="nba", season="2025-26", date=date(2026, 5, 1),
                 home_team_id=2, away_team_id=1, status="scheduled"),
        ])
        session.commit()

        # ESPN reports only one real game today.
        espn_events = [_espn_event("DET", "Detroit Pistons", "ORL", "Orlando Magic")]
        _store_games(session, "nba", date(2026, 5, 1), espn_events)

        phantom = session.get(Game, 100)
        assert phantom.status == "canceled"
    finally:
        session.close()


def test_real_game_in_espn_list_remains_scheduled():
    """A scheduled row that ESPN DOES list must be left alone."""
    from backend.database import get_engine, get_session
    from backend.models import Base, Team, Game
    from backend.pipeline.full_pipeline import _store_games

    engine = get_engine(":memory:")
    Base.metadata.create_all(engine)
    session = get_session(engine)
    try:
        session.add_all([
            Team(id=1, name="Detroit Pistons", abbreviation="DET", sport="nba"),
            Team(id=2, name="Orlando Magic", abbreviation="ORL", sport="nba"),
            Game(id=200, sport="nba", season="2025-26", date=date(2026, 5, 1),
                 home_team_id=2, away_team_id=1, status="scheduled"),
        ])
        session.commit()

        espn_events = [_espn_event("DET", "Detroit Pistons", "ORL", "Orlando Magic")]
        _store_games(session, "nba", date(2026, 5, 1), espn_events)

        g = session.get(Game, 200)
        assert g.status == "scheduled"
    finally:
        session.close()


def test_canceled_game_back_in_espn_list_is_restored_to_scheduled():
    """If an event was previously canceled but ESPN now lists it (e.g. a
    rescheduled postponement), restore status to 'scheduled' so the user
    sees it again."""
    from backend.database import get_engine, get_session
    from backend.models import Base, Team, Game
    from backend.pipeline.full_pipeline import _store_games

    engine = get_engine(":memory:")
    Base.metadata.create_all(engine)
    session = get_session(engine)
    try:
        session.add_all([
            Team(id=1, name="Lakers", abbreviation="LAL", sport="nba"),
            Team(id=2, name="Rockets", abbreviation="HOU", sport="nba"),
            Game(id=300, sport="nba", season="2025-26", date=date(2026, 5, 1),
                 home_team_id=2, away_team_id=1, status="canceled"),
        ])
        session.commit()

        espn_events = [_espn_event("HOU", "Rockets", "LAL", "Lakers")]
        _store_games(session, "nba", date(2026, 5, 1), espn_events)

        g = session.get(Game, 300)
        assert g.status == "scheduled"
    finally:
        session.close()


def test_final_game_not_in_espn_list_is_left_alone():
    """A row already graded to 'final' must not be flipped to 'canceled' just
    because today's ESPN scoreboard doesn't include it (it's historical)."""
    from backend.database import get_engine, get_session
    from backend.models import Base, Team, Game
    from backend.pipeline.full_pipeline import _store_games

    engine = get_engine(":memory:")
    Base.metadata.create_all(engine)
    session = get_session(engine)
    try:
        session.add_all([
            Team(id=1, name="Bulls", abbreviation="CHI", sport="nba"),
            Team(id=2, name="Bucks", abbreviation="MIL", sport="nba"),
            Team(id=3, name="Detroit Pistons", abbreviation="DET", sport="nba"),
            Team(id=4, name="Orlando Magic", abbreviation="ORL", sport="nba"),
            Game(id=400, sport="nba", season="2025-26", date=date(2026, 5, 1),
                 home_team_id=2, away_team_id=1,
                 home_score=110, away_score=98, status="final"),
        ])
        session.commit()

        espn_events = [_espn_event("DET", "Detroit Pistons", "ORL", "Orlando Magic")]
        _store_games(session, "nba", date(2026, 5, 1), espn_events)

        g = session.get(Game, 400)
        assert g.status == "final"
    finally:
        session.close()


def test_in_progress_game_not_in_espn_list_is_left_alone():
    """ESPN sometimes drops in_progress games briefly during box-score updates.
    We must not cancel a row whose status is 'in_progress'."""
    from backend.database import get_engine, get_session
    from backend.models import Base, Team, Game
    from backend.pipeline.full_pipeline import _store_games

    engine = get_engine(":memory:")
    Base.metadata.create_all(engine)
    session = get_session(engine)
    try:
        session.add_all([
            Team(id=1, name="Knicks", abbreviation="NYK", sport="nba"),
            Team(id=2, name="Heat", abbreviation="MIA", sport="nba"),
            Team(id=3, name="Detroit Pistons", abbreviation="DET", sport="nba"),
            Team(id=4, name="Orlando Magic", abbreviation="ORL", sport="nba"),
            Game(id=500, sport="nba", season="2025-26", date=date(2026, 5, 1),
                 home_team_id=2, away_team_id=1,
                 home_score=58, away_score=62, status="in_progress"),
        ])
        session.commit()

        espn_events = [_espn_event("DET", "Detroit Pistons", "ORL", "Orlando Magic")]
        _store_games(session, "nba", date(2026, 5, 1), espn_events)

        g = session.get(Game, 500)
        assert g.status == "in_progress"
    finally:
        session.close()


def test_no_reconciliation_when_espn_returns_zero_events():
    """ESPN outage / off-day → returns no events. Must NOT cancel everything
    (we can't tell outage apart from genuine no-games). Leave rows alone."""
    from backend.database import get_engine, get_session
    from backend.models import Base, Team, Game
    from backend.pipeline.full_pipeline import _store_games

    engine = get_engine(":memory:")
    Base.metadata.create_all(engine)
    session = get_session(engine)
    try:
        session.add_all([
            Team(id=1, name="Lakers", abbreviation="LAL", sport="nba"),
            Team(id=2, name="Rockets", abbreviation="HOU", sport="nba"),
            Game(id=600, sport="nba", season="2025-26", date=date(2026, 5, 1),
                 home_team_id=2, away_team_id=1, status="scheduled"),
        ])
        session.commit()

        _store_games(session, "nba", date(2026, 5, 1), [])

        g = session.get(Game, 600)
        assert g.status == "scheduled"
    finally:
        session.close()


def test_other_date_games_unaffected_by_reconciliation():
    """Reconciliation must scope to (sport, target_date) — it must not cancel
    rows on other dates that ESPN obviously didn't list (because we didn't ask)."""
    from backend.database import get_engine, get_session
    from backend.models import Base, Team, Game
    from backend.pipeline.full_pipeline import _store_games

    engine = get_engine(":memory:")
    Base.metadata.create_all(engine)
    session = get_session(engine)
    try:
        session.add_all([
            Team(id=1, name="Bucks", abbreviation="MIL", sport="nba"),
            Team(id=2, name="Bulls", abbreviation="CHI", sport="nba"),
            Team(id=3, name="Detroit Pistons", abbreviation="DET", sport="nba"),
            Team(id=4, name="Orlando Magic", abbreviation="ORL", sport="nba"),
            # Tomorrow's row — not what we're fetching today.
            Game(id=700, sport="nba", season="2025-26", date=date(2026, 5, 2),
                 home_team_id=2, away_team_id=1, status="scheduled"),
        ])
        session.commit()

        espn_events = [_espn_event("DET", "Detroit Pistons", "ORL", "Orlando Magic")]
        _store_games(session, "nba", date(2026, 5, 1), espn_events)

        g = session.get(Game, 700)
        assert g.status == "scheduled"
    finally:
        session.close()


def test_reconciliation_matches_unordered_team_pair():
    """Bookmakers and ESPN can disagree on home/away orientation. A row stored
    as MIN @ DEN should match an ESPN event listing it as DEN @ MIN — they're
    the same matchup, not separate games."""
    from backend.database import get_engine, get_session
    from backend.models import Base, Team, Game
    from backend.pipeline.full_pipeline import _store_games

    engine = get_engine(":memory:")
    Base.metadata.create_all(engine)
    session = get_session(engine)
    try:
        session.add_all([
            Team(id=1, name="Denver Nuggets", abbreviation="DEN", sport="nba"),
            Team(id=2, name="Minnesota Timberwolves", abbreviation="MIN", sport="nba"),
            # DB row: home=MIN, away=DEN
            Game(id=800, sport="nba", season="2025-26", date=date(2026, 5, 1),
                 home_team_id=2, away_team_id=1, status="scheduled"),
        ])
        session.commit()

        # ESPN says home=DEN, away=MIN — flipped corners but same matchup.
        espn_events = [_espn_event(
            "DEN", "Denver Nuggets", "MIN", "Minnesota Timberwolves",
        )]
        _store_games(session, "nba", date(2026, 5, 1), espn_events)

        g = session.get(Game, 800)
        # Must NOT be canceled — same teams playing on the same day.
        assert g.status == "scheduled"
    finally:
        session.close()

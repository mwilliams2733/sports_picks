"""Tests for /games/today filter behavior + upsert dedupe in fetch pipeline.

Driving requirement: 'Today's Picks' should only ever surface bets the user
can actually make — live games and future games with odds posted. Stale
phantom rows (no odds, no start_time) and already-graded games must not
leak through.
"""
from datetime import date, datetime, timedelta, timezone
from fastapi.testclient import TestClient

from backend.api.main import create_app
from backend.database import get_session
from backend.models import Base, Team, Game, Odds


def _seed(app, rows: list):
    """Helper: seed Team/Game/Odds rows into a fresh in-memory app.

    Flush in dependency order so SQLite's FK enforcement is happy: Teams
    first, then Games, then Odds.
    """
    engine = app.state.engine
    Base.metadata.create_all(engine)
    session = get_session(engine)
    try:
        teams = [r for r in rows if isinstance(r, Team)]
        games = [r for r in rows if isinstance(r, Game)]
        odds = [r for r in rows if isinstance(r, Odds)]
        if teams:
            session.add_all(teams); session.flush()
        if games:
            session.add_all(games); session.flush()
        if odds:
            session.add_all(odds); session.flush()
        session.commit()
    finally:
        session.close()


def _utc(y, m, d, h=12):
    return datetime(y, m, d, h, 0, tzinfo=timezone.utc)


def test_today_excludes_scheduled_game_with_no_odds():
    """A scheduled game with no Odds rows is not bookable — must not appear."""
    app = create_app(":memory:")
    today = date.today()
    _seed(app, [
        Team(id=1, name="Denver Nuggets", abbreviation="DEN", sport="nba"),
        Team(id=2, name="Minnesota Timberwolves", abbreviation="MIN", sport="nba"),
        Game(id=100, sport="nba", season="2025-26", date=today,
             home_team_id=2, away_team_id=1, status="scheduled"),
    ])
    resp = TestClient(app).get("/games/today")
    assert resp.status_code == 200
    ids = [g["id"] for g in resp.json()]
    assert 100 not in ids


def test_today_includes_scheduled_game_with_odds():
    """A scheduled game with at least one Odds row is bettable — must appear."""
    app = create_app(":memory:")
    today = date.today()
    future = datetime.now(timezone.utc) + timedelta(hours=3)
    _seed(app, [
        Team(id=1, name="Boston Celtics", abbreviation="BOS", sport="nba"),
        Team(id=2, name="Los Angeles Lakers", abbreviation="LAL", sport="nba"),
        Game(id=200, sport="nba", season="2025-26", date=today,
             home_team_id=2, away_team_id=1, status="scheduled",
             start_time=future),
        Odds(game_id=200, bookmaker="dk",
             moneyline_home=-150, moneyline_away=+130,
             spread_home=-3.5, spread_away=3.5, over_under=220.0,
             timestamp=datetime.now(timezone.utc)),
    ])
    resp = TestClient(app).get("/games/today")
    ids = [g["id"] for g in resp.json()]
    assert 200 in ids


def test_today_includes_in_progress_game_even_without_odds():
    """A live game stays visible even if odds rows are gone — user can still
    react to score updates and check the game's pick."""
    app = create_app(":memory:")
    today = date.today()
    _seed(app, [
        Team(id=1, name="Knicks", abbreviation="NYK", sport="nba"),
        Team(id=2, name="Heat", abbreviation="MIA", sport="nba"),
        Game(id=300, sport="nba", season="2025-26", date=today,
             home_team_id=2, away_team_id=1, status="in_progress",
             home_score=58, away_score=62),
    ])
    resp = TestClient(app).get("/games/today")
    ids = [g["id"] for g in resp.json()]
    assert 300 in ids


def test_today_excludes_final_game():
    """A finalized game is not bettable — must not appear in Today's Picks."""
    app = create_app(":memory:")
    today = date.today()
    _seed(app, [
        Team(id=1, name="Bulls", abbreviation="CHI", sport="nba"),
        Team(id=2, name="Bucks", abbreviation="MIL", sport="nba"),
        Game(id=400, sport="nba", season="2025-26", date=today,
             home_team_id=2, away_team_id=1, status="final",
             home_score=110, away_score=98),
        Odds(game_id=400, bookmaker="dk",
             moneyline_home=-200, moneyline_away=+170,
             spread_home=-5.5, spread_away=5.5, over_under=215.0,
             timestamp=datetime.now(timezone.utc)),
    ])
    resp = TestClient(app).get("/games/today")
    ids = [g["id"] for g in resp.json()]
    assert 400 not in ids


def test_today_excludes_scheduled_game_with_long_passed_start_time():
    """If start_time is >4 hours in the past but status is still 'scheduled',
    the pipeline failed to update it — likely a postponement or cancellation.
    Treat as stale and hide."""
    app = create_app(":memory:")
    today = date.today()
    very_past = datetime.now(timezone.utc) - timedelta(hours=8)
    _seed(app, [
        Team(id=1, name="Hawks", abbreviation="ATL", sport="nba"),
        Team(id=2, name="Magic", abbreviation="ORL", sport="nba"),
        Game(id=500, sport="nba", season="2025-26", date=today,
             home_team_id=2, away_team_id=1, status="scheduled",
             start_time=very_past),
        Odds(game_id=500, bookmaker="dk",
             moneyline_home=-110, moneyline_away=-110,
             spread_home=-1.5, spread_away=1.5, over_under=210.0,
             timestamp=datetime.now(timezone.utc)),
    ])
    resp = TestClient(app).get("/games/today")
    ids = [g["id"] for g in resp.json()]
    assert 500 not in ids


def test_today_response_includes_last_meeting_and_l10_records():
    """Each surfaced game carries enough context for the user to spot stale
    series data: last_meeting (most recent prior game between the same teams)
    and last-10 record per team."""
    app = create_app(":memory:")
    today = date.today()
    future = datetime.now(timezone.utc) + timedelta(hours=2)
    rows = [
        Team(id=1, name="Suns", abbreviation="PHX", sport="nba"),
        Team(id=2, name="Warriors", abbreviation="GSW", sport="nba"),
    ]
    # Recent meeting: GSW won 4 days ago.
    rows.append(Game(
        id=600, sport="nba", season="2025-26", date=today - timedelta(days=4),
        home_team_id=2, away_team_id=1, status="final",
        home_score=120, away_score=108,
    ))
    # Each team has 5 prior wins + 5 prior losses → "5-5" L10. Use distinct
    # opponents so head-to-head only counts the one above.
    for i in range(10):
        rows.append(Team(id=100 + i, name=f"Filler{i}", abbreviation=f"F{i}", sport="nba"))
    for i in range(5):
        rows.append(Game(
            id=700 + i, sport="nba", season="2025-26",
            date=today - timedelta(days=10 + i),
            home_team_id=1, away_team_id=100 + i, status="final",
            home_score=110, away_score=100,  # PHX (home) wins
        ))
    for i in range(5):
        rows.append(Game(
            id=750 + i, sport="nba", season="2025-26",
            date=today - timedelta(days=20 + i),
            home_team_id=105 + i, away_team_id=1, status="final",
            home_score=110, away_score=100,  # PHX (away) loses
        ))
    # Today's bettable game.
    rows.append(Game(
        id=999, sport="nba", season="2025-26", date=today,
        home_team_id=1, away_team_id=2, status="scheduled",
        start_time=future,
    ))
    rows.append(Odds(
        game_id=999, bookmaker="dk",
        moneyline_home=-120, moneyline_away=+105,
        spread_home=-2.0, spread_away=2.0, over_under=225.0,
        timestamp=datetime.now(timezone.utc),
    ))
    _seed(app, rows)

    resp = TestClient(app).get("/games/today")
    games = [g for g in resp.json() if g["id"] == 999]
    assert len(games) == 1
    g = games[0]
    assert g["last_meeting"] is not None
    assert g["last_meeting"]["home_score"] == 120
    assert g["last_meeting"]["away_score"] == 108
    assert g["last_meeting"]["winner"] == "away"  # GSW was home, won; home team in TODAY's matchup is PHX → GSW (away today) wins last meeting
    # 5 wins + 5 losses split across home/away.
    assert g["home_l10_record"] == "5-5"


def test_today_last_meeting_is_null_when_no_prior_history():
    """Two teams meeting for the first time → last_meeting is null, not error."""
    app = create_app(":memory:")
    today = date.today()
    future = datetime.now(timezone.utc) + timedelta(hours=2)
    _seed(app, [
        Team(id=1, name="Team A", abbreviation="TA", sport="nba"),
        Team(id=2, name="Team B", abbreviation="TB", sport="nba"),
        Game(id=800, sport="nba", season="2025-26", date=today,
             home_team_id=2, away_team_id=1, status="scheduled",
             start_time=future),
        Odds(game_id=800, bookmaker="dk",
             moneyline_home=-110, moneyline_away=-110,
             spread_home=-1.5, spread_away=1.5, over_under=210.0,
             timestamp=datetime.now(timezone.utc)),
    ])
    resp = TestClient(app).get("/games/today")
    g = next(g for g in resp.json() if g["id"] == 800)
    assert g["last_meeting"] is None
    assert g["home_l10_record"] == "0-0"
    assert g["away_l10_record"] == "0-0"

from datetime import date, datetime, timezone
from fastapi.testclient import TestClient
from backend.api.main import create_app
from backend.models import Base, Game, Team, PlayerProp, PlayerStat
from backend.database import get_session

def test_props_today_includes_analysis():
    app = create_app(":memory:")
    Base.metadata.create_all(app.state.engine)
    session = get_session(app.state.engine)
    home = Team(name="Boston", abbreviation="BOS", sport="nba")
    away = Team(name="Brooklyn", abbreviation="BKN", sport="nba")
    session.add_all([home, away])
    session.commit()
    today = date.today()
    game = Game(sport="nba", season="2025-26", date=today,
                home_team_id=home.id, away_team_id=away.id, status="scheduled")
    session.add(game)
    session.commit()
    session.add(PlayerProp(
        game_id=game.id, bookmaker="dk", market="player_points",
        player_name="Jayson Tatum", outcome="Over", line=25.5, odds=-110,
        fetched_at=datetime.now(tz=timezone.utc),
    ))
    session.add(PlayerStat(
        player_name="Jayson Tatum", team_id=home.id, sport="nba",
        stat_type="season_avg", points=27.5, source="test",
        fetched_at=datetime.now(tz=timezone.utc),
    ))
    session.commit()
    session.close()
    client = TestClient(app)
    resp = client.get("/props/today")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) >= 1
    prop = data[0]
    assert "projection" in prop
    assert "edge_pct" in prop
    assert "confidence" in prop
    assert "source" in prop

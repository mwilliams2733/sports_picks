import json
from datetime import date, datetime, timezone
from fastapi.testclient import TestClient
from backend.api.main import create_app
from backend.models import Base, StrategyModel, Team, PlayerStat
from backend.database import get_engine, get_session

def test_backtest_run_prop():
    app = create_app(":memory:")
    engine = app.state.engine
    Base.metadata.create_all(engine)
    session = get_session(engine)
    strat = StrategyModel(name="prop_value", config_json=json.dumps({
        "recent_weight": 0.6, "season_weight": 0.4, "min_edge": 5.0, "lookback": 5
    }), strategy_type="prop")
    session.add(strat)
    team = Team(name="Test", abbreviation="TST", sport="nba")
    session.add(team)
    session.commit()
    for i in range(15):
        session.add(PlayerStat(
            player_name="Test Player", team_id=team.id, sport="nba",
            stat_type="game_log", game_date=date(2026, 2, 1 + i),
            points=25.0 + i, rebounds=8.0, assists=5.0, minutes=36.0,
            source="test", fetched_at=datetime.now(tz=timezone.utc),
        ))
    session.commit()
    strat_id = strat.id
    session.close()
    client = TestClient(app)
    resp = client.post("/backtest/run", json={
        "strategy_id": strat_id, "start_date": "2026-02-10", "end_date": "2026-02-15",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert "wins" in data
    assert "losses" in data
    assert "hit_rate" in data

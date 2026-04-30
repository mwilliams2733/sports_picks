from datetime import date, datetime, timezone
import pytest
from fastapi.testclient import TestClient
from backend.api.main import create_app
from backend.models import Base, Team, Game, PickModel, PickResult, StrategyModel

def _seed(client):
    engine = client.app.state.engine
    Base.metadata.create_all(engine)
    from backend.database import get_session
    s = get_session(engine)
    s.add_all([
        Team(id=1, name="BOS", abbreviation="BOS", sport="nba"),
        Team(id=2, name="LAL", abbreviation="LAL", sport="nba"),
        Game(id=1, sport="nba", season="2025-26", date=date(2026, 3, 10), home_team_id=1, away_team_id=2, status="final"),
        Game(id=2, sport="nba", season="2025-26", date=date(2026, 3, 11), home_team_id=1, away_team_id=2, status="final"),
        StrategyModel(id=1, name="test", config_json="{}", is_active=True),
        PickModel(id=1, game_id=1, strategy_id=1, pick_type="moneyline", pick_value="BOS ML", confidence=4, edge_pct=8.0, odds_at_pick=-150, created_at=datetime.now(tz=timezone.utc)),
        PickModel(id=2, game_id=2, strategy_id=1, pick_type="moneyline", pick_value="BOS ML", confidence=3, edge_pct=6.0, odds_at_pick=-130, created_at=datetime.now(tz=timezone.utc)),
        PickResult(id=1, pick_id=1, result="win", payout=0.667),
        PickResult(id=2, pick_id=2, result="loss", payout=-1.0),
    ])
    s.commit()
    s.close()

def test_get_record():
    app = create_app(":memory:")
    client = TestClient(app)
    _seed(client)
    resp = client.get("/stats/record")
    assert resp.status_code == 200
    data = resp.json()
    assert data["wins"] == 1
    assert data["losses"] == 1
    assert data["total"] == 2

def test_get_daily():
    app = create_app(":memory:")
    client = TestClient(app)
    _seed(client)
    resp = client.get("/stats/daily")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2


def _seed_clv_mix(client):
    """Seed a moneyline pick (price CLV) + a spread pick (line CLV)."""
    engine = client.app.state.engine
    Base.metadata.create_all(engine)
    from backend.database import get_session
    s = get_session(engine)
    s.add_all([
        Team(id=1, name="BOS", abbreviation="BOS", sport="nba"),
        Team(id=2, name="LAL", abbreviation="LAL", sport="nba"),
        Game(id=1, sport="nba", season="2025-26", date=date(2026, 3, 10),
             home_team_id=1, away_team_id=2, status="final"),
        Game(id=2, sport="nba", season="2025-26", date=date(2026, 3, 11),
             home_team_id=1, away_team_id=2, status="final"),
        StrategyModel(id=1, name="test", config_json="{}", is_active=True),
        # Moneyline: bet at -150, closed at -160 (we got better) -> positive price CLV.
        PickModel(id=1, game_id=1, strategy_id=1, pick_type="moneyline",
                  pick_value="HOME ML", confidence=4, edge_pct=8.0,
                  odds_at_pick=-150, created_at=datetime.now(tz=timezone.utc)),
        # Spread: bet HOME -3.0, closed HOME -3.5 -> took easier line, +0.5 line CLV.
        PickModel(id=2, game_id=2, strategy_id=1, pick_type="spread",
                  pick_value="HOME -3.0", confidence=4, edge_pct=4.0,
                  odds_at_pick=-110, created_at=datetime.now(tz=timezone.utc)),
        PickResult(id=1, pick_id=1, result="win", payout=0.667, odds_at_close=-160),
        PickResult(id=2, pick_id=2, result="win", payout=0.909, odds_at_close=-110, line_at_close=-3.5),
    ])
    s.commit()
    s.close()


def test_calibration_empty_when_no_graded_picks():
    app = create_app(":memory:")
    client = TestClient(app)
    Base.metadata.create_all(client.app.state.engine)
    resp = client.get("/stats/calibration")
    assert resp.status_code == 200
    data = resp.json()
    assert data["tiers"] == []
    assert data["total_graded"] == 0
    assert data["brier_score"] is None


def _seed_calibration(client):
    """Seed picks at multiple confidence tiers with mixed results.

    Tier 5 (expected 70%): 4 wins, 1 loss -> actual 80%
    Tier 3 (expected 57%): 1 win, 1 loss -> actual 50%
    """
    engine = client.app.state.engine
    Base.metadata.create_all(engine)
    from backend.database import get_session
    s = get_session(engine)
    s.add_all([
        Team(id=1, name="A", abbreviation="A", sport="nba"),
        Team(id=2, name="B", abbreviation="B", sport="nba"),
        StrategyModel(id=1, name="t", config_json="{}", is_active=True),
    ])
    # Seven picks across two tiers.
    for i in range(7):
        s.add(Game(id=100 + i, sport="nba", season="2025-26",
                   date=date(2026, 3, 1 + i), home_team_id=1, away_team_id=2, status="final"))
    s.flush()

    tier5_results = ["win", "win", "win", "win", "loss"]
    tier3_results = ["win", "loss"]
    pick_id = 1
    for i, res in enumerate(tier5_results):
        s.add(PickModel(id=pick_id, game_id=100 + i, strategy_id=1, pick_type="moneyline",
                        pick_value="HOME ML", confidence=5, edge_pct=10.0,
                        odds_at_pick=-150, created_at=datetime.now(tz=timezone.utc)))
        s.add(PickResult(id=pick_id, pick_id=pick_id, result=res, payout=0.0))
        pick_id += 1
    for i, res in enumerate(tier3_results):
        s.add(PickModel(id=pick_id, game_id=105 + i, strategy_id=1, pick_type="moneyline",
                        pick_value="HOME ML", confidence=3, edge_pct=5.0,
                        odds_at_pick=-110, created_at=datetime.now(tz=timezone.utc)))
        s.add(PickResult(id=pick_id, pick_id=pick_id, result=res, payout=0.0))
        pick_id += 1
    s.commit()
    s.close()


def test_calibration_returns_per_tier_actual_vs_predicted():
    app = create_app(":memory:")
    client = TestClient(app)
    _seed_calibration(client)

    resp = client.get("/stats/calibration")
    assert resp.status_code == 200
    data = resp.json()

    by_tier = {t["tier"]: t for t in data["tiers"]}
    assert by_tier[5]["sample_size"] == 5
    assert by_tier[5]["predicted_win_rate"] == 0.7
    assert by_tier[5]["actual_win_rate"] == 0.8
    assert by_tier[3]["sample_size"] == 2
    assert by_tier[3]["predicted_win_rate"] == 0.57
    assert by_tier[3]["actual_win_rate"] == 0.5

    # Tiers should be sorted descending so the highest-confidence row is first.
    assert [t["tier"] for t in data["tiers"]] == [5, 3]
    assert data["total_graded"] == 7


def test_calibration_brier_score_is_correct():
    """Brier = mean( (predicted - outcome)^2 ).
    Tier 5 (pred 0.7): 4 wins => (0.7-1)^2 = 0.09 each, 1 loss => (0.7-0)^2 = 0.49
    Tier 3 (pred 0.57): 1 win => (0.57-1)^2 = 0.1849, 1 loss => (0.57-0)^2 = 0.3249
    Total = 0.09*4 + 0.49 + 0.1849 + 0.3249 = 1.3598; / 7 = 0.1943
    """
    app = create_app(":memory:")
    client = TestClient(app)
    _seed_calibration(client)
    resp = client.get("/stats/calibration")
    data = resp.json()
    assert data["brier_score"] == pytest.approx(0.1943, abs=0.001)


def test_calibration_excludes_pushes_from_brier():
    app = create_app(":memory:")
    client = TestClient(app)
    engine = client.app.state.engine
    Base.metadata.create_all(engine)
    from backend.database import get_session
    s = get_session(engine)
    s.add_all([
        Team(id=1, name="A", abbreviation="A", sport="nba"),
        Team(id=2, name="B", abbreviation="B", sport="nba"),
        Game(id=1, sport="nba", season="2025-26", date=date(2026, 3, 10),
             home_team_id=1, away_team_id=2, status="final"),
        StrategyModel(id=1, name="t", config_json="{}", is_active=True),
        PickModel(id=1, game_id=1, strategy_id=1, pick_type="spread",
                  pick_value="HOME -3.0", confidence=4, edge_pct=4.0,
                  odds_at_pick=-110, created_at=datetime.now(tz=timezone.utc)),
        PickResult(id=1, pick_id=1, result="push", payout=0.0),
    ])
    s.commit()
    s.close()
    resp = client.get("/stats/calibration")
    data = resp.json()
    assert data["tiers"] == []
    assert data["brier_score"] is None


def test_clv_separates_price_and_line():
    """Price CLV should reflect only moneyline picks; line CLV only spread/total."""
    app = create_app(":memory:")
    client = TestClient(app)
    _seed_clv_mix(client)
    resp = client.get("/stats/clv")
    assert resp.status_code == 200
    data = resp.json()

    assert data["price_clv"]["total_picks"] == 1
    assert data["price_clv"]["clv_positive"] == 1  # -150 -> -160 is positive

    assert data["line_clv"]["total_picks"] == 1
    # HOME -3.0 vs close HOME -3.5: pick - close = -3.0 - (-3.5) = +0.5
    assert data["line_clv"]["avg_clv"] == 0.5
    assert data["line_clv"]["clv_positive"] == 1

    # Backwards-compat top-level keys still reflect price CLV.
    assert data["total_picks"] == data["price_clv"]["total_picks"]

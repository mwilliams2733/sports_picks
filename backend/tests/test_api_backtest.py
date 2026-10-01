from fastapi.testclient import TestClient
from backend.api.main import create_app
from backend.models import Base, StrategyModel
from backend.tests.auth_helpers import OWNER_HEADERS

def _seed(client):
    engine = client.app.state.engine
    Base.metadata.create_all(engine)
    from backend.database import get_session
    s = get_session(engine)
    s.add(StrategyModel(id=1, name="ensemble", description="test", config_json='{"min_edge": 5}', is_active=True))
    s.add(StrategyModel(id=2, name="value_only", description="test2", config_json='{"min_edge": 10}', is_active=False))
    s.commit()
    s.close()

def test_list_strategies():
    app = create_app(":memory:")
    client = TestClient(app, headers=OWNER_HEADERS)
    _seed(client)
    resp = client.get("/backtest/strategies")
    assert resp.status_code == 200
    # 2 seeded here + the `combat_sports` row run_migrations always ensures
    # exists (create_app runs it against every engine, including this one).
    assert len(resp.json()) == 3

def test_create_strategy():
    app = create_app(":memory:")
    client = TestClient(app, headers=OWNER_HEADERS)
    Base.metadata.create_all(client.app.state.engine)
    resp = client.post("/backtest/strategies", json={"name": "new_strat", "description": "desc", "config": {"min_edge": 7}})
    assert resp.status_code == 201
    assert resp.json()["name"] == "new_strat"

def test_promote_strategy():
    app = create_app(":memory:")
    client = TestClient(app, headers=OWNER_HEADERS)
    _seed(client)
    resp = client.patch("/backtest/strategies/2/promote")
    assert resp.status_code == 200
    strats = client.get("/backtest/strategies").json()
    active = [s for s in strats if s["is_active"]]
    assert len(active) == 1
    assert active[0]["id"] == 2


# --- promote: one active strategy per KIND, never the combat row ------------
#
# Every consumer (scheduler, /pipeline, prop pipeline, fetch_odds_now) takes
# "the" active strategy of a strategy_type with `.first()` and ignores
# `sport`. promote used to deactivate by `sport` instead -- and both live
# strategies have sport NULL, so promoting prop_value switched ensemble off
# (no game picks, no digest) and promoting ensemble switched prop_value off.

def _seed_live_shape(client):
    """The live table on 2026-09-30: ensemble (game) and prop_value (prop),
    both sport NULL and active, plus the inactive combat_sports row that
    run_migrations always adds."""
    engine = client.app.state.engine
    Base.metadata.create_all(engine)
    from backend.database import get_session
    s = get_session(engine)
    s.add_all([
        StrategyModel(id=1, name="ensemble", config_json="{}", is_active=True,
                      strategy_type="game"),
        StrategyModel(id=2, name="prop_value", config_json="{}", is_active=True,
                      strategy_type="prop"),
        StrategyModel(id=3, name="value_only", config_json="{}", is_active=False,
                      strategy_type="game", sport="nfl"),
    ])
    s.commit()
    s.close()


def _active(client):
    return {s["name"] for s in client.get("/backtest/strategies").json() if s["is_active"]}


def _client():
    return TestClient(create_app(":memory:"), headers=OWNER_HEADERS)


def test_promoting_the_prop_strategy_leaves_the_game_strategy_on():
    client = _client()
    _seed_live_shape(client)
    assert client.patch("/backtest/strategies/2/promote").status_code == 200
    assert _active(client) == {"ensemble", "prop_value"}


def test_promoting_a_game_strategy_replaces_the_game_strategy_only():
    # value_only carries sport="nfl"; ensemble has none. Kind decides, not sport.
    client = _client()
    _seed_live_shape(client)
    assert client.patch("/backtest/strategies/3/promote").status_code == 200
    assert _active(client) == {"value_only", "prop_value"}


def test_combat_sports_cannot_be_promoted():
    # Active, it would win the `.first()` "the game strategy" lookup and be
    # used for every sport; it is routed by name and must stay inactive.
    client = _client()
    _seed_live_shape(client)
    resp = client.patch("/backtest/strategies/999999/promote")
    assert resp.status_code == 400
    assert "routed automatically" in resp.json()["detail"]
    assert _active(client) == {"ensemble", "prop_value"}


def test_a_strategy_the_pipeline_cannot_run_is_refused():
    # generate_and_store_picks returns 0 for a name not in STRATEGY_MAP:
    # promoting one would silently end all game picks.
    client = _client()
    _seed_live_shape(client)
    made = client.post("/backtest/strategies", json={
        "name": "my_idea", "description": "d", "config": {}}).json()
    resp = client.patch(f"/backtest/strategies/{made['id']}/promote")
    assert resp.status_code == 400
    assert _active(client) == {"ensemble", "prop_value"}


def test_an_unknown_strategy_kind_cannot_be_promoted():
    client = _client()
    _seed_live_shape(client)
    made = client.post("/backtest/strategies", json={
        "name": "ensemble", "description": "d", "config": {},
        "strategy_type": "gmae"}).json()
    assert client.patch(f"/backtest/strategies/{made['id']}/promote").status_code == 400
    assert _active(client) == {"ensemble", "prop_value"}


def test_a_game_model_cannot_be_promoted_as_the_prop_strategy():
    client = _client()
    _seed_live_shape(client)
    made = client.post("/backtest/strategies", json={
        "name": "ensemble", "description": "d", "config": {},
        "strategy_type": "prop"}).json()
    assert client.patch(f"/backtest/strategies/{made['id']}/promote").status_code == 400
    assert _active(client) == {"ensemble", "prop_value"}

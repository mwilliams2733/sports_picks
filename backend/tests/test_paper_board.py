"""The sportsbook board, GET /paper/board (spec 2026-10-07 §5)."""
import itertools
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from backend.api.main import create_app
from backend.database import get_session
from backend.models import (WEATHER_RAIN_UNDER_STRATEGY_ID, Base, Game, PickModel, PlayerProp,
                            StrategyModel as Strategy, Team)
from backend.tests.auth_helpers import ALL_HEADERS
from backend.tests.pricing_helpers import seed_fresh_odds, seed_fresh_prop
from backend.time_utils import et_today

_n = itertools.count()


def _client(headers=ALL_HEADERS):
    client = TestClient(create_app(":memory:"), headers=headers)
    Base.metadata.create_all(client.app.state.engine)
    return client


def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _game(client, *, sport="nfl", day_offset=0, start=None, status="scheduled"):
    s = get_session(client.app.state.engine)
    i = next(_n)
    h = Team(name=f"Home{i}", abbreviation=f"Home{i}", sport=sport)
    a = Team(name=f"Away{i}", abbreviation=f"Away{i}", sport=sport)
    s.add_all([h, a])
    s.flush()
    g = Game(sport=sport, season="2026", date=et_today() + timedelta(days=day_offset),
             start_time=start, home_team_id=h.id, away_team_id=a.id, status=status)
    s.add(g)
    s.commit()
    gid = g.id
    s.close()
    return gid


def _ids(client, query=""):
    r = client.get(f"/paper/board{query}")
    assert r.status_code == 200
    return [g["id"] for g in r.json()["games"]]


def test_board_quotes_are_exactly_the_bet_quotes():
    client = _client()
    gid = _game(client)
    seed_fresh_odds(client.app.state.engine, gid)
    [game] = client.get("/paper/board").json()["games"]
    assert game["quotes"] == client.get(f"/paper/quotes?game_id={gid}").json()["quotes"]
    s = get_session(client.app.state.engine)
    expected_home = s.get(Team, s.get(Game, gid).home_team_id).abbreviation
    s.close()
    assert (game["home_team"], game["sport"]) == (expected_home, "nfl")


def test_an_unpriced_game_is_listed_with_every_side_refused():
    client = _client()
    gid = _game(client)                                    # no odds at all
    [game] = client.get("/paper/board").json()["games"]
    assert game["id"] == gid
    assert len(game["quotes"]) == 6
    assert all(q["available"] is False and q["message"] for q in game["quotes"])


def test_started_and_finished_games_are_off_the_board():
    client = _client()
    open_ = _game(client, start=_utcnow() + timedelta(hours=2))
    _game(client, start=_utcnow() - timedelta(hours=1))   # kicked off
    _game(client, status="final")
    _game(client, status="in_progress")
    assert _ids(client) == [open_]


def test_days_window_is_today_onward_and_clamped_to_1_through_14():
    client = _client()
    _game(client, day_offset=-1)                           # yesterday, never shown
    g0 = _game(client, day_offset=0)
    g3 = _game(client, day_offset=3)
    g10 = _game(client, day_offset=10)
    _game(client, day_offset=20)
    assert _ids(client) == [g0, g3]                        # default 7
    assert _ids(client, "?days=99") == [g0, g3, g10]       # clamped to 14
    assert _ids(client, "?days=0") == [g0]                 # clamped to 1


def test_order_is_date_then_kickoff_with_unknown_kickoff_last():
    client = _client()
    late = _game(client, start=_utcnow() + timedelta(hours=5))
    tbd = _game(client, start=None)
    early = _game(client, start=_utcnow() + timedelta(hours=2))
    tomorrow = _game(client, day_offset=1, start=_utcnow() + timedelta(hours=1))
    games = client.get("/paper/board").json()["games"]
    assert [g["id"] for g in games] == [early, late, tbd, tomorrow]
    by_id = {g["id"]: g for g in games}
    assert by_id[tbd]["start_time"] is None
    assert by_id[early]["start_time"].endswith("+00:00")   # UTC, offset explicit


def test_sport_filter():
    client = _client()
    nfl = _game(client, sport="nfl")
    _game(client, sport="nba")
    assert _ids(client, "?sport=nfl") == [nfl]


def test_model_pick_is_the_highest_edge_published_model_game_pick():
    client = _client()
    gid = _game(client)
    other = _game(client)
    s = get_session(client.app.state.engine)
    s.add(Strategy(id=1, name="ensemble", config_json="{}"))   # the rain strategy is seeded
    s.flush()

    def pick(ptype, value, edge, **kw):
        s.add(PickModel(game_id=gid, strategy_id=kw.pop("strategy_id", 1), pick_type=ptype,
                        pick_value=value, confidence=3, edge_pct=edge, odds_at_pick=-110, **kw))

    pick("moneyline", "HOME ML", 3.0)
    pick("over_under", "Over 47.5", 4.0)
    pick("spread", "AWAY +3.5", 5.0, tracking_only=True)
    pick("spread", "HOME -3.5", 7.0, withdrawn_at=_utcnow())
    pick("over_under", "Under 47.5", 8.0, strategy_id=WEATHER_RAIN_UNDER_STRATEGY_ID)
    pick("prop", "QB Over 225.5", 9.0, prop_player="QB", prop_market="player_pass_yds")
    s.commit()
    s.close()
    by_id = {g["id"]: g for g in client.get("/paper/board").json()["games"]}
    view = by_id[gid]["model_pick"]
    assert {k: v for k, v in view.items() if k != "reasoning"} == {
        "pick_type": "over_under", "pick_value": "Over 47.5", "odds": -110, "edge_pct": 4.0}
    assert view["reasoning"] is None          # no model_prob stored: nothing to explain
    assert by_id[other]["model_pick"] is None


def test_prop_count_is_not_computed_on_the_board():
    """Counting priced props per game took the 7-day board over its 1 s budget."""
    client = _client()
    gid = _game(client)
    engine = client.app.state.engine
    seed_fresh_prop(engine, gid, outcome="Over")
    seed_fresh_prop(engine, gid, outcome="Under")
    s = get_session(engine)
    s.add(PlayerProp(game_id=gid, bookmaker="testbook", market="player_pass_yds",
                     player_name="Stale QB", outcome="Over", line=200.5, odds=-110,
                     fetched_at=_utcnow() - timedelta(hours=7)))
    s.commit()
    s.close()
    [game] = client.get("/paper/board").json()["games"]
    assert game["prop_count"] is None


def test_board_is_an_open_read():
    client = _client(headers={})                           # no owner key, no PIN
    _game(client)
    r = client.get("/paper/board")
    assert r.status_code == 200 and len(r.json()["games"]) == 1   # JSON, not the SPA's HTML


def test_model_pick_carries_its_reasoning():
    client = _client()
    gid = _game(client)
    s = get_session(client.app.state.engine)
    s.add(Strategy(id=1, name="ensemble", config_json="{}"))
    s.flush()
    s.add(PickModel(game_id=gid, strategy_id=1, pick_type="moneyline", pick_value="HOME ML",
                    confidence=3, edge_pct=10.7, odds_at_pick=-110, model_prob=0.58,
                    market_prob_novig=0.52, suggested_unit_size=0.87,
                    rationale_json='[{"code": "rating_gap", "side": "home", "strength": "slight"}]'))
    s.commit()
    s.close()
    game = next(g for g in client.get("/paper/board").json()["games"] if g["id"] == gid)
    r = game["model_pick"]["reasoning"]
    home = game["home_team"]
    assert {k: r[k] for k in ("model_prob", "market_prob", "edge_pct", "fair_odds", "units")} == {
        "model_prob": 0.58, "market_prob": 0.52, "edge_pct": 10.7, "fair_odds": -138, "units": 0.87}
    assert r["note"] == (
        f"The model gives {home} a 58% chance to win; the books' price, with their margin removed, "
        f"says 52%. At -110 that is a 10.7% edge (the model's fair price -138). Rating gap slightly favors "
        f"{home}. The model has not shown an edge over NFL closing lines yet, so treat this as one "
        f"opinion, not a sure thing.")

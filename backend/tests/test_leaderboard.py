"""Friends and the model on one board, ranked by ROI; one win-rate definition."""
from datetime import date

import pytest
from fastapi.testclient import TestClient

from backend.api.main import create_app
from backend.database import get_session
from backend.models import (EmailedPick, Game, PaperPick, Parlay, PickModel,
                            StrategyModel, Team, UserProfile)

D = date(2026, 9, 28)


def _app():
    app = create_app(":memory:")
    s = get_session(app.state.engine)
    s.add(StrategyModel(id=1, name="x", config_json="{}", is_active=True))
    s.add_all([Team(id=1, name="H", abbreviation="H", sport="nfl"),
               Team(id=2, name="A", abbreviation="A", sport="nfl")])
    s.commit()
    s.close()
    return app


def _game(s, home=24, away=17):
    g = Game(sport="nfl", season="2026", date=D, home_team_id=1, away_team_id=2,
             status="final", home_score=home, away_score=away)
    s.add(g)
    s.flush()
    return g


def _player(s, name, results, stake=100.0, odds=-110):
    u = UserProfile(name=name)
    s.add(u)
    s.flush()
    for r in results:
        g = _game(s)
        payout = {"win": stake * 100 / 110, "loss": -stake, "push": 0.0, None: None}[r]
        s.add(PaperPick(user_id=u.id, game_id=g.id, pick_type="moneyline",
                        pick_value="HOME ML", odds=odds, stake=stake, result=r,
                        payout=payout))
    s.commit()
    return u.id


def _model(s, results):
    for i, r in enumerate(results):
        g = _game(s, home=24 if r == "win" else 10)
        p = PickModel(game_id=g.id, strategy_id=1, pick_type="moneyline",
                      pick_value="HOME ML", confidence=4, edge_pct=5.0, odds_at_pick=-110)
        s.add(p)
        s.flush()
        s.add(EmailedPick(digest_date=D, pick_id=p.id, game_id=g.id, sport="nfl",
                          pick_type="moneyline", pick_value="HOME ML", odds=-110,
                          confidence=4))
    s.commit()


def _board(app):
    return {r["name"]: r for r in TestClient(app).get("/users/leaderboard").json()}


def test_the_route_is_not_shadowed_by_user_id():
    assert TestClient(_app()).get("/users/leaderboard").status_code == 200


def test_the_model_is_a_player():
    app = _app()
    s = get_session(app.state.engine)
    _model(s, ["win", "loss"])
    s.close()
    row = _board(app)["Model"]
    assert row["is_model"] is True and row["id"] is None
    assert (row["wins"], row["losses"]) == (1, 1)


def test_fewer_than_ten_settled_bets_is_unranked_and_sorts_last():
    app = _app()
    s = get_session(app.state.engine)
    _player(s, "hot", ["win"] * 3)                      # 100% ROI-ish, only 3 bets
    _player(s, "steady", ["win"] * 6 + ["loss"] * 4)    # 10 bets
    s.close()
    names = [r["name"] for r in TestClient(app).get("/users/leaderboard").json()]
    board = _board(app)
    assert board["hot"]["ranked"] is False and board["steady"]["ranked"] is True
    assert names.index("steady") < names.index("hot")


def test_ranked_by_roi_not_balance():
    app = _app()
    s = get_session(app.state.engine)
    _player(s, "big", ["win"] * 6 + ["loss"] * 4, stake=1000)   # same ROI, 10x money
    _player(s, "sharp", ["win"] * 8 + ["loss"] * 2, stake=10)
    s.close()
    names = [r["name"] for r in TestClient(app).get("/users/leaderboard").json()]
    assert names.index("sharp") < names.index("big")


def test_seven_and_three_does_not_outrank_forty_and_thirty():
    """Brief G.2: a hot start must not crown itself. Shrunk ROI: 7-3 -> 0.0182,
    40-30 -> 0.0341 (both at -110), though 7-3's raw ROI is 0.336."""
    app = _app()
    s = get_session(app.state.engine)
    _player(s, "hot", ["win"] * 7 + ["loss"] * 3)
    _player(s, "long", ["win"] * 40 + ["loss"] * 30)
    s.close()
    rows = TestClient(app).get("/users/leaderboard").json()
    names = [r["name"] for r in rows]
    board = {r["name"]: r for r in rows}
    assert names.index("long") < names.index("hot")
    assert board["hot"]["roi"] == pytest.approx(0.3364, abs=1e-4)
    assert board["hot"]["shrunk_roi"] == pytest.approx(0.0182, abs=1e-4)
    assert board["long"]["shrunk_roi"] == pytest.approx(0.0341, abs=1e-4)


def test_parlays_are_off_the_board_but_in_personal_stats():
    app = _app()
    s = get_session(app.state.engine)
    u = UserProfile(name="parlayer")
    s.add(u)
    s.flush()
    p = Parlay(user_id=u.id, stake=100, combined_odds=264, result="win", payout=264.46)
    s.add(p)
    s.flush()
    for _ in range(2):
        g = _game(s)
        s.add(PaperPick(user_id=u.id, game_id=g.id, pick_type="moneyline",
                        pick_value="HOME ML", odds=-110, stake=0, result="win",
                        payout=0, parlay_id=p.id))
    s.commit()
    uid = u.id
    s.close()
    row = _board(app)["parlayer"]
    assert row["n"] == 0 and row["wins"] == 0          # brief G.3: not on the board
    stats = TestClient(app).get(f"/users/{uid}/stats").json()["all_time"]
    assert (stats["wins"], stats["total"]) == (1, 1)   # once, legs never
    assert stats["profit"] == pytest.approx(264.46)


def test_one_definition_of_win_rate_and_roi_everywhere():
    """The same record through three routes. All call backend.analysis.
    scorecard; this checks the wiring, not a second formula."""
    app = _app()
    s = get_session(app.state.engine)
    _model(s, ["win", "win", "loss"])
    uid = _player(s, "mirror", ["win", "win", "loss"])      # same odds, 100x stake
    s.close()
    c = TestClient(app)
    board = _board(app)
    emailed = c.get("/stats/emailed?kind=game&by=week").json()["total"]
    stats = c.get(f"/users/{uid}/stats").json()["all_time"]
    assert board["Model"]["win_rate"] == emailed["win_rate"] == board["mirror"]["win_rate"]
    assert board["Model"]["roi"] == pytest.approx(board["mirror"]["roi"], abs=1e-4)
    assert stats["win_rate"] == pytest.approx(board["mirror"]["win_rate"] * 100, abs=0.1)
    assert stats["roi"] == pytest.approx(board["mirror"]["roi"] * 100, abs=0.01)


def test_player_stats_win_rate_excludes_pushes():
    """The visible change the spec calls out: wins / (W+L), not / (W+L+P)."""
    app = _app()
    s = get_session(app.state.engine)
    uid = _player(s, "pusher", ["win", "loss", "push"])
    s.close()
    stats = TestClient(app).get(f"/users/{uid}/stats").json()["all_time"]
    assert stats["win_rate"] == 50.0 and stats["total"] == 3


def test_parlay_only_player_counts_once_in_list_and_get_user():
    """Carry-over from Task 6's review: total_wagered/wins/roi in list_users
    and get_user must come from summarize(player_bets(...)) -- the parlay's
    own stake counted once, never its legs (stake 0)."""
    app = _app()
    s = get_session(app.state.engine)
    u = UserProfile(name="parlayer2")
    s.add(u)
    s.flush()
    p = Parlay(user_id=u.id, stake=100, combined_odds=264, result="win", payout=264.46)
    s.add(p)
    s.flush()
    for _ in range(2):
        g = _game(s)
        s.add(PaperPick(user_id=u.id, game_id=g.id, pick_type="moneyline",
                        pick_value="HOME ML", odds=-110, stake=0, result="win",
                        payout=0, parlay_id=p.id))
    s.commit()
    uid = u.id
    s.close()
    c = TestClient(app)
    listed = next(x for x in c.get("/users/").json() if x["id"] == uid)
    got = c.get(f"/users/{uid}").json()
    for row in (listed, got):
        assert row["wins"] == 1
        assert row["total_wagered"] == pytest.approx(100.0)
        assert row["roi"] == pytest.approx(row["profit"] / 100.0 * 100, abs=0.01)

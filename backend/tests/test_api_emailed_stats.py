"""The emailed record over HTTP. Numbers come from backend.analysis.scorecard."""
from datetime import date

import pytest
from fastapi.testclient import TestClient

from backend.api.main import create_app
from backend.database import get_session
from backend.models import EmailedPick, Game, PickModel, StrategyModel, Team

SUN, MON = date(2026, 9, 27), date(2026, 9, 28)


def _client(rows):
    """rows: (digest_date, pick_type, pick_value, odds, stars, home, away).
    home/away None -> the game has not finished."""
    app = create_app(":memory:")
    s = get_session(app.state.engine)
    s.add(StrategyModel(id=1, name="x", config_json="{}", is_active=True))
    s.add_all([Team(id=1, name="H", abbreviation="H", sport="nfl"),
               Team(id=2, name="A", abbreviation="A", sport="nfl")])
    s.flush()
    for i, (d, ptype, value, odds, stars, home, away) in enumerate(rows, start=1):
        final = home is not None
        s.add(Game(id=i, sport="nfl", season="2026", date=d, home_team_id=1,
                   away_team_id=2, status="final" if final else "scheduled",
                   home_score=home, away_score=away))
        s.flush()
        s.add(PickModel(id=i, game_id=i, strategy_id=1, pick_type=ptype,
                        pick_value=value, confidence=stars or 1, edge_pct=5.0,
                        odds_at_pick=odds))
        s.flush()
        s.add(EmailedPick(digest_date=d, pick_id=i, game_id=i, sport="nfl",
                          pick_type=ptype, pick_value=value, odds=odds,
                          confidence=stars))
    s.commit()
    s.close()
    return TestClient(app)


def test_no_emailed_picks_is_an_empty_record_not_an_error():
    """Review Focus 3: day one of recording."""
    body = _client([]).get("/stats/emailed?kind=game&by=week").json()
    assert body["groups"] == []
    assert body["total"]["n"] == 0 and body["total"]["win_rate"] is None


def test_by_stars():
    c = _client([
        (MON, "moneyline", "HOME ML", -110, 5, 24, 17),   # win
        (MON, "moneyline", "HOME ML", -110, 5, 10, 17),   # loss
        (MON, "moneyline", "AWAY ML", 150, 3, 10, 17),    # win
    ])
    body = c.get("/stats/emailed?kind=game&by=stars").json()
    assert [g["label"] for g in body["groups"]] == ["5", "3"]
    five = body["groups"][0]
    assert (five["wins"], five["losses"]) == (1, 1)
    assert five["win_rate"] == 0.5
    assert five["break_even"] == pytest.approx(0.5238, abs=1e-4)
    assert (body["total"]["wins"], body["total"]["losses"]) == (2, 1)


def test_by_week_newest_first():
    c = _client([(SUN, "moneyline", "HOME ML", -110, 4, 24, 17),
                 (MON, "moneyline", "HOME ML", -110, 4, 24, 17)])
    labels = [g["label"] for g in c.get("/stats/emailed?kind=game&by=week").json()["groups"]]
    assert labels == ["2026-09-28", "2026-09-21"]


def test_pending_is_counted_not_graded():
    body = _client([(MON, "moneyline", "HOME ML", -110, 4, None, None)]) \
        .get("/stats/emailed?kind=game&by=week").json()
    assert body["total"]["pending"] == 1 and body["total"]["n"] == 0


def test_props_and_game_picks_never_mix():
    c = _client([(MON, "moneyline", "HOME ML", -110, 4, 24, 17),
                 (MON, "prop", "Q Over 200.5 Pass Yards", -110, 5, None, None)])
    game = c.get("/stats/emailed?kind=game&by=week").json()["total"]
    prop = c.get("/stats/emailed?kind=prop&by=week").json()["total"]
    assert (game["wins"], game["pending"]) == (1, 0)
    assert (prop["wins"], prop["pending"]) == (0, 1)


def test_bad_parameters_are_422():
    c = _client([])
    assert c.get("/stats/emailed?kind=both").status_code == 422
    assert c.get("/stats/emailed?by=year").status_code == 422


def test_trend():
    c = _client([(SUN, "moneyline", "HOME ML", -110, 4, 10, 17),   # loss
                 (MON, "moneyline", "HOME ML", -110, 4, 24, 17)])  # win
    body = c.get("/stats/emailed/trend?kind=game").json()
    assert [p["date"] for p in body["points"]] == ["2026-09-27", "2026-09-28"]
    assert body["points"][0]["units"] == -1.0
    assert body["max_drawdown"] == 1.0
    assert body["longest_losing_streak"] == 1

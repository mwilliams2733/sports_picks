"""Fixtures for the cash-out tests (sportsbook spec 2026-10-07 §9).

Games are scheduled NFL games dated today with no start time, so they are
open for betting, quoted by one fresh book: ML -110/-110, spread -3.5/+3.5,
total 220.5, all -110 (pricing_helpers.ODDS_DEFAULTS)."""
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from backend.api.main import create_app
from backend.database import get_session
from backend.models import Base, Game, Odds, PaperPick, Parlay, PlayerProp, Team
from backend.paper import cashout
from backend.tests.auth_helpers import ALL_HEADERS, TEST_PIN
from backend.tests.pricing_helpers import seed_fresh_odds
from backend.time_utils import et_today


def client():
    c = TestClient(create_app(":memory:"), headers=ALL_HEADERS)
    Base.metadata.create_all(c.app.state.engine)
    return c


def games(c, n=1, **odds):
    s = get_session(c.app.state.engine)
    ids = []
    for i in range(n):
        h = Team(name=f"H{i}", abbreviation=f"H{i}", sport="nfl")
        a = Team(name=f"A{i}", abbreviation=f"A{i}", sport="nfl")
        s.add_all([h, a])
        s.flush()
        g = Game(sport="nfl", season="2026", date=et_today(), home_team_id=h.id,
                 away_team_id=a.id, status="scheduled")
        s.add(g)
        s.flush()
        ids.append(g.id)
    s.commit()
    s.close()
    for gid in ids:
        seed_fresh_odds(c.app.state.engine, gid, **odds)
    return ids


def user(c, name="sam"):
    r = c.post("/users/", json={"name": name, "pin": TEST_PIN})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def place(c, uid, gid, stake=100, **leg):
    r = c.post(f"/users/{uid}/picks", json={"game_id": gid, "stake": stake, **leg})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def place_parlay(c, uid, legs, stake=20):
    r = c.post(f"/users/{uid}/parlay", json={"legs": legs, "stake": stake})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def reprice(c, gid, **fields):
    """Change what the game's book quotes now (every Odds row for it)."""
    s = get_session(c.app.state.engine)
    for o in s.query(Odds).filter(Odds.game_id == gid):
        for k, v in fields.items():
            setattr(o, k, v)
    s.commit()
    s.close()


def set_game(c, gid, **fields):
    s = get_session(c.app.state.engine)
    g = s.get(Game, gid)
    for k, v in fields.items():
        setattr(g, k, v)
    s.commit()
    s.close()


def set_row(c, model, row_id, **fields):
    s = get_session(c.app.state.engine)
    r = s.get(model, row_id)
    for k, v in fields.items():
        setattr(r, k, v)
    s.commit()
    s.close()


def offer_of(c, kind, bet_id):
    s = get_session(c.app.state.engine)
    try:
        row = s.get(PaperPick if kind == "straight" else Parlay, bet_id)
        return cashout.offer(s, kind, row)
    finally:
        s.close()


def reason_of(c, kind, bet_id):
    try:
        offer_of(c, kind, bet_id)
    except cashout.CashOutUnavailable as e:
        return e.reason
    return None


def stale(c, gid):
    reprice(c, gid, timestamp=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=7))


def props(c, gid):
    """Both sides of QB One's passing-yards prop at 225.5, -110 each."""
    s = get_session(c.app.state.engine)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    for outcome in ("Over", "Under"):
        s.add(PlayerProp(game_id=gid, bookmaker="testbook", market="player_pass_yds",
                         player_name="QB One", outcome=outcome, line=225.5, odds=-110, fetched_at=now))
    s.commit()
    s.close()

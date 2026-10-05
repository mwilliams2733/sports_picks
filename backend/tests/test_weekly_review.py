"""The weekly review's own joins: Claude vs the model, and what counts as a model pick."""
from datetime import date, datetime, timezone

import pytest
from sqlalchemy import create_engine

from backend.database import get_session
from backend.models import (Base, Game, PaperPick, PickModel, PickResult, Team,
                            UserProfile)
from backend.scripts import weekly_review as wr

NOW = datetime(2026, 10, 4, tzinfo=timezone.utc)
END = date(2026, 10, 11)


@pytest.fixture
def session():
    s = get_session(create_engine("sqlite:///:memory:"))
    Base.metadata.create_all(s.get_bind())
    s.add_all([Team(id=1, name="H", abbreviation="H", sport="nfl"),
               Team(id=2, name="A", abbreviation="A", sport="nfl"),
               UserProfile(id=wr.CLAUDE_USER_ID, name="Claude")])
    for gid in range(1, 6):
        s.add(Game(id=gid, sport="nfl", season="2026-27", date=date(2026, 10, 4),
                   status="final", home_team_id=1, away_team_id=2))
    s.commit()
    yield s
    s.close()


def _model(s, gid, value, pick_type="spread", tracking=False, withdrawn=False,
           result=None, payout=0.0, prob=None, game_date=None):
    p = PickModel(game_id=gid, strategy_id=1, pick_type=pick_type, pick_value=value,
                  confidence=1, edge_pct=5.0, odds_at_pick=-110, tracking_only=tracking,
                  withdrawn_at=NOW if withdrawn else None, model_prob=prob, created_at=NOW)
    s.add(p)
    s.flush()
    if result:
        s.add(PickResult(pick_id=p.id, result=result, payout=payout))
    return p


def _claude(s, gid, value, result=None):
    s.add(PaperPick(user_id=wr.CLAUDE_USER_ID, game_id=gid, pick_type="spread",
                    pick_value=value, odds=-110, stake=100.0, result=result,
                    payout=90.91 if result == "win" else -100.0 if result == "loss" else None))


def test_head_to_head_sorts_agree_against_and_no_side(session):
    _model(session, 1, "HOME -3.5");               _claude(session, 1, "HOME -3")
    _model(session, 2, "HOME ML", "moneyline");    _claude(session, 2, "AWAY +7", result="win")
    _model(session, 3, "Under 44.5", "over_under"); _claude(session, 3, "HOME -1")
    _model(session, 4, "AWAY +3", withdrawn=True); _claude(session, 4, "HOME -3")
    session.commit()

    lines = wr.head_to_head(session, END, END)

    assert "agree 1, against 1, model had no side 2" in lines[0]
    assert "Claude 1-0, model's side 0-1" in lines[1]


def test_model_bets_split_published_and_tracking_and_drop_withdrawn(session):
    _model(session, 1, "HOME ML", "moneyline", result="win", payout=0.91)
    _model(session, 2, "HOME -3", tracking=True, result="loss", payout=-1.0)
    _model(session, 3, "AWAY ML", "moneyline", withdrawn=True, result="win", payout=1.5)
    session.commit()

    published = wr.model_bets(session, tracking=False)
    tracking = wr.model_bets(session, tracking=True)

    assert [(b.result, b.profit) for b in published] == [("win", 0.91)]
    assert [b.result for b in tracking] == ["loss"]


def _prop(s, gid, prob, result, game_day):
    s.query(Game).filter(Game.id == gid).update({"date": game_day})
    _model(s, gid, "QB Over 240.5 Pass Yards", "prop", result=result, prob=prob)


def test_prop_calibration_never_pools_across_the_matchup_change(session):
    _prop(session, 1, 0.70, "win", date(2026, 10, 4))
    _prop(session, 2, 0.70, "loss", date(2026, 10, 5))
    _prop(session, 3, None, "win", date(2026, 10, 4))
    session.commit()

    lines = wr.prop_calibration(session, END, END)

    assert lines[0].strip().startswith("1 graded NFL props predate")
    before = next(line for line in lines if "before" in line)
    after = next(line for line in lines if "from 2026-10-05" in line)
    assert "n    1" in before and "hit 1.000" in before
    assert "n    1" in after and "hit 0.000" in after

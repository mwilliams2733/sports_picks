"""Football props are tracking picks until their projection is fixed.

`player_stats` "season_avg" rows for nfl/ncaaf hold season TOTALS, read as
per-game, so 451 graded football props predicted 0.818 and hit 0.494 with
no signal at any confidence (2026-10-04). Until fixed they are generated
and graded but never published -- `tracking_only`, the flag spreads and
totals already use. A prop already emailed stays published: it is advice
people were sent.
"""
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine

from backend.data_types import PropAnalysis
from backend.database import get_session
from backend.models import Base, EmailedPick, Game, PickModel, Team
from backend.pipeline.prop_pipeline import PROP_TRACKED_SPORTS, _store_prop_picks

DAY = date(2026, 10, 4)


@pytest.fixture
def session():
    s = get_session(create_engine("sqlite:///:memory:"))
    Base.metadata.create_all(s.get_bind())
    start = (datetime.now(timezone.utc) + timedelta(hours=3)).replace(tzinfo=None)
    for gid, sport in ((1, "nfl"), (2, "ncaaf"), (3, "mlb")):
        s.add_all([Team(id=gid * 10, name=f"H{gid}", abbreviation=f"H{gid}", sport=sport),
                   Team(id=gid * 10 + 1, name=f"A{gid}", abbreviation=f"A{gid}", sport=sport)])
        s.flush()
        s.add(Game(id=gid, sport=sport, season="2026", date=DAY, status="scheduled",
                   home_team_id=gid * 10, away_team_id=gid * 10 + 1, start_time=start))
    s.commit()
    yield s
    s.close()


def _a(game_id, market="player_pass_yds", line=245.5):
    return PropAnalysis(player_name=f"P{game_id}", market=market, line=line,
                        outcome="Over", season_avg=250.0, recent_avg=250.0,
                        projection=260.0, edge_pct=8.0, confidence=2, source="espn",
                        is_stale=False, game_id=game_id, odds=-110,
                        bookmaker="draftkings", model_probability=0.6)


def _pick(s, game_id):
    return s.query(PickModel).filter(PickModel.game_id == game_id).one()


def test_football_props_are_stored_as_tracking_picks(session):
    _store_prop_picks(session, [_a(1), _a(2), _a(3, market="batter_total_bases", line=1.5)],
                      strategy_id=7)

    assert _pick(session, 1).tracking_only is True
    assert _pick(session, 2).tracking_only is True
    assert _pick(session, 3).tracking_only is False


def test_a_published_football_prop_becomes_tracking_on_refresh(session):
    session.add(PickModel(game_id=1, strategy_id=7, pick_type="prop",
                          pick_value="P1 Over 245.5 Pass Yards", confidence=3,
                          edge_pct=20.0, odds_at_pick=-110, prop_player="P1",
                          prop_market="player_pass_yds", tracking_only=False,
                          created_at=datetime.now(timezone.utc)))
    session.commit()

    _store_prop_picks(session, [_a(1)], strategy_id=7)

    assert _pick(session, 1).tracking_only is True


def test_an_emailed_football_prop_stays_published(session):
    p = PickModel(game_id=1, strategy_id=7, pick_type="prop",
                  pick_value="P1 Over 245.5 Pass Yards", confidence=3, edge_pct=20.0,
                  odds_at_pick=-110, prop_player="P1", prop_market="player_pass_yds",
                  tracking_only=False, created_at=datetime.now(timezone.utc))
    session.add(p)
    session.flush()
    session.add(EmailedPick(pick_id=p.id, game_id=1, digest_date=DAY, sport="nfl",
                            pick_type="prop", pick_value=p.pick_value, odds=-110,
                            sent_at=datetime.now(timezone.utc)))
    session.commit()

    _store_prop_picks(session, [_a(1)], strategy_id=7)

    assert _pick(session, 1).tracking_only is False


def test_the_tracked_sports_are_football():
    assert PROP_TRACKED_SPORTS == {"nfl", "ncaaf"}

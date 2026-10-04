"""A prop pick that stops qualifying before kickoff is withdrawn.

Game picks have done this since 2026-10-03 (`pick_generator._withdraw_stale`).
Props did not: `_store_prop_picks` refreshed props that still qualified and
never touched the rest, so a prop that lost its edge stayed published at
the old edge until kickoff, and the digest could send it.

Same rule as game picks, through the same `pick_generator.withdraw_pick`:
never a graded pick, a started game, or an emailed pick. Only props the run
actually analysed (``answered``) can be withdrawn -- a prop the run never
looked at is no answer, not the answer "no pick".
"""
import asyncio
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine

from backend.data_types import PropAnalysis
from backend.database import get_session
from backend.models import (Base, EmailedPick, Game, PickModel, PickResult,
                            PickVersion, PlayerProp, Team)
from backend.pipeline import prop_pipeline
from backend.pipeline.prop_pipeline import _store_prop_picks

DAY = date(2026, 9, 26)
KEY = (1, "Julian Lewis", "player_pass_yds")


def _a(game_id=1, player="Julian Lewis", market="player_pass_yds"):
    return PropAnalysis(
        player_name=player, market=market, line=205.5, outcome="Over",
        season_avg=220.0, recent_avg=220.0, projection=220.0, edge_pct=10.0,
        confidence=3, source="espn", is_stale=False, game_id=game_id,
        odds=-110, bookmaker="draftkings")


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    s = get_session(engine)
    s.add_all([Team(id=1, name="H", abbreviation="H", sport="ncaaf"),
               Team(id=2, name="A", abbreviation="A", sport="ncaaf")])
    s.flush()
    future = (datetime.now(timezone.utc) + timedelta(hours=3)).replace(tzinfo=None)
    s.add(Game(id=1, sport="ncaaf", season="2026", date=DAY, status="scheduled",
               home_team_id=1, away_team_id=2, start_time=future))
    s.commit()
    yield s
    s.close()


def _stored(session):
    """A prop pick stored by an earlier run, then the session's view of it."""
    _store_prop_picks(session, [_a()], strategy_id=7)
    session.commit()
    return session.query(PickModel).filter(PickModel.pick_type == "prop").one()


def test_an_answered_prop_that_no_longer_qualifies_is_withdrawn(session):
    pick = _stored(session)

    added, refreshed, withdrawn = _store_prop_picks(
        session, [], strategy_id=7, answered={KEY})

    assert (added, refreshed, withdrawn) == (0, 0, 1)
    assert pick.withdrawn_at is not None
    last = (session.query(PickVersion).filter(PickVersion.pick_id == pick.id)
            .order_by(PickVersion.version.desc()).first())
    assert (last.source, last.withdrawn) == ("withdraw", True)


def test_a_prop_the_run_did_not_analyse_is_kept(session):
    """No answer is not the answer "no pick"."""
    pick = _stored(session)

    _store_prop_picks(session, [], strategy_id=7,
                      answered={(1, "Arch Manning", "player_pass_yds")})

    assert pick.withdrawn_at is None


def test_no_answered_set_withdraws_nothing(session):
    pick = _stored(session)

    _store_prop_picks(session, [], strategy_id=7)

    assert pick.withdrawn_at is None


def test_an_emailed_prop_is_never_withdrawn(session):
    pick = _stored(session)
    session.add(EmailedPick(pick_id=pick.id, game_id=1, digest_date=DAY,
                            sport="ncaaf", pick_type="prop",
                            pick_value=pick.pick_value, odds=-110,
                            sent_at=datetime.now(timezone.utc)))
    session.commit()

    _store_prop_picks(session, [], strategy_id=7, answered={KEY})

    assert pick.withdrawn_at is None


def test_a_graded_prop_is_never_withdrawn(session):
    pick = _stored(session)
    session.add(PickResult(pick_id=pick.id, result="win", payout=0.91))
    session.commit()

    _store_prop_picks(session, [], strategy_id=7, answered={KEY})

    assert pick.withdrawn_at is None


def test_a_started_games_prop_is_never_withdrawn(session):
    pick = _stored(session)
    game = session.get(Game, 1)
    game.start_time = (datetime.now(timezone.utc) - timedelta(minutes=5)).replace(tzinfo=None)
    session.commit()

    _store_prop_picks(session, [], strategy_id=7, answered={KEY})

    assert pick.withdrawn_at is None


def test_a_withdrawn_prop_that_qualifies_again_is_reinstated(session):
    pick = _stored(session)
    _store_prop_picks(session, [], strategy_id=7, answered={KEY})
    assert pick.withdrawn_at is not None

    _store_prop_picks(session, [_a()], strategy_id=7, answered={KEY})

    assert pick.withdrawn_at is None
    last = (session.query(PickVersion).filter(PickVersion.pick_id == pick.id)
            .order_by(PickVersion.version.desc()).first())
    assert (last.source, last.withdrawn) == ("refresh", False)


def test_only_the_stale_prop_is_withdrawn(session):
    """Another market for the same player still qualifies and stays."""
    _store_prop_picks(session, [_a(), _a(market="player_rush_yds")], strategy_id=7)
    session.commit()
    rush_key = (1, "Julian Lewis", "player_rush_yds")

    _store_prop_picks(session, [_a(market="player_rush_yds")], strategy_id=7,
                      answered={KEY, rush_key})

    state = {p.prop_market: p.withdrawn_at is None
             for p in session.query(PickModel).filter(PickModel.pick_type == "prop")}
    assert state == {"player_pass_yds": False, "player_rush_yds": True}


# --- the pipeline answers only for the window's own games -------------------

class _NoStats:
    async def fetch_player_stats(self, sport, abbreviation):
        return None, None

    def store_stats(self, *a, **k):
        return 0

    async def close(self):
        pass


def _game_with_stored_prop(session, game_id, sport, player):
    t1, t2 = Team(name=f"{sport}H", abbreviation=f"{sport}H", sport=sport), \
        Team(name=f"{sport}A", abbreviation=f"{sport}A", sport=sport)
    session.add_all([t1, t2])
    session.flush()
    future = (datetime.now(timezone.utc) + timedelta(hours=3)).replace(tzinfo=None)
    session.add(Game(id=game_id, sport=sport, season="2026", date=DAY,
                     status="scheduled", home_team_id=t1.id, away_team_id=t2.id,
                     start_time=future))
    session.add(PlayerProp(game_id=game_id, bookmaker="draftkings",
                           market="player_pass_yds", player_name=player,
                           outcome="Over", line=205.5, odds=-110))
    session.flush()
    _store_prop_picks(session, [_a(game_id=game_id, player=player)], strategy_id=7)
    session.commit()
    return (session.query(PickModel)
            .filter(PickModel.game_id == game_id, PickModel.pick_type == "prop").one())


def test_a_window_withdraws_only_its_own_sports_props():
    """An mlb window gives no answer for an nfl prop, so it cannot withdraw
    it. (The props query once spanned every sport on the date.)"""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    s = get_session(engine)
    mlb = _game_with_stored_prop(s, 10, "mlb", "Aaron Judge")
    nfl = _game_with_stored_prop(s, 20, "nfl", "Patrick Mahomes")

    result = asyncio.run(prop_pipeline._run_prop_pipeline_inner(
        s, _NoStats(), DAY, 7, sports=("mlb",)))

    assert result["picks_withdrawn"] == 1
    assert mlb.withdrawn_at is not None, "analysed, no longer qualifying"
    assert nfl.withdrawn_at is None, "another window's prop"


# --- a withdrawn prop is not emailed ---------------------------------------

def test_the_digest_does_not_send_a_withdrawn_prop():
    from backend.digest.selector import select_digest
    from backend.tests.test_digest_selector import (OPEN_BAR, SEASONS,
                                                    _mk_priced, _session)
    s = _session()
    d = date(2026, 11, 1)
    _mk_priced(s, "nfl", 1, 1, 2, d, [(3, 40.0, -110, 0.9)], pick_type="prop")
    s.query(PickModel).filter_by(pick_value="P1-0").update(
        {"prop_market": "player_pass_yds",
         "withdrawn_at": datetime(2026, 11, 1, 12, 0)})
    s.commit()

    sections = select_digest(s, d, ["nfl"], SEASONS, send_bar=OPEN_BAR)

    assert sections[0].props == []

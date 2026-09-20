"""The prop pipeline must only touch the sport whose window is running.

`_run_window` runs once per window and called `run_prop_pipeline` with no
sport filter, so every window collected player stats for *every* sport
with a game that day. On 2026-09-20 three windows ran -- one nfl, two mlb
-- and every NFL roster was fetched three times. The 404 warnings for
players with no stats page appeared in triplicate, which is how it was
spotted.

That is three times the ESPN request volume for nothing, against an API
with no published rate limit that starts returning 403 once a client asks
quickly enough. Throttling has already cost this project a full run.
"""
import datetime

import pytest
from sqlalchemy import create_engine

from backend.database import get_session
from backend.models import Base, Game, Team
from backend.pipeline import prop_pipeline

TODAY = datetime.date(2026, 9, 20)


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return get_session(engine)


def _game(session, sport, home, away):
    def team(name):
        t = (session.query(Team)
             .filter(Team.sport == sport, Team.abbreviation == name).first())
        if t:
            return t
        t = Team(name=name, abbreviation=name, sport=sport)
        session.add(t)
        session.flush()
        return t

    g = Game(sport=sport, date=TODAY, status="scheduled", season="2026",
             home_team_id=team(home).id, away_team_id=team(away).id)
    session.add(g)
    session.flush()
    return g


class _RecordingCollector:
    """Records which (sport, team) rosters were asked for."""

    def __init__(self):
        self.asked = []

    async def fetch_player_stats(self, sport, abbreviation):
        self.asked.append((sport, abbreviation))
        return None, None

    def store_stats(self, *a, **k):
        return 0

    async def close(self):
        pass


@pytest.mark.asyncio
async def test_a_sport_filter_limits_which_rosters_are_fetched(session):
    _game(session, "nfl", "NFL Home", "NFL Away")
    _game(session, "mlb", "MLB Home", "MLB Away")
    session.commit()
    collector = _RecordingCollector()

    await prop_pipeline._run_prop_pipeline_inner(
        session, collector, TODAY, None, sports=("mlb",))

    sports_asked = {s for s, _ in collector.asked}
    assert sports_asked == {"mlb"}, (
        "an mlb window must not re-fetch every NFL roster; three windows "
        "fetched every NFL athlete three times on 2026-09-20")


@pytest.mark.asyncio
async def test_no_filter_still_fetches_everything(session):
    """The unscoped call is still valid -- fetch_odds_now and the API use
    it deliberately to cover the whole day."""
    _game(session, "nfl", "NFL Home", "NFL Away")
    _game(session, "mlb", "MLB Home", "MLB Away")
    session.commit()
    collector = _RecordingCollector()

    await prop_pipeline._run_prop_pipeline_inner(
        session, collector, TODAY, None)

    assert {s for s, _ in collector.asked} == {"nfl", "mlb"}


@pytest.mark.asyncio
async def test_a_filter_matching_nothing_fetches_nothing(session):
    _game(session, "nfl", "NFL Home", "NFL Away")
    session.commit()
    collector = _RecordingCollector()

    result = await prop_pipeline._run_prop_pipeline_inner(
        session, collector, TODAY, None, sports=("mlb",))

    assert collector.asked == []
    assert result["games"] == 0


# --- the window must actually pass its sport through ----------------------

def test_run_window_scopes_the_prop_pipeline_to_its_own_sport(session,
                                                              monkeypatch):
    """The filter is useless if the one caller that needs it omits it."""
    from backend.pipeline import scheduler as sched

    captured = {}

    async def fake_pipeline(session, target_date=None, strategy_id=None,
                            sports=None):
        captured["sports"] = sports
        return {"picks_generated": 0}

    monkeypatch.setattr(sched, "run_prop_pipeline", fake_pipeline)
    monkeypatch.setattr(sched, "get_session", lambda engine: session)

    sched._run_window({"odds_api_key": None}, object(), "mlb",
                      {"games": [{"id": 1}]})

    assert captured["sports"] == ("mlb",), (
        "an mlb window that omits the filter re-collects every NFL roster")

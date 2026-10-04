"""The prop pipeline analyses only props a book still offers, on its own games.

Two defects, both in `_run_prop_pipeline_inner`'s props query:

* **Pulled props.** `full_pipeline._store_props` upserts and restamps
  ``fetched_at`` on every row a fetch returns, and never deletes. A prop a
  book stopped offering (player ruled out) kept its last row forever, and
  every run analysed it at that line -- it could stay published with no
  book offering it. Now only rows from each game's latest fetch are
  analysed, and a pulled prop's pick is withdrawn.
* **Sport scope.** The query took every prop on the date, so each sport's
  window analysed (and re-priced) every other sport's props, all under
  one analyzer built from ``games[0]``'s thresholds.
"""
import asyncio
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine

from backend.data_types import PropAnalysis
from backend.database import get_session
from backend.models import Base, Game, PickModel, PlayerProp, Team
from backend.pipeline import prop_pipeline
from backend.pipeline.prop_pipeline import _store_prop_picks, current_props

DAY = date(2026, 9, 26)
T0 = datetime(2026, 9, 26, 12, 0)


def _row(game_id, player="P", fetched_at=T0, market="player_pass_yds"):
    return PlayerProp(game_id=game_id, bookmaker="draftkings", market=market,
                      player_name=player, outcome="Over", line=205.5, odds=-110,
                      fetched_at=fetched_at)


# --- current_props ----------------------------------------------------------

def test_a_row_missing_from_the_latest_fetch_is_not_current():
    kept = _row(1, "Kept", T0 + timedelta(hours=3))
    pulled = _row(1, "Pulled", T0)

    assert current_props([kept, pulled]) == [kept]


def test_rows_from_one_fetch_are_all_current():
    """One fetch writes its rows seconds apart."""
    a, b = _row(1, "A", T0), _row(1, "B", T0 + timedelta(seconds=40))

    assert current_props([a, b]) == [a, b]


def test_current_is_relative_to_each_games_own_latest_fetch():
    """Game 2 was last fetched hours before game 1. Its rows are still its
    latest -- an age cutoff from the clock would discard them."""
    g1 = _row(1, "A", T0 + timedelta(hours=5))
    g2 = _row(2, "B", T0)

    assert current_props([g1, g2]) == [g1, g2]


# --- the pipeline -----------------------------------------------------------

class _NoStats:
    async def fetch_player_stats(self, sport, abbreviation):
        return None, None

    def store_stats(self, *a, **k):
        return 0

    async def close(self):
        pass


@pytest.fixture
def session():
    s = get_session(create_engine("sqlite:///:memory:"))
    Base.metadata.create_all(s.get_bind())
    yield s
    s.close()


def _game(s, gid, sport):
    t1 = Team(name=f"{sport}{gid}H", abbreviation=f"{sport}{gid}H", sport=sport)
    t2 = Team(name=f"{sport}{gid}A", abbreviation=f"{sport}{gid}A", sport=sport)
    s.add_all([t1, t2])
    s.flush()
    start = (datetime.now(timezone.utc) + timedelta(hours=3)).replace(tzinfo=None)
    s.add(Game(id=gid, sport=sport, season="2026", date=DAY, status="scheduled",
               home_team_id=t1.id, away_team_id=t2.id, start_time=start))
    s.flush()


def _run(s, **kw):
    return asyncio.run(prop_pipeline._run_prop_pipeline_inner(
        s, _NoStats(), DAY, kw.pop("strategy_id", None), **kw))


def test_a_pulled_props_pick_is_withdrawn_and_the_prop_not_analysed(session):
    _game(session, 1, "nfl")
    session.add_all([_row(1, "Pulled", T0),
                     _row(1, "Offered", T0 + timedelta(hours=3))])
    session.flush()
    _store_prop_picks(session, [PropAnalysis(
        player_name="Pulled", market="player_pass_yds", line=205.5,
        outcome="Over", season_avg=220.0, recent_avg=220.0, projection=220.0,
        edge_pct=10.0, confidence=3, source="espn", is_stale=False, game_id=1,
        odds=-110, bookmaker="draftkings")], strategy_id=7)
    session.commit()

    result = _run(session, strategy_id=7)

    pick = session.query(PickModel).filter(PickModel.prop_player == "Pulled").one()
    assert result["props_analyzed"] == 1, "only the offered prop is analysed"
    assert pick.withdrawn_at is not None, "no book offers it any more"


def test_a_window_analyses_only_its_own_sports_props(session):
    _game(session, 1, "mlb")
    _game(session, 2, "nfl")
    session.add_all([_row(1, "M"), _row(2, "N")])
    session.commit()

    result = _run(session, sports=("mlb",))

    assert result["props_analyzed"] == 1


def test_each_sport_is_analysed_under_its_own_thresholds(session, monkeypatch):
    """An unscoped call (fetch_odds_now, the pipeline API) spans sports; it
    used to analyse all of them with games[0]'s thresholds."""
    _game(session, 1, "mlb")
    _game(session, 2, "nfl")
    session.add_all([_row(1, "M"), _row(2, "N")])
    session.commit()
    monkeypatch.setattr(prop_pipeline, "get_prop_thresholds",
                        lambda session, sport: {"sport": sport})
    seen = {}

    class _Recording:
        def __init__(self, **kw):
            self.thresholds = kw["thresholds"]

        def analyze(self, prop, *a, **k):
            seen[prop.player_name] = self.thresholds["sport"]

    monkeypatch.setattr(prop_pipeline, "PropAnalyzer", _Recording)

    _run(session)

    assert seen == {"M": "mlb", "N": "nfl"}

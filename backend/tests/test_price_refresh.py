"""Game prices are refreshed often enough that a paper bet can always be placed.

A paper bet is priced only from a quote under `pricing.MAX_QUOTE_AGE` (6h)
old. Prices were fetched by the 8am scout and by window jobs ~2h before each
game, so most of any day every quote on file was stale and the Paper
Trading page offered almost nothing (owner, 2026-10-01: "it doesn't seem
like I've got the full dataset to pick from"). `price_refresh` fetches game
prices every half of that window for every in-season sport with a game
today. It refreshes prices only: picks are left to the scout and windows.
"""
import datetime

import pytest
from sqlalchemy import create_engine

import backend.pipeline.scheduler as sch
from backend.database import get_session
from backend.models import Base, Game, Team
from backend.paper.pricing import MAX_QUOTE_AGE
from backend.paper.board import MAX_DAYS

TODAY = datetime.date(2026, 10, 2)
SEASONS = {"nfl": {"start": "09-05", "end": "02-10"},
           "mlb": {"start": "03-20", "end": "11-05"},
           "nba": {"start": "10-22", "end": "06-20"},
           "mma": {"start": "01-01", "end": "12-31"}}


@pytest.fixture
def engine():
    e = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(e)
    s = get_session(e)
    for i, (sport, day, status) in enumerate([
            ("nfl", TODAY, "scheduled"),
            ("mma", TODAY, "scheduled"),
            ("mlb", TODAY, "final"),                      # nothing left to bet
            ("nba", TODAY, "scheduled"),                  # out of season
            ("mlb", TODAY + datetime.timedelta(days=1), "scheduled")]):
        s.add_all([Team(id=2 * i + 1, name=f"H{i}", abbreviation=f"H{i}", sport=sport),
                   Team(id=2 * i + 2, name=f"A{i}", abbreviation=f"A{i}", sport=sport)])
        s.flush()
        s.add(Game(sport=sport, season="2026", date=day, status=status,
                   home_team_id=2 * i + 1, away_team_id=2 * i + 2))
    s.commit()
    s.close()
    return e


@pytest.fixture
def calls(monkeypatch):
    seen = {"odds": [], "picks": 0}

    async def fake_fetch(session, sports, api_key, budget=None):
        seen["odds"].append(list(sports))
        return 0

    def fake_picks(*a, **k):
        seen["picks"] += 1
        return 0

    monkeypatch.setattr(sch, "fetch_and_store_odds", fake_fetch)
    monkeypatch.setattr(sch, "generate_and_store_picks", fake_picks)
    monkeypatch.setattr(sch, "et_today", lambda: TODAY)
    monkeypatch.setattr(sch, "is_sport_in_season",
                        lambda sport, seasons, today=None: sport in ("nfl", "mlb", "mma"))
    return seen


def _config(**kw):
    return {"odds_api_key": "k", "seasons": SEASONS, "odds_budget": {}, **kw}


def test_refreshes_every_in_season_sport_with_a_game_still_to_play_on_the_board(engine, calls):
    sch.refresh_prices(_config(), engine)
    # mlb's game today is final but it plays tomorrow (on the board); nba is
    # out of season. Before 2026-10-09 only sports playing TODAY were
    # refreshed, so NFL went stale every Friday and Saturday.
    assert [sorted(s) for s in calls["odds"]] == [["mlb", "mma", "nfl"]]


def _only_mlb_game_on(engine, day, status="scheduled"):
    s = get_session(engine)
    s.query(Game).filter(Game.sport == "mlb").delete()
    s.add_all([Team(id=101, name="MH", abbreviation="MH", sport="mlb"),
               Team(id=102, name="MA", abbreviation="MA", sport="mlb")])
    s.flush()
    s.add(Game(sport="mlb", season="2026", date=day, status=status,
               home_team_id=101, away_team_id=102))
    s.commit()
    s.close()


def test_a_game_on_the_last_board_day_is_refreshed(engine, calls):
    _only_mlb_game_on(engine, TODAY + datetime.timedelta(days=MAX_DAYS - 1))
    sch.refresh_prices(_config(), engine)
    assert "mlb" in calls["odds"][0]


def test_a_game_past_the_board_horizon_is_not_refreshed(engine, calls):
    _only_mlb_game_on(engine, TODAY + datetime.timedelta(days=MAX_DAYS))
    sch.refresh_prices(_config(), engine)
    assert [sorted(s) for s in calls["odds"]] == [["mma", "nfl"]]


def test_a_game_from_before_today_is_not_refreshed(engine, calls):
    _only_mlb_game_on(engine, TODAY - datetime.timedelta(days=1))
    sch.refresh_prices(_config(), engine)
    assert "mlb" not in calls["odds"][0]

def test_refreshes_prices_only_never_picks(engine, calls):
    sch.refresh_prices(_config(), engine)
    assert calls["picks"] == 0


def test_no_api_key_means_no_fetch(engine, calls):
    sch.refresh_prices(_config(odds_api_key=None), engine)
    assert calls["odds"] == []


def test_a_sport_whose_games_today_have_all_started_is_not_fetched(engine, calls):
    # Games stay "scheduled" until the next morning's grading, so status
    # alone would keep billing a finished slate every 3 hours.
    s = get_session(engine)
    nfl = s.query(Game).filter_by(sport="nfl").one()
    nfl.start_time = datetime.datetime(2000, 1, 1, 17, 0)   # long started
    mma = s.query(Game).filter_by(sport="mma").one()
    mma.start_time = datetime.datetime(2999, 1, 1, 17, 0)   # still to come
    s.commit()
    s.close()
    sch.refresh_prices(_config(), engine)
    # mlb plays tomorrow, so it is on the board too; the started nfl game is not.
    assert [sorted(s) for s in calls["odds"]] == [["mlb", "mma"]]


def test_nothing_to_play_today_spends_no_credit(engine, calls, monkeypatch):
    monkeypatch.setattr(sch, "et_today", lambda: TODAY + datetime.timedelta(days=30))
    sch.refresh_prices(_config(), engine)
    assert calls["odds"] == []


def test_a_failing_fetch_is_logged_not_raised(engine, monkeypatch, calls):
    async def boom(*a, **k):
        raise RuntimeError("odds api down")
    monkeypatch.setattr(sch, "fetch_and_store_odds", boom)
    sch.refresh_prices(_config(), engine)   # must not raise into APScheduler


def test_the_refresh_interval_keeps_every_quote_fresh():
    """Derived from the bet-pricing rule, not a second number: a refresh at
    least twice per MAX_QUOTE_AGE means one missed run still leaves a
    bettable price."""
    assert datetime.timedelta(hours=sch.PRICE_REFRESH_HOURS) * 2 <= MAX_QUOTE_AGE
    scheduler = sch.configure_scheduler(
        {"database_path": ":memory:", "seasons": {}, "odds_budget": {}}, engine=None)
    trigger = str(scheduler.get_job("price_refresh").trigger)
    assert f"hour='*/{sch.PRICE_REFRESH_HOURS}'" in trigger

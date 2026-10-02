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


def test_refreshes_in_season_sports_with_a_game_still_to_play_today(engine, calls):
    sch.refresh_prices(_config(), engine)
    # mlb's only game today is final; nba is out of season.
    assert [sorted(s) for s in calls["odds"]] == [["mma", "nfl"]]


def test_refreshes_prices_only_never_picks(engine, calls):
    sch.refresh_prices(_config(), engine)
    assert calls["picks"] == 0


def test_no_api_key_means_no_fetch(engine, calls):
    sch.refresh_prices(_config(odds_api_key=None), engine)
    assert calls["odds"] == []


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

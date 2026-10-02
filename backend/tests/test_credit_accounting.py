"""api_usage must record what a call actually cost.

record_api_call wrote credits_used=1 for every call. Measured 2026-10-02
from requests_remaining drops: an odds call costs 3 credits (h2h, spreads,
totals; 1 for boxing/mma), a props call 3-4, an events call 0. check_budget
sums credits_used, so the daily/monthly guard undercounted real spend ~3x.
The Odds API reports each call's cost in the ``x-requests-last`` header.
"""
import asyncio
import datetime

import httpx
import pytest
from sqlalchemy import create_engine

import backend.pipeline.full_pipeline as fp
from backend.collectors.budget import get_credit_summary, record_api_call
from backend.collectors.odds_api import OddsAPICollector
from backend.database import get_session
from backend.models import ApiUsage, Base


@pytest.fixture
def session():
    e = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(e)
    s = get_session(e)
    yield s
    s.close()


def _respond(monkeypatch, body, headers):
    async def get(self, url, **kw):
        return httpx.Response(200, json=body, headers=headers,
                              request=httpx.Request("GET", url))
    monkeypatch.setattr(httpx.AsyncClient, "get", get)


@pytest.mark.parametrize("call", ["fetch_odds", "fetch_events"])
def test_the_collector_reads_each_calls_cost(monkeypatch, call):
    _respond(monkeypatch, [], {"x-requests-remaining": "19950", "x-requests-last": "3"})
    c = OddsAPICollector("k")
    asyncio.run(getattr(c, call)("nfl"))
    assert (c.requests_remaining, c.requests_last) == (19950, 3)


def test_the_collector_reads_a_props_calls_cost(monkeypatch):
    _respond(monkeypatch, {"bookmakers": []},
             {"x-requests-remaining": "19946", "x-requests-last": "4"})
    c = OddsAPICollector("k")
    asyncio.run(c.fetch_player_props("nfl", "evt1"))
    assert c.requests_last == 4


def test_a_missing_cost_header_is_unknown_not_zero(monkeypatch):
    _respond(monkeypatch, [], {"x-requests-remaining": "19950"})
    c = OddsAPICollector("k")
    asyncio.run(c.fetch_odds("nfl"))
    assert c.requests_last is None


def test_record_api_call_stores_the_real_cost(session):
    record_api_call(session, "odds", "nfl", requests_remaining=100, credits_used=3)
    record_api_call(session, "events", "nfl", requests_remaining=100, credits_used=0)
    assert [r.credits_used for r in session.query(ApiUsage).order_by(ApiUsage.id)] == [3, 0]


def test_an_unknown_cost_falls_back_to_one(session):
    record_api_call(session, "odds", "nfl", requests_remaining=100)
    assert session.query(ApiUsage).one().credits_used == 1


def test_the_odds_fetch_records_the_cost_the_api_reported(session, monkeypatch):
    class Collector:
        requests_remaining, requests_last = 19950, 3

        def __init__(self, key):
            pass

        async def fetch_odds(self, sport):
            return []

        async def close(self):
            pass

    monkeypatch.setattr(fp, "OddsAPICollector", Collector)
    asyncio.run(fp.fetch_and_store_odds(session, ["nfl", "mlb"], "k"))
    budget = {"monthly_limit": 20000, "daily_target": 600, "reserve": 2000}
    assert get_credit_summary(session, budget)["daily_used"] == 6

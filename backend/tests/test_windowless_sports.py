"""Sports with no ESPN schedule still need their odds fetched.

`fetch_and_store_odds` is called only from `_run_window`, and window jobs
are created only for `scheduled_sports` -- active sports that are also in
ESPN_TEAM_SPORTS. boxing and mma are in ALL_SPORTS and in season all year,
but have no ESPN scoreboard, so they are excluded from that set and their
odds were never fetched at all.

Measured 2026-09-19: the Odds API had 39 boxing events, 28 of them real
upcoming fights. The table held none of those and 25 stale rows from the
last fetch, on 2026-05-24.

Their games come from the Odds API, so there is nothing to cluster a window
around: they are fetched directly instead.
"""
import datetime

import pytest

import backend.pipeline.scheduler as sch


@pytest.fixture()
def calls(monkeypatch):
    """Record what the scheduler asks the collectors for."""
    seen = {"odds": [], "picks": []}

    async def fake_odds(session, sports, api_key, budget=None):
        seen["odds"].append(tuple(sports))
        return 0

    def fake_picks(session, strategy_id, target_date, **kw):
        seen["picks"].append(tuple(kw.get("sports") or ()))
        return 0

    monkeypatch.setattr(sch, "fetch_and_store_odds", fake_odds)
    monkeypatch.setattr(sch, "generate_and_store_picks", fake_picks)
    return seen


CONFIG = {
    "odds_api_key": "k",
    "seasons": {
        "boxing": {"start": "01-01", "end": "12-31"},
        "mma": {"start": "01-01", "end": "12-31"},
        "nba": {"start": "10-22", "end": "06-20"},
    },
}


def test_windowless_sports_are_identified(monkeypatch):
    active = ["nba", "boxing", "mma", "mlb"]
    assert sch.windowless_sports(active) == ["boxing", "mma"]


def test_a_sport_with_an_espn_schedule_is_not_windowless():
    assert sch.windowless_sports(["nba", "mlb", "ncaaf"]) == []


def test_their_odds_are_fetched(calls, monkeypatch):
    sch.fetch_windowless_odds(CONFIG, object(), ["boxing", "mma"])
    assert calls["odds"] == [("boxing", "mma")]


def test_nothing_is_fetched_without_a_key(calls):
    sch.fetch_windowless_odds({"seasons": {}}, object(), ["boxing"])
    assert calls["odds"] == []


def test_nothing_is_fetched_for_an_empty_list(calls):
    sch.fetch_windowless_odds(CONFIG, object(), [])
    assert calls["odds"] == []


def test_a_collector_failure_does_not_propagate(calls, monkeypatch):
    """Boxing odds must not take down the rest of the scout."""
    async def boom(*a, **k):
        raise RuntimeError("odds api down")

    monkeypatch.setattr(sch, "fetch_and_store_odds", boom)
    sch.fetch_windowless_odds(CONFIG, object(), ["boxing"])   # must not raise

"""The reconciler can only use events the fetch actually asked for.

Widening the match window is useless on its own: `fetch_and_store_games`
asked ESPN for one stamp, so a game filed under the neighbouring date was
never in the list to be matched against. The fetch has to see the window
too.

Only when reconciling. A lookback or backfill day passes
``reconcile=False`` and has nothing to protect, so it keeps its single
request rather than tripling the cost of a date-range backfill.

There is a second benefit. `_store_games` matches on `espn_id` first and
**corrects a drifted date** when it does -- so pulling the neighbouring
stamps progressively heals the rows whose date is wrong, rather than only
tolerating them.
"""
from datetime import date

import pytest

from backend.database import get_engine, get_session
from backend.models import Base, Game, Team
from backend.pipeline import full_pipeline

TARGET = date(2026, 5, 1)


def _event(home, away, when="2026-05-01T22:00:00Z", espn_id=None):
    return {"espn_id": espn_id, "home_team": home, "home_team_name": home,
            "away_team": away, "away_team_name": away,
            "date": when, "status": "scheduled",
            "home_score": None, "away_score": None}


@pytest.fixture
def session():
    engine = get_engine(":memory:")
    Base.metadata.create_all(engine)
    s = get_session(engine)
    yield s
    s.close()


def _patch_collector(monkeypatch, responses):
    """Record every (sport, stamp) asked for; answer from ``responses``."""
    asked = []

    class _Fake:
        async def fetch_scoreboard(self, sport, stamp):
            asked.append((sport, stamp))
            return list(responses.get(stamp, []))

        async def close(self):
            pass

    monkeypatch.setattr(full_pipeline, "ESPNCollector", lambda: _Fake())
    return asked


@pytest.mark.asyncio
async def test_reconciling_fetch_asks_for_the_neighbouring_dates(session,
                                                                 monkeypatch):
    asked = _patch_collector(monkeypatch, {"20260501": [_event("DET", "ORL")]})

    await full_pipeline.fetch_and_store_games(session, ["nba"], TARGET)

    assert [s for _, s in asked] == ["20260501", "20260430", "20260502"], \
        "the target date is asked first, so an exact match is stored first"


@pytest.mark.asyncio
async def test_a_non_reconciling_fetch_still_asks_once(session, monkeypatch):
    """Backfill walks a date range; tripling its requests for a guard it
    does not run would be pure cost."""
    asked = _patch_collector(monkeypatch, {"20260501": [_event("DET", "ORL")]})

    await full_pipeline.fetch_and_store_games(session, ["nba"], TARGET,
                                              reconcile=False)

    assert [s for _, s in asked] == ["20260501"]


@pytest.mark.asyncio
async def test_an_event_returned_under_two_stamps_is_stored_once(session,
                                                                 monkeypatch):
    """ESPN lists a late game under both its UTC and Eastern stamps. Storing
    it twice would create the duplicate rows `dedupe_combat_games` exists to
    clean up."""
    dup = _event("DET", "ORL", espn_id="401999")
    _patch_collector(monkeypatch, {"20260501": [dup], "20260502": [dup]})

    await full_pipeline.fetch_and_store_games(session, ["nba"], TARGET)

    assert session.query(Game).count() == 1


@pytest.mark.asyncio
async def test_the_merged_list_holds_one_entry_per_event(monkeypatch):
    """Asserted on the returned list, not on the DB.

    `_store_games` matches on `espn_id` and would collapse a repeat anyway,
    so a row count cannot tell whether the dedupe ran. What it does control
    is how many events `_store_games` iterates -- ESPN lists a late game
    under both its UTC and Eastern stamps, so every such game would be
    upserted twice per run without it.
    """
    dup = _event("DET", "ORL", espn_id="401999")
    other = _event("LAL", "BOS", espn_id="401000")

    class _Fake:
        async def fetch_scoreboard(self, sport, stamp):
            return [dup] if stamp != "20260430" else [dup, other]

        async def close(self):
            pass

    merged = await full_pipeline._with_neighbouring_dates(
        _Fake(), "nba", TARGET, [dup])

    ids = sorted(g["espn_id"] for g in merged)
    assert ids == ["401000", "401999"], f"one entry per event, got {ids}"
    assert merged[0] is dup, "the target-date event stays first"


@pytest.mark.asyncio
async def test_an_event_with_no_espn_id_is_not_collapsed_into_another(monkeypatch):
    """Odds-API rows carry no ESPN identity. Treating a missing id as a
    shared key would merge unrelated games into one."""
    a = _event("DET", "ORL")
    b = _event("LAL", "BOS")

    class _Fake:
        async def fetch_scoreboard(self, sport, stamp):
            return [b] if stamp == "20260430" else []

        async def close(self):
            pass

    merged = await full_pipeline._with_neighbouring_dates(
        _Fake(), "nba", TARGET, [a])

    assert len(merged) == 2


@pytest.mark.asyncio
async def test_a_neighbour_fetch_failure_does_not_lose_the_target_date(
        session, monkeypatch):
    """The neighbours are an enhancement. Losing one must not cost the day
    the run is actually for."""
    class _Fake:
        async def fetch_scoreboard(self, sport, stamp):
            if stamp != "20260501":
                raise RuntimeError("ESPN 503")
            return [_event("DET", "ORL", espn_id="401001")]

        async def close(self):
            pass

    monkeypatch.setattr(full_pipeline, "ESPNCollector", lambda: _Fake())

    stored = await full_pipeline.fetch_and_store_games(session, ["nba"], TARGET)

    assert stored == 1
    assert session.query(Game).count() == 1


@pytest.mark.asyncio
async def test_a_target_date_failure_still_reports_through_errors(session,
                                                                  monkeypatch):
    """The existing contract: a caller passing `errors` must still learn
    that the day failed, or a backfill turns a hole into a silent skip."""
    class _Fake:
        async def fetch_scoreboard(self, sport, stamp):
            raise RuntimeError("ESPN 503")

        async def close(self):
            pass

    monkeypatch.setattr(full_pipeline, "ESPNCollector", lambda: _Fake())
    errors = []

    await full_pipeline.fetch_and_store_games(session, ["nba"], TARGET,
                                              errors=errors)

    assert [sport for sport, _ in errors] == ["nba"]


# --- the scoreboard query itself -----------------------------------------

def test_college_scoreboards_ask_for_the_full_division(httpx_mock):
    """Without `groups`, ESPN answers with a featured subset: 2 ncaab events
    for a conference championship Sunday. `groups=50` is Division I
    basketball, `groups=80` is FBS football."""
    from backend.collectors.espn import SCOREBOARD_PARAMS

    assert SCOREBOARD_PARAMS["ncaab"] == {"groups": "50"}
    assert SCOREBOARD_PARAMS["ncaaf"] == {"groups": "80"}
    assert "nba" not in SCOREBOARD_PARAMS, \
        "pro leagues have one division and need no filter"


@pytest.mark.asyncio
async def test_the_scoreboard_request_carries_the_sport_params(httpx_mock):
    from backend.collectors.espn import ESPNCollector

    httpx_mock.add_response(json={"events": []})
    collector = ESPNCollector()
    try:
        await collector.fetch_scoreboard("ncaab", "20260315")
    finally:
        await collector.close()

    sent = httpx_mock.get_requests()[0].url
    assert "groups=50" in str(sent), f"ncaab must ask for Division I, got {sent}"
    assert "dates=20260315" in str(sent)

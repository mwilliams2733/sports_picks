"""The day's picks must exist before the digest goes out.

`morning_scout` fetched odds only for WINDOWLESS sports. For nba, nfl,
ncaab, ncaaf and mlb it merely scheduled window jobs, and those fire
`LEAD_TIME` (2 hours) before each game so the price is fresh. Baseball
starts in the evening, so its picks were created around 22:00 UTC -- and the
digest runs at 11:00 ET, 15:00 UTC.

Measured in production, picks for 2026-09-22:

    15:05:58 UTC   one pick, five minutes AFTER the digest ran
    22:05, 22:40, 23:40 UTC   the remaining fourteen

so the digest correctly reported "empty; nothing sent" on both 2026-09-21
and 2026-09-22 while picks for those days existed by evening. Saturday
college football hid the problem -- noon kickoffs mean those windows fire
in the morning, which is why 2026-09-19 produced 119 ncaaf picks.

The scout now prices the whole day's slate itself. The window runs continue
afterwards and refresh each pick as the market moves, which
`generate_and_store_picks` already does for any game that has not started.
"""
import datetime

import pytest

import backend.pipeline.scheduler as sch


class _Strategy:
    id = 1


class _FakeSession:
    """Answers the two queries `fetch_odds_and_pick` makes.

    A bare `object()` engine is not enough: the strategy lookup raises, the
    broad `except` swallows it, and the picks half silently never runs while
    the test still passes. That is exactly the hole this file exists to
    close, so the session has to be real enough to get past it.
    """
    def __init__(self, strategy=_Strategy()):
        self._strategy = strategy

    def query(self, *a, **k):
        return self

    def filter(self, *a, **k):
        return self

    def distinct(self):
        return self

    def first(self):
        return self._strategy

    def all(self):
        return []

    def close(self):
        pass


@pytest.fixture()
def calls(monkeypatch):
    seen = {"odds": [], "picks": []}
    monkeypatch.setattr(sch, "get_session", lambda engine: _FakeSession())

    async def fake_odds(session, sports, api_key, budget=None):
        seen["odds"].append(tuple(sports))
        return 0

    def fake_picks(session, strategy_id, target_date, **kw):
        seen["picks"].append(tuple(kw.get("sports") or ()))
        return 0

    monkeypatch.setattr(sch, "fetch_and_store_odds", fake_odds)
    monkeypatch.setattr(sch, "generate_and_store_picks", fake_picks)
    return seen


CONFIG = {"odds_api_key": "k", "seasons": {}}


class _Game:
    def __init__(self, sport):
        self.sport = sport


def _session_with(sports_today):
    """A session whose only job is to answer "which sports play today"."""
    class _Q:
        def __init__(self, rows): self._rows = rows
        def filter(self, *a, **k): return self
        def distinct(self): return self
        def all(self): return self._rows

    class _S:
        def query(self, *a, **k):
            return _Q([(s,) for s in sports_today])
    return _S()


# --- which sports get priced ---------------------------------------------

def test_a_sport_with_games_today_is_priced(calls):
    assert sch.slate_sports(_session_with(["mlb"]), ["mlb", "nfl"],
                            datetime.date(2026, 9, 23)) == ["mlb"]


def test_a_sport_with_no_games_today_is_not_priced(calls):
    """Every fetch costs an API credit. Asking about a sport with nothing on
    the card buys nothing."""
    assert sch.slate_sports(_session_with([]), ["mlb", "nfl"],
                            datetime.date(2026, 9, 23)) == []


def test_the_order_follows_the_requested_sports(calls):
    """Deterministic, so a budget-exhausted run always stops at the same
    place rather than starving a different sport each day.

    The requested order here is deliberately NOT alphabetical: with
    ["mlb", "nfl"] a `sorted()` implementation returns the same answer and
    the test passes without testing anything.
    """
    assert sch.slate_sports(_session_with(["mlb", "nfl"]), ["nfl", "mlb"],
                            datetime.date(2026, 9, 23)) == ["nfl", "mlb"]


# --- the fetch itself -----------------------------------------------------

def test_the_slate_is_fetched_and_picked(calls):
    sch.fetch_odds_and_pick(CONFIG, object(), ["mlb", "ncaaf"])

    assert calls["odds"] == [("mlb", "ncaaf")]
    assert calls["picks"] == [("mlb", "ncaaf")], \
        "odds without picks leaves the digest empty"


def test_nothing_is_fetched_without_a_key(calls):
    sch.fetch_odds_and_pick({"seasons": {}}, object(), ["mlb"])

    assert calls["odds"] == []


def test_nothing_is_fetched_for_an_empty_slate(calls):
    sch.fetch_odds_and_pick(CONFIG, object(), [])

    assert calls["odds"] == []


def test_a_collector_failure_does_not_propagate(monkeypatch, calls):
    """A dead odds API must not stop the scout from scheduling windows --
    that would cost the whole day, not just the morning."""
    async def boom(*a, **k):
        raise RuntimeError("odds api down")

    monkeypatch.setattr(sch, "fetch_and_store_odds", boom)
    sch.fetch_odds_and_pick(CONFIG, object(), ["mlb"])      # must not raise


def test_picks_are_still_generated_when_the_odds_call_returns_nothing(calls):
    """Zero new odds is normal -- prices may not have moved. The picks pass
    still has to run, or a day whose prices were already stored produces no
    picks at all."""
    sch.fetch_odds_and_pick(CONFIG, object(), ["mlb"])

    assert calls["picks"] == [("mlb",)]


# --- the scout must actually call it --------------------------------------

def test_the_morning_scout_prices_the_slate(monkeypatch):
    """THE test. `fetch_odds_and_pick` working is worth nothing if the
    scheduled job never calls it -- which is precisely the state the digest
    was in for the windowed sports.
    """
    from sqlalchemy import create_engine

    from backend.database import get_session as real_get_session
    from backend.models import Base, Game, Team

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = real_get_session(engine)
    today = sch.et_today()
    session.add_all([Team(id=1, name="H", abbreviation="H", sport="mlb"),
                     Team(id=2, name="A", abbreviation="A", sport="mlb")])
    session.flush()
    session.add(Game(sport="mlb", season="2026", date=today,
                     status="scheduled", home_team_id=1, away_team_id=2,
                     start_time=datetime.datetime.now()
                     + datetime.timedelta(hours=6)))
    session.commit()

    priced: list[tuple] = []
    monkeypatch.setattr(sch, "collect_box_scores_for_final_games", lambda s: None)
    monkeypatch.setattr(sch, "grade_pending_picks", lambda s: {})
    monkeypatch.setattr(sch, "grade_completed_games", lambda s: None)
    monkeypatch.setattr(sch, "finalize_stuck_bouts",
                        lambda s, *a, **k: {"finalized": 0, "dates": 0})
    monkeypatch.setattr(sch, "finalize_from_scores",
                        lambda s, sport, key, *a, **k: {
                            "sport": sport, "considered": 0, "finalized": 0,
                            "unmatched": 0, "skipped_no_key": False})
    monkeypatch.setattr(sch, "is_sport_in_season",
                        lambda sport, *a, **k: sport == "mlb")

    async def fake_games(*a, **k):
        return 0

    monkeypatch.setattr(sch, "fetch_and_store_games", fake_games)
    monkeypatch.setattr(sch, "fetch_odds_and_pick",
                        lambda cfg, eng, sports: priced.append(tuple(sports)))
    monkeypatch.setattr(sch, "cluster_game_windows", lambda games, **k: [])

    class _Sched:
        def get_jobs(self): return []
        def remove_job(self, _): pass
        def add_job(self, *a, **k): pass

    sch.morning_scout({"seasons": {}, "database_path": ":memory:",
                       "odds_api_key": "k"}, engine, _Sched())

    assert ("mlb",) in priced, (
        f"the scout never priced today's slate; it called {priced}")

"""The morning scout must survive the machine being asleep at 08:00 ET.

On 2026-09-28 the laptop slept from 13:09 ET the day before until 09:59 ET.
The 08:00 scout came due during sleep and APScheduler dropped it (default
misfire grace: one second). The 09:00 and 10:00 retries that exist for
exactly this did not run either: `morning_scout` had called `remove_job` on
them after the first successful scout on 2026-09-23, and removing a CRON job
deletes it for the life of the process, not for one day. The 11:00 digest
then found no picks and sent nothing, on a day with Monday Night Football.

Three properties, each tested here:

* the retries stay registered, and skip themselves once today's scout has
  succeeded;
* a late scout or digest still runs (misfire grace), and when the machine
  wakes with several overdue at once they do not run concurrently;
* the digest never runs ahead of today's scout.
"""
import threading

import pytest

import backend.pipeline.scheduler as sch


@pytest.fixture
def scout_calls(monkeypatch):
    """Stub every collaborator of morning_scout and count ESPN fetches.

    `fetch_and_store_games` is the step whose success defines a successful
    scout, so counting it counts scouts that did real work.
    """
    calls: list[str] = []
    monkeypatch.setattr(sch, "_last_scout_success", None, raising=False)
    monkeypatch.setattr(sch, "get_session", lambda engine: _Session())
    monkeypatch.setattr(sch, "collect_box_scores_for_final_games", lambda s: None)
    monkeypatch.setattr(sch, "collect_team_box_scores",
                        lambda s, sport: {"games": 0, "rows": 0, "skipped": 0})
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

    async def fake_games(*a, reconcile=False, **k):
        # One scout asks about today (reconcile=True) plus LOOKBACK_DAYS
        # past days; count the today call only, so one scout is one entry.
        if reconcile:
            calls.append("scout")
        return 0

    monkeypatch.setattr(sch, "fetch_and_store_games", fake_games)
    monkeypatch.setattr(sch, "fetch_odds_and_pick", lambda *a, **k: None)
    monkeypatch.setattr(sch, "slate_sports", lambda *a, **k: [])
    return calls


class _Query:
    def filter(self, *a, **k): return self
    def all(self): return []


class _Session:
    def query(self, *a, **k): return _Query()
    def close(self): pass


CONFIG = {"seasons": {}, "database_path": ":memory:", "odds_api_key": "k",
          "odds_budget": {}, "digest": {"enabled": True}}


def _jobs(scheduler):
    return {j.id: j for j in scheduler.get_jobs()}


# --- fix 1: retries are skipped, never deleted -----------------------------

def test_the_retries_survive_a_successful_scout(scout_calls):
    scheduler = sch.configure_scheduler(CONFIG, engine=None)

    sch.morning_scout(CONFIG, None, scheduler)

    assert scout_calls == ["scout"]
    assert {"scout_retry_9", "scout_retry_10"} <= set(_jobs(scheduler)), (
        "a successful scout deleted the retry cron jobs -- tomorrow has no "
        "safety net")


def test_a_retry_after_todays_success_does_nothing(scout_calls):
    scheduler = sch.configure_scheduler(CONFIG, engine=None)
    sch.morning_scout(CONFIG, None, scheduler)

    sch.morning_scout(CONFIG, None, scheduler, is_retry=True)

    assert scout_calls == ["scout"], "the retry re-ran a scout that had succeeded"


def test_a_retry_runs_when_the_8am_scout_never_happened(scout_calls):
    """2026-09-28 exactly: no scout at 08:00 because the machine slept."""
    scheduler = sch.configure_scheduler(CONFIG, engine=None)

    sch.morning_scout(CONFIG, None, scheduler, is_retry=True)

    assert scout_calls == ["scout"]


def test_yesterdays_success_does_not_excuse_todays_retry(scout_calls, monkeypatch):
    import datetime
    monkeypatch.setattr(sch, "_last_scout_success",
                        sch.et_today() - datetime.timedelta(days=1))
    scheduler = sch.configure_scheduler(CONFIG, engine=None)

    sch.morning_scout(CONFIG, None, scheduler, is_retry=True)

    assert scout_calls == ["scout"]


def test_a_failed_fetch_does_not_count_as_success(scout_calls, monkeypatch):
    async def boom(*a, **k):
        raise RuntimeError("ESPN down")

    monkeypatch.setattr(sch, "fetch_and_store_games", boom)
    scheduler = sch.configure_scheduler(CONFIG, engine=None)

    sch.morning_scout(CONFIG, None, scheduler)

    assert sch._last_scout_success is None


# --- fix 2: late jobs still run, and not concurrently ----------------------

@pytest.mark.parametrize("job_id", ["morning_scout", "scout_retry_9",
                                    "scout_retry_10", "daily_digest"])
def test_a_late_job_is_run_not_dropped(job_id):
    """APScheduler's default grace is one second: a job that comes due while
    the machine sleeps is discarded. These must run late instead."""
    job = _jobs(sch.configure_scheduler(CONFIG, engine=None))[job_id]

    assert isinstance(job.misfire_grace_time, int)
    assert job.misfire_grace_time >= 2 * 3600
    assert job.coalesce is True


def test_a_scout_already_running_is_not_duplicated(scout_calls):
    """On wake, the 8, 9 and 10 o'clock jobs all fall due at once and go to
    the thread pool together."""
    scheduler = sch.configure_scheduler(CONFIG, engine=None)
    with sch._scout_lock:
        # In a thread so a regression (blocking on the lock) fails this test
        # in seconds instead of hanging the suite for SCOUT_WAIT_SECONDS.
        second = threading.Thread(target=sch.morning_scout,
                                  args=(CONFIG, None, scheduler),
                                  kwargs={"is_retry": True}, daemon=True)
        second.start()
        second.join(timeout=5)
        assert not second.is_alive(), "a second scout blocked on the first"

    assert scout_calls == []


def test_a_waiting_scout_does_not_repeat_one_that_finished_meanwhile(scout_calls):
    scheduler = sch.configure_scheduler(CONFIG, engine=None)
    sch._scout_lock.acquire()
    waiter = threading.Thread(
        target=sch.morning_scout, args=(CONFIG, None, scheduler),
        kwargs={"is_retry": True, "wait": True})
    waiter.start()
    # The holder finishes a successful scout, then releases.
    sch._last_scout_success = sch.et_today()
    sch._scout_lock.release()
    waiter.join(timeout=10)

    assert not waiter.is_alive()
    assert scout_calls == []


# --- the digest never runs ahead of today's scout --------------------------

def test_the_digest_runs_the_scout_first_when_none_succeeded_today(
        scout_calls, monkeypatch):
    monkeypatch.setattr("backend.digest.job.send_daily_digest",
                        lambda cfg, eng: scout_calls.append("digest"))
    scheduler = sch.configure_scheduler(CONFIG, engine=None)

    sch._digest_after_scout(CONFIG, None, scheduler)

    assert scout_calls == ["scout", "digest"]


def test_the_digest_does_not_rescout_after_a_successful_scout(
        scout_calls, monkeypatch):
    monkeypatch.setattr("backend.digest.job.send_daily_digest",
                        lambda cfg, eng: scout_calls.append("digest"))
    scheduler = sch.configure_scheduler(CONFIG, engine=None)
    sch.morning_scout(CONFIG, None, scheduler)

    sch._digest_after_scout(CONFIG, None, scheduler)

    assert scout_calls == ["scout", "digest"]


def test_the_scheduled_digest_job_goes_through_the_scout_check(
        scout_calls, monkeypatch):
    """The wrapper is worth nothing if the cron job bypasses it."""
    monkeypatch.setattr("backend.digest.job.send_daily_digest",
                        lambda cfg, eng: scout_calls.append("digest"))
    job = _jobs(sch.configure_scheduler(CONFIG, engine=None))["daily_digest"]

    job.func()

    assert scout_calls == ["scout", "digest"]

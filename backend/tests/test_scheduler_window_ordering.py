"""A slow window must not starve the sports scheduled after it.

On 2026-09-19 morning_scout ran a due ncaaf window inline. That pipeline
took over 25 minutes fetching player stats one athlete at a time, and the
loop never reached mlb -- which is third in `scheduled_sports` -- so mlb had
no window created and went the day with 14 scheduled games and zero odds.
Nothing logged an error; the loop was simply still inside the earlier call.
"""
import datetime

import pytest

import backend.pipeline.scheduler as sch
from backend.database import get_engine, get_session
from backend.models import Base, Game, Team

from backend.time_utils import et_today

TODAY = et_today()


@pytest.fixture()
def engine(tmp_path):
    e = get_engine(str(tmp_path / "s.db"))
    Base.metadata.create_all(e)
    s = get_session(e)
    s.add_all([Team(id=i, name=f"T{i}", abbreviation=f"T{i}", sport=sp)
               for i, sp in ((1, "ncaaf"), (2, "ncaaf"), (3, "mlb"), (4, "mlb"))])
    s.flush()
    # Both windows are already due, so both take the "run now" path.
    past = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=3)
    s.add_all([
        Game(id=10, sport="ncaaf", season="2026", date=TODAY, status="scheduled",
             home_team_id=1, away_team_id=2,
             start_time=past.replace(tzinfo=None)),
        Game(id=20, sport="mlb", season="2026", date=TODAY, status="scheduled",
             home_team_id=3, away_team_id=4,
             start_time=past.replace(tzinfo=None)),
    ])
    s.commit()
    s.close()
    return e


class _Sched:
    def get_jobs(self): return []
    def add_job(self, *a, **k): pass
    def remove_job(self, *a, **k): pass


def test_a_slow_sport_does_not_starve_a_later_one(engine, monkeypatch):
    """The regression, stated as an ordering fact.

    `scheduled_sports` puts ncaaf before mlb. If the ncaaf window runs while
    the loop is still scheduling, mlb never gets one.
    """
    scheduled_for, ran_for = [], []

    def fake_run_window(config, eng, sport, window):
        ran_for.append(sport)
        # Whatever else has been scheduled must already be scheduled by now.
        assert set(scheduled_for) == {"ncaaf", "mlb"}, (
            f"ran {sport} while only {scheduled_for} had been scheduled -- "
            "the loop is still blocked inside a window")

    real_cluster = sch.cluster_game_windows

    def spy_cluster(games):
        out = real_cluster(games)
        return out

    monkeypatch.setattr(sch, "_run_window", fake_run_window)
    monkeypatch.setattr(sch, "cluster_game_windows", spy_cluster)
    monkeypatch.setattr(sch, "collect_box_scores_for_final_games", lambda s: None)
    monkeypatch.setattr(sch, "grade_pending_picks", lambda s: None)
    monkeypatch.setattr(sch, "grade_completed_games", lambda s: None)
    monkeypatch.setattr(sch, "fetch_and_store_games",
                        lambda *a, **k: _noop_coro())

    # Record the order sports are scheduled in, by wrapping the query loop's
    # observable effect: cluster_game_windows is called once per sport that
    # has games.
    def tracking_cluster(games):
        gid = games[0]["id"] if games else None
        scheduled_for.append("ncaaf" if gid == 10 else "mlb")
        return real_cluster(games)

    monkeypatch.setattr(sch, "cluster_game_windows", tracking_cluster)

    config = {"seasons": {"ncaaf": {"start": "08-24", "end": "01-20"},
                          "mlb": {"start": "03-27", "end": "10-31"}}}
    sch.morning_scout(config, engine, _Sched())

    assert ran_for == ["ncaaf", "mlb"], f"both windows must run, got {ran_for}"


async def _noop_coro():
    return None

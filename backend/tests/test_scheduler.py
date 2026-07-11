from datetime import datetime, timezone, timedelta
from backend.pipeline.scheduler import cluster_game_windows


def _make_game(hour, minute=0):
    return {
        "id": hash((hour, minute)),
        "start_time": datetime(2026, 3, 17, hour, minute, tzinfo=timezone.utc),
    }


def test_cluster_single_window():
    games = [_make_game(23, 0), _make_game(23, 10), _make_game(23, 30)]
    windows = cluster_game_windows(games)
    assert len(windows) == 1
    assert len(windows[0]["games"]) == 3


def test_cluster_two_windows():
    games = [_make_game(17, 0), _make_game(17, 15), _make_game(23, 0), _make_game(23, 30)]
    windows = cluster_game_windows(games)
    assert len(windows) == 2
    assert len(windows[0]["games"]) == 2
    assert len(windows[1]["games"]) == 2


def test_cluster_empty_list():
    windows = cluster_game_windows([])
    assert windows == []


def test_window_run_time_is_2_hours_before():
    games = [_make_game(23, 0), _make_game(23, 30)]
    windows = cluster_game_windows(games)
    expected_run = datetime(2026, 3, 17, 21, 0, tzinfo=timezone.utc)
    assert windows[0]["run_at"] == expected_run


def test_naive_start_time_is_treated_as_utc_not_raised():
    """Game.start_time is a plain DateTime column, so it comes back naive
    after a SQLite round trip even though every write path uses UTC. A
    naive start_time must be normalized to UTC-aware rather than producing
    a naive run_at that raises when later compared against an aware
    datetime.now(tz=timezone.utc) in the scheduler."""
    naive_game = {"id": 1, "start_time": datetime(2026, 3, 17, 23, 0)}
    windows = cluster_game_windows([naive_game])
    run_at = windows[0]["run_at"]
    assert run_at.tzinfo is not None
    now_utc = datetime.now(tz=timezone.utc)
    # Must not raise TypeError: can't compare offset-naive and offset-aware
    assert (run_at <= now_utc) in (True, False)
    assert run_at == datetime(2026, 3, 17, 21, 0, tzinfo=timezone.utc)


def test_mixed_naive_and_aware_start_times_cluster_together():
    naive_game = {"id": 1, "start_time": datetime(2026, 3, 17, 23, 0)}
    aware_game = {"id": 2, "start_time": datetime(2026, 3, 17, 23, 10, tzinfo=timezone.utc)}
    windows = cluster_game_windows([naive_game, aware_game])
    assert len(windows) == 1
    assert len(windows[0]["games"]) == 2

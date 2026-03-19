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

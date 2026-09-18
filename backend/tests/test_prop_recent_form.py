"""Prop projections are built to weight recent form at 0.6, and never saw it.

`PropAnalyzer` blends season_weight 0.4 with recent_weight 0.6, but
`prop_pipeline` reads recent form from player_stats where stat_type='game_log'
and that table had 0 rows -- so every prop ever generated took the season-only
branch, and `use_distribution` (which needs three game-by-game values for a
variance estimate) has never once been True.
"""
import inspect
import re

from nba_api.stats.endpoints import PlayerGameLog

from backend.collectors.player_stats import nba_api_source


def test_fetch_recent_games_does_not_pass_a_parameter_that_does_not_exist():
    """`last_n_games` is not a PlayerGameLog parameter.

    The TypeError fired before any HTTP request, and the fallback chain's
    blanket `except Exception` logged it as "{source} failed for {player}" --
    making an unusable call site indistinguishable, in the log, from a source
    that simply had no data. Line 167 already slices games[:n], so the kwarg
    was redundant as well as wrong.

    Asserted against the signature rather than the network: stats.nba.com
    read-times-out from here, which is a separate problem.
    """
    allowed = set(inspect.signature(PlayerGameLog.__init__).parameters)
    src = inspect.getsource(nba_api_source.NbaApiSource.fetch_recent_games)

    for call in re.findall(r"PlayerGameLog\(([^)]*)\)", src):
        for kwarg in re.findall(r"(\w+)\s*=", call):
            assert kwarg in allowed, (
                f"PlayerGameLog has no parameter {kwarg!r}; the call raises "
                f"TypeError before any request is made")


def test_the_prop_pipeline_does_not_fetch_recent_games_itself():
    """game_log rows come from collectors/espn_box_score.py, which runs
    post-game from morning_scout (plan 010).

    Fetching "last 5" inside the prop pipeline was a second path to the same
    table that never produced a row: nba_api raised before its request,
    stats.nba.com times out from this network, and the ESPN athlete endpoint
    404s. Worse, it ran pre-game -- the wrong shape for grading and the wrong
    shape for a point-in-time feature, since the last-5 it wanted is exactly
    what the box-score collector already writes.
    """
    from backend.pipeline import prop_pipeline

    # The work is in _run_prop_pipeline_inner, not the thin run_prop_pipeline
    # wrapper -- asserting against the wrapper passes vacuously.
    src = inspect.getsource(prop_pipeline._run_prop_pipeline_inner)
    assert "fetch_player_recent" not in src, (
        "the prop pipeline still calls fetch_player_recent; game_log comes "
        "from espn_box_score now")

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


# --- recent form must be point-in-time ---------------------------------------

import datetime  # noqa: E402

from backend.models import Base, PlayerStat, Team  # noqa: E402
from backend.pipeline.prop_pipeline import _recent_form  # noqa: E402


def _setup(session):
    """player_stats.team_id is a real FK and get_engine turns
    PRAGMA foreign_keys ON."""
    Base.metadata.create_all(session.get_bind())
    if not session.query(Team).filter_by(id=1).first():
        session.add(Team(id=1, name="T", abbreviation="T", sport="nba"))
        session.flush()


def _log(session, player, day, points):
    session.add(PlayerStat(
        player_name=player, team_id=1, sport="nba", stat_type="game_log",
        game_date=day, points=points, source="espn",
        fetched_at=datetime.datetime.now(datetime.timezone.utc)))


def test_recent_form_excludes_the_game_being_predicted_and_anything_after(db_session):
    """Same defect shape as plan 008's team-stat scoping (`399ac79`).

    The query took the 5 most recent game_log rows with no date bound. While
    the table was empty that was harmless; with data it reads the future. A
    prop on a game that has since been played would be analysed using that
    game's own box score, and any later game's too -- and recent form is 60%
    of the projection.
    """
    _setup(db_session)
    target = datetime.date(2026, 3, 10)
    for day, pts in ((8, 10.0), (9, 20.0), (10, 99.0), (11, 99.0)):
        _log(db_session, "X", datetime.date(2026, 3, day), pts)
    db_session.commit()

    rows = _recent_form(db_session, "X", before=target)

    assert [r.game_date for r in rows] == [
        datetime.date(2026, 3, 9), datetime.date(2026, 3, 8)]
    assert 99.0 not in [r.points for r in rows], "the future leaked in"


def test_recent_form_returns_at_most_five_newest_first(db_session):
    _setup(db_session)
    for day in range(1, 9):
        _log(db_session, "X", datetime.date(2026, 3, day), float(day))
    db_session.commit()

    rows = _recent_form(db_session, "X", before=datetime.date(2026, 3, 20))

    assert len(rows) == 5
    assert [r.points for r in rows] == [8.0, 7.0, 6.0, 5.0, 4.0]


def test_recent_form_is_scoped_to_the_player(db_session):
    _setup(db_session)
    _log(db_session, "X", datetime.date(2026, 3, 8), 10.0)
    _log(db_session, "Y", datetime.date(2026, 3, 8), 50.0)
    db_session.commit()

    rows = _recent_form(db_session, "X", before=datetime.date(2026, 3, 10))

    assert [r.points for r in rows] == [10.0]

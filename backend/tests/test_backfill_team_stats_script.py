"""The team-stats backfill script (plan 008, Step 8)."""
from datetime import date

import pytest

from backend.database import get_engine, get_session
from backend.models import Base, Team, Game, TeamStat, EloHistory
from backend.scripts import backfill_team_stats as script


@pytest.fixture
def db_file(tmp_path):
    path = tmp_path / "copy.db"
    engine = get_engine(str(path))
    Base.metadata.create_all(engine)
    session = get_session(engine)
    a = Team(name="Alpha", abbreviation="ALP", sport="nba")
    b = Team(name="Beta", abbreviation="BET", sport="nba")
    session.add_all([a, b])
    session.commit()
    for d, hs, aws in [(1, 110, 100), (5, 120, 100), (9, 100, 130)]:
        session.add(Game(sport="nba", season="2024-2025", date=date(2024, 1, d),
                         home_team_id=a.id, away_team_id=b.id,
                         home_score=hs, away_score=aws, status="final"))
    session.commit()
    session.close()
    engine.dispose()
    return str(path)


def _counts(db_path):
    engine = get_engine(db_path)
    session = get_session(engine)
    out = (session.query(TeamStat).count(),
           session.query(EloHistory).count(),
           session.query(TeamStat.game_id).distinct().count())
    session.close()
    engine.dispose()
    return out


def test_db_argument_is_required():
    with pytest.raises(SystemExit):
        script.main([])


def test_dry_run_writes_nothing(db_file):
    summary = script.run(db_file, dry_run=True)
    assert summary["nba"]["team_stats"]["games_processed"] == 3
    assert _counts(db_file) == (0, 0, 0)


def test_real_run_populates_every_game(db_file):
    script.run(db_file)
    n_stats, n_elo, n_games = _counts(db_file)
    assert n_games == 3
    assert n_elo == 6  # two teams per game
    assert n_stats == 3 * 2 * 8  # 3 games x 2 teams x 8 stat types


def test_second_run_is_a_no_op(db_file):
    script.run(db_file)
    before = _counts(db_file)
    summary = script.run(db_file)
    assert summary["nba"]["team_stats"]["games_skipped"] == 3
    assert summary["nba"]["team_stats"]["rows_written"] == 0
    assert summary["nba"]["elo_history"]["rows_written"] == 0
    assert _counts(db_file) == before


def test_backfilled_values_are_point_in_time(db_file):
    script.run(db_file)
    engine = get_engine(db_file)
    session = get_session(engine)
    games = session.query(Game).order_by(Game.date).all()
    a_id = games[0].home_team_id

    def pd(game):
        return (session.query(TeamStat)
                .filter(TeamStat.game_id == game.id, TeamStat.team_id == a_id,
                        TeamStat.stat_type == "point_diff").one().value)

    assert pd(games[0]) == pytest.approx(0.0)    # no prior games
    assert pd(games[1]) == pytest.approx(10.0)   # game 1 only
    assert pd(games[2]) == pytest.approx(15.0)   # games 1 and 2

    # Elo attached to game 1 is the initial rating: it cannot know game 1.
    r1 = (session.query(EloHistory)
          .filter(EloHistory.game_id == games[0].id,
                  EloHistory.team_id == a_id).one().rating)
    assert r1 == pytest.approx(1500.0)
    session.close()
    engine.dispose()


def test_a_typod_db_path_is_refused_not_created(tmp_path):
    """A nonexistent ``--db`` must raise, not silently create an empty database.

    ``run`` calls ``create_all``, so without the existence guard a typo'd path
    produced a brand-new empty database and a cheerful zero-row "successful"
    backfill -- indistinguishable from a real run that had nothing to do.
    """
    missing = tmp_path / "typo.db"

    with pytest.raises(FileNotFoundError):
        script.run(str(missing))

    assert not missing.exists(), "the script created the database it was told to fill"

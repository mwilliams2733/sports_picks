"""Guards for rebuilding Elo history after a backfill.

`backfill_elo_history` skips games it has already written, which is right for
an incremental catch-up and wrong after games are inserted *earlier* than
existing ones. On 2026-09-20 the ncaaf backfill added 185 games predating the
rows already stored, and team DEL carried pre-game ratings of 1500.0 (09-03),
1529.2 (09-12), then 1500.0 again (09-19) -- the last row still holding the
seed it was given when the database had no DEL history at all.

The dangerous outcome is rebuilding the wrong scope: a replay that also
rewrote combat sports, or one that deleted rows for a sport it was not asked
about.
"""
import datetime

import pytest

from backend.database import get_engine, get_session
from backend.models import Base, EloHistory, Game, Team
from backend.pipeline.team_stats import backfill_elo_history


def _session(tmp_path):
    session = get_session(get_engine(str(tmp_path / "elo.db")))
    Base.metadata.create_all(session.get_bind())
    session.add_all([
        Team(id=1, name="AAA", abbreviation="AAA", sport="ncaaf"),
        Team(id=2, name="BBB", abbreviation="BBB", sport="ncaaf"),
        Team(id=3, name="CCC", abbreviation="CCC", sport="nba"),
        Team(id=4, name="DDD", abbreviation="DDD", sport="nba"),
    ])
    session.flush()
    return session


def _game(gid, day, sport="ncaaf", home=1, away=2, hs=30, as_=10):
    return Game(id=gid, sport=sport, season="2026", date=day,
                home_team_id=home, away_team_id=away,
                home_score=hs, away_score=as_, status="final")


EARLY = datetime.date(2026, 9, 3)
LATE = datetime.date(2026, 9, 19)


def _rating(session, gid, team_id=1):
    return (session.query(EloHistory)
            .filter(EloHistory.game_id == gid, EloHistory.team_id == team_id)
            .one().rating)


def test_rebuild_corrects_a_row_left_at_the_seed(tmp_path):
    session = _session(tmp_path)
    session.add_all([_game(1, EARLY), _game(2, LATE)])
    session.flush()
    # The late game was rated when no earlier history existed: both teams seed.
    session.add_all([
        EloHistory(team_id=1, game_id=2, sport="ncaaf", rating=1500.0),
        EloHistory(team_id=2, game_id=2, sport="ncaaf", rating=1500.0),
    ])
    session.commit()

    backfill_elo_history(session, "ncaaf", rebuild=True)
    session.commit()

    assert _rating(session, 1) == 1500.0          # genuinely the first game
    assert _rating(session, 2) > 1500.0           # AAA won on 09-03


def test_without_rebuild_a_stale_row_is_left_alone(tmp_path):
    """The default stays incremental; rebuilding must be asked for."""
    session = _session(tmp_path)
    session.add_all([_game(1, EARLY), _game(2, LATE)])
    session.flush()
    session.add_all([
        EloHistory(team_id=1, game_id=2, sport="ncaaf", rating=1500.0),
        EloHistory(team_id=2, game_id=2, sport="ncaaf", rating=1500.0),
    ])
    session.commit()

    backfill_elo_history(session, "ncaaf")
    session.commit()

    assert _rating(session, 2) == 1500.0


def test_rebuild_does_not_touch_another_sport(tmp_path):
    session = _session(tmp_path)
    session.add_all([_game(1, EARLY), _game(2, LATE),
                     _game(3, LATE, sport="nba", home=3, away=4)])
    session.flush()
    session.add(EloHistory(team_id=3, game_id=3, sport="nba", rating=1234.0))
    session.commit()

    backfill_elo_history(session, "ncaaf", rebuild=True)
    session.commit()

    assert _rating(session, 3, team_id=3) == 1234.0


def test_rebuild_still_refuses_combat_sports(tmp_path):
    """Combat history is post-game and owned by the grader; a rebuild flag
    must not become a way around that refusal."""
    session = _session(tmp_path)
    with pytest.raises(ValueError):
        backfill_elo_history(session, "mma", rebuild=True)


def test_rebuild_leaves_no_duplicate_rows(tmp_path):
    session = _session(tmp_path)
    session.add_all([_game(1, EARLY), _game(2, LATE)])
    session.flush()
    session.add_all([
        EloHistory(team_id=1, game_id=2, sport="ncaaf", rating=1500.0),
        EloHistory(team_id=2, game_id=2, sport="ncaaf", rating=1500.0),
    ])
    session.commit()

    backfill_elo_history(session, "ncaaf", rebuild=True)
    session.commit()

    assert session.query(EloHistory).count() == 4   # two teams x two games

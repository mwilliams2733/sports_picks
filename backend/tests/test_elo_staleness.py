"""Guards against serving a stale Elo rating for an upcoming game.

`elo_history` stores the **pre-game** rating. For a scheduled game there is
no row yet, so `_team_elo` fell back to the most recent prior row -- which
holds *that* game's pre-game rating. A team in its second game therefore read
the seed it carried into its first. In NFL week 2 on 2026-09-20 that was all
32 teams at exactly 1500.0, so every signal tied and every pick collapsed to
tier 1.

The dangerous outcome in fixing it is the opposite error: reading the rating
*after* the game being predicted, which is lookahead and would flatter every
backtest.
"""
import datetime

import pytest

from backend.database import get_engine, get_session
from backend.models import Base, EloHistory, Game, Team
from backend.pipeline.pick_generator import _team_elo
from backend.pipeline.team_stats import backfill_elo_history

D1 = datetime.date(2026, 9, 13)
D2 = datetime.date(2026, 9, 20)
D3 = datetime.date(2026, 9, 27)


def _session(tmp_path):
    session = get_session(get_engine(str(tmp_path / "elo.db")))
    Base.metadata.create_all(session.get_bind())
    session.add_all([
        Team(id=1, name="AAA", abbreviation="AAA", sport="nfl"),
        Team(id=2, name="BBB", abbreviation="BBB", sport="nfl"),
    ])
    session.flush()
    return session


def _game(gid, day, status="final", hs=31, as_=10):
    return Game(id=gid, sport="nfl", season="2026", date=day,
                home_team_id=1, away_team_id=2,
                home_score=hs if status == "final" else None,
                away_score=as_ if status == "final" else None,
                status=status)


def test_an_upcoming_game_reads_the_rating_after_the_last_result(tmp_path):
    """The whole defect: AAA won its first game, so it must not still read
    the seed it carried into that game."""
    session = _session(tmp_path)
    session.add_all([_game(1, D1), _game(2, D2, status="scheduled")])
    session.flush()
    backfill_elo_history(session, "nfl")
    session.commit()

    rating = _team_elo(session, 1, "nfl", 2, D2)

    assert rating > 1500.0, "winner still reading its pre-game seed"


def test_the_loser_is_rated_below_the_seed(tmp_path):
    session = _session(tmp_path)
    session.add_all([_game(1, D1), _game(2, D2, status="scheduled")])
    session.flush()
    backfill_elo_history(session, "nfl")
    session.commit()

    assert _team_elo(session, 2, "nfl", 2, D2) < 1500.0


def test_the_upcoming_rating_equals_what_the_replay_would_write(tmp_path):
    """Derived, not reimplemented.

    If this lookup and `backfill_elo_history` ever disagree, the model is
    trained on one number and served another. The replay's post-replay
    `final_ratings` is exactly the rating a next game should carry.
    """
    session = _session(tmp_path)
    session.add_all([_game(1, D1), _game(2, D2, hs=17, as_=24),
                     _game(3, D3, status="scheduled")])
    session.flush()
    result = backfill_elo_history(session, "nfl")
    session.commit()
    final = result["final_ratings"]

    assert _team_elo(session, 1, "nfl", 3, D3) == pytest.approx(final["AAA"])
    assert _team_elo(session, 2, "nfl", 3, D3) == pytest.approx(final["BBB"])


def test_a_game_with_its_own_row_still_reads_that_row(tmp_path):
    """No lookahead: a game already in the history keeps its stored PRE-game
    rating, never the rating that game produced."""
    session = _session(tmp_path)
    session.add_all([_game(1, D1), _game(2, D2)])
    session.flush()
    backfill_elo_history(session, "nfl")
    session.commit()

    stored = (session.query(EloHistory)
              .filter(EloHistory.team_id == 1, EloHistory.game_id == 2)
              .one().rating)
    assert _team_elo(session, 1, "nfl", 2, D2) == stored


def test_a_drawn_last_game_leaves_the_rating_unchanged(tmp_path):
    """The replay skips ties; advancing one step must skip them too."""
    session = _session(tmp_path)
    session.add_all([_game(1, D1, hs=20, as_=20), _game(2, D2, status="scheduled")])
    session.flush()
    backfill_elo_history(session, "nfl")
    session.commit()

    assert _team_elo(session, 1, "nfl", 2, D2) == 1500.0

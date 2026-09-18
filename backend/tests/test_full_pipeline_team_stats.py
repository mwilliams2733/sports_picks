"""The daily pipeline must produce team_stats rows going forward (plan 008).

Before this, `_store_games` wrote games and scores and nothing ever wrote a
TeamStat row, so the model's feature vector stayed empty forever. These tests
pin the wiring *and* the property that matters: the row the pipeline writes for
a game must not contain that game's own result.
"""
from datetime import date

import pytest

from backend.models import Base, Game, TeamStat
from backend.pipeline.full_pipeline import _store_games


def _espn(home, away, d, home_score=None, away_score=None, status="scheduled"):
    return {
        "home_team": home, "away_team": away,
        "home_team_name": f"{home} Team", "away_team_name": f"{away} Team",
        # 19:00Z is 2pm ET on the same day. T00:00Z would be 7pm ET the
        # PREVIOUS day, so the game would be filed under d-1 -- dates are
        # Eastern, which is what ESPN files events under.
        "date": f"{d.isoformat()}T19:00Z",
        "home_score": home_score, "away_score": away_score,
        "status": status,
    }


@pytest.fixture
def session(db_session):
    Base.metadata.create_all(db_session.get_bind())
    return db_session


def test_store_games_writes_team_stats_for_a_final_game(session):
    d1, d2 = date(2024, 1, 1), date(2024, 1, 5)
    _store_games(session, "nba", d1,
                 [_espn("ALP", "BET", d1, 110, 100, "final")])
    _store_games(session, "nba", d2,
                 [_espn("ALP", "BET", d2, 120, 100, "final")])

    g2 = session.query(Game).filter(Game.date == d2).one()
    rows = session.query(TeamStat).filter(TeamStat.game_id == g2.id).all()
    assert rows, "the daily pipeline must produce team_stats rows"
    assert {r.team_id for r in rows} == {g2.home_team_id, g2.away_team_id}

    pd = next(r.value for r in rows
              if r.team_id == g2.home_team_id and r.stat_type == "point_diff")
    # Game 1 only: +10. If game 2's own +20 leaked in this would be 15.0.
    assert pd == pytest.approx(10.0)

    rest = next(r.value for r in rows
                if r.team_id == g2.home_team_id and r.stat_type == "rest_days")
    assert rest == pytest.approx(4.0)


def test_store_games_writes_stats_for_an_upcoming_scheduled_game(session):
    """The game being predicted gets its own point-in-time row, so the
    prediction path never has to fall back to a staler game."""
    d1, d2 = date(2024, 1, 1), date(2024, 1, 5)
    _store_games(session, "nba", d1,
                 [_espn("ALP", "BET", d1, 110, 100, "final")])
    _store_games(session, "nba", d2, [_espn("ALP", "BET", d2)])

    g2 = session.query(Game).filter(Game.date == d2).one()
    assert g2.status == "scheduled"
    pd = (session.query(TeamStat)
          .filter(TeamStat.game_id == g2.id,
                  TeamStat.team_id == g2.home_team_id,
                  TeamStat.stat_type == "point_diff").one().value)
    assert pd == pytest.approx(10.0)


def test_rerunning_the_pipeline_does_not_duplicate_rows(session):
    d1 = date(2024, 1, 1)
    payload = [_espn("ALP", "BET", d1, 110, 100, "final")]
    _store_games(session, "nba", d1, payload)
    first = session.query(TeamStat).count()
    _store_games(session, "nba", d1, payload)
    assert session.query(TeamStat).count() == first


def test_store_games_keeps_elo_history_current(session):
    """The backfill is a one-off; the daily path must keep writing pre-game
    Elo rows, or elo_history stalls the day this lands."""
    from backend.models import EloHistory
    d1, d2 = date(2024, 1, 1), date(2024, 1, 5)
    _store_games(session, "nba", d1,
                 [_espn("ALP", "BET", d1, 130, 100, "final")])
    _store_games(session, "nba", d2,
                 [_espn("ALP", "BET", d2, 100, 140, "final")])

    g1, g2 = session.query(Game).order_by(Game.date).all()
    hist = {(e.team_id, e.game_id): e.rating
            for e in session.query(EloHistory).all()}
    assert len(hist) == 4

    # Game 1's rows are the initial rating: they cannot know game 1.
    assert hist[(g1.home_team_id, g1.id)] == pytest.approx(1500.0)
    # Game 2's row reflects game 1's win but not game 2's loss.
    assert hist[(g2.home_team_id, g2.id)] > 1500.0
    assert (hist[(g2.home_team_id, g2.id)]
            + hist[(g2.away_team_id, g2.id)]) == pytest.approx(3000.0)


def test_combat_sports_do_not_get_pregame_elo_rows(session):
    """mma/boxing Elo is owned by the grader and is post-game; the daily
    team-stats path must not write into it."""
    from backend.models import EloHistory
    d = date(2024, 1, 1)
    _store_games(session, "mma", d, [_espn("AAA", "BBB", d, 1, 0, "final")])
    assert session.query(EloHistory).count() == 0
    # Team stats are still produced.
    assert session.query(TeamStat).count() > 0

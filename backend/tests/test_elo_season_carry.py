"""The off-season Elo reset: one function, applied identically in training and serving.

`team_stats.backfill_elo_history` writes the history the model trains on;
`pick_generator._team_elo` serves the rating for an upcoming game. On opening
night the two must agree, or the model is served ratings on a different
basis from the one it learned.
"""
from datetime import date

import pytest

from backend.analysis import sport_constants
from backend.analysis.elo import season_carry
from backend.database import get_engine, get_session
from backend.models import Base, EloHistory, Game, Team
from backend.pipeline.pick_generator import _team_elo
from backend.pipeline.team_stats import backfill_elo_history


def test_season_carry_keeps_that_share_of_the_distance_from_1500():
    assert season_carry(1700.0, 0.75) == pytest.approx(1650.0)
    assert season_carry(1300.0, 0.5) == pytest.approx(1400.0)
    assert season_carry(1700.0, 1.0) == pytest.approx(1700.0)


def test_nfl_keeps_its_rating_across_the_off_season():
    """Measured: any reset made NFL's first four weeks worse."""
    assert sport_constants.get_elo_season_carry("nfl") == 1.0


@pytest.fixture
def session(tmp_path, monkeypatch):
    monkeypatch.setitem(sport_constants.ELO_SEASON_CARRY, "nba", 0.5)
    s = get_session(get_engine(str(tmp_path / "carry.db")))
    Base.metadata.create_all(s.get_bind())
    s.add_all([Team(id=1, name="A", abbreviation="A", sport="nba"),
               Team(id=2, name="B", abbreviation="B", sport="nba")])
    # Last season: A beats B three times.
    for gid, d in ((1, date(2026, 4, 1)), (2, date(2026, 4, 3)), (3, date(2026, 4, 5))):
        s.add(Game(id=gid, sport="nba", season="2025-26", date=d, status="final",
                   home_team_id=1, away_team_id=2, home_score=120, away_score=100))
    # Opening night, not yet played.
    s.add(Game(id=10, sport="nba", season="2026-27", date=date(2026, 10, 20),
               status="scheduled", home_team_id=1, away_team_id=2))
    s.commit()
    backfill_elo_history(s, "nba")
    s.commit()
    yield s
    s.close()


def _rating_out_of_last_season(s, team_id):
    """What the replay carries out of game 3, with no reset."""
    from backend.pipeline.pick_generator import _rating_after
    prior = s.query(EloHistory).filter_by(game_id=3, team_id=team_id).one()
    return _rating_after(s, "nba", prior, team_id)


def test_serving_resets_a_rating_carried_over_from_last_season(session):
    raw = _rating_out_of_last_season(session, 1)
    assert raw > 1500.0

    served = _team_elo(session, 1, "nba", game_id=10, game_date=date(2026, 10, 20))

    assert served == pytest.approx(season_carry(raw, 0.5))


def test_the_replay_writes_the_same_opening_night_rating_serving_used(session):
    served = {t: _team_elo(session, t, "nba", game_id=10, game_date=date(2026, 10, 20))
              for t in (1, 2)}
    game = session.get(Game, 10)
    game.status, game.home_score, game.away_score = "final", 101, 99
    session.commit()

    backfill_elo_history(session, "nba")
    session.commit()

    for t in (1, 2):
        stored = session.query(EloHistory).filter_by(game_id=10, team_id=t).one().rating
        assert stored == pytest.approx(served[t])


def test_no_reset_within_a_season(session):
    """Game 3's pre-game row continues game 2 with no reset."""
    rows = {r.game_id: r.rating for r in session.query(EloHistory).filter_by(team_id=1)}
    from backend.pipeline.pick_generator import _rating_after
    prior = session.query(EloHistory).filter_by(game_id=2, team_id=1).one()

    assert rows[3] == pytest.approx(_rating_after(session, "nba", prior, 1))


def test_nba_regresses_half_way_to_the_mean():
    """Measured: 0.5 was the best carry in each of 2024-25 and 2025-26."""
    assert sport_constants.get_elo_season_carry("nba") == 0.5


def test_without_a_game_id_the_reset_is_judged_from_dates(session):
    """The lookahead probe passes no id. Opening night by date alone must
    still reset, and a date inside last season must not."""
    raw = _rating_out_of_last_season(session, 1)

    assert _team_elo(session, 1, "nba", None, date(2026, 10, 25)) == pytest.approx(season_carry(raw, 0.5))
    assert _team_elo(session, 1, "nba", None, date(2026, 4, 10)) == pytest.approx(raw)

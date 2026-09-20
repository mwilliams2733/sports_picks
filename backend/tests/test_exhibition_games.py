"""All-Star games are exhibitions, not results.

ESPN labels them ``season.type = 2`` -- regular season -- so the season
block alone cannot find them. The 2026 NBA All-Star round robin therefore
sat in `games` as three nba finals with totals of 72, 82 and 93 against a
real nba average of 230.9, inside CalibratedModel's training pool.

The competition block does distinguish them: "STD" for a normal game,
"SEMI" for a conference final, "ALLSTAR" for these.
"""
import datetime

import pytest

from backend.analysis.calibrated_model import (
    MIN_TRAINING_GAMES,
    NON_COMPETITIVE_PHASES,
    CalibratedModel,
)
from backend.collectors.espn import season_type_of
from backend.database import get_engine, get_session
from backend.models import Base, Game, Team


def _event(season_type=2):
    return {"season": {"year": 2026, "type": season_type}}


def test_a_normal_game_keeps_its_season_phase():
    comp = {"type": {"id": "1", "abbreviation": "STD"}}
    assert season_type_of(_event(2), comp) == "regular"


def test_a_playoff_game_keeps_its_season_phase():
    comp = {"type": {"id": "16", "abbreviation": "SEMI"}}
    assert season_type_of(_event(3), comp) == "postseason"


def test_an_all_star_game_is_found_despite_the_season_block():
    """The regression: ESPN says type 2 here, and the season block is wrong."""
    comp = {"type": {"id": "4", "abbreviation": "ALLSTAR"}}
    assert season_type_of(_event(2), comp) == "allstar"


def test_the_competition_type_is_read_case_insensitively():
    comp = {"type": {"id": "4", "abbreviation": "allstar"}}
    assert season_type_of(_event(2), comp) == "allstar"


def test_a_missing_competition_type_falls_back_to_the_season_block():
    assert season_type_of(_event(3), {}) == "postseason"


def test_an_absent_season_block_is_unknown():
    assert season_type_of({}, {"type": {"abbreviation": "STD"}}) == "unknown"


# --------------------------------------------------------------------------
# Exclusion from training.
# --------------------------------------------------------------------------

def _seed(session, n, season_type, start_id):
    session.add_all([
        Team(id=start_id, name=f"H{start_id}", abbreviation=f"H{start_id}",
             sport="nba"),
        Team(id=start_id + 1, name=f"A{start_id}", abbreviation=f"A{start_id}",
             sport="nba"),
    ])
    session.flush()
    for i in range(n):
        session.add(Game(
            sport="nba", season="2026",
            date=datetime.date(2026, 1, 1) + datetime.timedelta(days=i),
            home_team_id=start_id, away_team_id=start_id + 1,
            home_score=110 if i % 2 else 100,
            away_score=100 if i % 2 else 110,
            status="final", season_type=season_type))


@pytest.fixture()
def session(tmp_path):
    s = get_session(get_engine(str(tmp_path / "x.db")))
    Base.metadata.create_all(s.get_bind())
    return s


def test_exhibitions_are_not_trained_on(session):
    _seed(session, MIN_TRAINING_GAMES + 5, "regular", 100)
    _seed(session, 40, "allstar", 200)
    session.commit()

    m = CalibratedModel()
    m.train_from_db(session)

    assert m.trained
    assert m.n_training_games == MIN_TRAINING_GAMES + 5


def test_preseason_is_excluded_too(session):
    _seed(session, MIN_TRAINING_GAMES + 5, "regular", 100)
    _seed(session, 40, "preseason", 200)
    session.commit()

    m = CalibratedModel()
    m.train_from_db(session)

    assert m.n_training_games == MIN_TRAINING_GAMES + 5


def test_rows_predating_the_column_are_kept(session):
    """`unknown` is most of the history; dropping it would throw it away."""
    _seed(session, MIN_TRAINING_GAMES + 5, "unknown", 100)
    session.commit()

    m = CalibratedModel()
    m.train_from_db(session)

    assert m.trained
    assert m.n_training_games == MIN_TRAINING_GAMES + 5


def test_postseason_is_still_trained_on(session):
    """Playoff games are real results, however differently they score."""
    _seed(session, MIN_TRAINING_GAMES + 5, "regular", 100)
    _seed(session, 10, "postseason", 200)
    session.commit()

    m = CalibratedModel()
    m.train_from_db(session)

    assert m.n_training_games == MIN_TRAINING_GAMES + 15


def test_the_excluded_set_is_what_it_claims():
    assert set(NON_COMPETITIVE_PHASES) == {"allstar", "preseason"}

"""The feature row must carry sport, with a stable vocabulary."""

from datetime import date, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.analysis.calibrated_model import (
    SPORT_VOCAB,
    CalibratedModel,
    build_feature_row,
)
from backend.data_types import GameData, TeamStats
from backend.models import Base, Game, Team


def _row(sport):
    return build_feature_row(1.0, 2.0, 3.0, 4.0, 5.0, sport)


def test_legacy_features_come_first_and_unchanged():
    row = _row("nba")
    assert row[:5] == [1.0, 2.0, 3.0, 4.0, 5.0]


def test_row_length_is_five_plus_the_vocabulary():
    assert len(_row("nba")) == 5 + len(SPORT_VOCAB)


def test_exactly_one_sport_slot_is_set():
    row = _row("ncaab")
    assert sum(row[5:]) == 1.0
    assert row[5 + SPORT_VOCAB.index("ncaab")] == 1.0


def test_an_unknown_sport_sets_no_slot_rather_than_raising():
    """Prediction must not crash on a sport the vocabulary omits.

    All-zero sport slots fall back to the shared intercept, which is exactly
    the old behaviour -- a safe degradation rather than an exception in the
    middle of pick generation.
    """
    row = _row("curling")
    assert sum(row[5:]) == 0.0
    assert len(row) == 5 + len(SPORT_VOCAB)


def test_vocabulary_is_fixed_not_derived_from_data():
    """Feature positions must not move when the database changes.

    A vocabulary built from whatever sports happen to be in the training set
    would silently reassign column meanings between one training run and the
    next.
    """
    assert isinstance(SPORT_VOCAB, tuple)
    assert "ncaab" in SPORT_VOCAB and "nba" in SPORT_VOCAB
    assert SPORT_VOCAB == tuple(sorted(SPORT_VOCAB)), "order must be deterministic"


def test_two_sports_produce_different_rows():
    assert _row("nba") != _row("ncaab")


# --------------------------------------------------------------------------
# Acceptance: does the *fitted* baseline actually move, or did we only change
# the shape of the feature row? Everything above this line is structural.
# --------------------------------------------------------------------------


def _seed(session, sport, n_games, home_win_rate, start_team_id):
    """n_games of one sport where the home side wins home_win_rate of them.

    No TeamStat rows are written, so every difference feature falls through
    to its default and comes out zero -- identical team strength on both
    sides. The only thing distinguishing the sports is the label rate, so
    any probability difference the model shows is attributable to the sport
    encoding and nothing else.
    """
    session.add_all([
        Team(id=start_team_id, name=f"{sport}H", abbreviation=f"{sport[:2]}H",
             sport=sport),
        Team(id=start_team_id + 1, name=f"{sport}A", abbreviation=f"{sport[:2]}A",
             sport=sport),
    ])
    wins = round(n_games * home_win_rate)
    for i in range(n_games):
        home_won = i < wins
        session.add(Game(
            sport=sport, season="2026", date=date(2026, 1, 1) + timedelta(days=i),
            home_team_id=start_team_id, away_team_id=start_team_id + 1,
            home_score=101 if home_won else 99,
            away_score=99 if home_won else 101,
            status="final",
        ))


def _flat_game(sport):
    """A GameData with every difference feature at zero."""
    # TeamStats has no defaults for home_record/away_record/last_n_record/
    # strength_of_schedule -- omitting them raises TypeError.
    stats = TeamStats(
        point_diff=0.0, home_record=(0, 0), away_record=(0, 0),
        last_n_record=(0, 0), offensive_rating=100.0, defensive_rating=100.0,
        pace=100.0, strength_of_schedule=0.0, elo_rating=1500.0, rest_days=1,
    )
    return GameData(
        game_id=1, sport=sport, date=date(2026, 6, 1),
        home_team_id=1, away_team_id=2,
        home_stats=stats, away_stats=stats, odds=[],
    )


@pytest.fixture()
def trained_model():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as s:
        # Deliberately lopsided, the way production is: one sport dominates.
        _seed(s, "nba", 400, 0.55, 100)
        _seed(s, "ncaab", 80, 0.75, 200)
        s.commit()
        m = CalibratedModel()
        m.train_from_db(s)
        assert m.trained, "model must train for this test to mean anything"
        yield m


def test_the_fitted_baseline_differs_by_sport(trained_model):
    """The whole point of the change.

    With identical zeroed features, the only input differing is the sport, so
    the predictions must differ. Before this change they were identical by
    construction.
    """
    nba = trained_model.predict_home_win_prob(_flat_game("nba"))
    ncaab = trained_model.predict_home_win_prob(_flat_game("ncaab"))
    assert ncaab > nba + 0.08, (
        f"ncaab={ncaab:.3f} nba={nba:.3f}: the sport encoding is not moving "
        "the baseline"
    )


def test_each_baseline_lands_near_its_own_home_rate(trained_model):
    """Not merely different -- directionally right.

    L2 shrinks the minority sport toward the pool, so the tolerance is wide.
    The pooled rate here is ~0.583; ncaab must sit clearly above it.
    """
    nba = trained_model.predict_home_win_prob(_flat_game("nba"))
    ncaab = trained_model.predict_home_win_prob(_flat_game("ncaab"))
    assert 0.48 < nba < 0.64, f"nba baseline {nba:.3f} not near its 0.55"
    assert ncaab > 0.64, f"ncaab baseline {ncaab:.3f} not above the pooled rate"


def test_an_unseen_sport_still_predicts(trained_model):
    """mlb is in the vocabulary but absent from this training set."""
    p = trained_model.predict_home_win_prob(_flat_game("mlb"))
    assert 0.0 < p < 1.0

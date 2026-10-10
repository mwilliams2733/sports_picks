"""Combat bouts never reach the team-sport model or its reports.

Final review, 2026-10-10: `train_from_db`, the calibration and sport-signal
reports and the LightGBM trainer selected every final game of every sport.
Combat rows are not team games, their `elo_history` is written AFTER the bout
(so `elo_diff` carries the result), and the UFC history import adds ~8,750
of them -- ~73% of the training set -- whose leaked slope and first-listed
"home" win rate would be pooled into NFL/MLB/NBA.
"""
from datetime import date

import backend.analysis.calibrated_model as cm
from backend.analysis import calibration_report, sport_signal_report
from backend.analysis.calibrated_model import CalibratedModel, final_team_games
from backend.analysis.variants.ensemble import EnsembleStrategy
from backend.models import Base, Game, Team


def _seed(session):
    for tid, sport in [(1, "nfl"), (2, "nfl"), (3, "mma"), (4, "mma"), (5, "boxing"), (6, "boxing")]:
        session.add(Team(id=tid, name=f"T{tid}", abbreviation=f"T{tid}", sport=sport))
    session.flush()
    for gid, sport, h, a in [(1, "nfl", 1, 2), (2, "mma", 3, 4), (3, "boxing", 5, 6)]:
        session.add(Game(id=gid, sport=sport, season="2026", date=date(2026, 9, 1), status="final",
                         home_team_id=h, away_team_id=a, home_score=1, away_score=0))
    session.commit()


def test_training_sets_and_reports_hold_team_sports_only(db_engine, db_session):
    Base.metadata.create_all(db_engine)
    _seed(db_session)
    assert {g.sport for g in final_team_games(db_session)} == {"nfl"}
    assert {g.sport for g in calibration_report._final_games(db_session)} == {"nfl"}
    assert {g.sport for g in sport_signal_report._final_games(db_session)} == {"nfl"}


def test_both_trainers_read_the_team_sport_games(db_engine, db_session, monkeypatch):
    Base.metadata.create_all(db_engine)
    _seed(db_session)
    calls = []

    def spy(session, sport=None):
        calls.append(sport)
        return []

    monkeypatch.setattr(cm, "final_team_games", spy)
    CalibratedModel().train_from_db(db_session)
    EnsembleStrategy("ensemble", {}).train_lgbm_from_db(db_session)
    assert len(calls) == 2

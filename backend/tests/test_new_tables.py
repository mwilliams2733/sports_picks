from datetime import date, datetime, timezone
from backend.models import Base, CalibrationHistory, ModelMetrics, UserProfile, PaperPick, Team, Game
from backend.database import get_engine, get_session


def _setup():
    engine = get_engine(":memory:")
    Base.metadata.create_all(engine)
    return get_session(engine)


def test_calibration_history_creation():
    session = _setup()
    row = CalibrationHistory(
        date=date(2026, 3, 16), sport="nba", confidence_tier=5,
        predicted_win_rate=0.70, actual_win_rate=0.62,
        sample_size=45, old_threshold=12.0, new_threshold=13.0,
    )
    session.add(row)
    session.commit()
    result = session.query(CalibrationHistory).first()
    assert result.sport == "nba"
    assert result.confidence_tier == 5
    assert result.new_threshold == 13.0
    session.close()


def test_model_metrics_creation():
    session = _setup()
    row = ModelMetrics(
        date=date(2026, 3, 16), sport="nba", model_version="lgbm_v1",
        accuracy=0.65, log_loss=0.62,
        feature_importances='{"elo_diff": 0.25, "point_diff": 0.20}',
        training_games=950,
    )
    session.add(row)
    session.commit()
    result = session.query(ModelMetrics).first()
    assert result.model_version == "lgbm_v1"
    assert result.training_games == 950
    session.close()


def test_user_profile_streak_columns():
    session = _setup()
    user = UserProfile(name="TestUser")
    session.add(user)
    session.commit()
    assert user.current_streak == 0
    assert user.best_streak == 0
    assert user.streak_type == "none"
    user.current_streak = 5
    user.streak_type = "win"
    session.commit()
    session.close()


def test_paper_pick_graded_at():
    session = _setup()
    home = Team(name="Home Team", abbreviation="HM", sport="nba")
    away = Team(name="Away Team", abbreviation="AW", sport="nba")
    session.add_all([home, away])
    session.commit()
    game = Game(
        sport="nba", season="2025-26", date=date(2026, 3, 16),
        home_team_id=home.id, away_team_id=away.id,
    )
    session.add(game)
    session.commit()
    user = UserProfile(name="TestUser2")
    session.add(user)
    session.commit()
    pick = PaperPick(
        user_id=user.id, game_id=game.id, pick_type="moneyline",
        pick_value="HOME ML", odds=-150, stake=1000,
    )
    session.add(pick)
    session.commit()
    assert pick.graded_at is None
    pick.graded_at = datetime.now(tz=timezone.utc)
    session.commit()
    assert pick.graded_at is not None
    session.close()

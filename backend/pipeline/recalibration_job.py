"""Nightly recalibration job: grade picks, retrain model, adjust thresholds."""
import json
import logging
from datetime import date

from backend.analysis.ml_model import MIN_ML_GAMES
from backend.analysis.recalibrator import Recalibrator
from backend.analysis.variants.ensemble import EnsembleStrategy
from backend.database import get_engine, get_session
from backend.models import Base, Game, ModelMetrics
from backend.pipeline.scheduler import grade_pending_picks

logger = logging.getLogger(__name__)


def run_recalibration(db_path: str = "sports_picks.db") -> dict:
    """Execute nightly recalibration pipeline.

    Returns summary dict of actions taken.
    """
    engine = get_engine(db_path)
    session = get_session(engine)
    summary = {"graded": 0, "recalibrated": {}, "model_retrained": False}

    try:
        # Step 1: Grade remaining picks
        grade_pending_picks(session)

        # Step 2: Recalibrate confidence thresholds per sport
        for sport in ("nba", "nfl", "ncaab", "ncaaf"):
            game_count = (
                session.query(Game)
                .filter(Game.sport == sport, Game.status == "final")
                .count()
            )
            if game_count < 30:
                continue

            recal = Recalibrator(session, sport=sport)
            adjustments = recal.run(days=90)
            if adjustments:
                summary["recalibrated"][sport] = adjustments

        # Step 3: Retrain LightGBM for sports with enough games
        for sport in ("nba", "nfl", "ncaab", "ncaaf"):
            sport_count = (
                session.query(Game)
                .filter(Game.sport == sport, Game.status == "final")
                .count()
            )
            if sport_count >= MIN_ML_GAMES:
                logger.info("Retraining LightGBM for %s with %d games", sport, sport_count)
                strategy = EnsembleStrategy(name="recal", config={})
                strategy.train_lgbm_from_db(session)
                summary["model_retrained"] = True
                importances = {}
                if strategy._lgbm_model and strategy._lgbm_model.trained:
                    importances = strategy._lgbm_model.feature_importances()
                session.add(ModelMetrics(
                    date=date.today(),
                    sport=sport,
                    model_version="lgbm_v1_regression",
                    training_games=sport_count,
                    feature_importances=json.dumps(importances),
                ))

        session.commit()
        logger.info("Recalibration complete: %s", summary)

    except Exception:
        session.rollback()
        logger.exception("Recalibration failed")
        raise
    finally:
        session.close()

    return summary

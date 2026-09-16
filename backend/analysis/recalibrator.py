"""Confidence threshold recalibration based on actual pick performance."""
import logging
from datetime import date, timedelta

from sqlalchemy.orm import Session

from backend.analysis.confidence import DEFAULT_THRESHOLDS
from backend.models import CalibrationHistory, Game, PickModel, PickResult

logger = logging.getLogger(__name__)

MIN_PICKS_PER_TIER = 20
ADJUSTMENT_STEP = 1.0  # percentage points per cycle
EXPECTED_WIN_RATES = {5: 0.70, 4: 0.63, 3: 0.57, 2: 0.53, 1: 0.50}
DEVIATION_THRESHOLD = 0.05  # 5 percentage points


class Recalibrator:
    def __init__(self, session: Session, sport: str = "nba"):
        self.session = session
        self.sport = sport

    def run(self, days: int = 90) -> dict:
        """Analyze pick performance and adjust confidence thresholds.

        Returns dict of tier -> {actual_rate, expected_rate, direction, new_threshold, ...}.
        """
        cutoff = date.today() - timedelta(days=days)

        # Get current thresholds
        thresholds = dict(DEFAULT_THRESHOLDS)
        latest = (
            self.session.query(CalibrationHistory)
            .filter(CalibrationHistory.sport == self.sport)
            .order_by(CalibrationHistory.date.desc())
            .limit(5)
            .all()
        )
        for row in latest:
            thresholds[row.confidence_tier] = row.new_threshold

        adjustments = {}

        for tier in (5, 4, 3, 2, 1):
            picks_with_results = (
                self.session.query(PickModel, PickResult)
                .join(PickResult, PickResult.pick_id == PickModel.id)
                .join(Game, PickModel.game_id == Game.id)
                .filter(
                    Game.sport == self.sport,
                    PickModel.confidence == tier,
                    PickModel.created_at >= cutoff,
                )
                .all()
            )

            total = len(picks_with_results)
            wins = sum(1 for p, r in picks_with_results if r.result == "win")
            pushes = sum(1 for p, r in picks_with_results if r.result == "push")
            decided = total - pushes
            if decided < MIN_PICKS_PER_TIER:
                logger.info(
                    "Tier %d: only %d decided picks (need %d), skipping",
                    tier, decided, MIN_PICKS_PER_TIER,
                )
                continue

            actual_rate = wins / decided
            expected_rate = EXPECTED_WIN_RATES.get(tier, 0.5)
            deviation = actual_rate - expected_rate
            old_threshold = thresholds.get(tier, DEFAULT_THRESHOLDS[tier])

            if abs(deviation) < DEVIATION_THRESHOLD:
                continue

            if deviation < 0:
                new_threshold = old_threshold + ADJUSTMENT_STEP
                direction = "tighten"
            else:
                new_threshold = max(1.0, old_threshold - ADJUSTMENT_STEP)
                direction = "loosen"

            adjustments[tier] = {
                "actual_rate": actual_rate,
                "expected_rate": expected_rate,
                "direction": direction,
                "old_threshold": old_threshold,
                "new_threshold": new_threshold,
                "sample_size": decided,
            }

            self.session.add(CalibrationHistory(
                date=date.today(),
                sport=self.sport,
                confidence_tier=tier,
                predicted_win_rate=expected_rate,
                actual_win_rate=actual_rate,
                sample_size=decided,
                old_threshold=old_threshold,
                new_threshold=new_threshold,
            ))

            logger.info(
                "Tier %d: %.1f%% actual vs %.1f%% expected — %s threshold %.1f → %.1f",
                tier, actual_rate * 100, expected_rate * 100,
                direction, old_threshold, new_threshold,
            )

        self.session.commit()
        return adjustments

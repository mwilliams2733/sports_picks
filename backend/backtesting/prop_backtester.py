import math
from datetime import date
from sqlalchemy.orm import Session

from backend.models import PlayerStat
from backend.analysis.prop_confidence import calculate_prop_confidence
from backend.analysis.odds_utils import calculate_payout

try:
    from scipy.stats import norm, poisson

    _HAS_SCIPY = True
except ImportError:
    _HAS_SCIPY = False

PROP_MARKETS = ["player_points", "player_rebounds", "player_assists"]
MARKET_TO_FIELD = {
    "player_points": "points",
    "player_rebounds": "rebounds",
    "player_assists": "assists",
}

# Count-based markets where Poisson is appropriate for low means
_COUNT_BASED_MARKETS = {"player_rebounds", "player_assists"}

# Minimum data points needed for variance-based analysis
_MIN_VARIANCE_SAMPLES = 3


def _compute_exceedance_prob(
    mean: float, variance: float, line: float, market: str
) -> float:
    """Compute P(X > line) using an appropriate distribution."""
    if not _HAS_SCIPY:
        return 1.0 if mean > line else 0.0

    std = variance**0.5

    # Use Poisson for count-based stats with low mean
    if market in _COUNT_BASED_MARKETS and mean < 10 and mean > 0:
        return float(1.0 - poisson.cdf(math.floor(line), mu=mean))

    # Normal distribution
    if std < 1e-9:
        return 1.0 if mean > line else 0.0

    return float(1.0 - norm.cdf(line, loc=mean, scale=std))


class PropBacktester:
    def __init__(self, config: dict):
        self.recent_weight = config.get("recent_weight", 0.6)
        self.season_weight = config.get("season_weight", 0.4)
        self.min_edge = config.get("min_edge", 5.0)
        self.lookback = config.get("lookback", 5)
        self.min_minutes = config.get("min_minutes", 15)

    def backtest(
        self,
        session: Session,
        sport: str,
        start_date: date,
        end_date: date,
    ) -> dict:
        # Fetch all game_log PlayerStats for this sport in the date range or before end_date
        all_logs = (
            session.query(PlayerStat)
            .filter(
                PlayerStat.sport == sport,
                PlayerStat.stat_type == "game_log",
                PlayerStat.game_date <= end_date,
            )
            .order_by(PlayerStat.game_date)
            .all()
        )

        # Group logs by player
        player_logs: dict[str, list[PlayerStat]] = {}
        for log in all_logs:
            player_logs.setdefault(log.player_name, []).append(log)

        wins = 0
        losses = 0
        total_profit = 0.0
        picks = []
        by_market: dict[str, dict] = {m: {"wins": 0, "losses": 0} for m in PROP_MARKETS}
        by_confidence: dict[int, dict] = {i: {"wins": 0, "losses": 0} for i in range(6)}

        for player_name, logs in player_logs.items():
            # Logs are sorted by game_date
            for i, game_log in enumerate(logs):
                game_date = game_log.game_date
                if game_date is None:
                    continue
                if not (start_date <= game_date <= end_date):
                    continue

                # Respect min_minutes filter
                if game_log.minutes is not None and game_log.minutes < self.min_minutes:
                    continue

                # Pre-game data: all logs before this game
                prior_logs = logs[:i]
                if len(prior_logs) < self.lookback:
                    continue

                recent_logs = prior_logs[-self.lookback :]

                for market in PROP_MARKETS:
                    field = MARKET_TO_FIELD[market]

                    # Season avg from all pre-game data
                    all_vals = [getattr(lg, field) for lg in prior_logs if getattr(lg, field) is not None]
                    if not all_vals:
                        continue
                    season_avg_val = sum(all_vals) / len(all_vals)

                    # Recent avg from last N logs
                    recent_vals = [getattr(lg, field) for lg in recent_logs if getattr(lg, field) is not None]
                    if not recent_vals:
                        continue
                    recent_avg_val = sum(recent_vals) / len(recent_vals)

                    # Projection
                    projection = self.season_weight * season_avg_val + self.recent_weight * recent_avg_val

                    # Use season avg as the synthetic line
                    synthetic_line = season_avg_val
                    if synthetic_line == 0:
                        continue

                    # --- Distribution-based edge computation ---
                    use_distribution = _HAS_SCIPY and len(all_vals) >= _MIN_VARIANCE_SAMPLES

                    if use_distribution:
                        # Compute variance from all prior game values
                        mean_val = sum(all_vals) / len(all_vals)
                        variance = sum((v - mean_val) ** 2 for v in all_vals) / len(all_vals)

                        # Exceedance probability P(X > line) using projection as center
                        exceedance_prob = _compute_exceedance_prob(
                            mean=projection,
                            variance=variance,
                            line=synthetic_line,
                            market=market,
                        )

                        # Determine predicted direction and directional probability
                        if projection > synthetic_line:
                            predicted = "over"
                            directional_prob = exceedance_prob
                        else:
                            predicted = "under"
                            directional_prob = 1.0 - exceedance_prob

                        # Convert to edge percentage
                        edge = (directional_prob - 0.5) * 200

                        if edge < self.min_edge:
                            continue
                    else:
                        # Fallback: simple average-based comparison
                        edge = abs(projection - synthetic_line) / synthetic_line * 100
                        if edge < self.min_edge:
                            continue

                        if projection > synthetic_line:
                            predicted = "over"
                        else:
                            predicted = "under"

                    # Grade: did the actual stat match the predicted direction?
                    actual_val = getattr(game_log, field)
                    if actual_val is None:
                        continue

                    if predicted == "over":
                        correct = actual_val > synthetic_line
                    else:
                        correct = actual_val < synthetic_line

                    confidence = calculate_prop_confidence(edge)
                    odds = -110
                    payout = calculate_payout(odds)

                    pick_record = {
                        "player_name": player_name,
                        "market": market,
                        "game_date": str(game_date),
                        "projection": round(projection, 2),
                        "line": round(synthetic_line, 2),
                        "predicted": predicted,
                        "actual": actual_val,
                        "edge_pct": round(edge, 2),
                        "confidence": confidence,
                        "result": "win" if correct else "loss",
                    }
                    picks.append(pick_record)

                    if correct:
                        wins += 1
                        total_profit += payout
                        by_market[market]["wins"] += 1
                        by_confidence[confidence]["wins"] += 1
                    else:
                        losses += 1
                        total_profit -= 1.0
                        by_market[market]["losses"] += 1
                        by_confidence[confidence]["losses"] += 1

        total = wins + losses
        hit_rate = round(wins / total * 100, 2) if total > 0 else 0.0
        roi = round(total_profit / total * 100, 2) if total > 0 else 0.0

        return {
            "wins": wins,
            "losses": losses,
            "total": total,
            "hit_rate": hit_rate,
            "roi": roi,
            "total_profit": round(total_profit, 4),
            "picks": picks,
            "by_market": by_market,
            "by_confidence": by_confidence,
        }

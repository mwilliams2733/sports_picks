import logging

import numpy as np
from scipy.stats import norm

from backend.analysis.strategy import Strategy
from backend.analysis.confidence import calculate_confidence
from backend.analysis.odds_utils import american_to_implied_prob, remove_vig
from backend.analysis.calibrated_model import CalibratedModel, extract_features, features_to_array, _stat_value
from backend.analysis.ml_model import LightGBMModel, MIN_ML_GAMES
from backend.analysis.kelly import fractional_kelly
from backend.data_types import GameData, TeamStats, Pick

logger = logging.getLogger(__name__)

# NBA empirical standard deviations for CDF-based edge calculations
POINT_DIFF_STD = 12.0   # game-to-game margin std dev
TOTAL_POINTS_STD = 15.0  # game-to-game total std dev

class EnsembleStrategy(Strategy):
    _calibrated: CalibratedModel | None = None

    def __init__(self, name: str, config: dict):
        super().__init__(name, config)
        self._lgbm_model: LightGBMModel | None = None

    def predict(self, game: GameData) -> list[Pick]:
        if not game.odds: return []
        picks = []
        min_edge = self.config.get("min_edge", 5.0)
        kelly_fraction = self.config.get("kelly_fraction", 0.25)
        home_prob = self._calibrated_probability(game)
        away_prob = 1.0 - home_prob
        avg_odds = self._average_odds(game)
        if avg_odds is None: return []

        # Moneyline picks — use vig-adjusted implied probabilities
        if avg_odds["moneyline_home"] is not None:
            raw_home = american_to_implied_prob(avg_odds["moneyline_home"])
            raw_away = american_to_implied_prob(avg_odds["moneyline_away"])
            implied_home, implied_away = remove_vig(raw_home, raw_away)
            home_edge = (home_prob - implied_home) * 100
            away_edge = (away_prob - implied_away) * 100
            if home_edge >= min_edge:
                models = self._count_agreeing_models(game, "home")
                picks.append(Pick(game_id=game.game_id, pick_type="moneyline", pick_value="HOME ML",
                    confidence=calculate_confidence(home_edge, models), edge_pct=round(home_edge, 1),
                    model_probability=round(home_prob, 4), implied_probability=round(implied_home, 4),
                    odds_at_pick=avg_odds["moneyline_home"],
                    suggested_unit_size=fractional_kelly(home_prob, avg_odds["moneyline_home"], kelly_fraction)))
            elif away_edge >= min_edge:
                models = self._count_agreeing_models(game, "away")
                picks.append(Pick(game_id=game.game_id, pick_type="moneyline", pick_value="AWAY ML",
                    confidence=calculate_confidence(away_edge, models), edge_pct=round(away_edge, 1),
                    model_probability=round(away_prob, 4), implied_probability=round(implied_away, 4),
                    odds_at_pick=avg_odds["moneyline_away"],
                    suggested_unit_size=fractional_kelly(away_prob, avg_odds["moneyline_away"], kelly_fraction)))

        # Spread picks — distribution-based: P(cover) via normal CDF
        if avg_odds.get("spread_home") is not None:
            predicted_diff, diff_std = self._predicted_point_diff_ml(game)
            spread_home = avg_odds["spread_home"]  # e.g., -3.5 for home favorite
            # Home covers when margin > abs(spread) for favorites
            cover_threshold = -spread_home  # -(-3.5) = 3.5: home must win by >3.5
            home_cover_prob = self._spread_cover_prob(predicted_diff, cover_threshold, std=diff_std)
            away_cover_prob = 1.0 - home_cover_prob
            spread_fair = 0.5  # spread markets are ~50/50 after vig by design
            home_spread_edge = (home_cover_prob - spread_fair) * 100
            away_spread_edge = (away_cover_prob - spread_fair) * 100
            if home_spread_edge >= min_edge:
                models = self._count_agreeing_models(game, "home")
                pick_value = f"HOME {spread_home:+g}"
                picks.append(Pick(game_id=game.game_id, pick_type="spread",
                    pick_value=pick_value,
                    confidence=calculate_confidence(home_spread_edge, models),
                    edge_pct=round(home_spread_edge, 1),
                    model_probability=round(home_cover_prob, 4),
                    implied_probability=spread_fair,
                    odds_at_pick=-110,
                    suggested_unit_size=fractional_kelly(home_cover_prob, -110, kelly_fraction)))
            elif away_spread_edge >= min_edge:
                models = self._count_agreeing_models(game, "away")
                spread_away = avg_odds["spread_away"]
                pick_value = f"AWAY +{spread_away:g}" if spread_away >= 0 else f"AWAY {spread_away:g}"
                picks.append(Pick(game_id=game.game_id, pick_type="spread",
                    pick_value=pick_value,
                    confidence=calculate_confidence(away_spread_edge, models),
                    edge_pct=round(away_spread_edge, 1),
                    model_probability=round(away_cover_prob, 4),
                    implied_probability=spread_fair,
                    odds_at_pick=-110,
                    suggested_unit_size=fractional_kelly(away_cover_prob, -110, kelly_fraction)))

        # Over/Under picks — distribution-based: P(over) via normal CDF
        if avg_odds.get("over_under") is not None:
            predicted_total = self._predicted_total(game)
            ou_line = avg_odds["over_under"]
            over_prob = self._over_probability(predicted_total, ou_line, std=TOTAL_POINTS_STD)
            under_prob = 1.0 - over_prob
            ou_fair = 0.5  # O/U markets are ~50/50 after vig by design
            over_edge = (over_prob - ou_fair) * 100
            under_edge = (under_prob - ou_fair) * 100
            if over_edge >= min_edge:
                models = self._count_total_agreeing_models(game, True)
                pick_value = f"Over {ou_line:g}"
                picks.append(Pick(game_id=game.game_id, pick_type="over_under",
                    pick_value=pick_value,
                    confidence=calculate_confidence(over_edge, models),
                    edge_pct=round(over_edge, 1),
                    model_probability=round(over_prob, 4),
                    implied_probability=ou_fair,
                    odds_at_pick=-110,
                    suggested_unit_size=fractional_kelly(over_prob, -110, kelly_fraction)))
            elif under_edge >= min_edge:
                models = self._count_total_agreeing_models(game, False)
                pick_value = f"Under {ou_line:g}"
                picks.append(Pick(game_id=game.game_id, pick_type="over_under",
                    pick_value=pick_value,
                    confidence=calculate_confidence(under_edge, models),
                    edge_pct=round(under_edge, 1),
                    model_probability=round(under_prob, 4),
                    implied_probability=ou_fair,
                    odds_at_pick=-110,
                    suggested_unit_size=fractional_kelly(under_prob, -110, kelly_fraction)))

        return picks

    def _calibrated_probability(self, game: GameData) -> float:
        """Get calibrated P(home win), preferring LightGBM for NBA."""
        # Use LightGBM regression model for NBA if trained
        if game.sport == "nba" and self._lgbm_model and self._lgbm_model.trained:
            feature_dict = extract_features(game)
            feature_array = features_to_array(feature_dict)
            home_prob = self._lgbm_model.home_win_prob(np.array([feature_array]))
        else:
            home_prob = self._legacy_calibrated_probability(game)

        # Schedule adjustments
        if game.home_stats.is_schedule_fatigued:
            home_prob -= 0.03 * game.home_stats.schedule_fatigue_score
        if game.away_stats.is_schedule_fatigued:
            home_prob += 0.03 * game.away_stats.schedule_fatigue_score
        if game.home_stats.is_lookahead_spot:
            home_prob -= 0.04
        if game.away_stats.is_lookahead_spot:
            home_prob += 0.04
        return max(0.01, min(0.99, home_prob))

    def _legacy_calibrated_probability(self, game: GameData) -> float:
        """Fallback: logistic regression or heuristic sigmoid."""
        if EnsembleStrategy._calibrated is None:
            EnsembleStrategy._calibrated = CalibratedModel()
            try:
                from backend.database import get_engine, get_session
                import os
                db_path = os.environ.get("DB_PATH", "sports_picks.db")
                engine = get_engine(db_path)
                session = get_session(engine)
                try:
                    EnsembleStrategy._calibrated.train_from_db(session)
                finally:
                    session.close()
            except Exception:
                logger.warning("Could not train calibrated model; using fallback.", exc_info=True)
        return EnsembleStrategy._calibrated.predict_home_win_prob(game)

    def _predicted_point_diff_ml(self, game: GameData) -> tuple[float, float]:
        """Get predicted margin and residual std from LightGBM.

        Returns (predicted_margin, residual_std).
        Falls back to heuristic if ML not available.
        """
        if game.sport == "nba" and self._lgbm_model and self._lgbm_model.trained:
            feature_dict = extract_features(game)
            feature_array = features_to_array(feature_dict)
            margin = self._lgbm_model.predict(np.array([feature_array]))
            std = self._lgbm_model.residual_std or POINT_DIFF_STD
            return margin, std
        return self._predicted_point_diff(game), POINT_DIFF_STD

    def train_lgbm_from_db(self, session) -> None:
        """Train LightGBM on all completed games from the database."""
        from backend.models import Game, TeamStat, EloRating

        games = session.query(Game).filter(
            Game.status == "final",
            Game.home_score.isnot(None),
        ).all()

        if len(games) < MIN_ML_GAMES:
            logger.info("Only %d games, need %d for ML", len(games), MIN_ML_GAMES)
            return

        X_list, y_list, dates_list = [], [], []
        elo_map = {e.team_id: e.rating for e in session.query(EloRating).all()}

        for game in games:
            home_stats = session.query(TeamStat).filter(
                TeamStat.game_id == game.id, TeamStat.team_id == game.home_team_id,
            ).all()
            away_stats = session.query(TeamStat).filter(
                TeamStat.game_id == game.id, TeamStat.team_id == game.away_team_id,
            ).all()

            home_ts = TeamStats(
                point_diff=_stat_value(home_stats, "point_diff") or 0.0,
                home_record=(0, 0), away_record=(0, 0), last_n_record=(0, 0),
                offensive_rating=_stat_value(home_stats, "offensive_rating") or 100.0,
                defensive_rating=_stat_value(home_stats, "defensive_rating") or 100.0,
                pace=_stat_value(home_stats, "pace") or 100.0,
                strength_of_schedule=0.0,
                elo_rating=elo_map.get(game.home_team_id, 1500.0),
                rest_days=int(_stat_value(home_stats, "rest_days") or 1),
            )
            away_ts = TeamStats(
                point_diff=_stat_value(away_stats, "point_diff") or 0.0,
                home_record=(0, 0), away_record=(0, 0), last_n_record=(0, 0),
                offensive_rating=_stat_value(away_stats, "offensive_rating") or 100.0,
                defensive_rating=_stat_value(away_stats, "defensive_rating") or 100.0,
                pace=_stat_value(away_stats, "pace") or 100.0,
                strength_of_schedule=0.0,
                elo_rating=elo_map.get(game.away_team_id, 1500.0),
                rest_days=int(_stat_value(away_stats, "rest_days") or 1),
            )
            gd = GameData(
                game_id=game.id, sport=game.sport, date=game.date,
                home_team_id=game.home_team_id, away_team_id=game.away_team_id,
                home_stats=home_ts, away_stats=away_ts,
            )
            features = extract_features(gd)
            X_list.append(features_to_array(features))
            y_list.append(game.home_score - game.away_score)
            dates_list.append(game.date)

        self._lgbm_model = LightGBMModel()
        X = np.array(X_list)
        y = np.array(y_list, dtype=float)
        self._lgbm_model.train(X, y, dates_list)

        if self._lgbm_model.trained:
            metrics = self._lgbm_model.walk_forward_validate(X, y, dates_list)
            logger.info("LightGBM walk-forward: %s", metrics)

    def _model_probability(self, game: GameData) -> float:
        hs, aws = game.home_stats, game.away_stats
        pd_diff = hs.point_diff - aws.point_diff
        pd_score = 1 / (1 + 10 ** (-pd_diff / 10))
        elo_diff = hs.elo_rating - aws.elo_rating
        elo_score = 1 / (1 + 10 ** (-elo_diff / 400))
        net_home = hs.offensive_rating - hs.defensive_rating
        net_away = aws.offensive_rating - aws.defensive_rating
        net_diff = net_home - net_away
        rating_score = 1 / (1 + 10 ** (-net_diff / 10))
        weights = self.config.get("weights", {"pd": 0.3, "elo": 0.35, "rating": 0.25, "hca": 0.1})
        prob = (weights["pd"] * pd_score + weights["elo"] * elo_score +
                weights["rating"] * rating_score + weights["hca"] * 0.6)
        return max(0.01, min(0.99, prob))

    def _average_odds(self, game: GameData) -> dict | None:
        if not game.odds: return None
        ml_home = [o.moneyline_home for o in game.odds if o.moneyline_home is not None]
        ml_away = [o.moneyline_away for o in game.odds if o.moneyline_away is not None]
        if not ml_home: return None
        result = {
            "moneyline_home": int(sum(ml_home) / len(ml_home)),
            "moneyline_away": int(sum(ml_away) / len(ml_away)),
        }
        sp_home = [o.spread_home for o in game.odds if o.spread_home is not None]
        sp_away = [o.spread_away for o in game.odds if o.spread_away is not None]
        if sp_home:
            result["spread_home"] = round(sum(sp_home) / len(sp_home), 1)
            result["spread_away"] = round(sum(sp_away) / len(sp_away), 1)
        else:
            result["spread_home"] = None
            result["spread_away"] = None
        ou = [o.over_under for o in game.odds if o.over_under is not None]
        if ou:
            result["over_under"] = round(sum(ou) / len(ou), 1)
        else:
            result["over_under"] = None
        return result

    def _predicted_point_diff(self, game: GameData) -> float:
        """Predict home margin of victory using model components."""
        hs, aws = game.home_stats, game.away_stats
        return hs.point_diff - aws.point_diff

    def _predicted_total(self, game: GameData) -> float:
        """Predict total score from pace and offensive/defensive ratings."""
        hs, aws = game.home_stats, game.away_stats
        avg_pace = (hs.pace + aws.pace) / 2
        total_off = hs.offensive_rating + aws.offensive_rating
        return avg_pace * total_off / 200

    def _spread_cover_prob(self, predicted_diff: float, cover_threshold: float, std: float = POINT_DIFF_STD) -> float:
        """Probability that home margin exceeds the cover threshold.

        Args:
            predicted_diff: Model's predicted home margin (positive = home favored)
            cover_threshold: Points the home team needs to win by to cover.
                For home -3.5 spread: cover_threshold = 3.5 (must win by >3.5)
            std: Standard deviation of prediction residuals
        """
        return float(norm.sf(cover_threshold, loc=predicted_diff, scale=std))

    def _over_probability(self, predicted_total: float, ou_line: float, std: float = TOTAL_POINTS_STD) -> float:
        """Probability that total points exceeds the O/U line."""
        return float(norm.sf(ou_line, loc=predicted_total, scale=std))

    def _count_total_agreeing_models(self, game: GameData, is_over: bool) -> int:
        """Count models that agree with over/under prediction."""
        hs, aws = game.home_stats, game.away_stats
        count = 0
        # Pace-based: high pace suggests over
        avg_pace = (hs.pace + aws.pace) / 2
        if is_over and avg_pace > 100: count += 1
        elif not is_over and avg_pace <= 100: count += 1
        # Offensive rating: high combined offense suggests over
        combined_off = hs.offensive_rating + aws.offensive_rating
        if is_over and combined_off > 200: count += 1
        elif not is_over and combined_off <= 200: count += 1
        # Defensive rating: high combined defense (bad defense) suggests over
        combined_def = hs.defensive_rating + aws.defensive_rating
        if is_over and combined_def > 200: count += 1
        elif not is_over and combined_def <= 200: count += 1
        return count

    def _count_agreeing_models(self, game: GameData, side: str) -> int:
        count = 0
        hs, aws = game.home_stats, game.away_stats
        if side == "home":
            if hs.point_diff > aws.point_diff: count += 1
            if hs.elo_rating > aws.elo_rating: count += 1
            if (hs.offensive_rating - hs.defensive_rating) > (aws.offensive_rating - aws.defensive_rating): count += 1
        else:
            if aws.point_diff > hs.point_diff: count += 1
            if aws.elo_rating > hs.elo_rating: count += 1
            if (aws.offensive_rating - aws.defensive_rating) > (hs.offensive_rating - hs.defensive_rating): count += 1
        return count

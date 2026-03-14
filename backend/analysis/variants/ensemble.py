from backend.analysis.strategy import Strategy
from backend.analysis.confidence import calculate_confidence
from backend.analysis.odds_utils import american_to_implied_prob
from backend.data_types import GameData, Pick

class EnsembleStrategy(Strategy):
    def predict(self, game: GameData) -> list[Pick]:
        if not game.odds: return []
        picks = []
        min_edge = self.config.get("min_edge", 5.0)
        home_prob = self._model_probability(game)
        away_prob = 1.0 - home_prob
        avg_odds = self._average_odds(game)
        if avg_odds is None: return []

        # Moneyline picks
        if avg_odds["moneyline_home"] is not None:
            implied_home = american_to_implied_prob(avg_odds["moneyline_home"])
            implied_away = american_to_implied_prob(avg_odds["moneyline_away"])
            home_edge = (home_prob - implied_home) * 100
            away_edge = (away_prob - implied_away) * 100
            if home_edge >= min_edge:
                models = self._count_agreeing_models(game, "home")
                picks.append(Pick(game_id=game.game_id, pick_type="moneyline", pick_value="HOME ML",
                    confidence=calculate_confidence(home_edge, models), edge_pct=round(home_edge, 1),
                    model_probability=round(home_prob, 4), implied_probability=round(implied_home, 4),
                    odds_at_pick=avg_odds["moneyline_home"]))
            elif away_edge >= min_edge:
                models = self._count_agreeing_models(game, "away")
                picks.append(Pick(game_id=game.game_id, pick_type="moneyline", pick_value="AWAY ML",
                    confidence=calculate_confidence(away_edge, models), edge_pct=round(away_edge, 1),
                    model_probability=round(away_prob, 4), implied_probability=round(implied_away, 4),
                    odds_at_pick=avg_odds["moneyline_away"]))

        # Spread picks
        if avg_odds.get("spread_home") is not None:
            predicted_diff = self._predicted_point_diff(game)
            spread_line = avg_odds["spread_home"]
            # Model says home wins by predicted_diff; spread says home must cover spread_line
            # spread_line is negative for favorites (e.g., -3.5)
            # Edge: how much model disagrees with the line
            spread_edge = abs(predicted_diff - (-spread_line))
            if spread_edge >= min_edge:
                models = self._count_agreeing_models(game, "home" if predicted_diff > -spread_line else "away")
                if predicted_diff > -spread_line:
                    # Model thinks home covers
                    pick_value = f"HOME {spread_line:+g}"
                    picks.append(Pick(game_id=game.game_id, pick_type="spread",
                        pick_value=pick_value,
                        confidence=calculate_confidence(spread_edge, models),
                        edge_pct=round(spread_edge, 1),
                        model_probability=round(home_prob, 4),
                        implied_probability=0.5,
                        odds_at_pick=-110))
                else:
                    # Model thinks away covers
                    spread_away = avg_odds["spread_away"]
                    pick_value = f"AWAY +{spread_away:g}" if spread_away >= 0 else f"AWAY {spread_away:g}"
                    picks.append(Pick(game_id=game.game_id, pick_type="spread",
                        pick_value=pick_value,
                        confidence=calculate_confidence(spread_edge, models),
                        edge_pct=round(spread_edge, 1),
                        model_probability=round(away_prob, 4),
                        implied_probability=0.5,
                        odds_at_pick=-110))

        # Over/Under picks
        if avg_odds.get("over_under") is not None:
            predicted_total = self._predicted_total(game)
            ou_line = avg_odds["over_under"]
            ou_edge = abs(predicted_total - ou_line)
            if ou_edge >= min_edge:
                models = self._count_total_agreeing_models(game, predicted_total > ou_line)
                if predicted_total > ou_line:
                    pick_value = f"Over {ou_line:g}"
                    picks.append(Pick(game_id=game.game_id, pick_type="over_under",
                        pick_value=pick_value,
                        confidence=calculate_confidence(ou_edge, models),
                        edge_pct=round(ou_edge, 1),
                        model_probability=round(predicted_total, 4),
                        implied_probability=round(ou_line, 4),
                        odds_at_pick=-110))
                else:
                    pick_value = f"Under {ou_line:g}"
                    picks.append(Pick(game_id=game.game_id, pick_type="over_under",
                        pick_value=pick_value,
                        confidence=calculate_confidence(ou_edge, models),
                        edge_pct=round(ou_edge, 1),
                        model_probability=round(predicted_total, 4),
                        implied_probability=round(ou_line, 4),
                        odds_at_pick=-110))

        return picks

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

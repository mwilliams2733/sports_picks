from backend.analysis.strategy import Strategy
from backend.analysis.confidence import calculate_confidence
from backend.analysis.odds_utils import american_to_implied_prob
from backend.data_types import GameData, Pick


class ValueOnlyStrategy(Strategy):
    """Variant B: Only picks when implied probability edge exceeds a high threshold."""

    def predict(self, game: GameData) -> list[Pick]:
        if not game.odds:
            return []
        picks = []
        # Higher default threshold than other variants
        min_edge = self.config.get("min_edge", 10.0)

        home_prob = self._model_probability(game)
        away_prob = 1.0 - home_prob

        avg_odds = self._average_odds(game)
        if avg_odds is None:
            return []

        if avg_odds["moneyline_home"] is not None:
            implied_home = american_to_implied_prob(avg_odds["moneyline_home"])
            implied_away = american_to_implied_prob(avg_odds["moneyline_away"])
            home_edge = (home_prob - implied_home) * 100
            away_edge = (away_prob - implied_away) * 100

            # Only pick the side with the larger edge, and only if it meets threshold
            if home_edge >= min_edge and home_edge >= away_edge:
                models = self._count_agreeing_signals(game, "home")
                picks.append(Pick(
                    game_id=game.game_id, pick_type="moneyline", pick_value="HOME ML",
                    confidence=calculate_confidence(home_edge, models),
                    edge_pct=round(home_edge, 1),
                    model_probability=round(home_prob, 4),
                    implied_probability=round(implied_home, 4),
                    odds_at_pick=avg_odds["moneyline_home"]))
            elif away_edge >= min_edge:
                models = self._count_agreeing_signals(game, "away")
                picks.append(Pick(
                    game_id=game.game_id, pick_type="moneyline", pick_value="AWAY ML",
                    confidence=calculate_confidence(away_edge, models),
                    edge_pct=round(away_edge, 1),
                    model_probability=round(away_prob, 4),
                    implied_probability=round(implied_away, 4),
                    odds_at_pick=avg_odds["moneyline_away"]))
        return picks

    def _model_probability(self, game: GameData) -> float:
        hs, aws = game.home_stats, game.away_stats
        # Conservative blend: ELO + point diff + net rating (equal weights)
        pd_diff = hs.point_diff - aws.point_diff
        pd_score = 1 / (1 + 10 ** (-pd_diff / 10))

        elo_diff = hs.elo_rating - aws.elo_rating
        elo_score = 1 / (1 + 10 ** (-elo_diff / 400))

        net_home = hs.offensive_rating - hs.defensive_rating
        net_away = aws.offensive_rating - aws.defensive_rating
        net_diff = net_home - net_away
        rating_score = 1 / (1 + 10 ** (-net_diff / 10))

        prob = (pd_score + elo_score + rating_score) / 3
        return max(0.01, min(0.99, prob))

    def _count_agreeing_signals(self, game: GameData, side: str) -> int:
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

    def _average_odds(self, game: GameData) -> dict | None:
        if not game.odds:
            return None
        ml_home = [o.moneyline_home for o in game.odds if o.moneyline_home is not None]
        ml_away = [o.moneyline_away for o in game.odds if o.moneyline_away is not None]
        if not ml_home:
            return None
        return {
            "moneyline_home": int(sum(ml_home) / len(ml_home)),
            "moneyline_away": int(sum(ml_away) / len(ml_away)),
        }

from backend.analysis.strategy import Strategy
from backend.analysis.confidence import calculate_confidence
from backend.analysis.odds_utils import american_to_implied_prob
from backend.data_types import GameData, Pick


class RecentFormStrategy(Strategy):
    """Variant A: Heavily weights recent game performance over season averages."""

    def predict(self, game: GameData) -> list[Pick]:
        if not game.odds:
            return []
        picks = []
        min_edge = self.config.get("min_edge", 5.0)
        lookback = self.config.get("lookback", 5)

        home_prob = self._model_probability(game, lookback)
        away_prob = 1.0 - home_prob

        avg_odds = self._average_odds(game)
        if avg_odds is None:
            return []

        if avg_odds["moneyline_home"] is not None:
            implied_home = american_to_implied_prob(avg_odds["moneyline_home"])
            implied_away = american_to_implied_prob(avg_odds["moneyline_away"])
            home_edge = (home_prob - implied_home) * 100
            away_edge = (away_prob - implied_away) * 100

            if home_edge >= min_edge:
                picks.append(Pick(
                    game_id=game.game_id, pick_type="moneyline", pick_value="HOME ML",
                    confidence=calculate_confidence(home_edge, 1),
                    edge_pct=round(home_edge, 1),
                    model_probability=round(home_prob, 4),
                    implied_probability=round(implied_home, 4),
                    odds_at_pick=avg_odds["moneyline_home"]))
            elif away_edge >= min_edge:
                picks.append(Pick(
                    game_id=game.game_id, pick_type="moneyline", pick_value="AWAY ML",
                    confidence=calculate_confidence(away_edge, 1),
                    edge_pct=round(away_edge, 1),
                    model_probability=round(away_prob, 4),
                    implied_probability=round(implied_away, 4),
                    odds_at_pick=avg_odds["moneyline_away"]))
        return picks

    def _model_probability(self, game: GameData, lookback: int) -> float:
        hs, aws = game.home_stats, game.away_stats

        # Recent form: win pct over last N games (heavy weight)
        h_last_w, h_last_l = hs.last_n_record
        a_last_w, a_last_l = aws.last_n_record
        h_recent_pct = h_last_w / max(h_last_w + h_last_l, 1)
        a_recent_pct = a_last_w / max(a_last_w + a_last_l, 1)

        # Season point differential (light weight)
        pd_diff = hs.point_diff - aws.point_diff
        pd_score = 1 / (1 + 10 ** (-pd_diff / 10))

        # Home/away record split
        h_home_w, h_home_l = hs.home_record
        a_away_w, a_away_l = aws.away_record
        h_home_pct = h_home_w / max(h_home_w + h_home_l, 1)
        a_away_pct = a_away_w / max(a_away_w + a_away_l, 1)
        venue_score = (h_home_pct - a_away_pct + 1) / 2  # normalize to 0-1

        # Blend: 60% recent form, 20% point diff, 20% venue
        recent_weight = self.config.get("recent_weight", 0.6)
        pd_weight = self.config.get("pd_weight", 0.2)
        venue_weight = self.config.get("venue_weight", 0.2)

        recent_score = (h_recent_pct - a_recent_pct + 1) / 2  # normalize to 0-1
        prob = recent_weight * recent_score + pd_weight * pd_score + venue_weight * venue_score
        return max(0.01, min(0.99, prob))

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

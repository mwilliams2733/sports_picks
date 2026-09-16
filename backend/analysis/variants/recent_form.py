from backend.analysis.strategy import Strategy
from backend.analysis.confidence import calculate_confidence
from backend.analysis.odds_utils import american_to_implied_prob, remove_vig
from backend.analysis.kelly import fractional_kelly
from backend.data_types import GameData, Pick


class RecentFormStrategy(Strategy):
    """Variant A: Heavily weights recent game performance over season averages."""

    # _model_probability reads last_n_record, home_record, away_record and
    # point_diff only. Of those, point_diff is the sole signal with a factor
    # code ("recent_form"); win-pct/venue splits have no code. Elo, net
    # rating, rest, fatigue, lookahead and pitcher are never read here.
    FACTOR_CODES = frozenset({"recent_form"})

    def predict(self, game: GameData) -> list[Pick]:
        if not game.odds:
            return []
        picks = []
        min_edge = self.config.get("min_edge", 5.0)
        kelly_fraction = self.config.get("kelly_fraction", 0.25)
        lookback = self.config.get("lookback", 5)

        home_prob = self._model_probability(game, lookback)
        away_prob = 1.0 - home_prob

        avg_odds = self._average_odds(game)
        if avg_odds is None:
            return []

        if avg_odds["moneyline_home"] is not None:
            raw_home = american_to_implied_prob(avg_odds["moneyline_home"])
            raw_away = american_to_implied_prob(avg_odds["moneyline_away"])
            implied_home, implied_away = remove_vig(raw_home, raw_away)
            home_edge = (home_prob - implied_home) * 100
            away_edge = (away_prob - implied_away) * 100

            if home_edge >= min_edge:
                picks.append(Pick(
                    game_id=game.game_id, pick_type="moneyline", pick_value="HOME ML",
                    confidence=calculate_confidence(home_edge, 1, self.thresholds),
                    edge_pct=round(home_edge, 1),
                    model_probability=round(home_prob, 4),
                    implied_probability=round(implied_home, 4),
                    odds_at_pick=avg_odds["moneyline_home"],
                    suggested_unit_size=fractional_kelly(home_prob, avg_odds["moneyline_home"], kelly_fraction),
                    factors=self._build_factors(game, "home")))
            elif away_edge >= min_edge:
                picks.append(Pick(
                    game_id=game.game_id, pick_type="moneyline", pick_value="AWAY ML",
                    confidence=calculate_confidence(away_edge, 1, self.thresholds),
                    edge_pct=round(away_edge, 1),
                    model_probability=round(away_prob, 4),
                    implied_probability=round(implied_away, 4),
                    odds_at_pick=avg_odds["moneyline_away"],
                    suggested_unit_size=fractional_kelly(away_prob, avg_odds["moneyline_away"], kelly_fraction),
                    factors=self._build_factors(game, "away")))
        return picks

    def _model_probability(self, game: GameData, lookback: int) -> float:
        hs, aws = game.home_stats, game.away_stats

        # Recent form: win pct over last N games
        h_last_w, h_last_l = hs.last_n_record
        a_last_w, a_last_l = aws.last_n_record
        h_recent_pct = h_last_w / max(h_last_w + h_last_l, 1)
        a_recent_pct = a_last_w / max(a_last_w + a_last_l, 1)

        # Season win pct derived from home + away records
        h_total_w = hs.home_record[0] + hs.away_record[0]
        h_total_l = hs.home_record[1] + hs.away_record[1]
        h_season_pct = h_total_w / max(h_total_w + h_total_l, 1)

        a_total_w = aws.home_record[0] + aws.away_record[0]
        a_total_l = aws.home_record[1] + aws.away_record[1]
        a_season_pct = a_total_w / max(a_total_w + a_total_l, 1)

        # EWMA momentum: recent form trend relative to season baseline
        h_momentum = h_recent_pct - h_season_pct
        a_momentum = a_recent_pct - a_season_pct
        momentum_diff = h_momentum - a_momentum
        momentum_score = (momentum_diff + 1) / 2  # normalize to 0-1

        # Season point differential with EWMA decay toward recent form
        pd_diff = hs.point_diff - aws.point_diff
        pd_score = 1 / (1 + 10 ** (-pd_diff / 10))
        ewma_alpha = self.config.get("ewma_alpha", 0.3)
        recent_score = (h_recent_pct - a_recent_pct + 1) / 2  # normalize to 0-1
        adjusted_pd = (1 - ewma_alpha) * pd_score + ewma_alpha * recent_score

        # Home/away record split
        h_home_w, h_home_l = hs.home_record
        a_away_w, a_away_l = aws.away_record
        h_home_pct = h_home_w / max(h_home_w + h_home_l, 1)
        a_away_pct = a_away_w / max(a_away_w + a_away_l, 1)
        venue_score = (h_home_pct - a_away_pct + 1) / 2  # normalize to 0-1

        # Blend with momentum factor; re-normalize weights to sum to 1
        recent_weight = self.config.get("recent_weight", 0.6)
        pd_weight = self.config.get("pd_weight", 0.2)
        venue_weight = self.config.get("venue_weight", 0.2)
        momentum_weight = self.config.get("momentum_weight", 0.15)

        total_weight = recent_weight + pd_weight + venue_weight + momentum_weight
        prob = (
            (recent_weight / total_weight) * recent_score
            + (pd_weight / total_weight) * adjusted_pd
            + (venue_weight / total_weight) * venue_score
            + (momentum_weight / total_weight) * momentum_score
        )
        return max(0.01, min(0.99, prob))

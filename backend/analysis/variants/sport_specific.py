from backend.analysis.strategy import Strategy
from backend.analysis.confidence import calculate_confidence
from backend.analysis.odds_utils import american_to_implied_prob
from backend.data_types import GameData, Pick


# Per-sport default weight profiles
SPORT_WEIGHTS = {
    "nba": {"pd": 0.25, "elo": 0.25, "rating": 0.20, "rest": 0.15, "venue": 0.15},
    "nfl": {"pd": 0.20, "elo": 0.30, "rating": 0.15, "turnover": 0.20, "redzone": 0.15},
    "ncaab": {"pd": 0.20, "elo": 0.25, "rating": 0.25, "conference": 0.15, "venue": 0.15},
    "ncaaf": {"pd": 0.20, "elo": 0.30, "rating": 0.15, "conference": 0.20, "venue": 0.15},
    # MLB: pitcher dominates by design — the starter is the single biggest variable.
    "mlb": {"pd": 0.10, "elo": 0.20, "rating": 0.10, "pitcher": 0.45, "venue": 0.15},
}


class SportSpecificStrategy(Strategy):
    """Variant D: Independently tuned weights per sport."""

    def predict(self, game: GameData) -> list[Pick]:
        if not game.odds:
            return []
        picks = []
        min_edge = self.config.get("min_edge", 5.0)

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

            if home_edge >= min_edge:
                models = self._count_agreeing(game, "home")
                picks.append(Pick(
                    game_id=game.game_id, pick_type="moneyline", pick_value="HOME ML",
                    confidence=calculate_confidence(home_edge, models),
                    edge_pct=round(home_edge, 1),
                    model_probability=round(home_prob, 4),
                    implied_probability=round(implied_home, 4),
                    odds_at_pick=avg_odds["moneyline_home"]))
            elif away_edge >= min_edge:
                models = self._count_agreeing(game, "away")
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
        sport = game.sport
        weights = self.config.get("sport_weights", {}).get(sport, SPORT_WEIGHTS.get(sport, SPORT_WEIGHTS["nba"]))

        # Base signals (all sports)
        pd_diff = hs.point_diff - aws.point_diff
        pd_score = 1 / (1 + 10 ** (-pd_diff / 10))

        elo_diff = hs.elo_rating - aws.elo_rating
        elo_score = 1 / (1 + 10 ** (-elo_diff / 400))

        net_home = hs.offensive_rating - hs.defensive_rating
        net_away = aws.offensive_rating - aws.defensive_rating
        rating_score = 1 / (1 + 10 ** (-(net_home - net_away) / 10))

        prob = weights.get("pd", 0.25) * pd_score + weights.get("elo", 0.25) * elo_score + weights.get("rating", 0.20) * rating_score

        # Sport-specific adjustments
        if sport == "nba":
            rest_score = self._rest_advantage(hs.rest_days, aws.rest_days)
            venue_score = self._venue_score(hs, aws)
            prob += weights.get("rest", 0.15) * rest_score + weights.get("venue", 0.15) * venue_score
        elif sport == "nfl":
            to_score = self._turnover_score(hs, aws)
            rz_score = self._redzone_score(hs, aws)
            prob += weights.get("turnover", 0.20) * to_score + weights.get("redzone", 0.15) * rz_score
        elif sport in ("ncaab", "ncaaf"):
            conf_score = self._conference_score(hs, aws)
            venue_score = self._venue_score(hs, aws)
            prob += weights.get("conference", 0.15) * conf_score + weights.get("venue", 0.15) * venue_score
        elif sport == "mlb":
            pitcher_score = self._pitcher_score(hs, aws)
            venue_score = self._venue_score(hs, aws)
            prob += weights.get("pitcher", 0.45) * pitcher_score + weights.get("venue", 0.15) * venue_score

        # Schedule adjustments
        # Fatigue: penalize fatigued teams
        if hs.is_schedule_fatigued:
            prob -= 0.03 * hs.schedule_fatigue_score  # Up to 3% penalty for home
        if aws.is_schedule_fatigued:
            prob += 0.03 * aws.schedule_fatigue_score  # Boost if away is fatigued

        # Lookahead: penalize favored team looking ahead
        if hs.is_lookahead_spot:
            prob -= 0.04  # Home team may be unfocused (4% penalty)
        if aws.is_lookahead_spot:
            prob += 0.04  # Away team may be unfocused (benefits home)

        return max(0.01, min(0.99, prob))

    def _rest_advantage(self, home_rest: int, away_rest: int) -> float:
        """NBA: rest days advantage. Back-to-back (1 day) is a big disadvantage."""
        diff = home_rest - away_rest
        return 1 / (1 + 10 ** (-diff / 2))

    def _venue_score(self, hs, aws) -> float:
        h_home_w, h_home_l = hs.home_record
        a_away_w, a_away_l = aws.away_record
        h_pct = h_home_w / max(h_home_w + h_home_l, 1)
        a_pct = a_away_w / max(a_away_w + a_away_l, 1)
        return (h_pct - a_pct + 1) / 2

    def _turnover_score(self, hs, aws) -> float:
        """NFL: turnover margin advantage."""
        h_to = hs.turnover_margin or 0.0
        a_to = aws.turnover_margin or 0.0
        diff = h_to - a_to
        return 1 / (1 + 10 ** (-diff / 5))

    def _redzone_score(self, hs, aws) -> float:
        """NFL: red zone efficiency comparison."""
        h_rz = hs.red_zone_pct or 50.0
        a_rz = aws.red_zone_pct or 50.0
        diff = h_rz - a_rz
        return 1 / (1 + 10 ** (-diff / 20))

    def _conference_score(self, hs, aws) -> float:
        """NCAA: conference strength adjustment."""
        h_conf = hs.conference_strength or 0.5
        a_conf = aws.conference_strength or 0.5
        diff = h_conf - a_conf
        return 1 / (1 + 10 ** (-diff / 0.3))

    def _pitcher_score(self, hs, aws) -> float:
        """MLB: home_pitcher_skill / (home + away). Returns 0.5 if either is missing
        (neutral) so picks still generate before probable pitchers are announced.
        """
        h = hs.pitcher_skill_score
        a = aws.pitcher_skill_score
        if h is None or a is None:
            return 0.5
        diff = h - a
        # Sigmoid with scale 0.3 -> a 0.3 advantage gives ~0.73, full advantage ~0.95.
        return 1.0 / (1.0 + 10 ** (-diff / 0.3))

    def _count_agreeing(self, game: GameData, side: str) -> int:
        count = 0
        hs, aws = game.home_stats, game.away_stats
        if side == "home":
            if hs.point_diff > aws.point_diff: count += 1
            if hs.elo_rating > aws.elo_rating: count += 1
            if (hs.offensive_rating - hs.defensive_rating) > (aws.offensive_rating - aws.defensive_rating): count += 1
            if game.sport == "mlb" and hs.pitcher_skill_score is not None and aws.pitcher_skill_score is not None:
                if hs.pitcher_skill_score > aws.pitcher_skill_score: count += 1
        else:
            if aws.point_diff > hs.point_diff: count += 1
            if aws.elo_rating > hs.elo_rating: count += 1
            if (aws.offensive_rating - aws.defensive_rating) > (hs.offensive_rating - hs.defensive_rating): count += 1
            if game.sport == "mlb" and hs.pitcher_skill_score is not None and aws.pitcher_skill_score is not None:
                if aws.pitcher_skill_score > hs.pitcher_skill_score: count += 1
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

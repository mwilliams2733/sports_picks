"""CombatSportsStrategy — h2h moneyline picks for boxing and MMA.

Uses fighter Elo + recent-form score + opponent-quality adjustment. Does not
emit spread or total picks (those don't apply to combat sports).
"""
from __future__ import annotations
from backend.analysis.strategy import Strategy
from backend.analysis.confidence import calculate_confidence
from backend.analysis.odds_utils import american_to_implied_prob, remove_vig
from backend.data_types import GameData, Pick


class CombatSportsStrategy(Strategy):
    """Variant E: combat-sports Elo + recent-form blend, h2h only."""

    # _model_probability reads game.home_fighter / game.away_fighter — fighter
    # Elo, recent_form_score and opponent_avg_elo. _build_factors derives all
    # of its codes from game.home_stats / game.away_stats (TeamStats), which
    # this strategy never consults. So NO base factor code corresponds to a
    # signal this strategy computed; emitting one (e.g. "rating_gap" off a
    # TeamStats elo it did not use) would be a fabrication. Today those
    # TeamStats default identically for both fighters so the diffs are 0 and
    # nothing fires — but that is an accident of the fixture, not a guarantee.
    FACTOR_CODES = frozenset()

    def predict(self, game: GameData) -> list[Pick]:
        if not game.odds:
            return []
        home_fighter = getattr(game, "home_fighter", None)
        away_fighter = getattr(game, "away_fighter", None)
        if home_fighter is None or away_fighter is None:
            return []

        # Boxing data-thin gate: Wikidata only covers top-tier boxers, so a
        # missing fight history likely means "fighter unknown to us" rather
        # than "genuine debut." MMA bypasses this gate — UFCStats coverage
        # is comprehensive enough that fights_count=0 is a real debut signal.
        if game.sport == "boxing" and (
            home_fighter.fights_count == 0 or away_fighter.fights_count == 0
        ):
            return []

        home_prob = self._model_probability(home_fighter, away_fighter)
        away_prob = 1.0 - home_prob

        avg_odds = self._average_odds(game)
        if avg_odds is None:
            return []

        min_edge = self.config.get("min_edge", 5.0)
        picks: list[Pick] = []
        raw_home = american_to_implied_prob(avg_odds["moneyline_home"])
        raw_away = american_to_implied_prob(avg_odds["moneyline_away"])
        implied_home, implied_away = remove_vig(raw_home, raw_away)
        home_edge = (home_prob - implied_home) * 100
        away_edge = (away_prob - implied_away) * 100

        if home_edge >= min_edge:
            picks.append(Pick(
                game_id=game.game_id, pick_type="moneyline", pick_value="HOME ML",
                confidence=calculate_confidence(home_edge, models_agreeing=2, thresholds=self.thresholds),
                edge_pct=round(home_edge, 1),
                model_probability=round(home_prob, 4),
                implied_probability=round(implied_home, 4),
                odds_at_pick=avg_odds["moneyline_home"],
                factors=self._build_factors(game, "home")))
        elif away_edge >= min_edge:
            picks.append(Pick(
                game_id=game.game_id, pick_type="moneyline", pick_value="AWAY ML",
                confidence=calculate_confidence(away_edge, models_agreeing=2, thresholds=self.thresholds),
                edge_pct=round(away_edge, 1),
                model_probability=round(away_prob, 4),
                implied_probability=round(implied_away, 4),
                odds_at_pick=avg_odds["moneyline_away"],
                factors=self._build_factors(game, "away")))
        return picks

    def _model_probability(self, home, away) -> float:
        """Blend: 70% Elo + 20% recent form + 10% opponent quality.
        When opponent_avg_elo is missing for either fighter (debut), fall back
        to 78% Elo + 22% form (re-normalized).
        """
        elo_diff = home.elo_rating - away.elo_rating
        elo_term = 1 / (1 + 10 ** (-elo_diff / 400))

        # form_diff is bounded by [-1, 1] since recent_form_score ∈ [0, 1].
        # Scale=2.0 keeps the sigmoid responsive without saturating: a 0.5
        # form gap maps to ~0.64, a full 1.0 gap maps to ~0.76 — a meaningful
        # but non-decisive signal. (Scale=0.3 would saturate at form_diff≥0.5.)
        form_diff = home.recent_form_score - away.recent_form_score
        form_term = 1 / (1 + 10 ** (-form_diff / 2.0))

        if home.opponent_avg_elo is not None and away.opponent_avg_elo is not None:
            quality_diff = home.opponent_avg_elo - away.opponent_avg_elo
            quality_term = 1 / (1 + 10 ** (-quality_diff / 400))
            prob = 0.70 * elo_term + 0.20 * form_term + 0.10 * quality_term
        else:
            prob = 0.78 * elo_term + 0.22 * form_term
        return max(0.05, min(0.95, prob))

"""CombatSportsStrategy — h2h moneyline picks for boxing and MMA.

Uses fighter Elo + recent-form score + opponent-quality adjustment. Does not
emit spread or total picks (those don't apply to combat sports).
"""
from __future__ import annotations
import math
from backend.analysis.strategy import Strategy
from backend.analysis.confidence import calculate_confidence
from backend.analysis.odds_utils import american_to_implied_prob, remove_vig, value_edge
from backend.data_types import GameData, Pick

#: MMA win model fitted 2026-10-10 on 8,867 UFC bouts
#: (backend/analysis/combat_calibration.py; docs/FINDINGS.md): logistic on
#: (elo_diff/400, form_diff, quality_diff/400, log1p(fights) diff), no
#: intercept, fitted symmetric (UFCStats lists winners first), K=16 chosen on
#: 2021-2023. Test window 2024+ (n=1,181): log-loss .6747 vs .6815 for the old
#: blend. Still far less informed than the market (66 bouts with PRE-FIGHT
#: prices: .666 vs .590), so MMA picks stay tracking-only
#: (pick_generator.TRACKING_ONLY_SPORTS).
MMA_COEF = (2.5169, 0.7308, 2.3862, 0.0340)


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

        # Data-thin gate: no pick unless BOTH fighters have fight history.
        # Boxing: Wikidata covers only top-tier boxers. MMA (owner,
        # 2026-10-10): history comes from the UFCStats import, so a fighter
        # with no fights usually means "not in the UFC data", and pricing a
        # known fighter against the 1500 seed would publish a built-in bias
        # as edge (docs/FINDINGS.md, MMA picks at 0.5).
        if game.sport in ("boxing", "mma") and (
            home_fighter.fights_count == 0 or away_fighter.fights_count == 0
        ):
            return []

        home_prob = self._model_probability(home_fighter, away_fighter, game.sport)
        away_prob = 1.0 - home_prob

        avg_odds = self._average_odds(game)
        if avg_odds is None:
            return []

        min_edge = self.config.get("min_edge", 5.0)
        picks: list[Pick] = []
        raw_home = american_to_implied_prob(avg_odds["moneyline_home"])
        raw_away = american_to_implied_prob(avg_odds["moneyline_away"])
        implied_home, implied_away = remove_vig(raw_home, raw_away)
        home_edge = value_edge(home_prob, avg_odds["moneyline_home"])
        away_edge = value_edge(away_prob, avg_odds["moneyline_away"])

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

    def _model_probability(self, home, away, sport: str | None = None) -> float:
        """MMA: the fitted logistic model (MMA_COEF). Any other sport (boxing):
        the blend -- 70% Elo + 20% recent form + 10% opponent quality, or
        78% Elo + 22% form when opponent_avg_elo is missing for either fighter.
        """
        if sport == "mma":
            quality = (0.0 if home.opponent_avg_elo is None or away.opponent_avg_elo is None
                       else (home.opponent_avg_elo - away.opponent_avg_elo) / 400)
            z = (MMA_COEF[0] * (home.elo_rating - away.elo_rating) / 400
                 + MMA_COEF[1] * (home.recent_form_score - away.recent_form_score)
                 + MMA_COEF[2] * quality
                 + MMA_COEF[3] * (math.log1p(home.fights_count) - math.log1p(away.fights_count)))
            return max(0.05, min(0.95, 1 / (1 + math.exp(-z))))
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

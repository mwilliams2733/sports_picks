from abc import ABC, abstractmethod
from backend.analysis.odds_utils import InvalidOddsError, american_to_implied_prob
from backend.data_types import GameData, Pick, PickFactor

class Strategy(ABC):
    #: The factor codes this strategy is allowed to emit — i.e. the signals it
    #: genuinely consumes when computing a probability. `_build_factors` can
    #: derive all seven codes from TeamStats, but a variant that never reads
    #: (say) rest days must not tell a reader that rest decided its pick.
    #: These sentences are emailed to third parties as the reasoning behind a
    #: bet, so an unused code is a fabrication, not a rounding error.
    #: Subclasses MUST narrow this to what their own probability model reads.
    FACTOR_CODES: frozenset[str] = frozenset({
        "rating_gap", "recent_form", "net_rating", "schedule_fatigue",
        "lookahead_spot", "rest_advantage", "pitcher_edge",
    })

    def __init__(self, name: str, config: dict, thresholds: dict | None = None):
        self.name = name
        self.config = config
        self.thresholds = thresholds

    @abstractmethod
    def predict(self, game: GameData) -> list[Pick]:
        pass

    @classmethod
    def from_config(cls, config: dict) -> "Strategy":
        return cls(name=config.get("name", cls.__name__), config=config)

    def _average_odds(self, game: GameData) -> dict | None:
        """Average a game's book-level odds into a single consensus quote.

        Moneyline prices are averaged in probability space (American odds are
        non-linear around +/-100, so an arithmetic mean of prices is wrong and
        can even land in the invalid (-100, 100) band). Spread/total lines are
        points, not prices, so those stay arithmetic, rounded to 1 decimal.
        """
        if not game.odds:
            return None
        ml_home_prices = [o.moneyline_home for o in game.odds if o.moneyline_home is not None]
        ml_away_prices = [o.moneyline_away for o in game.odds if o.moneyline_away is not None]
        if not ml_home_prices or not ml_away_prices:
            return None

        home_probs = []
        for price in ml_home_prices:
            try:
                home_probs.append(american_to_implied_prob(price))
            except InvalidOddsError:
                continue
        away_probs = []
        for price in ml_away_prices:
            try:
                away_probs.append(american_to_implied_prob(price))
            except InvalidOddsError:
                continue
        if not home_probs or not away_probs:
            return None

        result = {
            "moneyline_home": self._prob_to_american(sum(home_probs) / len(home_probs)),
            "moneyline_away": self._prob_to_american(sum(away_probs) / len(away_probs)),
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

    @staticmethod
    def _prob_to_american(p: float) -> int:
        """Convert an implied probability back to an American price.

        Never returns a value in the invalid (-100, 100) band; p == 0.5 is
        clamped to the -100/+100 boundary.
        """
        p = max(1e-6, min(1 - 1e-6, p))
        if p >= 0.5:
            price = -round(100 * p / (1 - p))
            return min(price, -100)
        price = round(100 * (1 - p) / p)
        return max(price, 100)
    def _build_factors(self, game: "GameData", side: str) -> list["PickFactor"]:
        """Derive the factors that favor `side` ("home" or "away").

        Only emits a factor when the underlying signal is actually present,
        actually favors that side, AND is listed in this strategy's
        FACTOR_CODES — a factor the model did not use must never appear in a
        rationale.
        """
        from backend.data_types import PickFactor

        hs, aws = game.home_stats, game.away_stats
        mine, theirs = (hs, aws) if side == "home" else (aws, hs)
        factors: list[PickFactor] = []

        def _strength(diff: float, moderate: float, strong: float) -> str | None:
            if diff >= strong:
                return "strong"
            if diff >= moderate:
                return "moderate"
            if diff > 0:
                return "slight"
            return None

        s = _strength(mine.elo_rating - theirs.elo_rating, 50.0, 120.0)
        if s:
            factors.append(PickFactor(code="rating_gap", side=side, strength=s))

        s = _strength(mine.point_diff - theirs.point_diff, 3.0, 7.0)
        if s:
            factors.append(PickFactor(code="recent_form", side=side, strength=s))

        mine_net = mine.offensive_rating - mine.defensive_rating
        theirs_net = theirs.offensive_rating - theirs.defensive_rating
        s = _strength(mine_net - theirs_net, 3.0, 8.0)
        if s:
            factors.append(PickFactor(code="net_rating", side=side, strength=s))

        if theirs.is_schedule_fatigued:
            factors.append(PickFactor(code="schedule_fatigue", side=side, strength="moderate"))

        if theirs.is_lookahead_spot:
            factors.append(PickFactor(code="lookahead_spot", side=side, strength="slight"))

        s = _strength(float(mine.rest_days - theirs.rest_days), 2.0, 4.0)
        if s:
            factors.append(PickFactor(code="rest_advantage", side=side, strength=s))

        if mine.pitcher_skill_score is not None and theirs.pitcher_skill_score is not None:
            s = _strength(mine.pitcher_skill_score - theirs.pitcher_skill_score, 0.05, 0.15)
            if s:
                factors.append(PickFactor(code="pitcher_edge", side=side, strength=s))

        allowed = type(self).FACTOR_CODES
        return [f for f in factors if f.code in allowed]

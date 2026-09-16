from abc import ABC, abstractmethod
from backend.analysis.odds_utils import InvalidOddsError, american_to_implied_prob
from backend.data_types import GameData, Pick

class Strategy(ABC):
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

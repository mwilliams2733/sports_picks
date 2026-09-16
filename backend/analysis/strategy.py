from abc import ABC, abstractmethod
from backend.data_types import GameData, Pick, PickFactor

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

    def _build_factors(self, game: "GameData", side: str) -> list["PickFactor"]:
        """Derive the factors that favor `side` ("home" or "away").

        Only emits a factor when the underlying signal is actually present
        and actually favors that side — a factor the model did not use must
        never appear in a rationale.
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

        return factors

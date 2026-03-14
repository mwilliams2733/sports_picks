from backend.analysis.strategy import Strategy
from backend.data_types import GameData, Pick


class PropValueStrategy(Strategy):
    """Strategy wrapper for prop value analysis.
    This doesn't use the standard predict(GameData) interface.
    Prop picks are generated via PropAnalyzer in the pipeline.
    """

    def predict(self, game: GameData) -> list[Pick]:
        return []

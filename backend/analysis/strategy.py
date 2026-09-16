from abc import ABC, abstractmethod
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
